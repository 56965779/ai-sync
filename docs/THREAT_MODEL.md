# 威胁模型

> 本文结合源码（`sync_api.php` / `sync_api.py` / `SECURITY.md`）与线上实例审计编写。
> 线上实例曾暴露的高危项已用 ✅/🔴 标注实测状态。

---

## 1. 信任边界

```
Internet ──▶ [sync_api 入口] ──▶ scope.work_dir (受控文件根)
                         │
                         └─▶ exec (受 exec_enabled + IP 白名单约束)
                         └─▶ admin_token (可改配置 / 管 token)
```

- **不可信**：互联网请求（任何 token 都可能泄露）。
- **强边界**：所有文件操作锁定在 `work_dir` 内，越界返回 `1004`；软链接不跟随出界。
- **弱边界（默认）**：exec 与 admin 仅由 token 与 IP 白名单保护。

## 2. 威胁清单

### T1 🔴 配置与 token 公网可读（实测高危）
`sync_config.json` 与服务端同目录、位于 Web 根时，可直接 `GET` 下载：

```
GET https://host/sync_config.json → HTTP 200
→ 明文 admin_token + allow_ips=[] + rate_limit.enabled=false + exec_enabled=true
```

后果：拿到 `admin_token` 即完全控制（改配置、管 token、开 exec）。
缓解（服务端）：
- 把 `sync_config.json` 移出 Web 根，用 `AI_SYNC_CONFIG_FILE` 环境变量 / fastcgi_param 指向它；
- 或 nginx `location ~ sync_config\.json { deny all; }` 后 `reload`；
- 轮换全部 token（`token_add` 新 + `token_delete` 旧）。

> 服务端 `{"action":"ping"}` 自带 `deployment_warnings`，会结构化报出
> `config_in_webroot` 与 `admin_token_exposed`，可机器化检测。

### T2 ⚠️ 安装包含明文 token
`client_package` 生成的 zip 内含预置 `sync.conf`（明文 token）。生成目录若在 Web 根，
服务端自检报 `packages_in_webroot`；下载后应及时删除服务器副本。

### T3 ⚠️ exec 滥用
`exec_enabled=true` 且无 IP 白名单时，持有可执行 token 的人可在服务器跑任意 shell。
默认（模板）`exec_enabled=false`；线上实例为了调试开了 true，**不应出现在生产**。
缓解：exec token 单独授予 + `exec_allow_ips` 限定来源 IP。

### T4 ⚠️ 限流缺失
模板 `rate_limit.enabled=true`；线上实例关了限流（`1003` 不会触发）。
缓解：保持限流开启，调大 `per_ip_per_second` 上限而非关闭。

### T5 ⚠️ 路径穿越
所有 `path` 必须在 `work_dir` 内。`../`、绝对路径、出界软链接均被拒（`1004`）。
**不要把 work_dir 设为 `/` 或用户家目录**，否则边界形同虚设。

### T6 ℹ️ 审计与操作日志
每个请求至少一条 `audit_log`；配置变更、token 操作都会留痕。配对 `oplog_*` 可追溯客户端行为。
建议定期 `audit_log` 巡检异常 `code`（尤其是 `1005` / `1002` 突增）。

### T7 ℹ️ 传输安全
全程 HTTPS（服务端走 TLS 反向代理）。客户端 `VERIFY_SSL` 默认 `auto`，自举后建议显式
`true`。自签名证书时再考虑关闭验证，但**不要**长期关闭。

## 3. 最小安全部署清单（生产）

1. `sync_config.json` 移出 Web 根（或 nginx `deny`），设 `AI_SYNC_CONFIG_FILE`。
2. 轮换 `admin_token` 与所有 scope token。
3. `exec_enabled=false`（除非确需），确需时 `exec_allow_ips` 限定。
4. `rate_limit.enabled=true`。
5. `work_dir` 设为专用目录，不为 `/` 或家目录。
6. 全链路 HTTPS，`VERIFY_SSL=true`。
7. 部署后 `{"action":"ping"}` 确认 `deployment_warnings=[]`，`--selfcheck` 无告警。

## 4. 漏洞披露

见仓库 [SECURITY.md](../SECURITY.md)。请勿在公开 issue 贴 token、URL 或复现细节。
