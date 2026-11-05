# ai-sync

> **把你的服务器交给 AI 干活：打包一条链接，发给 AI，收工。**

在自己的服务器上装好一个单文件后端，打开管理面板：**加目录、加 Token、一键打包客户端**——你会得到一条链接，比如：

```
https://你的域名/sync/server/sync_api.php?pkg_key=xxxxx
```

把这条链接丢给任何 AI（Claude / 豆包 / Cursor / Copilot…），它会自己下载客户端、读代码、改代码、部署、回滚。**你不需要懂 SSH，不需要教 AI 命令格式，也不怕它改错**——每个动作都有预览、语法检查、MD5 复验和一键回滚。

这不是又一个文件同步工具，是给 AI 用的**远程工作台**：71 个带 JSON 契约的远程能力，输出恒为 `{"ok":true,...}`，AI 不用猜输出、不用正则、不用登 SSH。

```bash
# AI 拿到链接之后，自己会跑完这一整套：
python3 sync.py --workdir ~/my-server   # 自举，配置回写，末行 READY
python3 sync.py --whoami                # 权限边界 + 服务端能力清单
python3 sync.py --auto pull             # 拉代码，落盘 MD5 三方一致
python3 sync.py --deploy 文件 --smart-verify   # 改完部署，MD5 复验
```

`MIT` · `Python 3.8+` · `PHP 8.0+` · `71 个 action` · `PHP / Python 双服务端` · `零依赖客户端`

