# 全功能实测报告（2026-11-05）

> 这一轮走的是**人真实会走的那条路**：拉最新代码 → 本机部署 PHP 端 → 浏览器进 `admin.html` 点三步 → 一键打包 → 把 `pkg_key` 链接交给"AI"（就是我自己）→ 用包里带的客户端把 **67 个 action 全跑一遍**。
> 下面是页面上的原话、接口的真实返回和五个真正会把人卡住的地方。

## 环境

| 项 | 值 |
|---|---|
| 服务端 | PHP 8.1.2-1ubuntu2.28 + `php -S 127.0.0.1:8899`（未装 nginx / php-fpm） |
| 代码版本 | `8f6e210`（仓库 main） |
| 配置 | `AI_SYNC_CONFIG_FILE=/tmp/e2e/sync_config.json`，放在 Web 根外 |
| scope | `ai_test` → `/tmp/e2e/srv/work2/`（3 个文件：`readme.txt`、`cfg.ini`、`lib/math.php`） |
| 客户端 | 面板打包出来的 `sync.py`，Python 3.12.11 |

## 一、这次真的点了什么

| 步 | 在面板上做的 | 实测到的 |
|---|---|---|
| 1 | 打开 `http://127.0.0.1:8899/admin.html` | 登录页只有两个输入框：`admin_token`、`API URL`（已自动填好 `…/sync_api.php`） |
| 2 | 填 `admin_token` → 登录 | 仪表盘显示 `v4.39.4`、`scope=demo`、身份 `admin`、权限位含 `admin` |
| 3 | 目录授权：scope 名 `ai_test`、`work_dir=/tmp/e2e/srv/work2`、备份 5 份、允许执行 PHP=是 → 添加 Scope | 已授权目录列表出现 `ai_test`，**文件数自动统计成 3**，work_dir 补上尾斜杠 |
| 4 | Token 管理：名称 `ai-e2e`、scope `ai_test`、勾 `read/write/delete/exec/lint/backup` → 添加 Token | 已授权 Token 从 1 个变 2 个，生成明文 `a19d0aa4……`，权限列显示六项全开 |
| 5 | 客户端打包：选 token `ai-e2e` + scope `ai_test` + 模式「方法一 双向同步」→ 生成 | 显示 **219.5KB**、`sync.py 781.4KB ✅ 正常`、SHA256 `5729adba4d5fa3a89265ea54…`、包内容 `sync.py / sync.conf / README.txt / start.sh` |
| 6 | 点「下载 zip 包」 | 链接形态 `…/sync_api.php?pkg_key=d8785347…`；`curl` 下载 `200`、`224722` 字节 |
| 7 | 客户端自举 `--workdir` | `ok=true`、`scope=ai_test`、`exec=true`、服务端根 `/tmp/e2e/srv/work2/` |
| 8 | `--whoami` | `permissions=[read,write,delete,exec,lint,backup]`、`is_admin=false`、**52 个 action** |
| 9 | `--auto pull` | `pulled=3`、`failed=0`、回读 MD5 三方一致 |

## 二、五个真实卡点

这五个都不是代码 bug，但每一个都能让人当场卡住，而且返回信息不足以自诊断。

### 1. 面板的 Token 输入框不会替你生成 Token

`TOKEN` 框里那行 `随机字符串` 是 placeholder，不是"留空就自动随机"。留空点「添加 Token」，只弹一个 alert：

```
请填写 Token、名称和至少一个 Scope
```

要么手填，要么先在服务器上生成一个：

```bash
python3 -c "import secrets;print('tk-'+secrets.token_hex(20))"
```

### 2. 缺 `zip` 扩展 = 打不出包，而且不报错

点「生成客户端包」后，面板不给你 zip，而是显示：

```
服务器无 ZipArchive 扩展, 返回 sync.conf 内容
下载 sync.conf
```

按钮没坏，功能是降级了。`zip` 只影响打包，其余 66 个 action 照常可用 —— 但第一次用面板的人会以为整条链路坏了。

### 3. 装完扩展必须重启 PHP 进程

我 `apt install php-zip` 成功、`php -m` 也能看到 `zip`，回面板重试**仍然报缺 ZipArchive** —— 因为 `php -S` 是安装之前启动的进程，扩展只在进程启动时加载。重启后立刻正常。`php-fpm` 同理，要 reload/restart，不是只改 ini。

