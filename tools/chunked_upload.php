<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: Chunked Upload
 * chunked_upload (init/put/assemble/status/abort) + _cleanup_chunks
 *
 * 修复 (v4.2):
 *   - upload_id 严格格式校验 (防路径穿越)
 *   - chunk_size 除零崩溃防护 + chunk_count 上限
 *   - assemble 并发锁 (flock), 防同 upload_id 多客户端损坏文件
 *   - assemble 改临时文件写入 + rename (原子), 校验失败原文件从未被动过
 *   - _chunk_restore_or_delete 返回实际动作 (restored/deleted), 错误信息准确
 *   - chunk_index 范围 + chunk 大小校验 (防磁盘填充)
 *   - assemble 中 fopen 失败检查
 *   - 审计日志 (assemble 成功/失败)
 *   - 新增 op=abort (AI 可主动取消上传, 清理分片)
 *   - meta 文件 JSON 解析校验
 */
require_once __DIR__ . '/link_common.php';


/* P0-1 fix: 加载 _create_backup（与 upload/upload_batch/backup 行为一致）
   修复: 原实现只在 WORK_DIR/tools/backup.php 找, 而标准部署里工具都在 sync_api.php 同级 tools/ 下,
   WORK_DIR 是业务目录没有 tools/ → require 静默失败 → _create_backup 未定义 → assemble 覆盖时
   不备份直接丢原文件 (数据丢失)。改为优先找本文件同目录。 */
if (!function_exists('_create_backup')) {
    $_chunked_backup_candidates = [
        __DIR__ . '/backup.php',
        rtrim(($GLOBALS['CONFIG']['WORK_DIR'] ?? __DIR__), '/\\') . '/tools/backup.php',
    ];
    foreach ($_chunked_backup_candidates as $_chunked_backup) {
        if (file_exists($_chunked_backup)) { require_once $_chunked_backup; break; }
    }
}

/**
 * 校验 upload_id 格式: cu_ + 12 位 hex (md5 截断)
 * 修复: 原代码无校验, ../chunks/cu_victim 可路径穿越到其他 upload 目录
 */
function _chunk_validate_uid($uid) {
    return is_string($uid) && preg_match('/^cu_[0-9a-f]{12}$/', $uid);
}

/**
 * 安全: 校验分片上传调用者 token 与 init 时一致, 防止跨 token 劫持 upload_id.
 */
function _chunk_verify_owner($meta, $req, $cfg) {
    if (!is_array($meta) || !isset($meta['token_hash'])) return false;
    $tkh = substr(hash('sha256', $cfg['TOKEN'] . ($req['token'] ?? '')), 0, 12);
    return hash_equals($meta['token_hash'], $tkh);
}

/**
 * 校验失败时从最新备份恢复原文件; 无备份则删除.
 * 修复: 返回实际动作类型, 让调用方返回准确 message (原版无备份时说 "restored from backup" 是谎言)
 */
function _chunk_restore_or_delete($dest, $rel, $cfg, $backed_up) {
    if (empty($backed_up)) {
        if (is_link($dest) || file_exists($dest)) @unlink($dest);
        return 'deleted';
    }
    $backup_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['BACKUP_DIR'] . '/' . _utf8_dirname($rel) . '/' . _utf8_basename($rel);
    $candidates = glob($backup_dir . '/' . _utf8_basename($rel) . '.bak.*') ?: [];
    if ($candidates) {
        usort($candidates, function ($a, $b) { return filemtime($b) - filemtime($a); });
        if (_restore_backup_to_dest($candidates[0], $dest, $cfg) !== false) return 'restored';
        if (is_link($dest) || file_exists($dest)) @unlink($dest);
        return 'delete_failed_restore_failed';
    }
    if (is_link($dest) || file_exists($dest)) @unlink($dest);
    return 'deleted';
}

/**
 * 安全读取并解析 meta 文件 (带 is_array 校验)
 */
function _chunk_load_meta($meta_file) {
    $raw = @file_get_contents($meta_file);
    if ($raw === false) return null;
    $meta = json_decode($raw, true);
    if (!is_array($meta) || empty($meta['dest']) || empty($meta['path'])) return null;
    return $meta;
}

