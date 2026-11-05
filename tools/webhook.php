<?php
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 金鑫

/*
 * ai-sync v3.2 Tool: Webhook
 * webhook_set / webhook_list / webhook_delete / trigger_webhooks
 */

/* P0-2 fix: webhook URL 安全校验 - scheme 白名单 + 内网 IP 过滤 */
/**
 * 校验 webhook id 格式: wh_ + 10 位 hex (md5 截断)
 * 修复: webhook_delete 直接拼 $dir . $id . '.json', $id='../../x' 可删任意 .json 文件
 */
function _webhook_validate_id($id) {
    return is_string($id) && preg_match('/^wh_[0-9a-f]{10}$/', $id);
}

/**
 * 解析 host 的全部公网 IP (IPv4 + IPv6), 用于 DNS rebinding 防护.
 * 修复: 原版仅用 gethostbynamel (只返 IPv4), 纯 IPv6 主机解析为空 → 跳过校验 → 可 SSRF 内网 IPv6
 */
function _webhook_resolve_public_ips($host) {
    $ips = [];
    // IPv4
    $v4 = @gethostbynamel($host) ?: [];
    foreach ($v4 as $ip) $ips[] = $ip;
    // IPv6 (gethostbynamel 不支持 AAAA)
    $records = @dns_get_record($host, DNS_AAAA) ?: [];
    foreach ($records as $r) {
        if (!empty($r['ipv6'])) $ips[] = $r['ipv6'];
    }
    $public = [];
    foreach ($ips as $ip) {
        if (_webhook_is_public_ip($ip)) $public[] = $ip;
    }
    return $public;
}

function _webhook_validate_url($url) {
    global $E, $CONFIG;

    // 1. 基础合法 URL 校验
    if (!filter_var($url, FILTER_VALIDATE_URL)) {
        return [false, "Invalid URL: $url"];
    }
    $parts = parse_url($url);
    if (!$parts || empty($parts['scheme']) || empty($parts['host'])) {
        return [false, 'Invalid URL structure'];
    }

    // 2. scheme 白名单：仅允许 http/https（禁止 file:///gopher:///dict:// 等 SSRF 危险协议）
    $allowed_schemes = !empty($CONFIG['WEBHOOK_ALLOWED_SCHEMES'])
        ? $CONFIG['WEBHOOK_ALLOWED_SCHEMES']
        : ['http', 'https'];
    if (!in_array(strtolower($parts['scheme']), $allowed_schemes, true)) {
        return [false, "URL scheme '{$parts['scheme']}' not allowed (only: " . implode(',', $allowed_schemes) . ")"];
    }

    // 3. 内网/回环/私网 IP 过滤
    $host = $parts['host'];
    $allow_internal = !empty($CONFIG['WEBHOOK_ALLOW_INTERNAL']);

    // 3a. 直接是 IP
    if (filter_var($host, FILTER_VALIDATE_IP)) {
        if (!$allow_internal && !_webhook_is_public_ip($host)) {
            return [false, "Private/loopback/link-local IP '$host' not allowed. Set WEBHOOK_ALLOW_INTERNAL=true to override."];
        }
        return [true, null];
    }

    // 3b. 是域名，DNS 解析后逐 IP 检查（若内网无 DNS 则保守策略：WEBHOOK_ALLOW_INTERNAL=true 时跳过）
    if (!$allow_internal) {
        $records = @dns_get_record($host, DNS_A + DNS_AAAA);
        if ($records) {
            foreach ($records as $r) {
                $ip = $r['ip'] ?? $r['ipv6'] ?? null;
                if ($ip && !_webhook_is_public_ip($ip)) {
                    return [false, "Resolved IP '$ip' of host '$host' is internal/private/loopback; not allowed. Set WEBHOOK_ALLOW_INTERNAL=true if this is an internal host."];
                }
            }
        }
    }
    return [true, null];
}

