<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync Tool: 持久 shell (对齐原生 ssh 的会话工具, 面向 AI 易用性)
 * 与 exec 并存: exec 是单发无状态命令; shell 是带持久 cwd/env 的会话, 可连续操作.
 * 会话内 cd / export 跨调用保持, 后台任务可 shell_job 查询 / shell_kill 终止,
 * 会话可 shell_list 列举 / shell_close 关闭。
 *
 * ⚠️ 状态持久化说明 (与 Python 端实现差异, 但对外契约一致):
 *   Python 端是长驻进程, 用全局 dict 即可跨请求保持会话。
 *   PHP 端没有长驻进程 —— php -S 每次请求重新执行脚本, PHP-FPM 多 worker 更是各进程独立,
 *   全局变量无法跨请求共享。因此这里把 sessions/jobs 落盘到
 *   WORK_DIR/ai-sync/.shell/state.json, 并用 flock 保证并发安全。
 *   这样在 php -S 与生产 PHP-FPM 下行为一致。
 */

if (!function_exists('shell_arg')) {
    function shell_arg($s) {
        // 字节级 POSIX 单引号包裹: 对任意 locale / 非 ASCII 字节安全
        return "'" . str_replace("'", "'\\''", $s) . "'";
    }
}

/** 在 PATH 中查找可执行文件 (纯 PHP, 不依赖 shell 的 command -v)。 */
function _shell_which($name, $path = null) {
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

/**
 * 选会话 shell: 有 bash 用 bash (保持旧行为/支持 bash 语法), 否则退回 POSIX sh。
 * 旧代码硬编码 bash —— Android/BusyBox/精简容器没有 bash 时 shell_run 直接 'bash: not found',
 * 整个 shell 工具不可用。
 */
function _shell_bin($cfg = null) {
    static $bin = null;
    if ($bin !== null) return $bin;
    $path = null;
    if (is_array($cfg)) {
        $env = _shell_base_env();
        $path = $env['PATH'] ?? null;
    }
    $b = _shell_which('bash', $path);
    if ($b !== '') { $bin = $b; return $bin; }
    foreach (['/bin/sh', '/system/bin/sh', '/usr/bin/sh'] as $p) {
        if (@is_executable($p)) { $bin = $p; return $bin; }
    }
    $bin = _shell_which('sh', $path) ?: 'sh';
    return $bin;
}

/** 后台任务脱离方式: setsid 优先, 没有就 nohup, 再没有就普通 & (macOS/Android 兼容)。 */
function _shell_detach_prefix() {
    static $pre = null;
    if ($pre !== null) return $pre;
    if (_shell_which('setsid') !== '') { $pre = 'setsid'; return $pre; }
    if (_shell_which('nohup') !== '') { $pre = 'nohup'; return $pre; }
    $pre = '';
    return $pre;
}

function _shell_pid_alive($pid) {
    $pid = intval($pid);
    if ($pid <= 0) return false;
    // 僵尸进程 kill -0 也返回成功, 容器/PID1 不回收时会永远"活着":
    // 先看 /proc/<pid>/stat 的状态位(Z=僵尸), 没有 /proc 的系统再退回 kill -0 (macOS 等)。
    $stat = @file_get_contents('/proc/' . $pid . '/stat');
    if (is_string($stat) && $stat !== '') {
        $p = strrpos($stat, ')');
        $rest = $p === false ? '' : ltrim(substr($stat, $p + 1));
        $state = $rest === '' ? '' : $rest[0];
        if ($state === 'Z' || $state === 'X' || $state === 'x') return false;
    }
    if (function_exists('posix_kill')) return @posix_kill($pid, 0);
    $o = []; $r = 1;
    @exec('kill -0 ' . $pid . ' 2>/dev/null', $o, $r);
    return $r === 0;
}

function _shell_child_pids($pid) {
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

if (!function_exists('_shell_kill_tree')) {
    function _shell_kill_tree($pid, $sig) {
        $pid = intval($pid);
        if ($pid <= 0) return;
        $sig = intval($sig);
        $kill_one = function ($p) use ($sig) {
            $p = intval($p);
            if ($p <= 0) return;
            if (function_exists('posix_kill')) { @posix_kill($p, $sig); return; }
            @exec('kill -' . $sig . ' ' . $p . ' 2>/dev/null');
        };
        // 先收集整棵进程树 (Linux /proc 优先, 再 pgrep), 再自底向上杀
        $seen = [$pid => 1];
        $queue = [$pid];
        for ($i = 0; $i < 10 && $queue; $i++) {
            $next = [];
            foreach ($queue as $p) {
                foreach (_shell_child_pids($p) as $k) {
                    if ($k > 0 && !isset($seen[$k])) { $seen[$k] = 1; $next[] = $k; }
                }
            }
            if (!$next) break;
            $queue = $next;
            usleep(50000);
        }
        $all = array_keys($seen);
        rsort($all);
        foreach ($all as $p) $kill_one($p);
    }
}

// ==================== 状态落盘 (跨请求 / 跨 worker 共享) ====================

function _shell_state_dir($cfg) {
    $d = rtrim($cfg['WORK_DIR'], '/\\') . '/ai-sync/.shell';
    if (!is_dir($d)) @mkdir($d, 0777, true);
    return $d;
}

function _shell_state_file($cfg) {
    return _shell_state_dir($cfg) . '/state.json';
}

/** 取排他锁, 返回锁句柄 (失败返回 null, 降级为不加锁继续) */
function _shell_lock($cfg) {
    $lf = _shell_state_file($cfg) . '.lock';
    $f = @fopen($lf, 'c');
    if ($f) @flock($f, LOCK_EX);
    return $f ?: null;
}

function _shell_unlock($f) {
    if ($f) { @flock($f, LOCK_UN); @fclose($f); }
}

function _shell_load($cfg) {
    $st = ['sessions' => [], 'jobs' => []];
    $p = _shell_state_file($cfg);
    if (file_exists($p)) {
        $raw = @file_get_contents($p);
        if (is_string($raw) && $raw !== '') {
            $j = json_decode($raw, true);
            if (is_array($j)) {
                if (isset($j['sessions']) && is_array($j['sessions'])) $st['sessions'] = $j['sessions'];
                if (isset($j['jobs']) && is_array($j['jobs'])) $st['jobs'] = $j['jobs'];
            } else {
                // 状态文件损坏: 备份后重建。
                // 不备份的话, 损坏原因(磁盘满 / 并发写坏 / 被外部误改)事后无从追查,
                // 而"会话凭空消失"对 AI 是毫无头绪的故障。
                @rename($p, $p . '.corrupt.' . time() . '.' . substr(md5($raw), 0, 6));
            }
        }
    }
    return $st;
}

/** 会话结构体兜底: 状态文件被外部改坏 / 旧版本结构残留时, 缺字段不应变成 PHP warning */
function _shell_session_norm($s, $fallback_cwd) {
    if (!is_array($s)) $s = [];
    $cwd = $s['cwd'] ?? null;
    if (!is_string($cwd) || $cwd === '') $cwd = $fallback_cwd;
    $env = (isset($s['env']) && is_array($s['env'])) ? $s['env'] : [];
    $name = (isset($s['name']) && is_string($s['name'])) ? $s['name'] : '';
    $ts = isset($s['ts']) ? intval($s['ts']) : 0;
    return ['cwd' => $cwd, 'env' => $env, 'ts' => $ts, 'name' => $name];
}

function _shell_save($cfg, $st) {
    $p = _shell_state_file($cfg);
    $tmp = $p . '.tmp.' . getmypid();
    @file_put_contents($tmp, json_encode($st, JSON_UNESCAPED_UNICODE));
    @rename($tmp, $p);
}

/** 后台任务是否已结束: 以 runner 的 EXIT trap 是否写出 rc 文件为准(比 kill -0 可靠, 不受 pid 复用影响) */
function _shell_job_finished($job) {
    if (!empty($job['done'])) return true;
    $rp = $job['rc'] ?? '';
    if ($rp && file_exists($rp)) {
        $sz = @filesize($rp);
        return ($sz !== false && $sz > 0);
    }
    return false;
}

/**
 * 回收已完成的后台任务 (与 Python 端 _shell_sweep_jobs 同策略)。
 *
 * 没有这个回收的话: 提交满 32 个任务后 jobs 永不减少, 而状态是落盘的,
 * 后台功能会永久不可用(连重启都不能恢复) —— sweep 只清 sessions 是不够的。
 * 策略: 先标记已完成, 再回收超期任务; 仍达上限时淘汰最旧的已完成任务。
 */
function _shell_sweep_jobs(&$st) {
    $now = time();
    foreach ($st['jobs'] as $jid => $j) {
        if (empty($j['done']) && _shell_job_finished($j)) {
            $j['done'] = true;
            $j['ts'] = $j['ts'] ?? $now;
            $st['jobs'][$jid] = $j;
        }
    }
    foreach ($st['jobs'] as $jid => $j) {
        if (!empty($j['done']) && ($now - ($j['ts'] ?? 0)) > 600) {   // SHELL_JOB_TTL
            _shell_job_cleanup($j, true);
            unset($st['jobs'][$jid]);
        }
    }
    if (count($st['jobs']) >= 32) {                                    // SHELL_JOBS_MAX
        $dones = [];
        foreach ($st['jobs'] as $jid => $j) if (!empty($j['done'])) $dones[] = [$jid, $j['ts'] ?? 0];
        usort($dones, function ($a, $b) { return $a[1] <=> $b[1]; });
        $need = count($st['jobs']) - 32 + 1;
        foreach (array_slice($dones, 0, max(0, $need)) as $d) {
            _shell_job_cleanup($st['jobs'][$d[0]], true);
            unset($st['jobs'][$d[0]]);
        }
    }
}

/** 会话数上限与空闲回收 (与 Python 端 SHELL_SESSIONS_MAX=32 / SHELL_IDLE_TTL=3600 一致) */
function _shell_sweep_st(&$st) {
    $now = time();
    $S = $st['sessions'];
    if (count($S) > 32) {
        uasort($S, function ($a, $b) { return ($a['ts'] ?? 0) <=> ($b['ts'] ?? 0); });
        $over = count($S) - 32;
        foreach (array_keys($S) as $k) { if ($over-- <= 0) break; unset($S[$k]); }
        $st['sessions'] = $S;
    }
    foreach ($st['sessions'] as $k => $v) {
        if (($now - ($v['ts'] ?? 0)) > 3600) unset($st['sessions'][$k]);
    }
}

// ==================== 鉴权 (与 exec 一致) ====================

function _shell_auth($req, $cfg) {
    global $E;
    if (empty($cfg['EXEC_ENABLED'])) {
        return fail($E['EXEC_DISABLED'], 'exec_disabled', 'Shell 需要 exec_enabled=true');
    }
    if (!empty($cfg['EXEC_ALLOW_IPS']) && is_array($cfg['EXEC_ALLOW_IPS'])) {
        $ip = function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR'] ?? '');
        $allowed = false;
        foreach ($cfg['EXEC_ALLOW_IPS'] as $rule) {
            if ($ip === $rule || (strpos($rule, '/') !== false && function_exists('ip_in_cidr') && ip_in_cidr($ip, $rule))) {
                $allowed = true; break;
            }
        }
        if (!$allowed) return fail($E['FORBIDDEN'], 'ip_blocked', "IP $ip not in EXEC_ALLOW_IPS");
    }
    return null;
}

function _shell_resolve_cwd($param, $base_abs) {
    if (empty($param)) return $base_abs;
    $param = (string)$param;
    if ($param[0] === '/') return realpath($param) ?: $param;
    return realpath($base_abs . '/' . $param) ?: ($base_abs . '/' . $param);
}

// ==================== 破坏性命令防呆 (只防呆, 不防恶意) ====================

/**
 * 破坏性命令规则表。level: block = 拒绝执行; warn = 照常执行, 只在响应里提醒。
 */
function _shell_guard_rules() {
    return [
        // ---- T1: 毁灭性且不可逆 -> 拒绝执行, 并在响应里给出替代方案 ----
        ['kind' => 'wipe_root', 'level' => 'block',
         're' => '/^\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(?:\/|\/\*|\/\/)\s*$/',
         'msg' => '拒绝执行: 该命令会递归强制删除根目录 /, 会瞬间毁掉整个系统且不可恢复',
         'alts' => ['把 / 换成真实目标目录, 例: rm -rf /var/log/old',
                    '删之前先 ls -la <目标> 看一眼里面到底是什么']],
        ['kind' => 'wipe_device', 'level' => 'block',
         're' => '/^\s*(?:sudo\s+)?dd\s+.*\bof\s*=\s*\/dev\/(?:sd[a-z]+\d*|nvme\d+n\d+p?\d*|vd[a-z]+|hd[a-z]+|xvd[a-z]+)\s*$/',
         'msg' => '拒绝执行: 该命令会把数据直接写入裸磁盘/分区, 会不可逆地覆盖整块盘',
         'alts' => ['要生成镜像文件就写到普通文件: dd if=xx of=/path/to/file.img',
                    '确实要整盘写入, 先用 lsblk 确认设备名, 再单独走运维流程']],
        ['kind' => 'mkfs_device', 'level' => 'block',
         're' => '/^\s*(?:sudo\s+)?(?:mkfs(?:\.[a-z0-9]+)?|mke2fs|mkswap)\s+.*\/dev\/(?:sd|nvme|vd|hd|xvd)/',
         'msg' => '拒绝执行: 该命令会格式化整个磁盘/分区, 盘上数据全部丢失且不可恢复',
         'alts' => ['只是要清空目录就用 rm -rf <目录>, 不要用 mkfs',
                    '确实要格式化, 先用 lsblk 确认是数据盘而不是系统盘']],
        ['kind' => 'overwrite_device', 'level' => 'block',
         're' => '/^\s*(?:sudo\s+)?[^>]*>\s*\/dev\/(?:sd|nvme|vd|hd|xvd)/',
         'msg' => '拒绝执行: 该命令会用输出重定向覆盖裸磁盘/分区',
         'alts' => ['重定向请指向普通文件: echo "内容" > /path/to/file']],
        ['kind' => 'fork_bomb', 'level' => 'block',
         're' => '/^\s*:\s*\(\s*\)\s*\{[^}]*\|[^}]*\}/',
         'msg' => '拒绝执行: 这是 fork 炸弹, 会瞬间耗尽进程/内存, 只能重启机器才能恢复',
         'alts' => ['要并发就用限定数量: xargs -P 4',
                    '要压测就先 ulimit -u 限制进程数再跑']],
        ['kind' => 'power_off', 'level' => 'block',
         're' => '/^\s*(?:sudo\s+)?(?:shutdown|reboot|halt|poweroff|init\s+0|systemctl\s+(?:poweroff|reboot|halt))\b/',
         'msg' => '拒绝执行: 该命令会关机/重启, 会让本工具和你的连接一起掉线, 之后就无法再操作了',
         'alts' => ['要重启某个服务就用 systemctl restart <服务名>',
                    '确实要重启机器, 请走你自己的运维渠道, 不要用本工具']],

        // ---- T2: 破坏性但可能是 AI 的真意 -> 照常执行, 只提醒 ----
        ['kind' => 'rm_rf', 'level' => 'warn',
         're' => '/^\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+\S/',
         'msg' => '这条命令会递归强制删除, 删除不可恢复',
         'hint' => '确认目标路径是你真正想删的; 建议先 ls -la <目标> 看一眼内容'],
        ['kind' => 'rm_rf_var', 'level' => 'warn',
         're' => '/^\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+[^;\s]*\$/',
         'msg' => '删除目标里含 shell 变量; 一旦变量为空或未设置, 目标会退化成 /*, 实际等于删掉整个系统',
         'hint' => '先 echo "<变量>" 打印出实际值再删; 或用 set -u 让未设置变量直接报错'],
        ['kind' => 'chmod_recursive', 'level' => 'warn',
         're' => '/^\s*(?:sudo\s+)?chmod\s+.*-R.*(?:777|a=rwx).*\s\//',
         'msg' => '递归把权限改成 777 会影响整棵目录树, 作用到 / 或系统目录时风险很高',
         'hint' => '限定到具体业务目录, 并优先考虑 755/750 而不是 777'],
        ['kind' => 'wipe_file', 'level' => 'warn',
         're' => '/^\s*(?:sudo\s+)?(?:truncate|shred)\s+.*\/dev\/(?:sd|nvme|vd|hd|xvd)/',
         'msg' => 'truncate/shred 会不可逆销毁数据',
         'hint' => '确认目标设备不是系统盘; 只是要清空文件就用 truncate -s 0 <普通文件>'],
    ];
}

/**
 * 按 ; && || | 换行 把命令切成独立的段, 逐段判断。
 *
 * 引号内的分隔符会被一并切开 —— 这只影响检测精度(可能漏检),
 * 不会误伤(每段仍按规则独立判断)。防呆场景下漏检远好于误伤:
 * 误伤会直接拦下 AI 的正常命令, 那才是真正损害可用性的事。
 */
function _shell_split_cmds($cmd) {
    $parts = preg_split('/;|\|\||&&|\||\n/', $cmd);
    return $parts ?: [$cmd];
}

/**
 * 破坏性命令扫描。返回 ['block' => rule|null, 'warn' => [rule...]]。
 *
 * 定位是防呆, 不是安全边界: shell 只给 AI 用, 而能调用 shell 的调用方本来就能
 * 执行任意命令, 想绕过这些字面量规则易如反掌(用变量 / 编码 / 先写脚本再执行)。
 * 这里要解决的是 AI 的误操作:
 *   1) 路径变量为空 -> `rm -rf $DIR/*` 退化成 `rm -rf /*`;
 *   2) 复制粘贴来的清盘 / 格式化命令被原样下发;
 *   3) 关机 / 重启会让本工具和 AI 的连接一起掉线。
 * 规则刻意保守, 只认最常见、最典型的字面量形态: 宁可漏检, 也不误伤。
 */
function _shell_guard_scan($cmd) {
    $blocks = [];
    $warns = [];
    foreach (_shell_guard_rules() as $r) {
        if ($r['level'] === 'block') $blocks[] = $r; else $warns[] = $r;
    }
    $out = ['block' => null, 'warn' => []];

    // ---- 整串预检(必须先于分段) ----
    // 血的教训: fork 炸弹的形态是 :(){:|:&};:, 它的函数体依赖 | 和 ; —— 而这两个字符
    // 正是 _shell_split_cmds 的分隔符。切完之后段是 [":(){:", ":&}", ":"], 没有任何一段
    // 含完整形态, 这条规则 100% 漏检(实测: 炸弹真的被执行了, 把机器进程表打满,
    // 双端同时 EAGAIN 掉线)。所以任何依赖"被分段切走的字符"的规则, 都只能在整串上判。
    // 这些规则都以 ^ 锚定, 整串预检是分段的超集, 不会引入新的误伤。
    foreach ($blocks as $r) {
        if (preg_match($r['re'], $cmd)) {
            $out['block'] = $r;
            return $out;
        }
    }

    foreach (_shell_split_cmds($cmd) as $seg) {
        $seg = trim($seg);
        if ($seg === '') continue;
        $blocked = false;
        foreach ($blocks as $r) {
            if (preg_match($r['re'], $seg)) {
                if (!$out['block']) $out['block'] = $r;   // 只报第一条, 避免刷屏
                $blocked = true;
                break;
            }
        }
        if ($blocked) continue;   // 已判死的段不再重复提醒
        foreach ($warns as $r) {
            if (preg_match($r['re'], $seg)) {
                if (!isset($out['warn'][$r['kind']])) $out['warn'][$r['kind']] = $r;
            }
        }
    }
    $out['warn'] = array_values($out['warn']);
    return $out;
}

/** 把扫描结果里的提醒整理成响应字段 (双端同名同结构) */
function _shell_warnings_field($warns) {
    $out = [];
    foreach ($warns as $r) {
        $out[] = ['level' => 'warn', 'kind' => $r['kind'],
                  'message' => $r['msg'], 'hint' => $r['hint'] ?? ''];
    }
    return $out;
}

// ==================== 入参校验 (与 Python 端 _shell_check_* 同契约) ====================

/**
 * id 参数类型校验 (session_id / job_id)。
 *
 * 不校验的后果: PHP 拿数组当数组键 -> "Illegal offset type" Error,
 * Python 拿 list 查 dict -> "unhashable type" TypeError —— 双端都是 1099(INTERNAL),
 * 而真实问题只是"参数类型写错了"。1099 对 AI 毫无可操作性, 1011 才有。
 * 数字宽容转字符串(保留 unknown_session 这类更有信息量的语义), 其余非字符串一律 1011。
 */
function _shell_check_id($v) {
    if ($v === null || $v === '') return ['', null];
    if (is_bool($v)) return [null, true];
    if (is_int($v) || is_float($v)) return [(string)$v, null];
    if (is_string($v)) return [$v, null];
    return [null, true];
}

/**
 * env 入参校验。返回 [归一化后的 map, 错误码 key]。
 *
 * 定位是防呆, 不是防恶意: shell 只给 AI 用, 而能调用 shell 的调用方本来就能直接
 * 执行任意命令(exec 同权限), 这里不需要、也不该做"恶意破坏拦截"。
 * 要解决的是 AI 侧的静默失败:
 *   1) 键名非法: `export 1BAD=x` 的报错被 source 的 2>/dev/null 吞掉,
 *      AI 以为变量设上了, 后续命令却读不到;
 *   2) 值不是标量 / env 不是对象: 整包 env 被静默忽略, AI 同样无从察觉。
 * 副产品: 键名限制为合法标识符后, envfile 每一行只可能是 `export NAME='value'`
 * 这一种形状, 不会被 shell 解析成别的语法。
 */
function _shell_check_env($extra) {
    if ($extra === null) return [[], null];
    if (!is_array($extra)) return [null, 'invalid_env'];
    if (empty($extra)) return [[], null];
    // JSON 数组(如 ["A=1"]) 解码后是键为 0..n-1 的连续数组: 语义上是"env 不是对象",
    // 报 invalid_env 而不是 invalid_env_key, 与 Python 端保持一致
    if (array_keys($extra) === range(0, count($extra) - 1)) return [null, 'invalid_env'];
    $out = [];
    foreach ($extra as $k => $v) {
        if (!is_string($k) || !preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $k)) return [null, 'invalid_env_key'];
        if (is_array($v) || is_object($v)) return [null, 'invalid_env_value'];
        if (is_bool($v)) $v = $v ? '1' : '';
        $out[$k] = (string)$v;
    }
    return [$out, null];
}

/**
 * cwd 入参校验: 目录必须真实存在。
 *
 * 原先目录不存在时, Popen/proc_open 拿不到 cwd 就退化成"继承服务端进程的工作目录",
 * runner 里的 cd 失败后命令继续在那个目录跑 —— 于是 AI 指定 /data 却在
 * /workspace/ai_sync_server 下执行了命令, 并且会话 cwd 还被回写成服务端的目录。
 * 这种"静默在错误目录执行"比报错危险得多, 直接 1011。
 */
function _shell_check_cwd($param, $base_abs) {
    if ($param === null || $param === '' || $param === []) return [$base_abs, null];
    if (is_bool($param) || is_array($param) || is_object($param)) return [null, 'invalid_cwd'];
    if (is_int($param) || is_float($param)) $param = (string)$param;
    if (!is_string($param)) return [null, 'invalid_cwd'];
    $abs = ($param[0] === '/') ? $param : ($base_abs . '/' . $param);
    $rp = realpath($abs);
    if ($rp === false || !is_dir($rp)) return [null, 'cwd_not_found'];
    return [$rp, null];
}

/**
 * background 归一化: 只认布尔语义的字符串。
 *
 * PHP 的 !empty("false") 与 Python 的 bool("false") 都是 true —— AI 按直觉传
 * {"background": "false"} 会把前台命令扔进后台(拿不到输出), 而 "0" 上双端恰好相反
 * (PHP empty("0") 为真 -> 前台; Python bool("0") 为真 -> 后台), 是实打实的双端漂移。
 * 返回 true/false; 无法识别返回 null(调用方报 1011)。
 */
function _shell_parse_boolish($v) {
    if ($v === null || $v === '' || $v === []) return false;
    if (is_bool($v)) return $v;
    if (is_int($v) || is_float($v)) return (intval($v) != 0);
    if (is_string($v)) {
        $s = strtolower(trim($v));
        if (in_array($s, ['1', 'true', 'yes', 'on', 'y'], true)) return true;
        if (in_array($s, ['0', 'false', 'no', 'off', 'n'], true)) return false;
        return null;
    }
    return null;
}

/** lines 解析: <=0 视为"全部"(与 timeout 不同, 0 在这里是无害且明确的含义), 非数字报错 */
function _shell_parse_lines($v) {
    if ($v === null || $v === '' || $v === []) return 0;
    if (is_bool($v)) return null;
    if (is_int($v) || is_float($v)) { $iv = intval($v); return $iv > 0 ? $iv : 0; }
    if (is_string($v)) {
        $s = trim($v);
        if ($s === '') return 0;
        if (!is_numeric($s)) return null;
        $iv = intval(floatval($s));
        return $iv > 0 ? $iv : 0;
    }
    return null;
}

// 统一的错误文案 (与 Python 端 _SHELL_ERR_MSG 逐字一致, 否则双端响应比对必然"漂移")
function _shell_err_msg($key) {
    $m = [
        'invalid_session_id' => 'session_id 必须是字符串, 例: {"session_id": "sh_abc123"}',
        'invalid_job_id'     => 'job_id 必须是字符串, 例: {"job_id": "jb_abc123"}',
        'invalid_env'        => 'env 必须是对象(键值对), 例: {"env": {"FOO": "bar"}}',
        'invalid_env_key'    => 'env 的键名必须是合法环境变量名(字母或下划线开头, 仅含字母/数字/下划线), 例: {"env": {"FOO": "bar"}}',
        'invalid_env_value'  => 'env 的值必须是字符串或数字, 例: {"env": {"FOO": "bar"}}',
        'invalid_cwd'        => 'cwd 必须是字符串(绝对路径或相对会话 cwd 的路径)',
        'cwd_not_found'      => 'cwd 指向的目录不存在或不是目录, 请先创建它再执行',
        'invalid_background' => 'background 必须是布尔值, 例: {"background": true}',
        'invalid_lines'      => 'lines 必须是正整数, 例: {"lines": 50}',
        'cmd_contains_null'  => 'cmd 不能包含 NUL 字节(\\0): shell 会静默截断其后的内容, 请改用换行分隔多条命令',
    ];
    return $m[$key] ?? '参数不合法';
}

function _shell_err($E, $key, $extra = null) {
    // fail() 的第 4 参会进 array_merge(), 传 null 会抛 TypeError -> 1099(把参数错误报成内部错误)
    return fail($E['INVALID_PARAM'], $key, _shell_err_msg($key), $extra ?: []);
}

/**
 * 生成 envfile 内容 —— 再兜一道键名过滤。
 * 入参已经过 _shell_check_env, 但会话 env 是落盘的(旧版本残留 / 被外部改写),
 * 这里统一按合法标识符过滤, 保证写进脚本的每一行都是 `export NAME='value'`
 * 这一种形状, 不会退化成别的 shell 语法。
 */
function _shell_envfile_content($env) {
    $out = '';
    foreach (($env ?: []) as $k => $v) {
        $ks = (string)$k;
        if (!preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $ks)) continue;
        if (is_array($v) || is_object($v)) $v = '';
        if (is_bool($v)) $v = $v ? '1' : '';
        $out .= 'export ' . $ks . '=' . shell_arg((string)$v) . "\n";
    }
    return $out;
}

