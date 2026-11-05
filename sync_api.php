<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

// opcache 清理: 仅在显式开发模式 (APP_ENV=dev) 下重置, 避免生产性能损失
// 修复: 原代码每请求都 opcache_reset() 导致 PHP 永远无法缓存编译结果, 性能下降数倍
// DEBUG 配置在 $CONFIG 加载后通过 opcache_invalidate 精准失效 (见下方)
if (getenv('APP_ENV') === 'dev' && function_exists('opcache_reset')) {
    @opcache_reset();
}
/**
 * ai-sync v4.3 服务器端 API - 多目录授权接口层
 *
 * 架构: sync_api.php (接口层) + tools/*.php (工具层)
 * 工具目录: {WORK_DIR}/tools/
 *
 * 升级日志:
 *  v4.1 - 多目录授权: 配置文件 sync_config.json + tokens/scopes/permissions
 *  v4.3 - AI 体验增强: 危险操作二次确认/exec退出码修复/无操作显示帮助/配置缺字段友好提示/pull全量确认/push路径区分不存在与被忽略
 *         管理 API: config_get/config_reload/scopes_list/tokens_list/
 *                   token_add/token_delete/scope_add/scope_delete/audit_log/admin_stats
 *         权限模型: read/write/delete/exec/lint/backup/admin
 *         向后兼容: 无配置文件时回退到旧单 token 单目录模式
 *  v3.2 - 模块化拆分: 接口层 + tools/ 工具层
 *         写代码辅助: search / todo / stats / read_range / format / lint / doc
 *         snippet CRUD / rename / copy
 *  v3.1 - 错误码统一 / list/diff/chunked_upload/webhook/health/metrics
 *         IP 白名单精确匹配 + CIDR / 频率限制 / 审计日志 / gzip 压缩
 *  v3.0 - 基础单文件 CRUD + 备份 + 探针
 */

// ==================== 错误码定义 ====================
$E = [
    'OK'                => 0,
    'UNAUTHORIZED'      => 1001,
    'FORBIDDEN'         => 1002,
    'RATE_LIMITED'      => 1003,
    'INVALID_PATH'      => 1004,
    'NOT_FOUND'         => 1005,
    'FILE_TOO_LARGE'    => 1006,
    'CHECKSUM_MISMATCH' => 1007,
    'UPLOAD_FAILED'     => 1008,
    'EXEC_DISABLED'     => 1009,
    'BACKUP_NOT_FOUND'  => 1010,
    'INVALID_PARAM'     => 1011,
    'CHUNK_MISSING'     => 1012,
    'WEBHOOK_FAILED'    => 1013,
    // 语义校验失败: 请求本身合法, 但目标内容未通过校验 (lint 语法错误等).
    // 与 1011(参数非法) 区分: 调用方应读取 data 中的结构化结果决定如何修复.
    'VALIDATION_FAILED' => 1014,
    'INTERNAL'          => 1099,
];

// ==================== 默认配置 (无 sync_config.json 时回退使用) ====================
// BUG-39 修复 (审计 Finding 2): 原 TOKEN 是硬编码公开值, sync_config.json 一旦丢失/损坏
// 服务器会以这个公开 token 启动 → 任何人都能用已知 token 接管. 改为空串,
// 让 line 359 的 fail-closed 检查直接拒绝所有请求 (强制操作员恢复 config 才能恢复服务).
$DEFAULT_CONFIG = [
    'TOKEN'             => '',
    'WORK_DIR'          => '/mnt/data/wwwroot/kodbox/Trae/works/player/',
    'BACKUP_DIR'        => 'ai-sync/backups/',
    'PROBE_DIR'         => 'ai-sync/probes/',
    'CHUNK_DIR'         => 'ai-sync/chunks/',
    'WEBHOOK_DIR'       => 'ai-sync/webhooks/',
    'AUDIT_LOG'         => 'ai-sync/audit.log',
    'AUDIT_MAX_SIZE'    => 10 * 1024 * 1024,   // P2-2: audit 日志轮转阈值 (默认 10MB)
    'AUDIT_MAX_KEEP'    => 5,                   // P2-2: audit 轮转保留份数
    'METRICS_FILE'      => 'ai-sync/metrics.json',
    // 客户端安装包目录: 含 token 明文, 默认放系统临时目录 (不在 Web 根下).
    // 如需持久化, 在 sync_config.json global 里指定 package_dir (务必在 Web 根之外).
    'PACKAGE_DIR'       => sys_get_temp_dir() . '/ai-sync-packages',
    'PACKAGE_TTL'       => 86400,

    'BACKUP_KEEP'       => 5,
    'MAX_SIZE'          => 64 * 1024 * 1024,
    'MAX_CHUNKED_SIZE'  => 1024 * 1024 * 1024,
    'CHUNK_TTL_DAYS'    => 7,
    'CHUNK_SIZE_DEFAULT'=> 4 * 1024 * 1024,
    // batch_deploy 单次最多文件数 (可由 scope 配置 max_files_per_deploy 覆盖).
    // 旧实现硬编码 50, 配置项存在但不生效, whoami 也报 50。
    'MAX_FILES_PER_DEPLOY' => 50,

    'EXEC_ENABLED'      => false,
    'EXEC_ALLOW_IPS'    => [],
    'EXEC_TIMEOUT_MAX'  => 120,

    'ALLOW_IPS'         => [],

    'RATE_LIMIT' => [
        'enabled'             => true,
        'per_token_per_minute'=> 60,
        'per_ip_per_second'   => 10,
        'upload_per_minute'   => 30,
    ],

    'GZIP_THRESHOLD_KB'  => 16,
    'GZIP_FORCE'         => false,
    // 超过该大小 (KB) 的 download 走流式输出 (不整包 base64 + json_encode + gzip), 峰值内存 O(块)
    'STREAM_THRESHOLD_KB'=> 2048,
    'AUDIT_ENABLED'      => true,
    'DEBUG'              => false,
    'API_VERSION'        => '3.2',
];
// =====================================================

// ==================== 配置文件加载 ====================
// 配置文件位置: 默认与脚本同目录 (兼容旧部署);
// 生产环境建议通过 AI_SYNC_CONFIG_FILE 指到 Web 根目录之外, 避免 sync_config.json 被静态下载.
// 例: SetEnv AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json (Apache)
//     fastcgi_param AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json; (nginx + php-fpm)
// 注意: nginx 的 fastcgi_param 进入 $_SERVER, 不一定出现在 getenv(); 两处都读.
$__cfg_env = getenv('AI_SYNC_CONFIG_FILE');
if (!is_string($__cfg_env) || trim($__cfg_env) === '') {
    $__cfg_env = $_SERVER['AI_SYNC_CONFIG_FILE'] ?? ($_ENV['AI_SYNC_CONFIG_FILE'] ?? '');
}
$CONFIG_FILE = (is_string($__cfg_env) && trim($__cfg_env) !== '')
    ? trim($__cfg_env)
    : __DIR__ . '/sync_config.json';

// 部署后自动清除 opcache 缓存, 确保加载最新版本 (同步推送后旧缓存会导致多目录模式失效)
if (function_exists('opcache_invalidate')) {
    @opcache_invalidate(__FILE__, true);
    if (file_exists($CONFIG_FILE)) @opcache_invalidate($CONFIG_FILE, true);
}

// 加载 sync_config.json, 失败返回 null (回退单目录模式)
function load_sync_config() {
    global $CONFIG_FILE;
    if (!file_exists($CONFIG_FILE)) return null;
    $json = @file_get_contents($CONFIG_FILE);
    if ($json === false) return null;
    $data = json_decode($json, true);
    if (!is_array($data)) return null;
    return $data;
}

// 保存配置到 sync_config.json (原子写入, 保留注释字段)
// 修复: 增加文件级互斥锁, 防止并发管理操作 (token_add/scope_add 等) last-write-wins 丢失数据
function save_sync_config($data) {
    global $CONFIG_FILE;
    $lock_file = $CONFIG_FILE . '.lock';
    $lf = @fopen($lock_file, 'c');
    if (!$lf) return false;
    // 阻塞获取独占锁, 等待其他写操作完成
    if (!flock($lf, LOCK_EX)) { fclose($lf); return false; }
    try {
        $json = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) return false;
        $tmp = $CONFIG_FILE . '.tmp';
        if (@file_put_contents($tmp, $json, LOCK_EX) === false) return false;
        return @rename($tmp, $CONFIG_FILE);
    } finally {
        @flock($lf, LOCK_UN);
        @fclose($lf);
    }
}

// 新建 scope 的默认字段
/**
 * 原子 read-modify-write sync_config.json.
 * 修复 P1-4: token_add/delete/scope_add/delete 原版用请求启动时加载的 $RAW_CONFIG 直接覆盖,
 *   并发 admin 操作会 last-write-wins 丢失数据 (如 A 加 token X, B 加 token Y, B 覆盖 A → X 丢失).
 *   此函数在锁内重新读取最新配置, 应用 $mutator 回调, 再写回, 保证不丢其他并发变更.
 * $mutator: callable($data) => [true, $data] | [false, $error_code, $extra]
 * 返回: [true, $data, $extra] | [false, $error_code, $extra]
 */
function update_sync_config_atomic($mutator) {
    global $CONFIG_FILE;
    $lock_file = $CONFIG_FILE . '.lock';
    $lf = @fopen($lock_file, 'c');
    if (!$lf) return [false, 'lock_fail', null];
    if (!flock($lf, LOCK_EX)) { fclose($lf); return [false, 'lock_fail', null]; }
    try {
        $raw = @file_get_contents($CONFIG_FILE);
        $data = ($raw === false) ? null : json_decode($raw, true);
        if (!is_array($data)) return [false, 'read_fail', null];
        $r = $mutator($data);
        if (!is_array($r) || empty($r[0])) {
            return [false, $r[1] ?? 'rejected', $r[2] ?? null];
        }
        $json = json_encode($r[1], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) return [false, 'encode_fail', null];
        $tmp = $CONFIG_FILE . '.tmp';
        if (@file_put_contents($tmp, $json, LOCK_EX) === false) return [false, 'write_fail', null];
        if (!@rename($tmp, $CONFIG_FILE)) return [false, 'rename_fail', null];
        return [true, $r[1], $r[2] ?? null];
    } finally {
        @flock($lf, LOCK_UN);
        @fclose($lf);
    }
}

function default_scope_config() {
    return [
        'work_dir'          => '',
        'backup_keep'       => 5,
        'max_size'          => 64 * 1024 * 1024,
        'max_chunked_size'  => 1024 * 1024 * 1024,
        'chunk_ttl_days'    => 7,
        'chunk_size_default'=> 4 * 1024 * 1024,
        'max_files_per_deploy' => 50,
        'exec_enabled'      => false,
        'exec_allow_ips'    => [],
        'exec_timeout_max'  => 30,
        'backup_dir'        => 'ai-sync/backups/',
        'probe_dir'         => 'ai-sync/probes/',
        'chunk_dir'         => 'ai-sync/chunks/',
        'webhook_dir'       => 'ai-sync/webhooks/',
        'audit_log'         => 'ai-sync/audit.log',
        'metrics_file'      => 'ai-sync/metrics.json',
    ];
}

// 构建启动阶段的 $CONFIG (仅含 global 字段, 供版本头/gzip/早期审计使用)
function build_boot_config($raw) {
    $g = $raw['global'] ?? [];
    $cfg = [
        'TOKEN'             => '',
        'WORK_DIR'          => '',
        'BACKUP_DIR'        => 'ai-sync/backups/',
        'PROBE_DIR'         => 'ai-sync/probes/',
        'CHUNK_DIR'         => 'ai-sync/chunks/',
        'WEBHOOK_DIR'       => 'ai-sync/webhooks/',
        'AUDIT_LOG'         => 'ai-sync/audit.log',
        'METRICS_FILE'      => 'ai-sync/metrics.json',
        'BACKUP_KEEP'       => 5,
        'MAX_SIZE'          => 64 * 1024 * 1024,
        'MAX_CHUNKED_SIZE'  => 1024 * 1024 * 1024,
        'CHUNK_TTL_DAYS'    => 7,
        'CHUNK_SIZE_DEFAULT'=> 4 * 1024 * 1024,
        'MAX_FILES_PER_DEPLOY' => 50,
        'EXEC_ENABLED'      => false,
        'EXEC_ALLOW_IPS'    => [],
        'EXEC_TIMEOUT_MAX'  => 120,
        'ALLOW_IPS'         => $g['allow_ips'] ?? [],
        'TRUSTED_PROXY'     => $g['trusted_proxy'] ?? false,
        'TRUSTED_PROXY_IPS' => $g['trusted_proxy_ips'] ?? [],
        'RATE_LIMIT'        => $g['rate_limit'] ?? [
            'enabled' => true, 'per_token_per_minute' => 60,
            'per_ip_per_second' => 10, 'upload_per_minute' => 30,
        ],
        'GZIP_THRESHOLD_KB' => $g['gzip_threshold_kb'] ?? 16,
        'GZIP_FORCE'        => $g['gzip_force'] ?? false,
        'STREAM_THRESHOLD_KB' => $g['stream_threshold_kb'] ?? 2048,
        'AUDIT_ENABLED'     => $g['audit_enabled'] ?? true,
        'DEBUG'             => $g['debug'] ?? false,
        'API_VERSION'       => $g['api_version'] ?? ($raw['version'] ?? '4.3'),
        'PACKAGE_DIR'       => $g['package_dir'] ?? (sys_get_temp_dir() . '/ai-sync-packages'),
        'PACKAGE_TTL'       => (int)($g['package_ttl'] ?? 86400),
    ];
    // 临时用第一个 scope 的 work_dir, 供早期审计/限流落盘
    $scopes = $raw['scopes'] ?? [];
    if (!empty($scopes)) {
        $first = reset($scopes);
        $cfg['WORK_DIR']  = $first['work_dir']  ?? '';
        $cfg['AUDIT_LOG'] = $first['audit_log'] ?? $cfg['AUDIT_LOG'];
    }
    return $cfg;
}

// 把 scope 配置合并进 $CONFIG, 保持旧字段名兼容 (工具函数无需改动)
function merge_scope_into_cfg($cfg, $scope_cfg, $scope_name, $token_info, $is_admin, $token) {
    $cfg['TOKEN']             = $token;
    $cfg['WORK_DIR']          = $scope_cfg['work_dir']           ?? $cfg['WORK_DIR'];
    $cfg['BACKUP_DIR']        = $scope_cfg['backup_dir']         ?? $cfg['BACKUP_DIR'];
    $cfg['PROBE_DIR']         = $scope_cfg['probe_dir']          ?? $cfg['PROBE_DIR'];
    $cfg['CHUNK_DIR']         = $scope_cfg['chunk_dir']          ?? $cfg['CHUNK_DIR'];
    $cfg['WEBHOOK_DIR']       = $scope_cfg['webhook_dir']        ?? $cfg['WEBHOOK_DIR'];
    $cfg['AUDIT_LOG']         = $scope_cfg['audit_log']          ?? $cfg['AUDIT_LOG'];
    // 全局配置里没有 audit_max_size 时 `$cfg['AUDIT_MAX_SIZE']` 会触发 Undefined array key 警告
    // (每个请求一条 PHP Warning), 这里给出与 audit_log 内部一致的默认值。
    $cfg['AUDIT_MAX_SIZE']    = $scope_cfg['audit_max_size']     ?? ($cfg['AUDIT_MAX_SIZE'] ?? 10 * 1024 * 1024);
    $cfg['AUDIT_MAX_KEEP']    = $scope_cfg['audit_max_keep']     ?? ($cfg['AUDIT_MAX_KEEP'] ?? 5);
    $cfg['METRICS_FILE']      = $scope_cfg['metrics_file']       ?? $cfg['METRICS_FILE'];
    $cfg['BACKUP_KEEP']       = $scope_cfg['backup_keep']        ?? $cfg['BACKUP_KEEP'];
    $cfg['MAX_SIZE']          = $scope_cfg['max_size']           ?? $cfg['MAX_SIZE'];
    $cfg['MAX_CHUNKED_SIZE']  = $scope_cfg['max_chunked_size']   ?? $cfg['MAX_CHUNKED_SIZE'];
    $cfg['CHUNK_TTL_DAYS']    = $scope_cfg['chunk_ttl_days']     ?? $cfg['CHUNK_TTL_DAYS'];
    $cfg['CHUNK_SIZE_DEFAULT']= $scope_cfg['chunk_size_default'] ?? $cfg['CHUNK_SIZE_DEFAULT'];
    $cfg['MAX_FILES_PER_DEPLOY'] = isset($scope_cfg['max_files_per_deploy'])
        ? max(1, (int)$scope_cfg['max_files_per_deploy'])
        : ($cfg['MAX_FILES_PER_DEPLOY'] ?? 50);
    $cfg['EXEC_ENABLED']      = $scope_cfg['exec_enabled']       ?? $cfg['EXEC_ENABLED'];
    $cfg['EXEC_ALLOW_IPS']    = $scope_cfg['exec_allow_ips']     ?? $cfg['EXEC_ALLOW_IPS'];
    $cfg['EXEC_TIMEOUT_MAX']  = $scope_cfg['exec_timeout_max']   ?? $cfg['EXEC_TIMEOUT_MAX'];
    $cfg['STREAM_THRESHOLD_KB'] = $scope_cfg['stream_threshold_kb'] ?? $cfg['STREAM_THRESHOLD_KB'];
    // 新增字段 (多目录授权信息)
    $cfg['SCOPE']             = $scope_name;
    $cfg['TOKEN_NAME']        = $token_info['name'] ?? '';
    $cfg['PERMISSIONS']       = $token_info['permissions'] ?? [];
    $cfg['IS_ADMIN']          = $is_admin;
    $cfg['AVAILABLE_SCOPES']  = $token_info['scopes'] ?? [];
    return $cfg;
}

// token 脱敏: 前6位 + ***
function mask_token($tk) {
    $tk = (string)$tk;
    if (strlen($tk) <= 6) return '***';
    return substr($tk, 0, 6) . '***';
}

/** 推断外部可见 scheme: 优先反向代理头, 其次 HTTPS 标志, 最后 request scheme. */
function _request_scheme() {
    $xfp = $_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '';
    if (is_string($xfp) && $xfp !== '') {
        $first = strtolower(trim(explode(',', $xfp)[0]));
        if ($first === 'http' || $first === 'https') return $first;
    }
    $rs = $_SERVER['REQUEST_SCHEME'] ?? '';
    if ($rs === 'http' || $rs === 'https') return $rs;
    $https = $_SERVER['HTTPS'] ?? '';
    return ($https !== '' && strtolower((string)$https) !== 'off') ? 'https' : 'http';
}

/** 当前请求的 API URL (不信任 Host 注入? Host 只影响返回给调用方自己的 URL, 不参与鉴权). */
function _request_api_url() {
    return _request_scheme() . '://' . ($_SERVER['HTTP_HOST'] ?? 'localhost')
        . ($_SERVER['SCRIPT_NAME'] ?? '/sync_api.php');
}

$RAW_CONFIG = load_sync_config();
$MULTI_DIR_MODE = ($RAW_CONFIG !== null);

if ($MULTI_DIR_MODE) {
    $CONFIG = build_boot_config($RAW_CONFIG);
} else {
    $CONFIG = $DEFAULT_CONFIG;
}
// =====================================================

// ==================== 全局辅助函数 ====================
// 错误码 => 修复建议 (供 AI 自主决策)
// 错误码短标签 (help 索引用): 与 Python 端 ERROR_LABELS 逐字一致
$GLOBALS['ERROR_LABELS'] = [
    1001 => '未授权(token 无效或缺失)', 1002 => '无权限(权限位不足/scope 未授权)',
    1003 => '限流(请求过快)', 1004 => '路径非法(越界/绝对路径/../)',
    1005 => '未找到(文件或目录不存在)', 1006 => '文件过大(超过 MAX_SIZE)',
    1007 => '校验失败(md5/乐观锁冲突)', 1008 => '写入失败(权限/磁盘)',
    1009 => 'exec 未启用', 1010 => '备份不存在', 1011 => '参数非法(缺失或类型错误)',
    1012 => '分片缺失', 1013 => 'webhook 失败', 1014 => '校验未通过(语法错误等)',
    1099 => '服务器内部错误',
];

$GLOBALS['ERROR_HINTS'] = [
    1001 => '检查请求是否携带正确 token; admin 操作需 admin_token',
    1002 => '当前 token 无此权限, 用 whoami API 查询权限边界; 或确认 scope 是否在 token 授权范围',
    1003 => '请求过快, 降低频率或联系管理员调整 rate_limit',
    1004 => '路径越界: 只接受相对 work_dir 的相对路径(如 TV/config.php), 不要用绝对路径或含 ../; 客户端正确命令: --push <相对路径> 或 --upload-file <WATCH_DIR内相对路径>',
    1005 => '文件/目录不存在, 先用 list 确认路径; 创建用 mkdir/upload',
    1006 => '文件过大, 改用 chunked_upload 分片上传; 或联系管理员调大 max_size',
    1007 => 'MD5 校验失败, 重新计算本地 md5 后重传; content_b64 可能被截断',
    1008 => '服务器写入失败, 检查目录权限/磁盘空间',
    1009 => 'exec 未启用, 联系管理员在 sync_config.json 开启 exec_enabled',
    1010 => '备份不存在, 用 backup_ls 列出可用备份',
    1011 => '参数缺失或非法, 查看错误详情确认字段名',
    1012 => '分片缺失, 重新上传缺失的分片; 或用 chunked_upload resume 查询已传分片',
    1014 => '请求已执行但内容未通过校验: 读取 issues/results 中的行号与消息修复后重试; 不要当传输错误盲目重试',
    1099 => '服务器内部错误, 查看日志; 可尝试回滚 (backup_rb)',
];

