# 把服务器交给 AI：一条链接的接入方法

> 实测日期 2026-11-05。对象：v4.39.4 服务端 + v4.39.4 客户端包，公网 HTTPS（`https://<host>:5443/sync/server/sync_api.php`），scope 名 `Lan`。
> 下面每个数字都来自那次真实运行，不是设计推导。

## 这套用法长什么样

人在管理面板点三下 → 得到一个 zip 下载链接 → 把链接和一句「帮我做什么」丢给 AI → AI 自己下载、解包、自举、拉代码、干活。

人不需要懂 `sync.py` 的参数，AI 不需要拿到 SSH 密码。**凭据只在一条一次性链接里。**

## 一、人在面板做三步

| 顺序 | 面板位置 | 填什么 |
|---|---|---|
| 1 | 授权 → **添加目录授权** | scope 名（如 `Lan`）+ 绝对 `work_dir`（如 `/mnt/data/wwwroot/default/lan/`）。这就是 AI 能碰的全部范围 |
| 2 | 授权 → **添加 Token** | 给这台的客户端单独建一个 token，只勾需要的权限：至少要 `read/write/delete`；要跑命令再加 `exec`。**绝不下发 `admin_token`** |
| 3 | 概览 → **打包客户端** | 选刚才的 token + scope → 生成 zip → 复制返回的下载链接 |

得到的链接形如：

```
https://your-server.example/server/sync_api.php?pkg_key=<64 位十六进制>
```

关于这条链接，五条硬事实（源码 `_serve_package_by_key()`）：

- **下载不需要 token**，包内 `sync.conf` 已内置 `API_URL` / `API_TOKEN` / `SCOPE` → **链接本身等于凭据**
- **默认 24 小时过期**（`PACKAGE_TTL`，默认 86400 秒）；过期返回 `410 package_expired`，并删除服务器上的包
- 未过期前**可重复下载**（实测同一 key 连下两次都是 `200`、`223674` 字节，内容一致）
- 每次下载都写一条 `package_download` 审计（记录文件名 / scope / 来源 IP）
- 包里是**明文 token**，所以：不要贴公开频道、不要进 Git、发给 AI 后把服务器上的副本删掉

## 二、给 AI 的话（照抄改任务）

```
这是 ai-sync 客户端包：<pkg_key 链接>

请按包内 README.txt 接入：
1) 下载并解包到稳定目录（不要 /tmp）
2) python3 sync.py --workdir <稳定工作目录>   # 自举，必须第一步
3) python3 sync.py --whoami                  # 确认权限边界
4) python3 sync.py --auto pull               # 首次拉取

接入完成后，帮我 <你的任务>。
所有远程命令用 --exec - 的 heredoc 形式，不要直传含特殊字符的命令。
```

## 三、AI 会做的四步（含实测输出）

### 1) 下载 + 解包

```bash
curl -sSL '<pkg_key 链接>' -o /tmp/ai-sync-client.zip \
  && mkdir -p ~/ai-sync-client \
  && (unzip -o /tmp/ai-sync-client.zip -d ~/ai-sync-client 2>/dev/null \
      || python3 -m zipfile -e /tmp/ai-sync-client.zip ~/ai-sync-client) \
  && rm /tmp/ai-sync-client.zip && cd ~/ai-sync-client
```

实测：`http=200`、`Content-Type: application/zip`、`223674` 字节；解出 4 个文件：

```
README.txt   # 写给 AI 的接入说明（包自动生成，内容是这台服务器的真实地址与 scope）
start.sh     # bash start.sh | pull | save | fix
sync.py      # 客户端
sync.conf    # 已预置 API_URL / TOKEN / SCOPE
```

> `unzip` 不是每台机器都有，`python3 -m zipfile -e` 是零依赖兜底，两条命令串在一起就不用先探测。

### 2) 自举（必须先做）

```bash
python3 sync.py --workdir ~/ai-sync-client/workspace
```

实测返回：

```json
{"ok": true, "code": 0, "scope": "Lan", "server_version": "4.3", "exec": true,
 "work_dir": "/mnt/data/wwwroot/default/lan/", "monitored": 0, "snapshot_entries": 0}
```

`work_dir` 是**服务端**的 scope 根，`WATCH_DIR` 是本机目录，两者不是一回事。

> ⚠️ 包里 `sync.conf` 的 `WATCH_DIR` 是**空的**（客户端不替你猜目录，避免落到不稳定位置）。
> 所以**跳步直接 `--whoami` 会失败**：实测返回
> `{"ok":false,"code":1,"stderr":"❌ 未指定工作目录 (WATCH_DIR 为空)"}`。
> 自举一次后目录会写回 `sync.conf`，后续命令不用再带 `--workdir`。

### 3) 确认权限边界

```bash
python3 sync.py --whoami
```

实测关键字段：

