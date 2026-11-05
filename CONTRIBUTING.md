# 贡献指南

欢迎提交 issue 和 PR。这个项目面向 AI Agent 调用，**任何改变输出结构或退出码语义的改动都需要
连同契约测试一起提交** —— 下游是脚本，不是人眼。

---

## 1. 先理解不能动的东西

仓库看起来是「平铺」的，但这是运行时约束，不是没整理：

| 文件 | 为什么必须同目录 |
|---|---|
| `sync_api.php` + `sync.py` | `_client_meta()` 用 `__DIR__ . '/sync.py'` 取 MD5 供 `whoami.client_md5`，`action_client_download()` 用 `readfile(__DIR__ . '/sync.py')` 分发客户端。分开就断掉全网自动升级。 |
| `sync_api.php` + `tools/` | `ensure_tool_loaded()` 只找 `__DIR__ . '/tools/<name>.php'` 或 `<WORK_DIR>/tools/`。 |
| `sync_api.php` + `sync_config.json` | 默认配置路径是脚本同目录（可用 `AI_SYNC_CONFIG_FILE` 改）。 |
| `sync_api.py` + `sync.py` | `_client_sync_path()` 用脚本同目录的 `sync.py` 做 Python 端自动升级。 |

**所以不要把它重构为 `client/` + `server/php/` 分层**，除非同时改两端的路径解析并更新所有部署文档。
`admin.html` 是纯静态页面，可以单独托管。

---

## 2. 本地开发环境

```bash
git clone https://github.com/56965779/ai-sync.git
cd ai-sync
python3 --version          # 需要 3.8+，实测 3.13.14
```

**永远不要拿真实服务器做测试。** 推荐三层隔离：

```bash
mkdir -p /tmp/ai-sync-dev/work
cp sync.conf.example /tmp/ai-sync-dev/sync.conf
# 在 sync.conf 里指向你自己的测试服务端，并加：
#   AUTO_UPGRADE = false
# 这条很关键：否则 _auto_upgrade() 会把你的测试副本静默换成服务端分发版。
```

改动后至少跑：

```bash
python3 -m py_compile sync.py sync_api.py
python3 sync.py --conf /tmp/ai-sync-dev/sync.conf --no-auto-upgrade --version
python3 sync.py --conf /tmp/ai-sync-dev/sync.conf --doctor --brief --no-auto-upgrade
```

> 小坑：`--no-auto-upgrade` 要放在命令后面（本仓库已把该 flag 加入 `command` 识别白名单，
> 但老版本客户端会因参数顺序把顶层 `command` 写成 `no-auto-upgrade`）。

---

## 3. 改动契约必须自检

任何涉及输出的改动，请逐项确认：

- [ ] 默认输出仍是**一行合法 JSON**，顶层有 `ok / code / stdout / stderr / command`。
- [ ] `ok` 只表示「工具执行成功」；环境结论放在 `health` / `errors` / `status` 等独立字段。
- [ ] 退出码语义不变：`0` 成功、`1` 不一致/环境、`2` 参数/文件错误、`3` 远程命令失败、
      `4` 部署/回滚失败、`99` 策略阻断。
- [ ] 连续执行同一只读命令，结论稳定（诊断不能改变被诊断对象）。
- [ ] 新字段是**追加**的，不删除既有字段，避免破坏已有消费者。
- [ ] 老服务端缺少新 action 时能优雅降级（用 `whoami.available_actions` 门控，不要盲目试探 ——
      探测未知 action 本身会写一条非零审计）。
- [ ] 不硬编码路径、挂载点、命令可用性；采集失败要显式 `unavailable`，不能静默留空还报 `ok`。
- [ ] 批量命令默认摘要化，明细靠 `-v/--verbose` 开启（AI 上下文是有成本的）。

---

## 4. 提交 PR 前的检查清单

```bash
# 1) 语法
python3 -m py_compile sync.py sync_api.py
php -l sync_api.php                 # 有 PHP 环境时
for f in tools/*.php; do php -l "$f"; done

# 2) 契约冒烟（对每个改动过的命令）
python3 sync.py --conf <测试配置> --version --json
python3 sync.py --conf <测试配置> --doctor --brief --json
python3 sync.py --conf <测试配置> --health --json
python3 sync.py --conf <测试配置> --check-update --json

# 3) 确认没有把凭据带进来
git status --short
git diff --stat
```

- [ ] 没有提交 `sync_config.json` / `sync.conf` / `*.key` / `*.zip` / `.sync_*`（`.gitignore` 已覆盖）。
- [ ] 文档里的命令名与 `sync.py` 的 `add_argument` 实际定义一致。
- [ ] README / docs 里的路径真实存在（本项目曾因 README 指向不存在的目录被实测打脸）。
- [ ] 新增行为写进了 `CHANGELOG.md`。
- [ ] 涉及安全边界的改动同步更新 `SECURITY.md` / `docs/THREAT_MODEL.md`。

---

## 5. 提交信息

用简洁的一句话说明「改了什么契约、为什么」，例如：

```
fix(health): 按 work_dir 取挂载点，避免 Termux 下误报 100% 磁盘
docs: 补齐下载/部署/使用全链路，全部命令经实测
```

## 6. 行为准则

- 不要提交任何真实 token、生产域名、客户数据；示例一律用 `CHANGE_ME_*` / `your-server.example`。
- 不要把「环境差异」当成「环境问题」报废给调用方；拿不到数据就明确 `unavailable`。
- 安全相关的改动请先开 issue 私下沟通，不要在公开 PR 里贴可直接利用的细节。

## 7. 许可证

贡献即表示你同意以 [MIT](LICENSE) 许可发布你的代码。新文件请带
`SPDX-License-Identifier: MIT` 头（PHP 用 `//`，Python 用 `#`，HTML 用 `<!-- -->`）。