function _webhook_is_public_ip($ip) {
    // 严格匹配 RFC 明确的私有 / 回环 / 链路本地段；
    // 不再启用 FILTER_FLAG_RESERVED（PHP 8.3+ 会把很多公网 IP 当成保留段）。
    $is_v4 = filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4);
    $is_v6 = filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6);
    if (!$is_v4 && !$is_v6) return false; // 非 IP 视为不安全

    // --- IPv4 黑名单（写死避免 PHP filter 版本差异） ---
    if ($is_v4) {
        // 回环 127.0.0.0/8
        if (strpos($ip, '127.') === 0) return false;
        // 私网 10.0.0.0/8
        if (strpos($ip, '10.') === 0) return false;
        // 私网 192.168.0.0/16
        if (strpos($ip, '192.168.') === 0) return false;
        // 私网 172.16.0.0/12
        if (preg_match('/^172\.(1[6-9]|2\d|3[01])\./', $ip)) return false;
        // 链路本地 169.254.0.0/16
        if (strpos($ip, '169.254.') === 0) return false;
        // 0.0.0.0/8 本网络
        if (strpos($ip, '0.') === 0) return false;
        return true;
    }

    // --- IPv6 黑名单 ---
    $low = strtolower($ip);
    if ($low === '::1' || strpos($low, '::1') === 0) return false;        // 回环
    if (strpos($low, 'fe80:') === 0) return false;                        // 链路本地 fe80::/10
    if (strpos($low, 'fc') === 0 || strpos($low, 'fd') === 0) return false; // 私网 fc00::/7
    // 修复: IPv4-mapped IPv6 (::ffff:x.x.x.x) → 提取 IPv4 部分重新校验
    if (strpos($low, '::ffff:') === 0) {
        $v4 = substr($low, 7);
        if (filter_var($v4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            return _webhook_is_public_ip($v4);
        }
        return false;
    }
    return true;
}

function action_webhook_set($req, $cfg) {
    global $E, $CONFIG;
    $url = $req['url'] ?? '';

    // P0-2 fix: 启用 URL 安全校验
    list($ok, $err) = _webhook_validate_url($url);
    if (!$ok) {
        return fail($E['INVALID_PARAM'], 'bad_url', $err);
    }

    $events = $req['events'] ?? ['upload', 'delete'];
    $id = 'wh_' . substr(md5($url . microtime(true) . mt_rand()), 0, 10);
    $secret = $req['secret'] ?? '';
    $record = [
        'id'         => $id,
        'url'        => $url,
        'events'     => $events,
        'filter'     => $req['filter'] ?? [],
        'secret'     => $secret,
        'fail_count' => 0,
        'paused'     => false,
        'created_at' => time(),
    ];
    $dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['WEBHOOK_DIR'];
    @mkdir($dir, 0755, true);
    if (file_put_contents($dir . $id . '.json', json_encode($record, JSON_UNESCAPED_UNICODE), LOCK_EX) === false) {
        return fail($E['UPLOAD_FAILED'], 'write_fail', 'Cannot write webhook record');
    }
    audit_log('webhook_set', $req, 0, ['id' => $id, 'url' => $url, 'events' => $events]);
    return ok(['id' => $id, 'url' => $url, 'events' => $events]);
}

function action_webhook_list($req, $cfg) {
    global $E, $CONFIG;
    $dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['WEBHOOK_DIR'];
    if (!is_dir($dir)) return ok(['hooks' => [], 'total' => 0]);
    $items = [];
    foreach (glob($dir . '*.json') as $f) {
        $rec = json_decode((string)@file_get_contents($f), true);
        if (!is_array($rec)) continue;
        // 修复: 仅返回白名单字段, 防 record 被篡改后泄漏 secret 或注入异常字段
        unset($rec['secret']);
        $items[] = $rec;
    }
    return ok(['hooks' => $items, 'total' => count($items)]);
}

function action_webhook_delete($req, $cfg) {
    global $E, $CONFIG;
    $id = $req['id'] ?? '';
    // 修复: 严格校验 id 格式, 防 $id='../../etc/x' 删除任意 .json (路径遍历)
    if (!_webhook_validate_id($id)) {
        audit_log('webhook_delete', array_merge($req, ['_blocked' => 'bad_id']), $E['INVALID_PARAM']);
        return fail($E['INVALID_PARAM'], 'bad_id', "webhook id 格式非法 (需 wh_+10 hex)");
    }
    $dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['WEBHOOK_DIR'];
    $file = $dir . $id . '.json';
    // 二次物理校验: 确保文件在 WEBHOOK_DIR 内
    $real_dir = @realpath($dir);
    $real_file = @realpath($file);
    if ($real_dir === false || $real_file === false
        || strpos($real_file . DIRECTORY_SEPARATOR, $real_dir . DIRECTORY_SEPARATOR) !== 0) {
        if (!file_exists($file)) return fail($E['NOT_FOUND'], 'not_found', "Hook $id not found");
        audit_log('webhook_delete', array_merge($req, ['_blocked' => 'path_escape']), $E['INVALID_PATH']);
        return fail($E['INVALID_PATH'], 'path_escape', '文件路径逃逸拦截');
    }
    if (!file_exists($real_file)) return fail($E['NOT_FOUND'], 'not_found', "Hook $id not found");
    @unlink($real_file);
    audit_log('webhook_delete', $req, 0, ['id' => $id]);
    return ok(['id' => $id, 'deleted' => true]);
}

