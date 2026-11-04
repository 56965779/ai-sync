<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: File CRUD
 * upload / upload_batch / download / stat / list / diff / delete / rename / copy / mkdir / checksum
 */

require_once __DIR__ . '/link_common.php';

function _action_upload_impl($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    // 不跟随最后一级软链接: 目标已是链接时要"替换链接本身", 而不是把内容写进它指向的文件.
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) {
        return reject_path($rel, 'upload', $req);
    }
    // path 以 / 结尾表示目录, 但 upload 写的是文件:
    //   旧行为在 PHP 上拼出 "a/" → file_put_contents 失败 → 1008 write_fail;
    //   在 Python 上 realpath 规范化掉尾斜杠 → 静默创建名为 a 的文件。两端不一致且都反直觉。
    if (preg_match('#[/\\\\]+$#', $rel)) {
        return fail($E['INVALID_PARAM'], 'bad_path',
            "upload 的 path 不能以 / 结尾 (它是文件路径): '$rel'", [
                'hint' => '要写入目录请给出完整文件名, 如 "dir/a.txt"; 要创建目录请用 mkdir。',
            ]);
    }
    // 形如 "dir/." / "dir/./" 的路径等价于目录本身, 不可能作为文件写入:
    // 旧行为先 mkdir(dir) 再 rename 到 dir/. → 1008 write_fail, AI 无法从错误定位.
    if (basename($dest) === '.') {
        return fail($E['INVALID_PARAM'], 'bad_dest_path',
            "upload 的目标必须以文件名结尾, 不能是目录路径: '$rel'", [
                'hint' => '形如 "dir/." 的 path 等价于目录 dir; 请给出具体文件名, 如 "dir/a.txt"。',
            ]);
    }
    // ---- 软链接上传: link_target / link_target_b64 存在时创建/替换链接本身, 不需要 content_b64 ----
    $has_link_target = (array_key_exists('link_target', $req) && $req['link_target'] !== null)
                    || (array_key_exists('link_target_b64', $req) && $req['link_target_b64'] !== null);
    if ($has_link_target) {
        // b64 是权威值 (可表达非 UTF-8 目标); link_target 保留给旧客户端和可读性.
        if (array_key_exists('link_target_b64', $req) && $req['link_target_b64'] !== null) {
            if (!is_string($req['link_target_b64'])) {
                audit_log('upload', $req, $E['INVALID_PARAM']);
                return fail($E['INVALID_PARAM'], 'link_target_b64_not_string',
                    "link_target_b64 must be a base64 string, got " . gettype($req['link_target_b64']));
            }
            $link_target = base64_decode($req['link_target_b64'], true);
            if ($link_target === false) {
                audit_log('upload', $req, $E['INVALID_PARAM']);
                return fail($E['INVALID_PARAM'], 'bad_link_target_b64', 'link_target_b64 decode failed',
                    ['hint' => 'link_target_b64 必须是 raw target 字节的 base64 编码']);
            }
        } else {
            if (!is_string($req['link_target'])) {
                audit_log('upload', $req, $E['INVALID_PARAM']);
                return fail($E['INVALID_PARAM'], 'link_target_not_string',
                    "link_target must be a string, got " . gettype($req['link_target']), [
                        'hint' => 'link_target 是软链接指向的相对路径 (如 "../lib/a.so" 或 "target.txt")。',
                    ]);
            }
            $link_target = $req['link_target'];
        }
        // 父目录可能尚不存在 (首次向新目录上传链接): 先按已校验的安全路径创建父目录,
        // 否则 _link_meta_from_target() 里 realpath(dirname) 失败 → 误报 invalid_link_target.
        // safe_path_nofollow 已解析最近存在祖先并确保仍在 work_dir 内, 这里 mkdir 不会逃逸.
        $parent = dirname($dest);
        if (!is_dir($parent)) {
            if (file_exists($parent)) {
                return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                    'Cannot create parent directory: a path component is a file, not a directory', [
                        'hint' => '目标路径的父级已存在同名文件; 请换路径或先删除/重命名该文件。',
                    ]);
            }
            @mkdir($parent, 0755, true);
            if (!is_dir($parent)) {
                return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                    'Cannot create parent directory (permission denied or path component is a file)', [
                        'hint' => '检查 WORK_DIR 写权限, 以及目标路径中是否存在同名文件。',
                    ]);
            }
        }
        $lm = _link_meta_from_target($link_target, $dest, $cfg['WORK_DIR']);
        if (!$lm['safe']) {
            audit_log('upload', $req, $E['INVALID_PATH'] ?? 'E_INVALID_PATH',
                ['reason' => 'invalid_link_target', 'why' => $lm['reason'] ?? '']);
            return fail($E['INVALID_PATH'] ?? 'E_INVALID_PATH', 'invalid_link_target',
                "软链接目标被拒绝: " . ($lm['reason'] ?? 'invalid'), [
                    'hint' => 'link_target 必须是相对路径, 且归一化后仍在 work_dir 内; '
                            . '绝对路径/../ 越界/中间链接逃逸都会被拒绝。',
                    'link_target' => $link_target,
                    'link_target_b64' => base64_encode((string)$link_target),
                ]);
        }
        $link_rel = $lm['target_rel'];
        $link_md5 = hash('md5', $lm['target_norm']);
        // 乐观锁: 目标当前是链接则比较规范化目标, 是文件则比较内容 md5.
        $base_md5 = (string)($req['expect_md5'] ?? ($req['base_md5'] ?? ''));
        if ($base_md5 !== '' && (@is_link($dest) || file_exists($dest))) {
            $cur_md5 = _node_md5($dest, $cfg['WORK_DIR']);
            if ($cur_md5 !== $base_md5) {
                audit_log('upload', $req, $E['CHECKSUM_MISMATCH'], ['reason' => 'cas_conflict']);
                return fail($E['CHECKSUM_MISMATCH'], 'cas_conflict',
                    '远程对象已被修改, 期望 md5=' . $base_md5 . ' 实际 md5=' . $cur_md5, [
                        'current_md5' => $cur_md5,
                        'expected_md5' => $base_md5,
                        'hint' => '先 stat/checksum 重新读取最新状态再改; 若确认覆盖请去掉 expect_md5。',
                    ]);
            }
        }
        if (@is_dir($dest) && !@is_link($dest)) {
            return fail($E['INVALID_PARAM'], 'dest_is_dir',
                "目标 '$rel' 已存在且是目录, 不能用软链接替换", [
                    'hint' => '先删除或重命名该目录, 或换一个链接路径。',
                ]);
        }
        if (!@is_link($dest) && ($sk = _special_kind($dest)) !== null) {
            return fail($E['INVALID_PARAM'], 'dest_is_special',
                "目标 '$rel' 已存在且是特殊文件 ($sk), 拒绝直接覆盖", [
                    'path' => $rel, 'special_type' => $sk,
                    'hint' => '特殊文件无法备份/读取; 先 delete 删除该节点, 再上传链接或文件。',
                ]);
        }
        $backed_up = '';
        if ((@is_link($dest) || file_exists($dest)) && $cfg['BACKUP_KEEP'] > 0) {
            // 目标可能是链接 (记录 raw target) 或普通文件 (复制内容): 不能一律按链接备份,
            // 否则普通文件会被 _create_link_backup 返回空 → 原内容无备份直接丢失.
            $backed_up = @is_link($dest)
                ? _create_link_backup($dest, $rel, $cfg)
                : _create_backup($dest, $rel, $cfg);
        }
        $tmp_link = $dest . '.upload_tmp.' . bin2hex(random_bytes(4));
        if (!_symlink_raw($link_target, $tmp_link, $cfg['WORK_DIR'])) {
            audit_log('upload', $req, $E['UPLOAD_FAILED'], ['reason' => 'symlink_fail']);
            return fail($E['UPLOAD_FAILED'], 'symlink_fail', 'Cannot create symlink (permission denied?)', [
                'hint' => '目标文件系统可能不支持符号链接 (FAT/exFAT), 或父目录不可写。',
            ]);
        }
        if (!@rename($tmp_link, $dest)) {
            @unlink($tmp_link);
            audit_log('upload', $req, $E['UPLOAD_FAILED'], ['reason' => 'link_replace_fail']);
            return fail($E['UPLOAD_FAILED'], 'write_fail', 'Cannot replace destination with symlink');
        }
        clearstatcache(true, $dest);
        // 创建后再算一次 broken: 自指/环链在创建前目标不存在 (broken=true), 创建后链接本身已存在.
        $lm_after = _link_meta_from_target($link_target, $dest, $cfg['WORK_DIR']);
        $lst = @lstat($dest);
        $mtime = $lst ? (int)$lst['mtime'] : time();
        $mode = $lst ? _mode4($lst['mode']) : '0777';
        _metrics_inc($cfg, 'upload', 0);
        audit_log('upload', $req, 0, ['type' => 'symlink', 'link_target' => $link_target, 'md5' => $link_md5]);
        return ok([
            'type'           => 'symlink',
            'size'           => 0,
            'md5'            => $link_md5,
            'mtime'          => $mtime,
            'mode'           => $mode,
            'link_target'    => $link_target,
            'link_target_b64'=> base64_encode($link_target),
            'link_target_rel'=> $lm_after['safe'] ? $lm_after['target_rel'] : $link_rel,
            'link_target_rel_b64'=> $lm_after['safe'] ? base64_encode((string)$lm_after['target_rel']) : null,
            'broken'         => $lm_after['broken'],
            'cycle'          => !empty($lm_after['cycle']),
            'resolvable'     => !empty($lm_after['resolvable']),
            'backed_up'      => $backed_up,
            'lint'           => null,
        ]);
    }
    // 缺 content_b64 必须拒绝: base64_decode 缺省为 "" == 上传空文件, 会静默清空已有内容,
    // 且旧行为返回 ok:true+size:0, 调用方(尤其 AI)无法察觉 (静默数据损坏)。
    if (!array_key_exists('content_b64', $req) || $req['content_b64'] === null) {
        audit_log('upload', $req, $E['INVALID_PARAM']);
        return fail($E['INVALID_PARAM'], 'missing_content_b64',
            "Required field 'content_b64' is missing or null (received fields: " . _brief_keys($req) . ")", [
                'hint' => 'upload 必须带 content_b64 (文件内容的 base64 字符串)。'
                        . '写空文件请显式传 content_b64=""; 若你用的是 content/text/data/body '
                        . '等字段名, 改名为 content_b64; 超过 MAX_SIZE 改用 chunked_upload '
                        . '(op=init → put ×N → assemble)。',
            ]);
    }
    // 类型必须显式校验: base64_decode(123, true) 会隐式转成 "123" 并解出 2 字节;
    // base64_decode([...]) 在 PHP 8 直接抛 TypeError → 1099. 两者都是静默/误报.
    if (!is_string($req['content_b64'])) {
        audit_log('upload', $req, $E['INVALID_PARAM']);
        return fail($E['INVALID_PARAM'], 'content_b64_not_string',
            "content_b64 must be a base64 string, got " . gettype($req['content_b64']), [
                'hint' => 'content_b64 必须是字符串: 空文件传 ""; 二进制先做 base64 编码再传。',
            ]);
    }
    $data = base64_decode($req['content_b64'], true);
    if ($data === false) {
        audit_log('upload', $req, $E['INVALID_PARAM']);
        return fail($E['INVALID_PARAM'], 'bad_base64',
            'content_b64 base64 解码失败 (长度 ' . strlen($req['content_b64']) . ')', [
                'hint' => 'content_b64 必须是标准 base64 (A-Z a-z 0-9 + /, 可带 = 填充); '
                        . '不要传 data: URI 前缀、文件路径或原始文本; 空文件传 "".',
            ]);
    }
    if (strlen($data) > $cfg['MAX_SIZE']) {
        audit_log('upload', $req, $E['FILE_TOO_LARGE']);
        return fail($E['FILE_TOO_LARGE'], 'too_large', 'Size ' . strlen($data) . ' exceeds MAX_SIZE');
    }
    // 乐观锁: 期望远程当前 md5 (CAS). 不匹配则拒绝, 返回当前值供客户端决策
    // FIX-v4.33 (P0): 单文件 upload 此前只认内部名 base_md5, 而对外契约(HELP_SPEC)没声明
    //   任何 CAS 字段 → AI 根本不知道有乐观锁可用, 并发改动被静默覆盖。
    //   现在 expect_md5 为对外契约名, base_md5 作为历史别名保留, 两者等价。
    $base_md5 = (string)($req['expect_md5'] ?? ($req['base_md5'] ?? ''));
    if ($base_md5 !== '' && (@is_link($dest) || file_exists($dest))) {
        $cur_md5 = _node_md5($dest, $cfg['WORK_DIR']);
        if ($cur_md5 !== $base_md5) {
            audit_log('upload', $req, $E['CHECKSUM_MISMATCH'], ['reason' => 'cas_conflict']);
            return fail($E['CHECKSUM_MISMATCH'], 'cas_conflict',
                '远程文件已被修改, 期望 md5=' . $base_md5 . ' 实际 md5=' . $cur_md5, [
                'current_md5' => $cur_md5,
                'expected_md5' => $base_md5,
                'hint' => '先 --cat 重新读取最新内容再改; 若确认覆盖请去掉 expect_md5。'
                        . '字段名提示: 对外契约名是 expect_md5, 历史 batch_deploy 里叫 base_md5',
            ]);
        }
    }
    if (@is_dir($dest) && !@is_link($dest)) {
        return fail($E['INVALID_PARAM'], 'dest_is_dir',
            "目标 '$rel' 已存在且是目录, 不能用文件替换", [
                'hint' => '先删除或重命名该目录, 或换一个文件路径。',
            ]);
    }
    if (!@is_link($dest) && ($sk = _special_kind($dest)) !== null) {
        return fail($E['INVALID_PARAM'], 'dest_is_special',
            "目标 '$rel' 已存在且是特殊文件 ($sk), 拒绝直接覆盖", [
                'path' => $rel, 'special_type' => $sk,
                'hint' => 'FIFO/socket/设备节点无法备份/校验; 先 delete 删除该节点, 再上传文件。',
            ]);
    }
    // P0 可移植性修复: 未显式传 mode 时保留目标文件原有权限位。
    //   旧行为固定 0644, 导致 --upload-file/--write-remote 把可执行脚本 (0755) 降级为 0644,
    //   init.d/S* 不再被执行; 不同系统上的 umask 也会让结果不一致。
    //   显式传 mode 的调用方 (probe 0600 等) 仍按 safe_mode 校验后生效。
    $upload_mode = 0644;
    $mode_req = $req['mode'] ?? null;
    if ($mode_req === null || $mode_req === '') {
        if (!@is_link($dest) && file_exists($dest)) {
            $upload_mode = fileperms($dest) & 0777;
        }
    } else {
        if (!valid_mode_input($mode_req)) return bad_mode($mode_req);
        $upload_mode = safe_mode($mode_req);
    }
    if ($upload_mode <= 0) $upload_mode = 0644;
    // mtime: 客户端推送时带本地 mtime, 服务端落盘后原样恢复 (默认用当前时间)
    $upload_mtime = null;
    $mtime_req = $req['mtime'] ?? null;
    if ($mtime_req !== null && $mtime_req !== '') {
        if (!valid_mtime_input($mtime_req)) {
            audit_log('upload', $req, $E['INVALID_PARAM']);
            return bad_mtime($mtime_req);
        }
        $upload_mtime = (int)$mtime_req;
    }
    $backed_up = '';
    if ((@is_link($dest) || file_exists($dest)) && $cfg['BACKUP_KEEP'] > 0) {
        $backed_up = @is_link($dest)
            ? _create_link_backup($dest, $rel, $cfg)
            : _create_backup($dest, $rel, $cfg);
    }
    // 父目录: 必须真的能建出来. 旧实现 @mkdir 失败后继续写临时文件,
    // 路径中间某段是文件时最终只报 1008 write_fail, 调用方不知道是哪一层出了问题。
    $parent = dirname($dest);
    if (!is_dir($parent)) {
        if (file_exists($parent)) {
            return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                'Cannot create parent directory: a path component is a file, not a directory', [
                    'hint' => '目标路径的父级已存在同名文件 (如 "a" 是文件, 却要写 "a/b.txt"); '
                            . '请换路径或先删除/重命名该文件。',
                ]);
        }
        @mkdir($parent, 0755, true);
        if (!is_dir($parent)) {
            return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                'Cannot create parent directory (permission denied or path component is a file)', [
                    'hint' => '检查 WORK_DIR 写权限, 以及目标路径中是否存在同名文件。',
                ]);
        }
    }
    // P1-1-FIX: 原子写 — 同目录 tmp + rename, 避免 push 中断留下半文件
    $tmp_atomic = $dest . '.upload_tmp.' . bin2hex(random_bytes(4));
    if (file_put_contents($tmp_atomic, $data, LOCK_EX) === false) {
        @unlink($tmp_atomic);
        audit_log('upload', $req, $E['UPLOAD_FAILED']);
        return fail($E['UPLOAD_FAILED'], 'write_fail', 'Cannot write to destination');
    }
    @chmod($tmp_atomic, $upload_mode);
    if (!@rename($tmp_atomic, $dest)) {
        @unlink($tmp_atomic);
        audit_log('upload', $req, $E['UPLOAD_FAILED']);
        return fail($E['UPLOAD_FAILED'], 'write_fail', 'Cannot write to destination');
    }
    if ($upload_mtime !== null) @touch($dest, $upload_mtime);
    _metrics_inc($cfg, 'upload', 0);
    audit_log('upload', $req, 0, ['size' => strlen($data)]);

    // P0-2: PHP 文件自动语法检查
    $lint = null;
    $ext = strtolower(pathinfo($dest, PATHINFO_EXTENSION));
    if ($ext === 'php' && function_exists('action_lint') === false) {
        // 修复: 使用 _find_php_cli() 而非硬编码 'php', 兼容 php-fpm 环境
        $php_bin = function_exists('_find_php_cli') ? _find_php_cli() : 'php';
        $lint_out = [];
        $lint_rc = 0;
        @exec(escapeshellarg($php_bin) . ' -l ' . escapeshellarg($dest) . ' 2>&1', $lint_out, $lint_rc);
        $lint = [
            'passed'    => ($lint_rc === 0),
            'exit_code' => $lint_rc,
            'output'    => implode("\n", $lint_out),
        ];
    }

    return ok([
        'size'      => strlen($data),
        'md5'       => md5($data),
        'mtime'     => filemtime($dest),
        'mode'      => _mode4($upload_mode),
        'backed_up' => $backed_up,
        'lint'      => $lint,
    ]);
}