/**
 * timeout 容错解析: 接受 int/float/数字字符串('30' 很常见), 其它返回 null 交给调用方报参数错误。
 * 直接 intval("abc") 会得到 0, 而 GNU timeout 的 0 表示"不限制" —— 静默把超时变成永不超时,
 * 比报错危险得多。与 Python 端 _shell_parse_timeout 同契约。
 */
function _shell_parse_timeout($v) {
    if ($v === null) return 30;
    if (is_bool($v)) return 30;
    if (is_int($v) || is_float($v)) return intval($v) > 0 ? intval($v) : null;
    if (is_string($v)) {
        $s = trim($v);
        if ($s === '') return 30;
        if (!is_numeric($s)) return null;
        $iv = intval(floatval($s));
        // <=0 一律拒绝: GNU timeout 的 0 表示"不限制", 静默变成永不超时, 比报错危险得多
        return $iv > 0 ? $iv : null;
    }
    return null;
}

function _shell_base_env() {
    // getenv() 必须放在第一位: PHP 的 $_ENV 受 variables_order 控制, 默认配置下常常是空数组,
    // 只信 $_ENV 会让 shell 里的 PATH 退化成下面的兜底值 —— 表现为"同一条命令 PY 端能跑、
    // PHP 端 command not found"(例如装在 pyenv 下的 python3.11)。
    $path = getenv('PATH');
    if (!$path) $path = $_ENV['PATH'] ?? ($_SERVER['PATH'] ?? '');
    if (!$path) $path = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin';
    $lang = getenv('LANG');
    if (!$lang) $lang = $_ENV['LANG'] ?? 'C.UTF-8';
    $home = getenv('HOME');
    if (!$home) $home = $_ENV['HOME'] ?? '/tmp';
    $env = ['PATH' => $path, 'LANG' => $lang, 'HOME' => $home];
    // 时区: 与 php.ini 的 date.timezone 对齐; 无 /etc/localtime 时必须同时给 TZDIR,
    // 否则 glibc 解析不到 Asia/Shanghai, shell 里 date 会和日志差 8 小时
    $tz = @date_default_timezone_get();
    if ($tz) $env['TZ'] = $tz;
    foreach (['/opt/share/zoneinfo', '/usr/share/zoneinfo'] as $zdir) {
        if (is_dir($zdir)) { $env['TZDIR'] = $zdir; break; }
    }
    return $env;
}

