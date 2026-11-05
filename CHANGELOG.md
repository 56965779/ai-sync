# Changelog

本文件的版本条目来自 `sync.py` 文件头的逐版本特性注释，以及本次开源前的实测记录。
`APP_VERSION` 常量现为 `v4.39.4`（服务端从 `__DIR__/sync.py` 动态读取，无需单独维护）。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，本项目遵循语义化版本。

---

## 未发布 — 首次部署一键初始化（2026-11-05）

- **新增 `bootstrap` / `bootstrap_status`（无需 token，鉴权之前执行）**：首次部署不再手写 sync_config.json —— 打开 admin.html 自动进入初始化卡片，留空 admin_token 自动生成 40 hex，自动创建默认 scope 与工作目录；配置一旦存在立即失效（1002 already_initialized）。
- 双服务端 action 契约同步：67 → **69**。
- admin.html 启动时先探测配置状态，未初始化显示向导而非登录框；初始化成功后免输入直接登录。

## 未发布 — 扩展缺失全部降级（2026-11-05）

服务端 `sync_api.php` / `admin.html` 与 4 个 tools 文件；客户端 `sync.py` 未改（`whoami.client_md5` 不变，不触发自动升级）。

### 修复
- **缺 `zip` 时不再只给一个 `sync.conf`**：新增纯 PHP zip 写入器 `_zip_pack_pure()`（store 压缩 + CRC32 + 中央目录，UTF-8 文件名 flag），`_zip_pack()` 在 ZipArchive 不可用时自动接管。实测 `unzip -t` / `zipfile.testzip()` 双通过。
- **缺 `mbstring` 不再让 `exec` / `search` / `shell_run` 返 1099**：新增 `_utf8_trunc` / `_mb_lower` / `_mb_stripos` 三个回退封装，替换 `exec.php`、`shell.php`、`dev_tools.php`、`error_log.php` 里 8 处裸 `mb_*` 调用。
- **缺 `curl` 不再让 `webhook_test` 返 1099**：新增 `_http_request_simple()`（curl 优先、stream 回退，4xx 也能读到状态码），`webhook_test` 多返回 `transport` / `degraded`；`batch_deploy` 的 `post_check` 同样走它。
- **面板 Token 输入框留空自动生成**：`crypto.getRandomValues` → `tk-` + 40 hex，创建后当场显示明文并尝试写剪贴板（原行为：只弹「请填写 Token、名称和至少一个 Scope」）。
- **1099 直接带根因**：管理员身份下响应体新增 `exception` / `exception_at` / `exception_hint`（自动识别"缺哪个扩展、装完必须重启进程"）；非 admin 仍只回 `exception_class`，不泄漏路径。
- **部署预检覆盖扩展**：`_deployment_warnings()` 新增三条（curl=high，zip/mbstring=low，含降级后果与重启提示）；`health.extensions` 改用同一判定函数；面板首屏新增「环境自检」卡。

### 测试
- 新增 `AI_SYNC_SIMULATE_MISSING_EXT=zip,mbstring,curl` 开关（仅用于验证降级路径，生产勿设）。
- 全扩展模式与三扩展缺失模式各跑一遍 81 次真实调用：均为 74 成功 / 7 失败（6 条主动断言的错误码 + 1 条 `error_log` 无日志源），67 个 action 覆盖一致。
- 浏览器实点面板：环境自检卡五灯、留空加 Token 自动生成并显示明文。

## 未发布 — Python 服务端 Windows 兼容（2026-11-05）

**改动 `sync_api.py`**（客户端 `sync.py` 未改，`whoami.client_md5` 不变，不触发客户端自动升级）。
本次在本机 Windows（Python 3.13.12）实测跑通。

### 修复
- **模块级 `import fcntl` 崩溃**：原 `import fcntl as _fcntl` 在 Windows 直接 `ModuleNotFoundError`，
  服务端连启动都到不了。改为 `try/except` 降级（`_fcntl=None`），无 `select(管道)` 时读取子进程输出
  改用线程读管道（`_drain_pipes_threaded`）。
- **`exec`/`shell` 在 Windows 上「拒绝访问」**：`subprocess(... shell=True)` 在 Windows 走 `cmd.exe`，
  而 exec/shell 协议是 POSIX 语法（`; $? 2>/dev/null sh -s` 管道），`cmd.exe` 无法解释。
  新增 `_posix_shell_spawn` / `_posix_shell_run`，Windows 显式以 Git 自带 MSYS2 `sh` 为解释器。