function action_upload($req, $cfg) {
    // CAS (expect_md5) 串行化: 旧实现 check-then-act, 并发上传同一路径时多个请求
    // 都会通过旧 md5 校验, 后写覆盖先写 (静默丢更新). 锁文件与 Python 端同算法同路径,
    // 两个服务端操作同一 work_dir 时也互斥.
    $need = $req['expect_md5'] ?? ($req['base_md5'] ?? '');
    if (!is_string($need) || $need === '') {
        return _action_upload_impl($req, $cfg);
    }
    $rel = $req['path'] ?? '';
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) {
        return _action_upload_impl($req, $cfg); // 统一由实现层返回 invalid_path
    }
    $lock_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/ai-sync/locks';
    @mkdir($lock_dir, 0755, true);
    $lf = @fopen($lock_dir . '/' . md5($dest) . '.lock', 'c');
    if ($lf && @flock($lf, LOCK_EX)) {
        try {
            return _action_upload_impl($req, $cfg);
        } finally {
            @flock($lf, LOCK_UN);
            @fclose($lf);
        }
    }
    if ($lf) @fclose($lf);
    return _action_upload_impl($req, $cfg);
}

function action_upload_batch($req, $cfg) {
    global $E, $CONFIG;
    // FIX-v4.34 (P1 静默降级): 旧版缺 items 时 $items=[] → foreach 0 次 → 返回
    //   ok:true + ok_idx=[] + total:0。AI 读不出错误, 会以为批量上传已完成。
    $raw_items = null;
    if (isset($req['items'])) {
        $raw_items = $req['items'];
    } elseif (isset($req['files'])) {
        $raw_items = $req['files'];
    }
    if ($raw_items === null) {
        return fail($E['INVALID_PARAM'], 'missing_items', 'upload_batch 缺少必填参数', [
            'hint' => '必须传 items 数组 (每项 {path, content_b64}); files 为历史别名仍兼容。'
                    . '单个文件请直接用 upload action。',
            'received_fields' => array_values(array_diff(
                array_keys($req), ['token', 'scope', 'action'])),
        ]);
    }
    if (!is_array($raw_items)) {
        return fail($E['INVALID_PARAM'], 'bad_items', 'items must be array');
    }
    if (count($raw_items) === 0) {
        return fail($E['INVALID_PARAM'], 'empty_items', 'items 数组不能为空 (没有要上传的文件)', [
            'hint' => 'items 至少包含 1 项; 若只想上传单个文件请用 upload action。',
        ]);
    }
    $items = $raw_items;
    $ok_idx = []; $fail_idx = []; $errors = []; $results = [];
    foreach ($items as $i => $item) {
        if (!is_array($item)) {
            $fail_idx[] = $i; $errors[$i] = 'bad_item'; continue;
        }
        $rel = $item['path'] ?? '';
        $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
        if (!$dest) {
            reject_path($rel, 'upload_batch', array_merge($req, ['_idx' => $i]));
            $fail_idx[] = $i; $errors[$i] = 'invalid_path'; continue;
        }
        if (preg_match('#[/\\\\]+$#', $rel)) {
            $fail_idx[] = $i; $errors[$i] = 'bad_path'; continue;
        }
        if (basename($dest) === '.') {
            $fail_idx[] = $i; $errors[$i] = 'bad_dest_path'; continue;
        }
        if (@is_dir($dest) && !@is_link($dest)) {
            // 与单文件 upload 一致: 目标是目录时明确报 dest_is_dir, 而不是备份目录后 write_fail
            $fail_idx[] = $i; $errors[$i] = 'dest_is_dir'; continue;
        }
        if (!array_key_exists('content_b64', $item) || $item['content_b64'] === null) {
            $fail_idx[] = $i; $errors[$i] = 'missing_content_b64'; continue;
        }
        if (!is_string($item['content_b64'])) {
            $fail_idx[] = $i; $errors[$i] = 'content_b64_not_string'; continue;
        }
        $data = base64_decode($item['content_b64'], true);
        if ($data === false) {
            $fail_idx[] = $i; $errors[$i] = 'bad_base64'; continue;
        }
        if (strlen($data) > $cfg['MAX_SIZE']) {
            $fail_idx[] = $i; $errors[$i] = 'too_large'; continue;
        }
        if (!@is_link($dest) && _special_kind($dest) !== null) {
            $fail_idx[] = $i; $errors[$i] = 'dest_is_special'; continue;
        }
        $backed_up = '';
        if ((@is_link($dest) || file_exists($dest)) && $cfg['BACKUP_KEEP'] > 0) {
            $backed_up = @is_link($dest)
                ? _create_link_backup($dest, $rel, $cfg)
                : _create_backup($dest, $rel, $cfg);
        }
        // 与单文件 upload 一致: 未传 mode 时保留目标原权限, 防止批量部署把可执行位抹掉
        $item_mode = 0644;
        $item_mode_req = $item['mode'] ?? null;
        if ($item_mode_req === null || $item_mode_req === '') {
            if (!@is_link($dest) && file_exists($dest)) $item_mode = fileperms($dest) & 0777;
        } else {
            if (!valid_mode_input($item_mode_req)) {
                $fail_idx[] = $i; $errors[$i] = 'bad_mode'; continue;
            }
            $item_mode = safe_mode($item_mode_req);
        }
        if ($item_mode <= 0) $item_mode = 0644;
        // mtime 同单文件 upload: 批量项可带本地 mtime
        $item_mtime = null;
        $item_mtime_req = $item['mtime'] ?? null;
        if ($item_mtime_req !== null && $item_mtime_req !== '') {
            if (!valid_mtime_input($item_mtime_req)) {
                $fail_idx[] = $i; $errors[$i] = 'bad_mtime'; continue;
            }
            $item_mtime = (int)$item_mtime_req;
        }
        $parent = dirname($dest);
        if (!is_dir($parent)) {
            if (file_exists($parent)) {
                $fail_idx[] = $i; $errors[$i] = 'parent_not_dir'; continue;
            }
            @mkdir($parent, 0755, true);
            if (!is_dir($parent)) {
                $fail_idx[] = $i; $errors[$i] = 'parent_not_dir'; continue;
            }
        }
        // P1-1-FIX: 原子写 — 同目录 tmp + rename
        $tmp_atomic = $dest . '.upload_tmp.' . bin2hex(random_bytes(4));
        if (file_put_contents($tmp_atomic, $data, LOCK_EX) === false) {
            @unlink($tmp_atomic);
            $fail_idx[] = $i; $errors[$i] = 'write_fail'; continue;
        }
        @chmod($tmp_atomic, $item_mode);
        if (!@rename($tmp_atomic, $dest)) {
            @unlink($tmp_atomic);
            $fail_idx[] = $i; $errors[$i] = 'write_fail'; continue;
        }
        if ($item_mtime !== null) @touch($dest, $item_mtime);
        $ok_idx[] = $i;
        $results[$i] = [
            'size'  => strlen($data),
            'md5'   => md5($data),
            'mtime' => filemtime($dest),
            'mode'  => _mode4($item_mode),
            'backed_up' => $backed_up,
        ];
        // AI 优化: 为每个文件写 audit_log, 使 recent_changes 可追踪批量上传的文件
        audit_log('upload_batch', array_merge($req, ['path' => $rel]), 0, [
            'size' => strlen($data), 'md5' => md5($data), 'backup' => $backed_up ?: null,
        ]);
    }
    _metrics_inc($cfg, 'upload_batch', $fail_idx ? 1008 : 0);
    // 契约: errors 是与 items 等长的数组 (成功项为 null), 与 Python 端一致;
    // 旧实现只写入失败索引, json_encode 会把它变成 {"2":...} 对象, 调用方无法按位置对齐.
    $err_list = [];
    $item_count = is_array($items) ? count($items) : 0;
    for ($i = 0; $i < $item_count; $i++) $err_list[] = $errors[$i] ?? null;
    return ok([
        'ok_idx'   => $ok_idx,
        'fail_idx' => $fail_idx,
        'errors'   => $err_list,
        'results'  => $results,
        'total'    => $item_count,
        // 顶层 ok 表示"请求已处理"而非"全部成功"; AI 直接看这两个字段即可判断部分失败.
        'ok_count' => count($ok_idx),
        'failed_count' => count($fail_idx),
        'partial'  => !empty($ok_idx) && !empty($fail_idx),
    ]);
}