function _shell_merge_env($session, $req) {
    $base = _shell_base_env();
    foreach (($session['env'] ?? []) as $k => $v) $base[$k] = $v;
    $extra = $req['env'] ?? null;
    if (is_array($extra)) foreach ($extra as $k => $v) $base[$k] = $v;
    return $base;
}

function _parse_export_p($s) {
    $env = [];
    foreach (explode("\n", (string)$s) as $line) {
        $line = trim($line);
        // bash: declare -x K="v"   dash/ash/mksh: export K='v' 或 export K=v
        if (strpos($line, 'declare -x ') === 0) {
            $body = substr($line, strlen('declare -x '));
        } elseif (strpos($line, 'export ') === 0) {
            $body = substr($line, strlen('export '));
        } else {
            continue;
        }
        if (!preg_match('/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/', $body, $m)) continue;
        $k = $m[1]; $v = $m[2];
        if (strlen($v) >= 2) {
            $q = $v[0];
            if (($q === '"' || $q === "'") && substr($v, -1) === $q) {
                $v = substr($v, 1, -1);
                if ($q === '"') {
                    $v = str_replace(['\\"', '\\\\', '\\$', '\\`'], ['"', '\\', '$', '`'], $v);
                } else {
                    // POSIX shell 两种自嵌套单引号写法都要还原:
                    //   ash/BusyBox: 'it'\''s'     dash: 'it'"'"'s'
                    $v = str_replace("'\\''", "'", $v);
                    $v = str_replace("'\"'\"'", "'", $v);
                }
            }
        }
        $env[$k] = $v;
    }
    return $env;
}

