<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: Backup
 * backup_ls / backup_rb / _create_backup
 */
require_once __DIR__ . '/link_common.php';


/**
 * 校验备份文件名格式: {basename}.bak.{YYYYMMDD_HHMMss}_{6hex}
 * 修复 P0-1: backup_rb 直接拼 $backup_dir . '/' . $backup_file, $backup_file='../../etc/x' 可任意文件读/RCE
 */
if (!function_exists('_utf8_basename')) {
    function _utf8_basename($p) {
        $p = str_replace('\\', '/', (string)$p);
        $i = strrpos($p, '/');
        return $i === false ? $p : substr($p, $i + 1);
    }
}
if (!function_exists('_utf8_dirname')) {
    function _utf8_dirname($p) {
        $p = str_replace('\\', '/', (string)$p);
        $i = strrpos($p, '/');
        if ($i === false) return '.';
        if ($i === 0) return '/';
        return substr($p, 0, $i);
    }
}

function _backup_validate_filename($fn) {
    if (!is_string($fn) || $fn === '') return false;
    // basename 兜底: 不允许任何路径分隔符 (防穿越)
    if (strpos($fn, '/') !== false || strpos($fn, '\\') !== false) return false;
    if ($fn === '.' || $fn === '..') return false;
    // 严格匹配备份命名约定
    return (bool)preg_match('/^[^\/\\\\]+\.bak\.\d{8}_\d{6}_[0-9a-f]{6}$/', $fn);
}

/**
 * 把 safe_path() 返回的绝对路径还原成 scope 相对路径。
 * 旧逻辑直接把请求里的 path (可能是 /opt/foo) 拼到 WORK_DIR 后面,
 * 结果备份目录变成 /opt/ai-sync/backups//opt/foo, 与真实备份目录
 * /opt/ai-sync/backups/foo 对不上 → 绝对路径查询永远 0 条、回滚 not_found。
 */
function _backup_scope_rel($dest, $work_dir) {
    $d = str_replace('\\', '/', (string)$dest);
    $wd = str_replace('\\', '/', (string)$work_dir);
    $wd = rtrim($wd, '/');
    if ($wd === '') return ltrim($d, '/');          // scope 根为 /
    if ($d === $wd) return '';
    if (strpos($d, $wd . '/') === 0) return substr($d, strlen($wd) + 1);
    return ltrim($d, '/');                          // 理论上 safe_path 已拦截, 兜底
}

function action_backup_ls($req, $cfg) {
    global $E, $CONFIG;
    // FIX-v4.33 (P0): 备份列表此前无分页无上限, 实测一次返回 2004 条 / 179KB ——
    //   AI 一句 --backup-list 就吃掉几万 token, 而它真正想要的往往只是"某文件最近几个备份"。
    //   现在默认返回最近 50 条并明确告知 total/truncated, AI 可用 path 收窄或 limit 翻页。
    $limit = _parse_limit($req['limit'] ?? null, 50, 500);
    if (is_array($limit)) return $limit[0];   // 参数错误 -> 1011
    $rel = $req['path'] ?? '';

    // path="." 或空 → 递归列出所有备份
    if ($rel === '' || $rel === '.') {
        $backup_root = rtrim(rtrim($cfg['WORK_DIR'], '/') . '/' . $cfg['BACKUP_DIR'], '/');
        if (!is_dir($backup_root)) return ok(['backups' => [], 'total' => 0]);
        $items = [];
        $iti = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($backup_root, \RecursiveDirectoryIterator::SKIP_DOTS),
            \RecursiveIteratorIterator::LEAVES_ONLY
        );
        $root_len = strlen($backup_root) + 1; // +1 for the /
        foreach ($iti as $f) {
            // 备份文件必须是普通文件: 跳过符号链接, 避免 backup_ls 顺着链接读到 work_dir 外.
            if ($f->isLink() || !$f->isFile()) continue;
            $fn = $f->getFilename();
            if (strpos($fn, '.bak.') === false) continue;
            $full = $f->getPathname();
            $rel_in_backup = substr($full, $root_len); // 去掉 backup_root/
            // rel_in_backup 形如 "sync.py/sync.py.bak.xxx" 或 "tools/backup.php/backup.php.bak.xxx"
            $orig = dirname($rel_in_backup); // 原始文件相对路径
            if ($orig === '.') $orig = _utf8_basename($rel_in_backup);
            $items[] = [
                'file'  => $fn,
                'path'  => $orig,
                'size'  => $f->getSize(),
                'mtime' => $f->getMTime(),
            ];
        }
        usort($items, function ($a, $b) { return $b['mtime'] - $a['mtime']; });
        return _backup_ls_result($items, $limit,
            '备份总数较多, 已按时间倒序返回最近的若干条。'
            . '要找某个文件的备份请用 path=<文件路径>; 要更多请 limit=<N>(上限500)');
    }

    // 指定文件路径: 只解析父目录, 不跟随最后一级 —— 链接的备份目录挂在链接名下, 不是目标名下.
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) return reject_path($rel, 'backup_ls', $req);
    $scope_rel = _backup_scope_rel($dest, $cfg['WORK_DIR']);
    $dir_rel = _utf8_dirname($scope_rel);
    if ($dir_rel === '.') $dir_rel = '';
    $backup_dir = rtrim($cfg['WORK_DIR'], '/') . '/' . $cfg['BACKUP_DIR']
                . ($dir_rel !== '' ? '/' . $dir_rel : '') . '/' . _utf8_basename($scope_rel);
    if (!is_dir($backup_dir)) return ok(['backups' => [], 'total' => 0]);
    $files = glob($backup_dir . '/' . _utf8_basename($scope_rel) . '.bak.*');
    $items = [];
    foreach ($files ?: [] as $f) {
        $items[] = [
            'file'  => _utf8_basename($f),
            'path'  => $scope_rel,
            'size'  => filesize($f),
            'mtime' => filemtime($f),
            'mode'  => _mode4(fileperms($f) & 0777),
            'type'  => _backup_entry_type($f),
        ];
    }
    usort($items, function ($a, $b) { return $b['mtime'] - $a['mtime']; });
    return _backup_ls_result($items, $limit, '该文件备份较多, 已返回最近若干条; 要更多请 limit=<N>');
}