function action_download($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "File '$rel' not found");
    if (is_dir($dest)) return fail($E['INVALID_PARAM'], 'is_dir', 'Cannot download a directory');
    // 特殊文件 (FIFO/socket/设备) 读取会永久阻塞或产生无限流:
    //   旧实现 file_get_contents() 在无写端的 FIFO 上会挂死整个 PHP worker,
    //   并进而占住 checksum 缓存锁, 让后续请求一起超时 (可用性放大)。
    //   safe_path 已 realpath 跟随最终链接, 这里用 stat 判定真实目标类型。
    $sk = _special_kind_follow($dest);
    if ($sk !== null) {
        return fail($E['INVALID_PARAM'], 'not_regular_file',
            "Cannot download special file '$rel' ($sk): only regular files are readable", [
                'path' => $rel, 'special_type' => $sk,
                'hint' => 'FIFO/socket/设备节点没有有限内容可传输; 如需读取请用 exec/shell 在服务器端处理。',
            ]);
    }
    clearstatcache(true, $dest);
    $size = (int)@filesize($dest);
    // 大文件交给接口层流式输出 (对齐 Python 端): 避免 base64 + JSON 整包常驻内存
    //   阈值可通过 sync_config.json 的 stream_threshold_kb / scope.stream_threshold_kb 调整
    $stream_kb = isset($cfg['STREAM_THRESHOLD_KB']) ? (int)$cfg['STREAM_THRESHOLD_KB'] : 2048;
    if ($stream_kb > 0 && $size > $stream_kb * 1024) {
        return ok(['_stream_file' => $dest]);
    }
    $content = base64_encode(file_get_contents($dest));
    return ok(['content_b64' => $content, 'size' => filesize($dest), 'md5' => md5_file($dest),
               // mode/mtime 供客户端 pull/read-remote 往返保留权限与修改时间 (尤其是 0755 脚本)
               'mode' => _mode4(fileperms($dest) & 0777),
               'mtime' => filemtime($dest)]);
}

function action_stat($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    // 不跟随最后一级: stat 一个软链接时返回链接本身的信息, 否则调用方无法判断该走文件协议还是链接协议.
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!@is_link($dest) && !file_exists($dest)) {
        return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    }
    clearstatcache(true, $dest);
    $st = @lstat($dest);
    if ($st === false) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    $data = [
        'path'   => $rel,
        'type'   => 'file',
        'path_b64' => base64_encode((string)$rel),
        'size'   => $st['size'],
        'mtime'  => $st['mtime'],
        'ctime'  => $st['ctime'],
        'atime'  => $st['atime'],
        'mode'   => _mode4($st['mode']),
        // 属主/硬链接/稀疏诊断 (传输层不保留, 但 AI 需要能观测到真实状态)
        'uid'    => $st['uid'],
        'gid'    => $st['gid'],
        'nlink'  => $st['nlink'],
        'ino'    => $st['ino'],
        'blocks' => $st['blocks'],
    ];
    if (@is_link($dest)) {
        $lm = _link_meta($dest, $cfg['WORK_DIR']);
        $data['type'] = 'symlink';
        $data['size'] = 0;
        $data['safe'] = $lm['safe'];
        $data['broken'] = $lm['broken'];
        $data['cycle'] = !empty($lm['cycle']);
        $data['resolvable'] = !empty($lm['resolvable']);
        $data['link_target'] = $lm['safe'] ? $lm['target'] : null;
        $data['link_target_rel'] = $lm['safe'] ? $lm['target_rel'] : null;
        $data['link_target_rel_b64'] = $lm['safe'] ? base64_encode((string)$lm['target_rel']) : null;
        if ($lm['safe'] && $lm['target'] !== null) $data['link_target_b64'] = base64_encode($lm['target']);
        if (!$lm['safe'] && isset($lm['reason'])) {
            $data['reason'] = $lm['reason'];
        }
        if ($lm['safe'] && $lm['target_norm'] !== null) {
            $data['md5'] = hash('md5', $lm['target_norm']);
        }
        return ok($data);
    }
    if (is_dir($dest)) {
        $data['type'] = 'dir';
        $data['size'] = 0;
        $data['child_count'] = iterator_count(new \FilesystemIterator($dest, \FilesystemIterator::SKIP_DOTS));
    } elseif (($sk = _special_kind($dest)) !== null) {
        // 特殊节点只返回元数据, 绝不 hash/读取: hash_file 在 FIFO 上会永久阻塞.
        $data['type'] = 'special';
        $data['special_type'] = $sk;
        $data['readable'] = false;
        $data['size'] = 0;
    } else {
        // FIX-large-skip-v4.39.1: 超大文件(>LARGE_SKIP_KB)不现算 md5/sha1 —
        //   增量同步只需 size+mtime 指纹; 冷缓存下 29.8GB 级文件现算会卡死 PHP worker 拖垮服务器.
        //   md5=null 时客户端用 (size,mtime) 兜底判定差异; 内容级校验走 push 回读/deploy 显式路径.
        $LARGE_SKIP_B = (int)($cfg['LARGE_SKIP_KB'] ?? 65536) * 1024;
        if ((int)$st['size'] > $LARGE_SKIP_B) {
            $data['md5']  = null;
            $data['sha1'] = null;
            $data['large_skipped'] = true;
        } else {
            $data['md5']  = md5_file($dest);
            $data['sha1'] = sha1_file($dest);
        }
    }
    return ok($data);
}

/**
 * 确定性的目录遍历 (list/checksum/diff 共用):
 *   每层先输出子目录 (按名字字节序), 再输出普通文件和软链接 (按名字),
 *   然后递归真实子目录. 软链接目录绝不递归 (防环/防越界/防重复枚举目标).
 * yield: ['full','name','type'(dir|file|symlink)]
 */
function _walk_tree_sorted($root, $max_depth = 10, $include_hidden = false, $allow_internal = false) {
    $walk = function ($dir, $depth) use (&$walk, $max_depth, $include_hidden, $allow_internal) {
        if ($depth >= $max_depth) return;
        $names = @scandir($dir);
        if ($names === false) return;
        $dirs = [];
        $files = [];
        foreach ($names as $n) {
            if ($n === '.' || $n === '..') continue;
            if (!$include_hidden && isset($n[0]) && $n[0] === '.') continue;
            if (!$allow_internal && $depth === 0 && $n === 'ai-sync') continue;
            $full = $dir . DIRECTORY_SEPARATOR . $n;
            if (@is_link($full)) {
                $files[] = $n;          // 软链接按"文件"排序输出, 不进入递归
            } elseif (is_dir($full)) {
                $dirs[] = $n;
            } else {
                $files[] = $n;
            }
        }
        sort($dirs, SORT_STRING);
        sort($files, SORT_STRING);
        foreach ($dirs as $n) {
            yield ['full' => $dir . DIRECTORY_SEPARATOR . $n, 'name' => $n, 'type' => 'dir'];
        }
        foreach ($files as $n) {
            $full = $dir . DIRECTORY_SEPARATOR . $n;
            if (@is_link($full)) {
                $type = 'symlink';
            } elseif (($sk = _special_kind($full)) !== null) {
                // FIFO/socket/设备: 只能列出元数据, 不能当普通文件读取/哈希.
                $type = 'special';
            } else {
                $type = 'file';
            }
            yield ['full' => $full, 'name' => $n, 'type' => $type];
        }
        foreach ($dirs as $n) {
            yield from $walk($dir . DIRECTORY_SEPARATOR . $n, $depth + 1);
        }
    };
    yield from $walk($root, 0);
}

function action_list($req, $cfg) {
    global $E, $CONFIG;
    // 对外契约: path 可选, 默认 "." (与 help 一致). 旧实现用 '' 导致缺省时 1011 missing_path.
    $rel = $req['path'] ?? '.';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    if (!is_dir($dest)) return fail($E['INVALID_PARAM'], 'not_dir', 'Path is not a directory');

    $recursive = bool_flag($req['recursive'] ?? false);
    if ($recursive === null) return bad_bool('recursive', $req['recursive']);
    $pattern = $req['pattern'] ?? null;
    if ($pattern !== null && !is_string($pattern)) {
        return fail($E['INVALID_PARAM'], 'bad_pattern', 'pattern 必须是字符串 (如 "*.php")', [
            'hint' => 'pattern 用于按文件名过滤, 不能传 true/数字/数组/对象',
        ]);
    }
    $include_hidden = bool_flag($req['include_hidden'] ?? false);
    if ($include_hidden === null) return bad_bool('include_hidden', $req['include_hidden']);
    // max_depth=0 表示不限深度 (客户端全量拉取目录状态时使用); 默认仍为 10 层.
    $max_depth = isset($req['max_depth']) ? intval($req['max_depth']) : 10;
    if ($max_depth <= 0) $max_depth = PHP_INT_MAX;
    $offset = intval($req['offset'] ?? 0);
    $limit = intval($req['limit'] ?? 200);
    if ($limit > 1000) $limit = 1000;
    // since_mtime: 只返回此时间之后变更的文件 (AI 监控远程变更用)
    $since_mtime = isset($req['since_mtime']) ? intval($req['since_mtime']) : 0;

    $items = [];
    $total = 0;
    $base = rtrim((string)realpath($cfg['WORK_DIR']), '/\\') . DIRECTORY_SEPARATOR;

    // 服务端簿记目录 (ai-sync/: audit.log, backups, chunks, rate 计数, metrics ...) 默认不出现在
    // 业务列举里: 两端实现产生的簿记文件数不同 (PHP 每请求写 rate .cnt, Python 走内存计数),
    // 混进 items 会让分页窗口错位、客户端把内部文件当业务文件。显式请求该目录时才返回。
    $req_norm = trim(str_replace('\\', '/', (string)$rel), '/');
    $allow_internal = ($req_norm === 'ai-sync' || strpos($req_norm, 'ai-sync/') === 0);

    // 统一遍历实现: 每层目录先子目录、再普通文件/软链接 (各按字节序), 然后递归真实目录.
    // 软链接作为独立类型输出, 绝不递归/重复枚举目标; 非递归时 max_depth=1 只输出当前层.
    $walk_depth = $recursive ? $max_depth : 1;
    foreach (_walk_tree_sorted($dest, $walk_depth, $include_hidden, $allow_internal) as $e) {
        $full = $e['full'];
        $lst = @lstat($full);
        if ($lst === false) continue;
        $mtime = (int)$lst['mtime'];
        if ($since_mtime > 0 && $mtime < $since_mtime) continue;
        $rel_path = ltrim(substr($full, strlen($base)), '/\\');
        if (!$allow_internal && ($rel_path === 'ai-sync'
                || strpos(str_replace('\\', '/', $rel_path), 'ai-sync/') === 0)) continue;
        if ($pattern && !fnmatch($pattern, $e['name'])) continue;
        $item = [
            'name'  => $e['name'],
            'path'  => $rel_path,
            'type'  => $e['type'],
            'size'  => 0,
            'mtime' => $mtime,
            'mode'  => _mode4($lst['mode'] & 07777),
        ];
        // 非 UTF-8 文件名: JSON 只能用 U+FFFD 替换, 返回值无法再定位原文件.
        // 显式标记 + 提供原始字节的 base64, 调用方可用 path_b64 精确寻址.
        if (preg_match('//u', $rel_path) !== 1) {
            $item['encoding'] = 'binary';
            $item['path_b64'] = base64_encode($rel_path);
        }
        if (preg_match('//u', $e['name']) !== 1) {
            $item['name_b64'] = base64_encode($e['name']);
        }
        if ($e['type'] === 'symlink') {
            $lm = _link_meta($full, $cfg['WORK_DIR']);
            $item['safe'] = $lm['safe'];
            $item['link_target'] = $lm['safe'] ? $lm['target'] : null;
            $item['link_target_rel'] = $lm['safe'] ? $lm['target_rel'] : null;
            $item['link_target_rel_b64'] = $lm['safe'] ? base64_encode((string)$lm['target_rel']) : null;
            $item['broken'] = $lm['broken'];
            $item['cycle'] = !empty($lm['cycle']);
            $item['resolvable'] = !empty($lm['resolvable']);
            if ($lm['safe'] && $lm['target'] !== null) $item['link_target_b64'] = base64_encode($lm['target']);
            if (!$lm['safe']) $item['reason'] = $lm['reason'];
        } elseif ($e['type'] === 'dir') {
            $n = 0;
            foreach ((array)@scandir($full) as $sub) {
                if ($sub !== '.' && $sub !== '..') $n++;
            }
            $item['child_count'] = $n;
        } elseif ($e['type'] === 'special') {
            // 明确标注不可读取: 客户端/AI 不应尝试 download/checksum 该条目.
            $item['special_type'] = _special_kind($full);
            $item['readable'] = false;
            $item['size'] = 0;
        } else {
            $item['size'] = (int)$lst['size'];
        }
        $items[] = $item;
        $total++;
    }

    $has_more = ($offset + $limit) < $total;
    $page = array_slice($items, $offset, $limit);
    return ok([
        'path'     => $rel,
        'total'    => $total,
        'offset'   => $offset,
        'limit'    => $limit,
        'has_more' => $has_more,
        'items'    => $page,
        'since_mtime' => $since_mtime,
    ]);
}

