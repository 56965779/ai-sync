# API 参考

> 本文件由 `{"action":"help"}` 运行时自描述生成，与实际代码一致。
> 全部 action 通过唯一入口 `sync_api.php`（PHP）或 `sync_api.py`（Python）提供，两者契约等价。

---

## 1. 请求信封

```json
{
  "token":  "str 必填  —— 客户端/管理员令牌",
  "scope":  "str 多目录模式下必填 —— 指定操作的目录命名空间",
  "action": "str 必填 —— 见下方 action 索引",
  "...":    "各 action 专属参数，详见 help?name=<action>"
}
```

- 传输层：POST `application/json`（也可 multipart / 分片上传）。
- **HTTP 状态码**与业务码解耦：
  - `1001 未授权` → HTTP 401
  - `1002 无权限` → HTTP 403
  - `1003 限流` → HTTP 429
  - 其余（含 token 之外的所有业务错误）→ **HTTP 200**，错误体在 JSON 内表达。
- 响应恒含 `{ok, code}`；失败额外含 `error` / `message` / `fix_hint`（**可直接照做的修复建议**，机器可执行）。

## 2. 错误码

| code | 含义 | 典型 fix_hint 方向 |
|---|---|---|
| 1001 | 未授权（token 无效或缺失） | 检查 token / 是否被轮换 |
| 1002 | 无权限（权限位不足 / scope 未授权） | 换有权限的 token 或授权目录 |
| 1003 | 限流（请求过快） | 降速 / 调大 `per_ip_per_second` |
| 1004 | 路径非法（越界 / 绝对路径 / `../`） | 改为 scope 内相对路径 |
| 1005 | 未找到（文件或目录不存在） | 确认路径；注意 `error_log` 缺失源会写入审计 |
| 1006 | 文件过大（超过 `MAX_SIZE`） | 用分片上传 `chunked_upload` |
| 1007 | 校验失败（md5 / 乐观锁冲突） | 重新拉取后合并 |
| 1008 | 写入失败（权限 / 磁盘） | 检查 work_dir 可写与磁盘空间 |
| 1009 | exec 未启用 | 在 `sync_config.json` 打开 `exec_enabled` |
| 1010 | 备份不存在 | 先 `backup_ls` 看可用备份 |
| 1011 | 参数非法（缺失或类型错误） | 补齐必填 `r` 字段 |
| 1012 | 分片缺失 | 重传缺失片或 `abort` 后重来 |
| 1013 | webhook 失败 | 检查 webhook URL 可达性 |
| 1014 | 校验未通过（语法错误等） | 见 `lint` 返回的具体行 |
| 1099 | 服务器内部错误 | 查 `error_log` / 服务端日志 |

## 3. 客户端 — 服务端 鉴权模型

- **scope token**：绑定到某个目录命名空间，带 `read/write/delete/exec` 等位。下发到设备用。
- **admin_token**：可调用所有管理类 action（`scope_add`、`token_add`、`config_reload`…）。
  **千万不要把 admin_token 写进客户端 `sync.conf` 或安装包**。
- **目录边界**：所有 `path` 都必须在 `work_dir` 内，越界返回 `1004`。软链接指向 `work_dir`
  外的，服务端拒绝跟随（防目录穿越）。

## 4. action 索引（67 个）

### 4.1 系统 / 自描述
| action | 说明 | 必填 |
|---|---|---|
| `ping` | 连通性 / 版本号 / 全部可用 action / 当前 token 权限 | — |
| `health` | 健康检查：目录可写 / 磁盘空间 / 内部目录完整性 | — |
| `metrics` | 各 action 调用次数与错误码分布 | — |
| `help` | 无参返回索引；`name=<action>` 返回参数契约、示例、返回字段 | — |
| `client_download` | 返回参考客户端 `sync.py` 字节（响应头 `X-Client-Md5` 携 md5），客户端 `--self-update` 用 | — |
| `whoami` | 当前 token 身份 / scope / 权限位 / 可用 scope 列表 | — |

### 4.2 文件读写
| action | 说明 | 必填 |
|---|---|---|
| `upload` | 写单文件（覆盖）或建软链接（传 `link_target`）；自动备份 + PHP 语法检查 | `path`,`content_b64` |
| `upload_batch` | 批量写（部分成功也返回 200，逐条看 `results`） | `items` |
| `chunked_upload` | 大文件分片：`op=init` → `put` → `assemble`（`status` 查进度 / `abort` 放弃） | `op`,`path` |
| `batch_deploy` | 全部暂存再切换，可带 pre/post 检查，失败自动回滚 | `items` |
| `mkdir` | 递归建目录；已存在时 `mode` 会修正权限 | `path` |
| `download` | 读文件（base64）；超大走流式，`Content-Length` 精确 | `path` |
| `stat` | 单文件 / 目录元信息（不读内容） | `path` |
| `list` | 目录列举；非递归默认 200 条；隐藏目录整体剪枝 | — |
| `read_range` | 按行读文本（带行号）；`format=text` 省约 29% token（实测） | `path` |
| `checksum` | 文件 / 目录校验和（PHP 端流式增量） | `path` |

### 4.3 比对 / 同步
| action | 说明 | 必填 |
|---|---|---|
| `diff` | 客户端清单 vs 服务端现状，得待推 / 待拉 / 冲突 | `local_items`,`path` |
| `sync_check` | 批量比对指定文件，逐条 `should_push/pull` / `conflict` 与原因 | `files` |
| `delete` | 删文件 / 目录（`recursive=true` 删目录） | `path` |
| `rename` | 重命名 / 移动（同 scope 内） | `from`,`to` |
| `copy` | 复制文件 | `from`,`to` |