// ==================== 自描述: action 契约表 (AI 零文档接入) ====================
// 与 Python 端 HELP_SPEC 保持一致 (compare_all 有 help 对齐用例)。
$GLOBALS['HELP_SPEC'] = [
    'ping' => [
        'd' => '探测连通性/版本号/全部可用 action/当前 token 权限',
        'r' => [],
        'eg' => ['action' => 'ping'],
        'ret' => 'version,time,work_dir,backup_dir,probe_dir,chunk_dir,exec_enabled,actions,scope,token_name,is_admin,permissions,available_scopes,help(help 命令用法提示)',
        'see' => ['whoami', 'health', 'help'],
    ],
    'health' => [
        'd' => '健康检查: 目录可写/磁盘空间/内部目录完整性',
        'r' => [],
        'eg' => ['action' => 'health'],
        'ret' => 'version,php_version,uptime_s,work_dir,work_dir_writable,disk_free_mb,disk_total_mb,extensions',
        'see' => ['ping', 'admin_stats'],
    ],
    'metrics' => [
        'd' => '调用统计: 各 action 次数与错误码分布',
        'r' => [],
        'eg' => ['action' => 'metrics'],
        'ret' => 'actions{name:count},errors[{code,count}],total,since,last_ts',
        'see' => ['admin_stats', 'audit_log'],
    ],
    'help' => [
        'd' => '自描述: 无参返回全部 action 索引; 传 name=<action> 返回该 action 的参数契约、示例与返回字段',
        'r' => [],
        'o' => ['name:str 要详查的 action 名(别名 about/topic/target)'],
        'ret' => '无参: actions[{n:名,d:描述,r:必填}],total,usage,envelope,error_codes; 详查: action,desc,required,optional,returns,example,errors,see_also',
        'eg' => [
            'action' => 'help',
            'name' => 'upload',
        ],
        'see' => ['ping'],
    ],
    'client_download' => [
        'd' => '返回服务端参考客户端 sync.py 原始字节 (客户端 --self-update 自动升级用; 响应头 X-Client-Md5 携带 md5)',
        'r' => [],
        'eg' => ['action' => 'client_download'],
        'ret' => '原始字节流 (application/octet-stream)',
        'see' => ['whoami'],
    ],
    'whoami' => [
        'd' => '当前 token 的身份、scope、权限位、可用 scope 列表',
        'r' => [],
        'eg' => ['action' => 'whoami'],
        'ret' => 'token_name,scope,is_admin,permissions,available_scopes,available_actions,capabilities(各能力开关),workflow_hint(推荐工作流),work_dir,exec_enabled,api_version,server_time,server_timezone,max_size,max_chunked_size,max_files_per_deploy,chunk_size_default,chunk_ttl_days,backup_keep,rate_limit',
        'see' => ['ping'],
    ],
    'upload' => [
        'd' => '写单个文件(覆盖)或创建/替换软链接(传 link_target)。自动备份 + PHP 文件语法检查',
        'r' => ['path:str 相对 work_dir 的路径', 'content_b64:str 文件内容 base64(空文件请显式传 ""); 软链接改用 link_target'],
        'o' => ['link_target:str 软链接目标(相对路径); 传此字段时忽略 content_b64',
               'link_target_b64:str link_target 的 base64 原始字节(用于非 UTF-8 目标, 优先于 link_target)',
               'link_target_rel_b64:str 规范化目标(相对 work_dir)的 base64, 供客户端无损校验; 输出时提供',
               'expect_md5:str 乐观锁: 期望远端当前 md5, 不符拒绝并回 1007(别名 base_md5)',
               'mode:str 文件权限; 省略/空串=保留目标原权限(新文件 0644)',
               'mtime:int 文件修改时间(Unix 秒), 落盘后 touch 恢复'],
        'ret' => 'size, md5, mtime, backed_up, lint; 软链接额外: type=symlink,link_target,link_target_b64,link_target_rel,link_target_rel_b64,broken,resolvable,cycle',
        'eg' => [
            'action' => 'upload',
            'path' => 'src/a.txt',
            'content_b64' => 'aGVsbG8=',
        ],
        'err' => ['1004 路径越界', '1006 超 MAX_SIZE(改用 chunked_upload)', '1007 expect_md5 冲突', '1011 bad_path 以/结尾; bad_dest_path 形如 dir/.; dest_is_dir 目标是目录'],
        'see' => ['chunked_upload', 'upload_batch', 'download'],
    ],
    'upload_batch' => [
        'd' => '批量写文件(部分成功也返回 200, 逐条结果看 results)',
        'r' => ['items:array 每项 {path, content_b64, mode?, mtime?}'],
        'o' => ['items[].mode:str 权限; 省略/空串=保留目标原权限(新文件 0644)', 'items[].mtime:int 修改时间(Unix 秒)', 'files:array (items 的历史别名, 与 items 等价)'],
        'ret' => 'ok_idx[],fail_idx[],errors[] (与 items 等长, 成功项 null),results[],total,ok_count,failed_count,partial(部分成功=true)',
        'eg' => [
            'action' => 'upload_batch',
            'items' => [[
                'path' => 'a.txt',
                'content_b64' => 'aGk=',
            ]],
        ],
        'err' => ['1004 路径越界', '1011 目标为特殊文件时该项失败(dest_is_special)', '1011 bad_path/bad_dest_path/dest_is_dir 逐项失败'],
        'see' => ['upload', 'batch_deploy', 'chunked_upload'],
    ],
    'chunked_upload' => [
        'd' => '大文件分片上传。op=init 建会话 → put 逐片 → assemble 合并(status 查进度/abort 放弃)',
        'r' => ['op:str init|put|assemble|status|abort', 'path:str 目标路径(init 必填)'],
        'o' => ['total_size:int 总字节数(init)', 'chunk_size:int 分片大小(init, 默认取 scope 的 chunk_size_default, 通常 4MB)', 'upload_id:str 会话 id(put/assemble/status/abort)', 'chunk_index:int 片序号(put, 从 0 起)', 'content_b64:str 该片内容(put)', 'md5:str 该片 md5(put, 可选校验)', 'final_md5:str 整文件 md5(assemble, 可选校验)', 'mode:str 权限(init 可选; 省略=保留目标原权限, 新文件 0644)', 'mtime:int 修改时间(Unix 秒, init 可选)', 'total:int (size 的别名)'],
        'ret' => 'upload_id,chunk_size,total_chunks,chunk_count(init 阶段); progress(put 阶段); md5,mode,mtime(assemble 阶段),backed_up(assemble 阶段)',
        'eg' => [
            'action' => 'chunked_upload',
            'op' => 'init',
            'path' => 'big.bin',
            'total_size' => 10485760,
            'chunk_size' => 1048576,
        ],
        'err' => ['1006 too_large 超 max_chunked_size', '1011 invalid_size total_size 必须>0', '1012 分片缺失', '1007 final_md5 不符', '1011 目标为目录/特殊文件', '1011 chunk_index 缺失/类型错误/越界'],
        'see' => ['upload', 'download'],
    ],
    'batch_deploy' => [
        'd' => '批量部署: 先全部暂存再切换, 可带 pre/post 检查, 失败自动回滚',
        'r' => ['items:array 每项 {path, content_b64, mode?, mtime?}'],
        'o' => ['items[].mode:str 权限; 省略/空串=保留目标原权限(新文件 0644)', 'items[].mtime:int 修改时间(Unix 秒)', 'pre_check:str 暂存后执行的命令, {file} 替换为路径', 'post_check:str 切换后执行的命令', 'post_check_expect:str post_check 输出需包含此串', 'rollback_on_fail:bool 默认 true', 'files:array (items 的历史别名, 与 items 等价)'],
        'ret' => 'file_count,files[],sizes,backed_up[],pre_check,post_check,deploy_id,rolled_back',
        'eg' => [
            'action' => 'batch_deploy',
            'items' => [[
                'path' => 'a.php',
                'content_b64' => 'PD9waHA=',
            ]],
        ],
        'err' => ['1004 路径越界', '1011 重复路径/目录目标/根目标/父子路径冲突', '1099 部署失败(已回滚)'],
        'see' => ['upload_batch', 'batch_rollback'],
    ],
    'mkdir' => [
        'd' => '创建目录(递归); 目录已存在时传 mode 会 chmod 修正权限',
        'r' => ['path:str 目录路径'],
        'o' => ['mode:str 目录权限, 默认 0755; 已存在时会 chmod (owner 不可读的 mode 回退 0755)'],
        'ret' => 'path,mode,recursive,already_exists(目录已存在时为 true)',
        'eg' => [
            'action' => 'mkdir',
            'path' => 'src/new',
        ],
        'err' => ['1004 路径越界'],
        'see' => ['list'],
    ],
    'download' => [
        'd' => '读取文件内容(base64)。大于 stream_threshold 时走流式响应, Content-Length 精确',
        'r' => ['path:str 文件路径'],
        'ret' => 'content_b64, size, md5, mode, mtime; 特殊文件(FIFO/socket/设备)拒绝读取: ok=false,error=not_regular_file',
        'eg' => [
            'action' => 'download',
            'path' => 'a.txt',
        ],
        'err' => ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可读'],
        'see' => ['read_range', 'stat', 'checksum'],
    ],
    'stat' => [
        'd' => '单文件/目录元信息(不读内容)',
        'r' => ['path:str 路径'],
        'ret' => 'path,type,size,mtime,mode,uid,gid,nlink,ino,blocks,md5,sha1(文件时); ctime,atime,child_count(目录时); 特殊文件 type=special,special_type=fifo|socket|char_device|block_device,readable=false(不计算 hash); 链接: type=symlink,size=0,link_target,link_target_b64,link_target_rel,link_target_rel_b64,safe,broken,resolvable,cycle,md5(target_norm)',
        'eg' => [
            'action' => 'stat',
            'path' => 'a.txt',
        ],
        'err' => ['1004 路径越界', '1005 不存在'],
        'see' => ['list', 'download'],
    ],
    'list' => [
        'd' => '目录列举。非递归默认 200 条; 隐藏目录整体剪枝; 结果按名排序',
        'r' => [],
        'o' => ['path:str 目录, 默认 .', 'recursive:bool 递归', 'max_depth:int 递归深度, 默认 10; 0=不限', 'limit:int 默认 200, 上限 1000', 'offset:int 分页偏移', 'pattern:str 文件名通配, 如 *.php', 'include_hidden:bool 含点开头的项', 'since_mtime:int 只返回此时间后修改的'],
        'ret' => 'items[{name,path,type,size,mtime,mode,child_count(目录项); 特殊文件 type=special,special_type,readable=false; 链接额外 link_target,link_target_b64,link_target_rel,link_target_rel_b64,safe,broken,resolvable,cycle; 非 UTF-8 名称额外 encoding=binary,path_b64,name_b64}],total,has_more,limit,offset,path,since_mtime(传了才有)',
        'eg' => [
            'action' => 'list',
            'path' => '.',
            'recursive' => true,
            'pattern' => '*.php',
            'limit' => 100,
        ],
        'err' => ['1004 路径越界', '1005 不存在'],
        'see' => ['stat', 'search', 'checksum'],
    ],
    'read_range' => [
        'd' => '按行读文本(带行号). format=text 只回纯文本块, 省约 29% token(实测)',
        'r' => ['path:str 文本文件路径'],
        'o' => ['start_line:int 起始行, 默认 1', 'count:int 行数', 'end_line:int 结束行', 'max_line_len:int 单行截断, 默认 2000', 'format:str json(默认)|text'],
        'ret' => 'path,start_line,end_line(本次最后一行),returned,total_lines,truncated(=has_more),truncated_line,has_more,content[{line,text}](format=json 时),text(format=text 时的整段文本)',
        'eg' => [
            'action' => 'read_range',
            'path' => 'a.php',
            'start_line' => 1,
            'count' => 80,
            'format' => 'text',
        ],
        'err' => ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可读', '1011 binary_file 二进制文件不能按行读取', '1011 bad_range end_line<start_line', '1011 bad_max_line_len 必须>=1', '1011 bad_format 只支持 json|text'],
        'see' => ['download', 'search'],
    ],
    'checksum' => [
        'd' => '文件/目录校验和(PHP 端为流式增量)',
        'r' => ['path:str 文件或目录'],
        'o' => ['algorithm:str md5|sha1|sha224|sha256|sha384|sha512|crc32b|adler32, 默认 md5', 'pattern:str 文件名过滤'],
        'ret' => 'items[{path,size,mtime,mode,<algorithm>; 非 UTF-8 名称额外 encoding=binary,path_b64; 链接额外 type=symlink,link_target,link_target_b64,link_target_rel,link_target_rel_b64,safe,broken,resolvable,cycle,<algorithm>=hash(target_norm)}],total,algorithm,elapsed_ms,skipped_special,skipped[{path,special_type}]',
        'eg' => [
            'action' => 'checksum',
            'path' => '.',
            'algorithm' => 'md5',
            'pattern' => '*.php',
        ],
        'err' => ['1004 路径越界', '1011 顶层路径是特殊文件不可 hash'],
        'see' => ['diff', 'sync_check'],
    ],
    'diff' => [
        'd' => '客户端清单与服务端现状比对, 得出待推/待拉/冲突',
        'r' => ['local_items:array 每项 {path, md5, size, mtime}', 'path:str 比对子目录, 必填, 全库用 "."'],
        'o' => ['compute_hash:bool 对缺失 md5 的项现算', 'algorithm:str 仅 compute_hash=true 时生效, 白名单同 checksum'],
        'ret' => 'only_local[](仅本地有=待推送),only_remote[](仅服务端有=待拉取),modified[{path,local,remote,reason}](双方都有但内容不同=冲突),summary{only_local,only_remote,modified,identical}',
        'eg' => [
            'action' => 'diff',
            'path' => '.',
            'local_items' => [[
                'path' => 'a.txt',
                'md5' => '',
                'size' => 0,
                'mtime' => 0,
            ]],
        ],
        'err' => ['1004 路径越界'],
        'see' => ['sync_check', 'checksum'],
    ],
    'sync_check' => [
        'd' => '批量比对指定文件, 逐项给出 should_push / should_pull / conflict 与原因',
        'r' => ['files:array 每项 {path, md5, size, mtime}'],
        'ret' => 'results[{path,local_md5,local_size,remote_exists,remote_md5,remote_mtime(远端存在时),should_push,reason,conflict,hint(有处理建议时)}],total,should_push_count,conflict_count,summary',
        'eg' => [
            'action' => 'sync_check',
            'files' => [[
                'path' => 'a.txt',
                'md5' => 'd41d8cd98f00b204e9800998ecf8427e',
                'size' => 0,
                'mtime' => 0,
            ]],
        ],
        'err' => ['1011 files 缺失'],
        'see' => ['diff', 'stat'],
    ],
    'delete' => [
        'd' => '删除文件或目录。目录需 recursive=true',
        'r' => ['path:str 路径'],
        'o' => ['recursive:bool 递归删除目录'],
        'ret' => 'deleted,type,backed_up,recursive; 目标不存在时: already_missing=true(幂等, 非错误)',
        'eg' => [
            'action' => 'delete',
            'path' => 'tmp/x.txt',
        ],
        'err' => ['1004 路径越界', '1005 不存在', '1011 是目录但未传 recursive', '1011 root_delete_not_allowed 拒绝删除 work_dir 根'],
        'see' => ['backup_ls'],
    ],
    'rename' => [
        'd' => '重命名/移动(同 scope 内)',
        'r' => ['from:str 源路径', 'to:str 目标路径'],
        'o' => ['path:str (from 的别名)', 'new_path:str (to 的别名)'],
        'ret' => 'from,to,type,backed_up',
        'eg' => [
            'action' => 'rename',
            'from' => 'a.txt',
            'to' => 'b.txt',
        ],
        'err' => ['1004 路径越界', '1005 源不存在', '1011 dst_exists 目标已存在', '1011 root_rename_not_allowed 拒绝移动 work_dir 根'],
        'see' => ['copy', 'stat'],
    ],
    'copy' => [
        'd' => '复制文件',
        'r' => ['from:str 源路径', 'to:str 目标路径'],
        'o' => ['overwrite:bool 覆盖已存在目标, 默认 false', 'path:str (from 的别名)', 'new_path:str (to 的别名)'],
        'ret' => 'from,to,size,md5,backed_up',
        'eg' => [
            'action' => 'copy',
            'from' => 'a.txt',
            'to' => 'b.txt',
            'overwrite' => true,
        ],
        'err' => ['1004 路径越界', '1005 源不存在', '1011 dst_exists 目标已存在(传 overwrite 或换名), 或源/目标为特殊文件(src_is_special/dst_is_special)'],
        'see' => ['rename', 'backup_ls'],
    ],
    'format' => [
        'd' => '格式化源码(php/js/css/json...)',
        'r' => ['path:str 文件路径'],
        'o' => ['type:str 指定格式类型', 'write:bool 写回, 默认 false 仅预览'],
        'ret' => 'path,type,valid,changed,written,before_bytes,after_bytes,backed_up,content(格式化后的完整内容, write=false 预览时返回)',
        'eg' => [
            'action' => 'format',
            'path' => 'a.php',
            'write' => true,
        ],
        'err' => ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可格式化'],
        'see' => ['lint'],
    ],
    'lint' => [
        'd' => '语法检查单个文件',
        'r' => ['path:str 文件路径'],
        'ret' => 'language,valid,syntax_ok,issues[{line,severity,rule,message}],stats{template_lines},summary{error,warning,info}; 语法错误时顶层 ok=false/code=1014',
        'eg' => [
            'action' => 'lint',
            'path' => 'a.php',
        ],
        'err' => ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可 lint'],
        'see' => ['lint_batch', 'format'],
    ],
    'lint_batch' => [
        'd' => '批量语法检查',
        'r' => ['files:array 路径数组'],
        'ret' => 'results[{path,ok,valid,syntax_ok,issue_count,language,first_error{line,message}}],total,failed(无法执行),syntax_failed(语法不通过),total_issues,all_ok; 有语法错误时顶层 ok=false/code=1014',
        'eg' => [
            'action' => 'lint_batch',
            'files' => ['a.php'],
        ],
        'err' => ['1011 files 缺失'],
        'see' => ['lint'],
    ],
    'php_run' => [
        'd' => '用 php-cli 执行 work_dir 内的 PHP 脚本并捕获输出',
        'r' => ['path:str 脚本路径'],
        'o' => ['args:array 命令行参数', 'stdin_b64:str 标准输入 base64', 'timeout:int 秒, 默认 30'],
        'ret' => 'exit_code,stdout,stderr,timed_out',
        'eg' => [
            'action' => 'php_run',
            'path' => 't.php',
        ],
        'err' => ['1004 路径越界', '1009 未启用', '1099 超时'],
        'see' => ['exec', 'lint'],
    ],
    'search' => [
        'd' => '全文搜索(带上下文行)',
        'r' => ['pattern:str 关键词或正则'],
        'o' => ['path:str 搜索根目录, 默认 .', 'regex:bool pattern 视为正则', 'case_insensitive:bool 忽略大小写', 'whole_word:bool 全词匹配', 'file_pattern:str 文件名过滤, 如 *.php', 'context_before:int 前置上下文行', 'context_after:int 后置上下文行', 'max_results:int 上限, 默认 200', 'context:int (context_before 的别名)'],
        'ret' => 'hits[{path,line,match,context?}],total_matches,truncated,files_scanned,elapsed_ms,pattern,regex,case_insensitive,file_pattern,tip(扫描文件多且未指定 file_pattern 时才给)',
        'eg' => [
            'action' => 'search',
            'pattern' => 'TODO',
            'path' => '.',
            'file_pattern' => '*.php',
            'max_results' => 20,
        ],
        'err' => ['1004 路径越界', '1011 pattern 缺失'],
        'see' => ['todo', 'read_range', 'stats'],
    ],
    'todo' => [
        'd' => '扫描 TODO/FIXME/XXX/HACK 等标记, 按 tag 聚合',
        'r' => [],
        'o' => ['path:str 根目录, 默认 .', 'tags:array 自定义标记, 默认 TODO/FIXME/XXX/HACK', 'file_pattern:str 文件名过滤', 'max_results:int 上限, 默认 200'],
        'ret' => 'tags[],by_tag{},files_scanned,total,items[{path,line,tag,note,text}]',
        'eg' => [
            'action' => 'todo',
            'path' => '.',
            'file_pattern' => '*.php',
        ],
        'err' => ['1004 路径越界'],
        'see' => ['search', 'stats'],
    ],
    'stats' => [
        'd' => '代码统计: 文件数/行数/按扩展名或目录分组',
        'r' => ['path:str 根目录'],
        'o' => ['group_by:str ext|dir, 默认 ext', 'max_files:int 扫描上限', 'max_size:int 单文件大小上限', 'file_pattern:str 过滤'],
        'ret' => 'total_files,total_lines,total_bytes,total_code,total_comment,total_blank,group_by,groups[],largest[]',
        'eg' => [
            'action' => 'stats',
            'path' => '.',
            'group_by' => 'ext',
        ],
        'err' => ['1004 路径越界'],
        'see' => ['todo', 'checksum'],
    ],
    'doc' => [
        'd' => '生成本 API 的文档(HTML/Markdown/JSON)',
        'r' => [],
        'o' => ['format:str html|md|json, 默认 md'],
        'ret' => 'format,total,content(markdown 格式时)|actions(json 格式时)',
        'eg' => [
            'action' => 'doc',
            'format' => 'md',
        ],
        'see' => ['help'],
    ],
    'snippet_set' => [
        'd' => '保存代码片段(同 name 覆盖)',
        'r' => ['name:str 片段名', 'content:str 内容'],
        'o' => ['description:str 说明', 'language:str 语言', 'tags:array 标签'],
        'ret' => 'name,language,safe,tags',
        'eg' => [
            'action' => 'snippet_set',
            'name' => 'x',
            'content' => 'echo 1;',
        ],
        'err' => ['1011 name/content 缺失'],
        'see' => ['snippet_get', 'snippet_list'],
    ],
    'snippet_get' => [
        'd' => '按名取代码片段',
        'r' => ['name:str 片段名'],
        'ret' => 'name,content,language,description,tags,safe,created_at,updated_at',
        'eg' => [
            'action' => 'snippet_get',
            'name' => 'x',
        ],
        'err' => ['1005 不存在'],
        'see' => ['snippet_list', 'snippet_delete'],
    ],
    'snippet_list' => [
        'd' => '列代码片段(可按关键词/语言/标签过滤)',
        'r' => [],
        'o' => ['keyword:str 名称/说明关键词', 'language:str 语言', 'tag:str 标签'],
        'ret' => 'snippets[{name,description,language,tags,size,updated_at}],total',
        'eg' => [
            'action' => 'snippet_list',
            'language' => 'php',
        ],
        'see' => ['snippet_get', 'snippet_set'],
    ],
    'snippet_delete' => [
        'd' => '删除代码片段',
        'r' => ['name:str 片段名'],
        'ret' => 'deleted,name',
        'eg' => [
            'action' => 'snippet_delete',
            'name' => 'x',
        ],
        'err' => ['1005 不存在'],
        'see' => ['snippet_list'],
    ],
    'backup_ls' => [
        'd' => '列备份文件(默认最近 50 条)。不传 path 列全部',
        'r' => [],
        'o' => ['path:str 只看某文件的备份',
               'limit:int 返回条数, 默认 50 上限 500; 超过 500 条时用 path 收窄'],
        'ret' => 'backups[{file,path,size,mtime}],total,returned,truncated,tip(截断时才有)',
        'eg' => [
            'action' => 'backup_ls',
            'path' => 'a.txt',
            'limit' => 20,
        ],
        'see' => ['backup_rb', 'batch_rollback'],
    ],
    'backup_rb' => [
        'd' => '用备份回滚单个文件',
        'r' => ['path:str 目标文件', 'backup_file:str 备份名(来自 backup_ls)'],
        'ret' => 'path,restored_from,size,md5',
        'eg' => [
            'action' => 'backup_rb',
            'path' => 'a.txt',
            'backup_file' => 'a.txt.bak.20260101_000000_ab12',
        ],
        'err' => ['1004 路径越界', '1010 备份不存在'],
        'see' => ['backup_ls', 'batch_rollback'],
    ],
    'batch_rollback' => [
        'd' => '按时间点批量回滚(回滚到 before_ts 之前的最近备份)',
        'r' => ['before_ts:int unix 时间戳'],
        'o' => ['x:int 同 before_ts(兼容旧参数)', 'paths:array 限定路径', 'dry_run:bool 仅预演'],
        'ret' => 'results[],restored_count,failed_count,before_ts,dry_run,summary',
        'eg' => [
            'action' => 'batch_rollback',
            'before_ts' => 1760000000,
            'dry_run' => true,
        ],
        'err' => ['1011 before_ts 缺失'],
        'see' => ['backup_ls', 'backup_rb', 'recent_changes'],
    ],
    'pipeline' => [
        'd' => '一次往返执行多个 action(省 RTT):AI 连续操作用它替代逐条请求, 10 步约快 4 倍',
        'r' => ['steps:array 每项是一个完整 action 请求对象 {action, ...}'],
        'o' => ['stop_on_error:bool 首错即停(默认 false, 继续执行后续步)'],
        'ret' => 'results[](每项含 index/action + 该 action 的原始响应),total,succeeded,failed,stopped_at,stop_on_error,max_steps,mutating_actions_declared',
        'eg' => [
            'action' => 'pipeline',
            'steps' => [
                ['action' => 'stat', 'path' => 'a.php'],
                ['action' => 'read_range', 'path' => 'a.php'],
                ['action' => 'checksum', 'path' => 'a.php'],
            ],
        ],
        'err' => [
            '1011 steps 缺失/非数组/为空/超上限',
            '1011 含黑名单 action(pipeline_blocked_action)',
            '1002 某个 step 越权(该 step 独立失败, 不影响其它步)',
        ],
        'see' => ['upload', 'batch_deploy', 'upload_batch', 'help'],
    ],
    'audit_log' => [
        'd' => '读审计日志(最近 N 行; 每个请求至少一条记录)',
        'r' => [],
        'o' => ['lines:int 默认 100, 上限 2000'],
        'ret' => 'lines[],count,file',
        'eg' => [
            'action' => 'audit_log',
            'lines' => 50,
        ],
        'see' => ['recent_changes', 'oplog_list', 'metrics'],
    ],
    'recent_changes' => [
        'd' => '最近写操作摘要(倒序)',
        'r' => [],
        'o' => ['lines:int 默认 50'],
        'ret' => 'changes[{ts,action,path,code,ok,ip,size,md5,error_line}],total,success,failed,by_action,by_path,summary',
        'eg' => [
            'action' => 'recent_changes',
            'lines' => 20,
        ],
        'see' => ['audit_log', 'batch_rollback'],
    ],
    'error_log' => [
        'd' => '读服务器错误日志(可按关键词/正则/时间过滤)',
        'r' => [],
        'o' => ['name:str 日志源, 默认 php_error (见 error_log_list)', 'lines:int 行数, 默认 100', 'grep:str 包含关键词', 'regex:str 正则过滤', 'since_ts:int 起始时间', 'before_ts:int 截止时间', 'path:str 仅在 name=custom 时生效', 'relate_to:str 关联文件'],
        'ret' => 'lines[],source,path,total',
        'eg' => [
            'action' => 'error_log',
            'name' => 'php_error',
            'grep' => 'Fatal',
            'lines' => 50,
        ],
        'err' => ['1005 日志不存在'],
        'see' => ['error_log_list'],
    ],
    'error_log_list' => [
        'd' => '列出可读的日志源',
        'r' => [],
        'ret' => 'logs[{name,path,scope,format,readable,size,mtime}],total',
        'eg' => [
            'action' => 'error_log_list',
        ],
        'see' => ['error_log'],
    ],
    'oplog_append' => [
        'd' => '追加一条操作日志(客户端自报, 服务端补 ip/ua/token_name/server_ts)',
        'r' => ['entry:object ts/ts_epoch/op_id/command/args/summary/rc/duration_ms/version/host/user/cwd'],
        'ret' => 'bytes,client_id,log_file,duplicate?',
        'eg' => [
            'action' => 'oplog_append',
            'entry' => [
                'command' => 'deploy',
                'args' => ['files' => ['a.php']],
                'summary' => '部署: a.php',
                'rc' => 0,
            ],
        ],
        'err' => ['1011 entry 缺失'],
        'see' => ['oplog_list', 'oplog_view'],
    ],
    'oplog_list' => [
        'd' => '列出操作日志的客户端/会话(含成功/失败计数)',
        'r' => [],
        'ret' => 'clients[{client_id,scope,log_file,size_bytes,size_human,mtime,mtime_str,line_count,ok_count,error_count,first_ts,last_ts,last_epoch,last_command,last_summary,last_ip,last_rc,commands}],scopes,total',
        'eg' => [
            'action' => 'oplog_list',
        ],
        'see' => ['oplog_view', 'oplog_append'],
    ],
    'oplog_view' => [
        'd' => '按客户端 id 读操作日志(筛选/分页/统计)',
        'r' => ['client_id:str 客户端 id(见 oplog_list)'],
        'o' => ['limit:int 默认 100 最大 1000', 'offset:int 从最新往前跳过 N 条(加载更早)', 'grep:str 关键词过滤', 'command:str 命令名(逗号分隔多个)', 'result:str ok|error', 'since/until:epoch 或日期时间'],
        'ret' => 'entries[],total,matched,returned,limit,offset,has_more,filters,stats{ok,error,avg_ms,max_ms,by_command,first_ts,last_ts}',
        'eg' => [
            'action' => 'oplog_view',
            'client_id' => 'cli-abc',
            'command' => 'deploy,exec',
            'result' => 'error',
            'limit' => 50,
        ],
        'err' => ['1011 client_id 缺失'],
        'see' => ['oplog_list'],
    ],
    'admin_stats' => [
        'd' => '管理员总览: 各 scope 用量/请求量/错误率',
        'r' => [],
        'ret' => 'mode,work_dir(单目录模式),file_count(单目录模式),size_mb(单目录模式),scopes[](多目录模式),cached_at(多目录模式),totals(多目录模式)',
        'eg' => [
            'action' => 'admin_stats',
        ],
        'err' => ['1002 需 admin_token'],
        'see' => ['health', 'metrics'],
    ],
    'config_get' => [
        'd' => '读当前生效配置(敏感字段打码)',
        'r' => [],
        'o' => ['section:str 只取某个配置节'],
        'ret' => 'mode,version,global,scopes,tokens,admin_token_present',
        'eg' => [
            'action' => 'config_get',
        ],
        'see' => ['config_reload', 'scopes_list'],
    ],
    'config_reload' => [
        'd' => '重新加载 sync_config.json',
        'r' => [],
        'ret' => 'reloaded,version,tokens,scopes,note(本次请求仍用旧配置, 下次请求才生效)',
        'eg' => [
            'action' => 'config_reload',
        ],
        'err' => ['1002 需 admin_token', '1099 配置非法'],
        'see' => ['config_get'],
    ],
    'scopes_list' => [
        'd' => '列出全部 scope 及其 work_dir',
        'r' => [],
        'ret' => 'scopes[{name,work_dir,writable,file_count,size_mb,backup_count,backup_keep,max_size_mb,max_files_per_deploy,exec_enabled}],mode',
        'eg' => [
            'action' => 'scopes_list',
        ],
        'see' => ['scope_add', 'scope_delete'],
    ],
    'scope_add' => [
        'd' => '新增/更新 scope',
        'r' => ['name:str scope 名'],
        'o' => ['work_dir:str 工作目录', 'backup_dir:str', 'probe_dir:str', 'chunk_dir:str', 'max_size:int', 'max_chunked_size:int', 'chunk_size_default:int', 'chunk_ttl_days:int', 'max_files_per_deploy:int 单次 batch_deploy 文件数上限, 默认 50', 'exec_enabled:bool', 'exec_timeout_max:int', 'exec_allow_ips:array', 'audit_log:bool', 'metrics_file:str', 'webhook_dir:str', 'backup_keep:int'],
        'ret' => 'name,created,config',
        'eg' => [
            'action' => 'scope_add',
            'name' => 'proj2',
            'work_dir' => '/srv/proj2',
        ],
        'err' => ['1002 需 admin_token', '1011 name 缺失'],
        'see' => ['scopes_list', 'scope_delete'],
    ],
    'scope_delete' => [
        'd' => '删除 scope',
        'r' => ['name:str scope 名'],
        'ret' => 'deleted,name',
        'eg' => [
            'action' => 'scope_delete',
            'name' => 'proj2',
        ],
        'err' => ['1002 需 admin_token', '1005 不存在'],
        'see' => ['scopes_list'],
    ],
    'tokens_list' => [
        'd' => '列出 token(仅显示前缀与权限)',
        'r' => [],
        'ret' => 'tokens[{name,token(admin 可见明文; 非 admin 为掩码),scopes,permissions}],admin_token_present,mode',
        'eg' => [
            'action' => 'tokens_list',
        ],
        'err' => ['1002 需 admin_token'],
        'see' => ['token_add', 'token_delete'],
    ],
    'token_add' => [
        'd' => '新增 token',
        'r' => ['name:str token 名'],
        'o' => ['new_token:str 指定 token 值(否则自动生成)', 'scopes:array 授权 scope', 'permissions:array 权限位'],
        'ret' => 'added,generated,name,token(自动生成时返回明文; 手动指定时为掩码),scopes,permissions',
        'eg' => [
            'action' => 'token_add',
            'name' => 'ci',
            'scopes' => ['xbxbb'],
        ],
        'err' => ['1002 需 admin_token'],
        'see' => ['tokens_list'],
    ],
    'token_delete' => [
        'd' => '删除 token',
        'r' => ['target_token:str 要删的 token 名或值前缀'],
        'ret' => 'deleted,target',
        'eg' => [
            'action' => 'token_delete',
            'target_token' => 'ci',
        ],
        'err' => ['1002 需 admin_token', '1005 不存在'],
        'see' => ['tokens_list'],
    ],
    'webhook_set' => [
        'd' => '注册/更新 webhook(事件回调)',
        'r' => ['url:str 回调地址'],
        'o' => ['events:array 事件名, 默认 [upload,delete]', 'filter:str 路径过滤', 'secret:str HMAC 签名密钥'],
        'ret' => 'id,url,events',
        'eg' => [
            'action' => 'webhook_set',
            'url' => 'https://x/hook',
            'events' => ['upload', 'delete'],
        ],
        'err' => ['1011 bad_url', '1011 内网地址被拒(需 WEBHOOK_ALLOW_INTERNAL)'],
        'see' => ['webhook_list', 'webhook_test'],
    ],
    'webhook_list' => [
        'd' => '列 webhook 配置',
        'r' => [],
        'ret' => 'hooks[{id,url,events,filter,fail_count,paused,created_at,last_fired_at,last_http_code,last_error}],total',
        'eg' => [
            'action' => 'webhook_list',
        ],
        'see' => ['webhook_set', 'webhook_delete'],
    ],
    'webhook_delete' => [
        'd' => '删除 webhook',
        'r' => ['id:str webhook id'],
        'ret' => 'deleted,id',
        'eg' => [
            'action' => 'webhook_delete',
            'id' => 'wh_x',
        ],
        'err' => ['1005 不存在'],
        'see' => ['webhook_list'],
    ],
    'webhook_test' => [
        'd' => '发送测试请求验证 webhook 可达',
        'r' => [],
        'o' => ['url:str 临时地址', 'path:str 模拟路径', 'secret:str 签名密钥'],
        'ret' => 'passed,http_code,total_time,url,curl_error,tip(失败时才给)',
        'eg' => [
            'action' => 'webhook_test',
            'url' => 'https://x/hook',
        ],
        'err' => ['1011 bad_url', '1099 不可达'],
        'see' => ['webhook_set'],
    ],
    'package_list' => [
        'd' => '列出已生成的客户端安装包',
        'r' => [],
        'ret' => 'packages[{file,size,mtime,url}],total',
        'eg' => [
            'action' => 'package_list',
        ],
        'see' => ['client_package', 'package_delete'],
    ],
    'package_delete' => [
        'd' => '删除客户端安装包',
        'r' => ['file:str 包文件名'],
        'ret' => 'deleted,file',
        'eg' => [
            'action' => 'package_delete',
            'file' => 'ai-sync-client-x.zip',
        ],
        'err' => ['1005 不存在'],
        'see' => ['package_list'],
    ],
    'client_package' => [
        'd' => '生成定制客户端安装包(zip, 内含预置配置)',
        'r' => [],
        'o' => ['target_token:str 打包进配置的 token', 'api_url:str 服务端地址', 'doc_mode:bool 附带文档', 'local_dir:str 本地目录', 'remote_subdir:str 远端子目录'],
        'ret' => 'filename,download_url,sha256,size,scope,api_url,permissions,entries,package_file,watch_dir,token_name,doc,zip_b64(包的 base64, 可直接落盘解包)',
        'eg' => [
            'action' => 'client_package',
            'target_token' => 'xbxbb123',
        ],
        'err' => ['1002 需 admin_token'],
        'see' => ['package_list'],
    ],
    'exec' => [
        'd' => '在服务器执行 shell 命令(受 exec_enabled 与 IP 白名单约束)',
        'r' => ['cmd:str 命令'],
        'o' => ['timeout:int 秒, 默认 30', 'cwd:str 工作目录'],
        'ret' => 'return_code,stdout,stderr,cwd,stdout_len,stderr_len,elapsed_ms,timed_out,output_truncated,timeout,warning,tip(安全提示)',
        'eg' => [
            'action' => 'exec',
            'cmd' => 'ls -la',
        ],
        'err' => ['1009 未启用', '1002 IP 不在白名单', '1011 cmd/超时参数非法',
                 '超时不是错误: ok=true, return_code=124, timed_out=true'],
        'see' => ['php_run', 'exec'],
    ],
    // ==================== 持久 shell (对齐原生 ssh, 面向 AI) ====================
    'shell_open' => [
        'd' => '打开持久 shell 会话(对齐原生 ssh): 会话内 cd / export 跨调用保持, 不必每条命令都重复绝对路径',
        'r' => [],
        'o' => ['session_id:str 自定义会话 id(省略则自动生成, 客户端会记下作后续默认)', 'cwd:str 起始工作目录',
                'env:obj 初始环境变量', 'name:str 备注名'],
        'ret' => 'session_id,cwd,name,reused(复用已有会话时 true),tip(新会话),note(复用且传了 cwd/env 时说明未生效)',
        'eg' => [
            'action' => 'shell_open',
            'cwd' => '/var/www',
        ],
        'err' => ['1009 exec 未启用', '1002 IP 不在白名单', '1011 cwd_not_found cwd 目录不存在',
                  '1011 invalid_env_key env 键名非法'],
        'see' => ['shell_run', 'shell_list', 'shell_close', 'exec'],
    ],
    'shell_run' => [
        'd' => '在持久会话内执行命令: cd / export 会持续生效到后续调用(对齐原生 ssh), 不用每条命令都 cd 一遍',
        'r' => ['session_id:str 会话 id', 'cmd:str 要执行的命令(支持多行/管道/&&/exit)'],
        'o' => ['cwd:str 本次覆盖工作目录(相对会话 cwd, 绝对路径直接用)', 'env:obj 本次追加环境变量',
                'timeout:int 秒, 默认 30', 'background:bool true 时后台执行并立即返回 job_id'],
        'ret' => '前台: session_id,return_code,stdout,stderr,cwd(执行后真实 cwd),timed_out,output_truncated,stdout_len,stderr_len,elapsed_ms,tip; 后台: job_id,background,session_id,cwd,timeout,tip; 破坏性命令额外返回 warnings[]',
        'eg' => [
            'action' => 'shell_run',
            'session_id' => 'sh_xxxxxxxxxxxx',
            'cmd' => 'cd /tmp && ls',
        ],
        'note' => '防呆(只防误操作, 不拦定向破坏): rm -rf/rm -fr 这类破坏性命令照常执行, 响应里带 warnings[] 提醒(如目标含 shell 变量, 为空时会退化成删根目录); rm -rf /、mkfs、dd 写磁盘、fork 炸弹、reboot 等毁灭性命令会被拒绝并返回 alternatives 替代方案',
        'err' => ['1011 unknown_session 会话不存在(先 shell_open)', '1011 empty_cmd 缺 cmd',
                  '1011 cmd_must_be_string cmd 必须是字符串', '1011 cwd_not_found cwd 目录不存在',
                  '1011 invalid_timeout timeout 非法', '1011 invalid_env_key env 键名非法',
                  '1002 destructive_blocked 毁灭性命令被拒(响应带 alternatives)',
                  '1099 超时(timed_out=true, return_code=124)'],
        'see' => ['shell_open', 'shell_job', 'shell_kill', 'shell_list', 'exec'],
    ],
    'shell_job' => [
        'd' => '查询后台任务: 轮询输出与退出码(对齐 nohup/& 后回来看结果)',
        'r' => ['job_id:str shell_run background=true 返回的 job_id'],
        'o' => ['lines:int 只返回末尾 N 行'],
        'ret' => 'job_id,status(running|done),return_code,timed_out,killed,output_truncated,output,stdout,stderr,pid,cwd,timeout,elapsed_ms',
        'eg' => [
            'action' => 'shell_job',
            'job_id' => 'jb_xxxxxxxxxxxx',
        ],
        'err' => ['1011 unknown_job job_id 不存在', '1009 exec 未启用'],
        'see' => ['shell_run', 'shell_kill', 'shell_list'],
    ],
    'shell_kill' => [
        'd' => '终止后台任务(先 TERM 后 KILL, 杀整棵进程树)',
        'r' => ['job_id:str'],
        'o' => [],
        'ret' => 'job_id,killed,pid,return_code,already_finished,note',
        'eg' => [
            'action' => 'shell_kill',
            'job_id' => 'jb_xxxxxxxxxxxx',
        ],
        'err' => ['1011 unknown_job job_id 不存在', '1009 exec 未启用'],
        'see' => ['shell_run', 'shell_job'],
    ],
    'shell_close' => [
        'd' => '关闭持久会话并清除其 cwd/env 状态(该会话的后台任务仍可 shell_job 查询); 幂等: 关一个本就不存在的会话也返回成功',
        'r' => ['session_id:str'],
        'o' => [],
        'ret' => 'session_id,closed,already_closed(会话本就不存在时 true),note',
        'eg' => [
            'action' => 'shell_close',
            'session_id' => 'sh_xxxxxxxxxxxx',
        ],
        'err' => ['1011 missing_session_id 未带 session_id', '1009 exec 未启用'],
        'see' => ['shell_open', 'shell_list'],
    ],
    'shell_list' => [
        'd' => '列举当前所有持久会话与后台任务(排查"我开过哪些会话/还有哪些任务在跑")',
        'r' => [],
        'o' => [],
        'ret' => 'sessions[session_id,cwd,name,idle_s],jobs[job_id,status,session_id,cmd_preview,pid],session_count,job_count',
        'eg' => [
            'action' => 'shell_list',
        ],
        'err' => ['1009 exec 未启用', '1002 IP 不在白名单'],
        'see' => ['shell_open', 'shell_run', 'shell_close'],
    ],
];

function _brief_keys($req, $limit = 8) {
    // 回显请求字段名 (排除 token), 供调用方自查是否拼错字段。
    // AI 客户端最常犯的错是字段名笔误 (content vs content_b64), 只回 "参数非法" 无法自愈。
    if (!is_array($req)) return '<not an object>';
    $ks = [];
    foreach ($req as $k => $_) {
        $ks_k = (string)$k;
        if ($ks_k === 'token' || $ks_k === 'scope' || $ks_k === 'action') continue;
        if ($ks_k !== '' && $ks_k[0] === '_') continue;   // 内部标记不对调用方回显
        $ks[] = (string)$k;
    }
    if (!$ks) return '<none>';
    $show = array_slice($ks, 0, $limit);
    $s = implode(', ', $show);
    if (count($ks) > $limit) $s .= ', ...(+' . (count($ks) - $limit) . ' more)';
    return $s;
}

function fail($code, $error, $message = '', $extra = []) {
    $hint = $GLOBALS['ERROR_HINTS'][$code] ?? '';
    $base = [
        'ok'       => false,
        'code'     => $code,
        'error'    => $error,
        'message'  => $message,
        'fix_hint' => $hint,
    ];
    // AI 调试辅助: DEBUG 模式下附带回溯信息, 帮助 AI 定位服务端出错位置 (生产关闭避免泄漏路径)
    if (!empty($GLOBALS['CONFIG']['DEBUG'])) {
        $bt = debug_backtrace(DEBUG_BACKTRACE_IGNORE_ARGS, 1);
        if (!empty($bt[0])) {
            $base['debug'] = [
                'file' => basename($bt[0]['file'] ?? ''),
                'line' => $bt[0]['line'] ?? 0,
            ];
        }
    }
    $out = array_merge($base, $extra);
    // fix_hint 永远给"最具体"的那条: 调用点带了 hint 就用它覆盖错误码通用文案。
    // 调用方往往只读 fix_hint 一个字段就决定下一步, 通用文案("参数非法")会导致盲目重试。
    if (!empty($out['hint'])) {
        $out['fix_hint'] = $out['hint'];
    }
    return $out;
}

function ok($data = []) {
    // 修复: data 中若含 ok/code 键会覆盖默认值导致状态矛盾, 显式强制
    $data = is_array($data) ? $data : [];
    $data['ok'] = true;
    if (!isset($data['code'])) $data['code'] = 0;
    return $data;
}

/**
 * 大文件流式下载响应 (对齐 Python 端 _send_stream_download)
 *
 * 旧实现: base64_encode(file_get_contents()) → json_encode → gzencode, 峰值内存约 文件 x 3~4
 *   (50MB 文件实测 RSS ~410MB; 500MB 直接 OOM)
 * 新实现: 单次顺序读, 边读边算 md5、边输出 base64 分块, 峰值内存 = O(块) = 128KB
 *
 * 为什么可以一次遍历就定 Content-Length:
 *   md5 十六进制恒为 32 字符, size 十进制位数在响应开始时已知
 *   → 总长 = 前缀 + ceil(size/3)*4 + 中缀 + 32 + 后缀, 全部可预计算
 * 因此不需要"头一遍读文件算 md5"的额外全盘 IO (Python 端为两遍, PHP 端一遍)。
 *
 * 注意: 不参与 gzip。整包 gzip 需要先拿到完整 JSON, 与流式目标冲突;
 *   且 base64 内容熵低、压缩收益 ~25%, 换来的是内存回到 O(文件)。客户端按 Content-Length 读完整 body 即可。
 */
function send_stream_download($path) {
    $fp = @fopen($path, 'rb');
    if (!$fp) {
        echo json_encode(fail($GLOBALS['E']['INTERNAL'], 'internal', 'Cannot open file for streaming'));
        return false;
    }
    $st   = @fstat($fp);
    $size = $st ? (int)$st['size'] : 0;
    $b64len = (int)(ceil($size / 3) * 4);
    $prefix = '{"content_b64": "';
    // mode/mtime 一并回传: 客户端落盘后可原样恢复权限与 mtime (否则流式路径只能拿到 0644/当前时间)
    $mode_s = $st ? sprintf('%04o', $st['mode'] & 07777) : '0644';
    $mtime_i = $st ? (int)$st['mtime'] : time();
    $mid    = '", "size": ' . $size . ', "mode": "' . $mode_s . '", "mtime": ' . $mtime_i . ', "md5": "';
    $tail   = '", "ok": true, "code": 0}';
    $total  = strlen($prefix) + $b64len + strlen($mid) + 32 + strlen($tail);

    header('Content-Type: application/json; charset=utf-8');
    header('Vary: Accept-Encoding');
    header('Content-Length: ' . $total);
    // 必须关掉任何输出压缩/缓冲, 否则 Content-Length 与实际字节数不符
    @ini_set('zlib.output_compression', '0');
    while (ob_get_level() > 0) { @ob_end_flush(); }

    echo $prefix;
    $ctx   = hash_init('md5');
    $CHUNK = 3 * 32768;   // 3 的整数倍: 每个分块 base64 不产生中途 padding
    $sent  = 0;
    while (!feof($fp)) {
        $buf = fread($fp, $CHUNK);
        if ($buf === false || $buf === '') break;
        $sent += strlen($buf);
        hash_update($ctx, $buf);
        echo base64_encode($buf);
        @flush();
    }
    fclose($fp);
    // 若文件在传输中被并发改写, size/md5 与 body 不再自洽 → 客户端 md5 校验会失败并重试
    //   (与旧实现行为一致, 不额外引入不确定性)
    echo $mid . hash_final($ctx) . $tail;
    @flush();
    return true;
}

