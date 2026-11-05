# 提交 Pull Request 前请确认

## 改动类型

- [ ] 修复 bug
- [ ] 新增 action / 能力
- [ ] 文档
- [ ] 重构（未改变输出契约）
- [ ] CI / 仓库维护

## 契约影响（本项目最重要的检查项）

下游是脚本和 AI Agent，不是人眼。请逐条确认：

- [ ] 没有改变 `{ok, code, stdout, stderr, command}` 信封结构
- [ ] 没有改变既有 action 的必填参数或返回字段含义
- [ ] 没有改变退出码语义
- [ ] 新增 action 时，**两端都加了**（`sync_api.php` 的 `$GLOBALS['HELP_SPEC']` 与 `sync_api.py` 的 `HELP_SPEC` 同名同键）
- [ ] `python3 .github/scripts/check_contract.py` 通过（双端 67 个 action 集合一致）

> 如果确实必须做破坏性变更：请在描述里写清影响版本、迁移方法，并在 `CHANGELOG.md` 里标 `BREAKING`。

## 已跑的验证

```bash
python3 -m py_compile sync.py sync_api.py
php -l sync_api.php          # 改了 PHP 就贴这一行
python3 .github/scripts/check_contract.py
```

- [ ] 贴出以上命令的实际输出（或说明哪条没跑、为什么）

## 其他

- [ ] 未提交任何真实 token / 生产地址（`sync_config.json`、`sync.conf` 已在 `.gitignore` 中）
- [ ] 涉及行为变化已更新 `README.md` / `docs/` 对应段落