/** 剔除 shell 自身簿记变量, 避免把临时文件路径 / 层数等噪声写进会话 env */
function _shell_env_clean($env) {
    $drop = ['RC_PATH' => 1, 'CWD_PATH' => 1, 'ENV_PATH' => 1, 'SHLVL' => 1, 'OLDPWD' => 1, 'PWD' => 1, '_' => 1];
    $out = [];
    foreach (($env ?: []) as $k => $v) {
        if (isset($drop[$k])) continue;
        $out[$k] = $v;
    }
    return $out;
}

function _shell_capture($rc_path, $cwd_path, $envout_path, $fallback) {
    $rc = 0; $new_cwd = $fallback; $env = [];
    if (file_exists($rc_path)) {
        $c = @file_get_contents($rc_path);
        if ($c !== false && preg_match('/AI_SYNC_RC=(\d+)/', $c, $m)) $rc = intval($m[1]);
    }
    if (file_exists($cwd_path)) {
        $p = trim(@file_get_contents($cwd_path));
        if ($p !== '') $new_cwd = $p;
    }
    if (file_exists($envout_path)) {
        $env = _parse_export_p(@file_get_contents($envout_path));
    }
    return [$rc, $new_cwd, $env];
}

function _shell_stream($process, $pipes, $timeout, $max_output, $grace = 6) {
    $stdout = ''; $stderr = ''; $truncated = false; $hit_deadline = false;
    // proc_close() 在之前调用过 proc_get_status() 的情况下会返回 -1 (退出码已被 reap 取走),
    // 这是 PHP 的既定行为, 在 7.4 上必然触发 -> 全程缓存 status 里的真实退出码用于回退。
    $last_exit = -1;
    $mark_exit = function ($st) use (&$last_exit) {
        if (is_array($st) && empty($st['running']) && isset($st['exitcode'])) {
            $v = intval($st['exitcode']);
            if ($v >= 0) $last_exit = $v;
        }
    };
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $end_ts = microtime(true) + $timeout + $grace;
    $running = true;
    while (true) {
        $now = microtime(true);
        if ($now >= $end_ts) { $hit_deadline = true; break; }
        if (strlen($stdout) + strlen($stderr) >= $max_output) { $truncated = true; break; }
        $read = [];
        if (is_resource($pipes[1])) $read[] = $pipes[1];
        if (is_resource($pipes[2])) $read[] = $pipes[2];
        if (empty($read)) {
            $st = proc_get_status($process);
            $running = !empty($st['running']);
            if (!$running) { $mark_exit($st); break; }
            usleep(100000);
            continue;
        }
        $w = null; $e = null;
        $secs = max(0, min(1, (int)ceil($end_ts - $now)));
        if (false === @stream_select($read, $w, $e, $secs, 0)) { usleep(50000); continue; }
        foreach ($read as $stream) {
            $chunk = fread($stream, 65536);
            if ($chunk === false || $chunk === '') continue;
            if ($stream === $pipes[1]) $stdout .= $chunk; else $stderr .= $chunk;
        }
        if (is_resource($pipes[1]) && feof($pipes[1])) fclose($pipes[1]);
        if (is_resource($pipes[2]) && feof($pipes[2])) fclose($pipes[2]);
        $st = proc_get_status($process);
        $running = !empty($st['running']);
        $mark_exit($st);
    }
    if (is_resource($pipes[1] ?? null)) @fclose($pipes[1]);
    if (is_resource($pipes[2] ?? null)) @fclose($pipes[2]);
    if ($running) {
        $st = proc_get_status($process);
        if (!empty($st['running'])) {
            $hit_deadline = true;
            _shell_kill_tree($st['pid'] ?? 0, 15); usleep(200000); _shell_kill_tree($st['pid'] ?? 0, 9);
            usleep(200000);
            $mark_exit(proc_get_status($process));  // 被信号杀掉后再取一次
        }
    }
    $exit_code = @proc_close($process);
    if ($exit_code < 0 && $last_exit >= 0) $exit_code = $last_exit;
    return [$stdout, $stderr, $truncated, $exit_code, $hit_deadline];
}

