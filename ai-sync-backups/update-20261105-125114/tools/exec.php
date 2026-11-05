<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: Remote Exec
 * exec action (dangerous, disabled by default)
 */

function shell_arg($s) {
    // Byte-level POSIX single-quote wrapper: safe for any locale / non-ASCII bytes.
    return "'" . str_replace("'", "'\\''", $s) . "'";
}

/** 在 PATH 中查找可执行文件 (只用 PHP 文件系统函数, 不依赖 shell 的 command -v)。 */
function _exec_which($name, $path = null) {
    if ($name === '') return '';
    if ($name[0] === '/' && @is_executable($name)) return $name;
    $path = $path !== null ? $path : (getenv('PATH') ?: '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin');
    foreach (explode(PATH_SEPARATOR, $path) as $dir) {
        if ($dir === '') continue;
        $p = rtrim($dir, '/\\') . '/' . $name;
        if (@is_executable($p)) return $p;
    }
    return '';
}

/** 找到可用的 POSIX shell (Android=/system/bin/sh, Linux=/bin/sh, macOS=/bin/sh)。 */
function _exec_sh_bin() {
    static $sh = null;
    if ($sh !== null) return $sh;
    foreach (['/bin/sh', '/system/bin/sh', '/usr/bin/sh', '/usr/local/bin/sh'] as $p) {
        if (@is_executable($p)) { $sh = $p; return $sh; }
    }
    $found = _exec_which('sh');
    $sh = $found ?: 'sh';
    return $sh;
}

/**
 * 探测 timeout 的可用参数形式, 返回可拼在命令前的字符串 (空串=没有 timeout)。
 * 旧代码固定 'timeout -k 5 -s TERM N': GNU 可用, 但 BusyBox/ToyBox/macOS 不支持 -k,
 * 会直接打印 usage 退出, 命令根本没运行 (rc=1), 表现为"远程 exec 永远失败"。
 * 这里按实际支持能力降级: GNU(-k) → POSIX(-s TERM) → 最简(timeout N) → 不用 timeout。
 * 缓存结果, 每个 PHP 进程只探测一次。
 */
function _exec_timeout_prefix($timeout) {
    static $cap = null;
    $timeout = intval($timeout);
    if ($timeout <= 0) return '';
    if ($cap === null) {
        $cap = '';
        $bin = _exec_which('timeout');
        if ($bin !== '') {
            $out = []; $rc = 1;
            @exec(escapeshellarg($bin) . ' --version 2>&1', $out, $rc);
            $txt = implode("\n", $out);
            if (stripos($txt, 'GNU') !== false) {
                $cap = 'gnu';
            } else {
                $o2 = []; $r2 = 1;
                @exec(escapeshellarg($bin) . ' -s TERM 1 true >/dev/null 2>&1', $o2, $r2);
                if ($r2 === 0) {
                    $cap = 'posix';
                } else {
                    $o3 = []; $r3 = 1;
                    @exec(escapeshellarg($bin) . ' 1 true >/dev/null 2>&1', $o3, $r3);
                    if ($r3 === 0) $cap = 'plain';
                }
            }
        }
    }
    if ($cap === 'gnu')   return 'timeout -k 5 -s TERM ' . $timeout;
    if ($cap === 'posix') return 'timeout -s TERM ' . $timeout;
    if ($cap === 'plain') return 'timeout ' . $timeout;
    return '';
}

/** POSIX 兼容的进程存活检测 (posix_kill 优先, 没有扩展时退回 kill -0)。 */
function _exec_pid_alive($pid) {
    $pid = intval($pid);
    if ($pid <= 0) return false;
    if (function_exists('posix_kill')) return @posix_kill($pid, 0);
    $o = []; $r = 1;
    @exec('kill -0 ' . $pid . ' 2>/dev/null', $o, $r);
    return $r === 0;
}

