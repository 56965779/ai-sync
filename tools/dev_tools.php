<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: Dev Tools
 * search / todo / stats / read_range / format / lint / doc / _lint_summary
 */

require_once __DIR__ . '/link_common.php';

function action_search($req, $cfg) {
    global $E, $CONFIG;
    $_t0 = microtime(true);
    // 对外契约: path 可选, 默认 "." (与 help 一致)
    $rel = $req['path'] ?? '.';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");

    $pattern = $req['pattern'] ?? '';
    if (!is_string($pattern)) {
        return fail($E['INVALID_PARAM'], 'bad_pattern', 'pattern 必须是字符串 (关键词或正则)', [
            'hint' => 'pattern 不能传 true/数字/数组/对象; 例: {"pattern": "TODO"}',
        ]);
    }
    if ($pattern === '') return fail($E['INVALID_PARAM'], 'empty_pattern', 'pattern is required');
    $is_regex = bool_flag($req['regex'] ?? false);
    if ($is_regex === null) return bad_bool('regex', $req['regex']);
    $case_insensitive = bool_flag($req['case_insensitive'] ?? false);
    if ($case_insensitive === null) return bad_bool('case_insensitive', $req['case_insensitive']);
    $whole_word = bool_flag($req['whole_word'] ?? false);
    if ($whole_word === null) return bad_bool('whole_word', $req['whole_word']);
    $file_pattern = $req['file_pattern'] ?? null;
    if ($file_pattern !== null && !is_string($file_pattern)) {
        return fail($E['INVALID_PARAM'], 'bad_file_pattern', 'file_pattern 必须是字符串 (如 "*.php")', [
            'hint' => '多个模式用空格或逗号分隔; 不能传 true/数字/数组/对象',
        ]);
    }
    $max_results = intval($req['max_results'] ?? 200);
    if ($max_results > 5000) $max_results = 5000;
    $context_before = intval($req['context_before'] ?? 0);
    $context_after  = intval($req['context_after'] ?? 0);

    $regex = $is_regex ? $pattern : preg_quote($pattern, '/');
    // AI 优化: whole_word 参数 — 按标识符边界匹配 (搜 User 不命中 UserService)
    if ($whole_word && !$is_regex) $regex = '\b' . $regex . '\b';
    $mods = $case_insensitive ? 'iu' : 'u';
    $regex_full = '/' . $regex . '/' . $mods;
    if (@preg_match($regex_full, '') === false) {
        return fail($E['INVALID_PARAM'], 'bad_regex', 'Invalid regular expression');
    }

    $base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
    $hits = [];
    $files_scanned = 0;
    $total_matches = 0;
    // 结果顺序确定性: max_results 截断时命中集取决于遍历顺序 (Python 端同样全局排序)
    $__root_norm = trim(str_replace('\\', '/', (string)$rel), '/');
    $allow_internal = ($__root_norm !== '' && (strpos($__root_norm, 'ai-sync') === 0
        || strpos($__root_norm, '.sync_oplog') === 0 || strpos($__root_norm, '.ai-deploy-tmp') === 0));

    $is_dir = is_dir($dest);
    $files = [];
    if ($is_dir) {
        $iter = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($dest, \RecursiveDirectoryIterator::SKIP_DOTS),
            \RecursiveIteratorIterator::SELF_FIRST,
            \RecursiveIteratorIterator::CATCH_GET_CHILD
        );
        foreach ($iter as $info) {
            if (!$info->isFile()) continue;   // FIFO/socket/设备: 读取会阻塞
            $real = $info->getRealPath();
            if ($real === false) continue;
            // BUG-45 修复 (审计 Finding 9): 缺尾部分隔符, WORK_DIR=/var/www/player 会误匹配
            // /var/www/player_secret/ 同级目录 (配合 symlink 可越权读取). 必须加 DIRECTORY_SEPARATOR.
            $_wd_base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
            if (strpos($real . DIRECTORY_SEPARATOR, $_wd_base) !== 0) continue;
            if ($file_pattern) {
                // AI 修复: 支持空格/逗号分隔多模式 (如 "*.php *.py *.js"), 任一匹配即通过
                $_fp_ok = false;
                foreach (preg_split('/[\s,]+/', trim($file_pattern)) as $_fp) {
                    if ($_fp !== '' && fnmatch($_fp, $info->getFilename())) { $_fp_ok = true; break; }
                }
                if (!$_fp_ok) continue;
            }
            // 服务端簿记不参与业务搜索; v4.28: 逐路径段判断 (与 PY 端 _is_internal_dir 同规则),
            // 同时覆盖 (a) 根级簿记"文件" (.sync_oplog.jsonl / .sync.log 等) 与 (b) 簿记目录内文件
            if (!$allow_internal) {
                $_relp = str_replace('\\', '/', substr($real, strlen($base)));
                foreach (explode('/', $_relp) as $_seg) {
                    if ($_seg === 'ai-sync' || $_seg === '.sync_oplog' || $_seg === '.ai-deploy-tmp'
                        || strpos($_seg, '.sync') === 0) {
                        continue 2;
                    }
                }
            }
            $files[] = $real;
        }
    } else {
        if (!_is_regular_file($dest)) {
            return fail($E['INVALID_PARAM'], 'not_regular_file',
                'Path is not a regular file: ' . $rel, [
                    'path' => $rel,
                    'special_type' => _special_kind($dest),
                    'hint' => 'search 只读取普通文件/文本; FIFO/socket/设备节点无法按行搜索。',
                ]);
        }
        $files[] = $dest;
    }

    sort($files, SORT_STRING);

    foreach ($files as $real) {
        $files_scanned++;
        $size = @filesize($real);
        if ($size === false || $size > 4 * 1024 * 1024) continue;
        // AI 优化 v4.28: 二进制文件(NUL 头探测)不进文本搜索 — 乱码行纯烧调用方 token (与 PY 端一致)
        $_probe = @file_get_contents($real, false, null, 0, 8192);
        if ($_probe !== false && strpos($_probe, "\0") !== false) continue;
        $lines = @file($real, FILE_IGNORE_NEW_LINES);
        if ($lines === false) continue;
        $rel_path = ltrim(substr($real, strlen($base)), '/\\');
        foreach ($lines as $idx => $line) {
            if (preg_match($regex_full, $line)) {
                $hit = [
                    'path'  => $rel_path,
                    'line'  => $idx + 1,
                    // 与 Python 端一致: 单条命中最多回显 500 字符, 防超长行打爆调用方上下文
                    'match' => mb_substr($line, 0, 500),
                ];
                if ($context_before || $context_after) {
                    $hit['context'] = [];
                    for ($i = max(0, $idx - $context_before); $i < $idx; $i++) {
                        $hit['context'][] = ['line' => $i + 1, 'text' => $lines[$i], 'kind' => 'before'];
                    }
                    for ($i = $idx + 1; $i <= min(count($lines) - 1, $idx + $context_after); $i++) {
                        $hit['context'][] = ['line' => $i + 1, 'text' => $lines[$i], 'kind' => 'after'];
                    }
                }
                $hits[] = $hit;
                $total_matches++;
                if (count($hits) >= $max_results) break 2;
            }
        }
    }

    // AI 易用性 v4.29: 大扫描成本自暴露 — 让调用方无需猜测就学会收窄搜索 (与 PY 端一致)
    $_elapsed = (int)((microtime(true) - $_t0) * 1000);
    $_res = [
        'path'           => $rel,
        'pattern'        => $pattern,
        'regex'          => $is_regex,
        'case_insensitive' => $case_insensitive,
        'file_pattern'   => $file_pattern,
        'files_scanned'  => $files_scanned,
        'total_matches'  => $total_matches,
        'truncated'      => $total_matches >= $max_results,
        'hits'           => $hits,
        'elapsed_ms'     => $_elapsed,
    ];
    if ($files_scanned >= 300 && !$file_pattern) {
        $_res['tip'] = sprintf('扫描 %d 个文件耗时 %dms; 加 file_pattern(如 *.php) 或缩小 path 到子目录通常可提速 3 倍以上', $files_scanned, $_elapsed);
    }
    return ok($_res);
}

