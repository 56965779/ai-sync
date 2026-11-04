<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync Tool: 软链接/备份公共层
 *
 * 所有落盘类工具 (file_crud / backup / chunked_upload / batch_deploy) 共用同一套:
 *   - _symlink_raw()         跨平台创建链接 (绕过 PHP symlink() 的 ELOOP 缺陷)
 *   - _create_link_backup()  链接备份, 不跟随目标, 自描述格式
 *   - _link_backup_target()  识别新/旧备份格式并取回 raw target
 *   - _restore_backup_to_dest() 按备份类型恢复 (链接→链接, 文件→文件), 原子替换
 *   - _backup_entry_type()   备份条目类型 (供 backup_ls 返回)
 *
 * 设计约束: 备份格式必须能表达任意 POSIX 链接目标 (含换行/非 UTF-8), 且恢复时
 * 绝不跟随目标、绝不把链接写成普通文件。旧格式 "symlink -> target\n" 仍可识别,
 * 但只用于兼容历史备份; 新备份一律使用 AISYNC-SYMLINK-V1 + base64。
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

/**
 * 特殊文件识别 (FIFO / socket / 字符设备 / 块设备).
 *
 * 背景: 这类节点在 Linux 上合法存在 (FIFO、unix socket 常见于运行时目录),
 * 但 read()/copy()/hash_file() 会阻塞 (FIFO 无写端时永久阻塞) 或读取无限流
 * (/dev/zero). 服务端所有"读内容"的路径都必须先判定 regular file.
 * 返回值: null=普通文件/目录/链接; 'fifo'|'socket'|'char_device'|'block_device'
 */
if (!function_exists('_special_kind')) {
    function _special_kind($path) {
        $st = @lstat($path);
        if ($st === false) return null;
        switch ($st['mode'] & 0170000) {
            case 0010000: return 'fifo';
            case 0140000: return 'socket';
            case 0020000: return 'char_device';
            case 0060000: return 'block_device';
            default:      return null;
        }
    }
}

/** 跟随最终软链接判定: 目标是否为特殊文件 (用于 download 等会跟随链接的路径). */
if (!function_exists('_special_kind_follow')) {
    function _special_kind_follow($path) {
        $st = @stat($path);
        if ($st === false) return null;
        switch ($st['mode'] & 0170000) {
            case 0010000: return 'fifo';
            case 0140000: return 'socket';
            case 0020000: return 'char_device';
            case 0060000: return 'block_device';
            default:      return null;
        }
    }
}

/** 统一 mode 输出格式: 4 位八进制 (含 setuid/setgid/sticky), 如 0644/4755/1777. */
if (!function_exists('_mode4')) {
    function _mode4($mode) {
        return sprintf('%04o', ((int)$mode) & 07777);
    }
}

/** 仅接受普通文件 (跟随链接); 目录/链接/特殊节点均为 false. */
if (!function_exists('_is_regular_file')) {
    function _is_regular_file($path) {
        return @is_file($path) && _special_kind_follow($path) === null;
    }
}

/**
 * 创建软链接 (兼容 PHP symlink() 的 target 解析缺陷).
 *
 * PHP 的 symlink($target, $link) 在创建前会把相对 $target 按"进程 cwd"和"link 所在目录"
 * 各解析一次; 只要其中一处命中自指/软链接环 (如 ln -s self self), 解析返回 ELOOP,
 * symlink() 就误报 "No such file or directory" —— 即使 $link 路径本身完全合法.
 * 纯 PHP 无法关闭该解析. 兜底策略: 在不会成环的上级目录 (或系统临时目录) 以相同
 * raw target 建链接, 再 rename 到目标位置; rename 不解释链接内容, 链接文本保持原样.
 * 仅接受绝对 $link 路径, 避免 cwd 变化影响链接落点.
 */
function _symlink_raw($target, $link_path, $base = null) {
    if (@symlink($target, $link_path)) return true;
    $tn = str_replace('\\', '/', (string)$target);
    // 绝对 target 不经过相对解析, 失败是真实错误 (权限/文件系统不支持)
    if ($tn !== '' && ($tn[0] === '/' || preg_match('#^[A-Za-z]:/#', $tn))) return false;
    $lp = str_replace('\\', '/', (string)$link_path);
    if (!(($lp !== '' && $lp[0] === '/') || preg_match('#^[A-Za-z]:/#', $lp))) return false;

    // 候选目录: link 父目录及其上级 (最多 6 层, 不越过 work_dir), 最后系统临时目录
    $cands = [];
    $dir = dirname($link_path);
    $stop = $base !== null ? rtrim(str_replace('\\', '/', $base), '/') : null;
    for ($i = 0; $i < 6 && $dir; $i++) {
        $cands[] = $dir;
        if ($stop === null || rtrim(str_replace('\\', '/', $dir), '/') === $stop) break;
        $up = dirname($dir);
        if ($up === $dir) break;
        $dir = $up;
    }
    $tmpdir = @sys_get_temp_dir();
    if ($tmpdir && !in_array($tmpdir, $cands, true)) $cands[] = $tmpdir;

    $cwd = @getcwd();
    foreach ($cands as $d) {
        if (_symlink_raw_try($target, $d, $link_path, $cwd)) return true;
    }
    // 最后: 在 work_dir 内建一次性临时目录 (同一文件系统, rename 不会 EXDEV)
    if ($base !== null && @is_dir($base) && @is_writable($base)) {
        $sd = rtrim($base, '/\\') . '/.ai-sync_link_dir_' . bin2hex(random_bytes(4));
        if (@mkdir($sd, 0700)) {
            $ok = _symlink_raw_try($target, $sd, $link_path, $cwd);
            @rmdir($sd);
            if ($ok) return true;
        }
    }
    return false;
}

