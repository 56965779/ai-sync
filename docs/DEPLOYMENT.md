# 部署指南

> 标注说明：✅ = 本轮实机跑通；⚠️ = 按源码给出但未在本轮实机复跑；❌ = 实测失败。
> 本轮环境：Windows 客户端（Python 3.13.14）+ Android Termux / nginx / php-fpm 服务端（HTTPS）。
> 维护机没有安装 PHP 与 nginx，所以「本地起服务」类命令无法在此复跑。

---

## 0. 部署前检查

```bash
git clone https://github.com/56965779/ai-sync.git && cd ai-sync
cp sync_config.example.json sync_config.json
cp sync.conf.example        sync.conf
python3 -c "import secrets;print(secrets.token_hex(32))"   # 生成 token
```

`.gitignore` 已忽略这两个真实配置文件。确认它们没被 `git add`：

```bash
git status --short | grep -E 'sync_config.json|sync.conf' || echo "OK: 真实配置未纳入版本控制"
```

**四个必须同目录的文件**：`sync_api.php`、`sync.py`、`tools/`、`sync_config.json`
（最后一个可用 `AI_SYNC_CONFIG_FILE` 外移）。原因见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 1. ✅ nginx + php-fpm（本轮实测形态）

### 1.1 目录

```
<WEB_ROOT>/server/
├── sync_api.php
├── sync.py            # 分发版客户端
├── sync_config.json
└── tools/*.php
```

本轮实测的 `work_dir` 是 nginx 站点根（Termux 下形如
`/data/data/com.termux/files/usr/share/nginx/html/`），`scope=works`。

### 1.2 nginx 配置要点

```nginx
server {
    listen 443 ssl;
    server_name your-server.example;
    # ssl_certificate / ssl_certificate_key ...

    root /path/to/webroot;

    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_pass 127.0.0.1:9000;        # 按实际 php-fpm 监听改
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        # 推荐：把含 token 的配置移出 Web 根
        # fastcgi_param AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json;
    }

    # 配置仍在 Web 根时必须加，否则 GET 就能拿到 admin_token
    location = /server/sync_config.json { deny all; }
}
```

`sync_api.php` 依次读 `getenv('AI_SYNC_CONFIG_FILE')` → `$_SERVER['AI_SYNC_CONFIG_FILE']`
→ `$_ENV['AI_SYNC_CONFIG_FILE']`，都没有才回退脚本同目录的 `sync_config.json`。
改 nginx 配置后需要 `nginx -t` 再 reload —— **这一步需要服务器本机权限，不能通过本工具的
`--exec` 在 Termux 上完成（实测 PATH 里没有 nginx/php-fpm 可执行文件）**。

### 1.3 验证

```bash
curl -s -X POST "https://your-server.example/server/sync_api.php" \
  -H 'Content-Type: application/json' \
  -d '{"action":"ping","token":"<CLIENT_TOKEN>","scope":"works"}' | python3 -m json.tool
```

检查三件事：

1. `ok=true`、`code=0`、`available_actions` 有 67 项。
2. `deployment_warnings` 为空。实测中一个未加 deny 的部署返回了两条 critical：
   `config_in_webroot` 与 `admin_token_exposed`。
3. 直接 `GET /server/sync_config.json` 应返回 403/404，而不是 200 + JSON。

---

## 2. ✅ 本地 PHP 内置服务器（调试用，已实机复跑）

PHP 8.1.2 上已实机跑通（2026-11-05 复跑）。以下命令可直接照抄：

```bash
cd ai-sync
php -S 127.0.0.1:8899 -t .
```

对应 `sync.conf`：

```text
API_URL    = "http://127.0.0.1:8899/sync_api.php"
API_TOKEN  = "<sync_config.json 里的普通 token>"
SCOPE      = "works"
VERIFY_SSL = false
```

注意：开发服务器会把 `sync_config.json` 静态暴露，只能绑 `127.0.0.1`，不要对外。

**实测记录（2026-11-05，PHP 8.1.2 + Python 3.8.20）**：

| 步骤 | 结果 |
|---|---|
| `POST {"action":"ping"}` | 200，返回 `version` / `actions` 全量清单 |
| 客户端 `--whoami` | `ok=true`，`scope=works`，`permissions=[read,write]` |
| 客户端 `--auto pull` | `pulled=2 / failed=0`，回读 MD5 三方一致 |

---

## 3. ✅ POSIX / ❌ Windows（Python 服务端）

```bash
python3 sync_api.py --config sync_config.json \
  --host 127.0.0.1 --port 8900 \
  --tls-cert /path/fullchain.pem --tls-key /path/privkey.key
```

参数（源码 `main()` 中定义）：`--config` / `--host`（默认 `0.0.0.0`）/
`--port`（默认 `5443`）/ `--tls-cert` / `--tls-key`。

**实测结论**：

- ❌ Windows：`python3 sync_api.py --help` 直接崩在模块级 `import fcntl as _fcntl`
  （`sync_api.py:7479`），`ModuleNotFoundError: No module named 'fcntl'`，
  连 argparse 都到不了。客户端 `sync.py` 有 `fcntl→msvcrt` 降级，服务端没有。