[![CI](https://github.com/56965779/ai-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/56965779/ai-sync/actions/workflows/ci.yml)

> 本文档只写**实测跑通过的路径**，没跑过的部署形态会明确标注，不写成承诺。

---

## 30 秒看懂你能干什么

| 你在面板做的事 | AI 自动完成的事 |
|---|---|
| 授权一个目录（比如 `~/网站`） | 浏览、搜索、读取该目录全部文件 |
| 发一个 Token（勾选读写/执行权限） | 改代码、跑命令、部署上线 |
| 一键打包客户端，复制链接 | 下载链接、自举配置、干活、回报结果 |

**典型场景**：你对 AI 说"把服务器上那个博客的主题色改成深色，然后部署"——AI 自己 pull 代码、本地分析、修改、语法检查、原子部署、MD5 校验，出问题一键 rollback。你只说了一句话。

---

## 为什么不用 SSH

| | SSH / SFTP / 自建脚本 | ai-sync |
|---|---|---|
| 输出 | 人类文本，AI 靠正则猜 | 恒定 JSON 信封 `{ok, code, stdout, stderr, command}` |
| 能力发现 | 靠人写文档 | 服务端自描述 `{"action":"help"}`，AI 零文档接入 |
| 出错 | 一条命令改坏就得手工救 | 语法检查 → 原子覆盖 + 自动备份 → MD5 复验 → `--rollback` |
| 权限 | 一个账号全开 | 按 token 分 read/write/delete/exec/admin，按 scope 隔离目录 |
| 可追溯 | 靠 history | 审计日志 / 操作日志 / 最近变更 / 错误日志 |
| 长任务 | 断了就丢 | 持久 shell 会话 + 后台任务轮询 |

---


## 实测覆盖范围（请先读这一节）

这份文档里的命令、输出和数值来自 2026-11-04 的一次完整接入与回归，以及 2026-11-05 的本地复跑：

| 项目 | 状态 | 说明 |
|---|---|---|
| 客户端 OS | ✅ 实测 | Windows / Python 3.13.14 |
| 服务端形态 | ✅ 实测 | Android Termux + nginx + php-fpm，HTTPS 公网可达 |
| 首次拉取 | ✅ 实测 | 远端 4002 项 → 下载 3998 文件约 189 MB，`failed=0`；跳过 2 个越界软链接；落盘 MD5 三方一致 |
| 日常命令 | ✅ 实测 | `pull` / `save` / `fix` / `status` / `whoami` / `health` / `doctor` / `check-update` / `batch` |
| 部署与回滚 | ✅ 实测 | 原子部署 `verify_ok=1`；原版 ↔ 补丁版往返部署可逆 |
| 全网自动升级 | ✅ 实测 | 服务端换分发版后，客户端下次运行静默升级并留备份 |
| 边界测试 | ✅ 实测 | 16 组：日志源缺失、采集失败、老服务端降级、Python 3.13 严格警告、进度折叠等 |
| CI（GitHub Actions） | ✅ 实测 | 6 个 job 全绿：Python 3.8 / 3.12、PHP 8.0 / 8.3、双端契约一致性、文档链接与占位符 |
| 本地 `php -S` 试跑 | ✅ 实测（2026-11-05 复跑） | PHP 8.1.2：`php -S 127.0.0.1:8899 -t .` 起服务，`ping` 返回 200；Python 3.8.20 客户端 `--whoami` / `--auto pull` 端到端通过（2 个文件，落盘 MD5 三方一致） |
| Python 服务端 | ✅ POSIX / ✅ Windows | POSIX 实机跑通（Python 3.8.20，`sync_api.py --port 8900` 明文 HTTP，`ping` 返回 200）；Windows 实机跑通（Python 3.13.12）：`ping` / `whoami` / `--auto pull` / `health` / `doctor` / `exec`（异步协议 + cwd + 文件写回）/ 超时 `rc=124` / `shell` 持久会话全部通过。Windows 需 Git 自带 `sh` 在 PATH（`fcntl` 已降级、`exec` 经 MSYS2 `sh`） |
| 客户端包安装包下载 | ✅ 实测（2026-11-05） | `client_package` 生成的 `pkg_key` 链接：`200` / `223674` 字节 / `application/zip`；解包 → `--workdir` 自举 → `--whoami` → `--auto pull`（`pulled=11`、`failed=0`）全链路跑通，详见 [docs/AI-USAGE.md](docs/AI-USAGE.md) |
| macOS 客户端 | ⚠️ 未实测 | 有代码级降级保护，但未实机验证 |
| 扩展缺失降级 | ✅ 实测（2026-11-05） | `AI_SYNC_SIMULATE_MISSING_EXT=zip,mbstring,curl` 强制走降级分支：67/67 action 仍全部通过，`client_package` 产出 812KB zip 且 `unzip -t` / Python `zipfile` 双通过，`webhook_test` 返回 `transport=stream` |
| 管理面板浏览器全流程 | ✅ 实测（2026-11-05） | 登录 → 加目录授权（文件数自动统计 3）→ 加 Token（六项权限）→ 一键打包（219.5KB、SHA256）→ `pkg_key` 下载 → 客户端自举 → `--auto pull`（`pulled=3`、`failed=0`），九步全通 |
| 67 个 action 全量实测 | ✅ 实测（2026-11-05） | 用 `admin_token` 逐条打真实参数：**67/67 可达并通过**；错误码 `1001/1002/1004/1005/1011/1012/1099` 逐项验证。详见 [docs/SELF-TEST.md](docs/SELF-TEST.md) |

数据细节见 [docs/BENCHMARKS.md](docs/BENCHMARKS.md)。

---

## 仓库文件结构（以及为什么是平铺的）

```
ai-sync/
├── sync.py                    # 客户端（单文件、零依赖）+ 服务端分发用的参考客户端
├── sync_api.php               # PHP 服务端入口（71 action 路由 + 鉴权 + 审计，其中 bootstrap_* 在鉴权之前）
├── sync_api.py                # Python 服务端（协议对齐实现）
├── sync_config.example.json   # 服务端配置模板（全部 token 为 CHANGE_ME_* 占位符）
├── sync.conf.example          # 客户端配置模板
├── admin.html                 # 静态管理面板（目录授权 / token / 备份 / 审计）
├── tools/*.php                # PHP 服务端按需加载的 11 个工具模块
├── docs/                      # 部署 / AI 接入 / 全功能实测 / API / 架构 / 基准 / 威胁模型
├── .github/                   # CI（语法 / 双端契约 / 文档检查） + issue / PR 模板
├── LICENSE  README.md
├── CHANGELOG.md  CONTRIBUTING.md  SECURITY.md
└── .gitignore  .gitattributes  .editorconfig
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

- 客户端：Python 3.8+（纯标准库；实测 3.8.20 / 3.12 / 3.13.14，其中 3.8 与 3.12 由 CI 每次 push 复跑）
- PHP 服务端：PHP 8.0+，nginx + php-fpm 或 Apache（实测形态）；8.0 / 8.3 由 CI 全量 `php -l` 覆盖，8.1.2 另做过 `php -S` 端到端
- Python 服务端：Python 3.8+，POSIX 与 Windows 均可用（`fcntl` 已改为 `try/except` 降级 + 线程读管道；Windows 的 `exec`/`shell` 经 Git 自带 MSYS2 `sh` 解释，需 `sh` 在 PATH）

下载完先做两件事，避免把凭据提交进仓库：

```bash
cp sync_config.example.json sync_config.json     # 服务端配置，已被 .gitignore 忽略
cp sync.conf.example        sync.conf           # 客户端配置，已被 .gitignore 忽略
```

---

# 二、部署

两种服务端**选一个**，客户端完全一样：

| 你的机器 | 走哪条 | 难在哪 |
|---|---|---|
| 有 PHP + nginx/Apache 的主机、虚拟主机、Termux | **路径 A：PHP 服务端** | 配置别留在 Web 根 |
| 只有 Python（VPS、容器、Windows、NAS） | **路径 B：Python 服务端** | 自己管进程与端口 |

---

## 2.1 配置服务端（两条路径共用同一份 `sync_config.json`）

```json
{
  "version": "4.39.4",
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
      "exec_enabled": false,
      "exec_allow_ips": []
    }
  }
}
```

**必改四处**（不改等于把服务器交出去）：

1. `tokens` 的**键** 和 `admin_token` → 全换成 32 字节以上随机值：
   `python3 -c "import secrets;print(secrets.token_hex(32))"`
2. `scopes.works.work_dir` → 绝对路径，服务进程要能读写。这是文件操作的根，客户端里的前导 `/` 不是服务器绝对路径
3. `allow_ips` → 填客户端出口 IP；**空数组别带公网**
4. `exec_enabled` → 默认 `false`，要远程执行命令再开，并配 `exec_allow_ips`

`permissions` 可选：`read` / `write` / `delete` / `lint` / `backup` / `exec` / `admin`。

> ⚠️ **配置留在 Web 根 = 明文 token 可被直接下载**。PHP 端上线前必须二选一：
> 用 `AI_SYNC_CONFIG_FILE` 把配置移出 Web 根，或加 `deny` 规则。详见 [SECURITY.md](SECURITY.md)。
> Python 端不适用（它只处理 JSON 请求，不吐静态文件）。

---

## 2.2 路径 A：PHP 服务端

### 前置条件

- **PHP 8.0+**，扩展：`json`、`hash`（默认带）、`mbstring`、`zip`、`curl`
  缺了不会把功能弄死，会**自动降级**并在面板「环境自检」标黄：`mbstring` → 截断改 `substr/stripos`（中文可能少一个字）；`zip` → 用**内置纯 PHP 打包器**(不压缩，包变大但可正常解压)；`curl` → `webhook_test`/`batch_deploy` 退到 stream（失去 IP 固定）。装齐即可回到完整行为
- **首次部署不用手写配置**：直接打开 `admin.html`，没有 `sync_config.json` 时会自动进入初始化向导（admin_token 留空自动生成）；已配置过则直接进登录框。向导只生效一次。
- **装完扩展必须重启 PHP 进程**（`php -S` 重启、`php-fpm` reload）：扩展只在进程启动时加载，只 `apt install` 不重启等于没装（实测踩过）。面板「环境自检 → 重新检测」能直接告诉你还缺什么
- 一个把 `.php` 交给 php-fpm 的 Web 服务；**只在本机验证**时不用装 nginx，直接用 `php -S`

### 放文件：五项必须在同一目录

```
<WEB_ROOT>/server/
├── sync_api.php      ← 唯一对外暴露的入口，71 个 action 都走它
├── sync.py           ← 分发版客户端，必须同目录：whoami.client_md5 拿它算 MD5
├── tools/            ← 11 个 PHP 工具模块，按需加载，路径写死为「入口同目录/tools/」
├── sync_config.json  ← 或用 AI_SYNC_CONFIG_FILE 外移
└── admin.html        ← 管理面板，要用面板就放这里（见 2.4）
```

### nginx 最小可用配置

```nginx
server {
    listen 443 ssl;
    server_name your-server.example;
    root /path/to/webroot;

    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_pass 127.0.0.1:9000;          # 按你的 php-fpm 实际监听改
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        # 推荐：配置外移（同时删掉目录里的 sync_config.json）
        # fastcgi_param AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json;
    }

    # 配置没外移时必须有；路径照你的真实 URL 写
    location = /server/sync_config.json { deny all; return 404; }
}
```

Apache：把 `SetEnv AI_SYNC_CONFIG_FILE /etc/ai-sync/sync_config.json` 放进 vhost，其余相同。

### 不装 nginx 也能先验证（已实测）

```bash
cd <那个目录>
php -S 127.0.0.1:8899 -t .
```

另开一个终端：

```bash
curl -s -X POST http://127.0.0.1:8899/sync_api.php \
  -H 'Content-Type: application/json' \
  -d '{"action":"ping","token":"<客户端token>","scope":"works"}'