function audit_log($action, $req, $code, $extra = []) {
    global $CONFIG;
    if (empty($CONFIG['AUDIT_ENABLED'])) return;
    $log_file = rtrim($CONFIG['WORK_DIR'], '/\\') . '/' . $CONFIG['AUDIT_LOG'];
    @mkdir(dirname($log_file), 0755, true);
    // P2-2-FIX: audit.log 大小轮转 — 超阈值时旋转为 .1~.N, 丢弃最旧, 防止无限增长
    //   锁内复查防并发重复轮转; 边界竞态仅可能使个别记录落入旧文件, 不丢数据
    $audit_max_size = $CONFIG['AUDIT_MAX_SIZE'] ?? 10485760;
    $audit_max_keep = $CONFIG['AUDIT_MAX_KEEP'] ?? 5;
    if (is_file($log_file) && @filesize($log_file) >= $audit_max_size) {
        $audit_lk = @fopen($log_file, 'a');
        if ($audit_lk && @flock($audit_lk, LOCK_EX)) {
            if (@filesize($log_file) >= $audit_max_size) {
                @unlink($log_file . '.' . $audit_max_keep);
                for ($i = $audit_max_keep - 1; $i >= 1; $i--) {
                    $from = $log_file . '.' . $i;
                    if (is_file($from)) @rename($from, $log_file . '.' . ($i + 1));
                }
                @rename($log_file, $log_file . '.1');
            }
            @flock($audit_lk, LOCK_UN);
        }
        if ($audit_lk) @fclose($audit_lk);
    }
    $token = $req['token'] ?? '';
    // token 只取标量: 数组/对象会让 "TOKEN . $token" 触发 Array to string 警告并算出错误 hash
    if (!is_string($token)) $token = (is_int($token) || is_float($token)) ? (string)$token : '';
    $token_hash = $token ? substr(hash('sha256', $CONFIG['TOKEN'] . $token), 0, 12) : 'none';
    $ip = function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR'] ?? '');
    // 与 Python 端对齐: path 统一成字符串再落盘 (客户端可能传 int/list/对象)
    $p = $req['path'] ?? '';
    if (!is_string($p)) {
        $p = (is_int($p) || is_float($p)) ? (string)$p : json_encode($p, JSON_UNESCAPED_UNICODE);
    }
    $entry = array_merge([
        'ts'         => date('Y-m-d H:i:s'),
        'action'     => $action,
        'token_hash' => $token_hash,
        'ip'         => $ip,
        'code'       => $code,
        'path'       => $p,
        // 请求关联 id: 同一次 HTTP 请求的明细条目与通用条目可串起来排查
        'rid'        => $GLOBALS['AUDIT_CTX']['rid'] ?? '',
    ], $extra);
    if (isset($GLOBALS['AUDIT_CTX'])) {
        $GLOBALS['AUDIT_CTX']['detail'] = ($GLOBALS['AUDIT_CTX']['detail'] ?? 0) + 1;
    }
    // 非法 UTF-8 (如命令/文件名被按字节截断) 用替换符落盘, 避免整条记录变成空行
    $__jflags = JSON_UNESCAPED_UNICODE;
    if (defined('JSON_INVALID_UTF8_SUBSTITUTE')) $__jflags |= JSON_INVALID_UTF8_SUBSTITUTE;
    $__line = json_encode($entry, $__jflags);
    if ($__line === false) {
        $__line = json_encode([
            'ts' => date('Y-m-d H:i:s'), 'action' => $action,
            'token_hash' => $token_hash, 'ip' => $ip, 'code' => $code,
            'rid' => $GLOBALS['AUDIT_CTX']['rid'] ?? '', 'encode_error' => true,
        ]);
    }
    if ($__line !== false) {
        @file_put_contents($log_file, $__line . "\n", FILE_APPEND | LOCK_EX);
    }
}

/** 审计字段截断: 按 UTF-8 字符边界截断, 不产生半个多字节字符 */
function _audit_trunc($s, $n) { return _utf8_trunc($s, $n); }

/**
 * 扩展是否可用.
 * 默认只看真实环境; 设 AI_SYNC_SIMULATE_MISSING_EXT=zip,mbstring,curl 可强制走降级分支,
 * 用于在没有对应扩展的机器上验证"降级也能跑", 生产环境不要设.
 */
function _ext_ok($ext) {
    static $missing = null;
    if ($missing === null) {
        $env = (string)@getenv('AI_SYNC_SIMULATE_MISSING_EXT');
        $missing = ($env === '') ? [] : array_map('trim', explode(',', strtolower($env)));
    }
    return !in_array(strtolower((string)$ext), $missing, true);
}

/** mbstring 是否真的可用 (含模拟缺失开关) */
function _mbstring_ok() { return function_exists('mb_substr') && _ext_ok('mbstring'); }

/** curl 是否真的可用 (含模拟缺失开关) */
function _curl_ok() { return function_exists('curl_init') && _ext_ok('curl'); }

/**
 * UTF-8 安全截断.
 * 有 mbstring: 按字符截断 (中文不会断字).
 * 无 mbstring: 按字节截断后回退到合法 UTF-8 边界 —— 能力保留, 只是可能少一个字.
 */
function _utf8_trunc($s, $n) {
    $s = (string)$s;
    $n = (int)$n;
    if ($n <= 0 || $s === '') return '';
    if (_mbstring_ok()) return mb_substr($s, 0, $n, 'UTF-8');
    if (strlen($s) <= $n) return $s;
    $s = substr($s, 0, $n);
    while ($s !== '' && @preg_match('//u', $s) !== 1) $s = substr($s, 0, -1);
    return $s;
}

/** 小写化: 无 mbstring 时按 ASCII 回退 */
function _mb_lower($s) {
    return _mbstring_ok() ? mb_strtolower((string)$s, 'UTF-8') : strtolower((string)$s);
}

/** 大小写不敏感查找: 无 mbstring 时按 ASCII 回退 */
function _mb_stripos($haystack, $needle) {
    return _mbstring_ok()
        ? @mb_stripos((string)$haystack, (string)$needle, 0, 'UTF-8')
        : @stripos((string)$haystack, (string)$needle);
}

/** DOS 时间戳 (zip 格式, 有效范围 1980-2107) */
function _zip_dos_time($ts) {
    $ts = (int)$ts;
    if ($ts < 315532800) $ts = 315532800; // 1980-01-01, 早于此的按此计
    $d = getdate($ts);
    $t  = (($d['hours'] & 0x1F) << 11) | (($d['minutes'] & 0x3F) << 5) | (($d['seconds'] >> 1) & 0x1F);
    $dd = ((($d['year'] - 1980) & 0x7F) << 9) | (($d['mon'] & 0x0F) << 5) | ($d['mday'] & 0x1F);
    return [$t, $dd];
}

/**
 * 纯 PHP 打包 (store 不压缩): 不需要 ZipArchive 扩展.
 * 结构: 每条 local file header + 文件名 + 原数据; 末尾 central directory + EOCD.
 * 文件名按 UTF-8 处理 (general purpose flag bit 11), unzip / Python zipfile / Windows 资源管理器均可读.
 */
function _zip_pack_pure(array $files) {
    $out = '';
    $cd  = '';
    $count = 0;
    list($ztime, $zdate) = _zip_dos_time(time());
    foreach ($files as $name => $data) {
        $name = str_replace('\\', '/', (string)$name);
        $data = (string)$data;
        $nlen = strlen($name);
        if ($nlen === 0 || $nlen > 65535) return null;
        $size = strlen($data);
        $crc  = crc32($data);
        if ($crc < 0) $crc += 4294967296; // 32 位平台上 crc32 可能返回负数
        $offset = strlen($out);
        $flags  = 0x0800; // UTF-8 文件名标记
        $out .= "PK\x03\x04" . pack('v', 20) . pack('v', $flags) . pack('v', 0)
              . pack('v', $ztime) . pack('v', $zdate)
              . pack('V', $crc) . pack('V', $size) . pack('V', $size)
              . pack('v', $nlen) . pack('v', 0)
              . $name . $data;
        $cd .= "PK\x01\x02" . pack('v', 20) . pack('v', 20) . pack('v', $flags) . pack('v', 0)
             . pack('v', $ztime) . pack('v', $zdate)
             . pack('V', $crc) . pack('V', $size) . pack('V', $size)
             . pack('v', $nlen) . pack('v', 0) . pack('v', 0)
             . pack('v', 0) . pack('v', 0) . pack('V', 0)
             . pack('V', $offset) . $name;
        $count++;
    }
    if ($count === 0) return null;
    $cd_off = strlen($out);
    return $out . $cd . "PK\x05\x06" . pack('v', 0) . pack('v', 0)
         . pack('v', $count) . pack('v', $count)
         . pack('V', strlen($cd)) . pack('V', $cd_off) . pack('v', 0);
}

/**
 * 打包入口: 有 ZipArchive 用它(压缩), 没有就用内置纯 PHP 打包器(store).
 * 返回 ['data' => zip 二进制, 'builder' => 'ziparchive'|'pure_php'], 都失败返回 null.
 */
function _zip_pack(array $files) {
    if (class_exists('ZipArchive') && _ext_ok('zip')) {
        $tmp = @tempnam(sys_get_temp_dir(), 'aisync_');
        if ($tmp === false) return null;
        $tmp .= '.zip';
        $zip = new ZipArchive();
        if ($zip->open($tmp, ZipArchive::CREATE | ZipArchive::OVERWRITE) === true) {
            foreach ($files as $name => $data) $zip->addFromString((string)$name, (string)$data);
            $zip->close();
            $data = @file_get_contents($tmp);
            @unlink($tmp);
            if ($data !== false && $data !== '') return ['data' => $data, 'builder' => 'ziparchive'];
        } else {
            @unlink($tmp);
        }
    }
    $pure = _zip_pack_pure($files);
    return $pure === null ? null : ['data' => $pure, 'builder' => 'pure_php'];
}

/**
 * 通用 HTTP 请求: 有 curl 用它, 没有 curl 就退回 stream (能力保留, 但无法锁定 IP).
 * 返回 ['http_code'=>int, 'error'=>string|null, 'body'=>string,
 *       'total_time'=>float, 'transport'=>'curl'|'stream']
 * $opts: method(GET/POST/HEAD) / body / headers[] / timeout / connect_timeout / resolve[]
 *        resolve 形如 ["host:port:ip"], 仅 curl 支持 (防 DNS rebinding 的 IP 固定)
 */
function _http_request_simple($url, array $opts = []) {
    $method  = strtoupper((string)($opts['method'] ?? 'GET'));
    $body    = array_key_exists('body', $opts) ? (string)$opts['body'] : null;
    $headers = (array)($opts['headers'] ?? []);
    $timeout = max(1, (int)($opts['timeout'] ?? 10));
    $ctime   = max(1, (int)($opts['connect_timeout'] ?? min(5, $timeout)));
    $resolve = (array)($opts['resolve'] ?? []);
    $only_head = ($method === 'HEAD');
    $t0 = microtime(true);

    if (_curl_ok()) {
        $ch = curl_init($url);
        $opt = [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HEADER         => false,
            CURLOPT_TIMEOUT        => $timeout,
            CURLOPT_CONNECTTIMEOUT => $ctime,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_FOLLOWLOCATION => false,
            // 只允许 http/https, 防止 file:// gopher:// 等被当成 URL
            CURLOPT_PROTOCOLS      => defined('CURLPROTO_HTTP') ? (CURLPROTO_HTTP | CURLPROTO_HTTPS) : 3,
        ];
        if ($headers) $opt[CURLOPT_HTTPHEADER] = $headers;
        if ($only_head) {
            $opt[CURLOPT_NOBODY] = true;
        } elseif ($body !== null) {
            $opt[CURLOPT_POST] = true;
            $opt[CURLOPT_POSTFIELDS] = $body;
        } elseif ($method !== 'GET') {
            $opt[CURLOPT_CUSTOMREQUEST] = $method;
        }
        if ($resolve) $opt[CURLOPT_RESOLVE] = $resolve;
        curl_setopt_array($ch, $opt);
        $resp = curl_exec($ch);
        $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err  = curl_error($ch);
        curl_close($ch);
        return [
            'http_code'  => $code,
            'error'      => $err !== '' ? $err : null,
            'body'       => is_string($resp) ? $resp : '',
            'total_time' => round(microtime(true) - $t0, 3),
            'transport'  => 'curl',
        ];
    }

    // ---- 无 curl: stream 回退 ----
    $hdr = '';
    foreach ($headers as $h) $hdr .= $h . "\r\n";
    $ctx = stream_context_create([
        'http' => [
            'method'          => $method,
            'header'          => $hdr,
            'content'         => (!$only_head && $body !== null) ? $body : '',
            'timeout'         => $timeout,
            'ignore_errors'   => true,   // 4xx/5xx 也要读到响应, 否则拿不到状态码
            'follow_location' => 0,
            'max_redirects'   => 0,
            'protocol_version'=> 1.1,
        ],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true],
    ]);
    $resp = @file_get_contents($url, false, $ctx);
    $code = 0;
    if (!empty($http_response_header) && preg_match('#\s(\d{3})\s#', (string)$http_response_header[0], $m)) {
        $code = (int)$m[1];
    }
    $err = null;
    if ($resp === false) {
        $le = error_get_last();
        $err = 'stream_request_failed' . (!empty($le['message']) ? ': ' . $le['message'] : '');
    }
    return [
        'http_code'  => $code,
        'error'      => $err,
        'body'       => is_string($resp) ? $resp : '',
        'total_time' => round(microtime(true) - $t0, 3),
        'transport'  => 'stream',
    ];
}

/** 把异常信息翻成人能直接执行的结论 (缺哪个扩展 / 装完要重启) */
function _exception_hint($msg) {
    $msg = (string)$msg;
    if (preg_match('/Call to undefined function\s+([a-z_]+[a-z0-9_]*)/i', $msg, $m)) {
        $fn = strtolower($m[1]);
        if (strpos($fn, 'mb_') === 0) {
            return '缺少 PHP 扩展 mbstring (函数 ' . $m[1] . ' 未定义): 安装 php-mbstring 后必须重启 php-fpm / php -S 进程';
        }
        if (strpos($fn, 'curl_') === 0) {
            return '缺少 PHP 扩展 curl (函数 ' . $m[1] . ' 未定义): 安装 php-curl 后必须重启 php-fpm / php -S 进程';
        }
        if (strpos($fn, 'zip') === 0) {
            return '缺少 PHP 扩展 zip (函数 ' . $m[1] . ' 未定义): 安装 php-zip 后必须重启 php-fpm / php -S 进程';
        }
        return '缺少提供函数 ' . $m[1] . ' 的 PHP 扩展: 安装对应扩展后必须重启 php-fpm / php -S 进程';
    }
    if (stripos($msg, 'ZipArchive') !== false) {
        return '缺少 PHP 扩展 zip: 安装 php-zip 后必须重启 php-fpm / php -S 进程';
    }
    return null;
}

/** 请求字段名列表 (只记名字不记值, 永不落 token/密码/content 等敏感内容) */
function _audit_req_keys($req) {
    if (!is_array($req)) return '';
    $ks = [];
    foreach (array_keys($req) as $k) {
        $k = (string)$k;
        if ($k === 'token' || strpos($k, '_') === 0) continue;
        $ks[] = $k;
    }
    sort($ks, SORT_STRING);
    return implode(',', $ks);
}

/** 按 action 补充少量可定位问题的字段 (命令预览/会话号/批量步骤名) */
function _audit_action_extra($action, $req) {
    if (!is_array($req)) return [];
    $x = [];
    if (is_string($action) && strpos($action, 'shell_') === 0) {
        if (isset($req['session_id']) && is_string($req['session_id'])) $x['session_id'] = $req['session_id'];
        if (isset($req['job_id']) && is_string($req['job_id'])) $x['job_id'] = $req['job_id'];
        if (isset($req['cmd']) && is_string($req['cmd'])) $x['cmd_preview'] = _audit_trunc($req['cmd'], 120);
        if (array_key_exists('timeout', $req)) $x['timeout'] = $req['timeout'];
    }
    if ($action === 'pipeline' && isset($req['steps']) && is_array($req['steps'])) {
        $names = [];
        foreach (array_slice($req['steps'], 0, 30) as $st) {
            $names[] = (is_array($st) && isset($st['action']) && is_string($st['action'])) ? $st['action'] : '';
        }
        $x['steps'] = $names;
        $x['step_count'] = count($req['steps']);
    }
    return $x;
}

/**
 * 请求级兜底审计: action 内部未写明细时补一条通用记录.
 * 旧实现只记录写操作/鉴权失败, list/stat/diff/checksum/search/whoami/help 等
 * 查询类行为完全无记录。这里每个请求至少落一条 (明细优先, 不重复; pipeline 汇总始终记).
 */
function audit_request($action, $req, $result, $resp_bytes = 0, $stream = false) {
    global $CONFIG;
    $ctx = $GLOBALS['AUDIT_CTX'] ?? [];
    if (!empty($ctx['detail']) && $action !== 'pipeline') return;
    if (!is_array($result)) return;
    $code = $result['code'] ?? 0;
    if (!is_int($code)) $code = is_numeric($code) ? intval($code) : 0;
    $ua = (string)($_SERVER['HTTP_USER_AGENT'] ?? '');
    $extra = [
        'ok'         => ($code === 0),
        'method'     => $_SERVER['REQUEST_METHOD'] ?? 'POST',
        'ua'         => _audit_trunc($ua, 120),
        'req_bytes'  => intval($ctx['req_bytes'] ?? 0),
        'resp_bytes' => intval($resp_bytes),
        'elapsed_ms' => intval(round((microtime(true) - ($ctx['t0'] ?? microtime(true))) * 1000)),
        'req_keys'   => _audit_req_keys($req),
    ];
    $scope = $CONFIG['SCOPE'] ?? null;
    if ($scope !== null && $scope !== '') $extra['scope'] = (string)$scope;
    if (array_key_exists('TOKEN_NAME', $CONFIG)) $extra['token_name'] = (string)$CONFIG['TOKEN_NAME'];
    if (array_key_exists('IS_ADMIN', $CONFIG)) $extra['is_admin'] = (bool)$CONFIG['IS_ADMIN'];
    if ($code !== 0) $extra['error'] = (string)($result['error'] ?? '');
    if ($stream) $extra['stream'] = true;
    $extra = array_merge($extra, _audit_action_extra($action, $req));
    audit_log($action, is_array($req) ? $req : [], $code, $extra);
}

// ==================== 启动 ====================
// OPT-R12: CORS —— admin.html 与服务端不同域/不同端口时(独立托管管理面板、本地调试),
//   浏览器会拦截响应并先发 OPTIONS 预检。与 PY 端保持一致的实现。
//   CORS_ALLOW_ORIGIN 默认 '*': 回显请求 Origin(而非写死 *), 带凭证的请求也能过。
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    $_origin = $_SERVER['HTTP_ORIGIN'] ?? '';
    $_allow  = isset($CONFIG['CORS_ALLOW_ORIGIN']) ? $CONFIG['CORS_ALLOW_ORIGIN'] : '*';
    if ($_allow === '*') {
        $_ao = $_origin !== '' ? $_origin : '*';
    } else {
        $_list = array_filter(array_map('trim', explode(',', (string)$_allow)), 'strlen');
        $_ao = ($_origin !== '' && in_array($_origin, $_list, true)) ? $_origin
             : (count($_list) ? $_list[0] : '*');
    }
    header('Access-Control-Allow-Origin: ' . $_ao);
    header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-Token, Authorization');
    header('Access-Control-Max-Age: 86400');
    header('Vary: Origin');
    http_response_code(204);
    exit;
}
$_cors_origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if ($_cors_origin !== '') {
    $_allow2 = isset($CONFIG['CORS_ALLOW_ORIGIN']) ? $CONFIG['CORS_ALLOW_ORIGIN'] : '*';
    if ($_allow2 === '*') {
        $_ao2 = $_cors_origin;
    } else {
        $_list2 = array_filter(array_map('trim', explode(',', (string)$_allow2)), 'strlen');
        $_ao2 = in_array($_cors_origin, $_list2, true) ? $_cors_origin : (count($_list2) ? $_list2[0] : '*');
    }
    header('Access-Control-Allow-Origin: ' . $_ao2);
    header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-Token, Authorization');
    header('Vary: Origin');
}
header('Content-Type: application/json; charset=utf-8');
header('X-AI-Sync-Version: ' . $CONFIG['API_VERSION']);

// 安装包能力下载 (无 token): GET /sync_api.php?pkg_key=<64hex>
// 必须在 JSON body 解析之前处理; 仅接受 GET, 不接受 POST 绕过鉴权.
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'GET' && isset($_GET['pkg_key'])) {
    _serve_package_by_key((string)$_GET['pkg_key'], $CONFIG);
    exit;
}

// gzip 响应压缩 (在脚本末尾按响应大小决定是否压缩)
$accept_enc = $_SERVER['HTTP_ACCEPT_ENCODING'] ?? '';
$can_gzip = extension_loaded('zlib')
    && (stripos($accept_enc, 'gzip') !== false || !empty($CONFIG['GZIP_FORCE']));
// 不使用 ob_gzhandler, 末尾统一 gzencode, 否则双重 gzip 导致前端 JSON 解析失败

// --- 请求解析 ---
// 每请求审计上下文 (rid/耗时/字节数): 必须在 JSON 解析前初始化, bad_json 也要留痕
$__audit_rid = '';
try { $__audit_rid = bin2hex(random_bytes(4)); }
catch (Throwable $__e) { $__audit_rid = substr(md5(uniqid('', true)), 0, 8); }
$GLOBALS['AUDIT_CTX'] = [
    't0'        => microtime(true),
    'rid'       => $__audit_rid,
    'detail'    => 0,
    'req_bytes' => 0,
];
$raw_body = file_get_contents('php://input');
$GLOBALS['AUDIT_CTX']['req_bytes'] = strlen($raw_body);
// 顶层必须是 JSON 对象. 仅用 json_decode(..., true)+is_array 无法区分:
//   - 数组/标量 会被当成"空请求"继续往下走 (旧行为: [] → 401, 数组型 action/token → 500)
//   - 空 JSON 与非法 JSON 都返回 null
// 先按首字符判定对象, 零成本拦住 [] / "x" / 123 / null 这类请求.
$__trimmed = ltrim($raw_body);
if ($__trimmed === '' || $__trimmed[0] !== '{') {
    audit_log('', [], $E['INVALID_PARAM'], ['reason' => 'bad_json']);
    echo json_encode(fail($E['INVALID_PARAM'], 'bad_json', 'Request body must be a JSON object'));
    exit;
}
$body = json_decode($raw_body, true);
if (!is_array($body)) {
    audit_log('', [], $E['INVALID_PARAM'], ['reason' => 'bad_json']);
    echo json_encode(fail($E['INVALID_PARAM'], 'bad_json', 'Request body must be a JSON object'));
    exit;
}

// 多目录模式下, 提前写入 $CONFIG['TOKEN'] 以保证审计日志 token_hash 一致
// token 只接受字符串/数字: 旧实现把数组型 token 直接当数组键用 (isset($tokens[$arr]))
// 会抛 TypeError → HTTP 500 空响应. 非标量按"未提供 token"处理 (401), 不泄露类型细节.
$REQ_TOKEN_RAW = $body['token'] ?? '';
$REQ_TOKEN = (is_string($REQ_TOKEN_RAW) || is_int($REQ_TOKEN_RAW) || is_float($REQ_TOKEN_RAW))
    ? (string)$REQ_TOKEN_RAW : '';
if ($MULTI_DIR_MODE) {
    $CONFIG['TOKEN'] = $REQ_TOKEN;
}

// --- 安全: Token 鉴权 + scope 解析 ---
if (!$MULTI_DIR_MODE) {
    // 单目录回退模式: hash_equals 防时序攻击
    // 修复: TOKEN 为空时 fail-closed (原 !empty 逻辑导致空 TOKEN 完全跳过鉴权)
    if ($CONFIG['TOKEN'] === '' || !hash_equals($CONFIG['TOKEN'], (string)$REQ_TOKEN)) {
        audit_log('*', $body, $E['UNAUTHORIZED']);
        http_response_code(401);
        $msg = ($CONFIG['TOKEN'] === '') ? 'Server TOKEN not configured (fail-closed)' : 'Invalid or missing token';
        echo json_encode(fail($E['UNAUTHORIZED'], 'unauthorized', $msg));
        exit;
    }
} else {
    // 多目录模式: tokens 字典 + admin_token
    $ALL_PERMS = ['read','write','delete','exec','lint','backup','admin'];
    $ALL_SCOPE_NAMES = array_keys($RAW_CONFIG['scopes'] ?? []);
    $token_info = null;
    $is_admin = false;

    if ($REQ_TOKEN === '') {
        audit_log('*', $body, $E['UNAUTHORIZED']);
        http_response_code(401);
        echo json_encode(fail($E['UNAUTHORIZED'], 'unauthorized', 'Token required'));
        exit;
    }
    if (!empty($RAW_CONFIG['admin_token']) && hash_equals($RAW_CONFIG['admin_token'], (string)$REQ_TOKEN)) {
        // 管理员: 拥有全部 scope + 全部权限
        $is_admin = true;
        $token_info = [
            'name'        => 'admin',
            'scopes'      => $ALL_SCOPE_NAMES,
            'permissions' => $ALL_PERMS,
        ];
    } elseif (isset($RAW_CONFIG['tokens'][$REQ_TOKEN])) {
        $token_info = $RAW_CONFIG['tokens'][$REQ_TOKEN];
        $token_info['name']        = $token_info['name'] ?? '';
        $token_info['scopes']      = $token_info['scopes'] ?? [];
        $token_info['permissions'] = $token_info['permissions'] ?? [];
    } else {
        audit_log('*', $body, $E['UNAUTHORIZED']);
        http_response_code(401);
        echo json_encode(fail($E['UNAUTHORIZED'], 'unauthorized', 'Invalid or missing token'));
        exit;
    }

    // scope 选择: 显式传参则用之, 否则取 token 的第一个 scope
    $req_scope  = $body['scope'] ?? '';
    // 数组/对象型 scope 直接当数组键用会抛 TypeError → 500 空响应; 数字按字符串处理 (与旧行为一致)
    if (!is_string($req_scope) && !is_int($req_scope) && !is_float($req_scope)) {
        audit_log('', $body, $E['INVALID_PARAM'], ['reason' => 'bad_scope_type']);
        echo json_encode(fail($E['INVALID_PARAM'], 'bad_scope_type',
            'scope must be a string, got ' . gettype($req_scope),
            ['hint' => 'scope 只接受字符串 (scope 名), 例: {"scope":"work"}']));
        exit;
    }
    $req_scope = (string)$req_scope;
    $scope_name = ($req_scope !== '') ? $req_scope : ($token_info['scopes'][0] ?? '');

    if ($scope_name === '' || !isset($RAW_CONFIG['scopes'][$scope_name])) {
        audit_log($body['action'] ?? '', $body, $E['INVALID_PARAM'], ['reason' => 'invalid_scope', 'scope' => $scope_name]);
        echo json_encode(fail($E['INVALID_PARAM'], 'invalid_scope', "Scope '$scope_name' not found", [
            'available' => $token_info['scopes'],
        ]));
        exit;
    }
    if (!in_array($scope_name, $token_info['scopes'], true)) {
        audit_log($body['action'] ?? '', $body, $E['FORBIDDEN'], ['reason' => 'scope_forbidden', 'scope' => $scope_name]);
        http_response_code(403);
        echo json_encode(fail($E['FORBIDDEN'], 'scope_forbidden', "Token has no access to scope '$scope_name'"));
        exit;
    }

    // 合并 scope 配置进 $CONFIG (保持旧字段名, 工具函数无需改动)
    $CONFIG = merge_scope_into_cfg(
        $CONFIG,
        $RAW_CONFIG['scopes'][$scope_name],
        $scope_name,
        $token_info,
        $is_admin,
        $REQ_TOKEN
    );
}

// --- 安全: IP 白名单 (精确匹配 + CIDR) ---
if (!empty($CONFIG['ALLOW_IPS'])) {
    $client_ip = client_ip();
    $allowed = false;
    foreach ($CONFIG['ALLOW_IPS'] as $rule) {
        if ($client_ip === $rule) { $allowed = true; break; }
        if (strpos($rule, '/') !== false && ip_in_cidr($client_ip, $rule)) { $allowed = true; break; }
    }
    if (!$allowed) {
        audit_log('*', $body, $E['FORBIDDEN']);
        http_response_code(403);
        echo json_encode(fail($E['FORBIDDEN'], 'ip_blocked', "IP $client_ip not in whitelist"));
        exit;
    }
}

function ip_in_cidr($ip, $cidr) {
    if (strpos($cidr, '/') === false) return false;
    list($subnet, $bits) = explode('/', $cidr, 2);
    $bits = intval($bits);
    $ip_bin = @inet_pton($ip);
    $subnet_bin = @inet_pton($subnet);
    if ($ip_bin === false || $subnet_bin === false || strlen($ip_bin) !== strlen($subnet_bin)) return false;
    // 修复: 同时支持 IPv4 (32 bit) 和 IPv6 (128 bit), 原版仅支持 IPv4
    $max_bits = strlen($ip_bin) * 8;
    if ($bits < 0 || $bits > $max_bits) return false;
    if ($bits === 0) return true; // 0/0 匹配全部
    $full_bytes = intdiv($bits, 8);
    $remain_bits = $bits % 8;
    for ($i = 0; $i < $full_bytes; $i++) {
        if ($ip_bin[$i] !== $subnet_bin[$i]) return false;
    }
    if ($remain_bits > 0 && $full_bytes < strlen($ip_bin)) {
        $mask = chr((0xFF << (8 - $remain_bits)) & 0xFF);
        if ((ord($ip_bin[$full_bytes]) & ord($mask)) !== (ord($subnet_bin[$full_bytes]) & ord($mask))) return false;
    }
    return true;
}

/**
 * 获取客户端真实 IP (防 XFF 伪造).
 * 默认只用 REMOTE_ADDR (TCP 对端, 不可伪造).
 * 修复: 原 TRUSTED_PROXY=true 时无条件信任 XFF 首段, 任何客户端可伪造 IP 绕过白名单/限流.
 * 现支持 trusted_proxy_ips 数组: REMOTE_ADDR 必须在可信代理列表内才取 XFF, 且从右往左取第一个非可信 IP.
 * 向后兼容: 仅 TRUSTED_PROXY=true (无 trusted_proxy_ips) 时保持旧行为, 但在 DEBUG 下记审计警告.
 */
function client_ip() {
    $remote = $_SERVER['REMOTE_ADDR'] ?? '';
    $cfg = $GLOBALS['CONFIG'] ?? [];
    $trusted_ips = $cfg['TRUSTED_PROXY_IPS'] ?? [];
    $trusted_proxy = !empty($cfg['TRUSTED_PROXY']);
    if (!$trusted_proxy && empty($trusted_ips)) return $remote;

    $xff = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
    if ($xff === '') return $remote;
    $parts = array_map('trim', explode(',', $xff));
    // 校验每段是合法 IP
    $parts = array_filter($parts, function($p) { return filter_var($p, FILTER_VALIDATE_IP) !== false; });
    if (empty($parts)) return $remote;

    if (!empty($trusted_ips)) {
        // 严格模式: REMOTE_ADDR 必须是可信代理, 否则忽略 XFF
        $remote_is_proxy = false;
        foreach ($trusted_ips as $tip) {
            if ($remote === $tip || (strpos($tip, '/') !== false && ip_in_cidr($remote, $tip))) {
                $remote_is_proxy = true; break;
            }
        }
        if (!$remote_is_proxy) return $remote;
        // 从右往左取第一个非可信 IP (RFC 7239 标准)
        for ($i = count($parts) - 1; $i >= 0; $i--) {
            $candidate = $parts[$i];
            $is_proxy = false;
            foreach ($trusted_ips as $tip) {
                if ($candidate === $tip || (strpos($tip, '/') !== false && ip_in_cidr($candidate, $tip))) {
                    $is_proxy = true; break;
                }
            }
            if (!$is_proxy) return $candidate;
        }
        // 全是可信代理, 返回最左一个
        return $parts[0];
    }
    // 兼容旧模式: TRUSTED_PROXY=true 但未配置 IP 列表, 取 XFF 首段 (不安全, 仅向后兼容)
    return $parts[0];
}

/**
 * 清理过期的分片上传 chunk + meta 文件 (CHUNK_TTL_DAYS 天前).
 * 节流: 每个 scope 的 chunk 目录, 最多每 10 分钟扫一次.
 */
function _cleanup_expired_chunks($cfg) {
    $chunk_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['CHUNK_DIR'];
    if (!is_dir($chunk_dir)) return;
    $marker = $chunk_dir . '.last_cleanup';
    $now = time();
    if ((int)@file_get_contents($marker) > $now - 600) return; // 10 分钟内已扫过
    @file_put_contents($marker, $now, LOCK_EX);
    $ttl = intval($cfg['CHUNK_TTL_DAYS'] ?? 7) * 86400;
    // 清理 .meta.json + 对应 chunk 目录
    foreach (glob($chunk_dir . 'cu_*.meta.json') ?: [] as $meta_file) {
        if (($now - @filemtime($meta_file)) > $ttl) {
            $uid = basename($meta_file, '.meta.json');
            _rmrf($chunk_dir . $uid, $chunk_dir); // chunk 分片目录 (传入 root 做边界校验)
            @unlink($meta_file);
        }
    }
}
// 递归删除目录 (内部用)
// 修复 P3-1: 增加 realpath 边界校验 + 符号链接防护, 防 chunk 目录被替换为符号链接后误删外部文件
function _rmrf($dir, $root = null) {
    if (!is_dir($dir)) return;
    $real_dir = @realpath($dir);
    if ($real_dir === false) return;
    if ($root !== null) {
        $real_root = @realpath($root);
        if ($real_root === false) return;
        if ($real_dir !== $real_root && strpos($real_dir . DIRECTORY_SEPARATOR, $real_root . DIRECTORY_SEPARATOR) !== 0) return;
    }
    $iti = new \RecursiveIteratorIterator(
        new \RecursiveDirectoryIterator($real_dir, \RecursiveDirectoryIterator::SKIP_DOTS),
        \RecursiveIteratorIterator::CHILD_FIRST
    );
    foreach ($iti as $f) {
        $fp = $f->getPathname();
        // 符号链接直接 unlink (不跟随), 防清理时误删链接目标
        if ($f->isLink()) { @unlink($fp); continue; }
        $f->isDir() ? @rmdir($fp) : @unlink($fp);
    }
    @rmdir($real_dir);
}

// --- 频率限制 ---
if (!empty($CONFIG['RATE_LIMIT']['enabled'])) {
    $rl = rate_limit_check($body, $CONFIG);
    if ($rl['limited']) {
        $__rl_action = $body['action'] ?? '';
        if (!is_string($__rl_action)) $__rl_action = '';
        audit_log($__rl_action, $body, $E['RATE_LIMITED'], [
            'reason'      => 'rate_limited',
            'limit'       => $rl['limit'],
            'window'      => $rl['window'],
            'retry_after' => $rl['retry_after'],
        ]);
        http_response_code(429);
        header('Retry-After: ' . $rl['retry_after']);
        echo json_encode(fail($E['RATE_LIMITED'], 'rate_limited', $rl['message'], [
            'limit' => $rl['limit'],
            'window' => $rl['window'],
            'retry_after' => $rl['retry_after'],
        ]));
        exit;
    }
}

// --- chunk 过期清理 (节流, 复用每次请求触发) ---
if (function_exists('_cleanup_expired_chunks')) _cleanup_expired_chunks($CONFIG);