- **`os.killpg` / `os.getpgid` / `signal.SIGKILL` 在 Windows 不存在**：`_exec_kill_tree` 在 Windows
  改用 `taskkill /T /F` 整树强杀；`action_shell_kill` 同步改用跨平台封装。
- `_exec_sh_bin()` 增加 Windows Git 路径回退，PATH 里没有 `sh` 时自动找常见 Git 安装位置。

### 实测（Windows / Python 3.13.12，`sync_api.py --port 8900` 明文 HTTP）
`ping` / `whoami` / `--auto pull` / `health` / `doctor` / `exec`（异步协议 + cwd + 文件写回）/
超时 `sleep 30 --timeout 3` → `rc=124`（9.8s 返回）/ `shell` 持久会话 全部通过。

---

## 未发布 — 仓库维护（2026-11-05）

本批只改仓库工程化与文档一致性，**不改 `sync.py`**（`sync_api.py` 的 Windows 兼容修复见上一节），
因此 `whoami.client_md5` 不变、不触发客户端自动升级，`APP_VERSION` 保持 `v4.39.4`。

### 新增
- `.gitignore`：此前只存在于文档描述里，仓库实际没有。缺少它时，按 README 执行
  `cp sync_config.example.json sync_config.json` 后再 `git add .`，会把明文 token 提交上去
  —— 而本项目的前提是「拿到 token ≈ 拿到服务器」。现在把运行期配置、同步产物、
  审计/操作日志、打包产物一次性排除。
- `.gitattributes`：只声明**新增文件**的行尾，既有源码保持原字节（见文件内注释）。
- `.editorconfig`：UTF-8 / LF / 4 空格，避免编辑器引入混合行尾。
- `.github/workflows/ci.yml`：Python 语法（3.8 / 3.12）、PHP lint（8.0 / 8.3）、
  双端契约一致性、README 内部链接与示例配置占位符检查。
- `.github/scripts/check_contract.py`：零依赖检查器 —— 双端 action 集合必须完全相同、
  README `N 个 action` 必须等于实现数量、`APP_VERSION` 与配置模板版本必须一致、
  `sync_config.json` / `sync.conf` 不得被 git 跟踪。
- `.github/ISSUE_TEMPLATE/`（bug / feature 表单 + 安全私密报告入口）、
  `.github/PULL_REQUEST_TEMPLATE.md`（把「契约影响」列为必勾项）。

### 修复（文档与实现漂移）
- `README.md` 配置示例的 `version` 由 `4.39.0` 更正为 `4.39.4`，与 `sync_config.example.json` 一致。
- `README.md` 已知边界表里「`APP_VERSION` 仍为 `v4.39.0`」更正为当前 `v4.39.4`，
  并保留「用 MD5 判版本」的原始建议。
- `README.md` 仓库结构树里 `tools/*.php` 写作「12 个工具模块」，实际为 11 个，已更正；
  同时补上 `.github/` 与 `.editorconfig`。

### 已核对（无需改动）
- `sync_api.php` 与 `sync_api.py` 的 `HELP_SPEC` 各 67 个 action，集合完全相同。
- README 内部文档链接（6 篇 docs + 3 个根文件）全部存在。
- 示例配置全部为 `CHANGE_ME_*` 占位符；历史提交中从未出现 `sync_config.json` / `sync.conf`。
- `python3 -m py_compile sync.py sync_api.py` 通过。

### CI 修复（同日二轮，实测驱动）
- 首跑唯一失败的是 `Python 语法（3.8）` 这个 leg，报错原文
  `The version '3.8' with architecture 'x64' was not found for Ubuntu 26.04`
  —— `ubuntu-latest` 已解析到 26.04，而 `actions/setup-python@v5` 的预编译清单里没有该镜像的 3.8 构建。
  代码本身在 3.8.20 上 `py_compile` 与 `sync.py --version` 均通过。
- 改法：`python` job 换 `astral-sh/setup-uv` + `uv python install`，与 runner 镜像解耦；
  `php` job 固定 `ubuntu-22.04`（`setup-php` 在其上装 8.0 / 8.3 是稳定组合，26.04 装 8.0 需额外源）。
  修复后 run `e4b7a31` 的 6 个 job 全绿。