function action_diff($req, $cfg) {
    global $E, $CONFIG;
    $local_items = $req['local_items'] ?? [];
    $remote_path = $req['path'] ?? '';
    $algorithm = $req['algorithm'] ?? 'md5';
    $dest = safe_path($remote_path, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($remote_path); // v4.29 修复: 原误写未定义的 $rel, path 非法时内部错误
    if (!is_dir($dest)) return fail($E['INVALID_PARAM'], 'not_dir', 'Remote path must be a directory');

    $base = rtrim((string)realpath($cfg['WORK_DIR']), '/\\') . DIRECTORY_SEPARATOR;
    $remote_index = [];
    $want_hash = bool_flag($req['compute_hash'] ?? false);
    if ($want_hash === null) return bad_bool('compute_hash', $req['compute_hash']);
    if ($want_hash) {
        // 对齐 checksum: 白名单外的算法返回 1011, 而不是让 hash_file 抛 ValueError(1099)
        // 或静默退化。注意 'crc32' 已从两端白名单剔除 (Python 无法等价实现 BZIP2 变体)。
        $__diff_algs = ['md5','sha1','sha256','sha224','sha384','sha512','crc32b','adler32'];
        $algorithm = strtolower(trim($algorithm));
        if (!in_array($algorithm, $__diff_algs, true)) {
            return fail($E['INVALID_PARAM'], 'bad_algorithm',
                "不支持的 algorithm '$algorithm' (支持: " . implode('|', $__diff_algs) . ')', [
                    'supported' => $__diff_algs,
                ]);
        }
    }
    // 同 list/checksum: 默认不把 ai-sync/ 簿记文件算进业务差异 (两端内部文件数天然不同)
    $_diff_req = trim(str_replace('\\', '/', (string)$remote_path), '/');
    $_diff_allow_internal = ($_diff_req === 'ai-sync' || strpos($_diff_req, 'ai-sync/') === 0);

    $skipped_special = 0;
    foreach (_walk_tree_sorted($dest, PHP_INT_MAX, true, $_diff_allow_internal) as $e) {
        if ($e['type'] === 'dir') continue;
        $full = $e['full'];
        $rel = ltrim(substr($full, strlen($base)), '/\\');
        if (!$_diff_allow_internal && strpos(str_replace('\\', '/', $rel), 'ai-sync/') === 0) continue;
        if ($e['type'] === 'special') { $skipped_special++; continue; }
        if ($e['type'] === 'symlink') {
            $lm = _link_meta($full, $cfg['WORK_DIR']);
            $lst = @lstat($full);
            $entry = [
                'size'  => 0,
                'mtime' => $lst ? (int)$lst['mtime'] : 0,
                'type'  => 'symlink',
                'safe'  => $lm['safe'],
                'link_target' => $lm['safe'] ? $lm['target'] : null,
                'link_target_rel' => $lm['safe'] ? $lm['target_rel'] : null,
                'link_target_rel_b64' => $lm['safe'] ? base64_encode((string)$lm['target_rel']) : null,
                'broken' => $lm['broken'],
                'cycle' => !empty($lm['cycle']),
                'resolvable' => !empty($lm['resolvable']),
            ];
            if ($lm['safe'] && $lm['target'] !== null) $entry['link_target_b64'] = base64_encode($lm['target']);
            if (preg_match('//u', $rel) !== 1) {
                $entry['encoding'] = 'binary';
                $entry['path_b64'] = base64_encode($rel);
            }
            if ($want_hash) {
                $entry[$algorithm] = ($lm['safe'] && $lm['target_norm'] !== null)
                    ? hash($algorithm, $lm['target_norm']) : '';
            }
            $remote_index[$rel] = $entry;
            continue;
        }
        $lst = @lstat($full);
        if ($lst === false) continue;
        if (!_is_regular_file($full)) { $skipped_special++; continue; }
        $entry = [
            'size'  => (int)$lst['size'],
            'mtime' => (int)$lst['mtime'],
        ];
        if (preg_match('//u', $rel) !== 1) {
            $entry['encoding'] = 'binary';
            $entry['path_b64'] = base64_encode($rel);
        }
        if ($want_hash) {
            // 修复: 支持多种算法, 不再硬编码 md5/sha1 二选一
            $entry[$algorithm] = hash_file($algorithm, $full) ?: md5_file($full);
        }
        $remote_index[$rel] = $entry;
    }

    $local_index = [];
    foreach ($local_items as $it) {
        if (!isset($it['path'])) continue;
        $local_index[$it['path']] = $it;
    }

    $only_local = [];
    $only_remote = [];
    $modified = [];
    $identical = 0;

    foreach ($local_index as $p => $l) {
        if (!isset($remote_index[$p])) {
            $only_local[] = $p;
            continue;
        }
        $r = $remote_index[$p];
        $eq_size = isset($l['size']) && intval($l['size']) === intval($r['size']);
        $eq_mtime = isset($l['mtime']) && intval($l['mtime']) === intval($r['mtime']);
        $eq_hash = false;
        if (isset($l[$algorithm]) && isset($r[$algorithm])) {
            $eq_hash = ($l[$algorithm] === $r[$algorithm]);
        }
        if ($eq_size && ($eq_mtime || $eq_hash)) {
            $identical++;
        } else {
            $modified[] = [
                'path'       => $p,
                'local'      => $l,
                'remote'     => $r,
                'reason'     => $eq_hash ? 'mtime_diff' : ($eq_size ? 'hash_diff' : 'size_diff'),
            ];
        }
    }
    foreach ($remote_index as $p => $r) {
        if (!isset($local_index[$p])) $only_remote[] = $p;
    }

    return ok([
        'skipped_special' => $skipped_special,
        'summary' => [
            'only_local'  => count($only_local),
            'only_remote' => count($only_remote),
            'modified'    => count($modified),
            'identical'   => $identical,
        ],
        'only_local'  => $only_local,
        'only_remote' => $only_remote,
        'modified'    => $modified,
    ]);
}

function action_delete($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    // 不跟随最后一级: 删除一个软链接必须 unlink 链接本身, 绝不能删掉它指向的目标.
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!@is_link($dest) && !file_exists($dest)) return ok(['already_missing' => true]);
    $type = @is_link($dest) ? 'symlink' : (is_dir($dest) ? 'dir' : 'file');
    // P0 安全边界: path="." / "/" (以及 ./ 等) 经 safe_path_nofollow 会解析为 work_dir 根.
    // 旧实现允许 recursive=true 把整个 scope 工作区清空 (只留下审计日志重建的 ai-sync/).
    // 删除根目录不是正常同步操作, 且不可恢复, 必须在动任何文件之前拒绝.
    $root_real = @realpath(rtrim($cfg['WORK_DIR'], '/\\'));
    if ($type === 'dir' && $root_real !== false && @realpath($dest) === $root_real) {
        audit_log('delete', $req, $E['INVALID_PARAM'], ['reason' => 'root_delete_blocked', 'path' => (string)$rel]);
        return fail($E['INVALID_PARAM'], 'root_delete_not_allowed',
            '拒绝删除 scope 工作目录根: 该操作会清空整个工作区', [
                'path' => $rel,
                'hint' => 'path="." / "/" 指向 work_dir 根, 不允许删除。要清理目录请传具体子目录名; '
                        . '确实要清空工作区请在服务器端操作。',
            ]);
    }
    $backed_up = '';
    $recursive = bool_flag($req['recursive'] ?? false);
    if ($recursive === null) return bad_bool('recursive', $req['recursive']);
    // 删除前备份文件 (目录不备份, 避免递归开销; 文件必须备份防数据丢失)
    if ($type === 'file' && $cfg['BACKUP_KEEP'] > 0) {
        $backed_up = _create_backup($dest, $rel, $cfg);
    } elseif ($type === 'symlink' && $cfg['BACKUP_KEEP'] > 0) {
        // 链接备份只记录 raw target, 不跟随 (目标可能在 work_dir 外)
        $backed_up = _create_link_backup($dest, $rel, $cfg);
    }
    if ($type === 'symlink') {
        if (!@unlink($dest)) return fail($E['INTERNAL'], 'unlink_fail', 'Permission denied');
    } elseif ($type === 'dir') {
        if ($recursive) {
            // 递归删除目录及内容 (传入 WORK_DIR 作为 root 做边界校验)
            $del_result = _rmtree($dest, $cfg['WORK_DIR']);
            if (!$del_result['ok']) {
                $failed_paths = $del_result['failed'] ?? [];
                return fail($E['INTERNAL'], 'rmdir_fail', 'Recursive delete failed (partial)', [
                    'reason' => $del_result['reason'] ?? 'partial',
                    'failed_count' => count($failed_paths),
                    'failed_sample' => array_slice($failed_paths, 0, 10),
                ]);
            }
        } else {
            if (!@rmdir($dest)) return fail($E['INTERNAL'], 'rmdir_fail', 'Directory not empty or permission denied (use recursive=true for non-empty dirs)');
        }
    } else {
        if (!@unlink($dest)) return fail($E['INTERNAL'], 'unlink_fail', 'Permission denied');
    }
    audit_log('delete', $req, 0, ['type' => $type, 'backed_up' => $backed_up, 'recursive' => $recursive]);
    return ok(['deleted' => $rel, 'type' => $type, 'backed_up' => $backed_up, 'recursive' => $recursive]);
}

/**
 * 递归删除目录及其所有内容
 * 修复: 增加 WORK_DIR 边界校验防止 TOCTOU 符号链接攻击; 返回详细失败信息
 */