function action_chunked_upload($req, $cfg) {
    global $E, $CONFIG;
    $op = $req['op'] ?? '';
    $chunk_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['CHUNK_DIR'];
    // 强制末尾斜杠, 避免配置不规范时 glob/mkdir 拼接错误
    if (substr($chunk_dir, -1) !== '/') $chunk_dir .= '/';

    if ($op === 'init') {
        $rel = $req['path'] ?? '';
        // 不跟随最后一级: 目标是链接时, assemble 要替换链接本身, 而不是写穿到目标.
        $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
        if (!$dest) return bad_path($rel);
        // 与 upload 一致: 分片上传的目标是文件, path 不能以 / 结尾;
        // 目标已是目录时也应在 init 阶段拒绝, 而不是等 assemble 到最后一步才报 rename_fail。
        if (preg_match('#[/\\\\]+$#', $rel)) {
            return fail($E['INVALID_PARAM'], 'bad_path',
                "chunked_upload 的 path 不能以 / 结尾 (它是文件路径): '$rel'", [
                    'hint' => '要写入目录请给出完整文件名, 如 "dir/big.bin"; 要创建目录请用 mkdir。',
                ]);
        }
        if (is_dir($dest) && !is_link($dest)) {
            return fail($E['INVALID_PARAM'], 'is_dir',
                "Path '$rel' 是目录, 不能作为分片上传目标", [
                    'hint' => 'chunked_upload 的目标必须是文件路径; 目录请换一个不冲突的文件名。',
                ]);
        }
        // 与 Python 端一致: 特殊文件 (FIFO/socket/device) 在 init 就拒绝, 不要拖到 assemble.
        if (!is_link($dest) && ($sk = _special_kind($dest)) !== null) {
            return fail($E['INVALID_PARAM'], 'dest_is_special',
                "目标 '$rel' 已存在且是特殊文件 ($sk), 拒绝覆盖", [
                    'path' => $rel, 'special_type' => $sk,
                    'hint' => '先 delete 删除该节点, 再重新 init/assemble 上传。',
                ]);
        }
        // mode 省略/null 时必须保留目标文件原有权限 (与 upload/upload_batch 一致),
        // 不能默认 0644 把已有 0755 可执行文件降级. 真正落盘模式在 assemble 时决定.
        $mode_req = $req['mode'] ?? null;
        $has_mode = !($mode_req === null || $mode_req === '');
        if ($has_mode && !valid_mode_input($mode_req)) return bad_mode($mode_req);
        // 可选 mtime: assemble 落盘后恢复 (客户端推送大文件时保留修改时间)
        $mtime_req = $req['mtime'] ?? null;
        if ($mtime_req !== null && $mtime_req !== '' && !valid_mtime_input($mtime_req)) {
            return bad_mtime($mtime_req);
        }
        $mtime_val = ($mtime_req === null || $mtime_req === '') ? null : (int)$mtime_req;
        // P0-1 fix: 兼容 size / total_size / total 三个字段（对外文档 & 部分客户端传 total）
        $total = intval($req['size'] ?? $req['total_size'] ?? $req['total'] ?? 0);
        if ($total <= 0) {
            return fail($E['INVALID_PARAM'], 'invalid_size',
                "total_size/size 必须是正整数 (字节数), 收到 $total", [
                    'hint' => 'init 时必须给出文件总字节数, 如 "total_size": 10485760; 空文件请直接用 upload, '
                            . '路径以 / 结尾或目标为目录也会被拒绝。',
                ]);
        }
        if ($total > $cfg['MAX_CHUNKED_SIZE']) {
            return fail($E['FILE_TOO_LARGE'], 'too_large',
                'Size ' . $total . ' exceeds MAX_CHUNKED_SIZE (' . $cfg['MAX_CHUNKED_SIZE'] . ')', [
                    'total_size' => $total,
                    'max_chunked_size' => $cfg['MAX_CHUNKED_SIZE'],
                    'hint' => '超过上限的文件无法分片上传; 联系管理员调整 scope 的 max_chunked_size, '
                            . '或先压缩/拆分后分别上传。',
                ]);
        }
        $chunk_size = intval($req['chunk_size'] ?? $cfg['CHUNK_SIZE_DEFAULT']);
        // 修复: chunk_size 必须 > 0, 否则 ceil($total/$chunk_size) 除零崩溃 (PHP 8 抛 DivisionByZeroError)
        if ($chunk_size <= 0) {
            return fail($E['INVALID_PARAM'], 'bad_chunk_size', 'chunk_size must be > 0');
        }
        // 修复: chunk_count 上限, 防小 chunk_size (如 1) 导致 10^9 次 fopen DoS
        $chunk_count = intval(ceil($total / $chunk_size));
        if ($chunk_count > 100000) {
            return fail($E['INVALID_PARAM'], 'too_many_chunks', "chunk_count $chunk_count 超过上限 100000, 请增大 chunk_size", [
                'chunk_count' => $chunk_count, 'limit' => 100000,
            ]);
        }
        $upload_id = 'cu_' . substr(md5($rel . microtime(true) . random_bytes(8)), 0, 12);
        $meta = [
            'upload_id'  => $upload_id,
            'path'       => $rel,
            'dest'       => $dest,
            'total_size' => $total,
            'chunk_size' => $chunk_size,
            'sha256'     => $req['sha256'] ?? '',
            'mode'       => $has_mode ? $mode_req : null,
            'mtime'      => $mtime_val,
            'created_at' => time(),
            'token_hash' => substr(hash('sha256', $cfg['TOKEN'] . ($req['token'] ?? '')), 0, 12),
        ];
        $meta_file = $chunk_dir . $upload_id . '.meta.json';
        @mkdir($chunk_dir, 0755, true);
        if (file_put_contents($meta_file, json_encode($meta, JSON_UNESCAPED_UNICODE), LOCK_EX) === false) {
            return fail($E['UPLOAD_FAILED'], 'meta_write_fail', 'Cannot write meta');
        }
        @mkdir($chunk_dir . $upload_id, 0755, true);
        audit_log('chunked_upload', array_merge($req, ['op' => 'init']), 0, [
            'upload_id' => $upload_id, 'path' => $rel, 'total_size' => $total, 'chunk_count' => $chunk_count,
        ]);
        return ok([
            'upload_id'    => $upload_id,
            'total_chunks' => $chunk_count,
            'chunk_count'  => $chunk_count,
            'chunk_size'   => $chunk_size,
        ]);
    }

    // put / assemble / status / abort 都需要校验 upload_id 格式
    if (in_array($op, ['put', 'assemble', 'status', 'abort'], true)) {
        $uid = $req['upload_id'] ?? '';
        if (!_chunk_validate_uid($uid)) {
            return fail($E['INVALID_PARAM'], 'bad_upload_id', "upload_id 格式非法 (需 cu_+12 hex)");
        }
        $meta_file = $chunk_dir . $uid . '.meta.json';
        if (!file_exists($meta_file)) {
            return fail($E['NOT_FOUND'], 'upload_not_found', "upload_id '$uid' not found");
        }
        $meta = _chunk_load_meta($meta_file);
        if ($meta === null) {
            return fail($E['INTERNAL'], 'meta_corrupted', "meta 文件损坏或字段缺失: $uid");
        }
        if (!_chunk_verify_owner($meta, $req, $cfg)) {
            return fail($E['FORBIDDEN'], 'token_mismatch', 'Token does not match the upload owner');
        }
        $chunk_count = intval(ceil($meta['total_size'] / $meta['chunk_size']));

        if ($op === 'put') {
            // 缺失/null 与类型错误分开报: 旧实现 intval("abc")=0, 会把内容静默写到第 0 片,
            // 最终 assemble 只报 size_mismatch, AI 完全看不出根因.
            if (!array_key_exists('chunk_index', $req) || $req['chunk_index'] === null) {
                return fail($E['INVALID_PARAM'], 'missing_chunk_index',
                    "Required field 'chunk_index' is missing (received fields: " . _brief_keys($req) . ")", [
                        'hint' => '分片从 0 开始, 第 i 片传 "chunk_index": i; 可用 status 查看 missing 列表。',
                    ]);
            }
            $idx_raw = $req['chunk_index'];
            $idx_ok = is_int($idx_raw)
                || (is_float($idx_raw) && floor($idx_raw) == $idx_raw)
                || (is_string($idx_raw) && preg_match('/^\s*[+-]?[0-9]+\s*$/', $idx_raw) === 1);
            if (!$idx_ok) {
                return fail($E['INVALID_PARAM'], 'bad_chunk_index_type',
                    'chunk_index 必须是整数 (0 起的片序号), 收到 ' . gettype($idx_raw), [
                        'received' => is_scalar($idx_raw) ? $idx_raw : gettype($idx_raw),
                        'hint' => '不要传 true/false、小数 (1.9) 或非数字字符串; 请传 "chunk_index": 0, 1, 2 ...',
                    ]);
            }
            $idx = (int)$idx_raw;
            // 修复: chunk_index 范围校验, 防攻击者传 999999999 写满磁盘
            if ($idx < 0 || $idx >= $chunk_count) {
                return fail($E['INVALID_PARAM'], 'bad_chunk_index', "chunk_index $idx 超出范围 [0, $chunk_count)");
            }
            if (!array_key_exists('content_b64', $req) || $req['content_b64'] === null) {
                return fail($E['INVALID_PARAM'], 'missing_content_b64',
                    "Required field 'content_b64' is missing (received fields: " . _brief_keys($req) . ")", [
                        'hint' => 'put 必须带该分片的 base64 内容; 空分片不可能出现在合法上传中。',
                    ]);
            }
            if (!is_string($req['content_b64'])) {
                return fail($E['INVALID_PARAM'], 'content_b64_not_string',
                    'content_b64 must be a base64 string, got ' . gettype($req['content_b64']), [
                        'hint' => 'content_b64 必须是字符串; 二进制先做 base64 编码。',
                    ]);
            }
            $data = base64_decode($req['content_b64'], true);
            if ($data === false) return fail($E['INVALID_PARAM'], 'bad_base64', 'chunk decode failed');
            // 修复: 单 chunk 大小校验 (末片允许 <= chunk_size, 其他必须 == chunk_size)
            $data_len = strlen($data);
            $is_last = ($idx === $chunk_count - 1);
            $expected_max = $is_last ? $meta['chunk_size'] : $meta['chunk_size'];
            if ($data_len > $expected_max) {
                return fail($E['INVALID_PARAM'], 'chunk_too_large', "chunk $idx size $data_len > chunk_size {$meta['chunk_size']}");
            }
            if (!$is_last && $data_len !== $meta['chunk_size']) {
                return fail($E['INVALID_PARAM'], 'chunk_size_mismatch', "非末片 chunk $idx size $data_len != chunk_size {$meta['chunk_size']}");
            }
            $chunk_md5 = md5($data);
            $req_md5 = $req['md5'] ?? $req['chunk_md5'] ?? '';
            if (!empty($req_md5) && $req_md5 !== $chunk_md5) {
                return fail($E['CHECKSUM_MISMATCH'], 'chunk_md5_mismatch', "Chunk $idx md5 mismatch");
            }
            $chunk_file = $chunk_dir . $uid . '/' . sprintf('%05d', $idx);
            if (file_put_contents($chunk_file, $data, LOCK_EX) === false) {
                return fail($E['UPLOAD_FAILED'], 'chunk_write_fail', "Cannot write chunk $idx");
            }
            return ok(['upload_id' => $uid, 'chunk_index' => $idx, 'md5' => $chunk_md5, 'size' => $data_len]);
        }

        if ($op === 'assemble') {
            // 修复: 并发锁, 防同 upload_id 多客户端同时 assemble 损坏文件
            $meta_fp = @fopen($meta_file, 'r');
            if (!$meta_fp) return fail($E['INTERNAL'], 'meta_lock_fail', 'Cannot lock meta');
            if (!flock($meta_fp, LOCK_EX | LOCK_NB)) {
                // 已有其他 assemble 在进行
                fclose($meta_fp);
                return fail($E['INVALID_PARAM'], 'assemble_in_progress', '该 upload_id 正在被其他请求 assemble, 请稍后重试或查 status');
            }
            try {
                $rel = $meta['path'];
                // 重新按 nofollow 解析: init 时保存的绝对路径可能因父目录改名/链接替换而失效,
                // 且必须以"链接本身"为落点 (旧实现复用 safe_path 的结果会写穿目标).
                $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
                if (!$dest) return bad_path($rel);
                // init 之后目标可能被其他请求改成了目录 (TOCTOU): 这里再确认一次,
                // 否则 rename(tmp, dir) 只会报笼统的 rename_fail。
                if (is_dir($dest) && !is_link($dest)) {
                    return fail($E['INVALID_PARAM'], 'is_dir',
                        "Path '$rel' 现在是目录, 无法写入文件", [
                            'hint' => '目标路径在 init 之后被改成了目录; 请换文件名或先移除该目录。',
                        ]);
                }
                // 特殊文件 (FIFO/socket/device) 不能作为组装落点: rename 覆盖语义不明,
                // 且 mode 回退会 lstat 特殊节点. 明确拒绝, 让调用方先 delete.
                if (!is_link($dest) && ($sk = _special_kind($dest)) !== null) {
                    return fail($E['INVALID_PARAM'], 'dest_is_special',
                        "目标 '$rel' 已存在且是特殊文件 ($sk), 拒绝直接覆盖", [
                            'path' => $rel, 'special_type' => $sk,
                            'hint' => '先 delete 删除该节点, 再重新 init/assemble 上传.',
                        ]);
                }
                $total = $meta['total_size'];
                $chunk_size = $meta['chunk_size'];

                $missing = [];
                for ($i = 0; $i < $chunk_count; $i++) {
                    if (!file_exists($chunk_dir . $uid . '/' . sprintf('%05d', $i))) $missing[] = $i;
                }
                if (!empty($missing)) {
                    return fail($E['CHUNK_MISSING'], 'chunks_missing', 'Missing ' . count($missing) . ' chunks', [
                        'missing' => $missing,
                    ]);
                }
                $backed_up = '';
                if ((is_link($dest) || file_exists($dest)) && $cfg['BACKUP_KEEP'] > 0) {
                    $backed_up = is_link($dest)
                        ? _create_link_backup($dest, $rel, $cfg)
                        : _create_backup($dest, $rel, $cfg);
                }
                @mkdir(dirname($dest), 0755, true);
                // 修复: 先写临时文件, 校验通过后 rename (原子), 校验失败原文件从未被动过
                //       原版 fopen($dest, 'wb') 立即截断原文件为 0, 进程崩溃时原文件已毁
                $tmp_dest = $dest . '.chunk_tmp.' . bin2hex(random_bytes(4));
                $out = fopen($tmp_dest, 'wb');
                if (!$out) return fail($E['UPLOAD_FAILED'], 'open_fail', 'Cannot open temp destination');
                $write_ok = true;
                for ($i = 0; $i < $chunk_count; $i++) {
                    $in = fopen($chunk_dir . $uid . '/' . sprintf('%05d', $i), 'rb');
                    // 修复: fopen 失败检查 (原版未检查, stream_copy_to_stream(false, $out) 产残缺文件)
                    if (!$in) {
                        fclose($out);
                        @unlink($tmp_dest);
                        $write_ok = false;
                        return fail($E['INTERNAL'], 'chunk_read_fail', "Cannot read chunk $i");
                    }
                    stream_copy_to_stream($in, $out);
                    fclose($in);
                }
                fclose($out);
                // mode 省略时保留目标现有权限; 新文件默认 0644 (与 upload 行为一致)
                $mode_meta = $meta['mode'] ?? null;
                if ($mode_meta === null || $mode_meta === '') {
                    $final_mode = (!is_link($dest) && file_exists($dest))
                        ? (fileperms($dest) & 0777) : 0644;
                } else {
                    $final_mode = safe_mode($mode_meta);
                }
                if ($final_mode <= 0) $final_mode = 0644;
                @chmod($tmp_dest, $final_mode);
                $actual_size = filesize($tmp_dest);
                $actual_md5 = md5_file($tmp_dest);
                $final_md5 = $req['md5'] ?? $req['final_md5'] ?? '';
                $sha256_expected = $meta['sha256'] ?? '';
                // 修复: 校验失败的 message 区分 restored/deleted
                if (!empty($final_md5) && $final_md5 !== $actual_md5) {
                    @unlink($tmp_dest); // 临时文件直接删, 原文件未动
                    $action = $backed_up ? 'kept_original' : 'no_original';
                    audit_log('chunked_upload', array_merge($req, ['op' => 'assemble']), $E['CHECKSUM_MISMATCH'], [
                        'upload_id' => $uid, 'path' => $rel, 'reason' => 'md5_mismatch',
                        'expected' => $final_md5, 'actual' => $actual_md5,
                    ]);
                    return fail($E['CHECKSUM_MISMATCH'], 'md5_mismatch', "Final MD5 mismatch. 原文件未受影响 ($action)", [
                        'expected_md5' => $final_md5, 'actual_md5' => $actual_md5,
                    ]);
                }
                if ($actual_size !== $total) {
                    @unlink($tmp_dest);
                    $action = $backed_up ? 'kept_original' : 'no_original';
                    audit_log('chunked_upload', array_merge($req, ['op' => 'assemble']), $E['CHECKSUM_MISMATCH'], [
                        'upload_id' => $uid, 'path' => $rel, 'reason' => 'size_mismatch',
                        'expected' => $total, 'actual' => $actual_size,
                    ]);
                    return fail($E['CHECKSUM_MISMATCH'], 'size_mismatch', "Size $actual_size != $total. 原文件未受影响 ($action)");
                }
                // 修复: 若 init 时传了 sha256, assemble 必须校验 (原版存了死字段从不校验)
                if ($sha256_expected !== '' && function_exists('hash_file')) {
                    $actual_sha256 = hash_file('sha256', $tmp_dest);
                    if ($actual_sha256 !== $sha256_expected) {
                        @unlink($tmp_dest);
                        audit_log('chunked_upload', array_merge($req, ['op' => 'assemble']), $E['CHECKSUM_MISMATCH'], [
                            'upload_id' => $uid, 'path' => $rel, 'reason' => 'sha256_mismatch',
                        ]);
                        return fail($E['CHECKSUM_MISMATCH'], 'sha256_mismatch', "SHA256 mismatch. 原文件未受影响", [
                            'expected_sha256' => $sha256_expected, 'actual_sha256' => $actual_sha256,
                        ]);
                    }
                }
                // 原子 rename 到目标
                if (!@rename($tmp_dest, $dest)) {
                    @unlink($tmp_dest);
                    return fail($E['UPLOAD_FAILED'], 'rename_fail', 'Cannot move temp file to destination');
                }
                if (isset($meta['mtime']) && $meta['mtime'] !== null) {
                    @touch($dest, (int)$meta['mtime']);
                }
                _cleanup_chunks($uid, $chunk_dir);
                @unlink($meta_file);
                _metrics_inc($cfg, 'chunked_upload', 0);
                audit_log('chunked_upload', array_merge($req, ['op' => 'assemble']), 0, [
                    'upload_id' => $uid, 'path' => $rel, 'size' => $actual_size, 'md5' => $actual_md5, 'backed_up' => $backed_up ?: null,
                ]);
                return ok([
                    'upload_id' => $uid,
                    'path'  => $rel,
                    'size'  => $actual_size,
                    'md5'   => $actual_md5,
                    'mode'  => _mode4($final_mode),
                    'mtime' => filemtime($dest),
                    'backed_up' => $backed_up,
                ]);
            } finally {
                @flock($meta_fp, LOCK_UN);
                @fclose($meta_fp);
            }
        }

        if ($op === 'status') {
            $received = [];
            $missing = [];
            for ($i = 0; $i < $chunk_count; $i++) {
                if (file_exists($chunk_dir . $uid . '/' . sprintf('%05d', $i))) $received[] = $i;
                else $missing[] = $i;
            }
            // 修复: 返回 state 和 created_at, 让 AI 判断是否已 assembled/abandoned
            return ok([
                'upload_id' => $uid,
                'path'      => $meta['path'],
                'total_size'=> $meta['total_size'],
                'received'  => $received,
                'missing'   => $missing,
                'progress'  => $chunk_count > 0 ? round(count($received) / $chunk_count * 100, 1) : 0,
                'state'     => 'uploading',
                'created_at' => $meta['created_at'] ?? 0,
                'ttl_seconds' => max(0, intval($cfg['CHUNK_TTL_DAYS'] ?? 7) * 86400 - (time() - intval($meta['created_at'] ?? 0))),
            ]);
        }

        if ($op === 'abort') {
            // 修复: 新增 abort, AI 可主动取消上传并清理分片 (原版无此能力, 孤儿分片永久残留)
            _cleanup_chunks($uid, $chunk_dir);
            @unlink($meta_file);
            audit_log('chunked_upload', array_merge($req, ['op' => 'abort']), 0, [
                'upload_id' => $uid, 'path' => $meta['path'] ?? '',
            ]);
            return ok(['aborted' => true, 'upload_id' => $uid]);
        }
    }

    return fail($E['INVALID_PARAM'], 'bad_op', "op must be init/put/assemble/status/abort, got '$op'");
}

function _cleanup_chunks($uid, $chunk_dir) {
    $dir = $chunk_dir . $uid;
    if (is_dir($dir)) {
        foreach (glob($dir . '/*') ?: [] as $f) @unlink($f);
        @rmdir($dir);
    }
}
