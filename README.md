# ai-sync

> **给 AI 用的服务器遥控器。**
> 一个零依赖的 Python 文件，把「在服务器上读写文件、改代码、跑命令」变成 **67 个带 JSON 契约的 API**。
> Agent 不用 SSH、不用猜命令输出、不用怕改错 —— 每一步都能预览、校验、复验、一键回滚。

**AI 专用 · 跨设备 · 跨平台 · 零依赖**：在服务器上装好服务端，任何能跑 Python 的设备都能远程管理文件、编写并发布代码、运维服务器。

`MIT` · `Python 3.8+` · `67 个 action` · `PHP / Python 双服务端` · `全部输出机器可读`

> 本项目的文档只写**实测跑通过的路径**。没跑过的部署形态会明确标注，不写成承诺。

---

## 为什么不用 SSH

| | SSH / SFTP / 自建脚本 | ai-sync |
|---|---|---|
| 输出 | 人类文本，靠正则猜 | 恒定 JSON 信封 `{ok, code, stdout, stderr, command}` |
| 能力发现 | 靠人写文档 | 服务端自描述 `{"action":"help"}` / 客户端 `--api-help` |
| 出错 | 一条命令改坏就得手工救 | `--dry-run` → 语法检查 → 原子覆盖 + 自动备份 → MD5 复验 → `--rollback` |
| 权限 | 一个账号全开 | 按 token 分 `read/write/delete/exec/lint/backup/admin`，按 scope 隔离目录 |
| 可追溯 | 靠 `history` | 审计日志 / 操作日志 / 最近变更 / 错误日志 |
| 连续操作 | 每条一次 RTT | `pipeline` 一次往返跑多步；`--batch` 单进程连续只读探查 |
| 长任务 | 断了就丢 | 持久 shell 会话（`shell_open` / `shell_run`）+ 后台任务轮询（`shell_job`） |

---

## 实测覆盖范围（请先读这一节）

这份文档里的命令、输出和数值来自 2026-11-04 的一次完整接入与回归：

| 项目 | 状态 | 说明 |
|---|---|---|
| 客户端 OS | ✅ 实测 | Windows / Python 3.13.14 |
| 服务端形态 | ✅ 实测 | Android Termux + nginx + php-fpm，HTTPS 公网可达 |
| 首次拉取 | ✅ 实测 | 远端 4002 项 → 下载 3998 文件约 189 MB，`failed=0`；跳过 2 个越界软链接；落盘 MD5 三方一致 |
| 日常命令 | ✅ 实测 | `pull` / `save` / `fix` / `status` / `whoami` / `health` / `doctor` / `check-update` / `batch` |
| 部署与回滚 | ✅ 实测 | 原子部署 `verify_ok=1`；原版 ↔ 补丁版往返部署可逆 |
| 全网自动升级 | ✅ 实测 | 服务端换分发版后，客户端下次运行静默升级并留备份 |
| 边界测试 | ✅ 实测 | 16 组：日志源缺失、采集失败、老服务端降级、Python 3.13 严格警告、进度折叠等 |
| 本地 `php -S` 试跑 | ⚠️ 未实测 | 维护环境未装 PHP/nginx；命令按源码给出，见 [部署文档](docs/DEPLOYMENT.md) |
| Python 服务端 | ⚠️ 部分 | Windows 上**启动即失败**（`import fcntl` 无降级）；POSIX 未实机跑通 |
| macOS / 管理面板浏览器流程 | ⚠️ 未实测 | 客户端有代码级降级保护，但未实机验证 |

数据细节见 [docs/BENCHMARKS.md](docs/BENCHMARKS.md)。

---

## 仓库文件结构（以及为什么是平铺的）

```
ai-sync/
├── sync.py                    # 客户端（单文件、零依赖）+ 服务端分发用的参考客户端
├── sync_api.php               # PHP 服务端入口（67 action 路由 + 鉴权 + 审计）
├── sync_api.py                # Python 服务端（协议对齐实现）
├── sync_config.example.json   # 服务端配置模板（全部 token 为 CHANGE_ME_* 占位符）
├── sync.conf.example          # 客户端配置模板
├── admin.html                 # 静态管理面板（目录授权 / token / 备份 / 审计）
├── tools/*.php                # PHP 服务端按需加载的 12 个工具模块
├── docs/                      # 部署 / API / 架构 / 基准 / 威胁模型
├── LICENSE  README.md
├── CHANGELOG.md  CONTRIBUTING.md  SECURITY.md
└── .gitignore  .gitattributes
```

**这些文件必须在同一目录，不是没整理：**

