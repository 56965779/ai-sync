# 实测数据

> 本节全部数字来自本项目的真实运行（Windows 11 客户端 `sync.py` + Termux/nginx/php-fpm 服务端，
> HTTPS 公网链路）。标注「源码注释」的为代码内自带说明，未在本机独立复跑。
> 2026-11-05 补充：本地 `php -S`（PHP 8.1.2）与 POSIX 上的 Python 服务端都已实机跑通，数字见第 9 节；仍未实测：Windows 服务端、macOS。

---

## 1. 全量首次拉取

| 指标 | 数值 |
|---|---|
| 远程清单文件数 | 4002 |
| 实际下载 | **3998 个文件，约 189 MB** |
| 跳过（越界软链接，如 `sdcard`） | 2 |
| 失败 | 0 |
| 并发 | 8 线程 |
| 落盘校验 | 3998 个文件 MD5 回读，**本地 / 远端 / 快照三方一致** |
| `--status` 复核 | `OK=3998 DIRTY=0 MISSING=0` |

结论：核心拉取 / 校验链路可靠。

## 2. 上下文经济性（进度输出折叠）

目标：拉取同一批 **722 个文件**（`plugins/`），对比补丁前后 `stderr` 体积。

| 版本 | `stderr` 总行数 | 体积 | 逐文件进度行 |
|---|---|---|---|
| 原版 | 728 行 | 46,127 B | 722 行（每个文件一行） |
| 补丁版（默认） | 7 行 | **868 B** | 2 行（第 500 / 722 条）+ 3 行摘要 |
| 补丁版 `--verbose` | 724 行 | — | 恢复全量明细（可调试） |

**默认省约 98% 日志体积**，且不影响可调试性（`-v` 一键恢复）。

## 3. RTT 批处理

| 场景 | 说明 |
|---|---|
| `pipeline` 10 步 | 一次往返执行多 action，源码注释称约 **快 4 倍**（省 9 次 RTT） |
| 适用 | AI 连续操作（读 → lint → 备份 → 写）替代逐条请求 |

> 该倍数来自源码 `pipeline` action 注释，未在本机独立压测；RTT 收益随链路延迟放大。

## 4. 读文件省 token

| 调用 | 说明 |
|---|---|
| `read_range` `format=text` | 源码注释称比默认 JSON 回显 **省约 29% token** |
| 适用 | Agent 读大文件局部（如日志、单文件 diff） |

## 5. 诊断可收敛性

连续多次 `--doctor --brief` 的 `errors` 计数：

| 版本 | run1 → run2 → run3 → … | 说明 |
|---|---|---|
| 原版 | 6 → 7 → 8 → 单调增长 | `doctor` 无条件探测不存在的日志源，服务端把 `1005` 写入审计，下轮又计为 error —— **自激噪声** |
| 补丁版 | 5 → 5 → 5 → 5（稳定） | 只拉取实际存在的日志源；连续 6 次诊断后审计中 `error_log/1005` 计数 **14 → 14 不变** |

## 6. 跨设备健康采集

| 版本 | `disk` 字段 | 说明 |
|---|---|---|
| 原版 | `/ 100%`，`load=""`，`processes="0"`，却 `result=ok` | 硬编码 `df -h /`，在 Android/Termux/容器只读 system 分区上误报 |
| 补丁版 | 取 `work_dir` 所在分区得 `81%`；uptime 负载；32 进程 | 结构化解析 `df` 输出，采集失败给 `health=unknown` 而非假健康 |

## 7. 自动升级

| 指标 | 数值 |
|---|---|
| 升级触发 | 客户端启动比对 `whoami.client_md5`，不一致即升级 |
| 校验 | `X-Client-Md5` 头 == 响应体 md5，`py_compile` 预检 |
| 回滚 | 替换前备份 `sync.py.bak.<原md5>` |
| 实测往返 | 原版 → 补丁版 → 原版 真实部署 + 自动升级验证通过（可逆） |

## 8. 环境覆盖矩阵（源码声明）

| 环境 | 状态 |
|---|---|
| Ubuntu 24.04 / Debian 12 | 实测 |
| Alpine 3.20（musl + BusyBox） | 实测 |
| 最小 PATH / `php -n` 无扩展 / `LC_ALL=C` / 原生 TLS | 实测 |
| Android Termux | 实测（本项目服务端即运行于此） |
| Windows 客户端 `sync.py` | 实测（本机），含 `fcntl → msvcrt` 降级 |
| Windows 服务端 `sync_api.py` | **未覆盖**：模块级 `import fcntl` 启动即失败 |
| macOS | 代码级降级保护，未实机运行 |

## 9. 本地复跑（2026-11-05）

维护环境：Linux（Ubuntu 22.04 容器）、Python 3.8.20（`uv` 安装）、PHP 8.1.2。

| 项目 | 结果 |
|---|---|
| `python3.8 -m py_compile sync.py sync_api.py` | 退出码 0 |
| `python3.8 sync.py --version` | `ok=true`，`version=v4.39.4` |
| PHP 服务端 `php -S 127.0.0.1:8899 -t .` | `ping` 200 |
| 客户端 → PHP 服务端 `--auto pull` | `pulled=2`、`failed=0`、三维 MD5 一致 |
| Python 服务端 `sync_api.py --port 8900`（无 TLS） | 启动日志 `listening on http://127.0.0.1:8900 (tls=False)`，`ping` 200 |
| `php -l` 全量 12 个 PHP 文件 | 全部 `No syntax errors detected` |
| GitHub Actions（commit `e4b7a31`） | 6/6 job 绿：Python 3.8 / 3.12、PHP 8.0 / 8.3、契约、文档 |

> 复跑方法：服务端用 `php -S` 或 `sync_api.py` 起在 `127.0.0.1`，`sync.conf` 里把
> `API_URL` 指过去、`VERIFY_SSL=false`，然后 `--whoami` / `--auto pull`。
> 三个临时目录（服务端工作区 / 客户端工作区 / 配置）都放在 `/tmp` 之外更好，
> 因为 `--workdir` 会回写进 `sync.conf`。
