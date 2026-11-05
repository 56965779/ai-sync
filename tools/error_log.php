<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v4.4 Tool: Error Log
 * AI 排查白屏/500 的唯一出路。
 *
 * 支持的 action:
 *   error_log : 读取指定日志文件 (tail / grep / since_ts / before_ts)
 *     参数:
 *       name      : 日志短名: php_error | nginx_err | nginx_acc | audit | custom
 *       path      : 当 name=custom 时, 指定日志文件绝对路径 (必须满足安全白名单: 在 WORK_DIR 或 常见日志目录)
 *       lines     : 最近 N 行, 默认 100
 *       grep      : 关键词过滤 (大小写不敏感), 空=不过滤
 *       since_ts  : 只输出此 unix 时间之后的记录 (若日志非 JSON 行, 则忽略)
 *       before_ts : 只输出此 unix 时间之前的记录
 *       regex     : true 则 grep 解释为正则, 默认 false
 *
 *   error_log_list: 列出可用日志文件 (php error / nginx / audit / debug.log 等自动发现)
 *     参数: 无
 */

/**
 * 默认候选日志路径（根据 OS 环境自动探测）
 */
function _discover_log_candidates($cfg) {
    $work_dir = rtrim($cfg['WORK_DIR'], '/\\');
    $candidates = [];

    // 1. scope 自带的 audit / metrics
    $candidates[] = [
        'name'   => 'audit',
        'path'   => $work_dir . '/' . ltrim($cfg['AUDIT_LOG'], '/\\'),
        'scope'  => 'ai-sync',
        'format' => 'jsonl',
    ];

    // 2. 常见 PHP error_log (通过 ini_get 优先)
    $php_err = ini_get('error_log');
    if ($php_err && @is_file($php_err) && @is_readable($php_err)) {
        $candidates[] = [
            'name'   => 'php_error',
            'path'   => $php_err,
            'format' => 'text',
            'note'   => 'from ini_get(error_log)',
        ];
    }
    // 常见备选
    foreach (['/var/log/php-fpm.log', '/var/log/php-fpm/error.log',
              '/var/log/nginx/php_errors.log', '/tmp/php_errors.log',
              '/usr/local/php/var/log/php-fpm.log'] as $p) {
        if (@is_file($p) && @is_readable($p)) {
            $candidates[] = ['name'=>'php_error','path'=>$p,'format'=>'text'];
            break;
        }
    }

    // 3. Nginx 日志
    foreach (['/var/log/nginx/error.log'=>'nginx_err','/var/log/nginx/access.log'=>'nginx_acc',
              '/www/server/nginx/logs/error.log'=>'nginx_err','/www/server/nginx/logs/access.log'=>'nginx_acc',
              '/usr/local/nginx/logs/error.log'=>'nginx_err','/usr/local/nginx/logs/access.log'=>'nginx_acc'] as $p=>$n) {
        if (@is_file($p) && @is_readable($p)) {
            $fmt = ($n === 'nginx_acc') ? 'nginx_access' : 'text';
            $candidates[] = ['name'=>$n,'path'=>$p,'format'=>$fmt];
        }
    }

    // 4. WORK_DIR 目录下常见日志文件: error.log / debug.log / *.log
    $scan_dirs = [$work_dir];
    if (is_dir($work_dir . '/logs')) $scan_dirs[] = $work_dir . '/logs';
    if (is_dir($work_dir . '/runtime')) $scan_dirs[] = $work_dir . '/runtime';
    foreach ($scan_dirs as $sd) {
        $items = @glob($sd . '/*.log');
        if (!$items) continue;
        foreach (array_slice($items, 0, 20) as $f) {
            $bn = strtolower(basename($f));
            $is_err = (strpos($bn, 'error') !== false || strpos($bn, 'err') !== false);
            $name = $is_err ? 'custom_error' : 'custom_log';
            $candidates[] = [
                'name' => $name, 'path' => $f, 'format' => @_guess_log_format($f),
                'from' => 'workdir_scan',
            ];
        }
    }

    // 去重 (按 path)
    $seen = [];
    $out = [];
    foreach ($candidates as $c) {
        if (isset($seen[$c['path']])) continue;
        $seen[$c['path']] = true;
        $c['readable'] = @is_readable($c['path']) ? true : false;
        $c['size'] = @filesize($c['path']) ?: 0;
        $c['mtime'] = @filemtime($c['path']) ?: 0;
        $out[] = $c;
    }
    return $out;
}

function _guess_log_format($path) {
    $head = @file_get_contents($path, false, null, 0, 4096);
    if ($head === false || $head === '') return 'text';
    $line = strtok($head, "\n");
    if ($line === false) return 'text';
    $first = substr(ltrim($line), 0, 1);
    if ($first === '{') return 'jsonl';
    return 'text';
}

/**
 * 安全判断 custom 路径：防止读取 /etc/passwd 等敏感文件
 */