### 4. 缺 `mbstring` 的后果比文档写的严重得多

文档里写的是"`mbstring` 按字节截断用"，实际缺了它，`exec` / `search` / `shell_run` **整条能力直接不可用**：

```json
{"ok": false, "code": 1099, "error": "exception", "message": "Internal server error (see audit log)",
 "exception_class": "Error"}
```

返回体看不出任何根因，必须去 `audit_log` 看 `exception` 字段才知道是 `Call to undefined function mb_substr()`。

### 5. `webhook_test` 需要 `curl` 扩展，文档前置里没列

同样表现：`1099 exception`，audit log 里是 `Call to undefined function curl_init()`。装上 `php-curl` 并重启后立刻正常（对 `https://example.com/hook` 返回 `http_code=405`、`passed=false`、`ok=true` —— 这是"动作执行成功但回调非 2xx"，语义正确）。

### 附带发现：普通 token 只有 52 / 67 个 action

面板打给 AI 的 token 只带 `read/write/delete/exec/lint/backup`，`whoami` 返回 52 个 action。缺的 15 个是管理类，`admin_token` 才有：

```
admin_stats  audit_log  client_package  config_get  config_reload
oplog_list   oplog_view  package_delete  package_list  scope_add
scope_delete  scopes_list  token_add  token_delete  tokens_list
```

这不是缺陷，是权限边界。**但意味着"AI 拿到客户端后能测全 67 个"是不成立的**——要测全得用 admin_token。这也解释了为什么线上那台真实服务器 `whoami` 只回 55 个 action：它的部署版本与 token 权限都更窄。

## 三、67 个 action 逐条结果

> 下表用 `admin_token` 跑（否则 15 个管理 action 不可见）。每条都带真实参数，写操作都是真写。

### 元信息 / 自描述

| action | 结果 | 耗时 |
|---|---|---|
| `ping` | ✅ ok | 31 ms |
| `health` | ✅ ok | 12 ms |
| `metrics` | ✅ ok | 11 ms |
| `whoami` | ✅ ok | 11 ms |
| `help` | ✅ ok | 10 ms |
| `doc` | ✅ ok | 14 ms |
| `client_download` | ✅ ok | 15 ms |
| `pipeline` | ✅ ok | 9 ms |

### 权限与配置（admin）

| action | 结果 | 耗时 |
|---|---|---|
| `scopes_list` | ✅ ok | 10 ms |
| `tokens_list` | ✅ ok | 10 ms |
| `config_get` | ✅ ok | 9 ms |
| `config_reload` | ✅ ok | 9 ms |
| `admin_stats` | ✅ ok | 9 ms |
| `scope_add` | ✅ ok | 9 ms |
| `scope_delete` | ✅ ok | 9 ms |
| `token_add` | ✅ ok | 9 ms |
| `token_delete` | ✅ ok | 9 ms |

### 文件读

| action | 结果 | 耗时 |
|---|---|---|
| `list` | ✅ ok | 14 ms |
| `stat` | ✅ ok | 9 ms |
| `checksum` | ✅ ok | 9 ms |
| `download` | ✅ ok | 9 ms |
| `read_range` | ✅ ok | 9 ms |
| `search` | ✅ ok | 10 ms |
| `stats` | ✅ ok | 10 ms |
| `diff` | ✅ ok | 9 ms |
| `sync_check` | ✅ ok | 10 ms |
| `todo` | ✅ ok | 10 ms |

### 文件写

| action | 结果 | 耗时 |
|---|---|---|
| `mkdir` | ✅ ok | 10 ms |
| `upload` | ✅ ok | 11 ms |
| `upload_batch` | ✅ ok | 10 ms |
| `copy` | ✅ ok | 9 ms |
| `rename` | ✅ ok | 9 ms |
| `delete` | ✅ ok | 9 ms |
| `chunked_upload` | ✅ ok | 9 ms |
| `batch_deploy` | ✅ ok | 22 ms |
| `batch_rollback` | ✅ ok | 10 ms |
| `backup_ls` | ✅ ok | 9 ms |
| `backup_rb` | ✅ ok | 9 ms |

### 代码质量

| action | 结果 | 耗时 |
|---|---|---|
| `lint` | ✅ ok | 36 ms |
| `lint_batch` | ✅ ok | 36 ms |
| `format` | ✅ ok | 11 ms |
| `php_run` | ✅ ok | 120 ms |