- `sync_api.php:4215` 用 `__DIR__ . '/sync.py'` 算 MD5 放进 `whoami.client_md5`，
  `action_client_download()` 再 `readfile(__DIR__ . '/sync.py')` 把客户端分发给所有设备。
  把 `sync.py` 挪进 `client/` 会直接断掉自动升级通道。
- `ensure_tool_loaded()` 只找 `__DIR__ . '/tools/<name>.php'` 或 `<WORK_DIR>/tools/`。
- `sync_api.py` 的 `_client_sync_path()` 同样按脚本同目录找 `sync.py`。
- `sync_config.json` 默认也在脚本同目录（可用 `AI_SYNC_CONFIG_FILE` 外移，见安全章节）。

所以本仓库**不提供** `scripts/quickstart.py`，也没有 `client/` / `server/` / `config/` 目录；
与其给一个跑不起来的「一键脚本」，不如把手工步骤写清楚。

---

# 一、下载

```bash
git clone https://github.com/56965779/ai-sync.git
cd ai-sync
```

环境要求：

- 客户端：Python 3.8+（纯标准库，实测 3.13.14）；Windows / Linux / macOS / Termux / 容器
- PHP 服务端：PHP 8.0+，nginx + php-fpm 或 Apache（实测形态）
- Python 服务端：Python 3.8+，**当前仅 POSIX**（Windows 有 `fcntl` 依赖，见已知边界）

下载完先做两件事，避免把凭据提交进仓库：

```bash
cp sync_config.example.json sync_config.json     # 服务端配置，已被 .gitignore 忽略
cp sync.conf.example        sync.conf           # 客户端配置，已被 .gitignore 忽略
```

---

# 二、部署

## 2.1 配置服务端

编辑 `sync_config.json`。模板默认是**收紧**的，建议保留这些值：

```json
{
  "version": "4.39.0",
  "global": {
    "allow_ips": [],
    "rate_limit": { "enabled": true, "per_token_per_minute": 300,
                    "per_ip_per_second": 20, "upload_per_minute": 60 },
    "exec_enabled": false
  },
  "tokens": {
    "CHANGE_ME_client_token": {
      "name": "default", "scopes": ["works"],
      "permissions": ["read", "write", "delete"]
    }
  },
  "admin_token": "CHANGE_ME_admin_token",
  "scopes": {
    "works": {
      "work_dir": "/absolute/path/to/your/workdir",
      "exec_enabled": false
    }
  }
}
```

必改三处：

1. `admin_token` 与 `tokens` 的键 → 全部换成 **32 字节以上随机值**
   （`python3 -c "import secrets;print(secrets.token_hex(32))"`）。
2. `scopes.works.work_dir` → 绝对路径，服务进程有读写权限。
3. `allow_ips` → 填你的出口 IP；不要把空数组带到公网。

> ⚠️ `sync_config.json` 与 `sync_api.php` 同目录时，nginx 会把它当静态文件直接返回。
> **上线前必须**用 `AI_SYNC_CONFIG_FILE` 把配置移出 Web 根，或加
> `location = /path/sync_config.json { deny all; }`。详见 [SECURITY.md](SECURITY.md)。

## 2.2 部署文件到 Web 目录（实测形态）

把以下四项放到**同一个** Web 可访问目录：

```
sync_api.php
sync.py            ← 分发版客户端，必须同目录
tools/             ← 整个目录
sync_config.json   ← 或按上一步用 AI_SYNC_CONFIG_FILE 外移
```

nginx + php-fpm 最小要点：

```nginx
location ~ \.php$ {
    include fastcgi_params;
    fastcgi_pass 127.0.0.1:9000;          # 按你的 php-fpm 实际监听改
    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    # 推荐：把配置移出 Web 根
    # fastcgi_param AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json;
}
# 若配置仍在 Web 根，这条必须有
location = /path/sync_config.json { deny all; }
```

Python 服务端（POSIX，原生 TLS）：

```bash
python3 sync_api.py --config sync_config.json \
  --host 127.0.0.1 --port 8900 \
  --tls-cert /path/fullchain.pem --tls-key /path/privkey.key
```

> 本地 `php -S 127.0.0.1:8899 -t .` 也能起一个调试服务端，但**本次维护环境未装 PHP，
> 这条没实机跑通**。完整形态与排错见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 2.3 验证服务端

```bash
curl -s -X POST "https://your-server.example/path/sync_api.php" \
  -H 'Content-Type: application/json' \
  -d '{"action":"ping","token":"<YOUR_CLIENT_TOKEN>","scope":"works"}'
```

正常返回 `{"ok":true,"code":0,...}`。重点看 `deployment_warnings`：

- 若出现 `config_in_webroot` / `admin_token_exposed`（`severity: critical`），**先别往下走**，
  按 [SECURITY.md](SECURITY.md) 修好再继续。这是实测中真实踩到的坑。