```json
{"ok": true, "name": "Lan", "scope": "Lan", "is_admin": false,
 "permissions": ["read","write","delete","exec","lint","backup"],
 "exec_enabled": true, "max_upload_kb": 65536,
 "work_dir": "/mnt/data/wwwroot/default/lan/",
 "api_version": "4.3", "available_actions": [ ... 55 项 ... ]}
```

拿到手先看三件事：`permissions` 有没有 `admin`（不该有）、`exec_enabled` 是不是你真需要的、`work_dir` 是不是你要的目录。

### 4) 首次拉取

```bash
python3 sync.py --auto pull
```

实测：

```json
{"ok": true, "code": 0, "action": "pull", "pulled": 11, "skipped": 0,
 "failed": 0, "result": "ok", "failed_files": []}
```

stderr 里会有 `[校验] 回读 11 个落盘文件 MD5, 确认三方一致`。`failed=0` 是唯一该断言的东西。

## 四、接入之后 AI 的日常

```bash
python3 sync.py --auto pull                  # 增量拉取，只下变更
python3 sync.py --auto save <文件> --yes     # 部署：lint → 原子覆盖 + 自动备份 → MD5 复验，--yes 防呆必带
python3 sync.py --auto fix                   # 排障（doctor）
python3 sync.py --auto status                # 同步状态

# 远程命令：一律走 heredoc，避免引号/特殊字符转义踩坑（实测 rc=0）
python3 sync.py --exec - <<'EOF'
cd /your/scope/root && ls -la | head
EOF
```

实测 `--exec` 返回：

```json
{"ok": true, "code": 0, "remote_ok": true, "remote_rc": 0,
 "cwd": "/mnt/data/wwwroot/default/lan", "async": true, "elapsed_ms": 2011}
```

`cwd` 默认是服务端 scope 根，`remote_dir` 是临时执行目录——这两个字段要分清。

## 五、AI 只该断言的字段

| 目的 | 字段 | 判定 |
|---|---|---|
| 命令是否执行成功 | `ok` / `code` | `ok=true` 且 `code=0`；**不代表业务成功** |
| 拉取是否完整 | `pulled` / `failed` / `failed_files` | `failed=0` 且 `failed_files=[]` |
| 部署是否落地 | `result` / `verify_ok` / `verify_failed` | `result="ok"` 且 `verify_ok` 等于文件数 |
| 权限是否越界 | `is_admin` / `permissions` | `is_admin` 必须 `false` |
| 目录是否对 | `work_dir`（服务端）/ `WATCH_DIR`（本地） | 两者都核对，别混 |
| 部署是否合格 | `deployment_warnings` | 必须为空 |

## 六、这条路踩过的坑

1. **`WATCH_DIR` 为空就发命令** → 一定失败。`--workdir` 是第二步，不是可选步骤。
2. **把链接当普通地址转发** → 链接里就是 token。发群、发 issue、贴博客都等于泄露；用完删包或换 token。
3. **24 小时后才用** → `410 package_expired`，包已被删。回面板重新打包即可。
4. **把包丢进 Git** → `sync.conf` 是明文 token。仓库的 `.gitignore` 只挡 `sync.conf` / `sync_config.json`，挡不住别人改名后的 zip。
5. **机器没有 `unzip`** → 用 `python3 -m zipfile -e`，别先装包管理器。
6. **服务端版本比仓库旧**：本次实测服务端 `api_version=4.3`、`available_actions` 是 **55 项**，仓库版本是 67 项。**别背文档里的数量**，以 `--whoami` / `--api-help` 的运行时清单为准。
7. **以为 scope 名等于目录名** → scope 名只是别名，真正的边界是 `work_dir` 绝对路径。
8. **`VERIFY_SSL=auto` 误判**：先校验证书，不可信才降级。内网自签能跑通，但公网不要把 `VERIFY_SSL=false` 写死。

## 七、这次实测的原始记录

| 步骤 | 结果 |
|---|---|
| 下载包 | `200` / `application/zip` / `223674` 字节 |
| 重复下载同一 key | `200` / `223674` 字节（未过期前可重用） |
| 错误 key（64 个 0） | `404 package_not_found` |
| 包内文件 | `README.txt` / `start.sh` / `sync.py` / `sync.conf` |
| 未自举直接 `--whoami` | `ok=false`，`WATCH_DIR 为空` |
| `--workdir` 自举 | `ok=true`，`scope=Lan`，`exec=true`，服务端根 `/mnt/data/wwwroot/default/lan/` |
| `--whoami` | `permissions=[read,write,delete,exec,lint,backup]`，`is_admin=false` |
| `--ls /` | `count=13`，返回目录与文件条目 |
| `--exec -`（heredoc） | `remote_rc=0`，`cwd=/mnt/data/wwwroot/default/lan`，`elapsed_ms=2011` |
| `--auto pull` | `pulled=11`，`failed=0`，MD5 三方校验通过 |

相关：面板本身的用法见 [ADMIN.md](ADMIN.md)，服务端部署见 [DEPLOYMENT.md](DEPLOYMENT.md)。