### 实测补齐（文档口径据此改写）
- 装上 PHP 8.1.2 后本地起 `php -S 127.0.0.1:8899 -t .`：`ping` 返回 200；`php -l` 全量 12 个 PHP 文件无语法错误。
- Python 3.8.20 客户端连本地 PHP 服务端：`--whoami` 通过，`--auto pull` 拉到 2 个文件、
  `failed=0`、回读 MD5 三方一致。
- POSIX 上 `sync_api.py --host 127.0.0.1 --port 8900`（不传 TLS 参数）起服务，`ping` 返回 200。
- 文档同步：README 覆盖表「本地 `php -S`」由 ⚠️ 未实测改为 ✅ 实测，
  「Python 服务端」由 ⚠️ 部分改为「✅ POSIX / ❌ Windows」，环境要求写明实测解释器版本；
  `docs/DEPLOYMENT.md` 第 2 / 3 节改写为已复跑；`docs/BENCHMARKS.md` 新增第 9 节。

---


## v4.39.4 (2026-11-05) — 开源发布准备

此版本把「AI 契约加固」正式定型并补齐开源必要条件。服务端 `whoami.client_md5` 取同目录
`sync.py` 的 MD5，因此本版本通过替换服务端分发版 `sync.py` 全网生效（已实测自动升级与回滚）。

### 新增（开源必要条件）
- 版本号 `APP_VERSION` 由 `v4.39.0` 提升到 `v4.39.4`，消除「加固版仍是旧版本号」的歧义；
  服务端从 `__DIR__/sync.py` 动态读取，配置模板 `version` 同步为 `4.39.4`。
- 元文件：`.gitignore`（保护 `sync_config.json` / `sync.conf` / 备份与包）、`.gitattributes`
  （行尾与导出属性）、`sync.conf.example`（含全部配置键与中文注释）。
- 文档体系：`README.md` 重写（简介 / 下载 / 三种部署 / 客户端自举 / 日常命令真实 JSON /
  AI 改码闭环 / 实测数据 / 安全边界）；`docs/` 下 `DEPLOYMENT` / `ADMIN` / `API` /
  `ARCHITECTURE` / `BENCHMARKS` / `THREAT_MODEL` 六篇，全部以实测为准。
- 全部 `tools/*.php` 补齐 `SPDX-License-Identifier: MIT`。

### 修复
- 命令契约：`--no-auto-upgrade` 加入顶层 `command` 修饰参数白名单，不再因参数顺序把
  `command` 写成 `no-auto-upgrade`。
- 文档一致性：服务端 HELP_SPEC 与注释里的 `--self-upgrade` 更正为实际命令 `--self-update`。

### 开源化（继承自 v4.39.3 快照）
- AI 契约加固全部并入（诊断可收敛、结论带证据、退出码语义、跨设备健康、动态版本判定、
  Python 3.13/3.15 日期解析、上下文经济折叠）。
- 移除客户端硬编码私有服务地址（`--via-m3u8` 改为 `m3u8_proxy` 配置项）。

---

## 开源快照（2026-11-04）— AI 契约加固，未打 tag

这一版是仓库当前 `sync.py` 的内容，与 `v4.39.0` 共用同一个 `APP_VERSION` 字符串，
但已包含以下修复。**判断是否需要升级请用 MD5（`--check-update` / `--selfcheck`），不要用版本号。**

### 修复

- **诊断可收敛**：`--doctor` 先按服务端 `error_log_list` 实际存在的日志源取数，
  不再无条件探测 `nginx_err`；源缺失时服务端返回的 `log_not_found 1005` 不再被写进审计后
  又被下一轮诊断计成 error。实测原版连续诊断 `errors 6→7→8`，修复后稳定在 5，
  且审计中 `error_log/1005` 条数 14→14 不再增长。
- **结论与证据**：`--doctor --brief` 新增 `missing_sources` / `available_sources` / `known_noise`，
  `top_errors` 改为直接给结构化错误证据，不再靠关键词正则猜，避免「errors>0 却没有错误内容」。
- **退出码语义**：`--doctor --plain` 由「有环境问题就 rc=1 且不打印结论」改为
  `rc=0 + ok=true + health=...` 机器可读结论行；`ok` 只表示工具执行成功。