function action_todo($req, $cfg) {
    global $E, $CONFIG;
    // 对外契约: path 可选, 默认 "." (与 help 一致)
    $rel = $req['path'] ?? '.';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!is_dir($dest)) return fail($E['INVALID_PARAM'], 'not_dir', 'Path must be a directory');

    $default_tags = ['TODO', 'FIXME', 'XXX', 'HACK', 'NOTE', 'BUG', 'OPTIMIZE', 'REVIEW'];
    $tags = $req['tags'] ?? $default_tags;
    // 类型校验: 非数组/空数组/含非字符串项一律 1011。
    //   旧行为里 tags=[] 会拼出空正则 () 匹配每一行 (返回大量空 tag 噪声),
    //   tags="TODO" 在 PHP 8 直接 TypeError -> 1099, AI 无法自愈。
    if (!is_array($tags) || !$tags) {
        return fail($E['INVALID_PARAM'], 'bad_tags',
            'tags 必须是字符串数组 (如 ["TODO","FIXME"]), 且不能为空');
    }
    foreach ($tags as $__t) {
        if (!is_string($__t) || $__t === '') {
            return fail($E['INVALID_PARAM'], 'bad_tags', 'tags 的每一项都必须是非空字符串');
        }
    }
    // PHP fnmatch 不支持 brace 扩展 (*.{php,js}), 用扩展名白名单代替
    $code_exts = ['php','js','ts','jsx','tsx','py','java','go','rs','c','cpp','h','css','html','vue','svelte','rb','swift','kt'];
    $file_pattern = $req['file_pattern'] ?? null;
    $max_results = intval($req['max_results'] ?? 500);
    if ($max_results < 1) {
        return fail($E['INVALID_PARAM'], 'bad_limit', 'max_results 必须 >= 1 (收到 ' . $max_results . ')');
    }

    $regex = '/(?<![\w\/@])(' . implode('|', array_map(function($t){return preg_quote($t, '/');}, $tags)) . ')(?:\s*[\(:]?\s*([^\n\r]*))?/i';
    $base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
    $items = [];
    $by_tag = [];
    $files_scanned = 0;

    $iter = new \RecursiveIteratorIterator(
        new \RecursiveDirectoryIterator($dest, \RecursiveDirectoryIterator::SKIP_DOTS),
        \RecursiveIteratorIterator::SELF_FIRST,
        \RecursiveIteratorIterator::CATCH_GET_CHILD
    );
    foreach ($iter as $info) {
        if (!$info->isFile()) continue;   // 跳过 FIFO/socket/设备节点
        $real = $info->getRealPath();
        if ($real === false) continue;
        // BUG-45 修复: 见上文 (同上的尾部分隔符修复)
        $_wd_base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
        if (strpos($real . DIRECTORY_SEPARATOR, $_wd_base) !== 0) continue;
        $fname = $info->getFilename();
        $fext = strtolower(pathinfo($fname, PATHINFO_EXTENSION));
        if ($file_pattern) {
            if (!fnmatch($file_pattern, $fname)) continue;
        } elseif (!in_array($fext, $code_exts)) continue;
        $size = @filesize($real);
        if ($size === false || $size > 1 * 1024 * 1024) continue;
        $lines = @file($real, FILE_IGNORE_NEW_LINES);
        if ($lines === false) continue;
        $files_scanned++;
        $rel_path = ltrim(substr($real, strlen($base)), '/\\');
        foreach ($lines as $idx => $line) {
            if (preg_match_all($regex, $line, $m)) {
                foreach ($m[1] as $k => $tag) {
                    $item = [
                        'path'  => $rel_path,
                        'line'  => $idx + 1,
                        'tag'   => strtoupper($tag),
                        'note'  => trim($m[2][$k] ?? ''),
                        'text'  => trim($line),
                    ];
                    $items[] = $item;
                    $tk = strtoupper($tag);
                    $by_tag[$tk] = ($by_tag[$tk] ?? 0) + 1;
                    if (count($items) >= $max_results) break 3;
                }
            }
        }
    }
    ksort($by_tag);
    return ok([
        'path'          => $rel,
        'tags'          => array_keys($by_tag),
        'by_tag'        => $by_tag,
        'files_scanned' => $files_scanned,
        'total'         => count($items),
        'items'         => $items,
    ]);
}

