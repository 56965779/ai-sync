#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ai-sync 契约一致性检查（CI 与本地共用，零依赖）。

检查项
------
1. 双服务端 action 契约等价：sync_api.php 的 $GLOBALS['HELP_SPEC'] 与
   sync_api.py 的 HELP_SPEC 的 action 名集合必须完全相同。
   这是本项目的核心承诺（README / docs/API.md 都写「67 个 action」），
   任何一端漏加一个 action，客户端切服务端实现就会静默 404。
2. action 数量与文档声明一致：README 里写的「N 个 action」必须等于实际数量。
3. 版本号一致：sync.py 的 APP_VERSION、sync_config.example.json 的 version
   必须是同一个版本（服务端从 __DIR__/sync.py 读版本，两处漂移会让
   --check-update 的判断出现歧义）。
4. 敏感文件未被跟踪：sync_config.json / sync.conf 不允许出现在仓库里。

用法
----
    python3 .github/scripts/check_contract.py

退出码 0 表示全部通过；非 0 表示存在契约漂移，输出会指出具体差异。
"""

import ast
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def fail(msg):
    failures.append(msg)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def php_help_spec_keys(src):
    """提取 $GLOBALS['HELP_SPEC'] = [ ... ] 里的一级键名。

    按字符扫描并跳过字符串字面量：HELP_SPEC 的 desc/example 里常出现
    `[file]`、`<URL>` 这类内容，直接数字符会把深度算歪。
    """
    m = re.search(r"\$GLOBALS\['HELP_SPEC'\]\s*=\s*\[", src)
    if not m:
        fail("sync_api.php: 未找到 $GLOBALS['HELP_SPEC'] 定义")
        return []

    # 正则以 `\[` 结尾，m.end()-1 就是赋值语句的开括号；
    # 不能用 src.index('[', m.start()) —— 那会落在 $GLOBALS 的下标括号上。
    i = m.end() - 1
    depth = 0
    keys = []

    while i < len(src):
        ch = src[i]

        if ch in "'\"":
            quote = ch
            j = i + 1
            literal = []
            while j < len(src):
                if src[j] == "\\":          # PHP 转义
                    if j + 1 < len(src):
                        literal.append(src[j + 1])
                    j += 2
                    continue
                if src[j] == quote:         # 字面量结束
                    break
                literal.append(src[j])
                j += 1

            if depth == 1:
                k = j + 1
                while k < len(src) and src[k].isspace():
                    k += 1
                if src[k:k + 2] == "=>":
                    k += 2
                    while k < len(src) and src[k].isspace():
                        k += 1
                    if src[k:k + 1] == "[":
                        name = "".join(literal).strip()
                        if re.fullmatch(r"[a-z_]+", name):
                            keys.append(name)
            i = j + 1
            continue

        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1

    if depth != 0:
        fail("sync_api.php: HELP_SPEC 方括号未闭合，无法解析")
    return keys


def py_help_spec_keys(src):
    """用 ast 取 HELP_SPEC 字典的键，避免 exec 整个服务端模块。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        fail("sync_api.py: 语法错误，无法解析 HELP_SPEC (%s)" % e)
        return []

    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "HELP_SPEC" in names and isinstance(node.value, ast.Dict):
                return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    fail("sync_api.py: 未找到 HELP_SPEC 字典")
    return []


def check_action_parity():
    php = php_help_spec_keys(read("sync_api.php"))
    py = py_help_spec_keys(read("sync_api.py"))

    for name, keys in (("sync_api.php", php), ("sync_api.py", py)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            fail("%s: HELP_SPEC 存在重复 action: %s" % (name, ", ".join(dupes)))

    only_php = sorted(set(php) - set(py))
    only_py = sorted(set(py) - set(php))
    if only_php:
        fail("sync_api.php 有、sync_api.py 缺的 action: %s" % ", ".join(only_php))
    if only_py:
        fail("sync_api.py 有、sync_api.php 缺的 action: %s" % ", ".join(only_py))

    print("action 契约: php=%d, py=%d, 交集=%d" % (len(php), len(py), len(set(php) & set(py))))

    # 文档声明数量
    readme = read("README.md")
    m = re.search(r"(\d+)\s*个\s*action", readme)
    if m:
        declared = int(m.group(1))
        actual = len(set(php) & set(py))
        if declared != actual:
            fail("README 声明 %d 个 action，实际双端共同实现 %d 个" % (declared, actual))
        else:
            print("README 声明的 action 数量与实现一致: %d" % actual)
    else:
        fail("README.md: 未找到「N 个 action」的数量声明")

    # API 文档里的数量声明
    api_doc = read("docs/API.md")
    m = re.search(r"(\d+)\s*个\s*action", api_doc)
    if m and int(m.group(1)) != len(set(php) & set(py)):
        fail("docs/API.md 声明 %s 个 action，与实现不一致" % m.group(1))


def check_version():
    version_m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', read("sync.py"))
    if not version_m:
        fail("sync.py: 未找到 APP_VERSION")
        return
    app_version = version_m.group(1)
    conf_version = json.loads(read("sync_config.example.json"))["version"]
    if app_version.lstrip("v") != conf_version.lstrip("v"):
        fail(
            "版本漂移: sync.py APP_VERSION=%s, sync_config.example.json version=%s"
            % (app_version, conf_version)
        )
    else:
        print("版本号一致: %s" % app_version)


def check_no_secrets_tracked():
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as e:
        print("跳过 git 跟踪检查（非 git 环境或 git 不可用: %s）" % e)
        return

    banned = {"sync_config.json", "sync.conf"}
    hit = [f for f in tracked if os.path.basename(f) in banned]
    if hit:
        fail("含明文 token 的配置文件已被 git 跟踪: %s" % ", ".join(hit))
    else:
        print("敏感文件未被跟踪: 通过")


def main():
    check_action_parity()
    check_version()
    check_no_secrets_tracked()

    if failures:
        print("\n契约检查未通过：", file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 1

    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