function _rmtree($dir, $root = null) {
    if (!is_dir($dir)) return ['ok' => false, 'reason' => 'not_dir'];
    // 边界校验: 确保要删除的目录仍在 root 内 (防符号链接逃逸)
    if ($root !== null) {
        $real_dir = @realpath($dir);
        $real_root = @realpath($root);
        if ($real_dir === false || $real_root === false) {
            return ['ok' => false, 'reason' => 'realpath_fail'];
        }
        if ($real_dir !== $real_root && strpos($real_dir, $real_root . DIRECTORY_SEPARATOR) !== 0) {
            return ['ok' => false, 'reason' => 'out_of_bounds', 'path' => $real_dir];
        }
    }
    $items = @scandir($dir);
    if ($items === false) return ['ok' => false, 'reason' => 'scandir_fail'];
    $items = array_diff($items, ['.', '..']);
    $failed = [];
    foreach ($items as $item) {
        $path = $dir . DIRECTORY_SEPARATOR . $item;
        // 修复: is_link 先判断, 符号链接一律 unlink (不跟随); 普通目录递归
        if (@is_link($path)) {
            if (!@unlink($path)) $failed[] = $path;
        } elseif (is_dir($path)) {
            $sub = _rmtree($path, $root ?? $dir);
            if (!$sub['ok']) $failed = array_merge($failed, $sub['failed'] ?? [$path]);
        } else {
            if (!@unlink($path)) $failed[] = $path;
        }
    }
    if (!@rmdir($dir) && is_dir($dir)) {
        $failed[] = $dir;
    }
    return ['ok' => empty($failed), 'failed' => $failed];
}

/**
 * 探测 PHP CLI 二进制路径.
 * fpm-fcgi 下 PHP_BINARY 指向 php-fpm, 不支持 -l/-r, 必须找真正的 CLI.
 */
function _find_php_cli() {
    static $cached = null;
    if ($cached !== null) return $cached;
    // 1. 配置显式指定
    if (!empty($GLOBALS['CONFIG']['PHP_CLI']) && is_executable($GLOBALS['CONFIG']['PHP_CLI'])) {
        $cached = $GLOBALS['CONFIG']['PHP_CLI']; return $cached;
    }
    // 2. PHP_BINARY 仅当 SAPI=cli 时可靠
    if (PHP_SAPI === 'cli' && PHP_BINARY && is_executable(PHP_BINARY)) {
        $cached = PHP_BINARY; return $cached;
    }
    // 3. 常见路径探测
    $cands = [
        '/usr/bin/php', '/usr/local/bin/php', '/usr/sbin/php',
        '/opt/php/bin/php', '/opt/php7/bin/php', '/opt/php74/bin/php',
        '/www/server/php/bin/php', '/www/server/php74/bin/php',
        '/www/server/php/74/bin/php',
        trim(PHP_BINARY) ?: '',
    ];
    // 从 php-fpm 路径推导同目录 cli/cgi 兄弟
    //   Entware/Android 常见: /opt/bin/php8-fpm, CLI 叫 php8-cli / php-cli (没有 php 软链)
    if (PHP_BINARY) {
        $base = basename(PHP_BINARY);
        $dir = dirname(PHP_BINARY);
        if (preg_match('/^php-fpm(\d+(?:\.\d+)*)$/', $base, $m)) {
            $cands[] = '/usr/bin/php' . $m[1];
            $cands[] = '/usr/local/bin/php' . $m[1];
        }
        if (preg_match('/^php(.*)-fpm$/', $base, $m)) {
            $cands[] = rtrim($dir, '/\\') . '/php' . $m[1] . '-cli';
            $cands[] = rtrim($dir, '/\\') . '/php-cli';
            $cands[] = rtrim($dir, '/\\') . '/php' . $m[1] . '-cgi';
        }
    }
    // PATH 扫描: 不同发行版 CLI 可能叫 php-cli/php8-cli/php-cgi (Entware 无 php 软链)
    $path_dirs = array_filter(explode(PATH_SEPARATOR, (string)getenv('PATH')));
    foreach (array_unique(array_merge($path_dirs, ['/opt/bin', '/usr/bin', '/usr/local/bin'])) as $d) {
        if ($d === '' || !is_dir($d)) continue;
        $names = @scandir($d);
        if (!is_array($names)) continue;
        // cli 优先于 cgi; 版本号高的先试
        $found_cli = []; $found_cgi = [];
        foreach ($names as $n) {
            if ($n === 'php' || preg_match('/^php\d+(?:\.\d+)*$/', $n)) $found_cli[] = $n;
            elseif (preg_match('/^php.*-cli$/', $n)) $found_cli[] = $n;
            elseif (preg_match('/^php.*-cgi$/', $n)) $found_cgi[] = $n;
        }
        rsort($found_cli); rsort($found_cgi);
        foreach (array_merge($found_cli, $found_cgi) as $n) {
            $cands[] = rtrim($d, '/\\') . '/' . $n;
        }
    }
    foreach ($cands as $c) {
        if ($c && @is_executable($c)) {
            // 名称含 fpm 的跳过 (php-fpm 不能跑 -l/脚本)
            if (stripos(basename($c), 'fpm') !== false) continue;
            // 尝试 -v 验证 (exec 可能被禁, 失败则仅凭 is_executable + 非 fpm 名称放行)
            $o = []; $rc = -1;
            $can_exec = @exec(escapeshellarg($c) . ' -v 2>&1', $o, $rc);
            if ($rc === 0) {
                $info = implode("\n", $o);
                if (stripos($info, 'php') !== false && stripos($info, 'fpm') === false) {
                    $cached = $c; return $cached;
                }
            } elseif ($can_exec === null && stripos(basename($c), 'php') !== false) {
                // exec 被禁, 仅凭可执行 + 非 fpm 名称 + 含 php 接受
                $cached = $c; return $cached;
            }
        }
    }
    $cached = '';
    return $cached;
}

/**
 * 安全 PHP 执行: 仅允许工作目录内 .php 文件, 强制超时, 禁用危险函数.
 * 用于 AI 在服务器端验证 PHP 代码 (替代危险的通用 exec).
 */