/** _symlink_raw 的单目录尝试: 以 $dir 为 cwd 建临时链接, 再 rename 到最终位置. */
function _symlink_raw_try($target, $dir, $link_path, $cwd) {
    if (!$dir || !@is_dir($dir) || !@is_writable($dir)) return false;
    if (!@chdir($dir)) return false;
    $name = '.ai-sync_link_' . bin2hex(random_bytes(4));
    // cwd 与 link 目录都是 $dir: symlink() 内部两处 target 解析都不会命中原来的环
    $ok = @symlink($target, $name);
    if ($cwd !== false && $cwd !== '') @chdir($cwd);
    if (!$ok) return false;
    $scratch = rtrim($dir, '/\\') . '/' . $name;
    if (@rename($scratch, $link_path)) return true;
    @unlink($scratch);
    return false;
}

/** 链接备份内容: 自描述头 + base64(raw target). 可表达换行/非 UTF-8/任意字节. */
function _link_backup_body($target) {
    return "AISYNC-SYMLINK-V1\n" . base64_encode((string)$target) . "\n";
}

/**
 * 软链接备份: 不跟随目标 (目标可能在 work_dir 外), 只记录 raw target.
 * 备份文件保持普通文件 (不建链接), 否则备份本身也会被目标解析影响.
 */
function _create_link_backup($dest, $rel, $cfg) {
    $target = @readlink($dest);
    if ($target === false) return '';
    $backup_dir = rtrim($cfg['WORK_DIR'], '/') . '/' . $cfg['BACKUP_DIR'];
    @mkdir($backup_dir, 0755, true);
    $file_backup_dir = $backup_dir . '/' . _utf8_dirname($rel) . '/' . _utf8_basename($rel);
    @mkdir($file_backup_dir, 0755, true);
    $ts = date('Ymd_His') . '_' . substr(md5($rel . microtime()), 0, 6);
    $backup_file = $file_backup_dir . '/' . _utf8_basename($rel) . '.bak.' . $ts;
    if (@file_put_contents($backup_file, _link_backup_body($target), LOCK_EX) === false) return '';
    @chmod($backup_file, 0644);
    $files = glob($file_backup_dir . '/' . _utf8_basename($rel) . '.bak.*');
    if ($files && count($files) > $cfg['BACKUP_KEEP']) {
        usort($files, function ($a, $b) { return filemtime($b) - filemtime($a); });
        foreach (array_slice($files, $cfg['BACKUP_KEEP']) as $old) @unlink($old);
    }
    return _utf8_basename($backup_file);
}

/**
 * 读取链接备份里的 raw target.
 * 返回 string (链接备份) 或 null (普通文件备份/无法识别).
 * 新格式优先; 旧格式 "symlink -> target\n" 仅作兼容, 目标含换行时旧格式本身有歧义.
 */
function _link_backup_target($backup_path) {
    if (@is_link($backup_path)) return null;   // 备份本身不应是链接
    $raw = @file_get_contents($backup_path);
    if ($raw === false) return null;
    $magic = "AISYNC-SYMLINK-V1\n";
    if (strncmp($raw, $magic, strlen($magic)) === 0) {
        $body = substr($raw, strlen($magic));
        $line = strtok($body, "\n");
        if ($line === false) return null;
        $decoded = base64_decode($line, true);
        return $decoded === false ? null : $decoded;
    }
    if (strncmp($raw, 'symlink -> ', 11) === 0) {
        $t = substr($raw, 11);
        if (substr($t, -1) === "\n") $t = substr($t, 0, -1);
        // 旧格式无法表达换行; 多行内容不当作链接备份, 避免把普通文件误恢复成链接
        if ($t !== '' && strpos($t, "\n") === false) return $t;
    }
    return null;
}

/** 备份条目类型: symlink / file. 仅用于展示, 不读取目标内容. */
function _backup_entry_type($backup_path) {
    return _link_backup_target($backup_path) !== null ? 'symlink' : 'file';
}

/**
 * 按备份类型恢复到 $dest.
 *   - 链接备份: 重建链接 (临时链接 + rename, 原子替换目录项)
 *   - 普通备份: 复制到临时文件 + rename (绝不写穿目标链接)
 * 返回 'symlink'|'file'|false.
 */
function _restore_backup_to_dest($backup_path, $dest, $cfg) {
    $parent = dirname($dest);
    if (!is_dir($parent) && !@mkdir($parent, 0755, true) && !is_dir($parent)) return false;
    $link_target = _link_backup_target($backup_path);
    $tmp = $dest . '.restore_tmp.' . bin2hex(random_bytes(4));
    if ($link_target !== null) {
        if (!_symlink_raw($link_target, $tmp, $cfg['WORK_DIR'])) return false;
        if (!@rename($tmp, $dest)) { @unlink($tmp); return false; }
        return 'symlink';
    }
    if (!@copy($backup_path, $tmp)) return false;
    @chmod($tmp, fileperms($backup_path) & 0777);
    if (!@rename($tmp, $dest)) { @unlink($tmp); return false; }
    return 'file';
}

/** 供 upload 等 action 返回: 同时给出可读 target 与无损 base64. */
function _link_target_fields($raw) {
    $out = ['link_target' => $raw, 'link_target_b64' => base64_encode((string)$raw)];
    return $out;
}