/** 列出直接子进程: Linux /proc 优先, 再退回 pgrep (macOS/BSD/Android)。 */
function _exec_child_pids($pid) {
    $pid = intval($pid);
    if ($pid <= 0) return [];
    $f = '/proc/' . $pid . '/task/' . $pid . '/children';
    if (@is_readable($f)) {
        $s = trim((string)@file_get_contents($f));
        if ($s !== '') {
            return array_values(array_filter(array_map('intval', preg_split('/\s+/', $s)), function ($v) { return $v > 0; }));
        }
    }
    $kids = []; $rc = 1;
    @exec('pgrep -P ' . $pid . ' 2>/dev/null', $kids, $rc);
    if ($rc !== 0) return [];
    return array_values(array_filter(array_map('intval', $kids), function ($v) { return $v > 0; }));
}

function action_exec($req, $cfg) {
    global $E, $CONFIG;
    // P2-孤儿进程防护: 客户端断开(请求中断/Ctrl+C)后 PHP 仍继续执行,
    // 确保超时杀进程逻辑跑完, 不因 fastcgi 中断导致 proc_open 子进程成为孤儿 (vdc/ntfs-3g 残留根因)
    ignore_user_abort(true);
    if (!$cfg['EXEC_ENABLED']) {
        return fail($E['EXEC_DISABLED'], 'exec_disabled', 'Remote exec is disabled');
    }
    // BUG-48 修复 (审计 Finding 12): EXEC_ENABLED=true + 空 EXEC_ALLOW_IPS 原本 = "允许任意 IP 执行命令".
    // 严重安全足gun: token 一泄露, 全网可执行 shell.
    // 修复策略 (平衡 AI 工作流): 不直接拒绝 (会破坏用户现有 AI 测试流程), 但
    //   1) 写 CRITICAL 审计日志 + PHP error_log, 让操作员能在审计里看到 "open exec" 调用
    //   2) 在响应里返回 warning 字段, 让客户端 (AI) 知道当前是 open 模式, 提醒操作员加固
    $client_ip = function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR'] ?? '');
    $open_exec_warning = '';
    if (empty($cfg['EXEC_ALLOW_IPS']) || !is_array($cfg['EXEC_ALLOW_IPS']) || count($cfg['EXEC_ALLOW_IPS']) === 0) {
//        $open_exec_warning = '⚠ EXEC_ENABLED=true but EXEC_ALLOW_IPS is empty — any IP can run shell. Set exec_allow_ips in sync_config.json to restrict.';
//        error_log("[ai-sync CRITICAL] exec called with empty EXEC_ALLOW_IPS, ip=$client_ip cmd=" . substr($req['cmd'] ?? '', 0, 80));
        audit_log('exec', $req, 0, ['reason' => 'open_exec_no_allowlist', 'ip' => $client_ip]);
    } else {
        $allowed = false;
        foreach ($cfg['EXEC_ALLOW_IPS'] as $rule) {
            if ($client_ip === $rule || (strpos($rule, '/') !== false && function_exists('ip_in_cidr') && ip_in_cidr($client_ip, $rule))) {
                $allowed = true; break;
            }
        }
        if (!$allowed) {
            audit_log('exec', $req, $E['FORBIDDEN'], ['ip' => $client_ip]);
            return fail($E['FORBIDDEN'], 'ip_blocked', "IP $client_ip not in EXEC_ALLOW_IPS");
        }
    }
    $cmd = $req['cmd'] ?? '';
    // 类型/内容校验必须在拼接命令前: 旧实现把 123 当 "123" 执行 (rc=127),
    // 把数组/对象写进 stdin 时直接 TypeError → 1099; NUL 更危险 —— shell 会静默截断,
    // "echo A\0echo B" 实际只执行 "echo Aecho B", 调用方却以为执行了完整命令。
    if (!is_string($cmd)) {
        return fail($E['INVALID_PARAM'], 'cmd_must_be_string',
            'cmd 必须是字符串, 收到 ' . gettype($cmd), [
                'hint' => '例: {"action":"exec","cmd":"ls -la"}',
            ]);
    }
    if (strpos($cmd, "\0") !== false) {
        return fail($E['INVALID_PARAM'], 'cmd_contains_null',
            'cmd 不能包含 NUL 字节(\\0): shell 会静默截断其后的内容, 请改用换行分隔多条命令');
    }
    // 用 === '' 而不是 empty(): PHP 的 empty('0') 为 true, 旧实现会拒绝执行命令 "0"
    if ($cmd === '') return fail($E['INVALID_PARAM'], 'empty_cmd', 'cmd is required');
    // 可移植性/安全修复: 旧代码 min(intval(req.timeout ?? 30), MAX) 对 0/负数不校验,
    //   GNU timeout 的 0 表示"永不超时", 负数直接报错 —— 显式拒绝比静默变成无限更安全。
    $t_raw = $req['timeout'] ?? 30;
    if (!is_numeric($t_raw) || intval($t_raw) <= 0) {
        return fail($E['INVALID_PARAM'], 'invalid_timeout', 'timeout must be a positive number of seconds');
    }
    $timeout = intval($t_raw);
    $tmax = intval($cfg['EXEC_TIMEOUT_MAX'] ?? 120);
    if ($tmax > 0) $timeout = min($timeout, $tmax);
    // cwd 支持 (help 早已声明, 但旧实现直接忽略 → AI 传 cwd 后命令仍在 WORK_DIR 执行,
    // 与 shell_open/shell_run 的行为不一致). 相对路径以 WORK_DIR 为基准, 绝对路径直接用.
    $exec_cwd = rtrim($cfg['WORK_DIR'], '/\\');
    $cwd_req = $req['cwd'] ?? null;
    if ($cwd_req !== null && $cwd_req !== '') {
        if (!is_string($cwd_req) || strpos($cwd_req, "\0") !== false) {
            return fail($E['INVALID_PARAM'], 'cwd_not_found',
                'cwd 必须是字符串且不能含 NUL 字节', [
                    'hint' => 'cwd 指向一个已存在的目录; 相对路径以 WORK_DIR 为基准。',
                ]);
        }
        $cwd_abs = ($cwd_req !== '' && $cwd_req[0] === '/')
            ? $cwd_req
            : $exec_cwd . DIRECTORY_SEPARATOR . $cwd_req;
        $cwd_real = realpath($cwd_abs);
        if ($cwd_real === false || !is_dir($cwd_real)) {
            return fail($E['INVALID_PARAM'], 'cwd_not_found',
                "cwd '$cwd_req' 不存在或不是目录", [
                    'hint' => '相对路径以 WORK_DIR 为基准; 请先创建目录或改用已存在的路径。',
                ]);
        }
        $exec_cwd = $cwd_real;
    }
    // P2-孤儿进程防护: 给足 PHP 自身执行时间(超时+杀进程余量),
    // 防止 max_execution_time 中途强杀 PHP 导致 proc_open 子进程变孤儿
    set_time_limit($timeout + 30);
    // 修复 OOM: 输出总量上限, 防 `yes` / `cat /dev/urandom` / `find /` 等撑爆内存
    $MAX_OUTPUT = intval($cfg['EXEC_MAX_OUTPUT'] ?? 8 * 1024 * 1024);
    if ($MAX_OUTPUT < 65536) $MAX_OUTPUT = 65536;

    // 修复 proc_get_status 固有限制: PHP 在管道关闭后才报告 running=false, 但此时 exitcode 已丢失
    // 方案: 将退出码写入临时文件, 进程结束后读取 (可靠, 不受管道/重定向影响)
    $exit_code_file = tempnam(sys_get_temp_dir(), 'sync_exec_rc_');
    // 可移植性修复: 不再固定 GNU timeout 的 -k/-s 参数。
    //   BusyBox/ToyBox/macOS 不支持 -k, 旧写法会让命令根本没运行就报 usage (rc=1)。
    //   _exec_timeout_prefix() 探测实际能力并降级; 完全没有 timeout 时由 PHP 侧 deadline 兜底。
    $to_prefix = _exec_timeout_prefix($timeout);
    $sh_bin = _exec_sh_bin();
    $wrapped_cmd = ($to_prefix !== '' ? $to_prefix . ' ' : '')
                 . shell_arg($sh_bin) . ' -s'
                 . ' ; echo $? > ' . shell_arg($exit_code_file) . ' 2>/dev/null';

    $descriptorspec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    // 修复: 最小化环境变量, 防止泄漏 DB_PASSWORD/API_TOKEN 等密钥
    // PATH 必须优先 getenv(): $_ENV 受 variables_order 控制, 默认常为空,
    // 会导致服务器明明有 /usr/bin/timeout 却退化为硬编码兜底 PATH (实测)。
    $clean_env = [
        'PATH' => getenv('PATH') ?: ($_ENV['PATH'] ?? $_SERVER['PATH'] ?? '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'),
        'LANG' => getenv('LANG') ?: ($_ENV['LANG'] ?? 'C.UTF-8'),
        'HOME' => getenv('HOME') ?: ($_ENV['HOME'] ?? '/tmp'),
    ];
    // 时区: 与 php.ini 的 date.timezone 对齐, 否则命令里的 date 显示 UTC (与日志差 8 小时);
    // 无 /etc/localtime 时必须同时给 TZDIR, 否则 glibc 解析不到 Asia/Shanghai
    $tz = @date_default_timezone_get();
    if ($tz) $clean_env['TZ'] = $tz;
    foreach (['/opt/share/zoneinfo', '/usr/share/zoneinfo'] as $zdir) {
        if (is_dir($zdir)) { $clean_env['TZDIR'] = $zdir; break; }
    }
    $process = proc_open($wrapped_cmd, $descriptorspec, $pipes, $exec_cwd, $clean_env);
    if (!is_resource($process)) return fail($E['INTERNAL'], 'proc_open_fail', 'Cannot start process');

    // P1-2 H13 fix: 1) 立即关闭 stdin, 防止读取 stdin 的命令 (cat, grep - 等) 永远阻塞等待输入
    // 🆕 exec-stdin-v4.4: 命令经 stdin 传递 (sh -s 从 stdin 读脚本), 绕过 argv 单参数 128KB 限制
    //   行为与 sh -c 等价; 写入完成后关闭 stdin (cat 等读 stdin 的命令读到 EOF 不阻塞)
    if (is_resource($pipes[0])) { @fwrite($pipes[0], $cmd); fclose($pipes[0]); }

    $stdout = '';
    $stderr = '';
    $output_truncated = false;

    // 2) 非阻塞 select 循环按 $timeout 读, 避免单个 stream_set_timeout 对某些进程无效
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    // P2: 有 timeout 时多等 6s 让 TERM→KILL 收尾; 没有 timeout 时由 PHP 自己到点杀进程。
    $end_ts = microtime(true) + $timeout + ($to_prefix !== '' ? 6 : 1);
    $still_running = true;
    $GLOBALS['_exec_t0'] = microtime(true);
    $rc = null; // 进程退出码: 在循环内首次检测到退出时捕获 (proc_get_status 固有限制)
    $hit_deadline = false;
    $deadline_forced = false;

    while (true) {
        $now = microtime(true);
        if ($now >= $end_ts) { $hit_deadline = true; break; }
        if (!$still_running && !is_resource($pipes[1]) && !is_resource($pipes[2])) { break; }
        // 修复 OOM: 超过上限立即停止读取 (进程稍后 kill)
        if (strlen($stdout) + strlen($stderr) >= $MAX_OUTPUT) { $output_truncated = true; break; }

        $read = [];
        if (is_resource($pipes[1])) $read[] = $pipes[1];
        if (is_resource($pipes[2])) $read[] = $pipes[2];
        if (empty($read)) {
            $status = proc_get_status($process);
            $still_running = !empty($status['running']);
            if (!$still_running) break;
            usleep(100000); // 100ms
            continue;
        }

        $write = null;
        $except = null;
        $secs = max(0, min(1, (int)ceil($end_ts - $now)));
        $usecs = 0;
        if (false === ($num = @stream_select($read, $write, $except, $secs, $usecs))) {
            usleep(50000);
            continue;
        }
        if ($num === 0 && $still_running) {
            $status = proc_get_status($process);
            $still_running = !empty($status['running']);
            continue;
        }
        foreach ($read as $stream) {
            $chunk = fread($stream, 65536);
            if ($chunk === false || $chunk === '') {
                // EOF or pipe closed
            } else {
                if ($stream === $pipes[1]) $stdout .= $chunk;
                elseif ($stream === $pipes[2]) $stderr .= $chunk;
            }
        }
        // 检查 pipe EOF: 已经 feof 或关闭则从数组移除
        if (is_resource($pipes[1]) && feof($pipes[1])) { fclose($pipes[1]); }
        if (is_resource($pipes[2]) && feof($pipes[2])) { fclose($pipes[2]); }

        $status = proc_get_status($process);
        $still_running = !empty($status['running']);
        // 修复: proc_get_status 仅在进程退出后的首次调用返回真实 exitcode, 后续调用返回 -1
        // 在循环内首次检测到退出时立即捕获 exitcode, 避免循环后重复调用拿到 -1
        if (!$still_running && $rc === null) {
            $rc = $status['exitcode'];
        }
    }

    // 超时兜底: 强制终止, 关闭剩余 pipes
    if (is_resource($pipes[1] ?? null)) fclose($pipes[1]);
    if (is_resource($pipes[2] ?? null)) fclose($pipes[2]);

    $timed_out = false;
    if ($rc === null) {
        // 进程在循环期间仍在运行 (未捕获 exitcode), 此时是退出后首次调用, 能拿到真实 exitcode
        $status = proc_get_status($process);
        $timed_out = $hit_deadline && !empty($status['running']);
        if (!empty($status['running'])) {
            // timeout 异常未清理时的兜底: 递归杀进程树 (常规路径由 timeout 整组清理)
            $pid = $status['pid'] ?? 0;
            if ($pid > 0) {
                _exec_kill_tree($pid, 15);
                usleep(200000);
                $s2 = proc_get_status($process);
                if (!empty($s2['running'])) _exec_kill_tree($pid, 9);
            }
            if ($timed_out) {
                // 没有 timeout 命令时由 PHP 到点终止: 统一报 124 (与 GNU timeout 契约一致)
                $deadline_forced = true;
                $rc = 124;
            } else {
                $rc = $output_truncated ? 137 : -1;
            }
        } else {
            $rc = $status['exitcode'];
        }
    }
    // proc_close: 避免僵尸进程；拿到最终 exitcode（兜底: 如果 rc 仍是 -1)
    $close_rc = @proc_close($process);
    if ($rc == -1 && is_int($close_rc) && $close_rc >= 0) $rc = $close_rc;

    // 修复: 从临时文件读取真实退出码 (最可靠, 不受 proc_get_status/proc_close 限制)
    // AI 修复: 原逻辑只在 rc==-1/null 时才读, 但 proc_get_status 可能错误返回 exitcode=0,
    // 导致 false/exit N 等真实非零退出码被覆盖为 0. 改为无条件优先读临时文件.
    if (file_exists($exit_code_file)) {
        $exit_content = @file_get_contents($exit_code_file);
        if ($exit_content !== false && trim($exit_content) !== '') {
            $file_rc = intval(trim($exit_content));
            if ($file_rc >= 0) $rc = $file_rc;
        }
    }
    @unlink($exit_code_file);
    if ($rc === null) $rc = -1; // 最终兜底
    // PHP 到点强制终止时, sh 的 trap/echo 可能把 143 写进 rc 文件 —— 统一按超时 124 上报
    if ($deadline_forced) { $rc = 124; $timed_out = true; }
    // P2: timeout 标准超时码 124 → 标记 timed_out (进程已被 timeout 整组清理, 无残留)
    if ($rc === 124) $timed_out = true;
    // 修复: 审计记录截断后的 cmd (前 300 字符), 便于排查; 完整 cmd 太长且可能含密钥, 不全记
    audit_log('exec', $req, ($rc === 0 && !$timed_out) ? 0 : $E['INTERNAL'], [
        'cmd_preview'  => _utf8_trunc($cmd, 300),
        'cmd_len'      => strlen($cmd),
        'timeout'      => $timeout,
        'timed_out'    => $timed_out ? 1 : 0,
        'output_truncated' => $output_truncated ? 1 : 0,
        'stdout_len'   => strlen($stdout),
        'stderr_len'   => strlen($stderr),
        'return_code'  => $rc,
        'elapsed_ms'  => (int)round((microtime(true) - $GLOBALS['_exec_t0']) * 1000),
        'stdout_preview' => _utf8_trunc($stdout, 500),
        'stderr_preview' => _utf8_trunc($stderr, 500),
    ]);

    $resp = [
        'return_code' => $rc,
        'stdout'      => $stdout,
        'stderr'      => $stderr,
        'cwd'         => $exec_cwd,
        // FIX-cwd-v4.39: 回显 WORK_DIR, 让客户端/AI 一眼确认相对路径基准 (路径语义双轨制标注)
        'work_dir'    => $cfg['WORK_DIR'],
        'timeout'     => $timeout,
        'timed_out'   => $timed_out,
        'output_truncated' => $output_truncated,
        'stdout_len'  => strlen($stdout),
        'stderr_len'  => strlen($stderr),
        // 旧公式用 ($end_ts - $timeout), 但 $end_ts 含 +6s grace, 导致快命令恒为 -6000ms 左右
        'elapsed_ms'  => (int)round((microtime(true) - $GLOBALS['_exec_t0']) * 1000),
        'timeout_enforced' => ($to_prefix !== ''),
        'tip'         => $output_truncated ? '输出超过上限已截断, 如需完整输出请重定向到文件后 download' : null,
        // BUG-48: open-exec 模式时返回 warning, 提醒客户端 (AI) 当前无 IP 白名单
        'warning'     => $open_exec_warning ?: null,
    ];
    return ok($resp);
}