/**
 * 统一的 limit 解析 (与 PY 端 _parse_limit 对齐)。返回 int 或 [fail响应]。
 * PHP 弱类型下 "abc" 会被 (int) 转成 0 —— 错误信息里必须向 AI 说清这一点。
 */
function _parse_limit($v, $default = 50, $maximum = 500) {
    global $E;
    if ($v === null || $v === '') return $default;
    if (!is_numeric($v)) {
        $n = 0;   // 与后续共用的处理路径一致: 非数字按弱类型规则视为 0
    } else {
        $n = (int)$v;
    }
    if ($n < 1) {
        return [fail($E['INVALID_PARAM'], 'bad_limit',
            "limit 必须 >= 1 (收到 {$n})。注意: 参数按弱类型规则转换, 非数字字符串会变成 0"
            . " (如 \"abc\"→0), 请传正整数", [
                'hint' => "limit 用法: limit=20 (上限 500); 不传默认返回最近 {$default} 条;"
                        . ' 要找某个文件的备份用 path=<文件路径> 更省 token',
            ])];
    }
    return min($n, $maximum);
}

/**
 * 组装备份列表响应 (与 PY 端 _backup_ls_result 逐字段对齐):
 * 恒返回真实 total, 截断时明确告知 AI 如何继续。
 */
function _backup_ls_result($items, $limit, $hint_when_truncated = '') {
    $total = count($items);
    $shown = array_slice($items, 0, $limit);
    $res = [
        'backups'   => $shown,
        'total'     => $total,
        'returned'  => count($shown),
        'truncated' => $total > count($shown),
    ];
    if ($total > count($shown)) $res['tip'] = $hint_when_truncated;
    return ok($res);
}