## 2.4 部署客户端（自举）

把 `sync.py` + `sync.conf` 放到一个**长期稳定**的目录（例如 `~/ai-sync-client`），
不要放 `/tmp`；也不要把 `sync.conf` 放进后面的工作目录（否则含 token 的配置会被同步到服务器）。

编辑 `sync.conf`，填 `API_URL` / `API_TOKEN` / `SCOPE`，然后自举：

```bash
python3 sync.py --workdir /absolute/path/to/your/workspace
```

客户端会把 `WATCH_DIR` / `LOG_FILE` 回写进 `sync.conf`，并输出：

```
READY scope=works work_dir=/absolute/path/to/your/workspace remote_base=. next="--auto pull"
```

验证连接与权限边界：

```bash
python3 sync.py --whoami
python3 sync.py --selfcheck
```

---

# 三、使用

## 3.1 三条日常命令

```bash
python3 sync.py --auto pull                       # 增量拉取远端代码到本地工作区
python3 sync.py --auto save path/to/file --yes   # 改完发布：语法检查 + 原子覆盖 + MD5 复验
python3 sync.py --auto fix                       # 一键排查（doctor）
python3 sync.py --auto status                    # 查看同步状态
```

首次 `--auto pull` 会全量下载。实测结果：

```json
{"command":"pull","pulled":3998,"skipped":0,"failed":0,"result":"ok"}
```

拉取完成后 `status` 实测：

```json
{"command":"status","ok":true,"monitored":4010,"snapshot_entries":4010,
 "counts":{"ok":4010,"dirty":0,"missing":0}}
```

## 3.2 输出契约（AI 只需断言字段）

默认就是 JSON，无需加 `--json`：

```json
{"ok":true,"code":0,"stdout":"...","stderr":"","command":"doctor"}
```

`stdout` 若是单行 JSON，内层字段会自动合并到顶层，一次 `json.loads` 拿全：

```bash
python3 sync.py --doctor --brief
```

实测输出（节选）：

```json
{"ok":true,"code":0,"command":"doctor","brief":true,
 "health":"degraded","errors":5,"warnings":2,"known_noise":0,
 "missing_sources":["nginx_err"],"available_sources":["audit","php_error"],
 "top_errors":[{"source":"PHP错误","text":"[04-Nov-2026 ...] PHP Fatal error: ..."}]}
```

语义要点：

- `ok=true` 只表示**工具执行成功**；环境结论看 `health` / `errors` / `missing_sources`。
- 日志源不存在是**事实**不是故障，放在 `missing_sources`，不会把诊断次数算成错误。
- `top_errors` 直接给可行动证据，不靠关键词正则猜。

退出码：`0` 成功 · `1` 不一致/环境问题 · `2` 参数/文件错误 · `3` 远程命令失败 ·
`4` 部署/回滚失败 · `99` 策略阻断或锁占用。

人类可读模式加 `--plain`；批量逐文件明细加 `-v/--verbose`（默认折叠进度，省上下文）。

## 3.3 健康检查与版本

```bash
python3 sync.py --health
python3 sync.py --check-update
```

`--health` 实测（按 `work_dir` 所在挂载点取数，不会再误读 `/`）：

```json
{"command":"health","disk_percent":81,"health":"ok","unavailable":[],
 "memory":"Mem: ...","load":"... up 11 days ...","processes":"32","result":"ok"}
```

`--check-update` 用 MD5 判定一致性。**不要只看 `--version`**：`APP_VERSION` 字符串可能滞后于
实际构建（见 [CHANGELOG.md](CHANGELOG.md)）。

## 3.4 客户端自升级

服务端同目录的 `sync.py` 就是唯一分发源。替换它 = 发布新版客户端：

```
whoami.client_md5 变化 → 客户端下次运行下载 → MD5 校验 → py_compile 预检
→ 备份 sync.py.bak.<旧md5> → 原子替换
```

```bash
python3 sync.py --check-update                 # 只查询
python3 sync.py --self-update                  # 立即升级（真实参数名，不是 --self-upgrade）
python3 sync.py --whoami --no-auto-upgrade    # 临时关闭自动升级
```

> **已知残余风险**：自升级只做 MD5 校验，没有发布方签名。只在自有可信服务器启用，
> 或设 `AUTO_UPGRADE=false` 人工比对。见 [SECURITY.md](SECURITY.md)。

---

# 四、方法：AI 改码闭环

典型一次「找 → 改 → 验 → 兜底」，全部结构化输出，可被 Agent 直接断言：