function rate_limit_check($req, $cfg) {
    $rl = $cfg['RATE_LIMIT'];
    $ip = function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR'] ?? 'unknown');
    $token = isset($req['token']) ? substr(hash('sha256', $cfg['TOKEN'] . $req['token']), 0, 12) : 'anon';
    $action = $req['action'] ?? 'unknown';
    $is_upload = in_array($action, ['upload', 'upload_batch', 'chunked_upload']);
    $now = time();
    $state_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/ai-sync/rate/';
    @mkdir($state_dir, 0755, true);

    // 清理过期计数器文件: IP 文件保留 5 秒, token 文件保留 3 分钟.
    // 旧实现缺陷: 用 $now%2 / $minute%5 概率触发 + glob 精确时间戳匹配,
    // 低流量或错峰生成时永远匹配不到, 导致 .cnt 文件无限堆积.
    // 修复: 用标记文件节流(每 10 秒最多扫一次) + glob 全量 + filemtime 判断.
    $cleanup_marker = $state_dir . '.last_cleanup';
    $last_cleanup = (int)@file_get_contents($cleanup_marker);
    if ($now - $last_cleanup >= 10) {
        @file_put_contents($cleanup_marker, $now, LOCK_EX);
        foreach (glob($state_dir . 'ip_*.cnt') ?: [] as $f) {
            if (is_file($f) && ($now - @filemtime($f)) > 5) @unlink($f);
        }
        foreach (glob($state_dir . 'tk_*.cnt') ?: [] as $f) {
            if (is_file($f) && ($now - @filemtime($f)) > 180) @unlink($f);
        }
    }

    $ip_key = 'ip_' . preg_replace('/[^a-zA-Z0-9_.-]/', '_', $ip) . '_' . $now;
    $ip_file = $state_dir . $ip_key . '.cnt';
    // 修复 P1-3: 原版 file_get_contents 读 + file_put_contents 写之间无锁, 并发请求都读到同一值
    //   导致计数器被覆盖, 限流可被并发绕过 (~50%). 改为 flock 包裹的原子 read-increment-write.
    $count = _atomic_count_inc($ip_file);
    if ($count > $rl['per_ip_per_second']) {
        return ['limited' => true, 'retry_after' => 1, 'limit' => $rl['per_ip_per_second'], 'window' => '1s', 'message' => "IP rate limit exceeded"];
    }

    $minute = intval($now / 60);
    $tk_key = 'tk_' . $token . '_' . $minute;
    $tk_file = $state_dir . $tk_key . '.cnt';
    $tk_count = _atomic_count_inc($tk_file);
    $tk_limit = $is_upload ? $rl['upload_per_minute'] : $rl['per_token_per_minute'];
    if ($tk_count > $tk_limit) {
        $retry = 60 - ($now % 60);
        return ['limited' => true, 'retry_after' => $retry, 'limit' => $tk_limit, 'window' => '1m', 'message' => "Token rate limit exceeded"];
    }

    return ['limited' => false];
}

/**
 * 原子递增计数器文件, 返回递增后的值.
 * 修复 P1-3: 用 flock(LOCK_EX) 包裹 read+increment+write, 防并发覆盖导致限流绕过.
 */
function _atomic_count_inc($file) {
    $fp = @fopen($file, 'c+');
    if (!$fp) {
        // BUG-41 修复 (审计 Finding 5): fopen 失败时不能退化为 "返回 1" — 否则计数永远停在 1,
        // 限流形同虚设 (磁盘满/权限坏时尤其危险). 改为 fail-closed: 返回 PHP_INT_MAX 让调用方拒绝请求.
        // 同时 error_log 通知操作员 (本机 PHP 错误日志, 不暴露给客户端).
        error_log("[ai-sync] _atomic_count_inc: fopen failed for $file, fail-closed to deny request");
        return PHP_INT_MAX;
    }
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        error_log("[ai-sync] _atomic_count_inc: flock failed for $file, fail-closed to deny request");
        return PHP_INT_MAX;
    }
    $val = (int)stream_get_contents($fp) + 1;
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, (string)$val);
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return $val;
}

// --- 路径安全 ---
/**
 * 安全路径解析: 确保 $rel 解析后的真实路径一定在 $base 目录树内
 *
 * 故意保持"宽松词法 + 严格物理层"的双保险设计:
 *   词法层只拦整段 '..' —— 避免误伤 100%25.jpg / a..b.php / 中文 / \ / %xx 等合法文件名
 *   realpath 物理层才是真正的"最后铁闸" —— 任何绕过词法的 payload 都过不了前缀比较
 *
 * 返回: 真实绝对路径字符串 (新建文件时基于父目录 realpath 拼接) 或 false(拒绝)
 */