```

返回 `{"ok":true,"code":0,...}`、带 `version` 与 action 清单即成功（PHP 8.1.2 实测 200）。
再用浏览器开 `http://127.0.0.1:8899/admin.html`，能看到登录页。

---

## 2.3 路径 B：Python 服务端（自带 HTTP 服务，不需要 nginx）

### 前置条件

- **Python 3.8+**
- POSIX（Linux / macOS / Termux）与 Windows 都能跑；**只有 `exec` / `shell` 需要 `sh`**
  （Windows 用 Git for Windows 自带的 MSYS2 `sh`；纯文件同步类 action 不需要）
- 默认明文 HTTP。要 HTTPS 就传证书；不传证书时**只绑 `127.0.0.1` 或内网地址**

### 放文件：三项同一目录

```
/opt/ai-sync/
├── sync_api.py
├── sync_config.json
└── sync.py            ← client_download 与 whoami.client_md5 需要它同目录
```

> 不需要 `tools/` —— 那是 PHP 端的工具层，Python 端把 action 实现内联在自己文件里。

### 启动

```bash
# 明文 HTTP（本机或内网）
python3 sync_api.py --config sync_config.json --host 127.0.0.1 --port 8900

# HTTPS（公网）
python3 sync_api.py --config sync_config.json --host 0.0.0.0 --port 5443 \
  --tls-cert /path/fullchain.pem --tls-key /path/privkey.key
```