```bash
# 0. 不读文档，让服务端自己说明能做什么
python3 sync.py --api-help

# 1. 找与看（大文件按行切片，不撑爆上下文）
python3 sync.py --search "function save_user"
python3 sync.py --cat app/user.php --brief

# 2. 改之前先看差异，先试跑
python3 sync.py --diff app/user.php
python3 sync.py --auto save app/user.php --dry-run

# 3. 发布：语法检查 → 原子覆盖 + 自动备份 → MD5 复验
python3 sync.py --auto save app/user.php --yes
# 实测返回示例：
# {"command":"deploy","result":"ok","files":1,"smart_verify":"md5","verify_ok":1,"verify_failed":[]}

# 4. 端到端断言
python3 sync.py --http-check "https://your-site.example/health" --expect "OK"

# 5. 改错了，回到改动前
python3 sync.py --backup-list app/user.php
python3 sync.py --rollback app/user.php --backup <备份名> --yes
```

其它常用方法：

| 场景 | 命令 |
|---|---|
| 连续只读探查（省进程启动） | `--batch`，stdin 传 JSON 数组 |
| 一次往返跑多步 | `pipeline`（`{"action":"pipeline","steps":[...]}`） |
| 排障 | `--auto fix` → `--doctor --brief` → `--error-log <源> --error-lines 100` |
| 远程命令 / 持久会话 | `--exec "cmd"` / `shell_open` + `shell_run` |
| 批量回滚 | `batch_rollback`（按时间点） |
| 权限边界 | `--whoami` |

服务端 action 的完整参数、返回字段与错误码由运行时自描述给出，**不要背文档**：

```bash
python3 sync.py --api-help
python3 sync.py --help-ai              # 场景速查
python3 sync.py --help-ai deploy      # 单命令参数
curl -s -X POST ... -d '{"action":"help","name":"upload",...}'
```

更多见 [docs/API.md](docs/API.md)。

---

# 五、安全边界（上线前必读）

本项目**包含远程命令执行**（`exec` / `shell_*`）与外发能力（`spider` / `webhook`）。
这是有意设计，不是漏洞，但意味着：**拿到 token ≈ 拿到服务器**。

- 绝不使用默认 token、绝不裸奔公网、绝不以 root 运行
- 上线前：换全部 token → 配 IP 白名单 → 绑内网/走反代 → 关掉不需要的 exec → 启用 HTTPS
- 用 `{"action":"ping"}` 的 `deployment_warnings` 做部署自检
- 客户端用普通 scope token，**不要下发 `admin_token`**

完整清单、已知残余风险与披露方式见 [SECURITY.md](SECURITY.md) 与
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)。

---

# 六、已知边界（诚实清单）

| 边界 | 说明 |
|---|---|
| Python 服务端 Windows 不可用 | `sync_api.py` 模块级 `import fcntl` 无降级，Windows 启动即 `ModuleNotFoundError`；客户端本身有 `fcntl→msvcrt` 降级，Windows 实测可用。 |
| 双端协议等价有缺口 | 仓库里的 Python 服务端已补 `client_download`；但历史部署的 Python 端可能没有该 action，切过去会让自升级失效。用 `{"action":"help"}` 确认。 |
| 自升级无签名 | 只做 MD5 + `py_compile`，见上。 |
| 日志时间窗口 | 部分 PHP 日志格式（`[04-Nov-2026 05:54:08 UTC]`）不匹配 24h 窗口解析，且本地时间与服务端 UTC 可能有偏移；以 `top_errors` / `known_noise` 为准。 |
| 版本号 | `APP_VERSION` 仍为 `v4.39.0`，但仓库已含后续加固；用 MD5 判版本。 |
| 无一键脚本 | 本仓库不提供 `scripts/quickstart.py`，请按上面的手工步骤。 |
| 管理面板 | `admin.html` 是静态页面，浏览器全链路未在本轮实测覆盖，见 [docs/ADMIN.md](docs/ADMIN.md)。 |

---

# 七、文档

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 各形态部署与排错（含实测/未实测标注）
- [docs/API.md](docs/API.md) — 67 个 action 的分组、契约、错误码与自描述用法
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 双实现契约、鉴权、目录模型、自动升级链路
- [docs/ADMIN.md](docs/ADMIN.md) — 静态管理面板使用
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — 本轮实测数据与复跑方法
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) / [SECURITY.md](SECURITY.md) — 威胁模型与安全披露
- [CHANGELOG.md](CHANGELOG.md) / [CONTRIBUTING.md](CONTRIBUTING.md) — 版本与贡献

---

# 八、许可证

[MIT](LICENSE) · `SPDX-License-Identifier: MIT` · Copyright (c) 2026 金鑫

根目录 `LICENSE` 是唯一授权文本；源码文件头部带 SPDX 标识以便机器识别。