- **跨设备健康采集**：`--health` 从硬编码 `df -h /` 改为取 `whoami.work_dir` 所在挂载点，
  增加 `disk_percent` / `unavailable` / 阈值结论；`loadavg` 与进程数多路径兜底。
  实测原版在 Android/Termux 报 `/ 100% /`、`load=""`、`processes="0"` 却 `result=ok`，
  修复后为真实数据分区 81%、uptime 负载、进程数 32。
- **版本判定**：`--check-update` 从硬编码 `/mnt/data/wwwroot/default/sync/server/`
  改为由 `whoami.work_dir` 动态推导；取不到 md5 时返回 `status=unavailable` /
  `sync_py_consistent=null`，不再误报「版本不一致」。
- **Python 兼容**：syslog 无年份日期改为先补年份再解析，消除 Python 3.13 的
  `DeprecationWarning` 与 3.15 的异常；实测原版在严格警告模式下会静默吞掉整个日志源并报假健康。
- **上下文经济**：`--pull` 逐文件进度默认每 500 条 + 末条输出，`-v/--verbose` 恢复全量。
  实测 722 文件：`stderr` 728 行 / 46,127 B → 7 行 / 868 B。
- **命令契约**：`--no-auto-upgrade` 加入顶层 `command` 识别的修饰参数白名单，
  不再因为参数顺序把 `command` 写成 `no-auto-upgrade`。
- **文档一致性**：服务端 HELP_SPEC 与注释里的 `--self-upgrade` 更正为实际的 `--self-update`。

### 开源化

- 全部源码补 `SPDX-License-Identifier: MIT`；`LICENSE` 为唯一授权文本。
- 移除客户端里硬编码的私有服务地址（`--via-m3u8` 改为 `m3u8_proxy` 配置项，
  未配置且无法从 `api_url` 推导时明确报错，不再静默连第三方）。
- 新增 `.gitignore` / `.gitattributes` / `sync.conf.example` / 文档与贡献、安全披露文件。

---

## v4.39.0

- 客户端默认结构化输出（缺省注入 `--json`），顶层恒为 `{ok, code, stdout, stderr, command}`。
- 客户端静默自升级：`whoami.client_md5` 比对 → `client_download` → MD5 校验 →
  `py_compile` 预检 → 备份 → 原子替换；300 秒节流，失败保留旧版。
- 服务端 67 个 action，`{"action":"help"}` 自描述全部参数契约。
- `pipeline` 一次往返跑多步、`--batch` 单进程连续只读探查。

## v4.38.1 (2026-10-31) — 安全与一致性修复

- 配置回写不再静默失败；`--batch` 向下发 `AI_SYNC_WORKDIR`。
- TLS 证书校验失败只对本次运行降级，不再把 `VERIFY_SSL=false` 永久写盘。
- `--deploy` / `--upload-file` 补发 mtime；Windows 远程父目录路径修复。
- 新增 `--no-verify`（大目录跳过落盘 MD5 回读）；本地 MD5 并行计算。

## v4.38 (2026-10-30) — 深目录 / 父目录 / mtime=0 / watch 目录事件

## v4.37 (2026-10-30) — 状态原样：权限 / mtime / 目录

## v4.18 (2026-10-21) — 版本统一 + `--watch` 管道退出

## v4.17 (2026-10-18) — AI 契约 + 拉取提速（`--brief` / `--selfcheck` / 部署后链路验证）

## v4.16 (2026-10-18) — 增量拉取 + JSON 纯净

## v4.15 / v4.14 / v4.13 / v4.12 (2026-10-18) — AI 决策树、`--cat` 结构化、批量读写、watch 参数校验

## v4.5 (2026-09-23) — `--pull-smart` 增量拉取

## v4.4 (2026-09-09) — `--watch-subdir` 子目录级同步

## v4.3 — 上传前 MD5 对比、本地备份、冲突检测、原子写入、快照、空目录同步

### 服务端（`sync_api.php` / `sync_api.py`）

- v4.3：AI 体验增强、危险操作二次确认、exec 退出码修复、配置缺字段友好提示。
- v4.1：多目录授权（`sync_config.json` + tokens / scopes / permissions）。
- v3.2：接口层与 `tools/` 工具层拆分。
- v3.1：错误码统一，新增 list / diff / chunked_upload / webhook / health / metrics。
- v3.0：单文件 CRUD + 备份 + 探针。
