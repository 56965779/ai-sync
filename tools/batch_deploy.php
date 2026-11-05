<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v4.3 Tool: Batch Deploy (原子批量部署)
 *
 * 功能:
 *   1. 接收多文件, 全部先写到 .ai-deploy-tmp/ 临时目录
 *   2. 执行 pre_check (如 php -l 语法检查)
 *   3. 全部通过后, 备份旧文件 + 原子 rename 到正式位置
 *   4. 执行 post_check (如 HTTP 状态码检查)
 *   5. 任何步骤失败 → 已替换的文件从备份恢复, 返回详细错误
 *
 * 请求格式:
 * {
 *   "action": "batch_deploy",
 *   "token": "...",
 *   "scope": "...",
 *   "files": [
 *     {"path": "sync_api.php", "content_b64": "..."},
 *     {"path": "tools/file_crud.php", "content_b64": "..."}
 *   ],
 *   "pre_check": "php -l {file}",        // 可选, {file} 会替换为每个 PHP 文件路径
 *   "post_check": "curl -s -o /dev/null -w '%{http_code}' https://localhost/admin.html",  // 可选
 *   "post_check_expect": "200",          // 可选, post_check 输出包含此字符串才算通过
 *   "rollback_on_fail": true              // 默认 true
 * }
 */
require_once __DIR__ . '/link_common.php';