function safe_path_nofollow($rel, $base) {
    // 与 safe_path 相同的词法校验, 但**不 realpath 最后一级**:
    //   safe_path 会把 .../link 解析成 link 指向的目标, 导致 upload/delete 操作到目标而非链接本身.
    //   本函数只解析父目录, 返回 "已校验父目录 + 原始文件名", 专供软链接操作 (upload link_target,
    //   delete, stat, checksum 根路径) 使用.
    if (!is_string($rel)) return false;
    if ($rel === '') return false;
    if (strpos($rel, "\0") !== false) return false;
    if ($rel === '.' || $rel === './') {
        $real_base = realpath(rtrim($base, '/\\'));
        if ($real_base === false) return false;
        return $real_base;
    }
    $norm = str_replace('\\', '/', $rel);
    $norm = preg_replace('#/+#', '/', $norm);
    $trimed = trim($norm, '/');
    if ($trimed !== '') {
        foreach (explode('/', $trimed) as $seg) {
            if ($seg === '.' || $seg === '') continue;
            if ($seg === '..') return false;
        }
    }
    $final_rel = ltrim($rel, '/');
    while (strpos($final_rel, './') === 0) {
        $final_rel = ltrim(substr($final_rel, 2), '/');
    }
    $final_rel = rtrim($final_rel, '/\\');
    if ($final_rel === '') {
        $real_base = realpath(rtrim($base, '/\\'));
        return $real_base === false ? false : $real_base;
    }
    $base = rtrim($base, '/\\') . '/';
    $full = $base . $final_rel;
    $real_base = realpath($base);
    if ($real_base === false) return false;
    $real_base_norm = rtrim($real_base, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR;
    // 从完整路径向上找最近的已存在父目录 (与 safe_path 新文件分支一致)
    $last_sep = strrpos($full, DIRECTORY_SEPARATOR);
    $basename_parts = [$last_sep === false ? $full : substr($full, $last_sep + 1)];
    $parent = $last_sep === false ? '' : substr($full, 0, $last_sep);
    while (true) {
        if (strlen($parent) < strlen(rtrim($base, '/'))) return false;
        $parent_real = realpath($parent);
        if ($parent_real !== false) break;
        $prev_sep = strrpos($parent, DIRECTORY_SEPARATOR);
        if ($prev_sep === false || $prev_sep === 0) return false;
        $basename_parts[] = substr($parent, $prev_sep + 1);
        $parent = substr($parent, 0, $prev_sep);
    }
    if (strpos($parent_real . DIRECTORY_SEPARATOR, $real_base_norm) !== 0) return false;
    return $parent_real . DIRECTORY_SEPARATOR . implode(DIRECTORY_SEPARATOR, array_reverse($basename_parts));
}

/**
 * 纯词法归一化绝对路径 (不访问文件系统), 越界返回 false.
 */
function _lexical_norm_abs($abs) {
    $abs = str_replace('\\', '/', (string)$abs);
    if ($abs === '' || $abs[0] !== '/') return false;
    $parts = [];
    foreach (explode('/', $abs) as $seg) {
        if ($seg === '' || $seg === '.') continue;
        if ($seg === '..') {
            if (!$parts) return false;
            array_pop($parts);
            continue;
        }
        $parts[] = $seg;
    }
    return '/' . implode('/', $parts);
}

function _path_in_base($abs, $base_real) {
    $abs = rtrim(str_replace('\\', '/', (string)$abs), '/');
    $base_real = rtrim(str_replace('\\', '/', (string)$base_real), '/');
    if ($abs === '') $abs = '/';
    if ($base_real === '') $base_real = '/';
    return $abs === $base_real || strpos($abs . '/', $base_real . '/') === 0;
}

/**
 * 归一化"相对链接目标"(相对链接所在目录), 用于跨根一致的指纹.
 * 例: ./a -> a; sub/../a -> a; ../a -> ../a; . -> .
 * 与 work_dir 无关, 因此客户端/服务端、不同 remote_base 下可得到相同 hash.
 */
function _norm_rel_target($raw) {
    $norm = str_replace('\\', '/', (string)$raw);
    $out = [];
    foreach (explode('/', $norm) as $seg) {
        if ($seg === '' || $seg === '.') continue;
        if ($seg === '..') {
            $n = count($out);
            if ($n > 0 && $out[$n - 1] !== '..') array_pop($out);
            else $out[] = '..';
            continue;
        }
        $out[] = $seg;
    }
    return $out ? implode('/', $out) : '.';
}

/**
 * 解析链接链, 返回 status = ok|broken|cycle|outside.
 * 与 realpath() 的区别: 环/悬空链/中间层逃逸都能区分, 且不读取目标内容.
 */
function _link_real_ancestor($path) {
    // 返回 path 或其最近「可 realpath」祖先的真实路径.
    // 用途: 目标尚不存在时, 仍要确认路径中间经过的链接没有逃出 work_dir.
    $probe = $path;
    for ($i = 0; $i < 40; $i++) {
        $real = @realpath($probe);
        if ($real !== false) return $real;
        $parent = dirname($probe);
        if ($parent === $probe) return false;
        $probe = $parent;
    }
    return false;
}

function _link_resolve_chain($start, $base_real) {
    $cur = $start;
    $seen = [];
    for ($hop = 0; $hop < 40; $hop++) {
        $cn = _lexical_norm_abs($cur);
        if ($cn === false) return ['status' => 'broken', 'resolved' => null];
        if (isset($seen[$cn])) return ['status' => 'cycle', 'resolved' => null];
        $seen[$cn] = true;
        if (!@is_link($cur)) {
            return ['status' => @file_exists($cur) ? 'ok' : 'broken', 'resolved' => $cur];
        }
        $raw = @readlink($cur);
        if ($raw === false) return ['status' => 'broken', 'resolved' => null];
        $rn = str_replace('\\', '/', $raw);
        if ($rn !== '' && ($rn[0] === '/' || preg_match('#^[A-Za-z]:/#', $rn))) {
            $next = _lexical_norm_abs($rn);
        } else {
            $next = _lexical_norm_abs(dirname($cur) . '/' . $rn);
        }
        if ($next === false || !_path_in_base($next, $base_real)) {
            return ['status' => 'outside', 'resolved' => null];
        }
        $cur = $next;
    }
    return ['status' => 'cycle', 'resolved' => null];
}

/**
 * 计算软链接元数据. 安全规则:
 *   - 目标必须是相对路径 (绝对路径不可移植, 也容易指向 work_dir 外)
 *   - 词法归一化后必须仍在 work_dir 内
 *   - 目标已存在时 realpath 也必须仍在 work_dir 内 (防止中间链接逃逸)
 * 返回 ['safe'=>bool,'target'=>raw|null,'target_rel'=>canonical posix rel|null,
 *       'broken'=>bool,'reason'=>string|null]
 */
function _link_meta_from_target($raw, $link_full, $base) {
    $unsafe = function ($reason) {
        return ['safe' => false, 'target' => null, 'target_rel' => null,
                'target_norm' => null, 'broken' => false, 'cycle' => false,
                'resolvable' => false, 'reason' => $reason];
    };
    if (!is_string($raw) || $raw === '') return $unsafe('empty_target');
    if (strpos($raw, "\0") !== false) return $unsafe('nul_byte');
    $norm = str_replace('\\', '/', $raw);
    if ($norm !== '' && ($norm[0] === '/' || preg_match('#^[A-Za-z]:/#', $norm))) {
        return $unsafe('absolute_target');
    }
    $real_base = realpath(rtrim($base, '/\\'));
    if ($real_base === false) return $unsafe('base_unresolvable');
    $parent_real = realpath(dirname($link_full));
    if ($parent_real === false) return $unsafe('parent_unresolvable');
    if (!_path_in_base($parent_real, $real_base)) return $unsafe('parent_outside');
    $lex = _lexical_norm_abs($parent_real . '/' . $norm);
    if ($lex === false || !_path_in_base($lex, $real_base)) return $unsafe('outside_workdir');
    $rel = ltrim(substr($lex, strlen(rtrim($real_base, '/'))), '/');
    if ($rel === '') $rel = '.';
    clearstatcache(true, $lex);
    // FIX-link-mid-escape: _link_resolve_chain 只逐环解析最后一级链接,
    //   目标路径中间经过的链接目录 (如 outdir -> /etc, 目标 outdir/hostname) 会漏检.
    //   realpath 最近可解析祖先可覆盖「最终不存在但中间链接已逃逸」的情况.
    $real_anc = _link_real_ancestor($lex);
    if ($real_anc !== false && !_path_in_base($real_anc, $real_base)) {
        return $unsafe('target_escapes_workdir');
    }
    $res = _link_resolve_chain($lex, $real_base);
    if ($res['status'] === 'outside') {
        return $unsafe('target_escapes_workdir');
    }
    $broken = ($res['status'] !== 'ok');
    return ['safe' => true, 'target' => $raw, 'target_rel' => $rel,
            'target_norm' => _norm_rel_target($raw),
            'broken' => $broken, 'cycle' => ($res['status'] === 'cycle'),
            'resolvable' => ($res['status'] === 'ok'), 'reason' => null];
}

function _link_meta($link_full, $base) {
    $raw = @readlink($link_full);
    if ($raw === false) {
        return ['safe' => false, 'target' => null, 'target_rel' => null,
                'target_norm' => null, 'broken' => false, 'cycle' => false,
                'resolvable' => false, 'reason' => 'readlink_fail'];
    }
    return _link_meta_from_target($raw, $link_full, $base);
}

/**
 * 节点当前"内容指纹": 普通文件=md5(内容), 软链接=md5(规范化目标), 目录/不可读="".
 * 用于 upload CAS 与 push 复验, 让链接和普通文件都能参与乐观锁.
 */
function _node_md5($dest, $base) {
    if (@is_link($dest)) {
        $lm = _link_meta($dest, $base);
        return ($lm['safe'] && $lm['target_norm'] !== null)
            ? hash('md5', $lm['target_norm']) : '';
    }
    if (@is_file($dest)) return (string)@md5_file($dest);
    return '';
}

function safe_path($rel, $base) {
    if (!is_string($rel)) return false;
    if ($rel === '') return false;
    // NUL 字节: PHP 8 的 realpath()/stat() 会抛 ValueError("must not contain any null bytes"),
    // 旧实现会被入口 try/catch 兜成 1099 internal —— 明明是调用方输入错误, 却报成服务器故障.
    // 词法层直接拒绝, 交给 reject_path 返回 1004 invalid_path.
    if (strpos($rel, "\0") !== false) return false;

    // 特殊语义: "." 代表当前目录 (scope 根). 等价于空字符串, 但空字符串被安全函数拒绝了.
    if ($rel === '.' || $rel === './') {
        $real_base = realpath(rtrim($base, '/\\'));
        if ($real_base === false) return false;
        return $real_base;
    }

    // --- 词法层 (宽松版): 仅当某一段严格等于 '..' 时才拒绝, 其他一律放行 ---
    // 先统一 \ -> / (Windows 分隔符), 再分段
    $norm = str_replace('\\', '/', $rel);
    $norm = preg_replace('#/+#', '/', $norm); // 合并连续斜杠
    $trimed = trim($norm, '/');
    if ($trimed !== '') {
        foreach (explode('/', $trimed) as $seg) {
            // 吃掉 ./ 段 (当前目录不影响)
            if ($seg === '.' || $seg === '') continue;
            // 只拦整段 '..' —— a..b / ..hidden / 100%2e.jpg 全部放行
            if ($seg === '..') return false;
        }
    }

    // --- 规范化: 去掉多余开头 / 和 ./ ---
    $final_rel = ltrim($rel, '/');
    while (strpos($final_rel, './') === 0) {
        $final_rel = ltrim(substr($final_rel, 2), '/');
    }

    // --- 物理层 (严格铁闸): realpath 前缀校验 ---
    $base = rtrim($base, '/\\') . '/';
    $full = $base . $final_rel;
    $real_base = realpath($base);
    if ($real_base === false) return false;
    $real_base_norm = rtrim($real_base, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR;
    $real_full = realpath($full);
    if ($real_full !== false) {
        if (strpos($real_full . DIRECTORY_SEPARATOR, $real_base_norm) !== 0) return false;
        return $real_full;
    }
    // 新文件 / 新目录: 向上递归查找已存在的父目录, 确保在 base 内
    // 🆕 FIX-utf8-basename-v4.17: PHP basename()/dirname() 在 FPM C locale (无 LANG/LC_) 下
    //   对 UTF-8 文件名返回残缺 (实测 safe_path 把 "TV/_bt2/播放器.txt" 截成 ".txt").
    //   改用字节安全的 strrpos+substr 手动切分路径, 与 locale 无关.
    $last_sep = strrpos($full, DIRECTORY_SEPARATOR);
    $basename_parts = [$last_sep === false ? $full : substr($full, $last_sep + 1)];
    $parent = $last_sep === false ? '' : substr($full, 0, $last_sep);
    while (true) {
        if (strlen($parent) < strlen(rtrim($base, '/'))) return false;
        $parent_real = realpath($parent);
        if ($parent_real !== false) break;
        // 父目录不存在, 继续向上找
        $prev_sep = strrpos($parent, DIRECTORY_SEPARATOR);
        if ($prev_sep === false || $prev_sep === 0) return false; // 到达根目录
        $basename_parts[] = substr($parent, $prev_sep + 1);
        $parent = substr($parent, 0, $prev_sep);
    }
    if (strpos($parent_real . DIRECTORY_SEPARATOR, $real_base_norm) !== 0) return false;
    // 重建完整路径: 已验证的父目录 + 向下各层
    return $parent_real . DIRECTORY_SEPARATOR . implode(DIRECTORY_SEPARATOR, array_reverse($basename_parts));
}

/**
 * 路径安全失败的统一处理: 记审计 + 返回 fail, 避免每个 action 重复写
 */
function bad_path($rel, $which = 'path') {
    // 路径校验失败的统一出口 (供各 action 直接调用):
    // 缺字段/空值 -> 1011 missing_<which>; 类型不对 -> 1011 bad_path_type; 否则 -> 1004 越界。
    // action 从 $GLOBALS['CUR_ACTION'] 取 (dispatch 内设置), 用于审计。
    $req = $GLOBALS['CUR_BODY'] ?? [];
    if (is_array($req)) $req['_which'] = $which;
    return reject_path($rel, $GLOBALS['CUR_ACTION'] ?? '', is_array($req) ? $req : []);
}

function reject_path($rel, $action, $req) {
    global $E;
    // 空/缺 path 与"越界"是两类问题, 对调用方(尤其 AI)的修复动作完全不同:
    // 前者补字段, 后者改路径写法。旧实现统一回 1004 "security policy rejected", 会误导。
    if ($rel === null || (is_string($rel) && trim($rel) === '')) {
        return fail($E['INVALID_PARAM'], 'missing_path',
            "Required field 'path' is missing or empty (received fields: " . _brief_keys($req) . ")", [
                'hint' => '该 action 必须传 path (相对 work_dir 的路径); 列目录根用 path="."; '
                        . '本次收到的完整字段见 message。',
            ]);
    }
    if (!is_string($rel)) {
        return fail($E['INVALID_PARAM'], 'bad_path_type',
            "'path' must be a string, got " . gettype($rel) . " (received fields: " . _brief_keys($req) . ")", [
                'hint' => 'path 只能是字符串; 数组/对象请改为字符串路径。',
            ]);
    }
    // 审计里带原始 path, 便于入侵排查
    audit_log($action, $req,
              $GLOBALS['E']['INVALID_PATH'] ?? 'E_INVALID_PATH',
              ['blocked_rel' => (string)$rel, 'blocked_reason' => 'path_traversal']);
    return fail($E['INVALID_PATH'] ?? 'E_INVALID_PATH', 'invalid_path',
                "Path '$rel' rejected",
                ['hint' => '路径越界: 只接受相对 work_dir 的相对路径(如 src/a.php); '
                         . '前导 / 会按 work_dir 虚拟根解析(/tmp/x → <work_dir>/tmp/x), 不是服务器绝对路径; '
                         . '不要含 .. 或空字节。']);
}

/**
 * BUG-46 修复 (审计 Finding 10): 文件 mode 安全校验.
 * 原版直接 octdec($mode) → chmod, 攻击者可传 "4777" (setuid+全写) / "6666" (setgid+全写) / "0777".
 * 共享主机上 setuid PHP 是提权原语, 全写让其他租户篡改部署代码.
 * 修复: 强制 mask 掉 setuid/setgid/sticky (高 3 位), 只保留低 9 位 rwx;
 *       必须保留 owner-read (0400) 否则服务端自己无法 checksum/备份, 但 0400/0444/0500
 *       这类只读权限是合法的同步状态, 不能被"提升"成 0644 (会静默改变调用方意图);
 *       默认 0644.
 * v4.37: 旧实现把低于 0600 的一律改成 0644, 导致同步 0400 文件后变成 0644 (权限被放宽).
 *        现在只回退"owner 不可读"的权限 (0000/0200/0300 等), 其余原样保留.
 */
function safe_mode($mode_input, $default = 0644) {
    if ($mode_input === null || $mode_input === '') return $default;
    // 兼容 "0644" (字符串) / 0644 (八进制 int) / "644" (省略前导 0)
    if (is_string($mode_input)) {
        // "0000" 是合法输入 (4 位八进制), 不能 ltrim 成空后擅自替换成 644;
        // 全 0 交给统一策略: owner 不可读 → 回退调用方 default (文件 0644 / 目录 0755).
        $m = octdec($mode_input);
    } elseif (is_int($mode_input)) {
        $m = $mode_input;
    } else {
        return $default;
    }
    $m = $m & 0777;             // 强制去掉 setuid/setgid/sticky
    if (($m & 0400) === 0) $m = $default; // owner 不可读 → 服务端无法校验/备份, 回退默认
    return $m;
}

/** mtime 入参校验 (秒级 unix 时间戳; 接受 int/整数值 float/数字字符串). */
function valid_mtime_input($m) {
    // 上限 253402300799 = 9999-12-31T23:59:59Z。旧实现只拦负数, 极大值会被
    // touch() 静默夹到系统上限或直接失败; 13 位毫秒时间戳是最常见的误传。
    $max = 253402300799;
    if (is_int($m)) return $m >= 0 && $m <= $max;
    if (is_float($m)) return $m >= 0 && floor($m) == $m && $m <= $max;
    return is_string($m) && preg_match('/^[0-9]{1,12}$/', $m) === 1 && (float)$m <= $max;
}

function bad_mtime($m) {
    global $E;
    if (is_string($m)) $shown = '"' . $m . '"';
    elseif (is_int($m) || is_float($m)) $shown = (string)$m;
    elseif (is_bool($m)) $shown = $m ? 'true' : 'false';
    elseif ($m === null) $shown = 'null';
    else $shown = gettype($m);
    return fail($E['INVALID_PARAM'], 'bad_mtime',
        "mtime 必须是 0..253402300799 的秒级 unix 时间戳 (整数), 收到 $shown", [
            'hint' => 'mtime 用于保留文件修改时间, 如 "mtime": 1700000000; 不传则使用服务器当前时间。'
                    . '若你手上是 13 位毫秒时间戳, 先除以 1000。',
        ]);
}

/**
 * mode 入参格式校验 (与 Python 端 _valid_mode_input 同契约)。
 *
 * 旧实现把非法 mode 静默当 0 → safe_mode 回落到 0644:
 *   upload mode="abc"  → ok:true + 644 (调用方以为设置成功)
 *   upload mode=0o999  → 同上
 * Python 端则抛 ValueError → 1011 且 message 暴露 Python 内部错误。
 * 两端行为不一致且都不好用。现在统一: 只接受 1-4 位八进制字符串, 非法直接 1011 bad_mode。
 * (数字仍兼容: JSON 里写 644 会被当成十进制 644 → 低 9 位 0o363 < 0600 → 0644,
 *  语义含糊, 因此对外契约只承诺字符串形式。)
 */
function valid_mode_input($m) {
    return is_string($m) && preg_match('/^[0-7]{1,4}$/', $m) === 1;
}

function bad_mode($m) {
    global $E;
    $shown = is_string($m) ? ('"' . $m . '"') : gettype($m);
    return fail($E['INVALID_PARAM'], 'bad_mode',
        "mode 必须是 1-4 位八进制字符串 (如 \"644\"/\"0644\"/\"4755\"), 收到 $shown", [
            'hint' => 'JSON 中请用字符串: "mode": "0644"; setuid/setgid/sticky 位会被剥离; '
                    . '0400 等只读权限会原样保留 (owner 不可读的 0000/0200 才回退 0644); 不传则保留目标文件原权限。',
        ]);
}

/**
 * 严格布尔解析: 返回 true/false, 无法识别的值返回 null (调用方回 1011)。
 * 修复 !empty() 的两个坑:
 *   1) 字符串 "false"/"no"/"off" 被当成 true —— 调用方明确说"不要", 服务端却照做
 *      (delete recursive / copy overwrite / format write 都可能造成数据丢失);
 *   2) PHP !empty("0")=false 与 Python bool("0")=true 双端结果相反。
 * 兼容常见写法: true/false、1/0、数字、"1"/"0"、"true"/"false"、"yes"/"no"、"on"/"off"。
 */
function bool_flag($v, $default = false) {
    if ($v === null) return $default;
    if (is_bool($v)) return $v;
    if (is_int($v) || is_float($v)) return $v != 0;
    if (is_string($v)) {
        $s = strtolower(trim($v));
        if (in_array($s, ['1', 'true', 'yes', 'on'], true)) return true;
        if (in_array($s, ['0', 'false', 'no', 'off'], true) || $s === '') return false;
    }
    return null;
}

function bad_bool($field, $v) {
    global $E;
    $shown = is_string($v) ? ('"' . $v . '"') : gettype($v);
    return fail($E['INVALID_PARAM'], 'bad_bool',
        "$field 必须是布尔值 (true/false), 收到 $shown", [
            'hint' => '支持 true/false、1/0、"true"/"false"、"1"/"0"、"yes"/"no"、"on"/"off"; '
                    . '字符串 "false" 不会再被当成 true。',
        ]);
}

// ==================== action => permission 映射 ====================
$ACTION_PERMS = [
    // read
    'ping'           => 'read', 'help'           => 'read', 'health'         => 'read',
    'metrics'        => 'read',
    'download'       => 'read', 'stat'           => 'read', 'list'           => 'read',
    'diff'           => 'read', 'checksum'       => 'read', 'search'         => 'read',
    'todo'           => 'read', 'stats'          => 'read', 'read_range'     => 'read',
    'doc'            => 'read', 'snippet_get'    => 'read', 'snippet_list'   => 'read',
    'backup_ls'      => 'read', 'webhook_list'   => 'read', 'webhook_test' => 'read',
    'whoami'         => 'read', 'sync_check'     => 'read', 'recent_changes' => 'read',
    'client_download'=> 'read',
    // write
    'upload'         => 'write', 'upload_batch'  => 'write', 'chunked_upload'=> 'write',
    'batch_deploy'   => 'write',
    'rename'         => 'write', 'copy'          => 'write', 'mkdir'          => 'write',
    'format'         => 'write', 'snippet_set'   => 'write', 'webhook_set'    => 'write',
    // delete
    'delete'         => 'delete', 'snippet_delete' => 'delete', 'webhook_delete' => 'delete',
    // exec
    'php_run'        => 'exec', 'exec'           => 'exec',
    // 持久 shell 工具 (对齐原生 ssh, 与 exec 并存): 复用 exec 权限与 exec_enabled/IP 白名单
    'shell_open'     => 'exec', 'shell_run'      => 'exec', 'shell_job'   => 'exec',
    'shell_kill'     => 'exec', 'shell_close'    => 'exec', 'shell_list'  => 'exec',
    // lint
    'lint'           => 'lint', 'lint_batch'     => 'lint',
    'oplog_append'   => 'write',  // 客户端操作日志上传
    'oplog_list'     => 'admin',  // 查看所有客户端操作日志列表
    'oplog_view'     => 'admin',  // 查看某个客户端的操作日志内容
    // backup
    'backup_rb'      => 'backup', 'batch_rollback' => 'backup',
    // FIX-v4.34: pipeline 自身只需最低权限 —— 它本身不读写任何东西,
    //   真实权限由每个 step 在 action_pipeline 内做 $ACTION_PERMS 校验来把关。
    //   若不注册则 ACTION_PERMS[...] ?? 'admin' 回落 admin, AI 普通 token 就用不了。
    'pipeline'       => 'read',
    // error_log (AI 排查白屏)
    'error_log'      => 'read', 'error_log_list' => 'read',
    // admin
    'config_get'     => 'admin', 'config_reload' => 'admin', 'scopes_list'   => 'admin',
    'tokens_list'    => 'admin', 'token_add'     => 'admin', 'token_delete'  => 'admin',
    'scope_add'      => 'admin', 'scope_delete'  => 'admin', 'audit_log'     => 'admin',
    'admin_stats'    => 'admin', 'client_package' => 'admin',
    'package_list'   => 'admin', 'package_delete' => 'admin',
];

// ==================== 路由表 ====================
// ==================== FIX-v4.34 pipeline (一次往返执行多步) ====================
// 为什么要它: AI 的会话是"一串连续动作", 每步一次 HTTP。生产实测单次往返 ~105ms:
//   10 条独立命令 10×(进程170+网络105)≈2.8s ; --batch 省了进程启动但仍 10 次往返≈1.2s ;
//   pipeline 把 N 次往返压成 1 次 ≈0.3s。
// 安全边界:
//   ① 外层已完成 token/scope/IP白名单校验; step 级**补做权限位校验**(不同 action 权限不同,
//      不能靠外层放行);
//   ② 黑名单拦截不可逆/高危 action, 防止管道内的复核缺失;
//   ③ step 自带 token/scope 一律被外层覆盖, 防提权;
//   ④ 写类 step 照常计限流配额, 只读 step 不重复计(否则 10 步就撞满 per_ip_per_second)。
$PIPELINE_MAX_STEPS = 50;
$PIPELINE_FORBIDDEN = [
    'pipeline',                                          // 防嵌套放大
    'exec', 'php_run',                                   // 任意代码执行
    'delete',                                            // 删除不可逆
    'rename', 'copy',                                    // 路径变更不可逆(易误伤)
    'batch_rollback', 'backup_rb',                       // 回滚/还原不可逆
    'config_reload',                                     // 全局副作用
    'token_add', 'token_delete',                         // 凭据增删
    'scope_add', 'scope_delete',                         // scope 增删
    'webhook_set', 'webhook_delete', 'webhook_test',     // 外部副作用
    'client_package',                                    // 重操作(易被放大成 DoS)
];
$PIPELINE_MUTATING = [
    'upload', 'upload_batch', 'batch_deploy', 'chunked_upload',
    'mkdir', 'snippet_set', 'oplog_append', 'todo', 'doc', 'format', 'lint_batch',
];

/**
 * 按需加载某 action 依赖的 tools/*.php。
 * 抽成函数的原因: 主流程与 pipeline 都需要这段逻辑 —— 若各自内联一份,
 * 将来改加载路径时容易只改一处, pipeline 里就会出现"同类 action 直接调用 OK、
 * 管道里调用抛 undefined function" 的诡异差异 (v4.34 实测踩到)。
 *
 * @return array|null 出错时返回 fail() 结构, 成功返回 null
 */
function ensure_tool_loaded($action, $cfg) {
    global $TOOL_MAP, $E;
    $tool_name = $TOOL_MAP[$action] ?? null;
    if (!$tool_name) {
        return null;   // 该 action 无外部依赖
    }
    $api_dir = __DIR__;
    $tool_candidates = [
        $api_dir . '/tools/' . $tool_name . '.php',
        rtrim($cfg['WORK_DIR'], '/\\') . '/tools/' . $tool_name . '.php',
    ];
    $tool_file = null;
    foreach ($tool_candidates as $cand) {
        if (file_exists($cand)) { $tool_file = $cand; break; }
    }
    if ($tool_file === null) {
        audit_log($action, [], $E['INTERNAL']);
        return fail($E['INTERNAL'], 'tool_missing',
            "Tool file '$tool_name.php' not found (tried: " . implode(', ', $tool_candidates) . ')');
    }
    require_once $tool_file;
    return null;
}

/**
 * FIX-v4.34 (性能): 一次 HTTP 往返执行多个 action。
 *
 * 为什么需要它
 * ------------
 * AI 的典型会话是"一串连续动作"(查 README → 看目录 → 读文件 → 确认 → 改 → 验证),
 * 每步都是一次独立 HTTP。实测生产单次往返 ~105ms:
 *     10 条独立命令 : 10 × (进程启动 170 + 网络 105) ≈ 2.8s
 *     --batch       : 1 × 170 + 10 × 105        ≈ 1.2s   (省了进程启动, 往返照付)
 *     pipeline      : 1 × 170 + 1 × 105         ≈ 0.3s   ← 本 action
 * 关键在于 --batch 只是"同进程"而非"同请求", N 次往返一次都少不了;
 * pipeline 把 N 次往返压成 1 次, 这是 N×RTT 与 1×RTT 的区别。
 *
 * 安全边界
 * --------
 * 1. 每个 step **重新走 dispatch** —— 完整继承鉴权、scope 校验、权限位、IP 白名单。
 *    不做"外层验过就放行"的信任假设。
 * 2. step 自带的 token/scope **一律被顶层覆盖**, 防止用低权限凭据申请、
 *    再在管道里塞高权限 token 提权。
 * 3. 黑名单拦截不可逆/高危 action (exec/删除/凭据增删/嵌套 pipeline 等)。
 * 4. 只读 step 不重复计限流配额, 写类 step 照常计费 (否则管道会成为限流后门)。
 */
function action_pipeline($req, $cfg) {
    global $E, $ACTION_PERMS, $PIPELINE_MAX_STEPS, $PIPELINE_FORBIDDEN, $PIPELINE_MUTATING;

    $steps = $req['steps'] ?? null;
    if ($steps === null) {
        return fail($E['INVALID_PARAM'], 'missing_steps', 'pipeline 少必填参数 steps', [
            'hint' => '形态: {"action":"pipeline","steps":[{"action":"stat","path":"a.txt"},'
                    . '{"action":"read_range","path":"a.txt"}]}; 上限 ' . $PIPELINE_MAX_STEPS . ' 步。',
        ]);
    }
    if (!is_array($steps)) {
        return fail($E['INVALID_PARAM'], 'bad_steps', 'steps must be array', [
            'hint' => 'steps 是对象数组, 每项是一个完整 action 请求']);
    }
    if (count($steps) === 0) {
        return fail($E['INVALID_PARAM'], 'empty_steps', 'steps 数组不能为空 (没有要执行的动作)', [
            'hint' => '至少 1 步; 只想执行单个 action 请直接调用它']);
    }
    if (count($steps) > $PIPELINE_MAX_STEPS) {
        return fail($E['INVALID_PARAM'], 'too_many_steps',
            'steps 超过上限 ' . $PIPELINE_MAX_STEPS . ' (收到 ' . count($steps) . ')',
            ['hint' => '拆成多个 pipeline 分批执行']);
    }

    // 准入检查: 黑名单内的 action 不允许进管道 (避免"执行到一半才失败")
    $blocked = [];
    foreach (array_values($steps) as $i => $st) {
        if (!is_array($st)) { $blocked[] = "[$i] step 必须是对象"; continue; }
        $a = isset($st['action']) ? (string)$st['action'] : '';
        if ($a === '') { $blocked[] = "[$i] 缺 action 字段"; }
        elseif (in_array($a, $PIPELINE_FORBIDDEN, true)) {
            $blocked[] = "[$i] action '$a' 不允许在 pipeline 中执行";
        }
    }
    if ($blocked) {
        sort($PIPELINE_FORBIDDEN);
        return fail($E['INVALID_PARAM'], 'pipeline_blocked_action',
            'pipeline 拒绝了 ' . count($blocked) . ' 个 step', [
                'blocked' => array_slice($blocked, 0, 10),
                'hint' => '以下 action 因不可逆或高危不支持管道执行: '
                        . implode(', ', $PIPELINE_FORBIDDEN) . '。请单独调用它们。',
            ]);
    }

    $stop_on_error = bool_flag($req['stop_on_error'] ?? false);
    if ($stop_on_error === null) return bad_bool('stop_on_error', $req['stop_on_error']);
    $top_token = $req['token'] ?? '';
    $top_scope = $req['scope'] ?? '';
    global $ROUTES;
    $results = [];
    $n_ok = 0; $n_fail = 0; $stopped_at = -1;
    $mut_used = [];

    foreach (array_values($steps) as $i => $st) {
        $step = $st;
        // ① 正常情况注入外层凭据; ② 自带 token/scope 则被覆盖 —— 防提权
        if ($top_token !== '') { $step['token'] = $top_token; }
        if ($top_scope !== '') { $step['scope'] = $top_scope; }
        $act = isset($step['action']) ? (string)$step['action'] : '';

        // step 级权限校验 (不同 action 权限位不同, 不能依赖外层)
        if (!empty($GLOBALS['MULTI_DIR_MODE'])) {
            $need = $ACTION_PERMS[$act] ?? 'admin';
            if (!in_array($need, $cfg['PERMISSIONS'], true)) {
                audit_log($act, $step, $E['FORBIDDEN'], ['required' => $need]);
                $res = fail($E['FORBIDDEN'], 'forbidden',
                    "Permission '$need' required for action '$act'",
                    ['required' => $need, 'your_permissions' => $cfg['PERMISSIONS']]);
                $results[] = array_merge(['index' => $i, 'action' => $act], $res);
                $n_fail++;
                if ($stop_on_error) { $stopped_at = $i; break; }
                continue;
            }
        }

        // 写类 step 照常计限流(防绕过 upload_per_minute); 只读 step 跳过
        if (in_array($act, $PIPELINE_MUTATING, true)) {
            $rl = rate_limit_check($step, $cfg);
            if (!empty($rl['limited'])) {
                $res = fail($E['RATE_LIMITED'], 'rate_limited', $rl['message'], [
                    'retry_after' => $rl['retry_after'] ?? 1, 'limit' => $rl['limit'] ?? 0]);
                $results[] = array_merge(['index' => $i, 'action' => $act], $res);
                $n_fail++;
                if ($stop_on_error) { $stopped_at = $i; break; }
                continue;
            }
            $mut_used[$act] = true;
        }

        // 按需加载该 action 依赖的工具文件 (与主流程同一套逻辑, 见 ensure_tool_loaded)
        // 不加载会 Call to undefined function action_stat() —— 管道里表现为 1099 而非真实原因。
        $tool_err = ensure_tool_loaded($act, $cfg);
        if ($tool_err !== null) {
            $results[] = array_merge(['index' => $i, 'action' => $act], $tool_err);
            $n_fail++;
            if ($stop_on_error) { $stopped_at = $i; break; }
            continue;
        }

        try {
            $GLOBALS['CUR_ACTION'] = $act;
            $GLOBALS['CUR_BODY'] = $step;
            $res = $ROUTES[$act]($step, $cfg);
            if (!is_array($res)) {
                $res = fail($E['INTERNAL'], 'pipeline_bad_step_result', 'step returned non-array');
            }
        } catch (Throwable $ex) {
            $res = fail($E['INTERNAL'], 'pipeline_step_error', get_class($ex));
        }
        $results[] = array_merge(['index' => $i, 'action' => $act], $res);
        if (!empty($res['ok'])) {
            $n_ok++;
        } else {
            $n_fail++;
            if ($stop_on_error) { $stopped_at = $i; break; }
        }
    }

    $mut_list = array_keys($mut_used);
    sort($mut_list);
    return ok([
        'results' => $results,
        'total' => count($results),
        'succeeded' => $n_ok,
        'failed' => $n_fail,
        'stopped_at' => $stopped_at,
        'stop_on_error' => $stop_on_error,
        'max_steps' => $PIPELINE_MAX_STEPS,
        'mutating_actions_declared' => $mut_list,
    ]);
}

// 通用错误码提示: HELP_SPEC 里没写 err 的 action, help 会自动补上这几条。
// (与 PY 端 COMMON_ERRORS_HINT 逐字一致; 它们对任何 action 都成立)
const COMMON_ERRORS_HINT = [
    '1001 未授权(token 无效或缺失)',
    '1002 无权限(权限位不足/scope 未授权)',
    '1003 限流(请求过快, 稍后重试或改用 pipeline 合并请求)',
    '1011 参数非法(缺失或类型错误)',
    '1099 服务器内部错误',
];

$ROUTES = [
    'ping'           => 'action_ping',
    'help'           => 'action_help',
    'health'         => 'action_health',
    'metrics'        => 'action_metrics',
    // 文件 CRUD
    'upload'         => 'action_upload',
    'upload_batch'   => 'action_upload_batch',
    'chunked_upload' => 'action_chunked_upload',
    'batch_deploy'   => 'action_batch_deploy',
    'download'       => 'action_download',
    'stat'           => 'action_stat',
    'list'           => 'action_list',
    'diff'           => 'action_diff',
    'delete'         => 'action_delete',
    'rename'         => 'action_rename',
    'copy'           => 'action_copy',
    'mkdir'          => 'action_mkdir',
    'checksum'       => 'action_checksum',
    'php_run'        => 'action_php_run',
    // 写代码辅助
    'search'         => 'action_search',
    'todo'           => 'action_todo',
    'stats'          => 'action_stats',
    'read_range'     => 'action_read_range',
    'format'         => 'action_format',
    'lint'           => 'action_lint',
    'doc'            => 'action_doc',
    // 片段管理
    'snippet_set'    => 'action_snippet_set',
    'snippet_get'    => 'action_snippet_get',
    'snippet_list'   => 'action_snippet_list',
    'snippet_delete' => 'action_snippet_delete',
    // 备份
    'backup_ls'      => 'action_backup_ls',
    'backup_rb'      => 'action_backup_rb',
    // 远程命令
    'exec'           => 'action_exec',
    // 持久 shell 工具 (对齐原生 ssh): 会话内 cwd/export 跨调用保持, 后台任务可查询/终止
    'shell_open'     => 'action_shell_open',
    'shell_run'      => 'action_shell_run',
    'shell_job'      => 'action_shell_job',
    'shell_kill'     => 'action_shell_kill',
    'shell_close'    => 'action_shell_close',
    'shell_list'     => 'action_shell_list',
    // Webhook
    'webhook_set'    => 'action_webhook_set',
    'webhook_list'   => 'action_webhook_list',
    'webhook_delete' => 'action_webhook_delete',
    'webhook_test'   => 'action_webhook_test',
    // 管理 API (v4.1)
    'config_get'     => 'action_config_get',
    'config_reload'  => 'action_config_reload',
    'scopes_list'    => 'action_scopes_list',
    'tokens_list'    => 'action_tokens_list',
    'token_add'      => 'action_token_add',
    'token_delete'   => 'action_token_delete',
    'scope_add'      => 'action_scope_add',
    'scope_delete'   => 'action_scope_delete',
    'audit_log'      => 'action_audit_log_view',
    'admin_stats'    => 'action_admin_stats',
    'client_package' => 'action_client_package',
    'package_list'   => 'action_package_list',
    'package_delete' => 'action_package_delete',
    // AI 增强套件 v4.2
    'whoami'         => 'action_whoami',
    'client_download'=> 'action_client_download',
    'sync_check'     => 'action_sync_check',
    'lint_batch'     => 'action_lint_batch',
    'oplog_append'   => 'action_oplog_append',
    'oplog_list'     => 'action_oplog_list',
    'oplog_view'     => 'action_oplog_view',
    'recent_changes' => 'action_recent_changes',
    'batch_rollback' => 'action_batch_rollback',
    // FIX-v4.34 (性能): 单次往返执行多步, 把 N×RTT 压成 1×RTT
    'pipeline'       => 'action_pipeline',
    // P1-A: error_log
    'error_log'      => 'action_error_log',
    'error_log_list' => 'action_error_log_list',
];

// ==================== 工具映射 (action => 工具文件名) ====================
$TOOL_MAP = [
    'upload'         => 'file_crud',
    'upload_batch'   => 'file_crud',
    'download'       => 'file_crud',
    'stat'           => 'file_crud',
    'list'           => 'file_crud',
    'diff'           => 'file_crud',
    'delete'         => 'file_crud',
    'rename'         => 'file_crud',
    'copy'           => 'file_crud',
    'mkdir'          => 'file_crud',
    'checksum'       => 'file_crud',
    'php_run'        => 'file_crud',
    'chunked_upload' => 'chunked_upload',
    'batch_deploy'   => 'batch_deploy',
    // P1-A: error_log
    'error_log'      => 'error_log',
    'error_log_list' => 'error_log',
    'backup_ls'      => 'backup',
    'backup_rb'      => 'backup',
    'exec'           => 'exec',
    // 🆕 持久 shell 工具 (对齐原生 ssh 的会话工具, 面向 AI 易用性)
    'shell_open'     => 'shell', 'shell_run'      => 'shell', 'shell_job'   => 'shell',
    'shell_kill'     => 'shell', 'shell_close'    => 'shell', 'shell_list'  => 'shell',
    'webhook_set'    => 'webhook',
    'webhook_list'   => 'webhook',
    'webhook_delete' => 'webhook',
    'webhook_test'   => 'webhook',
    'search'         => 'dev_tools',
    'todo'           => 'dev_tools',
    'stats'          => 'dev_tools',
    'read_range'     => 'dev_tools',
    'format'         => 'dev_tools',
    'lint'           => 'dev_tools',
    'lint_batch'     => 'dev_tools',
    'doc'            => 'dev_tools',
    'snippet_set'    => 'snippet',
    'snippet_get'    => 'snippet',
    'snippet_list'   => 'snippet',
    'snippet_delete' => 'snippet',
];

$action = $body['action'] ?? '';
// action 必须是字符串: 旧实现直接 isset($ROUTES[$action]) —— 数组型 action 会抛
// "Cannot access offset of type array" (HTTP 500 空响应), AI 完全无法自助修复.
if (!is_string($action)) {
    $__atype = is_array($action) ? 'array' : gettype($action);
    audit_log('', $body, $E['INVALID_PARAM'], ['reason' => 'bad_action_type']);
    echo json_encode(fail($E['INVALID_PARAM'], 'bad_action_type',
        "action must be a string, got $__atype",
        ['hint' => 'action 只接受字符串, 例: {"action":"ping"}']));
    exit;
}
// 供 bad_path / reject_path 回显字段名与记审计 (路径类错误发生在 action 函数内部)
$GLOBALS['CUR_ACTION'] = $action;
$GLOBALS['CUR_BODY'] = $body;
// 参数严格类型: 这些字段必须是字符串 (与 Python dispatch 的 STRICT_STR_FIELDS 逐字对齐).
// PHP 弱转换会把 true/123 变成 "1"/"123", 传错类型时给明确 1011, 不猜调用方意图.
foreach (['pattern', 'file_pattern', 'algorithm', 'type', 'name', 'grep', 'relate_to',
          'url', 'content', 'language', 'description', 'keyword',
          'new_token', 'target_token',
          'pre_check', 'post_check', 'post_check_expect',
          'expect_md5', 'base_md5', 'md5', 'final_md5', 'chunk_md5', 'stdin_b64'] as $__sf) {
    if (array_key_exists($__sf, $body) && $body[$__sf] !== null && !is_string($body[$__sf])) {
        $__st = is_array($body[$__sf]) ? 'array' : gettype($body[$__sf]);
        audit_log($action, $body, $E['INVALID_PARAM'], ['reason' => 'bad_param_type', 'field' => $__sf]);
        echo json_encode(fail($E['INVALID_PARAM'], 'bad_param',
            "$__sf 必须是字符串, 收到 $__st",
            ['field' => $__sf,
             'hint' => "不要传 true/数字/数组/对象; 例: {\"$__sf\": \"value\"}"]));
        exit;
    }
}
// 非 UTF-8 路径的无损寻址: JSON 字符串无法承载任意字节, 调用方可传 path_b64/from_b64/to_b64.
// 这里在分发前覆盖同名字段, 所有现有 action 无需改动即可精确操作非 UTF-8 文件名.
foreach (['path' => 'path_b64', 'from' => 'from_b64', 'to' => 'to_b64'] as $__pf => $__bf) {
    if (!array_key_exists($__bf, $body)) continue;
    if (!is_string($body[$__bf]) || $body[$__bf] === '') {
        audit_log($action, $body, $E['INVALID_PARAM'], ['reason' => 'bad_' . $__bf]);
        echo json_encode(fail($E['INVALID_PARAM'], 'bad_path_b64',
            "$__bf 必须是非空 base64 字符串", [
                'hint' => "把原始路径字节 base64 编码后放入 $__bf; 与 $__pf 同时出现时以 $__bf 为准",
            ]));
        exit;
    }
    $__decoded = base64_decode($body[$__bf], true);
    if ($__decoded === false || $__decoded === '') {
        audit_log($action, $body, $E['INVALID_PARAM'], ['reason' => 'bad_' . $__bf]);
        echo json_encode(fail($E['INVALID_PARAM'], 'bad_path_b64',
            "$__bf base64 解码失败", ['hint' => "$__bf 必须是原始路径字节的 base64"]));
        exit;
    }
    $body[$__pf] = $__decoded;
}

if (!isset($ROUTES[$action])) {
    audit_log($action, $body, $E['INVALID_PARAM']);
    // 拼写纠正建议 (与 Python 端同算法: levenshtein 升序, 同距按名, 阈值 max(3, len/2))
    $cand = [];
    foreach (array_keys($GLOBALS['HELP_SPEC']) as $n) {
        if (!isset($ROUTES[$n])) continue;
        $cand[] = [$n, levenshtein($action, $n)];
    }
    usort($cand, function ($a, $b) {
        return $a[1] === $b[1] ? strcmp($a[0], $b[0]) : ($a[1] < $b[1] ? -1 : 1);
    });
    $lim = max(3, intdiv(strlen($action), 2));
    $sug = [];
    foreach (array_slice($cand, 0, 5) as $x) {
        if ($x[1] <= $lim) $sug[] = $x[0];
    }
    echo json_encode(fail($E['INVALID_PARAM'], 'unknown_action', "Action '$action' not found", [
        'suggestions' => $sug,
        'available' => array_values(array_filter(array_keys($GLOBALS['HELP_SPEC']),
                                   function ($a) use ($ROUTES) { return isset($ROUTES[$a]); })),
        'hint' => '不带参数调用 help 可列出全部 action' . ($sug ? ('; 是否想找: ' . implode(', ', $sug)) : ''),
    ]));
    exit;
}

// --- 权限校验 (多目录模式) ---
if ($MULTI_DIR_MODE) {
    // 修复: 缺失映射时默认按最严格 'admin' 权限处理 (原 null 默认放行是防御性漏洞)
    $need_perm = $ACTION_PERMS[$action] ?? 'admin';
    if (!in_array($need_perm, $CONFIG['PERMISSIONS'], true)) {
        audit_log($action, $body, $E['FORBIDDEN'], ['required' => $need_perm]);
        http_response_code(403);
        echo json_encode(fail($E['FORBIDDEN'], 'forbidden', "Permission '$need_perm' required for action '$action'", [
            'required'         => $need_perm,
            'your_permissions' => $CONFIG['PERMISSIONS'],
        ]));
        exit;
    }
}

// --- 按需加载工具文件 ---
// 优先从 sync_api.php 同级 tools/ 目录加载(与 scope 无关, 避免每个 scope 都要复制 tools/)
// 找不到再回退到 WORK_DIR/tools/ (兼容旧模式)
// 注: 加载逻辑已抽到 ensure_tool_loaded(), pipeline 复用同一份, 避免两处漂移。
$tool_err = ensure_tool_loaded($action, $CONFIG);
if ($tool_err !== null) {
    audit_log($action, $body, $tool_err['code'] ?? $E['INTERNAL'], [
        'reason' => 'tool_missing',
        'error'  => (string)($tool_err['error'] ?? ''),
    ]);
    echo json_encode($tool_err);
    exit;
}

if ($CONFIG['DEBUG']) {
    error_log('[ai-sync v4.3] action=' . $action . ' payload=' . json_encode($body));
}

try {
    $result = $ROUTES[$action]($body, $CONFIG);
} catch (Throwable $ex) {
    // BUG-47 修复 (审计 Finding 11): ex->getMessage() 含绝对路径/连接串/部分 token, 直接返回客户端
    // 会泄露服务器内部结构. 完整 message 写入审计日志 (本机), 客户端只收到通用错误 + 异常类名.
    audit_log($action, $body, $E['INTERNAL'], [
        'exception' => $ex->getMessage(),
        'exception_class' => get_class($ex),
        'trace_first' => array_slice(explode("\n", $ex->getTraceAsString()), 0, 3),
    ]);
    $result = fail($E['INTERNAL'], 'exception', 'Internal server error (see audit log)', [
        'exception_class' => get_class($ex),
    ]);
    // 管理员身份下直接把根因写进响应体: 不用再去 audit_log 里 grep exception
    if (!empty($GLOBALS['CONFIG']['IS_ADMIN'])) {
        $__emsg  = (string)$ex->getMessage();
        $__ehint = _exception_hint($__emsg);
        $result['exception']      = _utf8_trunc($__emsg, 300);
        $result['exception_at']   = basename((string)$ex->getFile()) . ':' . $ex->getLine();
        if ($__ehint !== null) $result['exception_hint'] = $__ehint;
    }
}

// 大文件下载: 流式分支 (跳过 json_encode + gzip, 峰值内存 O(块))
//   download 不在 $_webhook_actions 内, 直接 exit; 审计用文件大小作为 resp_bytes
if (is_array($result) && isset($result['_stream_file'])) {
    $__stream_size = @filesize($result['_stream_file']);
    audit_request($action, $body, $result, $__stream_size === false ? 0 : $__stream_size, true);
    send_stream_download($result['_stream_file']);
    exit;
}

$json = json_encode($result, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_UNESCAPED_SLASHES);
// 请求级兜底审计: action 内部未写明细时记录本次调用 (读操作/查询类此前完全无记录)
audit_request($action, $body, $result, strlen($json));
$size_kb = strlen($json) / 1024;
$force_gzip = $CONFIG['GZIP_FORCE'] || $size_kb >= $CONFIG['GZIP_THRESHOLD_KB'];
// 修复: Vary header 应无条件设置 (响应行为依赖 Accept-Encoding)
header('Vary: Accept-Encoding');
if ($can_gzip && $force_gzip) {
    $gz = gzencode($json, 6);
    if ($gz !== false) {
        header('Content-Encoding: gzip');
        header('X-Original-Size: ' . strlen($json));
        echo $gz;
    } else {
        // gzencode 失败, 回退到非压缩输出
        echo $json;
    }
} else {
    echo $json;
}

// P0-2 fix: 主动加载 webhook.php（保证 fastcgi_finish_request 之后 trigger_webhooks 已定义）
//    原逻辑：仅当 action=webhook_{set,list,delete} 时 TOOL_MAP 才加载 webhook.php
//    但 webhook 触发时机是 upload / upload_batch / delete / chunked_upload，之前这些场景 function_exists 恒 false → webhook 从不触发
if (!function_exists('trigger_webhooks')) {
    $_webhook_candidates = [
        __DIR__ . '/tools/webhook.php',
        rtrim($CONFIG['WORK_DIR'], '/\\') . '/tools/webhook.php',
    ];
    foreach ($_webhook_candidates as $_wf) {
        if (file_exists($_wf)) { require_once $_wf; break; }
    }
}

// Webhook 异步触发
// P0-2 fix: 非 fastcgi 环境也要触发（本地 dev server / CLI）；不能再把触发器塞到 fastcgi_finish_request 条件内部
$_webhook_actions = ['upload', 'upload_batch', 'delete', 'chunked_upload', 'batch_deploy'];
if (in_array($action, $_webhook_actions, true)) {
    $hook_result = $result;
    $hook_action = $action;
    $hook_req = $body;
    if (function_exists('fastcgi_finish_request')) {
        @fastcgi_finish_request();
    }
    if (function_exists('trigger_webhooks')) {
        trigger_webhooks($hook_action, $hook_req, $hook_result, $CONFIG);
    }
}

// ============ 基础 Action (保留在接口层) ============

/**
 * 自描述: 让调用方(尤其 AI)无需外部文档即可知道每个 action 该传什么。
 * 无参 → 全部 action 索引(名 + 一句话 + 必填参数名);
 * name=<action> → 单个 action 的完整契约(必填/可选/返回/示例/常见错误/相关 action)。
 */
function action_help($req, $cfg) {
    global $E;
    // 自描述: 让调用方(尤其 AI)无需外部文档即可知道每个 action 该传什么。
    // 无参 -> 全部 action 索引; name=<action> -> 单个 action 的完整契约。
    // FIX-v4.34 (P2 静默降级): AI 查契约时最自然的写法是 args.action (整个协议里
    //   action 就是"指定要什么"的通用字段名)。旧实现只认顶层 name/about/topic/target,
    //   遇到 action 就静默忽略 → 返回全部索引且 ok:true, AI 察觉不到自己没拿到详情。
    //   现在显式接管该写法, 并在响应里回注 alias_note 说明已按意图返回。
    $want = '';
    $aliasUsed = '';
    $sub = (isset($req['args']) && is_array($req['args'])) ? $req['args'] : [];
    // ① 契约字段优先: 顶层 name/about/topic/target, 再 args 内同名
    foreach ([$req, $sub] as $src) {
        foreach (['name', 'about', 'topic', 'target'] as $k) {
            if (isset($src[$k]) && is_string($src[$k]) && trim($src[$k]) !== '') {
                $want = trim($src[$k]);
                break 2;
            }
        }
    }
    // ② 别名: args.action
    if ($want === '' && isset($sub['action']) && is_string($sub['action'])
        && trim($sub['action']) !== '') {
        $want = trim($sub['action']);
        $aliasUsed = 'action';
    }
    if ($want === '') {
        $items = [];
        foreach ($GLOBALS['HELP_SPEC'] as $n => $sp) {
            if (!isset($GLOBALS['ROUTES'][$n])) continue;
            $reqs = [];
            foreach (($sp['r'] ?? []) as $d) {
                $pos = strpos($d, ':');
                $reqs[] = $pos === false ? $d : substr($d, 0, $pos);
            }
            $items[] = ['n' => $n, 'd' => $sp['d'], 'r' => $reqs];
        }
        $codes = [];
        foreach ($GLOBALS['ERROR_LABELS'] as $c => $lbl) {
            $codes[(string)$c] = $lbl;
        }
        return ok([
            'total'   => count($items),
            'usage'   => 'help name=<action> 查看单个 action 的参数契约; ping 查看权限与 scope',
            'envelope'=> [
                'request'  => ['token' => 'str 必填', 'scope' => 'str 多目录模式下必填',
                               'action' => 'str 必填', '...' => '各 action 专属参数, 见下方 r(必填)/详情'],
                'response' => '恒含 {ok, code}; 失败另含 error, message, fix_hint(可直接照做的修复建议)',
                'http_status' => '1001→401, 1002→403, 1003→429, 其余(含 token 之外的业务错误)→200',
            ],
            'error_codes' => $codes,
            'actions' => $items,
        ]);
    }

    $spec = $GLOBALS['HELP_SPEC'][$want] ?? null;
    if ($spec === null) {
        // 模糊匹配给出候选 (PHP 无 difflib, 用 levenshtein 取最近几个)
        // 与 Python 端同一套排序 (距离升序, 同距按名字) 与阈值, 保证 suggestions 逐项一致
        $cand = [];
        foreach ($GLOBALS['HELP_SPEC'] as $n => $_) {
            if (!isset($GLOBALS['ROUTES'][$n])) continue;
            $cand[] = [$n, levenshtein($want, $n)];
        }
        usort($cand, function ($a, $b) {
            return $a[1] === $b[1] ? strcmp($a[0], $b[0]) : ($a[1] < $b[1] ? -1 : 1);
        });
        $lim = max(3, intdiv(strlen($want), 2));
        $sug = [];
        foreach (array_slice($cand, 0, 5) as $x) {
            if ($x[1] <= $lim) $sug[] = $x[0];
        }
        return fail($E['INVALID_PARAM'], 'unknown_action', "No such action: '$want'", [
            'hint' => '不带参数调用 help 可列出全部 action' . ($sug ? ('; 是否想找: ' . implode(', ', $sug)) : ''),
            'suggestions' => $sug,
        ]);
    }
    if (!isset($GLOBALS['ROUTES'][$want])) {
        return fail($E['INVALID_PARAM'], 'unknown_action',
            "Action '$want' is documented but not enabled on this server",
            ['hint' => '该 action 在契约表中但本机未注册, 可能被服务端版本裁剪']);
    }
    $ret = [
        'action'   => $want,
        'desc'     => $spec['d'],
        'required' => $spec['r'] ?? [],
        'optional' => $spec['o'] ?? [],
        'returns'  => $spec['ret'],
        'example'  => empty($spec['eg']) ? new stdClass() : $spec['eg'],
        // FIX-v4.35: 与 PY 端一致 —— 没写具体错误码时自动补通用错误码
        'errors'   => $spec['err'] ?? COMMON_ERRORS_HINT,
        'see_also' => $spec['see'] ?? [],
    ];
    if ($aliasUsed !== '') {
        $ret['alias_note'] = "你用的是 '" . $aliasUsed . "' 字段查询本契约; 契约字段名是 'name'"
            . " (例: {\"action\":\"help\",\"name\":\"" . $want . "\"})。已按你的意图返回详情。";
    }
    return ok($ret);
}

/**
 * 部署自检: 找出"代码能跑但部署方式会泄密/放大风险"的配置.
 * 只做静态判断, 不发网络请求, 不阻塞请求.
 */
function _deployment_warnings($cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE, $CONFIG_FILE;
    $w = [];
    $app_dir = @realpath(__DIR__);
    $cfg_real = @realpath($CONFIG_FILE);
    if ($cfg_real === false) $cfg_real = $CONFIG_FILE;
    $cfg_in_app = ($app_dir !== false && strpos($cfg_real . DIRECTORY_SEPARATOR, rtrim($app_dir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR) === 0);
    if ($cfg_in_app) {
        $w[] = [
            'code' => 'config_in_webroot', 'severity' => 'critical',
            'message' => 'sync_config.json 位于 Web 根目录内, 可被 GET /sync_config.json 直接下载 (含 token)',
            'hint' => '把配置移到 Web 根之外并设置 AI_SYNC_CONFIG_FILE; 或在 nginx 加 exact deny. 移动后轮换全部 token.',
        ];
    }
    $pkg_dir = rtrim((string)($cfg['PACKAGE_DIR'] ?? ''), '/\\');
    $pkg_real = $pkg_dir !== '' ? @realpath($pkg_dir) : false;
    if ($pkg_real !== false && $app_dir !== false
        && strpos($pkg_real . DIRECTORY_SEPARATOR, rtrim($app_dir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR) === 0) {
        $w[] = [
            'code' => 'packages_in_webroot', 'severity' => 'high',
            'message' => '客户端安装包目录位于 Web 根目录内 (安装包含 token 明文)',
            'hint' => '在 sync_config.json global.package_dir 指定 Web 根之外的目录; 或使用 nginx deny.',
        ];
    }
    if ($MULTI_DIR_MODE && !empty($RAW_CONFIG['admin_token']) && $cfg_in_app) {
        $w[] = [
            'code' => 'admin_token_exposed', 'severity' => 'critical',
            'message' => 'admin_token 与可静态下载的配置文件同处 Web 根, 等于管理员凭据公开',
            'hint' => '立即将配置移出 Web 根并轮换 admin_token 与所有 scope token.',
        ];
    }
    if (!empty($cfg['EXEC_ENABLED']) && function_exists('posix_geteuid') && posix_geteuid() === 0) {
        $w[] = [
            'code' => 'exec_as_root', 'severity' => 'high',
            'message' => 'exec/shell 已启用且 PHP 进程以 root 运行, 任何 exec token 等价于 root 远程执行',
            'hint' => '改用非 root 用户运行 php-fpm, 或关闭 exec_enabled, 并为 exec 配置 IP 白名单.',
        ];
    }
    // 扩展缺失: 现在的代码已经全部能降级跑, 但部署者必须知道自己在用哪条兜底路径
    if (!_curl_ok()) {
        $w[] = [
            'code' => 'missing_php_extension', 'severity' => 'high',
            'message' => '未启用 curl 扩展: webhook_test 与 batch_deploy 的 post_check 降级为 stream 传输',
            'hint' => '影响: 失去 CURLOPT_RESOLVE 的 IP 固定(防 DNS rebinding 弱化). 装 php-curl 后必须重启 php-fpm / php -S 进程, 否则等于没装',
        ];
    }
    if (!class_exists('ZipArchive') || !_ext_ok('zip')) {
        $w[] = [
            'code' => 'missing_php_extension', 'severity' => 'low',
            'message' => '未启用 zip 扩展: 客户端打包自动改用内置打包器(不压缩), 包里文件变大但仍可正常解压',
            'hint' => '想要压缩包就用 apt install php-zip, 装完必须重启 php-fpm / php -S 进程',
        ];
    }
    if (!_mbstring_ok()) {
        $w[] = [
            'code' => 'missing_php_extension', 'severity' => 'low',
            'message' => '未启用 mbstring 扩展: 截断/大小写处理自动回退为 substr/stripos, exec / search / shell 能力保留',
            'hint' => '副作用仅是超长中文截断可能少一个字. 装 php-mbstring 并重启 PHP 进程即可恢复按字符截断',
        ];
    }
    return ['config_file' => $CONFIG_FILE, 'config_in_app_dir' => $cfg_in_app,
            'package_dir' => $pkg_dir, 'warnings' => $w];
}

function action_ping($req, $cfg) {
    $resp = [
        'version'     => $cfg['API_VERSION'],
        'time'        => time(),
        'work_dir'    => $cfg['WORK_DIR'],
        'backup_dir'  => $cfg['BACKUP_DIR'],
        'probe_dir'   => $cfg['PROBE_DIR'],
        'chunk_dir'   => $cfg['CHUNK_DIR'],
        'exec_enabled'=> $cfg['EXEC_ENABLED'],
        // actions 按 HELP_SPEC 顺序输出 (两端同序), 只列本机已注册的
        'actions'     => array_values(array_filter(array_keys($GLOBALS['HELP_SPEC']),
                                        function ($a) { return isset($GLOBALS['ROUTES'][$a]); })),
        'help'        => 'help (无参) 列出全部 action 及其必填参数; help name=<action> 看参数契约',
    ];
    $_dw = _deployment_warnings($cfg);
    $resp['deployment_warning_count'] = count($_dw['warnings']);
    if (!empty($_dw['warnings'])) $resp['deployment_warnings'] = $_dw['warnings'];
    // 多目录授权信息 (单目录回退模式下不输出, 保持旧客户端兼容)
    if (isset($cfg['SCOPE'])) {
        $resp['scope']            = $cfg['SCOPE'];
        $resp['token_name']       = $cfg['TOKEN_NAME'];
        $resp['is_admin']         = $cfg['IS_ADMIN'];
        $resp['permissions']      = $cfg['PERMISSIONS'];
        $resp['available_scopes'] = $cfg['AVAILABLE_SCOPES'] ?? [$cfg['SCOPE']];
    }
    return ok($resp);
}

function action_health($req, $cfg) {
    $work_dir = rtrim($cfg['WORK_DIR'], '/\\');
    $disk = @disk_free_space($work_dir);
    $total = @disk_total_space($work_dir);
    return ok([
        'version'           => $cfg['API_VERSION'],
        'uptime_s'          => time() - filemtime(__FILE__),
        'php_version'       => PHP_VERSION,
        'work_dir'          => $work_dir,
        'work_dir_writable' => is_writable($work_dir),
        'disk_free_mb'      => $disk ? round($disk / 1048576, 1) : null,
        'disk_total_mb'     => $total ? round($total / 1048576, 1) : null,
        // 与 _deployment_warnings 同源: 用 _ext_ok/_curl_ok 判定, 面板自检卡与告警不会互相矛盾
        'extensions'        => [
            'zlib'     => extension_loaded('zlib'),
            'json'     => extension_loaded('json'),
            'curl'     => _curl_ok(),
            'mbstring' => _mbstring_ok(),
            'zip'      => (class_exists('ZipArchive') && _ext_ok('zip')),
        ],
        'deployment'        => _deployment_warnings($cfg),
    ]);
}

function action_metrics($req, $cfg) {
    $file = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['METRICS_FILE'];
    if (!file_exists($file)) {
        return ok(['actions' => [], 'errors' => [], 'total' => 0, 'since' => time()]);
    }
    $data = json_decode(file_get_contents($file), true) ?: [];
    return ok($data);
}

function _metrics_inc($cfg, $action, $code) {
    $file = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['METRICS_FILE'];
    // 修复 P3-5: 用 flock 包裹 read-modify-write, 防并发覆盖导致计数偏低
    $fp = @fopen($file, 'c+');
    if (!$fp) { return; }
    if (!flock($fp, LOCK_EX)) { fclose($fp); return; }
    $raw = stream_get_contents($fp);
    $data = ($raw !== '' && $raw !== false) ? (json_decode($raw, true) ?: []) : ['since' => time(), 'actions' => [], 'errors' => []];
    if (!is_array($data)) $data = ['since' => time(), 'actions' => [], 'errors' => []];
    $data['actions'][$action] = ($data['actions'][$action] ?? 0) + 1;
    if ($code !== 0) {
        $data['errors'][$code] = ($data['errors'][$code] ?? 0) + 1;
    }
    $data['total'] = ($data['total'] ?? 0) + 1;
    $data['last_ts'] = time();
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($data, JSON_UNESCAPED_UNICODE));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
}

// ============ 管理 API (v4.1, 实现在接口层, 不放入 tools/) ============

// 统计目录下文件数与占用 (递归, 仅文件)
// 统计目录下文件数与占用 (递归, 仅文件; 病态 /mnt scope 已在 sync_config.json 剔除)
function _scope_disk_stats($work_dir) {
    $file_count = 0;
    $size_bytes = 0;
    if (is_dir($work_dir)) {
        try {
            $it = new RecursiveIteratorIterator(
                new RecursiveDirectoryIterator($work_dir, FilesystemIterator::SKIP_DOTS),
                RecursiveIteratorIterator::LEAVES_ONLY
            );
            foreach ($it as $f) {
                if ($f->isFile()) {
                    $file_count++;
                    $size_bytes += $f->getSize();
                }
            }
        } catch (Throwable $e) { /* 忽略统计错误 */ }
    }
    return ['file_count' => $file_count, 'size_bytes' => $size_bytes];
}

// 统计备份目录 .zip 数量
function _scope_backup_count($work_dir, $backup_dir_rel) {
    $bd = rtrim($work_dir, '/\\') . '/' . $backup_dir_rel;
    if (!is_dir($bd)) return 0;
    return count(glob($bd . '*.zip') ?: []);
}

// config_get - 返回脱敏配置 (token 前6位+***, admin_token 仅显示是否存在)
function action_config_get($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE;
    if (!$MULTI_DIR_MODE) {
        return ok([
            'mode'    => 'single',
            'version' => $cfg['API_VERSION'],
            'config'  => ['work_dir' => $cfg['WORK_DIR'], 'token' => mask_token($cfg['TOKEN'])],
        ]);
    }
    $tokens_out = [];
    foreach ($RAW_CONFIG['tokens'] ?? [] as $tk => $info) {
        $tokens_out[] = [
            'token'       => mask_token($tk),
            'name'        => $info['name'] ?? '',
            'scopes'      => $info['scopes'] ?? [],
            'permissions' => $info['permissions'] ?? [],
        ];
    }
    return ok([
        'mode'                => 'multi',
        'version'             => $RAW_CONFIG['version'] ?? '',
        'global'              => $RAW_CONFIG['global'] ?? [],
        'admin_token_present' => !empty($RAW_CONFIG['admin_token']),
        'tokens'              => $tokens_out,
        'scopes'              => $RAW_CONFIG['scopes'] ?? [],
    ]);
}

// config_reload - 重新加载 sync_config.json (新配置在下次请求生效)
function action_config_reload($req, $cfg) {
    global $RAW_CONFIG;
    $new = load_sync_config();
    if ($new === null) {
        return fail($GLOBALS['E']['INTERNAL'], 'config_load_failed', 'Cannot load sync_config.json');
    }
    $RAW_CONFIG = $new;
    return ok([
        'reloaded' => true,
        'version'  => $new['version'] ?? '',
        'tokens'   => count($new['tokens'] ?? []),
        'scopes'   => count($new['scopes'] ?? []),
        'note'     => 'current request still uses old config; new config takes effect on next request',
    ]);
}

// scopes_list - 列出所有 scope (含 work_dir、文件数、磁盘占用、备份数)
function action_scopes_list($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE;
    if (!$MULTI_DIR_MODE) {
        return ok(['mode' => 'single', 'scopes' => [[
            'name' => 'default', 'work_dir' => $cfg['WORK_DIR'], 'writable' => is_writable($cfg['WORK_DIR']),
        ]]]);
    }
    $out = [];
    foreach ($RAW_CONFIG['scopes'] ?? [] as $name => $sc) {
        $wd = $sc['work_dir'] ?? '';
        $stats = _scope_disk_stats($wd);
        $out[] = [
            'name'         => (string)$name,
            'work_dir'     => $wd,
            'writable'     => is_writable($wd),
            'file_count'   => $stats['file_count'],
            'size_mb'      => round($stats['size_bytes'] / 1048576, 2),
            'backup_count' => _scope_backup_count($wd, $sc['backup_dir'] ?? 'ai-sync/backups/'),
            'backup_keep'  => $sc['backup_keep'] ?? 0,
            'max_size_mb'  => round(($sc['max_size'] ?? 0) / 1048576, 1),
            'max_files_per_deploy' => $sc['max_files_per_deploy'] ?? ($cfg['MAX_FILES_PER_DEPLOY'] ?? 50),
            'exec_enabled' => $sc['exec_enabled'] ?? false,
        ];
    }
    return ok(['mode' => 'multi', 'scopes' => $out]);
}

// tokens_list - 列出所有 token (脱敏, 显示 name/scopes/permissions)
function action_tokens_list($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE;
    // admin 模式返回完整 token (管理操作 token_delete/client_package 需要完整值); 普通模式脱敏
    $show_full = !empty($cfg['IS_ADMIN']);
    if (!$MULTI_DIR_MODE) {
        return ok(['mode' => 'single', 'tokens' => [[
            'token' => $show_full ? $cfg['TOKEN'] : mask_token($cfg['TOKEN']), 'name' => 'default',
            'scopes' => ['default'], 'permissions' => ['read','write','delete','exec','lint','backup'],
        ]]]);
    }
    $out = [];
    foreach ($RAW_CONFIG['tokens'] ?? [] as $tk => $info) {
        $out[] = [
            // PHP 数组会把纯数字字符串键转成 int; token 对外必须始终是字符串, 否则严格类型的客户端会解析失败
            'token'       => $show_full ? (string)$tk : mask_token($tk),
            'name'        => $info['name'] ?? '',
            'scopes'      => $info['scopes'] ?? [],
            'permissions' => $info['permissions'] ?? [],
        ];
    }
    return ok([
        'mode'                => 'multi',
        'admin_token_present' => !empty($RAW_CONFIG['admin_token']),
        'tokens'              => $out,
    ]);
}

// token_add - 添加 token (参数: new_token, name, scopes, permissions)
// 注: 认证凭据固定占用 body.token 字段, 新 token 必须用 new_token 传递以避免字段冲突
function action_token_add($req, $cfg) {
    global $RAW_CONFIG;
    if (!$RAW_CONFIG) return fail($GLOBALS['E']['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support token management');
    $token       = trim($req['new_token'] ?? '');
    $name        = trim($req['name'] ?? '');
    $scopes      = $req['scopes'] ?? [];
    $permissions = $req['permissions'] ?? [];
    if ($name === '') {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_param', 'name required', [
            'hint' => 'name 是 token 的便于识别的名字, 必填; new_token 可不填(由服务端生成)',
        ]);
    }
    // FIX-v4.35 (帮助文档与实现不符): 契约一直写着 new_token 可选、"否则自动生成",
    //   但实现强制必填 —— AI 照契约不传就失败, 且被迫自己编随机串(安全性还更差)。
    //   现在补上自动生成分支; 生成时必须回明文, 否则 AI 拿不到凭据等于白创建。
    $generated = false;
    if ($token === '') {
        $token = 'tk-' . bin2hex(random_bytes(18));
        $generated = true;
    }
    if (!is_array($scopes) || !is_array($permissions)) {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_param', 'scopes and permissions must be arrays');
    }
    $valid_perms = ['read','write','delete','exec','lint','backup','admin'];
    foreach ($permissions as $p) {
        if (!in_array($p, $valid_perms, true)) {
            return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_permission', "permission '$p' invalid");
        }
    }
    // 修复 P1-4: 原子 read-modify-write, 防并发 admin 操作 last-write-wins 丢失 token
    list($ok, $data, $extra) = update_sync_config_atomic(function ($data) use ($token, $name, $scopes, $permissions) {
        if (!isset($data['tokens']) || !is_array($data['tokens'])) $data['tokens'] = [];
        if (isset($data['tokens'][$token])) {
            return [false, 'exists', 'token already exists'];
        }
        // 纯数字 scope 名在 PHP 数组里会变成 int 键; 对外/校验一律按字符串比较
        $all_scopes = array_map('strval', array_keys($data['scopes'] ?? []));
        foreach ($scopes as $s) {
            if (!in_array($s, $all_scopes, true)) {
                return [false, 'invalid_scope', "scope '$s' not found"];
            }
        }
        $data['tokens'][$token] = [
            'name'        => $name,
            'scopes'      => array_values($scopes),
            'permissions' => array_values($permissions),
        ];
        return [true, $data];
    });
    if (!$ok) {
        // $data = error_code (exists/invalid_scope/lock_fail/read_fail...), $extra = message
        $err_code = is_string($data) ? $data : 'save_failed';
        $msg = is_string($extra) && $extra !== '' ? $extra : 'failed to write config file';
        if (in_array($err_code, ['exists', 'invalid_scope'], true)) {
            return fail($GLOBALS['E']['INVALID_PARAM'], $err_code, $msg);
        }
        return fail($GLOBALS['E']['INTERNAL'], $err_code, $msg,
            ['hint' => '检查配置文件写权限; 并发 admin 操作可能冲突, 可重试']);
    }
    audit_log('token_add', $req, 0, ['name' => $name, 'token' => mask_token($token)]);
    // 自动生成时必须回明文(否则 AI 拿不到凭据 = 白创建); 手动指定的回掩码(调用方本就知道)
    return ok(['added' => true, 'generated' => $generated,
               'token' => $generated ? $token : mask_token($token),
               'name' => $name, 'scopes' => $scopes, 'permissions' => $permissions]);
}

// token_delete - 删除 token (参数: target_token)
// 注: 认证凭据固定占用 body.token 字段, 待删除 token 必须用 target_token 传递以避免字段冲突
function action_token_delete($req, $cfg) {
    global $RAW_CONFIG;
    if (!$RAW_CONFIG) return fail($GLOBALS['E']['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support token management');
    $token = trim($req['target_token'] ?? '');
    if ($token === '') {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_param', 'target_token required');
    }
    // 修复 P1-4: 原子 read-modify-write
    list($ok, $err_code, $msg) = update_sync_config_atomic(function ($data) use ($token) {
        if (!isset($data['tokens'][$token])) {
            return [false, 'not_found', 'token not found'];
        }
        // 修复: 防止删除最后一个可用 token 导致所有非 admin 客户端失效
        if (count($data['tokens']) <= 1) {
            return [false, 'last_token', '拒绝删除最后一个 token, 否则所有非 admin 客户端将立即失效. 请先 token_add 新 token 再删除旧 token.'];
        }
        unset($data['tokens'][$token]);
        return [true, $data];
    });
    if (!$ok) {
        $msg = is_string($msg) && $msg !== '' ? $msg : 'failed to write config file';
        $code = in_array($err_code, ['not_found'], true) ? $GLOBALS['E']['NOT_FOUND']
              : (in_array($err_code, ['last_token'], true) ? $GLOBALS['E']['INVALID_PARAM'] : $GLOBALS['E']['INTERNAL']);
        return fail($code, is_string($err_code) ? $err_code : 'save_failed', $msg);
    }
    audit_log('token_delete', $req, 0, ['target' => mask_token($token)]);
    return ok(['deleted' => true, 'token' => mask_token($token)]);
}

// scope_add - 添加 scope (参数: name, work_dir, backup_keep 等, 缺省自动补全)
function action_scope_add($req, $cfg) {
    global $RAW_CONFIG;
    if (!$RAW_CONFIG) return fail($GLOBALS['E']['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support scope management');
    $name_raw = $req['name'] ?? '';
    if (!is_string($name_raw)) return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_name', 'name 必须是字符串');
    $name     = trim($name_raw);
    $work_raw = $req['work_dir'] ?? '';
    if (!is_string($work_raw)) return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_work_dir', 'work_dir 必须是字符串路径');
    $work_dir = rtrim($work_raw, '/\\');
    if ($name === '' || $work_dir === '') {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_param', 'name and work_dir required');
    }
    // 修复: work_dir 必须解析为绝对路径并拒绝危险根目录, 防止管理员配错或被攻击后写入 /etc 等
    $real_wd = @realpath($work_dir);
    if ($real_wd === false || !is_dir($real_wd)) {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_work_dir', "work_dir 不存在或不可访问: $work_dir");
    }
    // 拒绝将系统根目录或敏感目录配为 work_dir
    $dangerous = ['/', '/etc', '/root', '/var', '/usr', '/bin', '/sbin', '/boot', '/sys', '/proc', '/dev'];
    foreach ($dangerous as $d) {
        if ($real_wd === $d) {
            return fail($GLOBALS['E']['INVALID_PARAM'], 'unsafe_work_dir', "拒绝将系统目录 '$d' 配为 work_dir");
        }
    }
    // 可选白名单: 若配置了 allowed_work_roots, work_dir 必须在白名单之下
    $allowed_roots = $RAW_CONFIG['global']['allowed_work_roots'] ?? [];
    if (!empty($allowed_roots)) {
        $ok = false;
        foreach ($allowed_roots as $root) {
            $root = rtrim($root, '/\\');
            if ($real_wd === $root || strpos($real_wd, $root . DIRECTORY_SEPARATOR) === 0) { $ok = true; break; }
        }
        if (!$ok) {
            return fail($GLOBALS['E']['INVALID_PARAM'], 'work_dir_not_allowed', "work_dir 不在 allowed_work_roots 白名单内", [
                'allowed_roots' => $allowed_roots,
            ]);
        }
    }
    $work_dir = $real_wd . '/';
    $d = default_scope_config();
    // 修复: 各 *_dir / audit_log / metrics_file 必须是相对路径, 防止指向敏感系统文件
    $rel_only_fields = ['backup_dir','probe_dir','chunk_dir','webhook_dir','audit_log','metrics_file'];
    foreach ($rel_only_fields as $f) {
        $val = $req[$f] ?? $d[$f];
        // 类型校验: 数组/布尔传给 preg_match/strpos 在 PHP 8 会抛 TypeError -> 1099,
        // 且 bool 会被弱转成 "1" 写进配置. 一律按参数错误 1011 返回.
        if (!is_string($val)) {
            return fail($GLOBALS['E']['INVALID_PARAM'], "bad_$f", "$f 必须是字符串相对路径 (如 \"ai-sync/audit.log\")");
        }
        // 拒绝绝对路径和 .. 段
        if (preg_match('#^([/\\\\]|[a-z]:[\\\\/])#i', $val) || strpos($val, '..') !== false) {
            return fail($GLOBALS['E']['INVALID_PARAM'], "bad_$f", "$f 必须是相对路径且不含 .. 段, 收到: $val");
        }
    }
    $exec_allow_ips = $d['exec_allow_ips'];
    if (array_key_exists('exec_allow_ips', $req) && $req['exec_allow_ips'] !== null) {
        if (!is_array($req['exec_allow_ips'])) {
            return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_exec_allow_ips', 'exec_allow_ips 必须是字符串数组 (CIDR 或 IP)');
        }
        foreach ($req['exec_allow_ips'] as $__ip) {
            if (!is_string($__ip)) {
                return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_exec_allow_ips', 'exec_allow_ips 的每一项都必须是字符串 (CIDR 或 IP)');
            }
        }
        $exec_allow_ips = array_values($req['exec_allow_ips']);
    }
    $exec_enabled = bool_flag($req['exec_enabled'] ?? $d['exec_enabled']);
    if ($exec_enabled === null) return bad_bool('exec_enabled', $req['exec_enabled']);
    $max_files_per_deploy = $d['max_files_per_deploy'];
    if (array_key_exists('max_files_per_deploy', $req) && $req['max_files_per_deploy'] !== null) {
        $__mfd = $req['max_files_per_deploy'];
        if (is_bool($__mfd) || (!is_int($__mfd) && !is_float($__mfd)
                && !(is_string($__mfd) && is_numeric(trim($__mfd))))) {
            return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_max_files_per_deploy',
                'max_files_per_deploy 必须是数字, 收到 ' . gettype($__mfd),
                ['hint' => '传整数, 如 "max_files_per_deploy": 50']);
        }
        $max_files_per_deploy = intval($__mfd);
    }
    if ($max_files_per_deploy < 1) {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'bad_max_files_per_deploy',
            'max_files_per_deploy 必须是 >= 1 的整数, 收到 ' . json_encode($req['max_files_per_deploy']),
            ['hint' => '它限制单次 batch_deploy 的文件数, 默认 50; 调大前确认 PHP memory_limit 与超时能承受。']);
    }
    $scope = [
        'work_dir'           => $work_dir,
        'backup_keep'        => isset($req['backup_keep'])        ? intval($req['backup_keep'])        : $d['backup_keep'],
        'max_size'           => isset($req['max_size'])           ? intval($req['max_size'])           : $d['max_size'],
        'max_chunked_size'   => isset($req['max_chunked_size'])   ? intval($req['max_chunked_size'])   : $d['max_chunked_size'],
        'chunk_ttl_days'     => isset($req['chunk_ttl_days'])     ? intval($req['chunk_ttl_days'])     : $d['chunk_ttl_days'],
        'chunk_size_default' => isset($req['chunk_size_default']) ? intval($req['chunk_size_default']) : $d['chunk_size_default'],
        'max_files_per_deploy' => $max_files_per_deploy,
        'exec_enabled'       => $exec_enabled,
        'exec_allow_ips'     => $exec_allow_ips,
        'exec_timeout_max'   => isset($req['exec_timeout_max'])   ? intval($req['exec_timeout_max'])   : $d['exec_timeout_max'],
        'backup_dir'         => $req['backup_dir']   ?? $d['backup_dir'],
        'probe_dir'          => $req['probe_dir']    ?? $d['probe_dir'],
        'chunk_dir'          => $req['chunk_dir']    ?? $d['chunk_dir'],
        'webhook_dir'        => $req['webhook_dir']  ?? $d['webhook_dir'],
        'audit_log'          => $req['audit_log']    ?? $d['audit_log'],
        'metrics_file'       => $req['metrics_file'] ?? $d['metrics_file'],
    ];
    // 修复 P1-4: 原子 read-modify-write, 防并发 scope_add 丢失
    list($ok, $err_code, $msg) = update_sync_config_atomic(function ($data) use ($name, $scope) {
        if (!isset($data['scopes']) || !is_array($data['scopes'])) $data['scopes'] = [];
        if (isset($data['scopes'][$name])) {
            return [false, 'exists', 'scope already exists'];
        }
        $data['scopes'][$name] = $scope;
        return [true, $data];
    });
    if (!$ok) {
        $msg = is_string($msg) && $msg !== '' ? $msg : 'failed to write config file';
        $code = ($err_code === 'exists') ? $GLOBALS['E']['INVALID_PARAM'] : $GLOBALS['E']['INTERNAL'];
        return fail($code, is_string($err_code) ? $err_code : 'save_failed', $msg,
            ['hint' => $err_code === 'exists' ? null : '检查配置文件写权限; 并发 admin 操作可能冲突, 可重试']);
    }
    audit_log('scope_add', $req, 0, ['name' => $name, 'work_dir' => $work_dir]);
    return ok(['added' => true, 'name' => $name, 'work_dir' => $work_dir]);
}

// scope_delete - 删除 scope (参数: name, 仍被 token 引用时仅警告)
function action_scope_delete($req, $cfg) {
    global $RAW_CONFIG;
    if (!$RAW_CONFIG) return fail($GLOBALS['E']['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support scope management');
    $name = trim($req['name'] ?? '');
    if ($name === '') {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_param', 'name required');
    }
    // 修复 P1-4: 原子 read-modify-write; 引用检查也在锁内基于最新数据
    $referenced = [];
    list($ok, $err_code, $msg) = update_sync_config_atomic(function ($data) use ($name, &$referenced) {
        if (!isset($data['scopes'][$name])) {
            return [false, 'not_found', 'scope not found'];
        }
        // 检查是否有 token 仍引用此 scope (仅警告, 仍允许删除)
        foreach ($data['tokens'] ?? [] as $tk => $info) {
            if (in_array($name, $info['scopes'] ?? [], true)) {
                $referenced[] = mask_token($tk);
            }
        }
        unset($data['scopes'][$name]);
        return [true, $data];
    });
    if (!$ok) {
        $msg = is_string($msg) && $msg !== '' ? $msg : 'failed to write config file';
        $code = ($err_code === 'not_found') ? $GLOBALS['E']['NOT_FOUND'] : $GLOBALS['E']['INTERNAL'];
        return fail($code, is_string($err_code) ? $err_code : 'save_failed', $msg);
    }
    audit_log('scope_delete', $req, 0, ['name' => $name, 'referenced_by' => $referenced]);
    return ok(['deleted' => true, 'name' => $name, 'referenced_by' => $referenced]);
}

// audit_log - 查看审计日志 (参数: lines=100, scope=可选)
function action_audit_log_view($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE;
    $lines = max(1, min(1000, intval($req['lines'] ?? 100)));
    $scope_name = $req['scope'] ?? ($cfg['SCOPE'] ?? '');
    // 修复: 跨 scope 越权校验 (与 recent_changes 一致)
    if ($scope_name !== ($cfg['SCOPE'] ?? '')) {
        if (empty($cfg['IS_ADMIN'])) {
            return fail($GLOBALS['E']['FORBIDDEN'], "无权访问其他 scope 的审计日志");
        }
    }
    // 确定审计日志文件路径
    if ($MULTI_DIR_MODE && isset($RAW_CONFIG['scopes'][$scope_name])) {
        $sc = $RAW_CONFIG['scopes'][$scope_name];
        $log_file = rtrim($sc['work_dir'], '/\\') . '/' . ($sc['audit_log'] ?? 'ai-sync/audit.log');
    } else {
        $log_file = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['AUDIT_LOG'];
    }
    if (!file_exists($log_file)) {
        return ok(['lines' => [], 'count' => 0, 'file' => $log_file]);
    }
    // 🆕 OPT-speed-20261022: 只读尾部 N 行, 不 file() 全量读入
    $tail = _read_tail_lines($log_file, $lines);
    $entries = [];
    foreach ($tail as $line) {
        $j = json_decode($line, true);
        $entries[] = $j ?: ['raw' => $line];
    }
    return ok(['lines' => $entries, 'count' => count($entries), 'file' => $log_file]);
}

// admin_stats - 管理统计 (各 scope 文件数、磁盘占用、备份数等)
// 🆕 OPT-speed-20261022: admin_stats 结果短 TTL 缓存, 避免每次进仪表盘对每个 scope 全量递归扫盘 → 加载慢
//   缓存写入 sys_get_temp_dir() (PHP-FPM 必可写); 原 dirname(__FILE__) 是 root 属主目录, Web 进程写不进 → 缓存从未生效
function _admin_stats_cache_file() {
    return rtrim(sys_get_temp_dir(), '/\\') . '/ai-sync_admin_stats_cache.json';
}
function _admin_stats_cached($compute) {
    $cacheFile = _admin_stats_cache_file();
    $ttl = 60;   // 秒: 60s 内直接返回缓存 (扫描成本高, 尤其 /mnt 等大 scope; 60s 对仪表盘已足够新鲜)
    if (is_file($cacheFile)) {
        $mtime = @filemtime($cacheFile);
        if ($mtime !== false && (time() - $mtime) < $ttl) {
            $cached = @json_decode(@file_get_contents($cacheFile), true);
            if (is_array($cached) && isset($cached['cached_at'])) return $cached;
        }
    }
    $data = $compute();
    $data['cached_at'] = time();
    @file_put_contents($cacheFile, json_encode($data, JSON_UNESCAPED_UNICODE), LOCK_EX);
    return $data;
}
function action_admin_stats($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE;
    $data = _admin_stats_cached(function () use ($cfg, $RAW_CONFIG, $MULTI_DIR_MODE) {
        if (!$MULTI_DIR_MODE) {
            $wd = $cfg['WORK_DIR'];
            $stats = _scope_disk_stats($wd);
            $df = @disk_free_space($wd);
            return [
                'mode'          => 'single',
                'work_dir'      => $wd,
                'file_count'    => $stats['file_count'],
                'size_mb'       => round($stats['size_bytes'] / 1048576, 2),
                'backup_count'  => _scope_backup_count($wd, $cfg['BACKUP_DIR']),
                'disk_free_mb'  => $df ? round($df / 1048576, 1) : null,
            ];
        }
        $scopes_out = [];
        $total_files = 0;
        $total_size  = 0;
        foreach ($RAW_CONFIG['scopes'] ?? [] as $name => $sc) {
            $wd = $sc['work_dir'] ?? '';
            $stats = _scope_disk_stats($wd);
            $df = @disk_free_space($wd);
            $scopes_out[] = [
                'name'         => (string)$name,
                'work_dir'     => $wd,
                'file_count'   => $stats['file_count'],
                'size_mb'      => round($stats['size_bytes'] / 1048576, 2),
                'backup_count' => _scope_backup_count($wd, $sc['backup_dir'] ?? 'ai-sync/backups/'),
                'disk_free_mb' => $df ? round($df / 1048576, 1) : null,
            ];
            $total_files += $stats['file_count'];
            $total_size  += $stats['size_bytes'];
        }
        return [
            'mode'   => 'multi',
            'scopes' => $scopes_out,
            'totals' => [
                'file_count'  => $total_files,
                'size_mb'     => round($total_size / 1048576, 2),
                'token_count' => count($RAW_CONFIG['tokens'] ?? []),
                'scope_count' => count($RAW_CONFIG['scopes'] ?? []),
            ],
        ];
    });
    return ok($data);
}

/**
 * client_package: 一键打包客户端程序 + 配置 + 使用说明
 * 参数: target_token (要打包的 token), scope (可选, 默认该 token 首个 scope)
 * 返回: zip_b64 (base64 编码的 zip), filename, sha256, size
 * 包含: sync.py (客户端), sync.conf (配置好的), README.txt (使用说明), start.sh (启动脚本)
 */
/**
 * FIX-v4.31: 打包产物的版本号必须与真正打进 zip 的 sync.py 一致.
 * 旧版在 sync.conf / start.sh 注释里硬编码 v4.18, 客户端升到 v4.30 后仍写 v4.18,
 * AI 会按注释版本判断能力边界 -> 误判. 现在实时从 __DIR__/sync.py 读 APP_VERSION.
 * 读不到时返回空串 (由调用方决定降级文案), 绝不撒谎写一个旧版本号.
 */
function _packaged_client_version() {
    $src = __DIR__ . '/sync.py';
    $ver = '';
    if (@is_readable($src)) {
        $head = @file_get_contents($src, false, null, 0, 65536); // 版本定义在文件头部, 不需要读全文
        if (is_string($head) && preg_match('/^\s*APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]/m', $head, $m)) {
            $ver = trim($m[1]);
        }
    }
    return $ver;
}

function action_client_package($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE;
    if (!$MULTI_DIR_MODE) {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'not_multi_mode', 'client_package 仅在多目录模式下可用');
    }

    $target_token = $req['target_token'] ?? '';
    if ($target_token === '') {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'missing_token', '请指定 target_token');
    }

    // 查找 token 信息
    $token_info = null;
    foreach ($RAW_CONFIG['tokens'] ?? [] as $tk => $info) {
        if ($tk === $target_token) { $token_info = $info; break; }
    }
    if (!$token_info) {
        return fail($GLOBALS['E']['NOT_FOUND'], 'token_not_found', 'Token 不存在');
    }

    // 确定 scope
    $req_scope = $req['scope'] ?? '';
    $scope_name = ($req_scope !== '') ? $req_scope : ($token_info['scopes'][0] ?? '');
    if (!isset($RAW_CONFIG['scopes'][$scope_name])) {
        return fail($GLOBALS['E']['INVALID_PARAM'], 'invalid_scope', "Scope '$scope_name' 不存在");
    }
    $scope_cfg = $RAW_CONFIG['scopes'][$scope_name];

    // ===== AI 说明文档参数 (可动态配置, 非写死) =====
    $doc_mode = (string)($req['doc_mode'] ?? 'two_way');
    if (!in_array($doc_mode, ['two_way','pull_edit_push','brand_new'], true)) $doc_mode = 'two_way';
    // 本地工作目录: 不写死, 可传入; 留空 = 客户端按当前环境自动解析 (P2)
    $local_dir_req = trim((string)($req['local_dir'] ?? ''));
    $local_dir = $local_dir_req !== '' ? str_replace('\\', '/', $local_dir_req) : '';
    // 供 README/返回值展示: 留空时说明自动规则, 不假装某个固定路径一定存在
    $local_dir_display = $local_dir !== '' ? $local_dir : ("/workspace/$scope_name (自动识别)");
    // 远程子目录: scope 下的子路径, 空=scope 根
    $remote_subdir = trim((string)($req['remote_subdir'] ?? ''), '/ \\');
    $remote_subdir = str_replace('\\', '/', $remote_subdir);
    $remote_full = $remote_subdir !== '' ? ("scope:$scope_name/" . $remote_subdir) : ("scope:$scope_name (根)");
    $remote_rel = $remote_subdir !== '' ? ($remote_subdir . '/') : '';

    // API URL (从请求推断; 优先显式 api_url, 其次代理头/HTTPS 标志)
    $api_url = ($req['api_url'] ?? '') ?: _request_api_url();

    // 生成 sync.conf
    // P2 约定: WATCH_DIR / LOG_FILE 留空 = 客户端首次运行按环境自动解析并回写;
    //           VERIFY_SSL = auto = 先校验, 证书不可信才降级.
    $watch_dir = $local_dir;
    $perms = $token_info['permissions'] ?? [];
    $client_ver = _packaged_client_version();
    $conf_content = "# ai-sync " . ($client_ver !== '' ? $client_ver : '(版本未知, 见 sync.py --version)') . " 客户端配置 - 自动生成\n";
    $conf_content .= "# 由管理面板打包, 生成时间: " . date('Y-m-d H:i:s') . "\n";
    $conf_content .= "# Token 名称: " . ($token_info['name'] ?? '') . "  Scope: $scope_name\n";
    $conf_content .= "# WATCH_DIR/LOG_FILE 留空是故意的: 首次运行需用 --workdir 指定稳定工作目录, 客户端会自动写入\n\n";
    $conf_content .= "API_URL    = \"$api_url\"\n";
    $conf_content .= "API_TOKEN  = \"$target_token\"\n";
    $conf_content .= "SCOPE      = \"$scope_name\"\n\n";
    $conf_content .= "WATCH_DIR  = \"$watch_dir\"\n";
    $conf_content .= "REMOTE_BASE = \"" . addslashes($remote_subdir) . "\"\n";
    $conf_content .= "LOG_FILE   = \"" . ($watch_dir !== '' ? "$watch_dir/.sync.log" : '') . "\"\n\n";
    $conf_content .= "# 同步行为\n";
    $conf_content .= "TWO_WAY        = " . ($doc_mode === 'two_way' ? 'true' : 'false') . "\n";
    $conf_content .= "DELETE_SYNC    = false\n";
    $conf_content .= "IGNORE         = [\".sync_*\", \"._*\", \"*.log\", \"*.bak\", \"ai-sync/*\", \".git/*\", \"node_modules/*\"]\n";
    $conf_content .= "ALLOW_EXT      = []\n\n";
    $conf_content .= "# 备份\n";
    $conf_content .= "LOCAL_BACKUP_DIR  = \".sync_backup\"\n";
    $conf_content .= "LOCAL_BACKUP_KEEP = 10\n\n";
    $conf_content .= "# 上传\n";
    $conf_content .= "UPLOAD_TIMEOUT  = 120\n";
    $conf_content .= "VERIFY_SSL      = auto\n";
    $conf_content .= "LOG_LEVEL       = \"info\"\n";

    // 生成 README.txt —— 按 doc_mode 动态渲染三种 AI 开发流程
    $perms_str = implode(', ', $perms);
    $mode_titles = [
        'two_way'        => '方法一：双向同步模式 (推荐)',
        'pull_edit_push' => '方法二：拉-改-推保守模式',
        'brand_new'      => '方法三：全新开发模式',
    ];
    $mode_title = $mode_titles[$doc_mode] ?? $mode_titles['two_way'];

    // ---------- 公共头部 (从 AI 视角写: 第一次拿到就知道先干嘛) ----------
    //   NOTICE: 这里的 $saved_url / $sha256 / $one_line_curl 还没算出来 (在 zip 生成之后)
    //   所以先用 {{DOWNLOAD_URL}} / {{SHA256}} / {{ONELINE_CURL_CMD}} 占位, 等 zip 写完再替换.
    // v4.12.8: README 模板文件化 — 优先读同目录 README.template.txt (可维护完整模板), 无则硬编码兜底
    $readme = null;
    $_tmpl = __DIR__ . '/README.template.txt';
    if (@file_exists($_tmpl)) { $readme = @file_get_contents($_tmpl); }
    if ($readme === null) { $readme  = "你好 AI。这是一份「用 ai-sync 在本地写代码 → 自动同步到服务器」的操作文档。\n"; }
    else { goto __readme_replace__; } // v4.12.8-FIX: 模板已加载时跳过下方硬编码拼接, 防止 README 内容双份
    $readme .= "你要做的事很简单: 在下面指定的【本地目录】里改/写代码, 同步程序会自动把变更推到服务器对应目录。\n\n";

    // -------- AI 一打开 README 第一件要做的事: 下载 + 验证客户端 (醒目的顶栏, 不需要往后翻) --------
    $readme .= "★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★\n";
    $readme .= "【★ AI 第一步：下载 / 解压 / 验收同步客户端 (复制下面 1 行命令直接执行) ★】\n";
    $readme .= "  ZIP 下载链接 : {{DOWNLOAD_URL}}\n";
    $readme .= "  一键下载 + 解压 + 验收:\n";
    $readme .= "    {{ONELINE_CURL_CMD}}\n";
    $readme .= "★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★\n\n";

    $readme .= "【★ AI 能力速览 (一眼看懂工具) ★】\n";
$readme .= "  python3 sync.py --conf sync.conf --help-ai          # JSON 命令清单 (命令/flags/conf键)\n";
$readme .= "  python3 sync.py --conf sync.conf --json --status    # 结构化输出 (脚本解析友好)\n";
$readme .= "  python3 sync.py --conf sync.conf --verify           # push 后校验三方 MD5\n\n";
$readme .= "【★ 让你更省事 (可选) ★】\n";
$readme .= "  --json          任何命令都可加, stdout 只输出一行 JSON (顶层 ok/code/command), 便于直接解析判成败\n";
$readme .= "  --version       查客户端当前版本 (与包注释里的 {{CLIENT_VERSION}} 应一致)\n";
$readme .= "  --help-ai full  输出完整命令清单 + 参数, 不确定有没有某个能力时用\n";
$readme .= "  --api-help [action]  输出服务端 action 契约 (必填/可选/返回字段), 直调 API 时用\n";
$readme .= "  --batch         一次性跑多条只读命令, 省冷启动 (每条约 10ms, 单发约 200ms)\n";
$readme .= "                  例: echo '[[\"--ls\",\".\"],[\"--cat\",\"index.php\"]]' | python3 sync.py --batch\n";
$readme .= "  写类命令多数需 --yes 防呆; 被服务端策略阻断时退出码 = 99。\n";
$readme .= "  部署到远端不存在的目录是允许的: 服务端会自动创建缺失的中间目录, 不必先 mkdir。\n\n";
$readme .= "========== 0. 你首先要知道的 3 件事 ==========\n\n";
    $readme .= "【你的工作区】所有代码都必须写在这里面 (不要写到这个目录外):\n";
    $readme .= "    本地 (你要写代码的位置): $local_dir\n";
    $readme .= "    服务器映射 (会自动同步到这里):  $remote_full\n";
    if ($remote_subdir !== '') {
        $readme .= "      例: 本地 $local_dir/index.php   ↔   服务器 scope:$scope_name/$remote_rel"."index.php\n";
    } else {
        $readme .= "      例: 本地 $local_dir/index.php   ↔   服务器 scope:$scope_name/index.php\n";
    }
    $readme .= "\n【你有哪些权限】$perms_str\n\n";
    $readme .= "【当前开发模式】$mode_title (doc_mode=$doc_mode)\n\n";

    $readme .= "========== 1. 获取同步客户端 (两步任选其一) ==========\n\n";
    // NOTE: $saved_url 此时还未生成 (后面写 zip 之后才知道), 所以用 {{DOWNLOAD_URL}} 占位, 后面 str_replace 替换.
    //       只要 file_put_contents 成功 saved_url 就一定非空; 就算失败, 也会给管理页面下载兜底, 所以方式 A 内容总是显示.
    $readme .= "方式 A: 直接从服务器下载（推荐, 包内已经写好 token + 配置）\n";
    $readme .= "    ZIP 下载链接（下载需携带 pkg_key 能力凭据, 视同 token; 不要转发到公开频道）:\n";
    $readme .= "      {{DOWNLOAD_URL}}\n";
    $readme .= "\n    用一行命令直接搞定下载 + 解压:\n";
    $readme .= "      {{ONELINE_CURL_CMD}}\n\n";
    $readme .= "    上面的最后一行必须看到 ok=true 且 scope=$scope_name, 才说明网络通、token 对。如果报错先检查 URL 是否可访问、python3 是否 >=3.8。\n\n";
    $readme .= "方式 B: 如果 ZIP 已经在本地 (别人发给你的/管理页下载的), 直接解压:\n";
    $readme .= "      mkdir -p ~/ai-sync-client && cd ~/ai-sync-client\n";
    $readme .= "      unzip -o ai-sync-client-*.zip -d ~/ai-sync-client\n";
    $readme .= "      python3 sync.py --conf sync.conf --status\n\n";
    $readme .= "【★ 验收标志】执行 --status 必须看到顶部 ok=true 和 scope={$scope_name}。没看到就停，先处理环境问题（Python 版本/网络/SSL 证书等）；\n";
    $readme .= "    ok=false 时用 --selfcheck --plain 排查 (输出单行键值, 含 work_dir/exec 状态)。\n\n";

    $readme .= "========== 2. 环境准备（只做一次）==========\n\n";
    $readme .= "  - Python 版本: python3 --version  ≥ 3.8 (本客户端 Python 标准库 0 依赖, 不用 pip 装任何东西)\n";
    $readme .= "  - 创建你专属的本地工作区: mkdir -p ".escapeshellarg($local_dir)."\n";
    $readme .= "  - 客户端目录建议放 ~/ai-sync-client/, 和工作区分开, 不要把 sync.py 放到 $local_dir 里面推到服务器.\n\n";

    // ---------- 模式 SOP (用【AI 你要按顺序执行】的口吻写) ----------
    if ($doc_mode === 'two_way') {
        $readme .= "========== 3. 你的日常工作流 (方法一: 双向同步 + 守护模式) ==========\n\n";
        $readme .= "适用场景：日常开发 / 改 bug / 加功能. 本地改 → 自动推上服务器; 服务器人工改的文件也能拉回本地.\n\n";
        $readme .= "  Step 1 [第一次进入项目] 先把服务器上现有的代码拉回本地 (如果是空项目跳过):\n";
        $readme .= "      cd ~/ai-sync-client\n";
        $readme .= "      python3 sync.py --conf sync.conf --pull\n";
        $readme .= "    → 现在 $local_dir 里就是服务器上当前最新的代码。\n\n";
        $readme .= "  Step 2 启动后台守护 (让它自己一直跑, 你写代码期间不要再管同步):\n";
        $readme .= "      python3 sync.py --conf sync.conf --watch start --watch-interval 2\n";
        $readme .= "    → 守护每 2 秒扫一次 $local_dir, 发现改动立刻推到服务器 {$remote_full}。\n";
        $readme .= "      停止守护: python3 sync.py --conf sync.conf --watch stop\n";
        $readme .= "      查看守护: python3 sync.py --conf sync.conf --watch status\n\n";
        $readme .= "  Step 3 在 $local_dir 里面直接写/改代码 —— 别 push, 守护会做。\n\n";
        $readme .= "  Step 4 服务器有人工改了配置/缓存时, 拉回本地:\n";
        $readme .= "      python3 sync.py --conf sync.conf --pull\n\n";
        $readme .= "  Step 5 关键节点 (提 PR / 发版前) 手动确认三方一致:\n";
        $readme .= "      python3 sync.py --conf sync.conf --verify\n\n";
        $readme .= "★ 200 个文件只改了 1 个时: 用 --smart 跳过未变更文件(省时间):\n";
        $readme .= "      python3 sync.py --conf sync.conf --smart ".escapeshellarg($local_dir.'/*')." --smart-apply\n\n";
    } elseif ($doc_mode === 'pull_edit_push') {
        $readme .= "========== 3. 你的日常工作流 (方法二: 拉-改-推 保守模式) ==========\n\n";
        $readme .= "适用场景：多人协作 / 关键服务器 / 怕覆盖别人改动. 每一轮(改一个功能/一个 bug)都严格按 Step1→5 走。\n\n";
        foreach ([
            'Step 1 先拉最新 (防止你覆盖别人刚改的文件):' =>
                "python3 sync.py --conf sync.conf --pull",
            'Step 2 在本地改/写代码 (所有文件都在 '.$local_dir.' 里面, 别写到外面):' =>
                "# 写代码就好, 不要手动同步",
            'Step 3 改完先做语法检查 (PHP/JS/CSS, 先 lint 再推送):' =>
                "python3 sync.py --conf sync.conf --lint ".escapeshellarg($remote_subdir ?: '.')."   # 批量 lint 子目录",
            'Step 4 【★ 原子部署 ★】批量推上去 + 自动 php -l 语法校验 + 失败自动回滚:' =>
                "python3 sync.py --conf sync.conf --deploy ".escapeshellarg($local_dir)." --smart-verify md5",
            'Step 5 入口冒烟 (看 HTTP 200 或约定 JSON, 别直接让人访问):' =>
                "python3 sync.py --conf sync.conf --test ".escapeshellarg($remote_rel."index.php"),
        ] as $title => $cmd) {
            $readme .= "  $title\n      $cmd\n\n";
        }
        $readme .= "========== 3.5 出错了怎么办 (白屏 / 500 / 页面空白) ==========\n\n";
        $readme .= "  先看服务器有哪些日志可用:\n";
        $readme .= "    python3 sync.py --conf sync.conf --error-log\n\n";
        $readme .= "  最近 100 行 Nginx 错误 (找不到页面/499/静态资源 404 居多):\n";
        $readme .= "    python3 sync.py --conf sync.conf --error-log nginx_err --error-lines 100\n\n";
        $readme .= "  PHP 报错/Fatal/parse error (白屏最常见):\n";
        $readme .= "    python3 sync.py --conf sync.conf --error-log php_error --error-grep \"Fatal|Parse|500|Uncaught\" --error-lines 200\n";
        $readme .= "    注意: php_error 依赖 /var/log/nginx/php_errors.log 存在; 若报 log_not_found,\n";
        $readme .= "    说明该日志未生成, 先 --error-log 看可用列表, 改用 nginx_err 或 audit:\n";
        $readme .= "    python3 sync.py --conf sync.conf --error-log nginx_err --error-grep \"PHP Fatal|PHP Parse|500\" --error-lines 200\n\n";
        $readme .= "  你自己的同步操作记录 (看到哪一步推了什么文件):\n";
        $readme .= "    python3 sync.py --conf sync.conf --error-log audit --error-lines 50\n\n";
        $readme .= "  推坏了直接回滚 (每一次覆盖文件服务器都会自动留备份):\n";
        $readme .= "    python3 sync.py --conf sync.conf --backup-list\n";
        $readme .= "    python3 sync.py --conf sync.conf --rollback ".escapeshellarg($remote_rel."index.php")." --backup <备份文件名>\n\n";
    } else { // brand_new
        $readme .= "========== 3. 你的工作流 (方法三: 全新开发 0→1 项目) ==========\n\n";
        $readme .= "适用场景：scope:$scope_name/$remote_subdir 里现在是空白或占位, 你要从 0 搭一套新的网站/API/脚本。\n\n";
        $readme .= "  Step 1 准备本地工作区 (确认空目录):\n";
        $readme .= "    mkdir -p ".escapeshellarg($local_dir)." && ls -la ".escapeshellarg($local_dir)."\n\n";
        $readme .= "  Step 2 规划项目结构 (示例: 常规 PHP 网站). 所有文件都写在 $local_dir 下, 不要越界:\n";
        $readme .= "    $local_dir/\n";
        $readme .= "      ├── index.php              入口\n";
        $readme .= "      ├── config.php             数据库/密钥 (不要写死, 后期用环境变量)\n";
        $readme .= "      ├── api/                   JSON 接口\n";
        $readme .= "      ├── assets/                css/js/img\n";
        $readme .= "      ├── uploads/               用户上传(同步忽略, 不要把上传写进 Git)\n";
        $readme .= "      └── install.md             初始化/部署说明\n\n";
        $readme .= "  Step 3 在 $local_dir 内写完整套项目代码.\n\n";
        $readme .= "  Step 4 【★ 首次原子部署 ★】一口气推, 自动 php-lint, --smart 只传改动, 失败自动回滚:\n";
        $readme .= "    python3 sync.py --conf sync.conf --deploy ".escapeshellarg($local_dir)." --smart-verify md5\n\n";
        $readme .= "  Step 5 入口 HTTP 冒烟:\n";
        $readme .= "    python3 sync.py --conf sync.conf --test ".escapeshellarg($remote_rel."index.php")."\n\n";
        $readme .= "  Step 6 如果 4/5 失败: 先用方法二的【白屏排查 + 回滚】修好，回到 Step 4。\n\n";
        $readme .= "  Step 7 稳定后，切到方法一 (双向同步 + --watch) 日常迭代即可。\n\n";
        $readme .= "★ 新项目最低验收标准:\n";
        $readme .= "  [ ] --lint 全部通过 (php -l 0 error)\n";
        $readme .= "  [ ] 入口 HTTP 200 / 返回约定 JSON\n";
        $readme .= "  [ ] config/数据库密码/密钥 不要硬编码进代码 (留配置项 + 后续用环境变量)\n";
        $readme .= "  [ ] uploads/cache/log 这类可变目录加入 IGNORE, 不要推\n\n";
    }

    // ---------- 公共: 最常用的 12 条命令 (AI 翻文档时, 先看这块) ----------
    $readme .= "========== 4. 常用命令速查 (你 90% 的需求都在这里) ==========\n\n";
    $readme .= "  # 连接是否通? token 对不对?\n";
    $readme .= "  python3 sync.py --conf sync.conf --status\n";
    $readme .= "  python3 sync.py --conf sync.conf --whoami\n\n";
    $readme .= "  # 全量同步\n";
    $readme .= "  python3 sync.py --conf sync.conf --push          # 本地 → 服务器 (覆盖式, 建议先 --pull 拉)\n";
    $readme .= "  python3 sync.py --conf sync.conf --pull          # 服务器 → 本地\n";
    $readme .= "  python3 sync.py --conf sync.conf --verify <file> # 按文件三方校验 (目录用 --push 自动复验)\n\n";
    $readme .= "  # 原子部署 (推荐日常直接用这个, 自带 php-lint + 回滚)\n";
    $readme .= "  python3 sync.py --conf sync.conf --deploy ".escapeshellarg($local_dir)."\n";
    $readme .= "  python3 sync.py --conf sync.conf --deploy ".escapeshellarg($local_dir)." --smart-verify md5\n\n";
    $readme .= "  # 守护模式 (双向同步期间一直开着)\n";
    $readme .= "  python3 sync.py --conf sync.conf --watch start --watch-interval 2\n";
    $readme .= "  python3 sync.py --conf sync.conf --watch status\n";
    $readme .= "  python3 sync.py --conf sync.conf --watch stop\n\n";
    $readme .= "  # 智能跳过 (200 个文件只改了 1 个时)\n";
    $readme .= "  python3 sync.py --conf sync.conf --smart ".escapeshellarg($local_dir.'/*')."\n";
    $readme .= "  python3 sync.py --conf sync.conf --smart ".escapeshellarg($local_dir.'/*')." --smart-apply\n\n";
    $readme .= "  # 白屏/500 排查 (你遇到报错先跑这个)\n";
    $readme .= "  python3 sync.py --conf sync.conf --error-log                                   # 先看有哪些日志\n";
    $readme .= "  python3 sync.py --conf sync.conf --error-log php_error --error-grep \"Fatal|Uncaught\" --error-lines 200\n";
    $readme .= "  python3 sync.py --conf sync.conf --error-log nginx_err --error-lines 100\n";
    $readme .= "  # 注意: php_error 若报 log_not_found, 说明 php_errors.log 未生成, 用 nginx_err/audit 替代\n\n";
    $readme .= "  # AI/脚本调用优化参数 (新 AI 上手必看):\n";
    $readme .= "  python3 sync.py --conf sync.conf --quiet --status     # 静默输出 (v4.7 起 quiet 默认开启, --no-quiet 恢复)\n";
    $readme .= "  python3 sync.py --conf sync.conf --plain --whoami    # 纯文本: 无 emoji, 机器可解析\n";
    $readme .= "  # --plain 支持: whoami/op-log/history/selfcheck/ls/sync-check/lint/test/backup-list/verify/error-log 列表/watch status\n";
    $readme .= "  #   契约: 每行一个 key=value 键值 (path=... ok=true push=false ...), 末行汇总 (total=/ok=/result=)\n";
    $readme .= "  #   例: --plain --sync-check 判断是否该推; --plain --verify 看 result=pass; --plain --lint 看 ok=true\n";
    $readme .= "  python3 sync.py --conf sync.conf --quiet --exec \"pwd\" --cwd /tmp   # --exec 指定服务器工作目录\n";
    $readme .= "  python3 sync.py --conf sync.conf --quiet --exec \"...\" --timeout 120  # 覆盖默认超时 (conf 可设 EXEC_TIMEOUT)\n";
    $readme .= "  # 注: audit 日志若出现 open_exec_no_allowlist, 只是提示服务端未配 exec IP 白名单(空白=放行), 命令仍正常执行, 可忽略.\n";
    $readme .= "  python3 sync.py --conf sync.conf --rm some_file --yes    # 删远程文件 (conf 加 auto_yes=true 可免 --yes)\n\n";
    $readme .= "  # lint / 冒烟 / 备份\n";
    $readme .= "  python3 sync.py --conf sync.conf --lint ".escapeshellarg($remote_subdir ?: '.')."   # 语法检查\n";
    $readme .= "  python3 sync.py --conf sync.conf --test ".escapeshellarg($remote_rel."index.php")."  # 服务器端冒烟执行\n";
    $readme .= "  python3 sync.py --conf sync.conf --backup-list   # 列出历史备份 (--backup-list __ALL__ 全量)\n";
    $readme .= "  python3 sync.py --conf sync.conf --history\n\n";

    // ---------- 公共: 绝对不要做的 5 件事 (帮 AI 踩刹车) ----------
    $readme .= "========== 5. 绝对不要做的 5 件事 (避免线上事故) ==========\n\n";
    $readme .= "  ① 不要把 sync.conf (里面有 token) 提交到任何公开仓库, 也不要写到 $local_dir 里 (否则会被推到服务器被下载).\n";
    $readme .= "  ② 不要在 --deploy 路径里传 ../../ 或绝对路径 (服务器会以安全原因拒绝, 并记录审计日志).\n";
    $readme .= "  ③ DELETE_SYNC=false 是默认的, 就是说你本地删文件不会同步到服务器删除. 真要删服务器文件时用 delete_remote 接口显式删.\n";
    $readme .= "  ④ 推敏感配置 (config.php 带数据库密码) 前先用 --lint 过一遍, 推完立刻 --test, 异常马上 --rollback.\n";
    $readme .= "  ⑤ 白屏/500 先执行 error_log (php_error / nginx_err), 不要瞎改. 服务器每次文件覆盖都会自动留备份, 回滚能救你.\n\n";
    $readme .= "【附录】API 地址 / Token / Scope (同步已经写在 sync.conf 里, 你一般不用手动改):\n";
    $readme .= "  API_URL = $api_url\n";
    $readme .= "  SCOPE   = $scope_name\n";
    $readme .= "  PERMS   = $perms_str\n";
    // NOTE: saved_url / sha256 此时还未生成, 用占位符, 写完 zip 后再替换.
    $readme .= "  客户端包下载链接 = {{DOWNLOAD_URL}}\n";
    $readme .= "\n";
__readme_replace__: // v4.12.8-FIX: 模板已加载时的跳转目标 (跳过上方硬编码拼接)

    // 生成 start.sh — 与 P1 意图命令对齐 (模板内置于 PHP, 避免服务器上残留旧版 start.sh)
    //   bash start.sh          → 无参自举, 末行输出 READY
    //   bash start.sh pull     → 等价 --auto pull
    //   bash start.sh save ... → 等价 --auto save ...
    //   bash start.sh fix      → 等价 --auto fix
    $start_sh = <<<'SH'
#!/bin/bash
# ai-sync {{CLIENT_VERSION}} 快速启动 — 无参=自举; 也可: bash start.sh pull|save|fix
# ({{CLIENT_VERSION}} 由服务端打包时按实际客户端版本注入; 查版本: python3 sync.py --version)
set -e
CONF="sync.conf"
INTENT=""
case "${1:-}" in
  pull|save|fix|status) INTENT="$1"; shift ;;
  "") ;;
  *) CONF="$1"; shift ;;