/**
 * AI 视角优化: webhook_test - 注册前先测试 webhook 是否可达 + 返回诊断信息.
 * AI 可据此判断 URL 是否可达、签名是否正确, 避免注册一个永远失败的 webhook.
 */
function action_webhook_test($req, $cfg) {
    global $E, $CONFIG;
    $url = $req['url'] ?? '';
    list($vok, $verr) = _webhook_validate_url($url);
    if (!$vok) return fail($E['INVALID_PARAM'], 'bad_url', $verr);
    $secret = $req['secret'] ?? '';
    $payload = [
        'event'      => 'webhook_test',
        'ts'         => time(),
        'test'       => true,
        'path'       => $req['path'] ?? '',
    ];
    if ($secret !== '') {
        $payload['signature'] = 'sha256=' . hash_hmac('sha256', json_encode($payload), $secret);
    }
    $body = json_encode($payload, JSON_UNESCAPED_UNICODE);
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $body,
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER         => true,
        CURLOPT_TIMEOUT        => 8,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_PROTOCOLS      => defined('CURLPROTO_HTTP') ? (CURLPROTO_HTTP | CURLPROTO_HTTPS) : 3,
    ]);
    // DNS rebinding 防护: 锁定已校验的公网 IP
    // 修复 P1-1: 解析不到公网 IP 时拒绝测试 (防 rebind 到内网), 除非显式允许内网
    $_hk = parse_url($url);
    $_resolved_ok = false;
    if (!empty($_hk['host'])) {
        $_hname = $_hk['host'];
        $_hport = $_hk['port'] ?? (($_hk['scheme'] ?? '') === 'https' ? 443 : 80);
        foreach (_webhook_resolve_public_ips($_hname) as $_hip) {
            curl_setopt($ch, CURLOPT_RESOLVE, ["$_hname:$_hport:$_hip"]);
            $_resolved_ok = true;
            break;
        }
    }
    if (!$_resolved_ok && empty($cfg['WEBHOOK_ALLOW_INTERNAL'])) {
        curl_close($ch);
        audit_log('webhook_test', $req, $E['WEBHOOK_FAILED'], [
            'url' => $url, 'reason' => 'dns_rebinding_blocked',
        ]);
        return fail($E['WEBHOOK_FAILED'], 'dns_rebinding_blocked',
            '无法解析到公网 IP (可能 DNS rebind 到内网). 如目标是内网服务, 设置 WEBHOOK_ALLOW_INTERNAL=true',
            ['url' => $url, 'hint' => 'webhook 目标必须解析到公网 IP, 防 SSRF']);
    }
    $resp = curl_exec($ch);
    $http_code = intval(curl_getinfo($ch, CURLINFO_HTTP_CODE));
    $curl_err = curl_error($ch);
    $total_time = curl_getinfo($ch, CURLINFO_TOTAL_TIME);
    curl_close($ch);
    $passed = ($http_code >= 200 && $http_code < 300);
    audit_log('webhook_test', $req, $passed ? 0 : $E['WEBHOOK_FAILED'], [
        'url' => $url, 'http_code' => $http_code, 'curl_error' => $curl_err ?: null,
    ]);
    return ok([
        'url'        => $url,
        'http_code'  => $http_code,
        'passed'     => $passed,
        'curl_error' => $curl_err ?: null,
        'total_time' => round($total_time, 3),
        'tip'        => $passed ? 'webhook 可达, 可安全注册' : 'webhook 不可达或返回非 2xx, 请检查 URL/签名/防火墙',
    ]);
}