function action_stats($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");

    $group_by = $req['group_by'] ?? 'ext';
    if ($group_by !== 'ext' && $group_by !== 'dir') {
        return fail($E['INVALID_PARAM'], 'bad_group_by', "group_by 只支持 'ext' 或 'dir'");
    }
    $max_files = intval($req['max_files'] ?? 50000);
    if ($max_files < 1) {
        return fail($E['INVALID_PARAM'], 'bad_limit', 'max_files 必须 >= 1 (收到 ' . $max_files . ')');
    }
    $max_size  = intval($req['max_size'] ?? 8 * 1024 * 1024);
    if ($max_size < 1) {
        return fail($E['INVALID_PARAM'], 'bad_limit', 'max_size 必须 >= 1 (收到 ' . $max_size . ')');
    }
    $file_pattern = $req['file_pattern'] ?? null;

    $is_dir = is_dir($dest);
    $files = [];
    if ($is_dir) {
        $iter = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($dest, \RecursiveDirectoryIterator::SKIP_DOTS),
            \RecursiveIteratorIterator::SELF_FIRST,
            \RecursiveIteratorIterator::CATCH_GET_CHILD
        );
        foreach ($iter as $info) {
            if (!$info->isFile()) continue;   // 跳过 FIFO/socket/设备节点
            $real = $info->getRealPath();
            if ($real === false) continue;
            // BUG-45 修复 (审计 Finding 9): 缺尾部分隔符, WORK_DIR=/var/www/player 会误匹配
            // /var/www/player_secret/ 同级目录 (配合 symlink 可越权读取). 必须加 DIRECTORY_SEPARATOR.
            $_wd_base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
            if (strpos($real . DIRECTORY_SEPARATOR, $_wd_base) !== 0) continue;
            if ($file_pattern) {
                // AI 修复: 支持空格/逗号分隔多模式 (如 "*.php *.py *.js"), 任一匹配即通过
                $_fp_ok = false;
                foreach (preg_split('/[\s,]+/', trim($file_pattern)) as $_fp) {
                    if ($_fp !== '' && fnmatch($_fp, $info->getFilename())) { $_fp_ok = true; break; }
                }
                if (!$_fp_ok) continue;
            }
            $files[] = $real;
            if (count($files) >= $max_files) break;
        }
    } else {
        if (!_is_regular_file($dest)) {
            return fail($E['INVALID_PARAM'], 'not_regular_file',
                'Path is not a regular file: ' . $rel, ['path' => $rel]);
        }
        $files[] = $dest;
    }

    $total_files = 0;
    $total_bytes = 0;
    $total_lines = 0;
    $total_blank = 0;
    $total_comment = 0;
    $groups = [];
    $largest = [];

    foreach ($files as $real) {
        $size = @filesize($real);
        if ($size === false || $size > $max_size) continue;
        $ext = strtolower(pathinfo($real, PATHINFO_EXTENSION));
        $lines = @file($real, FILE_IGNORE_NEW_LINES);
        if ($lines === false) continue;
        $line_count = count($lines);
        $blank = 0; $comment = 0;
        foreach ($lines as $ln) {
            $t = trim($ln);
            if ($t === '') $blank++;
            elseif (in_array($ext, ['php','js','ts','jsx','tsx','java','go','c','cpp','rs','swift','kt'])
                && (strpos($t, '//') === 0 || strpos($t, '/*') === 0 || strpos($t, '*') === 0)) $comment++;
            elseif ($ext === 'py' && strpos($t, '#') === 0) $comment++;
        }
        $total_files++;
        $total_bytes += $size;
        $total_lines += $line_count;
        $total_blank += $blank;
        $total_comment += $comment;
        $key = $group_by === 'ext' ? $ext : ($group_by === 'dir' ? dirname($real) : $ext);
        if (!isset($groups[$key])) {
            $groups[$key] = ['files'=>0, 'bytes'=>0, 'lines'=>0, 'blank'=>0, 'comment'=>0];
        }
        $groups[$key]['files']++;
        $groups[$key]['bytes'] += $size;
        $groups[$key]['lines'] += $line_count;
        $groups[$key]['blank'] += $blank;
        $groups[$key]['comment'] += $comment;
        $largest[] = ['path' => ltrim(substr($real, strlen(rtrim($cfg['WORK_DIR'],'/\\').DIRECTORY_SEPARATOR)), '/\\'), 'bytes' => $size, 'lines' => $line_count];
    }
    usort($largest, function($a,$b){ return $b['bytes'] - $a['bytes']; });
    $largest = array_slice($largest, 0, 10);
    return ok([
        'group_by'    => $group_by,
        'total_files' => $total_files,
        'total_bytes' => $total_bytes,
        'total_lines' => $total_lines,
        'total_blank' => $total_blank,
        'total_comment' => $total_comment,
        'total_code'  => max(0, $total_lines - $total_blank - $total_comment),
        'groups'      => $groups,
        'largest'     => $largest,
    ]);
}