// ==================== actions ====================

function action_shell_open($req, $cfg) {
    global $E;
    $err = _shell_auth($req, $cfg);
    if ($err) return $err;

    $lk = _shell_lock($cfg);
    $st = _shell_load($cfg);
    _shell_sweep_st($st);

    $sid = $req['session_id'] ?? $req['name'] ?? '';
    list($sid, $bad) = _shell_check_id($sid);
    if ($bad) { _shell_unlock($lk); return _shell_err($E, 'invalid_session_id'); }
    if (!$sid) $sid = 'sh_' . substr(md5(uniqid('', true)), 0, 12);

    if (isset($st['sessions'][$sid])) {
        $cur = _shell_session_norm($st['sessions'][$sid], realpath(rtrim($cfg['WORK_DIR'], '/')) ?: $cfg['WORK_DIR']);
        $st['sessions'][$sid] = $cur;
        $st['sessions'][$sid]['ts'] = time();
        _shell_save($cfg, $st); _shell_unlock($lk);
        $note = null;
        // 复用会话时若请求里还带了 cwd/env, 它们不会生效(会话已有自己的状态) —— 说清楚,
        // 否则 AI 会以为设置成功了。cwd/env 只在新开会话时生效。
        if (!empty($req['cwd']) || !empty($req['env'])) {
            $note = '会话已存在, 本次的 cwd/env 未生效(会话沿用原有状态); 需要新的 cwd/env 请先 shell_close 再 shell_open';
        }
        return ok(['session_id' => $sid, 'cwd' => $cur['cwd'], 'name' => $cur['name'], 'reused' => true, 'note' => $note]);
    }

    $base_abs = realpath(rtrim($cfg['WORK_DIR'], '/')) ?: $cfg['WORK_DIR'];
    list($start_abs, $ekey) = _shell_check_cwd($req['cwd'] ?? null, $base_abs);
    if ($ekey) { _shell_unlock($lk); return _shell_err($E, $ekey); }
    list($init_env, $ekey) = _shell_check_env($req['env'] ?? null);
    if ($ekey) { _shell_unlock($lk); return _shell_err($E, $ekey); }
    $name = (isset($req['name']) && is_string($req['name'])) ? $req['name'] : '';
    $st['sessions'][$sid] = ['cwd' => $start_abs, 'env' => $init_env, 'ts' => time(), 'name' => $name];
    _shell_save($cfg, $st); _shell_unlock($lk);

    return ok(['session_id' => $sid, 'cwd' => $start_abs, 'name' => $name, 'reused' => false,
               'tip' => 'action=shell_run 在会话内执行; cwd/export 跨调用保持']);
}