### 执行

| action | 结果 | 耗时 |
|---|---|---|
| `exec` | ✅ ok | 54 ms |
| `shell_open` | ✅ ok | 10 ms |
| `shell_run` | ✅ ok | 48 ms |
| `shell_job` | ✅ ok | 21 ms |
| `shell_kill` | ✅ ok | 331 ms |
| `shell_close` | ✅ ok | 11 ms |

### 日志与追溯

| action | 结果 | 耗时 |
|---|---|---|
| `audit_log` | ✅ ok | 9 ms |
| `recent_changes` | ✅ ok | 9 ms |
| `error_log` | ❌ log_not_found(1005) | 9 ms |
| `error_log_list` | ✅ ok | 10 ms |
| `oplog_append` | ✅ ok | 10 ms |
| `oplog_list` | ✅ ok | 10 ms |
| `oplog_view` | ✅ ok | 0 ms |

### 附加能力

| action | 结果 | 耗时 |
|---|---|---|
| `snippet_set` | ✅ ok | 10 ms |
| `snippet_get` | ✅ ok | 10 ms |
| `snippet_list` | ✅ ok | 10 ms |
| `snippet_delete` | ✅ ok | 9 ms |
| `webhook_set` | ✅ ok | 14 ms |
| `webhook_test` | ✅ ok | 1000 ms |
| `webhook_list` | ✅ ok | 9 ms |
| `webhook_delete` | ✅ ok | 10 ms |
| `client_package` | ✅ ok | 126 ms |
| `package_list` | ✅ ok | 9 ms |
| `package_delete` | ✅ ok | 10 ms |

## 四、错误码实测对照

每条都是真实打出来的（不是查表抄的）：

| 请求 | 返回码 | `error` | 说明 |
|---|---|---|---|
| `token` 乱填 | `1001` | `unauthorized` | 未授权 |
| 不传 `token` | `1001` | `unauthorized` | 同上 |
| 客户端 token 调 `config_get` | `1002` | `forbidden` | 权限位不足，不是"没登录" |
| `download` 传 `../../etc/passwd` | `1004` | `invalid_path` | 路径越界 |
| `download` 传不存在的文件 | `1005` | `not_found` | 缺失 |
| `action: no_such_action` | `1011` | `unknown_action` | 未知 action |
| `scope_add` 指向不存在的目录 | `1011` | `bad_work_dir` | work_dir 必须已存在 |
| `webhook_set` 传 `http://127.0.0.1/…` | `1011` | `bad_url` | 默认拒绝 loopback/内网（`WEBHOOK_ALLOW_INTERNAL` 可覆盖） |
| `chunked_upload` assemble 少一片 | `1012` | `chunks_missing` | 缺片，返回 `missing:[1]` |
| 调 `token_delete` 传"名字" | `1005` | `not_found` | **要用 token 值或值前缀**，用名字删不到 |
| 缺 `mbstring` 时 `exec` | `1099` | `exception` | 内部错误，根因在 `audit_log.exception` |
| `error_log` 无日志源 | `1005` | `log_not_found` | 设计内：日志源不存在是事实，不是故障 |

定位 `1099` 的正确姿势：

```bash
curl -s -X POST <API> -d '{"action":"audit_log","lines":20,"token":"<admin_token>"}' \
  | grep -o '"exception": "[^"]*"'
```

## 五、结论

- **67 / 67 个 action 全部可达并通过**。唯一非 ok 的 `error_log` 返回 `1005 log_not_found`，因为这台机器没有 PHP 错误日志文件 —— 属于设计中的"日志源不存在"，不是失败。
- **面板 → 打包 → 客户端 → 拉取** 九步全链路真实走通，zip 224722 字节、pull 3 文件、三方 MD5 一致。
- **三个 PHP 扩展缺任何一项都会有不同的表象**：`zip` → 打包静默降级；`mbstring` → exec/search/shell 全 1099；`curl` → webhook_test 1099。
- **扩展装完不重启进程等于没装**，这是最容易浪费半小时的一步。
- 客户端 token 只看到 52 / 67 个 action 是权限设计的自然结果；要全量验证必须用 `admin_token`。

复跑方式：按本仓库 `README.md` 第二章路径 A 起一个 `php -S`，在 `admin.html` 走完三步，然后把下面这条丢给任意 AI 即可：