function trigger_webhooks($action, $req, $result, $cfg) {
    $dir = rtrim($cfg['WORK_DIR'], '/\\') . '/' . $cfg['WEBHOOK_DIR'];
    if (!is_dir($dir)) return;
    $event = $action;
    // chunked_upload 作为 upload 事件触发，更符合前端直觉
    if ($event === 'chunked_upload') $event = 'upload';
    $hook_files = glob($dir . '*.json');
    foreach ($hook_files as $f) {
        $rec = json_decode(file_get_contents($f), true);
        if (!$rec) continue;
        if (!empty($rec['paused'])) continue;
        if (!in_array($event, (array)($rec['events'] ?? ['upload','delete']))) continue;
        if (!empty($rec['filter']['path_prefix'])) {
            $path = $req['path'] ?? '';
            if (strpos($path, $rec['filter']['path_prefix']) !== 0) continue;
        }
        if (!empty($rec['fail_count']) && $rec['fail_count'] >= 10) {
            $rec['paused'] = true;
            @file_put_contents($f, json_encode($rec, JSON_UNESCAPED_UNICODE), LOCK_EX);
            continue;
        }
        $payload = [
            'event'      => $event,
            'ts'         => time(),
            'path'       => $req['path'] ?? ($req['path'] ?? ''),
            'ok'         => !empty($result['ok']),
            'code'       => $result['code'] ?? 0,
            'webhook_id' => $rec['id'],
        ];
        if (!empty($rec['secret'])) {
            $payload['signature'] = 'sha256=' . hash_hmac('sha256', json_encode($payload), $rec['secret']);
        }
        $body = json_encode($payload, JSON_UNESCAPED_UNICODE);
        $success = false;
        $last_http_code = 0;
        $last_error = '';
        $backs = [1, 5, 15];
        for ($i = 0; $i < 3; $i++) {
            $ch = curl_init($rec['url']);
            curl_setopt_array($ch, [
                CURLOPT_POST           => true,
                CURLOPT_POSTFIELDS     => $body,
                CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => 5,
                CURLOPT_CONNECTTIMEOUT => 3,
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_SSL_VERIFYHOST => 2,
                CURLOPT_FOLLOWLOCATION => false,
                CURLOPT_PROTOCOLS      => defined('CURLPROTO_HTTP') ? (CURLPROTO_HTTP | CURLPROTO_HTTPS) : 3,
            ]);
            // 安全: DNS rebinding 防护 — curl 前重新解析 host, 用 CURLOPT_RESOLVE 锁定已校验的公网 IP,
            // 防止 set 时校验通过、触发时 DNS 切到内网 (TOCTOU).
            // 修复 P1-1: 用 _webhook_resolve_public_ips 同时解析 IPv4+IPv6 (原版 gethostbynamel 漏 IPv6).
            //   若解析不到任何公网 IP (可能已 rebind 到内网), 跳过本次投递并审计, 不让 curl 自由解析.
            $_hk = parse_url($rec['url']);
            $_resolved_ok = false;
            if (!empty($_hk['host'])) {
                $_hname = $_hk['host'];
                $_hport = $_hk['port'] ?? (($_hk['scheme'] ?? '') === 'https' ? 443 : 80);
                foreach (_webhook_resolve_public_ips($_hname) as $_hip) {
                    curl_setopt($ch, CURLOPT_RESOLVE, ["$_hname:$_hport:$_hip"]);
                    $_resolved_ok = true;
                    break;
                }
            }
            if (!$_resolved_ok && empty($cfg['WEBHOOK_ALLOW_INTERNAL'])) {
                curl_close($ch);
                $last_error = 'dns_rebinding_blocked (no public IP resolved)';
                $last_http_code = 0;
                if ($i < 2) sleep($backs[$i]);
                continue;
            }
            $resp = curl_exec($ch);
            $code = intval(curl_getinfo($ch, CURLINFO_HTTP_CODE));
            if ($resp === false) $last_error = curl_error($ch);
            curl_close($ch);
            $last_http_code = $code;
            if ($code >= 200 && $code < 300) { $success = true; break; }
            if ($i < 2) sleep($backs[$i]);
        }
        $rec['last_fired_at'] = time();
        $rec['last_http_code'] = $last_http_code;
        if (!$success) {
            $rec['fail_count'] = intval($rec['fail_count'] ?? 0) + 1;
            $rec['last_error'] = $last_error;
            @file_put_contents($f, json_encode($rec, JSON_UNESCAPED_UNICODE), LOCK_EX);
            if (function_exists('audit_log')) {
                $E_ref = $GLOBALS['E'] ?? [];
                audit_log('webhook', $req, $E_ref['WEBHOOK_FAILED'] ?? 512, [
                    'hook_id' => $rec['id'],
                    'url' => $rec['url'],
                    'last_http_code' => $last_http_code,
                    'last_error' => $last_error,
                ]);
            }
        } else {
            $rec['fail_count'] = 0;
            unset($rec['last_error']);
            @file_put_contents($f, json_encode($rec, JSON_UNESCAPED_UNICODE), LOCK_EX);
        }
    }
}