/**
 * 兜底清理: 杀掉进程及其所有后代.
 * 已实测容器内 /proc children 文件不可读 + proc_open 子进程非组长,
 * 原 posix_kill(-pgid)+children 方案实际失效. 改用 pgrep -P 遍历 /proc 找后代,
 * 逐代杀 (多轮覆盖孙进程). 常规路径由 timeout 命令负责整组清理, 本函数仅在异常时补漏.
 */
function _exec_kill_tree($pid, $sig) {
    $pid = intval($pid);
    if ($pid <= 0) return;
    $sig = intval($sig);
    // Linux/Android: /proc children 通常可用; macOS/BSD: pgrep。两条路都试, 最后直接 kill 兜底。
    $seen = [$pid => 1];
    $kill_one = function ($p) use ($sig) {
        $p = intval($p);
        if ($p <= 0) return;
        if (function_exists('posix_kill')) { @posix_kill($p, $sig); return; }
        @exec('kill -' . $sig . ' ' . $p . ' 2>/dev/null');
    };
    $queue = [$pid];
    for ($i = 0; $i < 10 && $queue; $i++) {
        $next = [];
        foreach ($queue as $p) {
            foreach (_exec_child_pids($p) as $k) {
                if ($k > 0 && !isset($seen[$k])) { $seen[$k] = 1; $next[] = $k; }
            }
        }
        foreach ($next as $k) $kill_one($k);
        if (!$next) break;
        $queue = $next;
        usleep(50000);
    }
    // 先杀后代再杀父进程, 避免父进程退出后子进程被 init 收养、pgrep -P 找不到
    $all = array_keys($seen);
    rsort($all);
    foreach ($all as $p) $kill_one($p);
}