参数：`--config`（默认同目录 `sync_config.json`）/ `--host`（默认 `0.0.0.0`）/ `--port`（默认 `5443`）/ `--tls-cert` / `--tls-key`。

实测（Python 3.8.20，Linux）：启动日志 `listening on http://127.0.0.1:8900 (tls=False)`。

### 验证

```bash
curl -s http://127.0.0.1:8900/healthz
# → {"ok":true,"service":"ai-sync-python",...}

curl -s -X POST http://127.0.0.1:8900/sync_api.php \
  -H 'Content-Type: application/json' \
  -d '{"action":"ping","token":"<客户端token>","scope":"works"}'
```

### 客户端怎么指过来

```text
API_URL = "http://127.0.0.1:8900/sync_api.php"             # 明文
API_URL = "https://your-server.example:5443/sync_api.php"  # HTTPS
```

> 历史部署的 Python 端可能没有 `client_download`，切过去会让自动升级失效。
> 先 `{"action":"help"}` 看 `available_actions` 里有没有它。

---

## 2.4 管理面板 `admin.html`（人工巡检用，可选）

纯静态单页，不用构建。它只是同一套 71 个 action 的可视化壳。

### 放哪、怎么打开

| 服务端 | 怎么托管 | 打开地址 |
|---|---|---|
| PHP 端 | 丢进 `sync_api.php` 同目录 | `https://your-server.example/server/admin.html` |
| Python 端 | `sync_api.py` **不托管静态文件**，用任一静态服务托管（或本地 `file://` 打开） | 登录框里填完整 `API URL`，例如 `http://127.0.0.1:8900/sync_api.php`（服务端已回显 CORS） |

> ⚠️ Python 端直接访问 `http://host:8900/admin.html` 会拿到 **HTTP 200 + JSON 错误体**
> （`{"ok":false,"code":1011,"error":"bad_json"}`），不是页面。200 在这里不代表成功，别被状态码骗了
> —— 这就是为什么 PY 端必须另找一个静态服务托管面板。实测（Python 3.12.11 + 3.8.20 同结论）。

### 登录

密码框填 **`admin_token`**。普通 token 权限不足，很多模块会 403。

> token 会明文存进浏览器 `localStorage`（键 `ai_sync_admin_token`）。只在可信设备登录，用完点退出。

### 推荐顺序（照这个顺序点，不会卡）

1. **登录**（`admin_token`）
2. **添加目录授权**：scope 名 + 绝对 `work_dir` —— 这就是所有文件操作的根
3. **添加 Token**：给客户端单独建一个，只勾 `read/write/delete`；**`admin_token` 绝不下发客户端**；
   要 `exec` 再单独授予并配 IP 白名单
4. **打包客户端**：生成 zip（内含预置配置的 `sync.py` / `sync.conf` / `README` / `start.sh`），分发到各设备
5. **看「服务器状态」**：`deployment_warnings` 必须为空

面板分六组：概览（状态/身份/调用分布/最近变更）、授权（目录与 Token）、文件（浏览/搜索/语法检查/格式化/统计）、
回滚（备份与批次回滚）、追溯（审计与操作日志）、执行（远程命令、跑 PHP 脚本）。
逐项与 CLI 的对应关系见 [docs/ADMIN.md](docs/ADMIN.md)。

