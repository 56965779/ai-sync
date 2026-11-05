# 安全策略

## 支持范围

| 版本 | 状态 |
|---|---|
| 当前 `main` / v4.39.x | 支持 |
| v4.38 及更早 | 不单独维护，请升级 |

本项目以单文件分发、靠 `whoami.client_md5` 自动升级，没有多分支维护线。
`APP_VERSION` 字符串可能滞后于实际构建，判断版本请用
`python3 sync.py --check-update` 或 `--selfcheck`，不要只看 `--version`。

## 报告漏洞

请不要在公开 issue 里贴可直接利用的细节（尤其是真实 token、生产 URL、请求样例）。
通过 GitHub 私有漏洞报告（仓库 Security → Report a vulnerability）或私信维护者。

报告请附：影响版本、复现步骤、影响范围、是否已公开。确认后会在修复版本里致谢。

---

## 威胁模型一句话

**拿到 token ≈ 拿到服务器。**

本项目有意提供 `exec`、`shell_*` 远程命令执行，以及 `spider` / `webhook` 外发能力。
这不是漏洞，是功能，但它把 token 的有效权限抬到了「整个 work_dir + 命令执行」。
因此所有安全设计都围绕「token 不能泄露、泄露后权限要可收窄」。

详细分析见 [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)。

---

## 服务端自带部署自检（请务必用）

服务端在 `ping` 与 `health` 两个 action 里返回结构化告警，这是判断部署是否安全的第一手依据：

```bash
# 用客户端
python3 sync.py --conf sync.conf --selfcheck
python3 sync.py --conf sync.conf --health

# 或直接 POST（把 URL / token 换成你自己的）
curl -s -X POST "https://your-server.example/path/sync_api.php" \
  -H 'Content-Type: application/json' \
  -d '{"action":"ping","token":"<YOUR_TOKEN>","scope":"works"}'
```

关注 `deployment_warnings` 字段。当前实现会报：

| code | severity | 含义 |
|---|---|---|
| `config_in_webroot` | critical | `sync_config.json` 在 Web 根内，可被静态下载（含全部 token） |
| `admin_token_exposed` | critical | 管理员凭据与可下载配置同处 Web 根 |
| `packages_in_webroot` | high | 客户端安装包（内含明文 token）在 Web 根内 |
| `exec_as_root` | high | `exec_enabled=true` 且服务进程以 root 运行 |

**实测提示**：一个已上线的部署曾同时命中前两条 critical —— 配置文件可被匿名
`GET` 到、返回 200、内容含明文 `admin_token`。请上线前跑一次自检，别靠肉眼。

---

## 上线前必做

1. **换掉全部 token**：`sync_config.example.json` 里的 `CHANGE_ME_*` 只是占位符，
   用 32 字节以上随机值。客户端应使用普通 scope token，**不要把 `admin_token` 下发给客户端**。
2. **配置移出 Web 根**：设置 `AI_SYNC_CONFIG_FILE`（nginx + php-fpm 用
   `fastcgi_param AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json;`，Apache 用 `SetEnv`），
   或至少加 `location = /path/sync_config.json { deny all; }`。
   `sync_api.php` 会依次读 `getenv` / `$_SERVER` / `$_ENV`。
3. **加 IP 白名单 + 限流**：`allow_ips` 不要留空；`rate_limit.enabled` 保持 `true`。
   模板默认 300 次/分钟、20 次/秒，按实际调整。
4. **绑内网或走反代**：不要把服务端直接暴露公网；启用 HTTPS。
5. **按需关 exec**：`exec_enabled=false`、`scopes.<name>.exec_enabled=false`，
   只为确实需要的 token 授予 `exec` 权限。
6. **不要用 root 运行** php-fpm / Python 服务端。
7. 重新加载配置：`{"action":"config_reload"}` 或重启服务。

`.gitignore` 已忽略 `sync_config.json`、`sync.conf`、`*.key`、`*.pem` 和客户端安装包，
防止把这些文件误提交到公开仓库。

---

## 已实现的防护

- 路径由 `safe_path` 限制在 scope 的 `work_dir` 内；越界软链接在拉取时被跳过。
- 按 token 分 `read / write / delete / exec / lint / backup / admin` 权限，按 scope 隔离目录。
- 写入前 PHP 语法检查、原子覆盖、自动备份、上传后 MD5 复验、一键回滚。
- 危险命令（覆盖裸磁盘、fork 炸弹、关机等）硬拦截；破坏性但可能是真意的命令只告警。
- 外发请求默认拦截内网/本机地址（黑名单式 SSRF 防护）。
- 审计日志：每个请求至少一条，含 action / code / token_hash / ip / rid，不记明文 token。

---

## 已知残余风险（不打算悄悄掩盖）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **自升级无发布方签名** | `client_download` 只做 MD5 校验 + `py_compile` 预检。如果攻击者能替换服务端同目录的 `sync.py`，所有客户端下次运行会静默执行它。 | 只在自有可信服务器启用；客户端可设 `AUTO_UPGRADE=false` 并人工比对 MD5。长期方案是加签名验签。 |
| 配置文件易被静态下载 | 默认与 `sync_api.php` 同目录，nginx 会直接返回 JSON。 | 用 `AI_SYNC_CONFIG_FILE` 外移或 nginx deny；用 `deployment_warnings` 自检。 |
| SSRF 是黑名单式 | 拦内网/本机地址，但 DNS rebinding、非常规进制 IP 等绕过需要额外防护。 | 不需要外发时关闭 `webhook_*` / `spider`；在出口防火墙层再做白名单。 |
| exec 权限粒度粗 | 拿到带 `exec` 的 token 即可在 work_dir 执行命令。 | 关 exec、配 `exec_allow_ips`、非 root 运行。 |
| 日志时间格式/时区 | 客户端 24h 窗口按字符串格式解析，部分 PHP 日志格式不匹配会退化；本地时间与服务端 UTC 可能有偏移。 | 以 `--doctor` 的 `known_noise` / `top_errors` 为准，别只看 `errors` 计数。 |

---

## 披露时间线

发现者报告 → 维护者确认（目标 3 个工作日内）→ 修复 + 发版 → 公开致谢。
涉及 token 泄露类问题建议同步轮换凭据，不要只依赖代码修复。