function action_read_range($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    if (is_dir($dest)) return fail($E['INVALID_PARAM'], 'is_dir', 'Path is a directory');
    if (($sk = _special_kind_follow($dest)) !== null) {
        return fail($E['INVALID_PARAM'], 'not_regular_file',
            "Cannot read special file '$rel' ($sk): only regular files are readable", [
                'path' => $rel, 'special_type' => $sk,
                'hint' => 'FIFO/socket/设备节点没有有限内容; 请用 exec 在服务器端读取。',
            ]);
    }
    // 二进制文件 (前 8KB 含 NUL): 按行读取只会返回乱码/控制字符, 白烧调用方上下文.
    // 与 search 的二进制探测一致; 需要原始字节请用 download.
    $__probe = @fopen($dest, 'rb');
    if ($__probe) {
        $__head = @fread($__probe, 8192);
        @fclose($__probe);
        if ($__head !== false && strpos($__head, "\0") !== false) {
            return fail($E['INVALID_PARAM'], 'binary_file',
                "File '$rel' 看起来是二进制 (前 8KB 含 NUL), 不能按行读取", [
                    'path' => $rel, 'type' => 'binary',
                    'hint' => '文本读取请用 download 取回后本地解码; 图片/压缩包等二进制请直接 download。',
                ]);
        }
    }

    $start = max(1, intval($req['start_line'] ?? 1));
    // 兼容两种参数: end_line (客户端 CLI 用) 或 count (原始 API)
    $end_line = intval($req['end_line'] ?? 0);
    if ($end_line > 0 && $end_line < $start) {
        return fail($E['INVALID_PARAM'], 'bad_range',
            "end_line ($end_line) 不能小于 start_line ($start)", [
                'hint' => '行号从 1 开始; 读取第 10-20 行用 start_line=10&end_line=20, '
                        . '或 start_line=10&count=11。',
            ]);
    }
    if ($end_line > 0) {
        $count = $end_line - $start + 1;
    } else {
        $count = intval($req['count'] ?? 200);
    }
    if ($count > 5000) $count = 5000;
    if ($count < 1) $count = 1;
    $max_line_len = intval($req['max_line_len'] ?? 2000);
    if ($max_line_len < 1) {
        return fail($E['INVALID_PARAM'], 'bad_max_line_len',
            'max_line_len 必须是 >= 1 的整数, 收到 ' . json_encode($req['max_line_len']),
            ['hint' => '默认 2000 字节; 传 0/负数/非数字会被拒绝, 避免每行都被截成 "..."。']);
    }
    $fmt = strtolower(trim((string)($req['format'] ?? 'json')));
    if ($fmt === '') $fmt = 'json';
    if ($fmt === 'lines') $fmt = 'json'; // 旧帮助里出现过 format=lines, 作为 json 的别名兼容
    if ($fmt !== 'json' && $fmt !== 'text') {
        return fail($E['INVALID_PARAM'], 'bad_format',
            "format 只支持 json 或 text, 收到 '$fmt'", [
                'hint' => '要结构化数组用 format="json" (默认), 要省 token 的纯文本块用 format="text"。',
            ]);
    }

    $fp = @fopen($dest, 'rb');
    if (!$fp) return fail($E['INTERNAL'], 'open_fail', 'Cannot open file');
    $line_no = 0; $collected = 0; $buf = [];
    $total = 0;
    $truncated_line = false;
    while (!feof($fp)) {
        $ln = fgets($fp);
        if ($ln === false) break;
        $line_no++;
        $total++;
        if ($line_no < $start) continue;
        if ($collected >= $count) continue;
        $text = rtrim($ln, "\r\n");
        // 截断按字节计长(与 Python len(bytes) 一致), 但切点遵守 UTF-8 字符边界,
        // 避免把中文/emoji 切成半个字符产生乱码
        if (strlen($text) > $max_line_len) {
            $cut = function_exists('mb_strcut') ? @mb_strcut($text, 0, $max_line_len, 'UTF-8') : false;
            if ($cut === false || ($cut === '' && $text !== '')) $cut = substr($text, 0, $max_line_len);
            $text = $cut . '...';
            $truncated_line = true;
        }
        $buf[] = ['line' => $line_no, 'text' => $text];
        $collected++;
    }
    fclose($fp);
    $last = $collected > 0 ? $buf[$collected - 1]['line'] : ($start - 1);
    $has_more = $line_no > $last;
    $out = [
        'path'           => $rel,
        'start_line'     => $start,
        // end_line 是"本次实际返回的最后一行", 不是文件总行数 (旧实现返回 line_no,
        // 一次读 10 行会得到 end_line=文件总行数, 调用方无法判断分页进度)。
        'end_line'       => $last,
        'returned'       => $collected,
        'total_lines'    => $total,
        // truncated 表示"因 count/end_line 限制还有更多行未返回", 与 has_more 等价;
        // 旧实现用 collected>=count, 正好读到文件末尾时也误报 truncated=true。
        'truncated'      => $has_more,
        'truncated_line' => $truncated_line,
        'has_more'       => $has_more,
    ];
    if ($fmt === 'text') {
        // 纯文本块: 每行 "行号<TAB>内容"。比 content[{line,text}] 数组省约 3 倍 token
        $lines_txt = [];
        foreach ($buf as $it) $lines_txt[] = $it['line'] . "\t" . $it['text'];
        $out['text'] = implode("\n", $lines_txt);
    } else {
        $out['content'] = $buf;
    }
    return ok($out);
}