function action_batch_deploy($req, $cfg) {
    global $E, $CONFIG;

    if (!function_exists('_create_backup')) {
        $api_dir = dirname(__DIR__); // tools/ 的上一级 = sync_api.php 所在目录
        $candidates = [
            $api_dir . '/tools/backup.php',
            rtrim($cfg['WORK_DIR'], '/\\') . '/tools/backup.php',
        ];
        foreach ($candidates as $c) {
            if (file_exists($c)) { require_once $c; break; }
        }
    }
    // 修复 P1-2: post_check 需复用 webhook.php 的 SSRF 防护 (_webhook_validate_url / _webhook_resolve_public_ips)
    if (!function_exists('_webhook_validate_url')) {
        $api_dir = dirname(__DIR__);
        foreach ([$api_dir . '/tools/webhook.php', rtrim($cfg['WORK_DIR'], '/\\') . '/tools/webhook.php'] as $c) {
            if (file_exists($c)) { require_once $c; break; }
        }
    }

    // FIX-v4.34 (P0 双端不一致): 契约(HELP_SPEC)与示例声明的参数名是 **items**,
    //   但本机只认 files → AI 严格照 `help` 契约写 items= 会在 PHP 端 100% 失败
    //   (报 empty_files), 而同一请求在 PY 端成功 —— 这是最伤 AI 的一类缺陷:
    //   "照着文档写却跑不通, 错误信息还指向一个我没用过的字段名"。
    //   现统一以契约名 items 为主, files 保留为历史别名(客户端旧版本在用)。
    $raw_files = null;
    if (isset($req['items'])) {
        $raw_files = $req['items'];
    } elseif (isset($req['files'])) {
        $raw_files = $req['files'];
    }
    if ($raw_files === null) {
        return fail($E['INVALID_PARAM'], 'missing_items', 'batch_deploy 缺少必填参数',
            [
                'hint' => '必须传 items 数组 (每项 {path, content_b64}); files 为历史别名仍兼容。'
                        . '契约名与示例请以 help name=batch_deploy 为准。',
                'received_fields' => array_values(array_diff(
                    array_keys($req), ['token', 'scope', 'action'])),
            ]);
    }
    if (!is_array($raw_files)) {
        return fail($E['INVALID_PARAM'], 'bad_items', 'items 必须是数组 ({path, content_b64} 的列表)',
            ['hint' => '正确形态: {"items":[{"path":"a.php","content_b64":"PD9waHA="}]}']);
    }
    if (count($raw_files) === 0) {
        return fail($E['INVALID_PARAM'], 'empty_items', 'items 数组不能为空 (没有要部署的文件)',
            ['hint' => 'items 至少包含 1 项; 若只部署单个文件可用 upload action。']);
    }
    $files = $raw_files;
    // 单次部署文件数上限: 由 scope 配置 max_files_per_deploy 控制 (默认 50)。
    // 旧实现硬编码 50, 即使配置成 500 也不生效, whoami 还错误地报 50。
    $max_files = isset($cfg['MAX_FILES_PER_DEPLOY']) ? (int)$cfg['MAX_FILES_PER_DEPLOY'] : 50;
    if ($max_files < 1) $max_files = 50;
    if (count($files) > $max_files) {
        return fail($E['INVALID_PARAM'], 'too_many_files',
            '单次部署最多 ' . $max_files . ' 个文件, 收到 ' . count($files) . ' 个', [
                'limit' => $max_files,
                'received' => count($files),
                'hint' => '请拆分成多次 batch_deploy (每次 <= ' . $max_files . ' 个); '
                        . '上限由 scope 配置 max_files_per_deploy 决定, 可用 whoami 查看。',
            ]);
    }

    // --- Phase 0: 目标路径冲突预检 (零写入) ---
    // AI 常见错误: 同一文件在 items 里出现两次 (或 ./a、/a 等别名指向同一目标),
    //   或同时部署 "a" 与 "a/b" (前者是文件, 后者要求它是目录).
    //   旧实现: 重复路径静默 last-wins 且 PHP file_count=1 / Python=2 双端不一致;
    //   目录/根目标则拖到 Phase3 rename 失败才报 1099 phase3_replace_fail, 错误不可自愈.
    //   现在统一在任何实际写入之前返回 1011, 并列出冲突条目.
    $real_work_root = @realpath(rtrim($cfg['WORK_DIR'], '/\\'));
    $norm_work_root = ($real_work_root !== false) ? _lexical_norm_abs($real_work_root) : false;
    $seen_dest    = [];   // [纯词法归一化绝对路径 => 首个 rel]
    $dup_pairs    = [];
    $dir_targets  = [];
    $root_targets = [];
    $dot_targets  = [];
    foreach ($files as $i => $file) {
        if (!is_array($file)) continue;
        $rel = $file['path'] ?? '';
        if (!is_string($rel) || $rel === '') continue;
        $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
        if (!$dest) continue; // 真正原因 (含审计) 由 Phase1 统一报告
        if (basename($dest) === '.') {
            // 形如 "dir/.": 词法上是目录, 不可能当文件写入
            $dot_targets[] = $rel;
            continue;
        }
        $dest_norm = _lexical_norm_abs($dest);
        if ($dest_norm === false) continue;
        if ($norm_work_root !== false && $dest_norm === $norm_work_root) {
            $root_targets[] = $rel;
            continue;
        }
        if (is_dir($dest) && !is_link($dest)) {
            $dir_targets[] = $rel;
            continue;
        }
        if (isset($seen_dest[$dest_norm])) {
            $dup_pairs[] = ['path' => $seen_dest[$dest_norm], 'duplicate' => $rel];
            continue;
        }
        $seen_dest[$dest_norm] = $rel;
    }
    if (!empty($root_targets)) {
        return fail($E['INVALID_PARAM'], 'root_deploy_not_allowed',
            '部署目标是 scope 工作目录根: ' . implode(', ', $root_targets), [
                'paths' => $root_targets,
                'hint'  => 'batch_deploy 的 path 必须指向 work_dir 内的文件; "." "/" "./" 都指向工作目录本身, 不能作为部署目标。',
            ]);
    }
    if (!empty($dot_targets)) {
        return fail($E['INVALID_PARAM'], 'bad_dest_path',
            '部署目标必须以文件名结尾, 不能是目录路径: ' . implode(', ', $dot_targets), [
                'paths' => $dot_targets,
                'hint'  => '形如 "dir/." 的 path 等价于目录 dir 本身; 请给出具体文件名, 如 "dir/app.php"。',
            ]);
    }
    if (!empty($dir_targets)) {
        return fail($E['INVALID_PARAM'], 'dest_is_dir',
            '部署目标是已存在的目录, 不能作为文件覆盖: ' . implode(', ', $dir_targets), [
                'paths' => $dir_targets,
                'hint'  => 'path 必须指向文件; 要写入目录内请带上文件名 (如 "conf/app.json"); 删除/重命名目录请用 delete/rename。',
            ]);
    }
    if (!empty($dup_pairs)) {
        return fail($E['INVALID_PARAM'], 'duplicate_path',
            '同一批次中多个条目指向同一目标路径', [
                'duplicates' => $dup_pairs,
                'hint'       => '每个目标路径在单次 batch_deploy 中只能出现一次; "./a"、"a"、"/a" 等别名视为同一路径。请合并成一条后重试。',
            ]);
    }
    // 目标互为父子: 一个要当文件写, 另一个却要求它是目录, 无法同时成立.
    $conflict_pairs = [];
    $dest_norm_keys = array_keys($seen_dest);
    $dn = count($dest_norm_keys);
    for ($i = 0; $i < $dn; $i++) {
        for ($j = $i + 1; $j < $dn; $j++) {
            $a = $dest_norm_keys[$i];
            $b = $dest_norm_keys[$j];
            if (strpos($a . '/', $b . '/') === 0 || strpos($b . '/', $a . '/') === 0) {
                $conflict_pairs[] = ['path' => $seen_dest[$a], 'conflicts_with' => $seen_dest[$b]];
            }
        }
    }
    if (!empty($conflict_pairs)) {
        return fail($E['INVALID_PARAM'], 'conflicting_paths',
            '同一批次中的路径互相冲突: 一个条目是文件, 另一个条目又需要它是目录', [
                'conflicts' => $conflict_pairs,
                'hint'      => '例如同时部署 "a" 和 "a/b.php" 无法同时成立; 请改用不冲突的路径, 或拆成两次部署并先建好目录结构。',
            ]);
    }

    $pre_check     = $req['pre_check'] ?? '';
    $post_check    = $req['post_check'] ?? '';
    $post_expect   = $req['post_check_expect'] ?? '';
    $rollback      = bool_flag($req['rollback_on_fail'] ?? true);
    if ($rollback === null) return bad_bool('rollback_on_fail', $req['rollback_on_fail']);
    $work_dir      = rtrim($cfg['WORK_DIR'], '/\\');
    $tmp_dir       = $work_dir . '/.ai-deploy-tmp';
    $deploy_id     = date('Ymd_His') . '_' . substr(md5(json_encode(array_column($files, 'path')) . microtime()), 0, 8);

    // --- Phase 1: 全部文件写入临时目录 ---
    $staged = []; // [rel => tmp_path]
    $phase1_errors = [];

    @mkdir($tmp_dir, 0755, true);

    // 修复 (P0 RCE): 临时目录路径基于 safe_path 校验过的 $dest 构建, 而非用户传入的 $rel.
    //   原版 $tmp_full = $tmp_dir . '/' . $deploy_id . '/' . $rel, 若 $rel 含 '../../'
    //   可写到 tmp_dir 之外 (如 /etc/cron.d/evil), pre_check 的 php -l 会执行任意 PHP → RCE.
    $work_base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
    foreach ($files as $i => $file) {
        if (!is_array($file)) {
            $phase1_errors[] = "文件 #{$i}: 每项必须是对象 {path, content_b64}";
            continue;
        }
        $rel = $file['path'] ?? '';
        if (!is_string($rel) || $rel === '') {
            $phase1_errors[] = "文件 #{$i}: path 为空或不是字符串";
            continue;
        }
        // Phase 1 即校验最终目标路径合法, 非法路径根本不写临时文件.
        // 不跟随最后一级: 目标是链接时部署要替换链接本身, 而不是写穿到目标.
        $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
        if (!$dest) {
            reject_path($rel, 'batch_deploy_stage', array_merge($req, ['deploy_id' => $deploy_id]));
            $phase1_errors[] = "{$rel}: 路径安全策略拦截 (path_traversal)";
            continue;
        }
        if (!array_key_exists('content_b64', $file) || $file['content_b64'] === null) {
            $phase1_errors[] = "{$rel}: 缺少 content_b64 (收到字段: " . _brief_keys($file) . ")";
            continue;
        }
        if (!is_string($file['content_b64'])) {
            $phase1_errors[] = "{$rel}: content_b64 必须是 base64 字符串";
            continue;
        }
        $data = base64_decode($file['content_b64'], true);
        if ($data === false) {
            $phase1_errors[] = "{$rel}: base64 解码失败";
            continue;
        }
        if (strlen($data) > $cfg['MAX_SIZE']) {
            $phase1_errors[] = "{$rel}: 文件过大 (" . strlen($data) . " > MAX_SIZE)";
            continue;
        }
        // 基于 $dest (已在 WORK_DIR 内) 反推相对路径, 拼到 tmp_dir 下 → tmp 一定在 tmp_dir 内
        $rel_norm = substr($dest, strlen($work_base));
        $rel_norm = ltrim(str_replace('\\', '/', $rel_norm), '/');
        // P0-FIX (FPM C locale): 临时文件用 ASCII 名 (序号+扩展名). FPM exec 子进程
        //   (php -l 等) 在纯 C locale 下无法处理非 ASCII argv (中文被剥成 '_.php' → pre_check 失败).
        //   正式文件名在 Phase3 rename 时恢复为 $dest (rename 是字节操作, 不受 locale 影响).
        $tmp_ext = strtolower(pathinfo($rel_norm, PATHINFO_EXTENSION));
        $tmp_full = $tmp_dir . '/' . $deploy_id . '/' . sprintf('%03d', count($staged) + 1) . ($tmp_ext !== '' ? '.' . $tmp_ext : '');
        // 物理层铁闸: 确保临时文件父目录 realpath 在 tmp_dir 内 (防符号链接逃逸)
        $tmp_parent = dirname($tmp_full);
        @mkdir($tmp_parent, 0755, true);
        $real_tmp_dir = realpath($tmp_dir);
        $real_tmp_parent = realpath($tmp_parent);
        if ($real_tmp_dir === false || $real_tmp_parent === false
            || strpos($real_tmp_parent . DIRECTORY_SEPARATOR, $real_tmp_dir . DIRECTORY_SEPARATOR) !== 0) {
            $phase1_errors[] = "{$rel}: 临时目录路径逃逸拦截";
            continue;
        }

        if (file_put_contents($tmp_full, $data, LOCK_EX) === false) {
            $phase1_errors[] = "{$rel}: 临时文件写入失败";
            continue;
        }
        // mode 省略/null 时保留目标现有权限 (Phase 3 决定); 新文件默认 0644.
        $mode_raw = $file['mode'] ?? null;
        $has_mode = !($mode_raw === null || $mode_raw === '');
        if ($has_mode && !valid_mode_input($mode_raw)) {
            $phase1_errors[] = "{$rel}: mode 非法 (需 1-4 位八进制字符串, 如 0644)";
            continue;
        }
        $mode = $has_mode ? safe_mode($mode_raw) : null;
        if ($mode !== null) @chmod($tmp_full, $mode);
        // 可选 mtime: Phase 3 rename 后恢复, 让部署文件保留本地修改时间
        $file_mtime = null;
        $mtime_req = $file['mtime'] ?? null;
        if ($mtime_req !== null && $mtime_req !== '') {
            if (!valid_mtime_input($mtime_req)) {
                $phase1_errors[] = "{$rel}: mtime 非法 (需秒级 unix 整数时间戳)";
                continue;
            }
            $file_mtime = (int)$mtime_req;
        }
        // 修复: 把 mode / dest 一并存入 staged, 避免 Phase 3 再用脆弱的 array_search 反查
        $staged[$rel] = ['tmp' => $tmp_full, 'dest' => $dest, 'data' => $data, 'size' => strlen($data), 'mode' => $mode, 'mtime' => $file_mtime];
    }

    if (!empty($phase1_errors)) {
        _cleanup_tmp_dir($tmp_dir, $deploy_id);
        audit_log('batch_deploy', $req, $E['UPLOAD_FAILED'], ['phase' => 'phase1', 'deploy_id' => $deploy_id, 'errors' => $phase1_errors]);
        return fail($E['UPLOAD_FAILED'], 'phase1_write_fail', '部分文件写入临时目录失败', [
            'errors'    => $phase1_errors,
            'deploy_id' => $deploy_id,
            'staged'    => count($staged),
        ]);
    }

    // --- Phase 2: pre_check (语法检查等) ---
    // 安全修复: pre_check 仅接受枚举 "php_lint" (服务端翻译为固定命令), 杜绝命令注入.
    // 兼容旧字符串 "php -l {file}" (严格匹配后转换). 其他值一律拒绝.
    $pre_results = [];
    if ($pre_check) {
        $check_type = ($pre_check === 'php -l {file}') ? 'php_lint' : $pre_check;
        if ($check_type !== 'php_lint') {
            _cleanup_tmp_dir($tmp_dir, $deploy_id);
            return fail($E['INVALID_PARAM'], 'bad_pre_check',
                "pre_check 仅支持 'php_lint' 或空 (旧 'php -l {file}' 兼容). 拒绝执行任意命令, 防止注入.",
                ['received' => $pre_check]);
        }
        $php_bin = function_exists('_find_php_cli') ? _find_php_cli() : (trim(@exec('command -v php 2>/dev/null')) ?: '/usr/bin/php');
        foreach ($staged as $rel => $info) {
            if (strtolower(pathinfo($rel, PATHINFO_EXTENSION)) !== 'php') {
                $pre_results[$rel] = ['exit_code' => 0, 'output' => '(非 PHP 文件, 跳过)', 'passed' => true];
                continue;
            }
            $check_cmd = escapeshellarg($php_bin) . ' -l ' . escapeshellarg($info['tmp']);
            $output = [];
            $rc = 0;
            @exec($check_cmd . ' 2>&1', $output, $rc);
            $pre_results[$rel] = [
                'cmd'        => 'php -l {file}',
                'exit_code'  => $rc,
                'output'     => implode("\n", $output),
                'passed'     => ($rc === 0),
            ];
        }
        $failed_checks = array_filter($pre_results, function ($r) { return !$r['passed']; });
        if (!empty($failed_checks)) {
            _cleanup_tmp_dir($tmp_dir, $deploy_id);
            audit_log('batch_deploy', $req, $E['INVALID_PARAM'], ['phase' => 'pre_check', 'deploy_id' => $deploy_id, 'failed' => array_keys($failed_checks)]);
            return fail($E['INVALID_PARAM'], 'pre_check_failed', 'pre_check 未通过, 已中止部署 (文件未变更)', [
                'deploy_id'   => $deploy_id,
                'pre_check'  => $pre_check,
                'results'    => $pre_results,
                'failed'      => array_keys($failed_checks),
            ]);
        }
    }

    // --- Phase 3: 备份旧文件 + 原子替换 ---
    $backed_up = [];   // [rel => backup_filename]
    $replaced = [];     // [rel => dest_path]
    $replace_errors = [];

    $real_work = @realpath(rtrim($cfg['WORK_DIR'], '/\\'));
    $real_work_p = ($real_work === false) ? '' : rtrim($real_work, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR;
    $in_work = function ($p) use ($real_work_p) {
        return $p !== false && $p !== '' && strpos(rtrim($p, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR, $real_work_p) === 0;
    };

    foreach ($staged as $rel => $info) {
        // Phase 3 重新按 nofollow 解析: Phase 1 的绝对路径可能因父目录改名/链接替换而失效.
        $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
        if (!$dest) {
            reject_path($rel, 'batch_deploy_replace', array_merge($req, ['deploy_id' => $deploy_id]));
            $replace_errors[] = "{$rel}: 路径安全策略拦截 (path_traversal)";
            continue;
        }
        if ($real_work === false) {
            reject_path($rel, 'batch_deploy_replace', array_merge($req, ['deploy_id' => $deploy_id]));
            $replace_errors[] = "{$rel}: 路径安全策略拦截 (work_dir 无法解析)";
            continue;
        }
        // Phase1 已预检; 这里防 Phase1→Phase3 期间目标被并发改成目录/根 (TOCTOU).
        $dest_norm = _lexical_norm_abs($dest);
        if ($dest_norm !== false && $norm_work_root !== false && $dest_norm === $norm_work_root) {
            $replace_errors[] = "{$rel}: 目标是 work_dir 根, 拒绝作为文件写入";
            continue;
        }
        if (is_dir($dest) && !is_link($dest)) {
            $replace_errors[] = "{$rel}: 目标是目录, 不能作为文件覆盖";
            continue;
        }
        // FIX-v4.31 (P0): 目标父目录可能尚不存在 —— AI 首次部署新目录/新项目 (brand_new 场景) 是常态.
        //   旧逻辑直接 realpath(dirname($dest)), 目录不存在时返回 false, 被误判为"路径逃逸"并 100% 拦截,
        //   且错误信息无法自愈 (AI 只能盲试). 现改为与 safe_path 一致的三级校验:
        //   ① 向上找最近已存在祖先并校验 (此刻零写入, 越界即拦, 无副作用)
        //   ② 祖先合法 → 创建缺失的中间目录
        //   ③ 目录落定后终校一次 (防 Phase 1 之后中间层被替换成符号链接)
        $dest_parent = dirname($dest);
        $ancestor = $dest_parent;
        $real_ancestor = false;
        while (true) {
            $real_ancestor = @realpath($ancestor);
            if ($real_ancestor !== false) break;
            $up = dirname($ancestor);
            if ($up === $ancestor || strlen($up) < strlen($real_work)) break; // 已到文件系统根
            $ancestor = $up;
        }
        if (!$in_work($real_ancestor)) {
            reject_path($rel, 'batch_deploy_replace', array_merge($req, ['deploy_id' => $deploy_id]));
            $replace_errors[] = "{$rel}: 路径安全策略拦截 (目标父目录 {$dest_parent} 解析后落在 work_dir 之外)";
            continue;
        }
        if (!is_dir($dest_parent) && !@mkdir($dest_parent, 0755, true)) {
            $replace_errors[] = "{$rel}: 目标目录创建失败 (权限不足或同名文件冲突): {$dest_parent}";
            continue;
        }
        // ③ 终校: 防止 Phase 1 之后 WORK_DIR/中间层被符号链接替换
        $real_dest_parent = @realpath($dest_parent);
        if (!$in_work($real_dest_parent)) {
            reject_path($rel, 'batch_deploy_replace', array_merge($req, ['deploy_id' => $deploy_id]));
            $replace_errors[] = "{$rel}: 路径安全策略拦截 (父目录创建后落在 work_dir 之外, 疑似符号链接逃逸)";
            continue;
        }

        // 特殊文件 (FIFO/socket/device) 拒绝部署覆盖: 无法备份/读内容,
        // rename 覆盖语义也不明确. 与 upload/upload_batch 保持一致, 先 delete 再部署.
        if (!is_link($dest) && ($sk = _special_kind($dest)) !== null) {
            $replace_errors[] = "{$rel}: 目标是特殊文件 ($sk), 拒绝覆盖";
            continue;
        }
        // 备份旧对象: 链接记 raw target, 普通文件复制内容.
        $old_dest_mode = (!is_link($dest) && file_exists($dest))
            ? (fileperms($dest) & 0777) : null;
        if ((is_link($dest) || file_exists($dest)) && $cfg['BACKUP_KEEP'] > 0) {
            $bak = is_link($dest)
                ? _create_link_backup($dest, $rel, $cfg)
                : _create_backup($dest, $rel, $cfg);
            if ($bak !== '') $backed_up[$rel] = $bak;
        }

        // 原子替换: rename tmp → dest
        if (!@rename($info['tmp'], $dest)) {
            // rename 失败 (可能跨设备), 回退到 同目录临时文件 + rename (仍保持原子)
            $side_tmp = dirname($dest) . '/' . basename($dest) . '.deploy_swap.' . bin2hex(random_bytes(4));
            if (@copy($info['tmp'], $side_tmp) && @rename($side_tmp, $dest)) {
                @unlink($info['tmp']);
            } else {
                @unlink($side_tmp);
                $replace_errors[] = "{$rel}: 文件替换失败";
                continue;
            }
        }
        // mode 省略: 保留被替换文件的原权限; 新文件 0644
        $final_mode = $info['mode'] !== null
            ? $info['mode']
            : ($old_dest_mode !== null ? $old_dest_mode : 0644);
        if ($final_mode <= 0) $final_mode = 0644;
        @chmod($dest, $final_mode);
        if (isset($info['mtime']) && $info['mtime'] !== null) @touch($dest, (int)$info['mtime']);
        $replaced[$rel] = $dest;
    }

    if (!empty($replace_errors)) {
        // 部分替换失败, 如果开启了回滚则恢复已替换的文件
        $rollback_results = [];
        if ($rollback) {
            foreach ($replaced as $rel => $dest) {
                $rb = _rollback_from_backup($rel, $dest, $cfg);
                $rollback_results[$rel] = $rb;
            }
        }
        _cleanup_tmp_dir($tmp_dir, $deploy_id);
        audit_log('batch_deploy', $req, $E['INTERNAL'], ['phase' => 'phase3', 'deploy_id' => $deploy_id, 'errors' => $replace_errors, 'rolled_back' => $rollback_results]);
        return fail($E['INTERNAL'], 'phase3_replace_fail', '部分文件替换失败' . ($rollback && !empty($replaced) ? ', 已回滚已替换文件' : ''), [
            'deploy_id'      => $deploy_id,
            'errors'         => $replace_errors,
            'replaced_count' => count($replaced),
            'rolled_back'    => $rollback_results,
        ]);
    }

    // --- Phase 4: post_check (HTTP 状态检查) ---
    // 安全修复 P1-2: post_check 复用 _webhook_validate_url 做 SSRF 防护 (内网 IP 过滤 + scheme 白名单),
    //   并加 DNS rebinding 防护 + 启用 SSL 证书校验. 原版仅校验 URL 格式, 可探内网 (169.254.169.254 等).
    $post_result = null;
    if ($post_check) {
        // AI 修复: post_check 支持两种形态
        //   A) http(s):// 开头 → URL 检查 (SSRF 防护, 原逻辑)
        //   B) 其他         → shell 命令 (需 exec 权限 + scope exec_enabled), 输出匹配 post_expect 即通过
        $post_is_shell = (preg_match('#^https?://#i', trim($post_check)) !== 1);
        if ($post_is_shell) {
            if (empty($cfg['EXEC_ENABLED']) || !in_array('exec', $cfg['PERMISSIONS'] ?? [], true)) {
                _cleanup_tmp_dir($tmp_dir, $deploy_id);
                audit_log('batch_deploy', $req, $E['INVALID_PARAM'], ['phase' => 'post_check', 'reason' => 'no_exec_perm']);
                return fail($E['INVALID_PARAM'], 'bad_post_check',
                    "post_check shell 命令需要 exec 权限且 scope 开启 exec_enabled",
                    ['hint' => 'HTTP(S) URL 检查无需 exec 权限; shell 命令需 exec 权限']);
            }
            // AI 修复: exec() 拿真实退出码 (shell_exec 拿不到 rc, 有输出即误判通过);
            // 且 cd 到 WORK_DIR 使相对路径与部署路径语义一致
            $post_out = '';
            $post_rc = -1;
            $_post_lines = [];
            exec('cd ' . escapeshellarg($cfg['WORK_DIR']) . ' && timeout -k 3 -s TERM 15 sh -c ' . escapeshellarg($post_check) . ' 2>&1', $_post_lines, $post_rc);
            $post_out = implode("\n", (array)$_post_lines);
            if ($post_expect !== '') {
                $post_passed = (strpos((string)$post_out, $post_expect) !== false);
            } else {
                $post_passed = ($post_rc === 0);
            }
            $post_result = [
                'url'     => 'shell',
                'rc'      => $post_rc,
                'output'  => trim((string)$post_out),
                'passed'  => $post_passed,
            ];
        } else {
            $post_url = $post_check;
            if (function_exists('_webhook_validate_url')) {
                list($uok, $uerr) = _webhook_validate_url($post_url);
                if (!$uok) {
                    _cleanup_tmp_dir($tmp_dir, $deploy_id);
                    audit_log('batch_deploy', $req, $E['INVALID_PARAM'], ['phase' => 'post_check', 'reason' => 'bad_url']);
                    return fail($E['INVALID_PARAM'], 'bad_post_check',
                        "post_check URL 被安全策略拒绝: $uerr",
                        ['received' => $post_check, 'hint' => 'post_check 仅允许公网 http/https URL, 防 SSRF']);
                }
            } else {
                // 回退: 至少校验 scheme (webhook.php 加载失败时)
                if (!filter_var($post_url, FILTER_VALIDATE_URL)
                    || !in_array(strtolower(parse_url($post_url, PHP_URL_SCHEME) ?: ''), ['http', 'https'])) {
                    _cleanup_tmp_dir($tmp_dir, $deploy_id);
                    return fail($E['INVALID_PARAM'], 'bad_post_check',
                        "post_check 仅支持 http/https URL.",
                        ['received' => $post_check]);
                }
            }
            // post_check: 有 curl 用 curl(可锁定已校验的 IP); 无 curl 退到 stream HEAD
            $_resolve = [];
            if (function_exists('_webhook_resolve_public_ips')) {
                $_hk = parse_url($post_url);
                if (!empty($_hk['host'])) {
                    $_hname = $_hk['host'];
                    $_hport = $_hk['port'] ?? (($_hk['scheme'] ?? '') === 'https' ? 443 : 80);
                    foreach (_webhook_resolve_public_ips($_hname) as $_hip) {
                        $_resolve[] = "$_hname:$_hport:$_hip";
                        break;
                    }
                }
            }
            $_probe = _http_request_simple($post_url, [
                'method' => 'HEAD', 'timeout' => 10, 'connect_timeout' => 5, 'resolve' => $_resolve,
            ]);
            $http_code = (int)$_probe['http_code'];
            $curl_err  = $_probe['error'];
            $post_transport = $_probe['transport'];
            $post_output = $curl_err ? ($post_transport . "_error: " . $curl_err) : ("HTTP " . $http_code);
            if ($post_expect !== '') {
                $post_passed = (strpos((string)$http_code, $post_expect) !== false);
            } else {
                $post_passed = ($http_code >= 200 && $http_code < 400);
            }
            $post_result = [
                'url'       => $post_url,
                'http_code' => $http_code,
                'output'    => $post_output,
                'passed'    => $post_passed,
            ];
        }

        if (!$post_passed && $rollback) {
            // post_check 失败, 回滚所有文件
            $rollback_results = [];
            foreach ($replaced as $rel => $dest) {
                $rb = _rollback_from_backup($rel, $dest, $cfg);
                $rollback_results[$rel] = $rb;
            }
            _cleanup_tmp_dir($tmp_dir, $deploy_id);
            audit_log('batch_deploy', $req, $E['INTERNAL'], ['phase' => 'post_check_failed', 'deploy_id' => $deploy_id, 'post_check' => $post_result]);
            return fail($E['INTERNAL'], 'post_check_failed', 'post_check 未通过, 已回滚全部文件', [
                'deploy_id'   => $deploy_id,
                'post_check'  => $post_result,
                'rolled_back' => $rollback_results,
            ]);
        }
    }

    // --- Phase 5: 清理临时目录, 返回成功 ---
    _cleanup_tmp_dir($tmp_dir, $deploy_id);

    _metrics_inc($cfg, 'upload_batch', 0);
    audit_log('batch_deploy', $req, 0, [
        'deploy_id' => $deploy_id,
        'files'     => array_keys($staged),
    ]);

    $result = [
        'deploy_id'   => $deploy_id,
        'files'       => array_keys($staged),
        'file_count'  => count($staged),
        'backed_up'   => $backed_up,
        'pre_check'   => $pre_results ?: null,
        'post_check'  => $post_result,
        'sizes'       => array_map(function ($info) { return $info['size']; }, $staged),
        'rolled_back' => false,
    ];
    return ok($result);
}

/**
 * 从备份回滚单个文件
 */
function _rollback_from_backup($rel, $dest, $cfg) {
    $backup_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['BACKUP_DIR'] . '/' . _utf8_dirname($rel) . '/' . _utf8_basename($rel);
    if (!is_dir($backup_dir)) {
        // 新建文件无备份 → 删除新建对象恢复到"不存在"状态 (链接也要按链接删除)
        if (is_link($dest) || file_exists($dest)) { @unlink($dest); }
        return ['ok' => true, 'restored_from' => 'deleted_new_file'];
    }
    $candidates = glob($backup_dir . '/' . _utf8_basename($rel) . '.bak.*') ?: [];
    if (empty($candidates)) {
        if (is_link($dest) || file_exists($dest)) { @unlink($dest); }
        return ['ok' => true, 'restored_from' => 'deleted_new_file'];
    }
    // 取最新备份, 按备份类型恢复 (链接→链接, 文件→文件)
    usort($candidates, function ($a, $b) { return filemtime($b) - filemtime($a); });
    $best = $candidates[0];
    $rtype = _restore_backup_to_dest($best, $dest, $cfg);
    if ($rtype !== false) {
        return ['ok' => true, 'restored_from' => _utf8_basename($best), 'type' => $rtype];
    }
    return ['ok' => false, 'error' => 'restore_fail'];
}

/**
 * 清理临时目录中的指定 deploy_id
 * 修复: 校验 deploy_id 格式 + realpath 边界, 防 $deploy_id='../' 删除任意目录
 */
function _cleanup_tmp_dir($tmp_dir, $deploy_id) {
    // deploy_id 格式: YYYYMMDD_HHMMSS_<8 hex>, 严格白名单
    if (!preg_match('/^\d{8}_\d{6}_[0-9a-f]{8}$/', (string)$deploy_id)) return;
    $target = $tmp_dir . '/' . $deploy_id;
    $real_tmp = @realpath($tmp_dir);
    $real_target = @realpath($target);
    // 边界校验: target 必须在 tmp_dir 内 (防符号链接逃逸)
    if ($real_tmp === false || $real_target === false) return;
    if (strpos($real_target . DIRECTORY_SEPARATOR, $real_tmp . DIRECTORY_SEPARATOR) !== 0) return;
    if (!is_dir($real_target)) return;
    $iti = new \RecursiveIteratorIterator(
        new \RecursiveDirectoryIterator($real_target, \RecursiveDirectoryIterator::SKIP_DOTS),
        \RecursiveIteratorIterator::CHILD_FIRST
    );
    foreach ($iti as $f) {
        // 修复: 仅处理 target 内的文件, 跳过符号链接 (防清理时误删外部文件)
        $fp = $f->getPathname();
        $real_fp = @realpath($fp);
        if ($real_fp !== false && strpos($real_fp . DIRECTORY_SEPARATOR, $real_target . DIRECTORY_SEPARATOR) !== 0) continue;
        if ($f->isLink()) { @unlink($fp); continue; }
        $f->isDir() ? @rmdir($fp) : @unlink($fp);
    }
    @rmdir($real_target);
}