function action_shell_run($req, $cfg) {
    global $E;
    $err = _shell_auth($req, $cfg);
    if ($err) return $err;

    $lk = _shell_lock($cfg);
    $st = _shell_load($cfg);
    $base_abs = realpath(rtrim($cfg['WORK_DIR'], '/')) ?: $cfg['WORK_DIR'];
    $sid = $req['session_id'] ?? '';
    list($sid, $bad) = _shell_check_id($sid);
    if ($bad) { _shell_unlock($lk); return _shell_err($E, 'invalid_session_id'); }
    if (empty($sid) || !isset($st['sessions'][$sid])) {
        _shell_unlock($lk);
        return fail($E['INVALID_PARAM'], 'unknown_session', "session_id '$sid' 不存在, 请先 shell_open",
                    ['available' => array_slice(array_keys($st['sessions']), 0, 20)]);
    }
    $st['sessions'][$sid] = _shell_session_norm($st['sessions'][$sid], $base_abs);
    $st['sessions'][$sid]['ts'] = time();
    $cmd = $req['cmd'] ?? '';
    if (!is_string($cmd)) {
        _shell_unlock($lk);
        // 措辞与 Python 端逐字一致(不暴露 gettype 的 integer 之类语言特有类型名)
        return fail($E['INVALID_PARAM'], 'cmd_must_be_string',
                    'cmd 必须是字符串, 例: {"cmd": "ls -la"}');
    }
    if ($cmd === '') { _shell_unlock($lk); return fail($E['INVALID_PARAM'], 'empty_cmd', 'cmd is required'); }
    if (strpos($cmd, "\0") !== false) {
        // bash 会把 "echo A<NUL>echo B" 静默拼成 "echo Aecho B" —— 命令语义被改写却不报错
        _shell_unlock($lk);
        return _shell_err($E, 'cmd_contains_null');
    }
    // 破坏性命令防呆: T1(毁灭性) 直接拒绝并给替代方案; T2(破坏性但可能是真意) 放行但提醒
    $_g = _shell_guard_scan($cmd);
    if ($_g['block']) {
        _shell_unlock($lk);
        return fail($E['FORBIDDEN'], 'destructive_blocked', $_g['block']['msg'], [
            'guard' => ['level' => 'block', 'kind' => $_g['block']['kind']],
            'alternatives' => $_g['block']['alts'],
            'fix_hint' => implode('; ', $_g['block']['alts']),
        ]);
    }
    $_tmo = _shell_parse_timeout($req['timeout'] ?? 30);
    if ($_tmo === null) {
        _shell_unlock($lk);
        return fail($E['INVALID_PARAM'], 'invalid_timeout',
                    'timeout 必须是数字(秒), 例: {"timeout": 30}');
    }
    $timeout = min($_tmo, intval($cfg['EXEC_TIMEOUT_MAX'] ?? 120));
    $max_output = max(65536, intval($cfg['EXEC_MAX_OUTPUT'] ?? 8 * 1024 * 1024));
    list($start_abs, $ekey) = _shell_check_cwd($req['cwd'] ?? null, $st['sessions'][$sid]['cwd']);
    if ($ekey) { _shell_unlock($lk); return _shell_err($E, $ekey); }
    list($extra_env, $ekey) = _shell_check_env($req['env'] ?? null);
    if ($ekey) { _shell_unlock($lk); return _shell_err($E, $ekey); }
    $_bg = _shell_parse_boolish($req['background'] ?? null);
    if ($_bg === null) { _shell_unlock($lk); return _shell_err($E, 'invalid_background'); }
    $env = _shell_merge_env($st['sessions'][$sid], ['env' => $extra_env]);
    _shell_save($cfg, $st);
    // ★ 立刻释放锁: 命令执行可能持续几十秒(最长 EXEC_TIMEOUT_MAX), 期间绝不能持有排他锁,
    //   否则一条慢命令会阻塞其它所有会话的请求 —— 实测过: sleep 8 会把并发的 echo 拖到 7s。
    //   锁只在下面"读-改-写状态"的极短临界区内使用。
    _shell_unlock($lk);

    $envfile = tempnam(sys_get_temp_dir(), 'shell_env_');
    $h = fopen($envfile, 'w');
    fwrite($h, _shell_envfile_content($env));
    fclose($h);
    $rc_path = tempnam(sys_get_temp_dir(), 'shell_rc_');
    $cwd_path = tempnam(sys_get_temp_dir(), 'shell_cwd_');
    $envout_path = tempnam(sys_get_temp_dir(), 'shell_envout_');
    // cmdfile: EXIT trap 回写 rc/cwd/env。路径必须直接写死进 trap(与 Python 端一致),
    //   不能用 "$RC_PATH" 环境变量 —— 会话 env 会把上一轮 export -p 里的旧路径带回来,
    //   runner 里 source envfile 时会覆盖掉本次的新路径, 导致 trap 写进已删除的旧文件,
    //   服务端读到的永远是空文件 (表现为: rc 恒为 0 / cwd 不更新 / export 不生效)。
    //   写死路径同时天然支持 exit, 也不受命令里大括号干扰。
    $cmdfile = tempnam(sys_get_temp_dir(), 'shell_cmd_');
    $cf = fopen($cmdfile, 'w');
    fwrite($cf, "trap 'printf \"AI_SYNC_RC=%d\\n\" \"\$?\" > " . shell_arg($rc_path)
              . "; printf \"%s\" \"\$(pwd)\" > " . shell_arg($cwd_path)
              . "; export -p > " . shell_arg($envout_path) . "' EXIT\n");
    fwrite($cf, $cmd . "\n");
    fclose($cf);
    // 可移植性: bash 不存在时退回 sh; 没有 setsid 时用 nohup/普通后台。
    // 超时不用外部 timeout 命令(各系统 exit code/信号分发不一致, 且杀掉 runner 后
    //   cmdfile 的 EXIT trap 常把 rc 记成 0) —— 改为 runner 内纯 POSIX sh 看门狗:
    //   超时后强制写 rc=124, 结果确定且不依赖 timeout/setsid/GNU 工具。
    $shell_bin = _shell_bin($cfg);
    $has_timeout = ($timeout > 0);
    $timeout_flag = tempnam(sys_get_temp_dir(), 'shell_to_');
    $done_flag = tempnam(sys_get_temp_dir(), 'shell_ok_');
    $runner = tempnam(sys_get_temp_dir(), 'shell_run_');
    // FIX-env-v4.39: 对齐 sshd 会话环境 — USER/LOGNAME 由登录进程注入, PHP-FPM/Apache 进程
    //   启动的 shell 中为空; git/make/crontab 等工具依赖。按当前用户补导出, 会话 envfile
    //   (export -p 持久化) 后 source 可覆盖, 顺序上默认值在前。
    $run_line = 'cd ' . shell_arg($start_abs) . "\n"
        . 'export USER=$(id -un 2>/dev/null); export LOGNAME=$USER;' . "\n"
        . '. ' . shell_arg($envfile) . " 2>/dev/null\n";
    if ($has_timeout) {
        // 超时必须连同命令的子进程一起收掉: 只 kill 直接子 shell 会留下孤儿 sleep/服务进程。
        // 子进程清单优先 /proc/<pid>/task/<pid>/children (Linux/Android), 没有该文件时退 pgrep -P
        // (macOS/BusyBox), 不依赖 setsid/进程组。先杀父再杀子: 避免子进程被 TERM 后父 shell
        // 继续执行后续命令(如 `sleep 30; echo done` 会把 done 也跑出来)。
        $kids_code = "  __aik_kids=\"\"\n"
            . "  if [ -r \"/proc/\$__aik_p/task/\$__aik_p/children\" ]; then\n"
            . "    __aik_kids=\$(cat \"/proc/\$__aik_p/task/\$__aik_p/children\" 2>/dev/null)\n"
            . "  elif command -v pgrep >/dev/null 2>&1; then\n"
            . "    __aik_kids=\$(pgrep -P \"\$__aik_p\" 2>/dev/null)\n"
            . "  fi\n";
        $run_line .= "__AISYNC_KILL_TREE() {\n"
            . "  __aik_p=\$1; __aik_s=\$2\n"
            . $kids_code
            . "  kill -\$__aik_s \"\$__aik_p\" 2>/dev/null\n"
            . "  for __aik_c in \$__aik_kids; do __AISYNC_KILL_TREE \"\$__aik_c\" \"\$__aik_s\"; done\n"
            . "}\n"
            . "__AISYNC_KILL_KIDS() {\n"
            . "  __aik_p=\$1; __aik_s=\$2\n"
            . $kids_code
            . "  for __aik_c in \$__aik_kids; do __AISYNC_KILL_TREE \"\$__aik_c\" \"\$__aik_s\"; done\n"
            . "}\n"
            . 'rm -f ' . shell_arg($timeout_flag) . ' ' . shell_arg($done_flag) . "\n"
            . shell_arg($shell_bin) . ' ' . shell_arg($cmdfile) . " & __AISYNC_P=\$!\n"
            // 看门狗每秒查一次 done 标记: 命令正常结束由 runner 写 done 并回收看门狗,
            // 不用 kill 硬杀 (sh 会往任务 stderr 打 'Terminated', 污染命令输出)。
            . '( __ai_i=0; while [ "$__ai_i" -lt ' . intval($timeout) . ' ] && [ ! -f ' . shell_arg($done_flag) . ' ]; do sleep 1; __ai_i=$((__ai_i+1)); done; '
            . 'if [ ! -f ' . shell_arg($done_flag) . ' ]; then : > ' . shell_arg($timeout_flag)
            . '; __AISYNC_KILL_TREE "$__AISYNC_P" TERM; sleep 2; __AISYNC_KILL_TREE "$__AISYNC_P" KILL; fi )'
            . " >/dev/null 2>&1 </dev/null & __AISYNC_W=\$!\n"
            // wait 的 stderr 重定向: 被 TERM 的后台任务会触发 sh 打印 'Terminated'
            . 'wait "$__AISYNC_P" 2>/dev/null; __AISYNC_RC=$?' . "\n"
            . ': > ' . shell_arg($done_flag) . "\n"
            . '__AISYNC_KILL_KIDS "$__AISYNC_W" TERM; wait "$__AISYNC_W" 2>/dev/null' . "\n"
            . 'if [ -f ' . shell_arg($timeout_flag) . " ]; then\n"
            . "  printf 'AI_SYNC_RC=%d\\n' 124 > " . shell_arg($rc_path) . "\n"
            . '  printf \'%s\' "$(pwd)" > ' . shell_arg($cwd_path) . "\n"
            . '  export -p > ' . shell_arg($envout_path) . "\n"
            . "  exit 124\n"
            . "fi\n"
            . "exit \"\$__AISYNC_RC\"\n";
    } else {
        $run_line .= shell_arg($shell_bin) . ' ' . shell_arg($cmdfile) . "\n";
    }
    $rf = fopen($runner, 'w');
    fwrite($rf, $run_line);
    fclose($rf);
    // 注: 不再把 RC_PATH/CWD_PATH/ENV_PATH 塞进 env —— 它们会经 export -p 回写到会话 env,
    // 污染下一次命令的真实环境 (用户 env 里出现 /tmp/shell_rc_xxx 之类的临时路径)。

    $t0 = microtime(true);

    // ---------- 后台任务 ----------
    if ($_bg) {
        $jid = 'jb_' . substr(md5(uniqid('', true)), 0, 12);
        $d = _shell_state_dir($cfg);
        $out = $d . '/' . $jid . '.out'; $errf = $d . '/' . $jid . '.err'; $pidfile = $d . '/' . $jid . '.pid';
        @unlink($pidfile);
        // setsid 脱离会话(父进程退出不受影响); $! 即该进程 pid; 超时由 runner 内看门狗强制
        // 无 setsid 时退回 nohup, 再无则直接后台运行。
        $detach = _shell_detach_prefix();
        $wrapper = ($detach !== '' ? $detach . ' ' : '')
                 . shell_arg($shell_bin) . ' ' . shell_arg($runner)
                 . ' > ' . shell_arg($out) . ' 2> ' . shell_arg($errf) . ' < /dev/null & echo $! > ' . shell_arg($pidfile);
        $wp = [];
        // 启动分离进程 (不持锁)
        $p = proc_open($wrapper, [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $wp, null, _shell_base_env());
        if (is_resource($p)) { fclose($wp[0]); stream_get_contents($wp[1]); fclose($wp[1]); fclose($wp[2]); proc_close($p); }
        $pid = 0;
        if (file_exists($pidfile)) { $pid = intval(trim(@file_get_contents($pidfile))); @unlink($pidfile); }

        // 登记任务 (短临界区)
        $lk2 = _shell_lock($cfg);
        $st = _shell_load($cfg);
        if (count($st['jobs']) >= 32) _shell_sweep_jobs($st);
        if (count($st['jobs']) >= 32) {
            _shell_save($cfg, $st); _shell_unlock($lk2);
            // 名额没拿到: 已经启动的进程必须收掉, 否则变成查不到也杀不掉的野进程
            if ($pid > 0) { _shell_kill_tree($pid, 15); usleep(200000); _shell_kill_tree($pid, 9); }
            foreach ([$out, $errf] as $pp) @unlink($pp);
            foreach ([$envfile, $cmdfile, $runner, $rc_path, $cwd_path, $envout_path, $timeout_flag, $done_flag] as $pp) @unlink($pp);
            return fail(isset($E['RATE_LIMITED']) ? $E['RATE_LIMITED'] : 1099, 'too_many_jobs',
                        '后台任务数已达上限(32), 且均无已完成可回收的任务; 先 shell_job 取走结果或 shell_kill 终止后重试');
        }
        $st['jobs'][$jid] = ['pid' => $pid, 'out' => $out, 'err' => $errf, 'rc' => $rc_path, 'cwd' => $cwd_path,
                    'envout' => $envout_path, 'temps' => [$envfile, $cmdfile, $runner, $timeout_flag, $done_flag], 'cmd' => $cmd,
                    'timeout' => $timeout, 'session_id' => $sid, 'ts' => time(), 'done' => false, 'cleaned' => false,
                    'start_cwd' => $start_abs];
        _shell_save($cfg, $st); _shell_unlock($lk2);
        // 注意: 临时脚本不能立刻删(分离进程尚未读取), 待任务收割时再清理 (与 PY 端一致)
        $tip = 'action=shell_job 轮询(job_id); action=shell_kill 终止';
        if (!empty($_g['warn'])) {
            $tip = '[提醒] ' . $_g['warn'][0]['msg'] . '; ' . ($_g['warn'][0]['hint'] ?? '');
        }
        return ok(['job_id' => $jid, 'background' => true, 'session_id' => $sid, 'cwd' => $start_abs, 'timeout' => $timeout,
                   'timeout_enforced' => $has_timeout,
                   'warnings' => _shell_warnings_field($_g['warn']), 'tip' => $tip]);
    }

    // ---------- 前台执行 ----------
    $descriptorspec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $proc_cmd = shell_arg($shell_bin) . ' ' . shell_arg($runner);
    $process = proc_open($proc_cmd, $descriptorspec, $pipes, null, $env);
    if (!is_resource($process)) {
        return fail($E['INTERNAL'], 'proc_open_fail', 'Cannot start process');
    }
    $pst = proc_get_status($process);
    $child_pid = intval($pst['pid'] ?? 0);
    if (is_resource($pipes[0])) fclose($pipes[0]);
    // 执行期间不持锁 —— 慢命令不会拖住别的会话
    // 没有 timeout 命令时只能由 PHP 侧在 timeout+1s 处强制收尾, 并报 rc=124。
    list($stdout, $stderr, $truncated, $proc_rc, $hit_deadline) = _shell_stream($process, $pipes, $timeout, $max_output, $has_timeout ? 6 : 1);
    list($real_rc, $new_cwd, $new_env) = _shell_capture($rc_path, $cwd_path, $envout_path, $start_abs);
    $new_env = _shell_env_clean($new_env);
    // 回写 cwd/env (重新载入再写回, 避免覆盖并发期间其他会话的变更)
    $lk2 = _shell_lock($cfg);
    $st2 = _shell_load($cfg);
    if (isset($st2['sessions'][$sid])) {
        $st2['sessions'][$sid]['cwd'] = $new_cwd;
        $st2['sessions'][$sid]['env'] = $new_env;
        $st2['sessions'][$sid]['ts'] = time();
    }
    _shell_save($cfg, $st2); _shell_unlock($lk2);

    // 与 Python 端一致: 以进程真实退出码为基准 (timeout 超时时是 124),
    // 再用 trap 捕获到的 rc 覆盖 (trap 能拿到 exit N 这类进程码里体现不出的值)。
    // 只信 trap 文件是不够的: 进程被信号杀掉时 trap 可能来不及写, 文件为空会误报 rc=0。
    $rc = $proc_rc;
    if ($real_rc != 0) $rc = $real_rc;
    if ($hit_deadline) {
        $rc = 124;  // PHP 侧到达截止时间强制收尾 = 超时
    } elseif ($rc < 0) {
        // 兜底: proc_close 返回 -1 且 trap 也没写到值时, 按是否超时推断,
        // 不要把"拿不到退出码"原样报成 -1 —— 那会让 AI 误判命令失败
        $rc = ((microtime(true) - $t0) >= $timeout) ? 124 : 0;
    }
    $timed_out = ($rc == 124);
    foreach ([$envfile, $cmdfile, $runner, $rc_path, $cwd_path, $envout_path, $timeout_flag, $done_flag] as $pp) @unlink($pp);
    audit_log('shell_run', $req, ($rc == 0 && !$timed_out) ? 0 : $E['INTERNAL'], [
        'session_id' => $sid, 'cmd_preview' => mb_substr($cmd, 0, 300), 'return_code' => $rc,
        'timed_out' => $timed_out ? 1 : 0, 'cwd' => $new_cwd,
        'elapsed_ms' => (int)round((microtime(true) - $t0) * 1000),
    ]);
    // 提醒放在 tip 位: 它是响应里最醒目的提示位, AI 扫一眼就能看到;
    // 输出截断的提示优先级更高, 两者冲突时保留截断提示(提醒仍完整留在 warnings 里)
    $tip = $truncated ? '输出超过上限已截断, 重定向到文件后 download' : null;
    if ($tip === null && !empty($_g['warn'])) {
        $tip = '[提醒] ' . $_g['warn'][0]['msg'] . '; ' . ($_g['warn'][0]['hint'] ?? '');
    }
    return ok(['session_id' => $sid, 'return_code' => $rc, 'stdout' => $stdout, 'stderr' => $stderr,
               'cwd' => $new_cwd, 'pid' => $child_pid, 'timed_out' => $timed_out,
               'timeout_enforced' => $has_timeout,
               'output_truncated' => $truncated,
               'stdout_len' => strlen($stdout), 'stderr_len' => strlen($stderr),
               'elapsed_ms' => (int)round((microtime(true) - $t0) * 1000),
               'warnings' => _shell_warnings_field($_g['warn']),
               'tip' => $tip]);
}

/**
 * 清理任务临时文件。
 * 默认只删启动用临时脚本(temps), 保留 rc/cwd/envout —— shell_job 需要反复读取它们
 * 才能给出真实退出码/工作目录/环境; 之前 rc 在第一次轮询时就被删掉, 导致所有
 * 后台任务都报 return_code=0 且 cwd/env 丢失。
 * $purge=true 用于 TTL 回收/淘汰: 连输出文件和采集文件一起删, 防止磁盘泄漏。
 */
function _shell_job_cleanup($job, $purge = false) {
    foreach (($job['temps'] ?? []) as $pp) {
        if ($pp) @unlink($pp);
    }
    $job['temps'] = [];
    $job['cleaned'] = true;
    if ($purge) {
        foreach ([$job['rc'] ?? null, $job['cwd'] ?? null, $job['envout'] ?? null,
                  $job['out'] ?? null, $job['err'] ?? null] as $pp) {
            if ($pp) @unlink($pp);
        }
        $job['purged'] = true;
    }
    return $job;
}

function action_shell_job($req, $cfg) {
    global $E;
    $err = _shell_auth($req, $cfg);
    if ($err) return $err;

    $lk = _shell_lock($cfg);
    $st = _shell_load($cfg);
    $jid = $req['job_id'] ?? '';
    list($jid, $bad) = _shell_check_id($jid);
    if ($bad) { _shell_unlock($lk); return _shell_err($E, 'invalid_job_id'); }
    $_lines = _shell_parse_lines($req['lines'] ?? null);
    if ($_lines === null) { _shell_unlock($lk); return _shell_err($E, 'invalid_lines'); }
    if (empty($jid) || !isset($st['jobs'][$jid])) {
        _shell_unlock($lk);
        return fail($E['INVALID_PARAM'], 'unknown_job', "job_id '$jid' 不存在");
    }
    $job = $st['jobs'][$jid];
    // 先看 rc 文件(EXIT trap 落盘 = 权威完成信号), 再看 pid。
    // 反过来会踩两个坑: (1) 容器里 init 不回收僵尸, kill -0 对僵尸恒为真 -> 永远 running;
    // (2) pid 被系统复用后 kill -0 也恒为真。rc 文件不受这两点影响。
    $finished = _shell_job_finished($job);
    $alive = false;
    if (!$finished) {
        $alive = ($job['pid'] > 0) && _shell_pid_alive($job['pid']);
        if (!$alive) {
            // 进程消失但没写 rc: 被 SIGKILL/OOM/外部杀掉, 或 timeout 直接终结了 runner。
            // 如实标记结束, 不伪造成功。
            $finished = true;
            $job['died_without_rc'] = true;
        }
    }
    if ($finished && empty($job['done'])) {
        $job['done'] = true;
    }
    list($rc, $new_cwd, $new_env) = _shell_capture($job['rc'], $job['cwd'], $job['envout'], $job['start_cwd']);
    if ($finished) {
        _shell_job_cleanup($job);  // 只删 temps, 保留 rc/cwd/envout 供后续重复轮询
        if ($new_env) {
            $job['final_env'] = _shell_env_clean($new_env);
        }
    }
    $st['jobs'][$jid] = $job;
    _shell_save($cfg, $st);
    _shell_unlock($lk);

    $raw_out = (isset($job['out']) && file_exists($job['out']) ? @file_get_contents($job['out']) : '');
    $raw_err = (isset($job['err']) && file_exists($job['err']) ? @file_get_contents($job['err']) : '');
    $cap = 8 * 1024 * 1024;
    $truncated = (strlen($raw_out) + strlen($raw_err) > $cap);
    if ($truncated) {
        // 与 Python 端一致: 超限时保留尾部(最有诊断价值的部分在末尾)
        $half = intdiv($cap, 2);
        if (strlen($raw_out) > $half) $raw_out = substr($raw_out, -$half);
        if (strlen($raw_err) > $half) $raw_err = substr($raw_err, -$half);
    }
    $combined = $raw_out . ($raw_err !== '' ? "\n" . $raw_err : '');
    $lines = $_lines;
    if ($lines > 0) {
        $parts = explode("\n", rtrim($combined, "\r\n"));
        $text = implode("\n", array_slice($parts, -$lines));
    } else {
        $text = $combined;
    }
    $rc_known = false;
    if (!empty($job['rc']) && file_exists($job['rc'])) {
        $rc_known = (bool)preg_match('/AI_SYNC_RC=(\d+)/', (string)@file_get_contents($job['rc']), $rm);
    }
    if ($finished) {
        if (!empty($job['killed'])) {
            // 客户端主动终止的任务一律按信号约定报 128+sig:
            // 杀子进程后 runner 常会继续走到脚本末尾并以 0 退出, trap 写出 rc=0,
            // 若优先信 rc 会把它报成"正常完成", AI 无法区分任务是被杀的。
            $final_rc = 128 + intval($job['kill_signal'] ?? 15);
        } elseif ($rc_known) {
            $final_rc = intval($rm[1]);
        } elseif (!empty($job['died_without_rc'])) {
            $final_rc = null;  // 进程消失且没有任何退出码: 如实返回 null, 不伪装成成功
        } else {
            $final_rc = $rc;
        }
    } else {
        $final_rc = null;
    }
    return ok(['job_id' => $jid, 'status' => $finished ? 'done' : 'running', 'return_code' => $final_rc,
               'timed_out' => ($finished && $final_rc == 124),
               'killed' => !empty($job['killed']),
               'output_truncated' => $truncated, 'output' => $text,
               'stdout' => $raw_out, 'stderr' => $raw_err,
               'pid' => $job['pid'], 'cwd' => $new_cwd, 'timeout' => $job['timeout'],
               'elapsed_ms' => (int)round((microtime(true) - $job['ts']) * 1000)]);
}

function action_shell_kill($req, $cfg) {
    global $E;
    $err = _shell_auth($req, $cfg);
    if ($err) return $err;

    $lk = _shell_lock($cfg);
    $st = _shell_load($cfg);
    $jid = $req['job_id'] ?? '';
    list($jid, $bad) = _shell_check_id($jid);
    if ($bad) { _shell_unlock($lk); return _shell_err($E, 'invalid_job_id'); }
    if (empty($jid) || !isset($st['jobs'][$jid])) {
        _shell_unlock($lk);
        return fail($E['INVALID_PARAM'], 'unknown_job', "job_id '$jid' 不存在");
    }
    $job = $st['jobs'][$jid];
    $was_done = !empty($job['done']) || _shell_job_finished($job);
    if (!$was_done && $job['pid'] > 0) {
        _shell_kill_tree($job['pid'], 15);
        for ($i = 0; $i < 10; $i++) {
            if (!_shell_pid_alive($job['pid'])) break;
            usleep(200000);
        }
        if (_shell_pid_alive($job['pid'])) { _shell_kill_tree($job['pid'], 9); $job['kill_signal'] = 9; }
        else { $job['kill_signal'] = 15; }
        $job['killed'] = true;
    }
    $job['done'] = true;
    // 只删 temps: rc/cwd/envout 保留, 客户端下一次 shell_job 才能拿到 143 和最终 cwd
    _shell_job_cleanup($job);
    $st['jobs'][$jid] = $job;
    _shell_save($cfg, $st); _shell_unlock($lk);
    // 幂等: 已是完成态的任务再 kill 不报错, 但要如实说明"它早就结束了",
    // 否则 AI 会把 killed=true 当成"是我终止的", 从而误判任务耗时/结果
    $now_killed = !$was_done;
    return ok(['job_id' => $jid, 'killed' => $now_killed, 'pid' => $job['pid'],
               'return_code' => $now_killed ? (128 + intval($job['kill_signal'] ?? 15)) : null,
               'already_finished' => $was_done ? true : false,
               'note' => $was_done ? '该任务在本次 kill 之前就已结束, 本次未产生实际终止动作' : null]);
}

function action_shell_close($req, $cfg) {
    global $E;
    $err = _shell_auth($req, $cfg);
    if ($err) return $err;

    $lk = _shell_lock($cfg);
    $st = _shell_load($cfg);
    $sid = $req['session_id'] ?? '';
    list($sid, $bad) = _shell_check_id($sid);
    if ($bad) { _shell_unlock($lk); return _shell_err($E, 'invalid_session_id'); }
    if (empty($sid)) {
        _shell_unlock($lk);
        return fail($E['INVALID_PARAM'], 'missing_session_id', 'session_id is required');
    }
    if (!isset($st['sessions'][$sid])) {
        // 幂等: "关闭"的目标状态(不存在)已经达成, 再关一次不该报错 —— AI 不必为此写重试/纠错分支
        _shell_unlock($lk);
        return ok(['session_id' => $sid, 'closed' => false, 'already_closed' => true,
                   'note' => '会话本就不存在(已关闭或已被超时回收), 无需再次关闭']);
    }
    unset($st['sessions'][$sid]);
    _shell_save($cfg, $st); _shell_unlock($lk);
    return ok(['session_id' => $sid, 'closed' => true, 'already_closed' => false,
               'note' => '会话状态已清除; 该会话的后台任务仍可在 shell_job 查询']);
}

function action_shell_list($req, $cfg) {
    global $E;
    $err = _shell_auth($req, $cfg);
    if ($err) return $err;

    $st = _shell_load($cfg);
    $base_abs = realpath(rtrim($cfg['WORK_DIR'], '/')) ?: $cfg['WORK_DIR'];
    $sessions = [];
    foreach ($st['sessions'] as $k => $v) {
        $v = _shell_session_norm($v, $base_abs);
        $sessions[] = ['session_id' => $k, 'cwd' => $v['cwd'], 'name' => $v['name'],
                       'idle_s' => intval(time() - $v['ts'])];
    }
    $jobs = [];
    foreach ($st['jobs'] as $k => $v) {
        // 与 shell_job 一致: rc 文件优先(僵尸进程 kill -0 恒真), 再看 pid
        $alive = !_shell_job_finished($v) && ($v['pid'] > 0) && _shell_pid_alive($v['pid']);
        $cmd = (isset($v['cmd']) && is_string($v['cmd'])) ? $v['cmd'] : '';
        $jobs[] = ['job_id' => $k, 'status' => $alive ? 'running' : 'done',
                   'session_id' => (isset($v['session_id']) && is_string($v['session_id'])) ? $v['session_id'] : '',
                   'cmd_preview' => mb_substr($cmd, 0, 120), 'pid' => intval($v['pid'] ?? 0)];
    }
    return ok(['sessions' => $sessions, 'jobs' => $jobs, 'session_count' => count($sessions), 'job_count' => count($jobs)]);
}