function _is_safe_log_path($path, $cfg) {
    if (!$path) return false;
    $real = @realpath($path);
    if (!$real) return false;
    $ext = strtolower(pathinfo($real, PATHINFO_EXTENSION));
    if ($ext !== 'log' && $ext !== 'txt' && $ext !== '') return false;
    $whitelist_prefix = [
        rtrim($cfg['WORK_DIR'], '/\\') . '/',
        '/var/log/',
        '/tmp/',
        '/www/server/',
        '/usr/local/nginx/logs/',
        '/usr/local/php/var/log/',
    ];
    foreach ($whitelist_prefix as $pf) {
        if (strpos($real, $pf) === 0) return true;
    }
    return false;
}

/**
 * 反向读取文件 N 行 (高效 tail)
 */
function _tail_lines($path, $n_lines) {
    $n_lines = max(1, (int)$n_lines);
    $f = @fopen($path, 'rb');
    if (!$f) return [];
    $size = filesize($path);
    $chunk = 8192;
    $pos = max(0, $size - $chunk);
    $buf = '';
    while (true) {
        fseek($f, $pos);
        $read_size = min($chunk, $size - $pos);
        if ($read_size <= 0) break;
        $buf = fread($f, $read_size) . $buf;
        $nl_count = substr_count($buf, "\n");
        if ($nl_count >= $n_lines || $pos === 0) break;
        $pos = max(0, $pos - $chunk);
    }
    fclose($f);
    $lines = explode("\n", $buf);
    // 去掉首行不完整片段 (除非 $pos=0 即文件开头)
    if ($pos > 0 && count($lines) > 0) array_shift($lines);
    $lines = array_values(array_filter($lines, function ($x) { return $x !== '' && $x !== false; }));
    if (count($lines) > $n_lines) $lines = array_slice($lines, -$n_lines);
    return $lines;
}

/**
 * 解析 JSON 行的时间戳 -> unix ts (失败返回 0)
 */
function _try_parse_jsonl_ts($line) {
    $obj = json_decode($line, true);
    if (!is_array($obj)) return 0;
    if (isset($obj['ts'])) {
        $t = strtotime($obj['ts']);
        if ($t > 0) return $t;
    }
    if (isset($obj['time'])) {
        $t = is_numeric($obj['time']) ? (int)$obj['time'] : @strtotime($obj['time']);
        if ($t > 0) return $t;
    }
    if (isset($obj[0])) {
        $t = is_numeric($obj[0]) ? (int)$obj[0] : @strtotime($obj[0]);
        if ($t > 0) return $t;
    }
    return 0;
}

function action_error_log_list($req, $cfg) {
    $list = _discover_log_candidates($cfg);
    return ok(['logs' => $list, 'total' => count($list)]);
}