function action_backup_rb($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    $backup_file = $req['backup_file'] ?? '';
    // 回滚目标同样不跟随最后一级: 恢复链接时替换链接本身, 不能写到目标文件.
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) return reject_path($rel, 'backup_rb', $req);
    $scope_rel = _backup_scope_rel($dest, $cfg['WORK_DIR']);
    // 修复 P0-1: 严格校验 backup_file 格式 + basename 兜底, 防路径穿越读取任意文件
    if (!_backup_validate_filename($backup_file)) {
        audit_log('backup_rb', array_merge($req, ['_blocked' => 'bad_backup_file']), $E['INVALID_PARAM']);
        return fail($E['INVALID_PARAM'], 'bad_backup_file',
            "backup_file 格式非法 (需 {name}.bak.{YYYYMMDD_HHMMss}_{6hex})",
            ['hint' => '用 backup_ls 查询该文件可用的备份列表']);
    }
    // basename 二次兜底 (即使正则漏过也拦)
    $backup_file = _utf8_basename($backup_file);
    $backup_dir = rtrim($cfg['WORK_DIR'], '/') . '/' . rtrim($cfg['BACKUP_DIR'], '/') . '/'
                . (_utf8_dirname($scope_rel) !== '.' ? _utf8_dirname($scope_rel) . '/' : '')
                . _utf8_basename($scope_rel);
    $backup_path = $backup_dir . '/' . $backup_file;
    // 物理层铁闸: realpath 校验 backup_path 必须在 backup_dir 内 (防符号链接逃逸)
    $real_backup_dir = @realpath($backup_dir);
    $real_backup_path = @realpath($backup_path);
    if ($real_backup_dir === false || $real_backup_path === false
        || strpos($real_backup_path . DIRECTORY_SEPARATOR, $real_backup_dir . DIRECTORY_SEPARATOR) !== 0
        || @is_link($backup_path)
        || !is_file($real_backup_path)) {
        audit_log('backup_rb', $req, $E['BACKUP_NOT_FOUND'], ['backup_file' => $backup_file]);
        return fail($E['BACKUP_NOT_FOUND'], 'backup_not_found', "Backup '$backup_file' not found",
            ['hint' => '用 backup_ls path=' . $rel . ' 查询该文件可用的备份列表']);
    }
    $restored_type = _restore_backup_to_dest($real_backup_path, $dest, $cfg);
    if ($restored_type !== false) {
        // 链接备份恢复后 mode 恒为 0777 (lstat); 普通文件按备份自身权限位恢复.
        $bmode = @fileperms($real_backup_path) & 0777;
        if ($restored_type === 'file' && $bmode > 0) @chmod($dest, $bmode);
        audit_log('backup_rb', $req, 0, ['path' => $scope_rel, 'restored_from' => $backup_file,
                                          'type' => $restored_type]);
        return ok(['path' => $scope_rel, 'restored_from' => $backup_file,
                   'type' => $restored_type,
                   'mode' => _mode4($bmode)]);
    }
    $err = error_get_last()['message'] ?? '';
    audit_log('backup_rb', $req, $E['INTERNAL'], ['path' => $rel, 'error' => $err]);
    return fail($E['INTERNAL'], 'restore_fail', 'Copy failed',
        ['hint' => '检查目录权限/磁盘空间; 用 backup_ls 确认备份存在', 'php_error' => $err]);
}

if (!function_exists('_create_backup')) {
function _create_backup($dest, $rel, $cfg) {
    // 链接必须走 _create_link_backup: copy() 会跟随目标, 备份内容会变成目标文件内容.
    if (@is_link($dest)) return '';
    // 特殊节点 (FIFO/socket/设备) 不能 copy: 无写端 FIFO 会永久阻塞.
    if (!_is_regular_file($dest)) return '';
    // 统一用 scope 相对路径建备份目录: 绝对路径入参也能与相对路径命中同一目录
    $rel = _backup_scope_rel($dest, $cfg['WORK_DIR']);
    $backup_dir = rtrim($cfg['WORK_DIR'], '/') . '/' . rtrim($cfg['BACKUP_DIR'], '/');
    @mkdir($backup_dir, 0755, true);
    $dir_rel = _utf8_dirname($rel);
    $file_backup_dir = $backup_dir . ($dir_rel !== '.' ? '/' . $dir_rel : '') . '/' . _utf8_basename($rel);
    @mkdir($file_backup_dir, 0755, true);
    $ts = date('Ymd_His') . '_' . substr(md5($rel . microtime()), 0, 6);
    $backup_file = $file_backup_dir . '/' . _utf8_basename($rel) . '.bak.' . $ts;
    if (!@copy($dest, $backup_file)) return '';
    // 备份保留源文件权限位, 否则回滚只能恢复内容、丢失 0755 (init.d 脚本失效)
    @chmod($backup_file, fileperms($dest) & 0777);
    $files = glob($file_backup_dir . '/' . _utf8_basename($rel) . '.bak.*');
    if ($files && count($files) > $cfg['BACKUP_KEEP']) {
        usort($files, function ($a, $b) { return filemtime($b) - filemtime($a); });
        foreach (array_slice($files, $cfg['BACKUP_KEEP']) as $old) @unlink($old);
    }
    return _utf8_basename($backup_file);
}
}