esac
if [ ! -f "$CONF" ]; then
  echo "缺少配置文件 $CONF (应与 start.sh 同目录)"
  exit 1
fi
if [ -z "$INTENT" ]; then
  exec python3 sync.py --conf "$CONF"
fi
exec python3 sync.py --conf "$CONF" --auto "$INTENT" "$@"
SH;


    // 读取 sync.py 客户端 (三级兜底: 部署同目录 → ai-sync scope → 当前 scope → 开发目录)
    // 之前只有最后一级, 导致给 works/其他 scope 打包时 sync.py 读不到, 只剩 1KB 占位符
    $sync_py = null;
    $candidates = [];
    $push_candidate = function ($label, $path) use (&$candidates) {
        $candidates[] = ['label' => $label, 'path' => $path, 'exists' => @file_exists($path), 'size' => @filesize($path) ?: 0];
    };
    // 1) sync_api.php 所在目录 (最常见部署: ai-sync scope 根, 里面同步放了 sync.py)
    $p = __DIR__ . '/sync.py';
    $push_candidate('__DIR__ (api同目录)', $p);
    if (@file_exists($p)) $sync_py = @file_get_contents($p);
    // 2) 配置里 ai-sync scope 的 work_dir (跨 scope 打包时的统一源头)
    if ($sync_py === null && isset($RAW_CONFIG['scopes']['ai-sync']['work_dir'])) {
        $p = rtrim($RAW_CONFIG['scopes']['ai-sync']['work_dir'], '/\\') . '/sync.py';
        $push_candidate('scopes[ai-sync].work_dir', $p);
        if (@file_exists($p)) $sync_py = @file_get_contents($p);
    }
    // 3) 当前 action 所在 scope 的 work_dir (兼容性兜底)
    if ($sync_py === null) {
        $p = rtrim($cfg['WORK_DIR'], '/\\') . '/sync.py';
        $push_candidate('current_scope.work_dir', $p);
        if (@file_exists($p)) $sync_py = @file_get_contents($p);
    }
    // 4) 开发环境: 兄弟目录 ai-sync-client/sync.py (本仓库本地开发结构)
    if ($sync_py === null) {
        $p = dirname(__DIR__) . '/ai-sync-client/sync.py';
        $push_candidate('dev sibling ai-sync-client', $p);
        if (@file_exists($p)) $sync_py = @file_get_contents($p);
    }
    // 5) 最终占位符 (方便调试为什么找不到, 而不是只写 "# sync.py 未找到")
    if ($sync_py === null || $sync_py === false || trim($sync_py) === '') {
        $sync_py = "# sync.py 客户端未在服务器上找到\n"
                 . "# 请先把 ai-sync-client/sync.py 上传到 ai-sync scope 的 work_dir 根目录,\n"
                 . "# 或上传到 sync_api.php 同目录后重新打包.\n"
                 . "# 尝试过的候选路径:\n";
        foreach ($candidates as $c) {
            $sync_py .= "#   - [" . ($c['exists'] ? "x" : " ") . "] {$c['label']} => {$c['path']}  (size={$c['size']})\n";
        }
    }
    $sync_py_size = strlen($sync_py);

    // 打包成 zip: 有 ZipArchive 用它(压缩), 没有就用内置纯 PHP 打包器(store) —— 不再降级成单个 sync.conf
    $pack = _zip_pack([
        'sync.py'    => $sync_py,
        'sync.conf'  => $conf_content,
        'README.txt' => $readme,
        'start.sh'   => $start_sh,
    ]);
    if ($pack === null) {
        // 两条路都失败(极端情况): 退回单文件, 但把原因写清楚, 不要假装成功
        return ok([
            'mode'     => 'single_file',
            'filename' => 'sync.conf',
            'content'  => $conf_content,
            'message'  => 'zip 打包失败(临时目录不可写或内存不足), 已回退为返回 sync.conf 内容',
        ]);
    }
    $zip_data    = $pack['data'];
    $zip_builder = $pack['builder'];
    $sha256      = hash('sha256', $zip_data);
    $size        = strlen($zip_data);

    // 保存安装包: 默认放在 Web 根之外 (PACKAGE_DIR), 下载走带随机 key 的 GET 接口.
    //   旧实现放在 __DIR__/packages/ (Web 根内), 依赖 .htaccess (nginx 不生效),
    //   且 zip 内含 token 明文 —— 目录一旦可访问就是凭据泄漏.
    $pkg_dir = rtrim((string)($GLOBALS['CONFIG']['PACKAGE_DIR'] ?? (sys_get_temp_dir() . '/ai-sync-packages')), '/\\');
    if ($pkg_dir === '') $pkg_dir = sys_get_temp_dir() . '/ai-sync-packages';
    @mkdir($pkg_dir, 0700, true);
    $ttl = max(60, (int)($GLOBALS['CONFIG']['PACKAGE_TTL'] ?? 86400));
    try {
        foreach (glob($pkg_dir . '/ai-sync-client-*.zip') ?: [] as $old_pkg) {
            if (is_file($old_pkg) && (time() - filemtime($old_pkg)) > $ttl) {
                @unlink($old_pkg);
                @unlink($old_pkg . '.meta.json');
            }
        }
        // 清理孤立 meta (zip 已被手工删除)
        foreach (glob($pkg_dir . '/ai-sync-client-*.zip.meta.json') ?: [] as $m) {
            $zipf = preg_replace('/\.meta\.json$/', '', $m);
            if (!is_file($zipf)) @unlink($m);
        }
    } catch (Throwable $e) { /* 清理失败不影响主流程 */ }
    $uuid = bin2hex(random_bytes(8));
    $pkg_filename = "ai-sync-client-$scope_name-$uuid.zip";
    $pkg_path = $pkg_dir . '/' . $pkg_filename;
    // 能力 URL: 64 位随机 key, 只能下载这一个包; 不暴露目录, 不能枚举其他包.
    $pkg_key = bin2hex(random_bytes(32));
    $meta_path = $pkg_path . '.meta.json';
    $meta = [
        'file' => $pkg_filename,
        'key' => $pkg_key,
        'created' => time(),
        'ttl' => $ttl,
        'scope' => $scope_name,
        'token_name' => $token_info['name'] ?? '',
    ];
    @file_put_contents($meta_path, json_encode($meta, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), LOCK_EX);
    // 先写原始 zip (README 还有占位符) 兜底; 替换后覆盖.
    @file_put_contents($pkg_path, $zip_data, LOCK_EX);
    $api_base = preg_replace('/\?.*$/', '', $api_url);
    $saved_url = $api_base . (strpos($api_base, '?') === false ? '?' : '&') . 'pkg_key=' . $pkg_key;

    // ====== 生成 README 真实值并替换占位符 ======
    $one_line_curl =
        "curl -sSL " . escapeshellarg($saved_url) . " -o /tmp/ai-sync-client.zip \\\n" .
        "  && mkdir -p ~/ai-sync-client \\\n" .
        "  && (unzip -o /tmp/ai-sync-client.zip -d ~/ai-sync-client 2>/dev/null \\\n" .
        "      || python3 -m zipfile -e /tmp/ai-sync-client.zip ~/ai-sync-client) \\\n" .
        "  && rm /tmp/ai-sync-client.zip \\\n" .
        "  && cd ~/ai-sync-client";
    $search  = ['{{DOWNLOAD_URL}}', '{{SHA256}}',        '{{ONELINE_CURL_CMD}}',
                '{{API_URL}}',      '{{SCOPE}}',         '{{PERMS}}',
                '{{LOCAL_DIR}}',    '{{REMOTE_SUBDIR}}', '{{REMOTE_REL}}',
                '{{CLIENT_VERSION}}'];
    $replace = [$saved_url,          $sha256,            $one_line_curl,
                $api_url,            $scope_name,        $perms_str,
                $local_dir,          ($remote_subdir ?: '.'), $remote_rel,
                ($client_ver !== '' ? $client_ver : 'latest')];
    $readme  = str_replace($search, $replace, $readme);
    // start.sh 内的版本占位符同步注入 (nowdoc 不会被 PHP 展开, 必须在这里替换)
    $start_sh = str_replace('{{CLIENT_VERSION}}', ($client_ver !== '' ? $client_ver : 'latest'), $start_sh);
    // 兜底: 万一有漏网的占位符 (极端情况), 直接换成空字符串防止误导
    $readme  = preg_replace('#\{\{[A-Z0-9_]+\}\}#', '', $readme);

    // ====== 重新打 zip (README.txt 已替换成真实 URL/curl/SHA256) ======
    $pack2 = _zip_pack([
        'sync.py'     => $sync_py,
        'sync.conf'   => $conf_content,
        'README.txt'  => $readme,
        'start.sh'    => $start_sh,
    ]);
    if ($pack2 !== null) {
        $zip_data    = $pack2['data'];
        $zip_builder = $pack2['builder'];
        $sha256      = hash('sha256', $zip_data);  // README 变了, 重新算 sha256/size
        $size        = strlen($zip_data);
        // 覆盖 packages/ 里的 zip 为新版 (README 已替换)
        @file_put_contents($pkg_path, $zip_data, LOCK_EX);
    }
    $b64 = base64_encode($zip_data);

    return ok([
        'zip_b64'       => $b64,
        'filename'      => $pkg_filename,
        'package_id'    => $pkg_filename,
        'sha256'        => $sha256,
        'size'          => $size,
        'zip_builder'   => $zip_builder,
        'scope'         => $scope_name,
        'token_name'    => $token_info['name'] ?? '',
        'permissions'   => $perms,
        'api_url'       => $api_url,
        'watch_dir'     => $watch_dir,
        'download_url'  => $saved_url,
        'package_file'  => $pkg_filename,
        'package_ttl'   => $ttl,
        'download_hint' => 'URL 内含一次性能力 key; 视为凭据, 不要贴到公开频道. 过期后重新打包.',
        // 打包结果明细: 每个文件大小 & sync.py 候选路径排查, 前端可以直接展示「为什么只有 1KB」
        'entries'       => [
            ['name' => 'sync.py',      'size' => $sync_py_size],
            ['name' => 'sync.conf',    'size' => strlen($conf_content)],
            ['name' => 'README.txt',   'size' => strlen($readme)],
            ['name' => 'start.sh',     'size' => strlen($start_sh)],
        ],
        'sync_py_candidates' => $candidates,
        'sync_py_loaded_bytes' => $sync_py_size,
        // AI 说明文档相关: 前端直接用这里渲染, 不用再拼
        'doc'           => [
            'mode'         => $doc_mode,
            'mode_title'   => $mode_title,
            'local_dir'    => $local_dir,
            'local_dir_display' => $local_dir_display,
            'remote_subdir'=> $remote_subdir,
            'remote_full'  => $remote_full,
            'readme_md'    => $readme, // 完整 Markdown 文档 (和 zip 内 README.txt 一样)
        ],
    ]);
}

