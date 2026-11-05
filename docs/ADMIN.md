# 管理面板 admin.html

> **覆盖边界**：`admin.html` 是仓库根目录的**纯静态单页**，无需构建、无需后端托管。
> 本轮实测覆盖了「服务端 API 全链路 + 客户端 CLI」，**没有**在真实浏览器里跑完整登录与
> 面板交互流程。下面的字段与模块清单来自 `admin.html` 源码，操作路径以源码为准。

---

## 1. 打开方式

`admin.html` 与 `sync_api.php` 放同一 Web 目录时，直接浏览器访问：

```
https://your-server.example/path/admin.html
```

也可以本地文件方式打开（`file://`），此时**必须**在登录框里手动填 `API URL`
（否则默认请求同目录的 `/sync_api.php`，会失败）。跨域时服务端已回显 `Origin` 处理 CORS，
但建议同源托管最省事。

## 2. 登录

源码里登录框两个输入：

| 字段 | 说明 |
|---|---|
| `admin_token` | **管理员令牌**，密码框。普通 scope token 权限不足，很多面板功能会 403 |
| `API URL` | 可选，默认同目录 `/sync_api.php`；填完整 URL 到 `sync_api.php` |

登录成功后 token 与 URL 会存进浏览器 `localStorage`（键
`ai_sync_admin_token` / `ai_sync_api_url`）。

> ⚠️ 这意味着 **token 以明文留在浏览器 localStorage**。只在可信设备上用管理员面板，
> 用完点退出（源码里有 `localStorage.removeItem`）。不要在公用电脑登录。
> 更根本的做法是别让 `sync_config.json` 可被静态下载 —— 见 [SECURITY.md](../SECURITY.md)。

## 3. 面板模块（源码 `h2` 清单）

| 分组 | 模块 |
|---|---|
| 概览 | 服务器状态、当前身份与能力、API 调用分布、管理统计、最近文件变更 |
| 授权 | 添加目录授权、已授权目录、添加 Token、已授权 Token |
| 文件 | 文件浏览、全文搜索、TODO/FIXME 扫描、代码统计、语法检查 / 批量语法检查、代码格式化、API 文档生成、保存代码片段 |
| 回滚 | 批量回滚、部署批次回滚、备份管理 |
| 追溯 | 审计日志、客户端操作日志 |
| 执行 | 远程命令执行、执行 PHP 脚本 |

## 4. 推荐配置顺序

1. 登录（`admin_token`）。
2. **添加目录授权**：填 scope 名 + 绝对 `work_dir`，这就是所有文件操作的根目录。
3. **添加 Token**：为客户端单独建一个 token，只勾 `read/write/delete`，
   **不要把 `admin_token` 下发给客户端**；需要 `exec` 时再单独授予并配 IP 白名单。
4. **打包客户端**：生成 zip（内含预置配置的 `sync.py` / `sync.conf` / `README` / `start.sh`），
   分发到各个设备。
5. 在「服务器状态」里确认 `deployment_warnings` 为空。

> 客户端安装包 zip 内含**明文 token**，生成目录不要在 Web 根内（服务端自检会报
> `packages_in_webroot`），下载后及时删除服务器上的副本。

## 5. 与 CLI 的关系

面板只是对同一套 67 个 action 的可视化封装，底层协议与 `sync.py` 完全一致：

| 面板操作 | 等价 CLI / action |
|---|---|
| 文件浏览 | `list` / `stat` / `download` |
| 备份管理 / 回滚 | `backup_ls` / `backup_rb` / `batch_rollback`、`--rollback` |
| 审计日志 | `audit_log`、`--error-log audit` |
| 远程命令执行 | `exec`、`--exec` |
| 语法检查 | `lint` / `lint_batch`、`--lint-first` |
| 客户端打包 | `client_package` |

CLI 的默认输出是 JSON，适合 Agent；面板适合人工巡检。两者可以混用，服务端状态是同一份。

## 6. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 登录后大面积 403 | 用的是普通 token；改用 `admin_token`，或检查 `config_get` 里的权限位 |
| `file://` 打开后所有请求失败 | 填完整 `API URL`；注意 CORS 与 HTTPS 混合内容限制 |
| 面板能看但打包下载失败 | 检查 `package_dir` 是否可写、是否在 Web 根内 |
| 看不到 `deployment_warnings` | 用 `{"action":"ping"}` 直接查，或看 CLI `--selfcheck` |

完整部署步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)，安全边界见 [SECURITY.md](../SECURITY.md)。