function action_php_run($req, $cfg) {
    global $E, $CONFIG;
    // BUG-38 修复 (审计 Finding 1): php_run 必须遵守 EXEC_ENABLED / EXEC_ALLOW_IPS,
    // 否则 scopes 里 exec_enabled=false 的隔离形同虚设, whoami 还宣称已 gated 却没真正 enforce.
    if (empty($cfg['EXEC_ENABLED'])) {
        audit_log('php_run', $req, $E['FORBIDDEN'], ['reason' => 'exec_disabled']);
        return fail($E['FORBIDDEN'], 'exec_disabled', 'php_run is disabled (EXEC_ENABLED=false)');
    }
    if (!empty($cfg['EXEC_ALLOW_IPS']) && is_array($cfg['EXEC_ALLOW_IPS'])) {
        $client_ip = function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR'] ?? '');
        $allowed = false;
        foreach ($cfg['EXEC_ALLOW_IPS'] as $rule) {
            if ($client_ip === $rule
                || (strpos($rule, '/') !== false && function_exists('ip_in_cidr') && ip_in_cidr($client_ip, $rule))) {
                $allowed = true; break;
            }
        }
        if (!$allowed) {
            audit_log('php_run', $req, $E['FORBIDDEN'], ['reason' => 'ip_blocked', 'ip' => $client_ip]);
            return fail($E['FORBIDDEN'], 'ip_blocked', "IP $client_ip not in EXEC_ALLOW_IPS");
        }
    }
    $rel = $req['path'] ?? '';
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!file_exists($dest)) return fail($E['NOT_FOUND'], 'not_found', "File '$rel' not found");
    if (!is_file($dest)) return fail($E['INVALID_PARAM'], 'not_file', 'Target must be a file');
    // 仅允许 .php 后缀, 杜绝执行任意脚本
    if (strtolower(pathinfo($dest, PATHINFO_EXTENSION)) !== 'php') {
        return fail($E['INVALID_PARAM'], 'not_php', 'Only .php files can be executed');
    }
    $php_bin = _find_php_cli();
    if (!$php_bin) {
        // 诊断: 输出环境信息便于排查
        $diag = [
            'php_binary' => PHP_BINARY,
            'php_sapi'    => PHP_SAPI,
            'disable_functions' => ini_get('disable_functions'),
            'exec_available'    => function_exists('exec'),
            'proc_open_available' => function_exists('proc_open'),
            'candidates' => [],
        ];
        foreach (['/usr/bin/php','/usr/local/bin/php','/usr/bin/php7.4','/www/server/php/74/bin/php'] as $p) {
            $diag['candidates'][$p] = ['exists' => @is_file($p), 'executable' => @is_executable($p)];
        }
        return fail($E['INTERNAL'], 'no_php_cli', 'Cannot find PHP CLI binary', $diag);
    }
    // 先做语法检查, 语法错误直接返回 (避免 PHP 抛 fatal)
    $out = []; $rc = 0;
    @exec(escapeshellarg($php_bin) . ' -l ' . escapeshellarg($dest) . ' 2>&1', $out, $rc);
    if ($rc !== 0) {
        audit_log('php_run', $req, $E['INVALID_PARAM'], ['phase' => 'lint']);
        return fail($E['INVALID_PARAM'], 'syntax_error', implode("\n", $out));
    }
    $timeout = min(max(intval($req['timeout'] ?? 15), 1), intval($cfg['EXEC_TIMEOUT_MAX'] ?? 30));
    // 安全执行: open_basedir 限定工作目录, 禁用危险函数, 超时控制
    $work_dir = rtrim($cfg['WORK_DIR'], '/\\');
    $descriptorspec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    // 修复沙箱: (1) 合并系统 disable_functions 避免覆盖放宽
    //          (2) 增加 set_time_limit / symlink / link / 文件写入类函数 (保护 audit.log/backups)
    //          (3) allow_url_fopen=0 防 SSRF
    //          (4) memory_limit 限制防 OOM
    // BUG-40 修复 (审计 Finding 4): 沙箱必须禁止破坏性文件系统操作, 否则被运行的 PHP 可以
    //   unlink('ai-sync/audit.log') 销毁审计 / unlink 备份让 rollback 失效 / chmod 0666 / rename 覆盖入口文件.
    //   open_basedir 只限制路径范围, 不禁止范围内的破坏性操作. 必须显式 disable.
    //   平衡: 保留 fopen/fwrite/file_put_contents (AI 测试代码常用), 只禁破坏性 + 提权类.
    $sys_disable = ini_get('disable_functions') ?: '';
    $extra_disable = 'exec,passthru,shell_exec,system,proc_open,popen,curl_exec,curl_multi_exec,'
                   . 'putenv,mail,ini_restore,ini_alter,dl,pcntl_exec,pcntl_fork,pcntl_signal,'
                   . 'socket_create,socket_connect,stream_socket_client,'
                   . 'set_time_limit,symlink,link,readlink,'
                   . 'phpinfo,posix_kill,posix_getpwuid,posix_getgrgid,posix_uname,'
                   . 'getmypid,getmyuid,getmygid,get_current_user,php_uname,php_ini_loaded_file,'
                   . 'unlink,rmdir,chmod,chown,chgrp,move_uploaded_file,'
                   . 'ini_set,error_log';
    $merged_disable = $sys_disable
        ? implode(',', array_unique(array_filter(array_merge(
            array_map('trim', explode(',', $sys_disable)),
            array_map('trim', explode(',', $extra_disable))
          ))))
        : $extra_disable;
    // 可选 args 参数: AI 可传 argv 给脚本
    $args = '';
    if (!empty($req['args']) && is_array($req['args'])) {
        foreach ($req['args'] as $a) $args .= ' ' . escapeshellarg((string)$a);
    }
    // 可选 stdin: AI 可传 stdin 输入给脚本
    $stdin_data = '';
    if (isset($req['stdin_b64'])) {
        $decoded = base64_decode($req['stdin_b64'], true);
        if ($decoded !== false && strlen($decoded) < 1024 * 1024) $stdin_data = $decoded;
    }
    $cmd = escapeshellarg($php_bin)
         . ' -d open_basedir=' . escapeshellarg($work_dir . PATH_SEPARATOR . sys_get_temp_dir())
         . ' -d disable_functions=' . escapeshellarg($merged_disable)
         . ' -d max_execution_time=' . intval($timeout)
         . ' -d memory_limit=128M'
         . ' -d allow_url_fopen=0'
         . ' -d allow_url_include=0'
         . ' ' . escapeshellarg($dest) . $args;
    $start_ts = microtime(true);
    // 修复: 传入最小化 env 和显式 cwd, 防止泄漏 DB_PASSWORD/API_TOKEN 等密钥
    $env = ['PATH' => getenv('PATH') ?: '/usr/bin:/bin', 'LANG' => getenv('LANG') ?: 'C'];
    $process = @proc_open($cmd, $descriptorspec, $pipes, $work_dir, $env);
    if (!is_resource($process)) {
        return fail($E['INTERNAL'], 'proc_open_fail', 'Cannot start process');
    }
    // 写 stdin 后关闭
    if ($stdin_data !== '') {
        @fwrite($pipes[0], $stdin_data);
    }
    fclose($pipes[0]);
    // 修复: 用 stream_select 并发读 stdout/stderr, 防止顺序读取管道死锁
    //       原版先读满 stdout 再读 stderr, 子进程写满 stderr 管道会阻塞致死锁
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $stdout = '';
    $stderr = '';
    $deadline = $start_ts + $timeout;
    $timed_out = false;
    while (true) {
        $remaining = $deadline - microtime(true);
        if ($remaining <= 0) { $timed_out = true; break; }
        $read = [];
        if (!feof($pipes[1])) $read[] = $pipes[1];
        if (!feof($pipes[2])) $read[] = $pipes[2];
        if (empty($read)) break;
        $write = null; $except = null;
        $n = @stream_select($read, $write, $except, (int)min(1, $remaining));
        if ($n === false) break;
        if ($n === 0) continue;
        foreach ($read as $r) {
            $chunk = fread($r, 65536);
            if ($r === $pipes[1]) $stdout .= $chunk;
            else $stderr .= $chunk;
        }
        // 防止 OOM: stdout/stderr 总量超 1MB 强制停止
        if (strlen($stdout) + strlen($stderr) > 1024 * 1024) break;
    }
    // 修复: 超时强制 kill 进程组, 防止子进程孤儿化
    if ($timed_out) {
        $status = @proc_get_status($process);
        if (!empty($status['running']) && !empty($status['pid'])) {
            $pid = $status['pid'];
            // 先 SIGTERM 整个进程组, 等 200ms, 仍存活则 SIGKILL
            if (function_exists('posix_kill')) {
                @posix_kill($pid, 15);
                usleep(200000);
                $s2 = @proc_get_status($process);
                if (!empty($s2['running'])) @posix_kill($pid, 9);
            }
        }
    }
    fclose($pipes[1]); fclose($pipes[2]);
    $rc = proc_close($process);
    // 截断超长输出, 防止内存爆炸
    $max_out = 256 * 1024;
    if (strlen($stdout) > $max_out) $stdout = substr($stdout, 0, $max_out) . "\n...[truncated]";
    if (strlen($stderr) > $max_out) $stderr = substr($stderr, 0, $max_out) . "\n...[truncated]";
    // AI 优化: 失败时自动 lint + 解析运行时错误行号 + 上下文, 让 AI 一步定位问题
    $auto_lint = null;
    $runtime_error = null;
    if ($rc !== 0) {
        // 1. 语法检查 (语法错误优先) - 仅在未超时时执行 (超时时文件未变, pre-lint 已通过)
        if (!$timed_out) {
            $lint_out = []; $lint_rc = 0;
            @exec(escapeshellarg($php_bin) . ' -l ' . escapeshellarg($dest) . ' 2>&1', $lint_out, $lint_rc);
            // 修复: 把绝对路径替换为相对路径, 减少 AI 噪声 + 不泄漏服务器物理路径
            $lint_msg = str_replace($work_dir . '/', '', implode("\n", $lint_out));
            $auto_lint = ['syntax_ok' => $lint_rc === 0, 'message' => $lint_msg];
            if ($lint_rc !== 0 && preg_match('/(?:on line|第\s*(\d+)\s*行|line\s+(\d+))/i', $lint_msg, $lm)) {
                $line = intval($lm[1] ?: $lm[2]);
                $auto_lint['error_line'] = $line;
                $lines = @file($dest, FILE_IGNORE_NEW_LINES);
                if ($lines && isset($lines[$line - 1])) {
                    $auto_lint['error_line_content'] = $lines[$line - 1];
                }
            }
        }
        // 2. 运行时错误解析 (语法 OK 但运行时 Fatal/Warning, 从 stderr 提取 file:line)
        // 修复: 用更精准的 PHP 8.x 格式 'in /path/file.php:42' 提取, 兼容 'on line N'
        if (empty($auto_lint['error_line']) && preg_match('/(?:in\s+\S+(?::(\d+))|on line (\d+))/i', $stderr, $rm)) {
            $err_line = intval($rm[1] ?: $rm[2]);
            if ($err_line > 0) {
                $runtime_error = ['line' => $err_line];
                $lines = @file($dest, FILE_IGNORE_NEW_LINES);
                if ($lines && isset($lines[$err_line - 1])) {
                    $runtime_error['line_content'] = $lines[$err_line - 1];
                    // AI 优化: 附带前后 3 行上下文, 便于 AI 直接 patch
                    $ctx_before = []; $ctx_after = [];
                    for ($i = max(0, $err_line - 4); $i < $err_line - 1; $i++) {
                        $ctx_before[] = ['line' => $i + 1, 'text' => $lines[$i]];
                    }
                    for ($i = $err_line; $i < min(count($lines), $err_line + 3); $i++) {
                        $ctx_after[] = ['line' => $i + 1, 'text' => $lines[$i]];
                    }
                    if ($ctx_before) $runtime_error['context_before'] = $ctx_before;
                    if ($ctx_after) $runtime_error['context_after'] = $ctx_after;
                }
                if (preg_match('/(Fatal error|Parse error|Warning|Notice|Deprecated|Uncaught|TypeError|ArgumentCountError|Error|Exception)/i', $stderr, $tm)) {
                    $runtime_error['type'] = trim($tm[1]);
                }
                // 提取错误消息正文 (Stack trace 之前)
                if (preg_match('/^(.+?)(?:\s+in\s+\S+:\d+|\s+Stack trace:)/ms', $stderr, $mm)) {
                    $runtime_error['message'] = trim($mm[1]);
                }
            }
        }
    }
    $duration_ms = (int)round((microtime(true) - $start_ts) * 1000);
    // exit_reason: 让 AI 直接理解退出码含义 (无需查手册)
    $exit_reason = 'normal';
    if ($timed_out) $exit_reason = 'timeout';
    elseif ($rc === 139) $exit_reason = 'segfault';
    elseif ($rc === 137) $exit_reason = 'killed';
    elseif ($rc > 128) $exit_reason = 'signal_' . ($rc - 128);
    elseif ($rc !== 0) $exit_reason = 'error';
    // crashed: 区分脚本主动 exit(N) 与 PHP fatal
    $crashed = !$timed_out && $rc !== 0 && (
        !empty($runtime_error) ||
        preg_match('/(Fatal error|Parse error|Uncaught|segfault)/i', $stderr)
    );
    // 探测 PHP 版本 (供 AI 判断语法兼容性, 如 match/enum 需要 8.0+)
    static $php_version = null;
    if ($php_version === null) {
        $v_out = []; $v_rc = 0;
        @exec(escapeshellarg($php_bin) . ' -v 2>&1', $v_out, $v_rc);
        $v_line = implode("\n", $v_out);
        if (preg_match('/PHP\s+(\d+\.\d+\.\d+)/', $v_line, $vm)) $php_version = $vm[1];
        else $php_version = PHP_VERSION;
    }
    audit_log('php_run', $req, $rc === 0 ? 0 : $E['INTERNAL'], [
        'rc' => $rc, 'duration_ms' => $duration_ms, 'timed_out' => $timed_out,
        'stderr_tail' => substr($stderr, -300),
        'error_line' => ($auto_lint['error_line'] ?? 0) ?: ($runtime_error['line'] ?? 0),
    ]);
    return ok([
        'return_code'   => $rc,
        'stdout'        => $stdout,
        'stderr'        => $stderr,
        'php_cli'       => basename($php_bin),
        'php_version'   => $php_version,
        'auto_lint'     => $auto_lint,
        'runtime_error' => $runtime_error,
        'duration_ms'   => $duration_ms,
        'timed_out'     => $timed_out,
        'exit_reason'   => $exit_reason,
        'crashed'       => $crashed,
    ]);
}

function action_rename($req, $cfg) {
    global $E, $CONFIG;
    // 别名与 Python 端一致: from/to 也可写作 path/new_path
    $from = $req['from'] ?? ($req['path'] ?? '');
    $to   = $req['to'] ?? ($req['new_path'] ?? '');
    if ($from === '' || $to === '')
        return fail($E['INVALID_PARAM'], 'missing_param', 'from/to are required', [
            'hint' => '需要 from 与 to 两个路径 (也可用 path / new_path 别名); 两者都必须相对 work_dir']);
    // 不跟随最后一级软链接: mv 链接时移动/覆盖的是链接本身, 而不是它指向的目标.
    //   safe_path 会把 .../link 解析成目标, 导致 rename 把目标文件搬走、链接留在原地.
    $src = safe_path_nofollow($from, $cfg['WORK_DIR']);
    $dst = safe_path_nofollow($to, $cfg['WORK_DIR']);
    if (!$src) return bad_path($from, 'from');
    if (!$dst) return bad_path($to, 'to');
    // 根目录不能被重命名/移动: path="." 解析为 work_dir 本身, rename 会以 EINVAL 失败并
    // 被兜成 1099 internal, 调用方(尤其 AI)看不出是参数问题.
    $src_root = @realpath(rtrim($cfg['WORK_DIR'], '/\\'));
    if ($src_root !== false && @realpath($src) === $src_root) {
        audit_log('rename', $req, $E['INVALID_PARAM'], ['reason' => 'root_rename_blocked']);
        return fail($E['INVALID_PARAM'], 'root_rename_not_allowed',
            '拒绝重命名 scope 工作目录根: from="." 指向 work_dir 本身', [
                'hint' => 'from 必须是 work_dir 内的具体文件/子目录; 根目录不能移动。',
            ]);
    }
    // 断链 file_exists() 为 false, 必须先用 is_link 判断, 否则断链既不能移动, 也不会被识别为"已存在".
    if (!@is_link($src) && !file_exists($src)) return fail($E['NOT_FOUND'], 'not_found', "from '$from' not found");
    if (@is_link($dst) || file_exists($dst)) return fail($E['INVALID_PARAM'], 'dst_exists', "to '$to' already exists");
    // 🆕 FIX-mv-backup-v4.17: 移动前备份源文件 — mv 可撤销 (backup_rb 恢复原路径).
    //   原行为: rename 直接移动, 无备份记录 → 移动后无法回滚到 mv 前状态.
    //   现行为: 文件移动前 _create_backup 源文件, 与 upload/delete 备份体系一致.
    //   v4.37: 源是软链接时按链接备份 (只记录 raw target), 不能复制它指向的文件内容.
    $mv_backed_up = 0;
    if (@is_link($src)) {
        $mv_backed_up = _create_link_backup($src, $from, $cfg) ? 1 : 0;
    } elseif (is_file($src)) {
        if (!function_exists('_create_backup')) require_once __DIR__ . '/backup.php';
        if (function_exists('_create_backup')) {
            $mv_backed_up = _create_backup($src, $from, $cfg) ? 1 : 0;
        }
    }
    $parent = dirname($dst);
    if (!is_dir($parent)) {
        if (file_exists($parent)) {
            return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                'Cannot create parent directory: a path component is a file, not a directory', [
                    'hint' => 'to 路径的父级已存在同名文件; 请换路径或先删除/重命名该文件。',
                ]);
        }
        @mkdir($parent, 0755, true);
        if (!is_dir($parent)) {
            return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                'Cannot create parent directory (permission denied or path component is a file)', [
                    'hint' => '检查 WORK_DIR 写权限, 以及 to 路径中是否存在同名文件。',
                ]);
        }
    }
    if (!@rename($src, $dst)) return fail($E['INTERNAL'], 'rename_fail', 'rename failed');
    clearstatcache(true, $dst);
    // is_dir 会跟随链接, 必须先判 is_link, 否则"链接 -> 目录"会被误报成 dir.
    $mv_type = @is_link($dst) ? 'symlink' : (is_dir($dst) ? 'dir' : 'file');
    $mv_out = ['from' => $from, 'to' => $to, 'type' => $mv_type, 'backed_up' => $mv_backed_up];
    if ($mv_type === 'symlink') {
        $lm = _link_meta($dst, $cfg['WORK_DIR']);
        $mv_out['link_target'] = $lm['safe'] ? $lm['target'] : null;
        $mv_out['link_target_rel'] = $lm['safe'] ? $lm['target_rel'] : null;
        $mv_out['link_target_rel_b64'] = $lm['safe'] ? base64_encode((string)$lm['target_rel']) : null;
        $mv_out['broken'] = $lm['broken'];
        $mv_out['cycle'] = !empty($lm['cycle']);
        $mv_out['resolvable'] = !empty($lm['resolvable']);
        if ($lm['safe'] && $lm['target'] !== null) $mv_out['link_target_b64'] = base64_encode($lm['target']);
        if (!$lm['safe'] && isset($lm['reason'])) $mv_out['reason'] = $lm['reason'];
    }
    audit_log('rename', $req, 0, ['from' => $from, 'to' => $to, 'type' => $mv_type]);
    return ok($mv_out);
}

