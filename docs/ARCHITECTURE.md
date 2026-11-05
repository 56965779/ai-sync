# 架构

> 本文描述的架构来自仓库源码（`sync_api.php` / `sync_api.py` / `sync.py` / `tools/`）。
> 设计目标是「**跨平台、跨设备、AI 专用**」：一套协议同时服务人类（管理面板）和 Agent（JSON CLI）。

---

## 1. 角色

```
┌──────────────┐         HTTPS JSON          ┌──────────────────────────────┐
│  设备 / Agent │  ───────────────────────▶  │  服务端 (sync_api.php/.py)   │
│  sync.py     │  ◀───────────────────────  │  work_dir (受控目录命名空间) │
│  sync.conf   │     X-Client-Md5 自动升级   │  sync.py (参考客户端分发版)  │
└──────────────┘                            │  tools/*.php (action 实现)  │
        │  human 巡检                         │  sync_config.json (配置)    │
        ▼                                    └──────────────────────────────┘
   admin.html (纯静态单页, 同一套 67 action)
```

- **服务端**：接收 JSON 请求，按 `action` 分派到 `tools/` 下的实现；所有文件操作都在
  scope 的 `work_dir` 内做边界校验。
- **客户端 `sync.py`**：一个文件搞定（依赖仅标准库），负责 pull / save / fix / status /
  doctor / health / 远程 exec / 自动升级，默认输出一行合法 JSON。
- **管理面板 `admin.html`**：纯静态，对 67 个 action 的可视化封装，无独立后端。

## 2. 双实现契约等价

服务端有两份：PHP（`sync_api.php`）与 Python（`sync_api.py`）。两者对外暴露**同一个**
action 集合、错误码、响应信封。选哪份取决于部署环境：

| 形态 | 适用 | 备注 |
|---|---|---|
| PHP + nginx/php-fpm | 生产首选（实测） | 无第三方依赖 |
| PHP 内置服务器 `php -S` | 本地调试 | 绝不绑公网 |
| Python `sync_api.py` | 无 PHP 环境 | 仅 POSIX（依赖 `fcntl`）；Windows 启动即失败 —— **已知未覆盖边界** |
| Python + TLS | 需要原生 TLS 反向代理 | `--tls-cert/--tls-key` |

> **自动升级通道只依赖 `sync.py` 分发的那份**。服务端 `whoami.client_md5` 取同目录
> `sync.py`（参考客户端）的 MD5；客户端每次启动据此静默升级。因此：
> - 改 `sync_api.php` 会影响**全部**客户端与回滚能力 —— 谨慎。
> - 给客户端打功能补丁，正确做法是替换服务端同目录的 `sync.py`，让全网自动升级。

## 3. 目录与权限模型

```
sync_config.json
  └─ scopes:
       works:
         work_dir: /abs/path/          # 该 scope 的文件根
         exec_enabled: bool
         exec_allow_ips: [...]
```

- 所有 `path` 必须在 `work_dir` 内；越界 / 绝对路径 / `../` → `1004`。
- 软链接指向 `work_dir` 外的，服务端拒绝跟随（防目录穿越）。
- token 分两级：`scope token`（绑定目录 + 权限位，下发设备）与 `admin_token`（管理全部）。
  **admin_token 绝不能进客户端配置或安装包**。

## 4. 同步协议（pull）

1. 客户端自举（`--workdir`）写好 `WATCH_DIR` / `LOG_FILE` / `VERIFY_SSL`，输出 `READY`。
2. `--auto pull`：从服务端拿清单，比对本地快照，下载差量；8 线程并发。
3. 落盘后回读 MD5，要求 **本地 / 远端 / 快照三方一致**；跳过指向 `work_dir` 外的软链接。
4. 写 `.sync_snapshot.json` / `.sync_oplog.jsonl` / `.sync_history.json` 作为下次差量基线。

退出码语义（机器可读）：
`0=成功 | 1=不一致/环境 | 2=参数/文件 | 3=远程命令失败 | 4=部署/回滚失败 | 99=锁定/策略`。

## 5. 自动升级闭环

```
客户端启动
  └─ _auto_upgrade(): whoami.client_md5 vs 本地 sync.py md5
       不一致 → client_download() 拉取
              → 校验 X-Client-Md5 == 响应体 md5
              → py_compile 预检
              → 备份原文件 (.bak.<md5>)
              → 原子替换
```

- 节流：`AUTO_UPGRADE_INTERVAL` 内不重复升级。
- 回滚：替换前备份到 `sync.py.bak.<原md5>`，出问题直接恢复。
- 暂停：客户端 `--no-auto-upgrade` 可临时跳过（排障用）。

## 6. 可观测性

- **审计日志 `audit_log`**：每个请求至少一条，含 `action` / `code` / `token_name` / `ip` /
  `server_ts`。
- **操作日志 `oplog_*`**：客户端自报的高层操作，服务端补环境信息。
- **错误日志 `error_log`**：读服务端原生错误日志（PHP / 审计等），可按关键词时间过滤。
- **部署自检 `health` / `ping`**：`deployment` / `deployment_warnings` 结构化输出配置是否
  在 Web 根、token 是否暴露等高危项，避免 Agent 误判。

## 7. 设计取舍

- **单文件客户端**：部署到一台新设备只需 `sync.py` + `sync.conf`，不依赖 pip 环境。
- **JSON 契约优先**：`{ok, code, stdout, stderr, command}` 顶层结构，让 Agent 直接 `json.loads`
  一行输出即可决策；`fix_hint` 给出可直接执行的修复步骤。
- **上下文经济性**：默认折叠逐文件进度（每 ~500 条一行摘要），`--verbose` 才展开明细，
  避免 4000 行进度淹没模型上下文。
- **可收敛诊断**：`doctor` 只拉取服务端实际存在的日志源，不制造噪声，连续诊断结果稳定。