function action_error_log($req, $cfg) {
    global $E;
    $name     = $req['name'] ?? 'php_error';
    if (!is_string($name)) {
        return fail($E['INVALID_PARAM'], 'bad_name', 'name 必须是字符串 (日志源名, 先调用 error_log_list 查看)', [
            'hint' => 'name 不能传 true/数字/数组/对象; 例: {"name": "php_error"}',
        ]);
    }
    // 修复: path 只在 name=custom 时才有意义。原实现无条件 trim($req['path']),
    //   客户端传数组/对象时 trim() 在 PHP 8 抛 TypeError → 1099 内部错误 (用户参数问题被报成服务器故障)。
    $custom   = '';
    if ($name === 'custom') {
        $raw_path = $req['path'] ?? '';
        if (!is_string($raw_path)) {
            return fail($E['INVALID_PARAM'], 'bad_path', 'path must be a string when name=custom');
        }
        $custom = trim($raw_path);
    }
    $lines    = (int)($req['lines'] ?? 100);
    $grep     = $req['grep'] ?? '';
    if ($grep === null) $grep = '';
    if (!is_string($grep)) {
        return fail($E['INVALID_PARAM'], 'bad_grep', 'grep 必须是字符串关键词', [
            'hint' => 'grep 不能传 true/数字/数组/对象; 正则过滤请用 regex=<pattern> + regex=true',
        ]);
    }
    $use_regex= bool_flag($req['regex'] ?? false);
    if ($use_regex === null) return bad_bool('regex', $req['regex']);
    $since    = (int)($req['since_ts'] ?? 0);
    $before   = (int)($req['before_ts'] ?? 0);
    if ($lines > 5000) $lines = 5000;
    if ($lines < 1) $lines = 100;

    $candidates = _discover_log_candidates($cfg);
    $target_path = '';

    if ($name === 'custom') {
        if ($custom === '') return fail($E['INVALID_PARAM'], 'missing_path', 'name=custom 时 path 必填');
        if (!_is_safe_log_path($custom, $cfg)) {
            return fail($E['FORBIDDEN'], 'unsafe_path', 'custom 日志路径不在白名单目录或后缀非 log/txt', [
                'hint' => '允许目录: WORK_DIR/ /var/log/ /tmp/ /www/server/ /usr/local/*/logs/ ; 允许后缀: .log / .txt',
            ]);
        }
        $target_path = $custom;
    } else {
        // 按 name 匹配第一个
        foreach ($candidates as $c) {
            if ($c['name'] === $name && $c['readable']) { $target_path = $c['path']; break; }
        }
        if (!$target_path) {
            return fail($E['NOT_FOUND'], 'log_not_found', "未找到 name='$name' 的可读日志，先调用 error_log_list 查看可用列表", [
                'available' => array_values(array_map(function($c){ return $c['name'].':'.$c['path']; }, $candidates)),
            ]);
        }
    }

    if (!@is_readable($target_path)) {
        return fail($E['NOT_FOUND'], 'not_readable', "日志路径不可读: $target_path");
    }

    // AI 优化: relate_to — 自动关联最近改动的文件, 一键排查白屏/500
    // relate_to="recent": 从 audit.log 提取最近 write 操作涉及的文件, 自动 grep
    // relate_to="index.php": 直接 grep 指定文件路径
    $relate_to = $req['relate_to'] ?? '';
    if ($relate_to === null) $relate_to = '';
    if (!is_string($relate_to)) {
        return fail($E['INVALID_PARAM'], 'bad_relate_to', 'relate_to 必须是字符串 ("recent" 或文件路径)', [
            'hint' => 'relate_to 不能传 true/数字/数组/对象',
        ]);
    }
    $related_files = [];
    if ($relate_to !== '') {
        if ($relate_to === 'recent') {
            $audit_file = rtrim($cfg['WORK_DIR'], '/\\') . '/' . ($cfg['AUDIT_LOG'] ?? 'ai-sync/audit.log');
            if (@is_readable($audit_file)) {
                $audit_lines = _tail_lines($audit_file, 500);
                $write_acts = ['upload','upload_batch','delete','rename','copy','chunked_upload','php_run'];
                foreach ($audit_lines as $al) {
                    $j = json_decode($al, true);
                    if (!$j || !in_array($j['action'] ?? '', $write_acts)) continue;
                    $p = $j['path'] ?? '';
                    if ($p !== '' && !in_array($p, $related_files)) $related_files[] = $p;
                }
            }
            if (!empty($related_files)) {
                // 修复: 用 # 作定界符避免与文件路径中的 / 冲突; preg_quote 转义 # 和 /
                $grep = implode('|', array_map(function($p) { return preg_quote($p, '#'); }, $related_files));
                $use_regex = true;
            }
        } else {
            $related_files[] = $relate_to;
            if ($grep === '') { $grep = preg_quote($relate_to, '#'); $use_regex = true; }
        }
    }

    $all_lines = _tail_lines($target_path, $lines * ($grep ? 5 : 1));
    $matched = [];
    $grep_needle = null;
    $grep_delim = '#';
    if ($grep !== '') {
        if ($use_regex) {
            // 自动选未使用的定界符, 避免用户正则含 # 或 / 时解析失败
            foreach (['#', '~', '%', '@', ';', '`'] as $d) {
                if (strpos($grep, $d) === false) { $grep_delim = $d; break; }
            }
            $grep_needle = @preg_match($grep_delim . $grep . $grep_delim, '') === false ? null : $grep;
            if ($grep_needle === null) {
                return fail($E['INVALID_PARAM'], 'bad_regex', 'regex 参数无效');
            }
        } else {
            $grep_needle = mb_strtolower($grep);
        }
    }
    foreach ($all_lines as $ln) {
        // grep 过滤
        if ($grep !== '') {
            if ($use_regex) {
                if (@preg_match($grep_delim . $grep_needle . $grep_delim, $ln) !== 1) continue;
            } else {
                if (mb_strpos(mb_strtolower($ln), $grep_needle) === false) continue;
            }
        }
        // ts 过滤 (只对 JSON 行生效)
        if ($since > 0 || $before > 0) {
            $ts = _try_parse_jsonl_ts($ln);
            if ($ts > 0) {
                if ($since > 0 && $ts < $since) continue;
                if ($before > 0 && $ts > $before) continue;
            }
        }
        $matched[] = $ln;
        if (count($matched) >= $lines) break;
    }
    // 最终保留 tail N
    if (count($matched) > $lines) $matched = array_slice($matched, -$lines);

    // 尝试 JSON 解析预览
    $parsed = null;
    $first = $matched[0] ?? '';
    $sample = substr(ltrim($first), 0, 1);
    if ($sample === '{') {
        $parsed = [];
        foreach ($matched as $ln) {
            $j = json_decode($ln, true);
            $parsed[] = is_array($j) ? $j : ['raw' => $ln];
        }
    }

    return ok([
        'name'          => $name,
        'path'          => $target_path,
        'size'          => filesize($target_path),
        'mtime'         => filemtime($target_path),
        'format'        => _guess_log_format($target_path),
        'lines'         => $matched,
        'line_count'    => count($matched),
        'parsed'        => $parsed,
        'related_files' => $related_files,
    ]);
}