> 安装包 zip 内含**明文 token**，生成目录不要在 Web 根内（自检会报 `packages_in_webroot`），
> 下载完把服务器上的副本删掉。

---

## 2.5 部署客户端（自举）

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

## 2.6 把服务器交给 AI（面板打包 → 一条链接）

人只做三步，剩下的 AI 自己接：

1. 面板「授权 → 添加目录授权」：scope 名 + 绝对 `work_dir` —— 这是 AI 能碰的全部范围
2. 面板「授权 → 添加 Token」：给客户端单独建一个，只勾需要的能力（`read/write/delete`，要跑命令再加 `exec`）；**`admin_token` 绝不下发**
3. 面板「概览 → 打包客户端」→ 复制它给的 `pkg_key` 链接

链接长这样（**下载不需要 token**）：

```
https://your-server.example/server/sync_api.php?pkg_key=<64 位hex>
```

包里 `sync.conf` 已内置 `API_URL` / `API_TOKEN` / `SCOPE`，所以**这条链接本身就是凭据**：默认 24 小时过期，别贴公开频道。

把链接加一句任务丢给 AI：

> 这是 ai-sync 客户端包：`<链接>`
> 按包内 `README.txt` 接入：下载解包 → `python3 sync.py --workdir <稳定目录>` → `--whoami` → `--auto pull`。
> 然后帮我 `<任务>`。

AI 侧实际四步（2026-11-05 对真实公网服务器实测）：

| 步骤 | 实测结果 |
|---|---|
| 下载解包 | `200` / `application/zip` / `223674` 字节 → `README.txt` `start.sh` `sync.py` `sync.conf` |
| `--workdir` 自举 | `ok=true`、`scope=Lan`、`exec=true`、服务端根 `/mnt/data/wwwroot/default/lan/` |
| `--whoami` | `permissions=[read,write,delete,exec,lint,backup]`、`is_admin=false` |
| `--auto pull` | `pulled=11`、`failed=0`、回读 MD5 三方一致 |

> ⚠️ 包里的 `WATCH_DIR` 是空的，**必须先 `--workdir`**；跳过自举直接 `--whoami` 会报「未指定工作目录」（实测）。
> 给 AI 的完整话术、该断言哪些字段、8 条坑，见 [docs/AI-USAGE.md](docs/AI-USAGE.md)。

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
| Python 服务端 Windows | 现已可用：`fcntl` 改为 `try/except` 降级 + 线程读管道，启动不再崩溃；`exec`/`shell` 协议经 Git 自带 MSYS2 `sh` 解释（需 `sh` 在 PATH，Git for Windows 自带）。纯文件类 action（`ping`/`upload`/`download`/`pull`/`push`/`ls`/`cat` 等）无需 `sh` 即可用。 |
| 双端协议等价有缺口 | 仓库里的 Python 服务端已补 `client_download`；但历史部署的 Python 端可能没有该 action，切过去会让自升级失效。用 `{"action":"help"}` 确认。 |
| 自升级无签名 | 只做 MD5 + `py_compile`，见上。 |
| 日志时间窗口 | 部分 PHP 日志格式（`[04-Nov-2026 05:54:08 UTC]`）不匹配 24h 窗口解析，且本地时间与服务端 UTC 可能有偏移；以 `top_errors` / `known_noise` 为准。 |
| 版本号 | `APP_VERSION` 当前为 `v4.39.4`；仍建议用 MD5（`--check-update` / `--selfcheck`）判版本，因为本地副本与服务端分发版可能不同步。 |
| 无一键脚本 | 本仓库不提供 `scripts/quickstart.py`，请按上面的手工步骤。 |
| 管理面板 | 浏览器全流程已实测（2026-11-05）：登录 → 目录授权 → Token → 打包全部可用，见 [docs/SELF-TEST.md](docs/SELF-TEST.md)。Python 端仍需另找静态服务托管，见 [docs/ADMIN.md](docs/ADMIN.md)。 |
| 客户端 token 的可见范围 | 普通 token（无 `admin`）只见 52 / 67 个 action，`config_*` / `token_*` / `scope_*` / `package_*` / 审计类共 15 个需 `admin_token`。 |

---

# 七、文档

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 各形态部署与排错（含实测/未实测标注）
- [docs/AI-USAGE.md](docs/AI-USAGE.md) — 把服务器交给 AI：面板打包 → 一条链接 → AI 自己接入
- [docs/SELF-TEST.md](docs/SELF-TEST.md) — 全功能实测报告：面板三步 → 打包 → 67 个 action 逐条结果与错误码对照
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