- ✅ POSIX：2026-11-05 实机跑通（Linux + Python 3.8.20）。`python3.8 sync_api.py --config sync_config.json --host 127.0.0.1 --port 8900` 启动日志为 `listening on http://127.0.0.1:8900 (tls=False)`，`ping` 返回 200。不传 `--tls-cert/--tls-key` 时走明文 HTTP，只适合本机调试。
- 仓库里的 Python 服务端已补 `client_download`（`_client_meta` + 流式分发），
  但**历史部署的 Python 端可能没有这个 action**，切 API_URL 过去会导致自升级失效。
  用 `{"action":"help"}` 确认 `available_actions` 里有没有 `client_download`。

Windows 上请用 PHP 服务端（WSL / 容器 / 远程 Linux 亦可）。

---

## 4. ✅ 客户端部署与自举（Windows 实测）

```bash
# 把 sync.py + sync.conf 放到长期稳定目录，例如 ~/ai-sync-client
python3 sync.py --workdir /absolute/path/to/your/workspace
```

自举会回写 `WATCH_DIR` / `LOG_FILE` 到 `sync.conf`，输出 `READY ... next="--auto pull"`。

三个不要：

1. 不要把客户端目录放 `/tmp`（自动升级会重写 `sync.py`，临时目录被清理就丢状态）。
2. 不要把 `sync.conf` 放进 `WATCH_DIR`（会把含 token 的配置同步到服务器）。
3. 不要在测试环境开着 `AUTO_UPGRADE=true` 指向生产服务端（会被静默升级）。
   测试时加 `AUTO_UPGRADE = false` 或 `--no-auto-upgrade`。

首次拉取：

```bash
python3 sync.py --auto pull
# 实测：4002 项 → downloaded 3998 文件 / 约 189 MB / failed=0
#       跳过 2 个指向 work_dir 之外的绝对软链接（目录穿越防护）
#       落盘后回读 MD5，本地 / 远端 / 快照三方一致
python3 sync.py --auto status
```

---

## 5. 配置外移（强烈建议）

```bash
# 服务器本机
mkdir -p /etc/ai-sync
mv /path/to/webroot/server/sync_config.json /etc/ai-sync/sync_config.json
chmod 600 /etc/ai-sync/sync_config.json
```

nginx：

```nginx
fastcgi_param AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json;
```

Apache：`SetEnv AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json`。

改完 reload，然后轮换全部 token（旧 token 可能已经泄露）。用
`{"action":"config_reload"}` 可让服务端重读配置而无需重启。

---

## 6. 排错表（均来自实测）

| 现象 | 原因 | 处理 |
|---|---|---|
| `GET /server/sync_config.json` 返回 200 + token | 配置在 Web 根且没加 deny | 按第 5 节外移或加 deny，然后轮换 token |
| `--health` 报 `/ 100%`、`load=""`、`processes="0"` | 旧版硬编码 `df -h /`，Android/容器里 `/` 是只读系统分区 | 用仓库当前版客户端；新版按 `work_dir` 挂载点取数 |
| `--doctor --brief` 的 `errors` 每次都涨 | 旧版无条件探测不存在的 `nginx_err`，`log_not_found 1005` 被写进审计又被计成 error | 用仓库当前版客户端；看 `missing_sources` / `known_noise` |
| `--check-update` 说「版本不一致」但 `--selfcheck` 说一致 | 旧版硬编码 `/mnt/data/wwwroot/default/sync/server/` | 用仓库当前版客户端；取不到 md5 时现在返回 `status=unavailable` |
| `error_log_list` 只有 `audit` / `php_error` | nginx 错误日志不在候选路径或未生成 | 正常；`missing_sources` 会列出缺失源，不是故障 |
| Termux 上 `--exec "nginx -t"` / `php -v` 失败 | exec 的 PATH 不含 `$PREFIX/bin`，且服务进程权限受限 | 用 `pwd` / `ls` / `df` 等基础命令；nginx 操作走服务器本机 |
| `--doctor` 在 Python 3.13 严格警告模式下丢掉整个日志源 | 旧版 `strptime("%b %d %H:%M:%S")` 无年份触发 `DeprecationWarning` | 用仓库当前版客户端（先补年份再解析） |
| 拉取后 `status` 有 `DIRTY` | 本地改动或部署后快照未更新 | 核对 MD5；必要时重新 `--auto pull` |
| `client_md5` 与本地 `sync.py` MD5 永远对不上 | 文件行尾 CRLF/LF 不一致 | 仓库已加 `.gitattributes` 统一 LF；必要时 `git add --renormalize .` |

---

## 7. 部署后验收清单

- [ ] `{"action":"ping"}` → `ok=true`，`deployment_warnings` 为空
- [ ] `GET /server/sync_config.json` → 403/404
- [ ] `{"action":"help"}` → 67 个 action，含 `client_download`
- [ ] 客户端 `--whoami` 权限符合预期（客户端不应有 `admin`）
- [ ] `--auto pull` → `failed=0`，`--auto status` → `dirty=0 missing=0`
- [ ] `--health` → `unavailable=[]`、`health=ok`
- [ ] `--doctor --brief` 连跑 3 次，`errors` 稳定不增长
- [ ] `--check-update` → `sync_py_consistent=true`
- [ ] 备份文件与回滚路径可用（`--backup-list`）

更多见 [README 第二章](../README.md) 与 [SECURITY.md](../SECURITY.md)。