function action_copy($req, $cfg) {
    global $E, $CONFIG;
    // 别名与 Python 端一致: from/to 也可写作 path/new_path
    $from = $req['from'] ?? ($req['path'] ?? '');
    $to   = $req['to'] ?? ($req['new_path'] ?? '');
    if ($from === '' || $to === '')
        return fail($E['INVALID_PARAM'], 'missing_param', 'from/to are required', [
            'hint' => '需要 from 与 to 两个路径 (也可用 path / new_path 别名); 两者都必须相对 work_dir']);
    // 不跟随最后一级软链接:
    //   源是链接 -> 在目标处重建链接 (保留 raw target), 不复制它指向的内容;
    //   目标是链接 -> 替换链接本身, 不写穿到它指向的文件 (copy() 会跟随目标链接).
    $src = safe_path_nofollow($from, $cfg['WORK_DIR']);
    $dst = safe_path_nofollow($to, $cfg['WORK_DIR']);
    if (!$src) return bad_path($from, 'from');
    if (!$dst) return bad_path($to, 'to');
    if (!@is_link($src) && !file_exists($src)) return fail($E['NOT_FOUND'], 'not_found', "from '$from' not found");
    $src_is_link = @is_link($src);
    if (!$src_is_link && is_dir($src)) return fail($E['INVALID_PARAM'], 'is_dir', 'Source is a directory, use chunked or batch');
    if (!$src_is_link && ($sk = _special_kind($src)) !== null) {
        return fail($E['INVALID_PARAM'], 'src_is_special',
            "Source '$from' is a special file ($sk); copy is only defined for regular files/symlinks", [
                'path' => $from, 'special_type' => $sk,
                'hint' => 'FIFO/socket/设备节点不能用 copy 复制; 如需迁移节点请用 exec 在服务器端处理。',
            ]);
    }
    // 修复: 默认不覆盖已存在文件 (与 rename 一致), 防止 AI 误操作静默丢数据; 显式 overwrite=true 才覆盖
    $overwrite = bool_flag($req['overwrite'] ?? false);
    if ($overwrite === null) return bad_bool('overwrite', $req['overwrite']);
    // 断链 file_exists()=false, 用 is_link 一并判断, 否则 overwrite 时会把断链当"不存在"而静默覆盖.
    $dst_exists = @is_link($dst) || file_exists($dst);
    if ($dst_exists && !$overwrite) {
        return fail($E['INVALID_PARAM'], 'dst_exists', "to '$to' already exists (传 overwrite=true 覆盖)", [
            'hint' => '覆盖前会自动备份; 不传 overwrite 时拒绝静默覆盖',
        ]);
    }
    if (is_dir($dst) && !@is_link($dst)) {
        return fail($E['INVALID_PARAM'], 'dst_is_dir', "to '$to' 已存在且是目录, 不能复制覆盖", [
            'hint' => '先删除或重命名该目录, 或换一个目标路径。',
        ]);
    }
    if (!@is_link($dst) && ($sk = _special_kind($dst)) !== null) {
        return fail($E['INVALID_PARAM'], 'dst_is_special',
            "to '$to' 已存在且是特殊文件 ($sk), 拒绝覆盖", [
                'path' => $to, 'special_type' => $sk,
                'hint' => '先 delete 删除该特殊节点, 再复制。',
            ]);
    }
    $backed_up = '';
    if ($dst_exists && $cfg['BACKUP_KEEP'] > 0) {
        // 目标原为链接时按链接备份 (目标可能在 work_dir 外, 不能跟随)
        $backed_up = @is_link($dst)
            ? _create_link_backup($dst, $to, $cfg)
            : _create_backup($dst, $to, $cfg);
    }
    // 父目录可能尚不存在 (safe_path_nofollow 已确保最近存在祖先在 work_dir 内, 此处 mkdir 不会逃逸)
    $parent = dirname($dst);
    if (!is_dir($parent)) {
        if (file_exists($parent)) {
            return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                'Cannot create parent directory: a path component is a file, not a directory', [
                    'hint' => 'to 路径的父级已存在同名文件; 请换路径或先删除/重命名该文件。',
                ]);
        }
        @mkdir($parent, 0755, true);
        if (!is_dir($parent)) {
            return fail($E['UPLOAD_FAILED'], 'parent_not_dir',
                'Cannot create parent directory (permission denied or path component is a file)', [
                    'hint' => '检查 WORK_DIR 写权限, 以及 to 路径中是否存在同名文件。',
                ]);
        }
    }
    if ($src_is_link) {
        $link_target = @readlink($src);
        if ($link_target === false) return fail($E['INTERNAL'], 'readlink_fail', 'Cannot read source symlink');
        // 链接文本原样复制, 但按目标位置的父目录重新校验落点必须仍在 work_dir 内
        $lm = _link_meta_from_target($link_target, $dst, $cfg['WORK_DIR']);
        if (!$lm['safe']) {
            return fail($E['INVALID_PATH'] ?? 'E_INVALID_PATH', 'invalid_link_target',
                "软链接目标被拒绝: " . ($lm['reason'] ?? 'invalid'), [
                    'hint' => '源链接的 raw target 复制到目标位置后会越出 work_dir (绝对路径/../ 越界)。',
                    'link_target' => $link_target,
                    'link_target_b64' => base64_encode((string)$link_target),
                ]);
        }
        $tmp_link = $dst . '.copy_tmp.' . bin2hex(random_bytes(4));
        if (!_symlink_raw($link_target, $tmp_link, $cfg['WORK_DIR'])) {
            return fail($E['UPLOAD_FAILED'], 'symlink_fail', 'Cannot create symlink (permission denied?)', [
                'hint' => '目标文件系统可能不支持符号链接 (FAT/exFAT), 或父目录不可写。',
            ]);
        }
        if (!@rename($tmp_link, $dst)) {
            @unlink($tmp_link);
            return fail($E['INTERNAL'], 'copy_fail', 'copy failed');
        }
        clearstatcache(true, $dst);
        $lm_after = _link_meta($dst, $cfg['WORK_DIR']);
        $link_md5 = ($lm_after['safe'] && $lm_after['target_norm'] !== null)
            ? hash('md5', $lm_after['target_norm']) : '';
        $lst_after = @lstat($dst);
        audit_log('copy', $req, 0, ['type' => 'symlink', 'link_target' => $link_target,
                                   'backed_up' => $backed_up ?: null]);
        return ok(['from' => $from, 'to' => $to, 'type' => 'symlink', 'size' => 0,
                   'md5' => $link_md5, 'mtime' => $lst_after ? (int)$lst_after['mtime'] : time(),
                   'mode' => $lst_after ? _mode4($lst_after['mode']) : '0777',
                   'link_target' => $lm_after['safe'] ? $lm_after['target'] : null,
                   'link_target_b64' => ($lm_after['safe'] && $lm_after['target'] !== null) ? base64_encode($lm_after['target']) : null,
                   'link_target_rel' => $lm_after['safe'] ? $lm_after['target_rel'] : null,
                   'link_target_rel_b64' => $lm_after['safe'] ? base64_encode((string)$lm_after['target_rel']) : null,
                   'broken' => $lm_after['broken'],
                   'cycle' => !empty($lm_after['cycle']),
                   'resolvable' => !empty($lm_after['resolvable']),
                   'backed_up' => $backed_up]);
    }
    // 普通文件: 先写同目录临时文件再 rename — 若目标处是链接, copy() 会把内容写进它指向的文件.
    $tmp_copy = $dst . '.copy_tmp.' . bin2hex(random_bytes(4));
    if (!@copy($src, $tmp_copy)) {
        @unlink($tmp_copy);
        return fail($E['INTERNAL'], 'copy_fail', 'copy failed');
    }
    // 保留源文件权限位: 复制可执行脚本后仍可执行 (copy() 默认按 umask 建新文件)
    @chmod($tmp_copy, fileperms($src) & 0777);
    if (!@rename($tmp_copy, $dst)) {
        @unlink($tmp_copy);
        return fail($E['INTERNAL'], 'copy_fail', 'copy failed');
    }
    clearstatcache(true, $dst);
    audit_log('copy', $req, 0, ['from' => $from, 'to' => $to, 'backed_up' => $backed_up ?: null]);
    return ok(['from' => $from, 'to' => $to, 'type' => 'file',
               'size' => filesize($dst), 'md5' => md5_file($dst),
               'mode' => _mode4(fileperms($dst) & 0777), 'backed_up' => $backed_up]);
}