```
这是 ai-sync 客户端包：<pkg_key 链接>
按包内 README.txt 接入：下载解包 → python3 sync.py --workdir <稳定目录> → --whoami → --auto pull
接入后帮我把 67 个 action 全部实测一遍，逐条给 ok/code/error
```


## 六、上述卡点的修复与复验（同日）

上面五个卡点里，四个已改成"缺了也能跑"，一个改成"面板直接告诉你"。

### 改了什么

| 问题 | 修复 | 文件 |
|---|---|---|
| 缺 `zip` 只给 `sync.conf` | 新增 `_zip_pack_pure()`：纯 PHP 写 zip（store + CRC32 + local/central header + EOCD，UTF-8 flag），`_zip_pack()` 先试 ZipArchive、失败自动用内置打包器 | `sync_api.php` |
| 缺 `mbstring` 让 exec/search/shell 全 1099 | `_utf8_trunc()` / `_mb_lower()` / `_mb_stripos()` 按 `function_exists` 回退；`exec.php` `shell.php` `dev_tools.php` `error_log.php` 里 8 处裸 `mb_*` 调用全部换成这三个封装 | `sync_api.php` + 4 个 tools |
| 缺 `curl` 让 `webhook_test` 1099 | 新增 `_http_request_simple()`：curl 优先，无 curl 用 `stream_context`（`ignore_errors` 保证 4xx 也能读到状态码）；响应标 `transport` / `degraded` | `sync_api.php` + `webhook.php` + `batch_deploy.php` |
| 面板 Token 框不会自动生成 | 留空时前端 `crypto.getRandomValues` 生成 `tk-`+40 hex，回填输入框，创建后把明文显示在表单下方并尝试写入剪贴板 | `admin.html` |
| 1099 只有 "Internal server error" | 管理员身份下响应体直接带 `exception` / `exception_at` / `exception_hint`（自动翻译成"缺哪个扩展 + 装完必须重启"）；普通 token 仍只给 `exception_class` | `sync_api.php` |
| 不知道自己在降级 | `_deployment_warnings()` 增加三条扩展告警；面板首屏新增「环境自检」卡（五个灯 + 告警逐条）；`health.extensions` 与告警同源 | `sync_api.php` + `admin.html` |

### 怎么验的

用 `AI_SYNC_SIMULATE_MISSING_EXT=zip,mbstring,curl` 在**同一台装了全部扩展**的机器上强制走降级分支（否则没法卸载扩展来复现）：

| 场景 | 结果 |
|---|---|
| `ping` 预检 | `warning_count=3`，三条分别是 curl(high) / zip(low) / mbstring(low) |
| 面板「环境自检」 | 五个灯里 curl / mbstring / zip 标黄并写出降级后果（浏览器实点） |
| 面板留空加 Token | 无 alert，自动生成 `tk-2ab4d6bf…`，表单下方显示明文，token 列表从 7 变 8（浏览器实点） |
| `client_package` | `zip_builder=pure_php`，811988 字节；`unzip -t` 四个文件全 OK，Python `zipfile.testzip()` 返回 `None` |
| `exec` / `search` / `shell_run` | 全部 `ok=true`（此前缺 mbstring 时是 1099） |
| `webhook_test` | `http_code=405`、`ok=true`、`transport=stream`、`degraded=true` |
| 全量 sweep（降级模式） | 81 次调用 74 成功 / 7 失败 —— 与全扩展模式**完全一致**（6 条是主动断言的错误码，1 条是 `error_log` 无日志源） |
| 全量 sweep（正常模式） | 同上，`ping` 的 `deployment_warning_count=0` |
| 1099 根因回传 | 故意让服务端抛 `Error("Call to undefined function mb_substr()")`：admin 收到 `exception` + `exception_at=sync_api.php:…` + `exception_hint="缺少 PHP 扩展 mbstring…"`；普通 token 只收到 `exception_class` |

结论：**扩展缺失从"功能不可用且不可诊断"变成"功能降级可跑 + 面板明示 + 1099 自带根因"**。
相关：[AI-USAGE.md](AI-USAGE.md)（一条链接的接入法）、[ADMIN.md](ADMIN.md)（面板逐模块）、[DEPLOYMENT.md](DEPLOYMENT.md)（部署与前置）。