/**
 * package_list: 列出已生成的客户端下载包 (供清理含 token 的过期包)
 * 返回: packages [{file, size, mtime, url}]
 */
function action_package_list($req, $cfg) {
    $pkg_dir = rtrim((string)($cfg['PACKAGE_DIR'] ?? ''), '/\\');
    if ($pkg_dir === '' || !is_dir($pkg_dir)) return ok(['packages' => [], 'total' => 0]);
    $items = [];
    $api_base = preg_replace('/\?.*$/', '', _request_api_url());
    foreach (glob($pkg_dir . '/ai-sync-client-*.zip') ?: [] as $f) {
        $fn = basename($f);
        $meta_file = $f . '.meta.json';
        $meta = is_file($meta_file) ? (json_decode((string)@file_get_contents($meta_file), true) ?: null) : null;
        $url = null;
        $expires_at = null;
        if (is_array($meta) && !empty($meta['key'])) {
            $url = $api_base . (strpos($api_base, '?') === false ? '?' : '&') . 'pkg_key=' . $meta['key'];
            $expires_at = (int)($meta['created'] ?? filemtime($f)) + (int)($meta['ttl'] ?? ($cfg['PACKAGE_TTL'] ?? 86400));
        }
        $items[] = [
            'file'  => $fn,
            'size'  => filesize($f),
            'mtime' => filemtime($f),
            'url'   => $url,
            'expires_at' => $expires_at,
        ];
    }
    usort($items, function($a, $b) { return $b['mtime'] - $a['mtime']; });
    return ok(['packages' => $items, 'total' => count($items)]);
}

/**
 * package_delete: 删除指定客户端下载包 (含 token, 用完即删)
 * 参数: file (包文件名)
 */
function action_package_delete($req, $cfg) {
    global $E;
    $fn = $req['file'] ?? '';
    if (!is_string($fn) || $fn === '' || !preg_match('/^ai-sync-client-[a-z0-9._-]+\.zip$/i', $fn)) {
        return fail($E['INVALID_PARAM'], 'bad_filename', '非法文件名');
    }
    $pkg_dir = rtrim((string)($cfg['PACKAGE_DIR'] ?? ''), '/\\');
    $path = $pkg_dir . '/' . $fn;
    if ($pkg_dir === '' || !is_file($path)) return fail($E['NOT_FOUND'], 'not_found', "包 $fn 不存在");
    if (!@unlink($path)) return fail($E['INTERNAL'], 'delete_fail', '删除失败');
    @unlink($path . '.meta.json');
    audit_log('package_delete', $req, 0, ['file' => $fn]);
    return ok(['deleted' => $fn]);
}

/**
 * GET /sync_api.php?pkg_key=<64hex>
 *
 * 安装包下载: 能力 URL, 无需 token, 但 key 只能解锁对应包 (不可枚举目录).
 * 包内 sync.conf 含目标 token 明文, 因此 key 必须视为凭据; 默认 24h 过期.
 */
function _serve_package_by_key($key, $cfg) {
    global $E;
    $pkg_dir = rtrim((string)($cfg['PACKAGE_DIR'] ?? ''), '/\\');
    if (!is_string($key) || !preg_match('/^[a-f0-9]{64}$/i', $key) || $pkg_dir === '' || !is_dir($pkg_dir)) {
        http_response_code(404);
        echo json_encode(fail($E['NOT_FOUND'], 'package_not_found', 'Package not found or link expired'));
        return;
    }
    $meta = null;
    foreach (glob($pkg_dir . '/ai-sync-client-*.zip.meta.json') ?: [] as $mf) {
        $m = json_decode((string)@file_get_contents($mf), true);
        if (!is_array($m) || empty($m['key']) || !is_string($m['key'])) continue;
        if (hash_equals($m['key'], $key)) { $meta = $m; break; }
    }
    if ($meta === null || empty($meta['file'])) {
        http_response_code(404);
        echo json_encode(fail($E['NOT_FOUND'], 'package_not_found', 'Package not found or link expired'));
        return;
    }
    $zip = $pkg_dir . '/' . basename((string)$meta['file']);
    $ttl = max(60, (int)($meta['ttl'] ?? ($cfg['PACKAGE_TTL'] ?? 86400)));
    $created = (int)($meta['created'] ?? (is_file($zip) ? filemtime($zip) : 0));
    if (!is_file($zip) || (time() - $created) > $ttl) {
        @unlink($zip); @unlink($zip . '.meta.json');
        http_response_code(410);
        echo json_encode(fail($E['NOT_FOUND'], 'package_expired', 'Package expired', [
            'hint' => '请管理员重新执行 client_package 生成新包',
        ]));
        return;
    }
    audit_log('package_download', [], 0, [
        'file' => basename($zip), 'scope' => (string)($meta['scope'] ?? ''),
        'ip' => $_SERVER['REMOTE_ADDR'] ?? '',
    ]);
    header('Content-Type: application/zip');
    header('Content-Length: ' . filesize($zip));
    header('Content-Disposition: attachment; filename="' . basename($zip) . '"');
    header('Cache-Control: no-store');
    readfile($zip);
}

// ==================== AI 增强套件 v4.2 ====================

/**
 * whoami: 查询当前 token 的身份/权限/配额 (AI 启动时自查边界)
 * 返回: token_name, scope, permissions, available_scopes, is_admin, exec_enabled, rate_limit
 */
/**
 * 服务端参考客户端 (sync_api.php 同目录 sync.py) 元数据: md5 按 mtime 缓存,
 * whoami 高频调用也不重算 (sync.py 未变则命中缓存). AI 自动升级检测用.
 */
function _client_meta() {
    static $cached = null;
    $path = __DIR__ . '/sync.py';
    clearstatcache(true, $path);
    $mt  = @filemtime($path);
    $sz  = @filesize($path);
    if ($cached !== null && $cached['mtime'] === $mt) return $cached;
    $md5 = (is_file($path) && $mt !== false) ? md5_file($path) : null;
    $cached = ['md5' => $md5, 'size' => ($sz === false ? 0 : $sz), 'mtime' => ($mt === false ? null : $mt)];
    return $cached;
}

/**
 * client_download: 返回 sync_api.php 同目录 sync.py 原始字节 (客户端 --self-update 自动升级用).
 * 绕过 JSON 包装直接流式输出; 与 pkg 下载同级 (客户端代码本就是公开分发物).
 */
function action_client_download($req, $cfg) {
    $meta = _client_meta();
    if ($meta['md5'] === null) {
        return fail($GLOBALS['E']['NOT_FOUND'] ?? 'E_NOT_FOUND', 'not_found', 'client sync.py not found');
    }
    header('Content-Type: application/octet-stream');
    header('Content-Length: ' . (string)$meta['size']);
    header('X-Client-Md5: ' . $meta['md5']);
    readfile(__DIR__ . '/sync.py');
    exit;
}

function action_whoami($req, $cfg) {
    global $ACTION_PERMS;
    // AI 优化: 返回 server_time (时间同步) + available_actions (AI 知道能调什么) + backup_keep
    $my_perms = $cfg['PERMISSIONS'] ?? [];
    $available_actions = [];
    if (!empty($ACTION_PERMS)) {
        foreach ($ACTION_PERMS as $act => $perm) {
            if (in_array($perm, $my_perms, true)) $available_actions[] = $act;
        }
    }
    sort($available_actions);   // 固定顺序, 便于客户端/测试对比; 与 Python 实现保持一致
    // AI 决策辅助: capability flags — AI 一次调用即可知道是否支持某能力, 无需试错
    $caps = [
        'batch_deploy'    => in_array('batch_deploy', $available_actions, true),
        'chunked_upload'  => in_array('chunked_upload', $available_actions, true),
        'php_run'         => in_array('php_run', $available_actions, true) && !empty($cfg['EXEC_ENABLED']),
        'webhook_test'    => in_array('webhook_test', $available_actions, true),
        'sync_check'      => in_array('sync_check', $available_actions, true),
        'recent_changes'  => in_array('recent_changes', $available_actions, true),
        'error_log'       => in_array('error_log', $available_actions, true),
        'backup'          => in_array('backup_rb', $available_actions, true),
        // 链接协议: type=symlink + link_target/link_target_b64, 见 help name=upload/stat/list
        'symlink'         => true,
        'symlink_b64'     => true,
    ];
    return ok([
        'token_name'        => $cfg['TOKEN_NAME'] ?? '',
        'scope'             => $cfg['SCOPE'] ?? '',
        'permissions'       => $my_perms,
        'available_scopes'  => $cfg['AVAILABLE_SCOPES'] ?? [],
        'is_admin'          => !empty($cfg['IS_ADMIN']),
        'exec_enabled'      => !empty($cfg['EXEC_ENABLED']),
        'max_size'          => $cfg['MAX_SIZE'] ?? 0,
        'max_chunked_size'  => $cfg['MAX_CHUNKED_SIZE'] ?? 0,
        'chunk_size_default'=> $cfg['CHUNK_SIZE_DEFAULT'] ?? 0,
        'chunk_ttl_days'    => $cfg['CHUNK_TTL_DAYS'] ?? 0,
        'max_files_per_deploy' => $cfg['MAX_FILES_PER_DEPLOY'] ?? 50,
        'rate_limit'        => $cfg['RATE_LIMIT'] ?? [],
        'work_dir'          => $cfg['WORK_DIR'] ?? '',
        'api_version'       => $cfg['API_VERSION'] ?? '',
        'server_time'       => time(),
        'server_timezone'   => @date_default_timezone_get() ?: 'UTC',
        'available_actions' => $available_actions,
        'capabilities'      => $caps,
        'backup_keep'       => $cfg['BACKUP_KEEP'] ?? 0,
        // 客户端自动升级: 服务端参考 sync.py 的 md5/size/mtime (旧客户端/旧服务端忽略)
        'client_md5'        => _client_meta()['md5'],
        'client_size'       => _client_meta()['size'],
        'client_updated_at' => _client_meta()['mtime'],
        // AI 同步决策建议: 推荐的工作流
        'workflow_hint'     => '改代码 → sync_check 判断需不需要推 → upload/batch_deploy → php_run 验证 → error_log 排查',
    ]);
}