function action_format($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    if (is_dir($dest)) return fail($E['INVALID_PARAM'], 'is_dir', 'Path is a directory');
    if (($sk = _special_kind_follow($dest)) !== null) {
        return fail($E['INVALID_PARAM'], 'not_regular_file',
            "Cannot format special file '$rel' ($sk)", [
                'path' => $rel, 'special_type' => $sk,
            ]);
    }

    $type = $req['type'] ?? 'auto';
    if (!is_string($type)) {
        return fail($E['INVALID_PARAM'], 'bad_type', 'type 必须是字符串 (auto/json/xml/raw...)，不能传 true/数字/数组/对象', [
            'hint' => '不传则按扩展名自动识别; 例: {"type": "json"}',
        ]);
    }
    $ext = strtolower(pathinfo($dest, PATHINFO_EXTENSION));
    if ($type === 'auto') {
        $type = in_array($ext, ['json']) ? 'json' : (in_array($ext, ['xml']) ? 'xml' : 'raw');
    }
    $content = @file_get_contents($dest);
    if ($content === false) return fail($E['INTERNAL'], 'read_fail', 'Cannot read file');

    $formatted = null;
    $valid = true;
    $error = '';
    if ($type === 'json') {
        $decoded = json_decode($content);
        if ($decoded === null && json_last_error() !== JSON_ERROR_NONE) {
            $valid = false;
            $error = json_last_error_msg();
        } else {
            $formatted = json_encode($decoded, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
        }
    } elseif ($type === 'xml') {
        $dom = new \DOMDocument('1.0', 'UTF-8');
        $prev = libxml_use_internal_errors(true);
        // 修复 XXE: 禁用网络访问和外部实体加载
        $valid = $dom->loadXML($content, LIBXML_NOBLANKS | LIBXML_NONET | LIBXML_NOENT);
        if ($valid) {
            $dom->formatOutput = true;
            $formatted = $dom->saveXML();
        } else {
            $error = 'Invalid XML';
        }
        libxml_clear_errors();
        libxml_use_internal_errors($prev);
    }

    if (!$valid) return fail($E['INVALID_PARAM'], 'parse_error', $error, ['type' => $type]);

    $changed = ($formatted !== null) && ($formatted !== $content);
    $write_flag = bool_flag($req['write'] ?? false);
    if ($write_flag === null) return bad_bool('write', $req['write']);
    $backed_up = '';
    if ($write_flag && $changed && file_exists($dest) && $cfg['BACKUP_KEEP'] > 0) {
        // P1-3 M8 fix: 写回前备份，与 upload 行为一致
        //  修复: 原只在 WORK_DIR/tools/ 找 backup.php, 标准部署工具在 sync_api.php 同级 tools/ 下
        //  → require 静默失败 → format 写回不备份 (数据丢失)。优先找本文件同目录。
        if (!function_exists('_create_backup')) {
            foreach ([__DIR__ . '/backup.php',
                      rtrim(($GLOBALS['CONFIG']['WORK_DIR'] ?? __DIR__), '/\\') . '/tools/backup.php'] as $_fmt_backup) {
                if (file_exists($_fmt_backup)) { require_once $_fmt_backup; break; }
            }
        }
        if (function_exists('_create_backup')) {
            $backed_up = _create_backup($dest, $rel, $cfg);
        }
    }
    if ($write_flag && $changed) {
        // BUG-44 修复 (审计 Finding 8): 缺 LOCK_EX, 并发 format 写同一文件会交错产生损坏文件.
        if (@file_put_contents($dest, $formatted, LOCK_EX) === false) {
            return fail($E['UPLOAD_FAILED'], 'write_fail', 'Cannot write formatted file');
        }
    }
    return ok([
        'path'     => $rel,
        'type'     => $type,
        'valid'    => $valid,
        'changed'  => $changed,
        'written'  => $write_flag && $changed,
        'before_bytes' => strlen($content),
        'after_bytes'  => $formatted !== null ? strlen($formatted) : strlen($content),
        'backed_up' => $backed_up,
        'content'  => $formatted ?? $content,
    ]);
}

function action_lint($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    if (is_dir($dest)) return fail($E['INVALID_PARAM'], 'is_dir', 'Path is a directory');
    if (($sk = _special_kind_follow($dest)) !== null) {
        return fail($E['INVALID_PARAM'], 'not_regular_file',
            "Cannot lint special file '$rel' ($sk)", [
                'path' => $rel, 'special_type' => $sk,
            ]);
    }

    $content = @file_get_contents($dest);
    if ($content === false) return fail($E['INTERNAL'], 'read_fail', 'Cannot read');
    $ext = strtolower(pathinfo($dest, PATHINFO_EXTENSION));
    $issues = [];

    if ($ext === 'php') {
        $lines = preg_split('/\r\n|\r|\n/', $content);
        $in_php = false;
        $outside_php_lines = 0;   // 只做计数, 不往 issues 里塞 (模板文件混写是合法的)
        foreach ($lines as $i => $ln) {
            $t = trim($ln);
            if (strpos($t, '<?php') === 0 || strpos($t, '<?=') === 0) $in_php = true;
            if (substr($ln, -2) === '?>') $in_php = false;
            if (!$in_php && $t !== '' && $t !== '?>' && strpos($t, '<?') !== 0) {
                $outside_php_lines++;
            }
            // 行尾空白: 只做 info 级提示, 不计入"问题"栏位的视觉压力 (trailing_whitespace 很常见, 不影响运行)
            // if (preg_match('/\s+$/', $ln)) {
            //     $issues[] = ['line' => $i + 1, 'severity' => 'info', 'rule' => 'trailing_whitespace', 'message' => 'Trailing whitespace'];
            // }
        }
        $php_bin = function_exists('_find_php_cli') ? _find_php_cli() : (PHP_BINARY ?: 'php');
        if (!$php_bin) $php_bin = 'php';
        $tmp = tempnam(sys_get_temp_dir(), 'lint_');
        @file_put_contents($tmp, $content);
        $out = []; $rc = 0;
        @exec(escapeshellarg($php_bin) . ' -l ' . escapeshellarg($tmp) . ' 2>&1', $out, $rc);
        @unlink($tmp);
        $syntax_ok = ($rc === 0);
        if (!$syntax_ok) {
            foreach ($out as $msg) {
                if (preg_match('/on line (\d+)/', $msg, $m)) {
                    $issues[] = ['line' => intval($m[1]), 'severity' => 'error', 'rule' => 'php_syntax', 'message' => trim($msg)];
                }
            }
        }
        return _lint_response($rel, [
            'language' => 'php',
            'syntax_ok' => $syntax_ok,
            'issues' => $issues,
            'stats'  => [
                'template_lines' => $outside_php_lines,   // 模板行(纯 HTML/JS) 仅作参考
            ],
            'summary' => _lint_summary($issues),
        ]);
    }

    if (in_array($ext, ['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs'])) {
        $lines = preg_split('/\r\n|\r|\n/', $content);
        $open_braces = 0; $open_brackets = 0; $open_parens = 0;
        $in_str = null;
        $i = 0; $len = strlen($content);
        while ($i < $len) {
            $c = $content[$i];
            if ($in_str) {
                if ($c === '\\') { $i += 2; continue; }
                if ($c === $in_str) $in_str = null;
                $i++; continue;
            }
            if ($c === '/' && $i + 1 < $len) {
                $n = $content[$i + 1];
                if ($n === '/') { while ($i < $len && $content[$i] !== "\n") $i++; continue; }
                if ($n === '*') { $i += 2; while ($i + 1 < $len && !($content[$i] === '*' && $content[$i+1] === '/')) $i++; $i += 2; continue; }
            }
            if ($c === '"' || $c === "'" || $c === '`') { $in_str = $c; $i++; continue; }
            if ($c === '{') $open_braces++;
            elseif ($c === '}') $open_braces--;
            elseif ($c === '[') $open_brackets++;
            elseif ($c === ']') $open_brackets--;
            elseif ($c === '(') $open_parens++;
            elseif ($c === ')') $open_parens--;
            $i++;
        }
        foreach ($lines as $idx => $line) {
            if (preg_match('/\s+$/', $line)) {
                $issues[] = ['line' => $idx + 1, 'severity' => 'info', 'rule' => 'trailing_whitespace', 'message' => 'Trailing whitespace'];
            }
        }
        if ($open_braces !== 0)   $issues[] = ['line' => 0, 'severity' => 'error', 'rule' => 'brace_balance', 'message' => "Unbalanced braces: $open_braces"];
        if ($open_brackets !== 0) $issues[] = ['line' => 0, 'severity' => 'error', 'rule' => 'bracket_balance', 'message' => "Unbalanced brackets: $open_brackets"];
        if ($open_parens !== 0)   $issues[] = ['line' => 0, 'severity' => 'error', 'rule' => 'paren_balance', 'message' => "Unbalanced parens: $open_parens"];
        if (strpos($content, 'console.log') !== false) {
            $issues[] = ['line' => 0, 'severity' => 'warning', 'rule' => 'console_log', 'message' => 'console.log found, may need cleanup'];
        }
        return _lint_response($rel, [
            'language' => 'js',
            'syntax_ok' => $open_braces === 0 && $open_brackets === 0 && $open_parens === 0,
            'issues' => $issues,
            'summary' => _lint_summary($issues),
        ]);
    }

    if ($ext === 'json') {
        json_decode($content);
        $ok = json_last_error() === JSON_ERROR_NONE;
        if (!$ok) {
            // AI 优化: 用 python3 定位 JSON 错误行号 (PHP json_decode 不报行号, python 报 "line N")
            $err_line = 0;
            $py = trim(@shell_exec('command -v python3 2>/dev/null') ?: '');
            if ($py) {
                $tmp = tempnam(sys_get_temp_dir(), 'jsonl_');
                @file_put_contents($tmp, $content);
                $out = [];
                // 修复 P3-2: $py 来自 PATH 解析, 仍用 escapeshellarg 包裹防 PATH 注入 (与下方 py 分支一致)
                @exec(escapeshellarg($py) . ' -c "import json,sys;json.load(open(sys.argv[1]))" ' . escapeshellarg($tmp) . ' 2>&1', $out, $rc2);
                @unlink($tmp);
                if (preg_match('/line\s+(\d+)/i', implode("\n", $out), $pm)) $err_line = intval($pm[1]);
            }
            $issues[] = ['line' => $err_line, 'severity' => 'error', 'rule' => 'json_syntax', 'message' => json_last_error_msg() . ($err_line ? " (near line $err_line)" : '')];
        }
        return _lint_response($rel, ['language' => 'json', 'syntax_ok' => $ok, 'issues' => $issues, 'summary' => _lint_summary($issues)]);
    }

    if ($ext === 'py') {
        $py = trim(shell_exec('command -v python3 || command -v python'));
        $syntax_ok = true; $py_msg = '';
        if ($py) {
            $tmp = tempnam(sys_get_temp_dir(), 'lint_');
            @file_put_contents($tmp, $content);
            $out = []; $rc = 0;
            @exec(escapeshellarg($py) . ' -m py_compile ' . escapeshellarg($tmp) . ' 2>&1', $out, $rc);
            @unlink($tmp);
            $syntax_ok = ($rc === 0);
            if (!$syntax_ok) $py_msg = implode('; ', $out);
        } else {
            $py_msg = 'python not installed';
        }
        if (!$syntax_ok) {
            // AI 优化: 从 py_compile 输出提取行号 "File ..., line N"
            $err_line = 0;
            if (preg_match('/line\s+(\d+)/i', $py_msg, $pm)) $err_line = intval($pm[1]);
            $issues[] = ['line' => $err_line, 'severity' => 'error', 'rule' => 'py_syntax', 'message' => $py_msg];
        }
        return _lint_response($rel, ['language' => 'py', 'syntax_ok' => $syntax_ok, 'issues' => $issues, 'summary' => _lint_summary($issues)]);
    }

    return _lint_response($rel, ['language' => $ext ?: 'unknown', 'syntax_ok' => null, 'issues' => [], 'summary' => ['error'=>0,'warning'=>0,'info'=>0], 'message' => "No linter available for .$ext"]);
}

function _lint_response($rel, $data) {
    // 机器一致的 lint 契约: 语法错误时顶层 ok=false/code=1014, 而不是 ok=true/code=0
    // 只在 issues 里放错误 (旧行为让只检查 ok/code 的 AI 把坏代码当成通过).
    global $E;
    $has_error = false;
    foreach (($data['issues'] ?? []) as $i) {
        if (($i['severity'] ?? '') === 'error') { $has_error = true; break; }
    }
    $valid = $data['syntax_ok'] ?? null;
    if ($has_error) $valid = false;
    $data['valid'] = $valid;
    if ($valid === false) {
        return fail($E['VALIDATION_FAILED'], 'syntax_error',
            "Syntax check failed: $rel", $data);
    }
    return ok($data);
}

function _lint_summary($issues) {
    $s = ['error' => 0, 'warning' => 0, 'info' => 0];
    foreach ($issues as $i) {
        $k = $i['severity'] ?? 'info';
        if (isset($s[$k])) $s[$k]++;
    }
    return $s;
}

function _doc_clean($body) {
    // 清洗 docblock 正文: 去掉每行前导 "* " 与公共缩进 (对齐 Python inspect.cleandoc 语义)。
    // 只收集紧贴 function action_* 的 docblock, 文件头注释请用 /* 而非 /**。
    $lines = preg_split('/\r\n|\r|\n/', $body);
    $out = [];
    foreach ($lines as $ln) {
        $ln = preg_replace('/^\s*\*\s?/', '', $ln);
        $out[] = rtrim($ln);
    }
    $min = null;
    for ($i = 1; $i < count($out); $i++) {
        if (trim($out[$i]) === '') continue;
        $ind = strlen($out[$i]) - strlen(ltrim($out[$i], ' '));
        if ($min === null || $ind < $min) $min = $ind;
    }
    if ($min) {
        for ($i = 1; $i < count($out); $i++) {
            $out[$i] = preg_replace('/^ {0,' . $min . '}/', '', $out[$i]);
        }
    }
    if (isset($out[0])) $out[0] = ltrim($out[0]);
    return trim(implode("\n", $out));
}

function action_doc($req, $cfg) {
    global $E, $CONFIG;
    $format = $req['format'] ?? 'json';

    // 扫描接口层 + 工具层所有 PHP 源文件, 提取紧贴 function action_* 的 docblock。
    $doc_map = [];
    $src_files = array_merge([dirname(__DIR__) . '/sync_api.php'], glob(__DIR__ . '/*.php') ?: []);
    foreach ($src_files as $sf) {
        $src = @file_get_contents($sf);
        if ($src === false) continue;
        if (!preg_match_all('#/\*\*(.*?)\*/#s', $src, $mm, PREG_OFFSET_CAPTURE)) continue;
        foreach ($mm[1] as $k => $cap) {
            $end = $mm[0][$k][1] + strlen($mm[0][$k][0]);
            $rest = substr($src, $end, 256);
            if (preg_match('/^\s*function\s+(action_\w+)\s*\(/', $rest, $fm)) {
                $doc_map[substr($fm[1], 7)] = _doc_clean($cap[0]);
            }
        }
    }

    $routes = [];
    $ROUTES_ref = $GLOBALS['ROUTES'] ?? [];
    foreach ($ROUTES_ref as $act => $fn) {
        $routes[] = [
            'action'   => $act,
            'function' => $fn,
            'doc'      => trim($doc_map[str_replace('action_', '', $fn)] ?? ''),
        ];
    }
    usort($routes, function($a, $b){ return strcmp($a['action'], $b['action']); });

    $total_actions = count($routes);
    if ($format === 'markdown') {
        $md = "# ai-sync API v{$cfg['API_VERSION']}\n\n";
        $md .= "总计 **{$total_actions}** 个 action\n\n";
        $md .= "| Action | 实现 | 说明 |\n|---|---|---|\n";
        foreach ($routes as $r) {
            $first_line = $r['doc'] ? str_replace('|', '\\|', strtok($r['doc'], "\n")) : '';
            $md .= "| `{$r['action']}` | `{$r['function']}` | $first_line |\n";
        }
        $md .= "\n## 详细说明\n\n";
        foreach ($routes as $r) {
            $md .= "### `{$r['action']}`\n\n";
            if ($r['doc']) $md .= $r['doc'] . "\n\n";
            else $md .= "_无说明_\n\n";
        }
        return ok(['format' => 'markdown', 'total' => $total_actions, 'content' => $md]);
    }
    return ok(['format' => 'json', 'total' => $total_actions, 'actions' => $routes]);
}