### 4.4 代码分析 / 质量
| action | 说明 | 必填 |
|---|---|---|
| `format` | 格式化源码（php/js/css/json…） | `path` |
| `lint` | 语法检查单文件 | `path` |
| `lint_batch` | 批量语法检查 | `files` |
| `php_run` | 用 php-cli 执行 work_dir 内 PHP 脚本并捕获输出 | `path` |
| `search` | 全文搜索（带上下文行） | `pattern` |
| `todo` | 扫描 TODO/FIXME/XXX/HACK，按 tag 聚合 | — |
| `stats` | 代码统计：文件数 / 行数 / 按扩展名或目录分组 | `path` |
| `doc` | 生成本 API 文档（HTML / Markdown / JSON） | — |

### 4.5 代码片段
| action | 说明 | 必填 |
|---|---|---|
| `snippet_set` | 保存片段（同名覆盖） | `name`,`content` |
| `snippet_get` | 按名取片段 | `name` |
| `snippet_list` | 列片段（可按关键词 / 语言 / 标签过滤） | — |
| `snippet_delete` | 删除片段 | `name` |

### 4.6 备份 / 回滚
| action | 说明 | 必填 |
|---|---|---|
| `backup_ls` | 列备份（默认最近 50 条）；不传 `path` 列全部 | — |
| `backup_rb` | 用备份回滚单文件 | `path`,`backup_file` |
| `batch_rollback` | 按时间点回滚（回滚到 `before_ts` 之前最近备份） | `before_ts` |

### 4.7 批处理 / 日志 / 追溯
| action | 说明 | 必填 |
|---|---|---|
| `pipeline` | 一次往返执行多 action（省 RTT；AI 连续操作替代逐条，10 步约快 4 倍） | `steps` |
| `audit_log` | 读审计日志（每请求至少一条） | — |
| `recent_changes` | 最近写操作摘要（倒序） | — |
| `error_log` | 读服务器错误日志（可按关键词 / 正则 / 时间过滤） | — |
| `error_log_list` | 列出可读的日志源 | — |
| `oplog_append` | 客户端自报操作日志（服务端补 ip/ua/token_name/server_ts） | `entry` |
| `oplog_list` | 列操作日志的客户端 / 会话（含成功 / 失败计数） | — |
| `oplog_view` | 按 client_id 读操作日志（筛选 / 分页 / 统计） | `client_id` |

### 4.8 管理（admin_token）
| action | 说明 | 必填 |
|---|---|---|
| `admin_stats` | 各 scope 用量 / 请求量 / 错误率 | — |
| `config_get` | 读当前生效配置（敏感字段打码） | — |
| `config_reload` | 重新加载 `sync_config.json` | — |
| `scopes_list` | 列全部 scope 及其 work_dir | — |
| `scope_add` | 新增 / 更新 scope | `name` |
| `scope_delete` | 删除 scope | `name` |
| `tokens_list` | 列 token（仅显示前缀与权限） | — |
| `token_add` | 新增 token | `name` |
| `token_delete` | 删除 token | `target_token` |
| `webhook_set` | 注册 / 更新 webhook | `url` |
| `webhook_list` | 列 webhook 配置 | — |
| `webhook_delete` | 删除 webhook | `id` |
| `webhook_test` | 发送测试请求验证可达 | — |

### 4.9 客户端打包
| action | 说明 | 必填 |
|---|---|---|
| `package_list` | 列已生成安装包 | — |
| `package_delete` | 删除安装包 | `file` |
| `client_package` | 生成定制客户端 zip（内含预置 `sync.conf`，**含明文 token**） | — |

### 4.10 远程执行（持久会话）
> 对齐原生 ssh：会话内 `cd` / `export` 跨调用保持，不必每条命令重复绝对路径。

| action | 说明 | 必填 |
|---|---|---|
| `exec` | 一次性执行 shell（受 `exec_enabled` 与 IP 白名单约束） | `cmd` |
| `shell_open` | 打开持久 shell 会话 | — |
| `shell_run` | 在会话内执行；`cd` / `export` 持续生效 | `session_id`,`cmd` |
| `shell_job` | 查询后台任务输出与退出码（对齐 `nohup &` 后回看） | `job_id` |
| `shell_kill` | 终止后台任务（先 TERM 后 KILL，杀整棵进程树） | `job_id` |
| `shell_close` | 关闭会话并清状态（幂等） | `session_id` |
| `shell_list` | 列所有会话与后台任务 | — |

## 5. 典型调用示例

探测 + 版本 + 能力：
```bash
curl -s -X POST https://your-server/sync_api.php \
  -H 'Content-Type: application/json' \
  -d '{"token":"<TOKEN>","action":"ping"}'
```

读取文件前 50 行（省 token）：
```json
{"token":"<TOKEN>","action":"read_range","path":"src/app.php","start":1,"end":50,"format":"text"}
```

一次往返做三件事（省 RTT）：
```json
{"token":"<TOKEN>","action":"pipeline","steps":[
  {"action":"stat","path":"a.txt"},
  {"action":"lint","path":"a.txt"},
  {"action":"backup_ls","path":"a.txt"}
]}
```

## 6. 客户端等价命令

每个 action 在 `sync.py` 里都有对应的 CLI 封装（默认 JSON 输出，适合 Agent）。
例如 `read_range` ↔ `--read-range`、`lint` ↔ `--lint-first`、`diff` ↔ `--diff`、
`backup_rb` ↔ `--rollback`、`exec` ↔ `--exec`、`pipeline` ↔ `--pipeline`。
完整映射见 `sync.py --help-ai`。