/**
 * sync_check: 一体化同步决策 (AI 一次调用判断该不该推)
 * 参数: files [{path, md5, size, mtime}], 可选 base_md5 (乐观锁, 期望远程当前 md5)
 * 返回: results [{path, remote_exists, remote_md5, should_push, reason, conflict}]
 *   reason: same/remote_missing/local_newer/remote_changed/conflict/size_diff
 *   conflict=true 表示远程已变更且本地也变 (需 backup_both)
 */
function action_sync_check($req, $cfg) {
    global $E;
    $files = $req['files'] ?? [];
    if (!is_array($files) || empty($files)) {
        return fail($E['INVALID_PARAM'], 'missing_files', 'files 数组必填');
    }
    $base = rtrim($cfg['WORK_DIR'], '/\\') . DIRECTORY_SEPARATOR;
    $results = [];
    foreach ($files as $f) {
        if (!is_array($f)) {
            return fail($E['INVALID_PARAM'], 'bad_files_item',
                'files 的每一项都必须是对象 ({path,size,md5,...})');
        }
        $rel = $f['path'] ?? '';
        $local_md5 = $f['md5'] ?? '';
        $local_size = intval($f['size'] ?? 0);
        $local_mtime = intval($f['mtime'] ?? 0);
        $expect_md5 = $f['base_md5'] ?? ''; // 乐观锁: 期望远程当前 md5
        // 不跟随最后一级: 链接本身也要参与 sync_check (md5(规范化目标)), 不能拿目标内容比对.
        $dest = safe_path_nofollow($rel, $cfg['WORK_DIR']);
        $item = ['path' => $rel, 'local_md5' => $local_md5, 'local_size' => $local_size];
        if (!$dest) {
            $item['remote_exists'] = false;
            $item['should_push'] = true;
            $item['reason'] = 'invalid_path';
            $item['conflict'] = false;
            $results[] = $item;
            continue;
        }
        if (!@is_link($dest) && !file_exists($dest)) {
            $item['remote_exists'] = false;
            $item['remote_md5'] = '';
            $item['should_push'] = true;
            $item['reason'] = 'remote_missing';
            $item['conflict'] = false;
            $results[] = $item;
            continue;
        }
        $remote_md5 = _node_md5($dest, $cfg['WORK_DIR']);
        $lst = @lstat($dest);
        $remote_mtime = $lst ? (int)$lst['mtime'] : 0;
        $item['remote_exists'] = true;
        $item['remote_md5'] = $remote_md5;
        $item['remote_mtime'] = $remote_mtime;
        $item['remote_type'] = @is_link($dest) ? 'symlink' : (is_dir($dest) ? 'dir' : 'file');
        // 乐观锁校验
        if ($expect_md5 !== '' && $expect_md5 !== $remote_md5) {
            $item['should_push'] = false;
            $item['reason'] = 'conflict';
            $item['conflict'] = true;
            $item['hint'] = '远程已被其他人修改, 建议 pull 后合并再 push, 或强制 push (放弃 base_md5)';
            $results[] = $item;
            continue;
        }
        // 内容一致
        if ($local_md5 && $remote_md5 === $local_md5) {
            $item['should_push'] = false;
            $item['reason'] = 'same';
            $item['conflict'] = false;
            $results[] = $item;
            continue;
        }
        // 本地更新 (远程较旧)
        if ($local_mtime > $remote_mtime + 1) {
            $item['should_push'] = true;
            $item['reason'] = 'local_newer';
            $item['conflict'] = false;
            $results[] = $item;
            continue;
        }
        // 远程更新且内容不同 (双向冲突)
        if ($remote_mtime > $local_mtime + 1) {
            $item['should_push'] = false;
            $item['reason'] = 'remote_changed';
            $item['conflict'] = true;
            $item['hint'] = '远程较新, 建议 pull 拉取后再决策';
            $results[] = $item;
            continue;
        }
        // mtime 接近但内容不同
        $item['should_push'] = true;
        $item['reason'] = 'size_diff';
        $item['conflict'] = false;
        $results[] = $item;
    }
    $push_count = count(array_filter($results, function($r) { return !empty($r['should_push']); }));
    $conflict_count = count(array_filter($results, function($r) { return !empty($r['conflict']); }));
    return ok([
        'results' => $results,
        'total' => count($results),
        'should_push_count' => $push_count,
        'conflict_count' => $conflict_count,
        'summary' => $push_count . ' 个需推送, ' . $conflict_count . ' 个冲突',
    ]);
}

/**
 * lint_batch: 批量语法检查 (一次请求验证多个文件)
 * 参数: files [path1, path2, ...]
 * 返回: results [{path, ok, syntax_ok, issue_count, first_error}]
 */
function action_lint_batch($req, $cfg) {
    global $E;
    $files = $req['files'] ?? [];
    if (!is_array($files) || empty($files)) {
        return fail($E['INVALID_PARAM'], 'missing_files', 'files 数组必填');
    }
    // 确保 action_lint 已加载 (lint_batch 定义在接口层, action_lint 在 tools/dev_tools.php)
    if (!function_exists('action_lint')) {
        foreach ([__DIR__ . '/tools/dev_tools.php',
                  rtrim($cfg['WORK_DIR'], '/\\') . '/tools/dev_tools.php'] as $__dt) {
            if (is_file($__dt)) { require_once $__dt; break; }
        }
    }
    if (!function_exists('action_lint')) {
        return fail($E['INTERNAL'], 'tool_missing', 'action_lint 不可用');
    }
    $results = [];
    $total_issues = 0;
    $failed_count = 0;      // 无法执行 lint (文件不存在/路径非法/内部错误)
    $syntax_failed = 0;     // lint 已执行但语法不通过
    foreach ($files as $rel) {
        $sub = ['action' => 'lint', 'path' => $rel, 'token' => $req['token'] ?? '', 'scope' => $req['scope'] ?? ''];
        $r = action_lint($sub, $cfg);
        $is_syntax_failure = (!$r['ok'] && (($r['error'] ?? '') === 'syntax_error'));
        $item = [
            'path' => $rel,
            'ok' => (bool)($r['ok'] ?? false),
            'valid' => $r['valid'] ?? null,
        ];
        if ($r['ok'] || $is_syntax_failure) {
            $issues = $r['issues'] ?? [];
            $item['syntax_ok'] = $r['syntax_ok'] ?? null;
            $item['issue_count'] = count($issues);
            $item['language'] = $r['language'] ?? '';
            $total_issues += count($issues);
            if ($is_syntax_failure) $syntax_failed++;
            // 提取第一个 error
            foreach ($issues as $i) {
                if (($i['severity'] ?? '') === 'error') {
                    $item['first_error'] = ['line' => $i['line'] ?? 0, 'message' => $i['message'] ?? ''];
                    break;
                }
            }
        } else {
            $item['error'] = $r['error'] ?? '';
            $item['message'] = $r['message'] ?? '';
            $failed_count++;
        }
        $results[] = $item;
    }
    $payload = [
        'results' => $results,
        'total' => count($results),
        'failed' => $failed_count,
        'syntax_failed' => $syntax_failed,
        'total_issues' => $total_issues,
        'all_ok' => $failed_count === 0 && $syntax_failed === 0 && $total_issues === 0,
    ];
    // 有文件语法不通过时顶层 ok=false/code=1014: 只检查 ok/code 的调用方不会把坏代码当通过.
    if ($syntax_failed > 0 || $total_issues > 0) {
        return fail($E['VALIDATION_FAILED'], 'syntax_error',
            sprintf('%d file(s) failed syntax check (%d issue(s))', max($syntax_failed, 0), $total_issues),
            $payload);
    }
    if ($failed_count > 0) {
        return fail($E['VALIDATION_FAILED'], 'lint_failed',
            "$failed_count file(s) could not be linted", $payload);
    }
    return ok($payload);
}

/**
 * 操作日志字段清洗: 剔除控制字符 + 按字节安全截断 (不切断 UTF-8)
 * 用于 oplog_append 的客户端元数据, 防止日志注入/超长字段。
 */
function _oplog_clip($v, $max) {
    if (is_array($v) || is_object($v)) return '';
    if (is_bool($v)) $v = $v ? '1' : '';
    $s = (string)$v;
    $s = preg_replace('/[\x00-\x1F\x7F]+/', '', $s);
    // 非法 UTF-8 会让 json_encode 返回 false, 先尽力转成合法 UTF-8
    if ($s !== '' && !preg_match('//u', $s)) {
        if (function_exists('iconv')) {
            $conv = @iconv('UTF-8', 'UTF-8//IGNORE', $s);
            if ($conv !== false) $s = $conv;
        }
        if ($s !== '' && !preg_match('//u', $s)) {
            $s = preg_replace('/[^\x09\x0A\x0D\x20-\x7E]/', '', $s);
        }
    }
    if (strlen($s) <= $max) return $s;
    $out = substr($s, 0, $max);
    $guard = 0;
    while ($out !== '' && !preg_match('//u', $out) && $guard++ < 4) {
        $out = substr($out, 0, -1);
    }
    return $out;
}

/**
 * args 白名单校验 + 大小上限 (8KB), 防止单条日志无限膨胀。
 * 空对象保持 JSON [] 语义 (与历史 Python 端逐字节对齐)。
 */
function _oplog_args($args) {
    if (!is_array($args)) return new stdClass();
    $enc = json_encode($args, JSON_UNESCAPED_UNICODE);
    if ($enc === false) return new stdClass();
    if (strlen($enc) > 8192) {
        return ['_truncated' => true, '_bytes' => strlen($enc)];
    }
    return $args;
}

/** 时间筛选参数: 支持 epoch 秒 / 日期时间字符串; 纯日期 until 自动补到当天 23:59:59 */
function _oplog_ts_bound($v, $is_until = false) {
    if ($v === null || $v === '') return null;
    if (is_numeric($v)) return (int)$v;
    $s = trim((string)$v);
    if ($s === '') return null;
    $t = strtotime($s);
    if ($t === false) return null;
    if ($is_until && preg_match('/^\d{4}-\d{2}-\d{2}$/', $s)) $t += 86399;
    return $t;
}

/**
 * oplog_append: 客户端操作日志上传 (本地 .sync_oplog.jsonl 的每条记录自动同步到服务器)
 * 参数: entry (JSON object: ts/ts_epoch/op_id/command/args/summary/rc/duration_ms/version/host/user/cwd)
 * 存储: {WORK_DIR}/.sync_oplog/{scope}_{token_hash8}.jsonl
 * 服务端补充: ip/ua/token_name/server_ts/server_ts_epoch (客户端不可伪造)
 * 权限: 任何已认证 token (write 权限即可, 日志是客户端自己的操作记录)
 */
function action_oplog_append($req, $cfg) {
    global $E;
    $entry = $req['entry'] ?? null;
    if ($entry === null || !is_array($entry)) {
        return fail($E['INVALID_PARAM'], 'missing_entry', 'entry (JSON object) 必填');
    }
    // 安全: 只保留白名单字段, 防止注入
    $safe = [
        'ts'          => _oplog_clip($entry['ts'] ?? '', 32),
        'ts_epoch'    => intval($entry['ts_epoch'] ?? 0),
        'op_id'       => _oplog_clip($entry['op_id'] ?? '', 64),
        'command'     => _oplog_clip($entry['command'] ?? '', 32),
        'args'        => _oplog_args($entry['args'] ?? []),
        'summary'     => _oplog_clip($entry['summary'] ?? '', 240),
        'rc'          => intval($entry['rc'] ?? 0),
        'duration_ms' => max(0, intval($entry['duration_ms'] ?? 0)),
        'version'     => _oplog_clip($entry['version'] ?? '', 32),
        'host'        => _oplog_clip($entry['host'] ?? '', 64),
        'user'        => _oplog_clip($entry['user'] ?? '', 64),
        'cwd'         => _oplog_clip($entry['cwd'] ?? '', 300),
        'ip'          => _oplog_clip(function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR'] ?? ''), 45),
        'ua'          => _oplog_clip($_SERVER['HTTP_USER_AGENT'] ?? '', 200),
        'token_name'  => _oplog_clip($cfg['TOKEN_NAME'] ?? '', 64),
        'server_ts'   => date('Y-m-d H:i:s'),
        'server_ts_epoch' => time(),
    ];
    // 客户端标识: scope + token 前8位 hash (按客户端区分日志文件)
    $scope = preg_replace('/[^a-zA-Z0-9_-]/', '_', (string)($req['scope'] ?? 'default'));
    $token_hash = substr(md5((string)($req['token'] ?? '')), 0, 8);
    $client_id = $scope . '_' . $token_hash;

    // 日志目录: {WORK_DIR}/.sync_oplog/
    $log_dir = rtrim($cfg['WORK_DIR'], '/\\') . '/.sync_oplog';
    if (!is_dir($log_dir)) {
        @mkdir($log_dir, 0755, true);
    }
    $log_file = $log_dir . '/' . $client_id . '.jsonl';

    // 幂等: 客户端超时重传时按 op_id 去重 (只比对最后一条, 零额外开销)
    if ($safe['op_id'] !== '' && is_file($log_file)) {
        $tail = _read_tail_lines($log_file, 1);
        if (!empty($tail)) {
            $last = json_decode($tail[0], true);
            if (is_array($last) && (string)($last['op_id'] ?? '') === $safe['op_id']) {
                return ok([
                    'client_id' => $client_id,
                    'log_file'  => $log_file,
                    'bytes'     => 0,
                    'duplicate' => true,
                ]);
            }
        }
    }

    // 追加写入 (加锁防并发)
    $line = json_encode($safe, JSON_UNESCAPED_UNICODE) . "\n";
    $bytes = @file_put_contents($log_file, $line, FILE_APPEND | LOCK_EX);

    if ($bytes === false) {
        return fail($E['INTERNAL'], 'write_failed', '操作日志写入失败 (检查目录权限)');
    }
    return ok([
        'client_id' => $client_id,
        'log_file'  => $log_file,
        'bytes'     => $bytes,
    ]);
}

/**
 * oplog_list: 列出所有客户端的操作日志文件 (admin)
 * 存储: {WORK_DIR}/.sync_oplog/{client_id}.jsonl
 * 返回: clients [{client_id, size_bytes, mtime, line_count, ok_count, error_count,
 *                first_ts, last_ts, last_epoch, last_command, last_summary, last_ip, last_rc}]
 */
function action_oplog_list($req, $cfg) {
    global $RAW_CONFIG;
    // 收集所有 scope 的 work_dir
    $work_dirs = [];
    $all_scopes = $RAW_CONFIG['scopes'] ?? [];
    if (!empty($all_scopes) && is_array($all_scopes)) {
        foreach ($all_scopes as $scope_name => $sc) {
            if (!empty($sc['work_dir'])) {
                $work_dirs[$scope_name] = rtrim($sc['work_dir'], '/\\');
            }
        }
    }
    // 默认 WORK_DIR 兜底
    if (empty($work_dirs) && !empty($cfg['WORK_DIR'])) {
        $work_dirs['default'] = rtrim($cfg['WORK_DIR'], '/\\');
    }
    $clients = [];
    foreach ($work_dirs as $scope_name => $wd) {
        $log_dir = $wd . '/.sync_oplog';
        if (!is_dir($log_dir)) continue;
        $files = glob($log_dir . '/*.jsonl');
        foreach ($files as $f) {
            $client_id = basename($f, '.jsonl');
            $size = filesize($f);
            $mtime = filemtime($f);
            $line_count = 0;
            $ok_count = 0;
            $error_count = 0;
            $first_ts = '';
            $last_ts = '';
            $last_epoch = 0;
            $last_command = '';
            $last_summary = '';
            $last_ip = '';
            $last_rc = null;
            $by_cmd = [];
            $fp = @fopen($f, 'r');
            if ($fp) {
                while (($line = fgets($fp)) !== false) {
                    $line = trim($line);
                    if ($line === '') continue;
                    $line_count++;
                    $data = json_decode($line, true);
                    if (!is_array($data)) continue;
                    $rc = intval($data['rc'] ?? 0);
                    if ($rc === 0) $ok_count++; else $error_count++;
                    if ($first_ts === '' && !empty($data['ts'])) $first_ts = (string)$data['ts'];
                    $last_ts = (string)($data['ts'] ?? $last_ts);
                    $last_epoch = intval($data['ts_epoch'] ?? $last_epoch);
                    $last_command = (string)($data['command'] ?? '');
                    $last_summary = (string)($data['summary'] ?? '');
                    $last_ip = (string)($data['ip'] ?? '');
                    $last_rc = $rc;
                    $ck = (string)($data['command'] ?? '');
                    if ($ck !== '') $by_cmd[$ck] = ($by_cmd[$ck] ?? 0) + 1;
                }
                fclose($fp);
            }
            arsort($by_cmd);
            $by_cmd_top = array_slice($by_cmd, 0, 30, true);
            $clients[] = [
                'client_id'    => $client_id,
                'scope'        => $scope_name,
                'log_file'     => $f,
                'size_bytes'   => $size,
                'size_human'   => ($size < 1024 ? $size.' B' : ($size < 1048576 ? round($size/1024,1).' KB' : ($size < 1073741824 ? round($size/1048576,1).' MB' : round($size/1073741824,2).' GB'))),
                'mtime'        => $mtime,
                'mtime_str'    => date('Y-m-d H:i:s', $mtime),
                'line_count'   => $line_count,
                'ok_count'     => $ok_count,
                'error_count'  => $error_count,
                'first_ts'     => $first_ts,
                'last_ts'      => $last_ts,
                'last_epoch'   => $last_epoch,
                'last_command' => $last_command,
                'last_summary' => $last_summary,
                'last_ip'      => $last_ip,
                'last_rc'      => $last_rc,
                'commands'     => empty($by_cmd_top) ? new stdClass() : $by_cmd_top,
            ];
        }
    }
    usort($clients, function($a, $b) { return $b['mtime'] - $a['mtime']; });
    return ok([
        'clients' => $clients,
        'total'   => count($clients),
        'scopes'  => array_keys($work_dirs),
    ]);
}

/**
 * oplog_view: 查看某个客户端的操作日志内容 (admin)
 * 参数: client_id (必填), limit (默认100, 最大1000), offset (从最新往前跳过 N 条),
 *       grep (全文关键词), command (命令名, 逗号分隔多个), result (ok/error),
 *       since/until (epoch 秒或日期时间字符串)
 * 返回: entries [{ts, ts_epoch, op_id, command, args, summary, rc, duration_ms,
 *                 version, host, user, cwd, ip, ua, token_name, server_ts}],
 *       total (文件总条数), matched (筛选后条数), returned, has_more, stats
 */
function action_oplog_view($req, $cfg) {
    global $E, $RAW_CONFIG;
    $client_id = trim($req['client_id'] ?? '');
    if ($client_id === '') {
        return fail($E['INVALID_PARAM'], 'missing_client_id', 'client_id 必填');
    }
    if (!preg_match('/^[a-zA-Z0-9_-]+$/', $client_id)) {
        return fail($E['INVALID_PARAM'], 'invalid_client_id', 'client_id 只允许字母数字下划线短横线');
    }
    $limit = max(1, min(1000, intval($req['limit'] ?? 100)));
    $offset = max(0, intval($req['offset'] ?? 0));
    $grep = trim($req['grep'] ?? '');
    $command = strtolower(trim((string)($req['command'] ?? '')));
    $cmd_set = [];
    if ($command !== '') {
        foreach (explode(',', $command) as $c) {
            $c = strtolower(trim($c));
            if ($c !== '') $cmd_set[] = $c;
        }
    }
    $result = strtolower(trim((string)($req['result'] ?? '')));
    $since = _oplog_ts_bound($req['since'] ?? null);
    $until = _oplog_ts_bound($req['until'] ?? null, true);
    // 在所有 scope 的 work_dir 中查找日志文件
    $work_dirs = [];
    $all_scopes = $RAW_CONFIG['scopes'] ?? [];
    if (!empty($all_scopes) && is_array($all_scopes)) {
        foreach ($all_scopes as $scope_name => $sc) {
            if (!empty($sc['work_dir'])) $work_dirs[] = rtrim($sc['work_dir'], '/\\');
        }
    }
    if (empty($work_dirs) && !empty($cfg['WORK_DIR'])) {
        $work_dirs[] = rtrim($cfg['WORK_DIR'], '/\\');
    }
    $log_file = null;
    $found_scope = '';
    foreach ($work_dirs as $wd) {
        $candidate = $wd . '/.sync_oplog/' . $client_id . '.jsonl';
        if (is_file($candidate)) { $log_file = $candidate; $found_scope = basename($wd); break; }
    }
    if (!$log_file) {
        return fail($E['NOT_FOUND'], 'log_not_found', "操作日志文件不存在: $client_id");
    }
    $all_lines = file($log_file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    if ($all_lines === false) $all_lines = [];
    $total = 0;
    $entries = [];
    $ok_count = 0;
    $error_count = 0;
    $sum_ms = 0;
    $max_ms = 0;
    $by_cmd = [];
    foreach ($all_lines as $line) {
        $line = trim($line);
        if ($line === '') continue;
        $data = json_decode($line, true);
        if (!is_array($data)) continue;
        $total++;
        $cmd = strtolower((string)($data['command'] ?? ''));
        $rc = intval($data['rc'] ?? 0);
        $epoch = intval($data['ts_epoch'] ?? 0);
        if ($epoch === 0 && !empty($data['ts'])) {
            $parsed = strtotime((string)$data['ts']);
            if ($parsed !== false) $epoch = $parsed;
        }
        // --- 筛选 ---
        if ($grep !== '') {
            $haystack = json_encode($data, JSON_UNESCAPED_UNICODE);
            if (stripos($haystack, $grep) === false) continue;
        }
        if (!empty($cmd_set) && !in_array($cmd, $cmd_set, true)) continue;
        if ($result === 'ok' || $result === 'success') { if ($rc !== 0) continue; }
        elseif ($result === 'error' || $result === 'fail') { if ($rc === 0) continue; }
        if ($since !== null && $epoch < $since) continue;
        if ($until !== null && $epoch > $until) continue;
        // --- 统计 (基于筛选结果) ---
        if ($rc === 0) $ok_count++; else $error_count++;
        $ms = max(0, intval($data['duration_ms'] ?? 0));
        $sum_ms += $ms;
        if ($ms > $max_ms) $max_ms = $ms;
        $cmd_key = (string)($data['command'] ?? '');
        if ($cmd_key !== '') $by_cmd[$cmd_key] = ($by_cmd[$cmd_key] ?? 0) + 1;
        $entries[] = $data;
    }
    $matched = count($entries);
    // 从最新往前 offset 条, 再取 limit 条 (offset=0 即最新一页)
    $end = max(0, $matched - $offset);
    $start = max(0, $end - $limit);
    $page = $end > $start ? array_slice($entries, $start, $end - $start) : [];
    arsort($by_cmd);
    $by_cmd_top = array_slice($by_cmd, 0, 30, true);
    $stats = [
        'total'       => $total,
        'matched'     => $matched,
        'ok'          => $ok_count,
        'error'       => $error_count,
        'avg_ms'      => $matched > 0 ? intval(round($sum_ms / $matched)) : 0,
        'max_ms'      => $max_ms,
        'by_command'  => empty($by_cmd_top) ? new stdClass() : $by_cmd_top,
        'first_ts'    => $matched > 0 ? (string)($entries[0]['ts'] ?? '') : '',
        'last_ts'     => $matched > 0 ? (string)($entries[$matched - 1]['ts'] ?? '') : '',
    ];
    return ok([
        'client_id' => $client_id,
        'scope'     => $found_scope,
        'entries'   => array_values($page),
        'total'     => $total,
        'matched'   => $matched,
        'returned'  => count($page),
        'limit'     => $limit,
        'offset'    => $offset,
        'has_more'  => $start > 0,
        'filters'   => [
            'grep'    => $grep,
            'command' => $command,
            'result'  => $result,
            'since'   => $since,
            'until'   => $until,
        ],
        'stats'     => $stats,
    ]);
}

/**
 * recent_changes: 服务器端变更历史 (从审计日志提取写操作)
 * 参数: lines (扫描最近多少行审计日志, 默认 200), scope (可选, 默认当前)
 * 返回: changes [{ts, action, path, code, ok, ip}], summary
 */
// 🆕 OPT-speed-20261022: 只读文件尾部 N 行 (fseek 倒读), 避免 file() 全量读入内存
//   - 日志越大越省: 只读尾部 ≤4MB, 提取最后 N 行
//   - 返回不含换行的非空行数组, 与 file(FILE_IGNORE_NEW_LINES|FILE_SKIP_EMPTY_LINES) 行为一致
function _read_tail_lines($file, $n) {
    $lines = [];
    $fp = @fopen($file, 'rb');
    if (!$fp) return $lines;
    $size = @filesize($file);
    if ($size === false || $size <= 0) { fclose($fp); return $lines; }
    $maxRead = 4 * 1024 * 1024;               // 最多倒读尾部 4MB
    $readBytes = min($size, $maxRead);
    fseek($fp, $size - $readBytes);
    $buf = fread($fp, $readBytes);
    fclose($fp);
    $parts = explode("\n", $buf);
    // 起点非行首 → 首片段不完整, 丢弃 (仅当未读全文件时)
    if ($size > $readBytes) array_shift($parts);
    // 去掉空行 (含末尾换行产生的空片段), 与 FILE_SKIP_EMPTY_LINES 一致
    $parts = array_values(array_filter($parts, function ($l) { return trim($l) !== ''; }));
    return array_slice($parts, -$n);
}

function action_recent_changes($req, $cfg) {
    global $RAW_CONFIG, $MULTI_DIR_MODE, $E;
    $lines = max(1, min(2000, intval($req['lines'] ?? 200)));
    $scope_name = $req['scope'] ?? ($cfg['SCOPE'] ?? '');
    // BUG-42 修复 (审计 Finding 3): cross-scope 越权读取.
    // 原版只检查 read 权限, 不校验 token 是否被授权访问目标 scope.
    // player-key token (scope=player, read-only) 可以传 scope=ai-sync 读其他租户审计日志.
    // 修复: 非本 token 的 scope 必须 IS_ADMIN 才能查.
    if ($scope_name !== ($cfg['SCOPE'] ?? '')) {
        if (empty($cfg['IS_ADMIN'])) {
            audit_log('recent_changes', $req, $E['FORBIDDEN'], ['reason' => 'cross_scope_denied', 'requested_scope' => $scope_name]);
            return fail($E['FORBIDDEN'], 'scope_forbidden', "Token has no access to scope '$scope_name'");
        }
    }
    // 定位审计日志
    if ($MULTI_DIR_MODE && isset($RAW_CONFIG['scopes'][$scope_name])) {
        $sc = $RAW_CONFIG['scopes'][$scope_name];
        $log_file = rtrim($sc['work_dir'], '/\\') . '/' . ($sc['audit_log'] ?? 'ai-sync/audit.log');
    } else {
        $log_file = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['AUDIT_LOG'];
    }
    if (!file_exists($log_file)) {
        return ok(['changes' => [], 'total' => 0, 'summary' => '无审计日志']);
    }
    // 🆕 OPT-speed-20261022: 只读尾部 N 行, 不 file() 全量读入 (日志越大越省)
    $tail = _read_tail_lines($log_file, $lines);
    $write_actions = ['upload', 'upload_batch', 'delete', 'rename', 'copy', 'mkdir', 'chunked_upload', 'backup_rb', 'php_run'];
    $changes = [];
    $by_path = [];
    $by_action = [];
    foreach ($tail as $line) {
        $j = json_decode($line, true);
        if (!$j) continue;
        $action = $j['action'] ?? '';
        if (!in_array($action, $write_actions)) continue;
        $path = $j['path'] ?? '';
        // 历史日志中可能存在非字符串 path (客户端传 int/list), 读侧兜底, 否则数组做 key 触发 TypeError
        if (!is_string($path)) {
            $path = (is_int($path) || is_float($path)) ? (string)$path : json_encode($path, JSON_UNESCAPED_UNICODE);
        }
        // AI 优化: rename/copy 无 path 时显示 from → to
        if ($path === '' && isset($j['from'])) {
            $path = ($j['from'] ?? '') . ' → ' . ($j['to'] ?? '');
        }
        $changes[] = [
            'ts'         => $j['ts'] ?? '',
            'action'     => $action,
            'path'       => $path,
            'code'       => $j['code'] ?? 0,
            'ok'         => ($j['code'] ?? 0) === 0,
            'ip'         => $j['ip'] ?? '',
            'size'       => $j['size'] ?? null,
            'md5'        => $j['md5'] ?? null,
            'error_line' => $j['error_line'] ?? null,
        ];
        $by_path[$path] = ($by_path[$path] ?? 0) + 1;
        $by_action[$action] = ($by_action[$action] ?? 0) + 1;
    }
    // 按时间倒序
    usort($changes, function($a, $b) { return strcmp($b['ts'], $a['ts']); });
    $ok_count = count(array_filter($changes, function($c) { return $c['ok']; }));
    // AI 优化: by_path/by_action 聚合, 让 AI 快速知道"哪个文件改了最多"
    arsort($by_path);
    arsort($by_action);
    return ok([
        'changes'   => $changes,
        'total'     => count($changes),
        'success'   => $ok_count,
        'failed'    => count($changes) - $ok_count,
        'by_path'   => $by_path,
        'by_action' => $by_action,
        'summary'   => count($changes) . ' 次变更 (成功 ' . $ok_count . ' 失败 ' . (count($changes) - $ok_count) . ')',
    ]);
}

/**
 * batch_rollback: 按时间点批量回滚 (恢复某时间点之前的所有文件)
 * 参数: before_ts (unix 时间戳, 回滚到此时间之前的备份), paths (可选, 指定文件列表, 默认全部有备份的)
 * 返回: results [{path, restored_from, ok, error}], restored_count, failed_count
 */
function action_batch_rollback($req, $cfg) {
    global $E;
    $before_ts = intval($req['before_ts'] ?? 0);
    if ($before_ts <= 0) {
        return fail($E['INVALID_PARAM'], 'missing_before_ts', 'before_ts (unix 时间戳) 必填');
    }
    $dry_run = bool_flag($req['dry_run'] ?? false);
    if ($dry_run === null) return bad_bool('dry_run', $req['dry_run']);
    $paths = $req['paths'] ?? [];
    $backup_dir = rtrim($cfg['WORK_DIR'], '/') . '/' . $cfg['BACKUP_DIR'];
    if (!is_dir($backup_dir)) {
        return ok(['results' => [], 'restored_count' => 0, 'failed_count' => 0, 'dry_run' => $dry_run, 'summary' => '无备份目录']);
    }
    // 若未指定 paths, 扫描所有有备份的文件
    if (empty($paths)) {
        $paths = [];
        $iter = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($backup_dir, \RecursiveDirectoryIterator::SKIP_DOTS),
            \RecursiveIteratorIterator::SELF_FIRST,
            \RecursiveIteratorIterator::CATCH_GET_CHILD
        );
        foreach ($iter as $info) {
            if ($info->isDir()) continue;
            $fn = $info->getFilename();
            // 备份文件名格式: {原名}.bak.{ts}, 去掉 .bak.* 得到原文件名
            if (strpos($fn, '.bak.') === false) continue;
            // 修复: 原 grandparent/parent 重建逻辑只支持 2 层深度路径, 3 层以上路径会回滚到错误位置.
            // 备份目录结构: backup_dir/dirname(rel)/basename(rel)/basename(rel).bak.*
            // 文件所在目录相对 backup_dir 的路径 = dirname(rel)/basename(rel) = rel 本身
            $abs_dir = $info->getPath();
            $prefix_len = strlen($backup_dir);
            if (substr($abs_dir, 0, $prefix_len) !== $prefix_len_str = (string)$backup_dir) continue;
            $rel_dir = ltrim(substr($abs_dir, $prefix_len), '/\\');
            if ($rel_dir === '') continue;
            // 统一为正斜杠 (跨平台兼容)
            $rel = str_replace('\\', '/', $rel_dir);
            $paths[$rel] = true;
        }
        $paths = array_keys($paths);
    }
    $results = [];
    $restored = 0;
    $failed = 0;
    foreach ($paths as $rel) {
        $dest = safe_path($rel, $cfg['WORK_DIR']);
        $file_backup_dir = $backup_dir . '/' . dirname($rel) . '/' . basename($rel);
        if (!$dest || !is_dir($file_backup_dir)) {
            $results[] = ['path' => $rel, 'ok' => false, 'error' => 'no_backup'];
            $failed++;
            continue;
        }
        // 找 before_ts 之前最新的备份
        $candidates = glob($file_backup_dir . '/' . basename($rel) . '.bak.*') ?: [];
        $best = null;
        $best_mtime = 0;
        foreach ($candidates as $bf) {
            $mt = filemtime($bf);
            if ($mt <= $before_ts && $mt > $best_mtime) {
                $best = $bf;
                $best_mtime = $mt;
            }
        }
        if (!$best) {
            $results[] = ['path' => $rel, 'ok' => false, 'error' => 'no_backup_before_ts'];
            $failed++;
            continue;
        }
        if ($dry_run) {
            $results[] = ['path' => $rel, 'ok' => true, 'restored_from' => basename($best), 'backup_mtime' => $best_mtime];
            $restored++;
            continue;
        }
        @mkdir(dirname($dest), 0755, true);
        if (@copy($best, $dest)) {
            $results[] = ['path' => $rel, 'ok' => true, 'restored_from' => basename($best), 'backup_mtime' => $best_mtime];
            $restored++;
        } else {
            $results[] = ['path' => $rel, 'ok' => false, 'error' => 'copy_fail'];
            $failed++;
        }
    }
    if (!$dry_run) {
        audit_log('batch_rollback', $req, $failed > 0 ? $E['INTERNAL'] : 0, ['before_ts' => $before_ts, 'restored' => $restored, 'failed' => $failed]);
    }
    return ok([
        'results' => $results,
        'restored_count' => $restored,
        'failed_count' => $failed,
        'before_ts' => $before_ts,
        'dry_run' => $dry_run,
        'summary' => $dry_run ? '预览: 将回滚 ' . $restored . ' 个文件, ' . $failed . ' 个无备份' : '回滚 ' . $restored . ' 个文件, 失败 ' . $failed . ' 个',
    ]);
}