function action_mkdir($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    // 修复: 删除原 "safe_path 失败时预创建父目录" 逻辑 — 该逻辑用未校验的 $rel 直接拼路径并 mkdir,
    // 当 $rel 含 .. 时会在 WORK_DIR 外创建任意目录 (路径遍历写漏洞).
    // 安全做法: 直接拒绝 safe_path 不通过的路径, 让客户端先 mkdir 父目录.
    $dest = safe_path($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    $mode_req = $req['mode'] ?? null;
    $has_mode = !($mode_req === null || $mode_req === '');
    if ($has_mode && !valid_mode_input($mode_req)) return bad_mode($mode_req);
    $mode = $has_mode ? safe_mode($mode_req, 0755) : 0755;
    if (is_dir($dest)) {
        // 已存在也给 mode: push 的目录同步需要在目录被上传流程隐式创建后回填正确权限
        if ($has_mode) @chmod($dest, $mode);
        return ok(['path' => $rel, 'already_exists' => true, 'mode' => $mode]);
    }
    // 同名文件已存在: mkdir 只会返回 false, 旧实现报 1099 "Cannot create directory",
    // 调用方无法判断是"路径被文件占用"还是"权限/磁盘问题".
    if (file_exists($dest)) {
        return fail($E['INVALID_PARAM'], 'path_is_file',
            "Path '$rel' 已存在且是文件, 不能创建同名目录", [
                'hint' => '换一个目录名, 或先删除/重命名该文件 (delete/rename)。',
            ]);
    }
    if (@mkdir($dest, $mode, true)) {
        @chmod($dest, $mode);  // umask 会削减 mkdir 的 mode, 显式 chmod 保证原样
        audit_log('mkdir', $req, 0, ['mode' => $mode]);
        return ok(['path' => $rel, 'mode' => $mode, 'recursive' => true]);
    }
    if (is_dir($dest)) return ok(['path' => $rel, 'already_exists' => true, 'mode' => $mode]);
    return fail($E['UPLOAD_FAILED'], 'mkdir_fail',
        'Cannot create directory (a parent component is a file, or permission denied)', [
            'hint' => '检查路径中间是否已有同名文件, 以及 WORK_DIR 下是否有写权限。',
        ]);
}

function action_checksum($req, $cfg) {
    global $E, $CONFIG;
    $rel = $req['path'] ?? '';
    // 不跟随最后一级: checksum 一个软链接时返回链接本身 (type=symlink), 不返回目标文件摘要.
    $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
    if (!$dest) return bad_path($rel);
    if (!@is_link($dest) && !file_exists($dest)) {
        return fail($E['NOT_FOUND'], 'not_found', "Path '$rel' not found");
    }
    $algorithm = $req['algorithm'] ?? 'md5';
    // 两端共同白名单, 刻意不含 PHP 的 'crc32' (BZIP2 变体): Python 端 zlib 无法等价实现,
    // 保留会出现"同名不同值"的摘要。crc32b/adler32 两端输出逐字节一致。
    $__supported_algs = ['md5','sha1','sha256','sha224','sha384','sha512','crc32b','adler32'];
    if (!is_string($algorithm)) {
        return fail($E['INVALID_PARAM'], 'bad_algorithm',
            'algorithm 必须是字符串: ' . implode('|', $__supported_algs), [
                'supported' => $__supported_algs,
                'hint' => 'algorithm 不能传 true/数字/数组/对象',
            ]);
    }
    $algorithm = strtolower(trim($algorithm));
    if (!in_array($algorithm, $__supported_algs, true)) {
        return fail($E['INVALID_PARAM'], 'bad_algorithm',
            "不支持的 algorithm '$algorithm' (支持: " . implode('|', $__supported_algs) . ')', [
                'supported' => $__supported_algs,
            ]);
    }
    $pattern = $req['pattern'] ?? null;
    if ($pattern !== null && !is_string($pattern)) {
        return fail($E['INVALID_PARAM'], 'bad_pattern', 'pattern 必须是字符串 (如 "*.php")', [
            'hint' => 'pattern 用于按文件名过滤, 不能传 true/数字/数组/对象',
        ]);
    }
    $base = rtrim((string)realpath($cfg['WORK_DIR']), '/\\') . DIRECTORY_SEPARATOR;
    $items = [];
    $total = 0;
    $skipped_special = 0;
    $skipped_large = 0;
    $skipped = [];
    $start = microtime(true);

    // 🆕 FIX-checksum-cache-v4.17: MD5 缓存 — 文件 mtime+size 未变则复用已算 MD5.
    //   实测 2457 文件每次全算需 ~3.2s; 缓存后静态文件直接跳过计算 (动态文件 mtime 变自动失效重算).
    //   缓存 key 前缀用 WORK_DIR 指纹, 避免多 scope 交叉污染.
    $cache_dir = rtrim($cfg['WORK_DIR'], '/') . '/ai-sync/cache';
    @mkdir($cache_dir, 0755, true);
    $cache_file = $cache_dir . '/checksum_cache.json';
    $cache = [];
    if (is_file($cache_file)) {
        $cache = @json_decode(@file_get_contents($cache_file), true) ?: [];
    }
    $cache_dirty = false;
    $cache_key_prefix = md5($cfg['WORK_DIR']) . '|';

    // 同 list: 默认剔除 ai-sync/ 簿记目录 (两端内部文件数天然不同), 显式请求时才返回
    $__req_norm = trim(str_replace('\\', '/', (string)$rel), '/');
    $allow_internal = ($__req_norm === 'ai-sync' || strpos($__req_norm, 'ai-sync/') === 0);

    $emit_link = function ($full, $rel_p) use (&$items, &$total, $algorithm, $cfg) {
        $lm = _link_meta($full, $cfg['WORK_DIR']);
        $lst = @lstat($full);
        $item = [
            'path'  => $rel_p,
            'size'  => 0,
            'mtime' => $lst ? (int)$lst['mtime'] : 0,
            'mode'  => '0777',
            'type'  => 'symlink',
            'safe'  => $lm['safe'],
            'link_target' => $lm['safe'] ? $lm['target'] : null,
            'link_target_rel' => $lm['safe'] ? $lm['target_rel'] : null,
            'link_target_rel_b64' => $lm['safe'] ? base64_encode((string)$lm['target_rel']) : null,
            'broken' => $lm['broken'],
            'cycle' => !empty($lm['cycle']),
            'resolvable' => !empty($lm['resolvable']),
        ];
        if ($lm['safe'] && $lm['target'] !== null) $item['link_target_b64'] = base64_encode($lm['target']);
        if (!$lm['safe']) $item['reason'] = $lm['reason'];
        if (preg_match('//u', $rel_p) !== 1) {
            $item['encoding'] = 'binary';
            $item['path_b64'] = base64_encode($rel_p);
        }
        $item[$algorithm] = ($lm['safe'] && $lm['target_norm'] !== null)
            ? hash($algorithm, $lm['target_norm']) : '';
        $items[] = $item;
        $total++;
    };

    if (@is_link($dest)) {
        $emit_link($dest, ltrim(str_replace('\\', '/', (string)$rel), '/'));
        return ok([
            'algorithm' => $algorithm,
            'items'     => $items,
            'total'     => $total,
            'elapsed_ms'=> round((microtime(true) - $start) * 1000, 1),
        ]);
    }

    if (is_dir($dest)) {
        foreach (_walk_tree_sorted($dest, PHP_INT_MAX, true, $allow_internal) as $e) {
            if ($e['type'] === 'dir') continue;
            $full = $e['full'];
            $rel_p = ltrim(substr($full, strlen($base)), '/\\');
            // 服务端簿记目录不参与业务校验和 (与 list 一致); 显式请求 ai-sync 时仍返回
            $rel_norm = str_replace('\\', '/', $rel_p);
            if (!$allow_internal && ($rel_norm === 'ai-sync' || strpos($rel_norm, 'ai-sync/') === 0)) continue;
            if ($pattern && !fnmatch($pattern, $e['name'])) continue;
            if ($e['type'] === 'symlink') {
                $emit_link($full, $rel_p);
                continue;
            }
            if ($e['type'] === 'special') {
                // FIFO/socket/设备节点没有有限内容, 不能 hash (hash_file 会阻塞).
                // 明确列在 skipped 中, 而不是静默消失, 让调用方知道"为什么少了一个条目".
                $skipped_special++;
                if (count($skipped) < 100) {
                    $skipped[] = ['path' => $rel_p, 'special_type' => _special_kind($full)];
                }
                continue;
            }
            // 防 TOCTOU: 遍历后被替换为特殊节点时也绝不进入 hash_file.
            if (!_is_regular_file($full)) {
                $skipped_special++;
                if (count($skipped) < 100) {
                    $skipped[] = ['path' => $rel_p, 'special_type' => _special_kind_follow($full) ?: 'not_regular'];
                }
                continue;
            }
            $alg = $algorithm;
            $lst = @lstat($full);
            if ($lst === false) continue;
            $mtime = (int)$lst['mtime'];
            $size  = (int)$lst['size'];
            // FIX-large-skip-v4.39.1: 超大文件跳过现算 hash — 增量同步用 size+mtime 指纹足够;
            //   冷缓存下现算 29.8GB 实测 30s+ 未完成, 卡死 PHP worker 拖垮服务器 (负载 6+).
            //   md5=null 时客户端降级用 (size,mtime) 判定差异; 小文件仍现算 (带缓存复用).
            $LARGE_SKIP_B = (int)($cfg['LARGE_SKIP_KB'] ?? 65536) * 1024;
            if ($size > $LARGE_SKIP_B) {
                $item = [
                    'path'  => $rel_p,
                    'size'  => $size,
                    'mtime' => $mtime,
                    'mode'  => _mode4($lst['mode'] & 07777),
                    $alg    => null,
                    'large_skipped' => true,
                ];
                if (preg_match('//u', $rel_p) !== 1) {
                    $item['encoding'] = 'binary';
                    $item['path_b64'] = base64_encode($rel_p);
                }
                $items[] = $item;
                $total++;
                $skipped_large++;
                continue;
            }
            // 缓存有效性必须带 ctime+inode: 仅 mtime+size 时, 同步工具恢复 mtime 后
            // 内容已变但缓存仍命中 → checksum 返回旧 hash → 客户端误判"一致"跳过下载 (静默丢更新).
            $ctime = (int)$lst['ctime'];
            $ino   = (int)$lst['ino'];
            // 校验和缓存 — 文件身份/时间戳命中才复用已算 hash (跳过磁盘读 + 哈希计算)
            $ckey  = $cache_key_prefix . $alg . '|' . $rel_p;
            if (isset($cache[$ckey]) && $cache[$ckey]['mtime'] == $mtime && $cache[$ckey]['size'] == $size
                    && (int)($cache[$ckey]['ctime'] ?? -1) == $ctime
                    && (int)($cache[$ckey]['ino'] ?? -1) == $ino) {
                $hashval = $cache[$ckey]['hash'];
            } else {
                $hashval = @hash_file($alg, $full);
                if ($hashval === false) $hashval = '';
                $cache[$ckey] = ['mtime' => $mtime, 'size' => $size,
                                 'ctime' => $ctime, 'ino' => $ino, 'hash' => $hashval];
                $cache_dirty = true;
            }
            $item = [
                'path'  => $rel_p,
                'size'  => $size,
                'mtime' => $mtime,
                'mode'  => _mode4($lst['mode'] & 07777),
                $alg    => $hashval,
            ];
            if (preg_match('//u', $rel_p) !== 1) {
                $item['encoding'] = 'binary';
                $item['path_b64'] = base64_encode($rel_p);
            }
            $items[] = $item;
            $total++;
        }
    } else {
        $rel_p = ltrim(str_replace('\\', '/', (string)$rel), '/');
        if (!$pattern || fnmatch($pattern, _utf8_basename($rel_p))) {
            if (($sk = _special_kind($dest)) !== null) {
                return fail($E['INVALID_PARAM'], 'not_regular_file',
                    "Cannot checksum special file '$rel' ($sk): only regular files are hashable", [
                        'path' => $rel, 'special_type' => $sk,
                        'hint' => 'FIFO/socket/设备节点没有有限内容; checksum 只适用于普通文件和软链接。',
                    ]);
            }
            $alg = $algorithm;
            $lst = @lstat($dest);
            $mtime = $lst ? (int)$lst['mtime'] : 0;
            $size  = $lst ? (int)$lst['size'] : 0;
            // FIX-large-skip-v4.39.1: 超大文件单文件 checksum 同样跳过现算 (同目录分支策略)
            $LARGE_SKIP_B = (int)($cfg['LARGE_SKIP_KB'] ?? 65536) * 1024;
            if ($size > $LARGE_SKIP_B) {
                $item = [
                    'path'  => $rel_p,
                    'size'  => $size,
                    'mtime' => $mtime,
                    'mode'  => $lst ? _mode4($lst['mode'] & 07777) : '0644',
                    $alg    => null,
                    'large_skipped' => true,
                ];
                if (preg_match('//u', $rel_p) !== 1) {
                    $item['encoding'] = 'binary';
                    $item['path_b64'] = base64_encode($rel_p);
                }
                $items[] = $item;
                $total++;
                $skipped_large++;
            } else {
                $hashval = _is_regular_file($dest) ? @hash_file($alg, $dest) : false;
                if ($hashval === false) $hashval = '';
                $item = [
                    'path'  => $rel_p,
                    'size'  => $size,
                    'mtime' => $mtime,
                    'mode'  => $lst ? _mode4($lst['mode'] & 07777) : '0644',
                    $alg    => $hashval,
                ];
                if (preg_match('//u', $rel_p) !== 1) {
                    $item['encoding'] = 'binary';
                    $item['path_b64'] = base64_encode($rel_p);
                }
                $items[] = $item;
                $total++;
            }
        }
    }
    if ($cache_dirty) {
        // 防膨胀: 超 20000 条丢最旧一半 (array_slice 保留 key)
        if (count($cache) > 20000) {
            $cache = array_slice($cache, -10000, null, true);
        }
        @file_put_contents($cache_file, json_encode($cache));
    }
    return ok([
        'algorithm' => $algorithm,
        'items'     => $items,
        'total'     => $total,
        'skipped_special' => $skipped_special,
        'skipped_large' => $skipped_large,
        'skipped'   => $skipped,
        'elapsed_ms'=> round((microtime(true) - $start) * 1000, 1),
    ]);
}

// 备份辅助函数 (供 upload/upload_batch 使用)
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


if (!function_exists('_create_backup')) {
function _create_backup($dest, $rel, $cfg) {
    $backup_dir = rtrim($cfg['WORK_DIR'], '/') . '/' . $cfg['BACKUP_DIR'];
    @mkdir($backup_dir, 0755, true);
    $file_backup_dir = $backup_dir . '/' . _utf8_dirname($rel) . '/' . _utf8_basename($rel);
    @mkdir($file_backup_dir, 0755, true);
    $ts = date('Ymd_His') . '_' . substr(md5($rel . microtime()), 0, 6);
    $backup_file = $file_backup_dir . '/' . _utf8_basename($rel) . '.bak.' . $ts;
    // 只备份普通文件: FIFO/socket/设备 copy() 会阻塞或产生无限流.
    if (!_is_regular_file($dest)) return '';
    if (!@copy($dest, $backup_file)) return '';
    // 备份保留源文件权限位: 否则 rollback 会把 0755 脚本恢复成 0644 (umask 决定),
    // 设备上的 init.d/S* 之类脚本直接失效。
    @chmod($backup_file, fileperms($dest) & 0777);
    $files = glob($file_backup_dir . '/' . _utf8_basename($rel) . '.bak.*');
    if ($files && count($files) > $cfg['BACKUP_KEEP']) {
        usort($files, function ($a, $b) { return filemtime($b) - filemtime($a); });
        foreach (array_slice($files, $cfg['BACKUP_KEEP']) as $old) @unlink($old);
    }
    return _utf8_basename($backup_file);
}
}
