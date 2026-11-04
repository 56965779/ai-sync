#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 金鑫

"""
ai-sync v4.18 —— 本地 workspace ↔ 远程服务器 双向同步客户端
零依赖、纯标准库、通过 api.php HTTP API 操作远程文件

v4.38.1 特性 (2026-10-31) [安全与一致性修复, 基于 v4.38]:
  * FIX-conf-persist 配置回写不再静默失败 —— re.sub 替换体改 lambda (Windows 路径里的
    反斜杠会被当成正则转义抛错), 失败时打印告警; POSIX 下顺带把 sync.conf 收紧到 0600
  * FIX-batch-workdir: --batch 向下发 AI_SYNC_WORKDIR, 子命令不再丢外层 --workdir
  * SEC-tls-downgrade: 证书校验失败只对本次运行降级, 不再把 VERIFY_SSL=false 永久写盘;
    确需长期跳过须显式设 AI_SYNC_INSECURE_TLS=1
  * FIX-deploy-mtime / FIX-upload-mtime: --deploy 与 --upload-file 补发 mtime,
    与 --push 行为对齐 (原先只有 push 保留时间戳)
  * FIX-win-remote-path: --diff <单文件> 用 rsplit("/") 取父目录, 修复 Windows 上
    Path.parent 产出反斜杠路径导致服务端回 not_dir
  * NEW --no-verify: 拉取后可跳过落盘 MD5 回读校验 (大目录省时, 默认仍校验)
  * PERF-diff-md5: 本地 MD5 计算并行化 (>200 文件时走线程池)

v4.38 特性 (2026-10-30) [边界修复: 深目录/文件目标父目录/mtime=0/watch 目录事件]:
  * --pull 目录元数据列举改为不限深度 (list max_depth=0): >10 层目录 mode 与深层空目录不再丢失
  * --push <文件> 同步其父目录链 mode (上传自动建父目录不再固定 0755)
  * 批量上传 mtime=0 (Unix epoch) 不再被当成"未提供"丢弃
  * watch: 新增空目录/chmod 即使无文件事件也会同步; 上传后使父目录缓存失效以回填真实 mode
  * watch two-way: 远程新增空目录与远程目录 mode 变更会拉回本地 (DIR_PULL_INTERVAL 节流;
    本地主动删除的目录不重建; 目录 mode 在文件落盘后回填, 避免只读目录阻塞写入)
  * 服务端契约: chunked/batch_deploy 省略 mode 时保留目标原权限 (与 upload/upload_batch 一致);
    mkdir mode="0000" 正确回退 0755 (原会被误当成 0644); upload_batch errors[] 与 items 等长对齐

v4.37 特性 (2026-10-30) [状态原样: 权限/mtime/目录]:
  * 推送保留 mode (含 0400/0600 只读) 与 mtime, 单文件/批量/分片同一策略;
    setuid/setgid/sticky 按安全策略剥离 (0755/1755 → 0755)
  * 拉取保留文件 mode/mtime (含流式大文件与 chunked), 目录 mode 与空目录一并恢复
  * 目录状态同步: 上传后回填目录权限; --push <目录> 可创建空目录; watch 用目录缓存避免重复请求
  * 仅 chmod 变更也触发推送 (扫描复用快照时 mode 取实时 lstat)
  * symlink mtime 不保留 (协议限制: 创建链接无法设置链接自身时间), 拉取按远端值恢复

v4.18 特性 (2026-10-21) [版本统一 + 管道退出]:
  * FIX-version-v4.18: 唯一版本来源 — APP_VERSION 常量, help-ai/启动日志/概览统一引用
    (修复启动日志/功能概览仍硬编码 v4.15 的遗漏)
  * FIX-watch-pipe: --watch 守护检测到输出管道关闭(BrokenPipeError/EPIPE)时优雅退出,
    不再被顶层兜底吞错导致进程无输出空转 (修复 AI 管道调用 --watch start 卡死场景)

v4.17 特性 (2026-10-18) [AI 契约 + 拉取提速]:
  * --pull 全量拉取提速: 先 checksum 全清单(含隐藏文件) 只下载差异 (v4.17-pullfast)
  * --json 契约: rollback/deploy/API 失败路径也输出纯 JSON; snippets 结构化
  * --deploy 部署后链路验证: nginx -t + reload + HTTP 健康检查 (含 pseudo404 识别)
  * --lint-first: 部署前本地 node --check 预检 JS/TS/HTML
  * --brief: op-log 只输出 total + 最近 3 条 (AI 快速判断)
  * --selfcheck 契约自检精简为 5 核心命令 (status/whoami/check-update/health/ls)

v4.16 特性 (2026-10-18) [增量拉取 + JSON 纯净]:
  * 普通 --pull 也走增量比对 (MD5 相同跳过, 只下载差异) (v4.16-pullskip)
  * --json 模式确认提示走 stderr — 不再混入 stdout 污染 JSON 契约

v4.15 特性 (2026-10-18) [功能去冗余 + AI 决策树]:
  * 合并重复命令 (旧调用全部兼容, 别名保留):
    - --grep-remote → --search --any-ext 的别名 (同源实现, 输出形状不变)
    - --read-range  → --cat --range <start> <end> 的别名 (同源实现, 输出形状不变)
    - --edit-remote → read-remote 别名 (已在 help-ai 标注)
  * --search 新增 --any-ext (不限扩展名搜索) 与 --grep-regex 复用
  * --cat 新增 --range START END 行范围切片 (复用服务端 read_range, 省带宽)
  * --help-ai 新增 decision_tree (场景→命令 决策树), 消除 AI 命令选择成本

v4.14 特性 (2026-10-18) [AI 写代码批量增强]:
  * --cat --json 结构化: {"command":"cat","content":...,"size":...} 直接拿远程内容,
    不再输出裸文本 (二进制时 binary=true + content_b64)
  * --read-remote / --write-remote / --upload-file 支持多文件批量 (nargs="+"):
    一次拉取/回传/直传多个文件, JSON 输出 results[]; 单文件行为与 v4.13 完全一致
  * --write-remote 批量: 逐文件 unchanged/CAS 独立判定, 互不阻塞, results[] 含每文件状态
  * write-remote/upload-file 成功后自动 backup_ls 取最新备份名 (backup_file 字段,
    AI 可直接 --rollback --backup <它> 回滚)

v4.13 特性 (2026-10-18) [AI 使用体验增强]:
  * --upload-file <本地文件>: 本地文件直传远程 (创建新文件场景), 免手动建缓存;
    自动写 read-remote 缓存 + root 保护 + 服务器备份 + lint + 回读验证, 支持 --dry-run
  * --grep-remote <pattern>: 远程任意文件内容检索 (支持 *.html 等非默认扩展名,
    不受 SEARCH_FILE_PATTERN 限制; 默认搜 "." 全目录, --grep-path 指定目录/文件)
  * --write-remote 增强: 输出 base_md5/缓存路径/备份名; 支持 --dry-run;
    回传后对 .js/.html 做本地 node --check 语法校验 (无 node 自动跳过)
  * --read-remote --json 输出 next_hint 流程引导
  * --help-ai 修正 write-remote 描述 (实际读缓存回传, 非 stdin), 新增 upload-file/grep-remote 条目
  * 版本统一 v4.13 (文件头/argparse/启动日志/help-ai)

v4.12 特性 (2026-10-18):
  * --watch 增加 choices 校验 (start/status/stop), 非法值直接报错不再误触发守护
  * --help-ai 修正: 补齐遗漏命令/flags, exit_codes 补 return 4, structured_commands 26→32

v4.5 特性 (2026-09-23):
  * --pull-smart 增量拉取: 手动"只拉差异"命令, 不改变原 --pull 全量语义
    流程: 1 次 checksum API 拿远程全部文件 MD5 (不下载内容) → 本地逐文件算 MD5
          → 内容一致的跳过不下载, 只下载远程有变化/本地缺失的文件
    安全: 覆盖前自动备份本地改动; 下载内容做 MD5 校验; 幂等可反复执行
    用法:
      python3 sync.py --conf sync.conf --pull-smart          # 全量比对, 只拉差异
      python3 sync.py --conf sync.conf --pull-smart JK/      # 只比对某子目录
    实测: 3122 文件首次拉取 384 / 跳过 2738; 再次执行拉取 1 / 跳过 3123 (收敛)

v4.4 特性 (2026-09-09):
  * --watch-subdir 子目录级同步: 只监控指定子目录, 所有扫描/推送/拉取限制在该目录内
    用法: python3 sync.py --conf sync.conf --watch start --watch-subdir ai-test/ --watch-mode two-way
    原理: 临时修改 watch_dir 和 remote_base, 纯客户端实现, 服务器端无需修改
  * --watch-mode 命令行优先级修复: 修复配置文件默认 watch_mode="push" 导致命令行
    --watch-mode two-way 被永久忽略的 bug; 现在命令行参数优先于配置文件
  * 拉取调试日志增强: _watch_do_pull 输出远程/本地文件数, 方便排查拉取问题

v4.3 特性:
  * 上传前 MD5 对比: 用 checksum API 批量获取远程 MD5, 相同文件跳过上传
  * 拉取前本地备份: 本地有修改时先备份到 .sync_backup/, 防止覆盖丢失
  * 冲突检测: 本地/远程同时修改时按策略处理 (local/remote/skip/backup_both)
  * 原子写入: 临时文件 + fsync + rename, 中断不损坏
  * 失败重试: 可配置重试次数与退避, 超阈值跳过, 不阻塞队列
  * 快照原子保存 + 清理: 写临时文件再 rename, 旧快照备份, 定期清理残留条目
  * 空目录同步: 扫描空目录, 上传时创建远程目录, 删除同步时清理空目录
  * mtime 和权限保留: 快照记录 mtime/mode, 拉取后恢复 mtime, 上传传 mode
  * 进度报告: 大文件上传进度, 每轮统计摘要, 心跳含当前状态
  * 带宽限制: 分片上传时控制发送间隔
  * 断点恢复: 同步状态保存到 .sync_state.json, 重启时恢复
  * 保留 v3.2 全部能力: 分片上传/全文搜索/语法检查/代码片段/CLI 兼容

快速开始:
  # 1. 启动守护进程 (自动同步本地变更到服务器)
  python3 sync.py --conf sync.conf --watch start

  # 2. 双向同步 (远程变更也自动拉取到本地)
  python3 sync.py --conf sync.conf --watch start --watch-mode two-way

  # 3. 只同步指定子目录
  python3 sync.py --conf sync.conf --watch start --watch-subdir ai-test/ --watch-mode two-way

  # 4. 手动推送/拉取
  python3 sync.py --conf sync.conf --push file1.php file2.php
  python3 sync.py --conf sync.conf --pull               # 全量拉取 (下载所有文件)
  python3 sync.py --conf sync.conf --pull-smart         # 增量拉取 (只下载有差异的, 推荐)
  python3 sync.py --conf sync.conf --pull-smart JK/     # 增量拉取指定子目录

  # 5. 查看状态/停止
  python3 sync.py --conf sync.conf --watch status
  python3 sync.py --conf sync.conf --watch stop

架构: FileLock / Logger / load_conf / ApiClient(不变) /
      Snapshot / LocalBackup / ConflictDetector / SyncState /
      Uploader / TwoWayPuller / SyncEngine / CLI
"""
from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import json
import os
import random
import re
import shutil
import shlex
import signal
import socket
import ssl
import stat
import sys
import threading
import time

# 🆕 FIX-version-v4.18: 唯一版本来源 — help-ai/启动日志/帮助文本统一引用, 不再散落硬编码
APP_VERSION = "v4.39.0"
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Optional

# 🆕 PERF-v4.24 / v4.28: requests 可选 + 懒加载 — keep-alive Session 只对多请求场景
#   (push 多文件/batch/watch) 有收益, 而顶层 import 让每次调用白付 ~40ms (实测
#   importtime: requests=39.7ms, 占模块初始化约 40%)。单请求命令 (--ls/--info/单文件
#   pull) 延迟到首个 HTTP 请求才探测; 未安装 requests 时走 urllib 路径, 行为不变。
_REQUESTS = False  # False=未探测; None=不可用; module 对象=可用


def _load_requests():
    """首次 HTTP 请求时才探测 requests 可用性 (懒加载, 省单请求命令的冷启动)。"""
    global _REQUESTS
    if _REQUESTS is False:
        try:
            import requests as _r
            _REQUESTS = _r
        except Exception:
            _REQUESTS = None
    return _REQUESTS


_HTTP_SEQ = 0


def _use_requests_fastpath(url: str) -> bool:
    """决定本请求是否走 requests keep-alive 快路径 (v4.28 懒加载策略)。

    - https 首请求即启用: TLS 握手摊销收益大 (实测 0.27s → 0.096s/请求);
    - http (通常本机/内网, 握手 <1ms) 首请求走 urllib, 省 ~40ms requests
      模块导入 — 单请求命令 (--ls/--info/单文件 pull) 是 AI 会话高频操作;
    - 同进程第 2 个请求起恒启用 keep-alive (push 多文件/batch/watch 场景)。
    严格不劣于 v4.27 的"一律顶层 import + 一律 requests"行为。"""
    global _HTTP_SEQ
    _HTTP_SEQ += 1
    if _HTTP_SEQ > 1:
        return _load_requests() is not None
    if url.startswith("https://"):
        return _load_requests() is not None
    return False

_TLS = threading.local()


def _get_session():
    """获取当前线程的 requests.Session (keep-alive)。每线程一个, 避免共享连接竞争。"""
    if _load_requests() is None:
        return None
    sess = getattr(_TLS, "session", None)
    if sess is None:
        sess = _REQUESTS.Session()
        try:
            from requests.adapters import HTTPAdapter
            _ad = HTTPAdapter(pool_connections=2, pool_maxsize=16, max_retries=0)
            sess.mount("https://", _ad)
            sess.mount("http://", _ad)
        except Exception:
            pass
        _TLS.session = sess
    return sess


def _reset_session():
    """丢弃当前线程 Session, 下次请求重新建连 (仅异常后调用)。"""
    sess = getattr(_TLS, "session", None)
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
        _TLS.session = None

# ---------------------------------------------------------------------------
# 1. 跨平台文件锁
# ---------------------------------------------------------------------------
class FileLock:
    """跨平台单实例锁。"""
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fp = None
        self._impl = None

    def acquire(self, blocking: bool = True) -> bool:
        """获取锁。blocking=False 时非阻塞, 拿不到立即返回 False。
        BUG-FIX: open 模式用 'a+' (不截断文件内容), 避免 flock 被占时 fallback
        读取 pid 读到被自己截断的空文件 → 误判锁可用并删除锁文件 (导致并发双写)."""
        try:
            import fcntl
            self._fp = open(self.path, "a+")
            flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(self._fp, flags)
            self._fp.seek(0)
            self._fp.truncate()
            self._fp.write(f"pid={os.getpid()} t={int(time.time())}\n")
            self._fp.flush()
            self._impl = "fcntl"
            return True
        except (ImportError, OSError):
            if self._fp:
                try: self._fp.close()
                except Exception: pass
                self._fp = None
        try:
            import msvcrt
            self._fp = open(self.path, "a+")
            self._fp.seek(0)
            lock_flag = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(self._fp.fileno(), lock_flag, 1)
            self._fp.seek(0)
            self._fp.truncate()
            self._fp.write(f"pid={os.getpid()} t={int(time.time())}\n")
            self._fp.flush()
            self._impl = "msvcrt"
            return True
        except (ImportError, OSError):
            if self._fp:
                try: self._fp.close()
                except Exception: pass
                self._fp = None
        # fallback: pid file
        if self.path.exists():
            try:
                # BUG-35 修复 (审计 Finding 12): 缺 encoding= 在 C locale 容器里 UnicodeDecodeError.
                old = int(self.path.read_text(encoding="utf-8").split("pid=")[1].split()[0])
                try:
                    os.kill(old, 0)
                    return False
                except (OSError, ProcessLookupError):
                    try: self.path.unlink()
                    except Exception: pass
            except Exception:
                try: self.path.unlink()
                except Exception: pass
        if blocking:
            # BUG-36 修复 (审计 Finding 1): pid 回退模式下 blocking=True 原本是 no-op,
            # 两个 --watch 守护可同时拿到锁 → 快照损坏. 改为有限重试等待.
            # 仍在 N 次内拿不到则返回 False (调用方决定退出或重试).
            deadline = time.time() + 30.0
            while time.time() < deadline:
                # 重新检查锁文件是否被释放
                if self.path.exists():
                    try:
                        old = int(self.path.read_text(encoding="utf-8").split("pid=")[1].split()[0])
                        try:
                            os.kill(old, 0)
                            time.sleep(0.5)
                            continue  # 仍被持有, 等待
                        except (OSError, ProcessLookupError):
                            try: self.path.unlink()
                            except Exception: pass
                    except Exception:
                        try: self.path.unlink()
                        except Exception: pass
                break  # 锁文件不存在或已清理, 跳出获取
        self._fp = open(self.path, "w", encoding="utf-8")
        self._fp.write(f"pid={os.getpid()} t={int(time.time())}\n")
        self._fp.flush()
        self._impl = "pid"
        return True

    def release(self):
        if self._impl == "fcntl" and self._fp:
            try:
                import fcntl
                fcntl.flock(self._fp, fcntl.LOCK_UN)
                self._fp.close()
            except Exception: pass
            # BUG-5 修复: fcntl 模式下不能删除锁文件! flock 依赖所有进程锁定同一个 inode.
            # 若释放时 unlink, 下一个 acquire 会创建新 inode, 旧进程仍持锁但新进程对新文件 flock 成功,
            # 单实例保护彻底失效. 锁文件留作下次复用即可.
        elif self._impl == "msvcrt" and self._fp:
            try:
                import msvcrt
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
                self._fp.close()
            except Exception: pass
            # msvcrt 同理不删
        elif self._fp:
            try: self._fp.close()
            except Exception: pass
            # pid 回退模式: 锁文件本身是状态载体, 释放后必须删除 (否则下次 acquire 会读到 stale pid)
            try:
                if self.path.exists(): self.path.unlink()
            except Exception: pass
        self._fp = None
        self._impl = None

# ---------------------------------------------------------------------------
# 2. 日志
# ---------------------------------------------------------------------------
class Logger:
    LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERR": 3}
    def __init__(self, log_file: Path | None, max_mb: int = 10, level: str = "INFO"):
        self.log_file = log_file
        self.max_bytes = max(max_mb, 1) * 1024 * 1024
        self.level = self.LEVELS.get(level.upper(), 1)
        self._fp = None
        self._rotate_if_needed()
        if self.log_file:
            try: self._fp = open(self.log_file, "a", encoding="utf-8", buffering=1)
            except Exception: self._fp = None

    def _rotate_if_needed(self):
        if not self.log_file or not self.log_file.exists(): return
        try:
            if self.log_file.stat().st_size > self.max_bytes:
                old = self.log_file.with_suffix(self.log_file.suffix + ".1")
                if old.exists(): old.unlink()
                self.log_file.rename(old)
        except Exception: pass

    def _emit(self, lvl: str, msg: str):
        if self.LEVELS.get(lvl, 1) < self.level: return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{lvl:4s}] {msg}"
        # FIX-link-log: POSIX 链接目标可能含非 UTF-8 字节 (os.readlink → surrogateescape),
        #   直接 print/write 会抛 UnicodeEncodeError. 统一转成 \udcXX 可读转义, 不丢信息也不中断推送.
        try:
            line.encode("utf-8")
        except UnicodeEncodeError:
            line = line.encode("utf-8", "backslashreplace").decode("utf-8")
        # 🆕 FIX-log-stderr: 日志走 stderr, 不污染 stdout
        #   原因: print() 默认 stdout, 导致 --cat 拉取的文件首行混入 [INFO] 启动日志,
        #         --exec 的 stdout 也被日志污染 (AI 捕获命令输出时不纯净)。
        #   修复: file=sys.stderr, stdout 只剩命令真实输出 (--cat 文件内容 / --exec stdout)。
        try:
            print(line, flush=True, file=sys.stderr)
        except OSError:
            # 🆕 FIX-watch-pipe: 输出流已关闭(如管道下游 head 提前退出/EPIPE) — 静默,
            #    守护循环会自行检测 EPIPE 并优雅退出, 其他命令的日志写入失败也不致命
            pass
        if self._fp:
            try: self._fp.write(line + "\n")
            except Exception: pass

    def info(self, m): self._emit("INFO", m)
    def warn(self, m): self._emit("WARN", m)
    def err(self, m): self._emit("ERR", m)
    def debug(self, m): self._emit("DEBUG", m)
    def close(self):
        if self._fp:
            try: self._fp.flush(); self._fp.close()
            except Exception: pass
            self._fp = None

# ---------------------------------------------------------------------------
# 3. 配置解析
# ---------------------------------------------------------------------------
def load_conf(conf_path: Path) -> dict:
    """解析 sync.conf → dict。支持 [sync]/[section] 分组, # 注释、引号值、空格/逗号分隔列表。
    key 支持大小写和 snake_case 多种写法 (内部统一处理)。"""
    raw: dict[str, str] = {}
    cur_section = ""
    for line in conf_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur_section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            # 静默忽略会让拼写错误 (如 "API_TOKEN abc" / "SKIP_DIRS .git") 悄悄回退默认值,
            # 症状表现为"配置明明写了却不生效". 至少给一行警示, 不影响 --json 的 stdout 契约.
            try:
                print(f"⚠ 忽略无法解析的配置行 (缺少 '='): {line[:80]}", file=sys.stderr)
            except Exception:
                pass
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # 去除行内注释 (引号外的 #)
        if val and '#' in val:
            in_quote = False
            quote_char = ''
            cut = len(val)
            for i, c in enumerate(val):
                if c in ('"', "'"):
                    if in_quote and c == quote_char:
                        in_quote = False
                    elif not in_quote:
                        in_quote = True
                        quote_char = c
                elif c == '#' and not in_quote:
                    cut = i
                    break
            val = val[:cut].strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        raw[key] = val

    def _split_ws(s: str) -> list:
        return [x for x in s.split() if x]

    def _split_list(s: str) -> list:
        """解析列表配置: 兼容 JSON 数组语法 [a, b] / 逗号分隔 a,b / 空白分隔 a b。
        空/[] → []"""
        s = (s or "").strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
            parts = [p.strip().strip('"').strip("'") for p in s.split(",")]
            return [p for p in parts if p]
        if "," in s and " " not in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        if "," in s:
            # 混合 (既有空格又有逗号): 按逗号拆, 每项 strip
            return [p.strip() for p in s.split(",") if p.strip()]
        return _split_ws(s)

    def _split_int_list(s: str) -> list[float]:
        return [float(x) for x in _split_list(s)]

    def _bool(raw: dict, key: str, default: bool) -> bool:
        v = (raw.get(key) or "").strip().lower()
        if not v: return default
        return v in ("true", "1", "yes", "on")

    def _int(raw: dict, key: str, default: int) -> int:
        try: return int((raw.get(key) or "").strip())
        except Exception: return default

    def _float(raw: dict, key: str, default: float) -> float:
        try: return float((raw.get(key) or "").strip())
        except Exception: return default

    ignore = _split_list(raw.get("IGNORE") or raw.get("ignore") or "")
    # 确保内部目录/文件被忽略 (去重)
    # 🆕 FIX-P0-internal: 补 .sync* / .ai-sync-* 通配 — 覆盖 .sync.lock/.ai-sync-watch.*/.sync_history 等
    internal_ignore = [".sync*", ".ai-sync-*", ".ai_sync_*",
                       ".sync_backup", ".sync_backup/**",
                       ".sync_state.json", ".sync_conflicts.json",
                       ".sync_snapshot.json", ".sync_snapshot.json.bak",
                       ".sync_snapshot.json.tmp",
                       "__pycache__", "__pycache__/**", "*.pyc"]
    for pat in internal_ignore:
        if pat not in ignore:
            ignore.append(pat)

    def _g(key: str, default: str = "") -> str:
        """大小写 + snake_case 合并取值。"""
        # 候选: KEY / key / KEY_WITH_UNDER / key-with-dash
        v = raw.get(key) or raw.get(key.lower()) or raw.get(key.upper()) or default
        if v is default:
            alt = key.lower().replace("_", "-")
            v = raw.get(alt) or raw.get(alt.upper()) or default
        return (v or "").strip()

    def _gb(key: str, default: bool) -> bool:
        return _bool(raw, key, _bool(raw, key.lower(), _bool(raw, key.upper(), default)))

    def _gi(key: str, default: int) -> int:
        for k in (key, key.lower(), key.upper()):
            v = (raw.get(k) or "").strip()
            if v:
                try: return int(v)
                except Exception: pass
        return default

    return {
        "api_url": _g("API_URL"),
        "api_token": _g("API_TOKEN") or _g("TOKEN"),
        "watch_dir": _g("WATCH_DIR") or _g("WATCH") or "",
        "remote_base": _g("REMOTE_BASE") or _g("REMOTE"),
        "two_way": _gb("TWO_WAY", False),
        "delete_sync": _gb("DELETE_SYNC", False),
        "json_default": _gb("JSON_DEFAULT", True),
        "auto_upgrade": _gb("AUTO_UPGRADE", True),   # v4.39.2 客户端自动升级开关
        "backup_keep": _gi("BACKUP_KEEP", 5),
        # BUG-24 修复: 用嵌套默认值替代 `A or B`, 避免显式设 0 被当成 falsy 吞掉
        "poll_interval": _float(raw, "POLL_INTERVAL", _float(raw, "poll_interval", 1.5)),
        "debounce_sec": _float(raw, "DEBOUNCE_SEC", _float(raw, "debounce_sec", 0.9)),
        # v4.38: watch 用远程目录列表校准目录缓存的间隔 (秒), 0=关闭
        "dir_reconcile_interval": _float(raw, "DIR_RECONCILE_INTERVAL",
                                          _float(raw, "dir_reconcile_interval", 60.0)),
        # v4.38: two-way watch 拉取远程目录状态 (空目录/mode) 的间隔 (秒), 0=每次 pull 都拉
        "dir_pull_interval": _float(raw, "DIR_PULL_INTERVAL",
                                    _float(raw, "dir_pull_interval", 15.0)),
        # v4.38: two-way watch 空闲时拉取远程变更的间隔 (秒), 0=每轮都拉
        "watch_pull_interval": _float(raw, "WATCH_PULL_INTERVAL",
                                      _float(raw, "watch_pull_interval", 15.0)),
        "batch_size": _gi("BATCH_SIZE", 20),
        "batch_max_kb": _gi("BATCH_MAX_KB", 10),
        "upload_timeout": _gi("UPLOAD_TIMEOUT", _gi("TIMEOUT", 90)),
        # 🆕 FIX-exectimeout-v4.6: --exec/--php_run 超时进配置, 默认 60 (服务端上限 120)
        "exec_timeout": _gi("EXEC_TIMEOUT", 60),
        "retry_backoff": _split_int_list(raw.get("RETRY_BACKOFF") or raw.get("retry_backoff") or "1.2 5 12"),
        "twoway_scan_interval": _float(raw, "TWOWAY_SCAN_INTERVAL", _float(raw, "twoway_scan_interval", 60)),
        "twoway_scan_batch": _gi("TWOWAY_SCAN_BATCH", 20),
        "ignore": ignore,
        "allow_ext": _split_list(_g("ALLOW_EXT")),
        "log_file": _g("LOG_FILE") or "./.sync.log",
        "log_level": (_g("LOG_LEVEL") or _g("LEVEL") or "info"),
        "log_max_mb": _gi("LOG_MAX_MB", 10),
        # v3.2
        "chunked_upload_enabled": _gb("CHUNKED_UPLOAD_ENABLED", True),
        "chunked_threshold_kb": _gi("CHUNKED_THRESHOLD_KB", _gi("SIZE_THRESHOLD", 8192)),
        "chunk_size_kb": _gi("CHUNK_SIZE_KB", 4096),
        "search_file_pattern": _split_list(_g("SEARCH_FILE_PATTERN") or "*.php *.py *.js"),
        "search_case_insensitive": _gb("SEARCH_CASE_INSENSITIVE", False),
        "lint_enabled": _gb("LINT_ENABLED", True),
        "lint_fix": _gb("LINT_FIX", False),
        "lint_exts": _split_list(_g("LINT_EXTS") or "php py js"),
        "snippet_auto_sync": _gb("SNIPPET_AUTO_SYNC", False),
        "snippet_local_dir": _g("SNIPPET_LOCAL_DIR"),
        "webhook_enabled": _gb("WEBHOOK_ENABLED", False),
        "webhook_url": _g("WEBHOOK_URL"),
        "webhook_events": _split_list(_g("WEBHOOK_EVENTS") or "upload delete"),
        "verify_ssl": _gb("VERIFY_SSL", True),
        # 🆕 P0: VERIFY_SSL=auto → 启动时先尝试校验, 证书不可信再降级
        "verify_ssl_auto": ((_g("VERIFY_SSL") or "").strip().lower() == "auto"),
        # v4.0 新增
        "conflict_strategy": (_g("CONFLICT_STRATEGY") or _g("CONFLICT") or "backup_both").lower(),
        # 🆕 v4.10-PUSH-DIFF: 推送前基线冲突策略 ask(警告不阻断)/block(阻断)/force(不提示)
        "push_conflict": (_g("PUSH_CONFLICT") or "ask").lower(),
        "local_backup_dir": _g("LOCAL_BACKUP_DIR") or ".sync_backup",
        "local_backup_keep": _gi("LOCAL_BACKUP_KEEP", 5),
        # 🆕 PERF-v4.24: 并发参数此前从未进 conf, 三个调优开关全部失效 (永远用代码里的默认值)。
        #   现服务端 token 限频已放宽到 3000/分钟; 并发 8 ≈ 83 req/s, 仍在 IP 100/s 限额内。
        "pull_concurrency": _gi("PULL_CONCURRENCY", 8),
        "push_concurrency": _gi("PUSH_CONCURRENCY", 4),
        "verify_concurrency": _gi("VERIFY_CONCURRENCY", 12),
        "bandwidth_limit_kb": _gi("BANDWIDTH_LIMIT_KB", 0),
        "snapshot_cleanup_interval": _gi("SNAPSHOT_CLEANUP_INTERVAL", 3600),
        "max_retries": _gi("MAX_RETRIES", 3),
        "fail_threshold": _gi("FAIL_THRESHOLD", 3),
        # v4.1 多目录授权
        "scope": _g("SCOPE"),
        "exec_enabled": _gb("EXEC_ENABLED", False),
        # 🆕 FIX-v4.7: AI 内置配置 (白名单补全; 未知键会被 load_conf 丢弃)
        "auto_yes": _gb("AUTO_YES", False),
        "keep_proxy": _gb("KEEP_PROXY", False),
        # v4.2 守护进程配置
        "watch_interval": _gi("WATCH_INTERVAL", 2),
        "watch_mode": (_g("WATCH_MODE") or "push").lower(),
    }

# ---------------------------------------------------------------------------
# 4. API 客户端 (v3.2 实现, v4.0 保持不变)
# ---------------------------------------------------------------------------
class ApiClient:
    """HTTP API 客户端，支持 v3.2 全部 action + v4.1 多目录授权。"""

    def __init__(self, url: str, token: str, timeout: int, log: Logger, verify_ssl: bool = True,
                 scope: str = ""):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.log = log
        self.verify_ssl = verify_ssl
        self.scope = scope  # v4.1 多目录: 指定操作的 scope, 空=用 token 默认首个
        self._action_handlers: dict[str, Callable] = {}
        self._probe_dir = "ai-sync/probes/"
        self._work_dir = ""
        self.remote_base = ""

    def _req(self, payload: dict) -> dict:
        """发送 JSON 请求。自动注入 token 和 scope。"""
        payload = {**payload, "token": self.token}
        if self.scope and "scope" not in payload:
            payload["scope"] = self.scope
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        ctx = None
        if not self.verify_ssl:
            ctx = urllib.request.ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = urllib.request.ssl.CERT_NONE
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        backoff = [0.5, 2, 5, 15]
        if _use_requests_fastpath(self.url):
            # 🆕 PERF-v4.24: keep-alive 快路径 (实测每请求 0.27s → 0.096s)
            return self._req_requests(payload, headers, backoff)
        for i, delay in enumerate(backoff):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # BUG-28 修复 (审计 Finding 15): 4xx 客户端错误 (除 429 限流外) 不会自愈, 重试只浪费
                # rate-limit 配额 + 让 daemon 卡 22~37s. AI 推 200 文件时一个 400 path 直接卡死.
                # 401/403/404 已短路面; 400/409/422/413 等也立即返回.
                # 仅 429 (Too Many Requests) 和 5xx 走重试 backoff.
                if 400 <= e.code < 500 and e.code != 429:
                    _hint = ""
                    if e.code in (401, 403):
                        _hint = " (下一步: 检查 conf token 权限; 用 --whoami 看当前权限, --admin tokens 管令牌)"
                    elif e.code == 404:
                        _hint = " (下一步: 检查命令/路径; 确认服务器 sync_api.php 在位且可达)"
                    elif e.code == 413:
                        _hint = " (下一步: 内容过大, 改用 --exec-file 通道或拆小)"
                    return {"ok": False, "code": e.code, "error": str(e) + _hint}
                # 反馈: 网络层失败/限流重试 — 告知等待原因与下一次重试延时, 避免 AI 误判卡死
                self.log.info(f"  ⏳ API {e.code} 第 {i+1}/{len(backoff)} 次重试, 等待 {delay}s ...")
                if i < len(backoff) - 1:
                    time.sleep(delay)
                    continue
                # 最后一轮: sleep 后做最后一次尝试 (而非直接放弃)
                time.sleep(delay)
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as e2:
                    return {"ok": False, "code": e2.code, "error": str(e2)}
                except Exception as e2:
                    return {"ok": False, "error": str(e2)}
            except Exception as e:
                # 反馈: 网络异常 (超时/断连) 重试 — 说明当前阶段与重试计划, 避免看似卡死
                self.log.info(f"  ⏳ 网络异常({type(e).__name__}: {str(e)[:60]}) 第 {i+1}/{len(backoff)} 次重试, 等待 {delay}s ...")
                if i < len(backoff) - 1:
                    time.sleep(delay)
                    continue
                time.sleep(delay)
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as e2:
                    return {"ok": False, "error": str(e2)}
        return {"ok": False, "error": "max_retries"}

    def _req_requests(self, payload: dict, headers: dict, backoff: list) -> dict:
        """requests 快路径: 线程内 Session 复用连接, 保留原重试/4xx 短路语义。"""
        if not self.verify_ssl:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        last_err = ""
        attempts = len(backoff) + 1
        for i in range(attempts):
            try:
                resp = _get_session().post(self.url, json=payload, headers=headers,
                                           timeout=self.timeout, verify=self.verify_ssl)
                code = resp.status_code
                if code == 200:
                    return resp.json()
                if 400 <= code < 500 and code != 429:
                    hint = ""
                    if code in (401, 403):
                        hint = " (下一步: 检查 conf token 权限; 用 --whoami 看当前权限, --admin tokens 管令牌)"
                    elif code == 404:
                        hint = " (下一步: 检查命令/路径; 确认服务器 sync_api.php 在位且可达)"
                    elif code == 413:
                        hint = " (下一步: 内容过大, 改用 --exec-file 通道或拆小)"
                    return {"ok": False, "code": code, "error": f"HTTP {code}: {resp.text[:300]}{hint}"}
                last_err = f"HTTP {code}: {resp.text[:200]}"
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:200]}"
                _reset_session()  # 连接可能已坏, 下次重建
            if i < len(backoff):
                self.log.info(f"  ⏳ API 请求失败 ({last_err[:60]}) 第 {i+1}/{attempts} 次重试, "
                              f"等待 {backoff[i]}s ...")
                time.sleep(backoff[i])
        return {"ok": False, "error": last_err or "max_retries"}

    def register_action(self, name: str, handler: Callable):
        """注册自定义 action 处理器。"""
        self._action_handlers[name] = handler

    # ---- 客户端路径安全过滤 (和后端 safe_path 保持一致: 宽松词法 + 提醒 AI 格式) ----
    # 词法层只拦整段 '..' —— 不折腾合法文件名 (a..b / 中文 / %xx / 反斜杠 / \ 全都放行)
    # 真正的安全由后端 safe_path 的 realpath 物理层保证; 这里只是先发前报错, 降低无效请求
    _TRAVERSAL_HINT = (
        "Path must be relative within scope. The only forbidden thing is a literal '..' path segment "
        "(like '../../x' or 'a/../b'). Valid names like 'a..b.php', '..hidden_file', '100%25_done.jpg', "
        "'a\\b.php', Chinese/emoji filenames, leading './' or '//' double slashes are all fine."
    )

    @classmethod
    def safe_rel_path(cls, rel: str, *, allow_empty: bool = False) -> str:
        if not isinstance(rel, str):
            raise ValueError(f"path must be str, got {type(rel).__name__}. {cls._TRAVERSAL_HINT}")
        if rel == "" and allow_empty:
            return ""
        if rel == "":
            raise ValueError(f"path is empty. {cls._TRAVERSAL_HINT}")

        # --- 只做一件事: 段级严格等于 '..' 才拦 ---
        # 把 \ 当普通字符, 统一 / 仅用于段级判断
        for_seg = rel.replace("\\", "/")
        # 合并连续斜杠 / 吃掉 ./ 段, 然后逐段判
        while "//" in for_seg:
            for_seg = for_seg.replace("//", "/")
        segs = []
        for s in for_seg.split("/"):
            if s == "" or s == ".":
                continue
            segs.append(s)
        for s in segs:
            if s == "..":
                raise ValueError(f"path {rel!r} contains '..' segment which is forbidden. {cls._TRAVERSAL_HINT}")

        # --- 规范化输出: 去掉开头 / 和 ./ 前缀 (后端也会再做一遍) ---
        final = rel
        while final.startswith("/"):
            final = final[1:]
        while True:
            if final.startswith("./"):
                final = final[2:]
                while final.startswith("/"):
                    final = final[1:]
                continue
            break
        return final

    def _assert_paths(self, pairs: Iterable[tuple[str, str]]):
        """pairs: [(path_value, field_name)]; 有一个不通过就 raise ValueError"""
        for val, name in pairs:
            # path "." 允许 (表示目录根)
            if isinstance(val, str) and val == ".":
                continue
            self.safe_rel_path(val, allow_empty=False) if isinstance(val, str) else None

    # ---- 基础 action ----
    def ping(self) -> dict:
        r = self._req({"action": "ping"})
        if r.get("ok"):
            self._probe_dir = r.get("probe_dir", "ai-sync/probes/")
            self._work_dir = r.get("work_dir", "")
        return r

    def health(self) -> dict:
        return self._req({"action": "health"})

    def metrics(self) -> dict:
        return self._req({"action": "metrics"})

    @property
    def probe_dir(self) -> str:
        return self._probe_dir

    # ---- v4.1 管理 API ----
    def config_get(self) -> dict:
        return self._req({"action": "config_get"})

    def config_reload(self) -> dict:
        return self._req({"action": "config_reload"})

    def scopes_list(self) -> dict:
        return self._req({"action": "scopes_list"})

    def tokens_list(self) -> dict:
        return self._req({"action": "tokens_list"})

    def token_add(self, new_token: str, name: str, scopes: list, permissions: list) -> dict:
        return self._req({"action": "token_add", "new_token": new_token,
                          "name": name, "scopes": scopes, "permissions": permissions})

    def token_delete(self, target_token: str) -> dict:
        return self._req({"action": "token_delete", "target_token": target_token})

    def scope_add(self, name: str, work_dir: str, **kwargs) -> dict:
        payload = {"action": "scope_add", "name": name, "work_dir": work_dir}
        payload.update(kwargs)
        return self._req(payload)

    def scope_delete(self, name: str) -> dict:
        return self._req({"action": "scope_delete", "name": name})

    def audit_log(self, lines: int = 100, scope: str = "") -> dict:
        payload = {"action": "audit_log", "lines": lines}
        if scope:
            payload["scope"] = scope
        return self._req(payload)

    def admin_stats(self) -> dict:
        return self._req({"action": "admin_stats"})

    def client_package(self, target_token: str, scope: str = "") -> dict:
        """v4.1: 一键打包客户端 (sync.py + sync.conf + README + start.sh)。"""
        payload = {"action": "client_package", "target_token": target_token}
        if scope:
            payload["scope"] = scope
        return self._req(payload)

    # ---- v4.2 AI 增强套件 ----
    def whoami(self) -> dict:
        """v4.2: 查询当前 token 身份/权限/配额 (AI 启动时自查边界)。"""
        return self._req({"action": "whoami"})

    def sync_check(self, files: list) -> dict:
        """v4.2: 一体化同步决策 (一次调用判断该不该推)。
        files: [{path, md5, size, mtime, base_md5?}, ...]
        返回: results [{path, should_push, reason, conflict, ...}], summary
        """
        return self._req({"action": "sync_check", "files": files})

    def lint_batch(self, files: list) -> dict:
        """v4.2: 批量语法检查。files: [path1, path2, ...]"""
        return self._req({"action": "lint_batch", "files": files})

    def recent_changes(self, lines: int = 200) -> dict:
        """v4.2: 服务器端变更历史 (从审计日志提取写操作)。"""
        return self._req({"action": "recent_changes", "lines": lines})

    def batch_rollback(self, before_ts: int, paths: list = None) -> dict:
        """v4.2: 按时间点批量回滚。before_ts: unix 时间戳。
        paths: 可选, 指定文件列表; 默认全部有备份的文件。
        """
        payload = {"action": "batch_rollback", "before_ts": before_ts}
        if paths is not None:
            payload["paths"] = paths
        return self._req(payload)

    def list_since(self, path: str, since_mtime: int, recursive: bool = True) -> dict:
        """v4.2: 只列出 since_mtime 之后变更的文件 (AI 监控远程变更)。"""
        self.safe_rel_path(path, allow_empty=True)
        return self._req({"action": "list", "path": path, "since_mtime": since_mtime, "recursive": recursive})

    def upload_cas(self, path: str, data: bytes, base_md5: str, mode: str = "0644") -> dict:
        """v4.2: 乐观锁上传 (CAS). 远程当前 md5 不等于 base_md5 则拒绝。
        用于多 AI 并发推送防覆盖。
        """
        self.safe_rel_path(path)
        return self._req({
            "action": "upload", "path": path,
            "content_b64": base64.b64encode(data).decode(),
            "md5": hashlib.md5(data).hexdigest(),
            "base_md5": base_md5,
            "mode": mode,
        })

    # ---- 文件 CRUD ----
    def upload_file(self, path: str, data: bytes, mode: str = "", md5: str = "",
                    base_md5: str = "", mtime: float | None = None) -> dict:
        self.safe_rel_path(path)
        payload = {
            "action": "upload",
            "path": path,
            "content_b64": base64.b64encode(data).decode(),
            "md5": md5 or hashlib.md5(data).hexdigest(),
        }
        # mode 未指定时不传: 服务端会保留目标已有权限(新文件默认 0644)。
        # 之前默认写死 "0644", 导致 AI 上传脚本/二进制后丢失可执行位, 还得再 chmod 一次。
        if mode:
            payload["mode"] = mode
        if mtime is not None:
            payload["mtime"] = int(mtime)
        # [AI-SAFE v1.1.2] CAS 乐观锁: 期望远程当前 md5, 不匹配服务器拒绝 (冲突检测)
        if base_md5:
            payload["base_md5"] = base_md5
        return self._req(payload)

    def upload_symlink(self, path: str, link_target: str, base_md5: str = "") -> dict:
        """上传软链接本身 (不传内容): 服务端在 path 处创建/替换为指向 link_target 的链接。"""
        self.safe_rel_path(path)
        wire_target, wire_b64 = _link_target_wire(link_target)
        payload = {
            "action": "upload",
            "path": path,
            "link_target": wire_target,
        }
        if wire_b64:
            payload["link_target_b64"] = wire_b64
        if base_md5:
            payload["base_md5"] = base_md5
        return self._req(payload)

    def upload_batch(self, files: list[dict]) -> dict:
        """批量上传 [{path, content_b64, mode}, ...]"""
        # 先过滤每一个 path
        cleaned: list[dict] = []
        for f in files:
            if not isinstance(f, dict):
                raise ValueError(f"upload_batch item must be dict, got {type(f).__name__}")
            p = f.get("path", "")
            cleaned.append({**f, "path": self.safe_rel_path(p)})
        return self._req({"action": "upload_batch", "files": cleaned})

    def batch_deploy(self, files: list[dict], pre_check: str = "", post_check: str = "",
                     post_check_expect: str = "", rollback_on_fail: bool = True,
                     smart_skip: bool = False) -> dict:
        """原子批量部署: 全部文件先写临时目录, pre_check 通过后原子替换, post_check 验证, 失败自动回滚。
        smart_skip=True 时会在发送前做内容比对，跳过与远程相同的文件（P1-C 智能跳过）。
        """
        # 先对每个 path 做客户端安全校验 (返回规范化 path)
        cleaned: list[dict] = []
        for f in files:
            if not isinstance(f, dict):
                raise ValueError(f"batch_deploy item must be dict, got {type(f).__name__}")
            p = f.get("path", "")
            cleaned.append({**f, "path": self.safe_rel_path(p)})
        files = cleaned
        if smart_skip:
            files = self._smart_filter_files(files)
        # 🆕 FIX-P0-batch: 服务端单次 batch_deploy 上限 50 — 客户端统一分批 (每批 ≤45), 逐批提交汇总.
        #   覆盖所有调用方 (deploy/push/watch/sync), 避免 >50 文件时 too_many_files 整批失败.
        _MAX_BATCH = 45
        if len(files) > _MAX_BATCH:
            _all_ok, _deploy_id, _errs = True, None, []
            for _bi in range(0, len(files), _MAX_BATCH):
                _bf = files[_bi:_bi + _MAX_BATCH]
                _p = {"action": "batch_deploy", "files": _bf, "rollback_on_fail": rollback_on_fail}
                if pre_check: _p["pre_check"] = pre_check
                if post_check: _p["post_check"] = post_check
                if post_check_expect: _p["post_check_expect"] = post_check_expect
                _r = self._req(_p)
                if _r.get("ok"):
                    if _deploy_id is None:
                        _deploy_id = _r.get("deploy_id")
                else:
                    _all_ok = False
                    _errs.append(f"batch{_bi // _MAX_BATCH + 1}: code={_r.get('code')} "
                                 f"{str(_r.get('message') or _r.get('error') or '')[:100]}")
            _out = {"ok": _all_ok, "batched": True, "total": len(files),
                    "batches": (len(files) + _MAX_BATCH - 1) // _MAX_BATCH,
                    "deploy_id": _deploy_id}
            if _errs:
                _out["code"] = 3
                _out["error"] = "; ".join(_errs)
            return _out
        payload: dict = {"action": "batch_deploy", "files": files, "rollback_on_fail": rollback_on_fail}
        if pre_check:
            payload["pre_check"] = pre_check
        if post_check:
            payload["post_check"] = post_check
        if post_check_expect:
            payload["post_check_expect"] = post_check_expect
        return self._req(payload)

    # ---- P1-C 智能跳过: 内容比对跳过未变更文件 ----
    def _smart_filter_files(self, files: list[dict], strategy: str = "auto",
                            remote_base: str = "") -> list[dict]:
        """根据内容/大小/mtime 比对过滤 files，返回需要上传的文件列表。
        strategy:
          "size_mtime" - 快速: 只比对 size+mtime (适合 200 个文件快速决策)
          "md5"        - 强校验: 计算本地 md5 + 拉取远程 md5 全量比对
          "auto"       - 自动: 少于 20 个用 md5，更多用 size_mtime（可对异常 size+mtime 再查 md5）
        返回的 list 会附带 "_skip_reason" 等内部键名，供输出日志。
        """
        if not files: return files
        n = len(files)
        if strategy == "auto":
            strategy = "md5" if n <= 20 else "size_mtime"
        watch_dir = self._watch_dir_path()

        # 远程索引 (size+mtime+md5) - 按需要获取
        remote_index: dict[str, dict] = {}
        if strategy in ("md5", "size_mtime"):
            # 远程路径: 用 remote_base 展开
            base = (remote_base or self.remote_base or "").rstrip("/")
            # list + compute_hash 一次性取所有文件 md5 (checksum 自定义路由)
            r = self.checksum_remote(base or ".", "md5")
            if r.get("ok"):
                for it in r.get("items", []):
                    p = it.get("path", "")
                    remote_index[p] = it
            else:
                # fallback: 用 list 取 size+mtime
                r2 = self.list_remote(base or ".", recursive=True, limit=5000, max_depth=0)
                if r2.get("ok"):
                    for it in r2.get("items", []):
                        p = it.get("path", "")
                        remote_index[p] = it

        # 本地计算
        result: list[dict] = []
        skipped_n = 0
        for f in files:
            rp = f.get("path", "")
            # 归一化
            rp_rel = rp.lstrip("/")
            if base and rp_rel.startswith(base + "/"):
                rp_rel = rp_rel[len(base)+1:]
            remote_it = remote_index.get(rp) or remote_index.get(rp_rel) or None

            data_b64 = f.get("content_b64", "")
            try: data = base64.b64decode(data_b64, validate=False) if data_b64 else b""
            except Exception: data = b""

            # BUG-10 修复 (size_mtime 漏传 mtime + 同尺寸不同内容误跳):
            #   原版 size_mtime 只比 size, size 相同就直接 skip → 改一个变量名 (尺寸不变) 就漏传, 数据丢失!
            #   修复: size 不同 → 必传 (不算 md5, 快速路径); size 相同 → 仍用 md5 校验内容是否真的一致.
            #   这样 size_mtime 仍比纯 md5 快 (不同尺寸的文件跳过 md5 计算), 但不会误跳同尺寸不同内容.
            skip = False
            reason = ""
            if strategy == "size_mtime":
                if remote_it is not None:
                    # BUG-32 修复 (审计 Finding 10): 服务端返回的 size 可能是字符串/"N/A" 等,
                    # int() 直接转 ValueError 会冒泡到 _smart_filter_files, 整批 smart_skip 失效.
                    try:
                        rsize = int(remote_it.get("size") or remote_it.get("length") or -1)
                    except (TypeError, ValueError):
                        rsize = -1
                    if rsize >= 0 and rsize != len(data):
                        pass  # 大小不同, 必须上传, 不需算 md5 (快速路径)
                    elif rsize == len(data) and rsize >= 0:
                        # 大小相同: 不能直接 skip! 降级用 md5 校验内容
                        rmd5 = (remote_it.get("md5") or "").lower()
                        if rmd5 and data:
                            lmd5 = hashlib.md5(data).hexdigest().lower()
                            if lmd5 == rmd5:
                                skip = True
                                reason = f"size+md5_match (size={rsize} md5={lmd5[:8]})"
                            # md5 不同 → 不 skip, 会进入上传
                        # 无远程 md5 或无 data → 不 skip (保守, 宁传不漏)
            if strategy == "md5" and not skip:
                if remote_it is not None and data:
                    rmd5 = (remote_it.get("md5") or "").lower()
                    if rmd5:
                        lmd5 = hashlib.md5(data).hexdigest().lower()
                        if lmd5 == rmd5:
                            skip = True
                            reason = f"md5_match ({lmd5[:8]})"

            if skip:
                skipped_n += 1
            else:
                result.append(f)

        self.log.info(f"[smart] strategy={strategy} n={n} skipped={skipped_n} will_upload={len(result)}")
        return result

    def _watch_dir_path(self) -> Path:
        return Path(getattr(self, "_watch_dir", Path.cwd()))

    # ---- P1-A: error_log 客户端封装 ----
    def error_log_list(self) -> dict:
        return self._req({"action": "error_log_list"})

    def error_log(self, name: str = "php_error", path: str = "", lines: int = 100,
                  grep: str = "", regex: bool = False,
                  since_ts: int = 0, before_ts: int = 0) -> dict:
        payload: dict = {"action": "error_log", "name": name, "lines": lines}
        if path: payload["path"] = path
        if grep: payload["grep"] = grep
        if regex: payload["regex"] = True
        if since_ts: payload["since_ts"] = since_ts
        if before_ts: payload["before_ts"] = before_ts
        return self._req(payload)

    def download_file(self, path: str) -> dict:
        self.safe_rel_path(path)
        return self._req({"action": "download", "path": path})

    # 🆕 v4.39.2: 客户端自动升级 — 下载服务端参考客户端 sync.py 原始字节 (非 JSON 协议)
    def client_download(self, dest: Path) -> dict:
        """下载服务端参考客户端 sync.py (自动升级用).
        响应为原始字节流 (application/octet-stream), X-Client-Md5 头携带服务端 md5.
        返回 {'ok': True, 'md5', 'size'} 或 {'ok': False, 'error'}."""
        payload = {"action": "client_download", "token": self.token}
        if self.scope:
            payload.setdefault("scope", self.scope)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            if self.verify_ssl:
                _ctx = ssl.create_default_context()
            else:
                _ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=self.timeout, context=_ctx) as resp:
                _content = resp.read()
                _md5h = resp.headers.get("X-Client-Md5") or ""
                Path(dest).write_bytes(_content)
                return {"ok": True, "md5": _md5h, "size": len(_content)}
        except Exception as _e:
            return {"ok": False, "error": str(_e)}

    # ---- PERF-v4.25: 大文件流式下载 (协议不变, 客户端不再整包入内存) ----
    _DL_B64_START_RE = re.compile(rb'"content_b64"\s*:\s*"')

    def download_to_file(self, path: str, dest: Path, expect_md5: str = "",
                         chunk_size: int = 1 << 20) -> dict:
        """流式下载远程文件到 dest (临时文件 + 原子替换), 峰值内存 O(块)。

        协议仍是单字段 content_b64 的 JSON, 但客户端不再 `resp.read()` 整包:
        解析出值起点后逐块 base64 解码落盘、边写边算 md5, 服务端发送与客户端落盘同步推进。
        实测 1GB 文件客户端峰值内存从 ~5GB 降到 ~2MB。

        返回 {'ok': True, 'size', 'md5', 'server_md5', 'server_size'}
             或 {'ok': False, 'error'[, 'code']}
        """
        self.safe_rel_path(path)
        payload = {"action": "download", "path": path, "token": self.token}
        if self.scope:
            payload.setdefault("scope", self.scope)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   # 流式路径服务端不做 gzip, 显式声明 identity 防中间层压缩导致长度/解析偏差
                   "Accept-Encoding": "identity"}
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout = max(float(getattr(self, "timeout", 60) or 60), 120.0)
        last_err = ""
        for attempt in range(3):
            tmp = dest.with_name(".dltmp_%d_%d" % (os.getpid(), random.randint(0, 1 << 32)))
            try:
                if _use_requests_fastpath(self.url):
                    resp = _get_session().post(self.url, data=body, headers=headers,
                                               timeout=timeout, stream=True,
                                               verify=self.verify_ssl)
                    try:
                        if resp.status_code != 200:
                            txt = (resp.text or "")[:300]
                            _reset_session()
                            return {"ok": False, "code": resp.status_code,
                                    "error": "HTTP %d: %s" % (resp.status_code, txt)}
                        return self._consume_download(resp.iter_content(chunk_size),
                                                      dest, tmp, expect_md5)
                    finally:
                        try: resp.close()
                        except Exception: pass
                ctx = None
                if not self.verify_ssl:
                    ctx = urllib.request.ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = urllib.request.ssl.CERT_NONE
                req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    it = iter(lambda: resp.read(chunk_size), b"")
                    return self._consume_download(it, dest, tmp, expect_md5)
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, str(e)[:160])
                try:
                    if tmp.exists(): tmp.unlink()
                except Exception:
                    pass
                if _REQUESTS not in (False, None):
                    _reset_session()  # 仅当 requests 已加载才需重置; 未加载时不触发懒探测
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
        return {"ok": False, "error": last_err or "download_failed"}

    def _consume_download(self, it, dest: Path, tmp: Path, expect_md5: str = "") -> dict:
        """消费 download 响应流: 跳过 JSON 头 → 逐块解码 content_b64 落盘 → 校验尾部元信息。"""
        def _next():
            return next(it, None)

        buf = b""
        m = None
        while m is None:
            chunk = _next()
            if not chunk:
                # 流结束都没出现 content_b64: 整个 body 是一个普通 JSON (错误响应/老版本)
                try:
                    obj = json.loads(buf.decode("utf-8", "replace"))
                except Exception:
                    return {"ok": False, "error": "unexpected response: " + buf[:200].decode("utf-8", "replace")}
                if obj.get("ok"):
                    # 老服务端可能返回 data.data.content_b64 之类封装, 交给整包路径兜底
                    return {"ok": False, "error": "no content_b64 field"}
                return {"ok": False, "code": obj.get("code"),
                        "error": obj.get("error") or obj.get("message") or "server error"}
            buf += chunk
            m = self._DL_B64_START_RE.search(buf)
            if m is None and len(buf) > 8192:
                return {"ok": False,
                        "error": "unexpected response head: " + buf[:200].decode("utf-8", "replace")}

        data = buf[m.end():]
        written = 0
        h = hashlib.md5()
        tail = b""
        try:
            fp = open(tmp, "wb")
        except Exception as e:
            return {"ok": False, "error": "cannot write temp file: %s" % e}
        try:
            while True:
                # FIX-json-slash-v4.39.1: 旧服务端 json_encode 未加 JSON_UNESCAPED_SLASHES,
                #   base64 中 '/' 被转义成 '\/' → 破坏 4 对齐 → Incorrect padding.
                #   解码前先还原 (PHP 仅转义 '/', 无其他歧义; 已加 UNESCAPED 的新服务端无副作用).
                data = data.replace(b'\\/', b'/')
                q = data.find(b'"')     # base64 字母表不含 '"', 首个 '"' 即值结束位置
                if q >= 0:
                    head, tail = data[:q], data[q:]
                    break
                cut = len(data) - (len(data) % 4)   # 4 字符对齐, 避免 padding 错位
                if cut:
                    blk = base64.b64decode(data[:cut])
                    fp.write(blk); h.update(blk); written += len(blk)
                    data = data[cut:]
                chunk = _next()
                if not chunk:
                    raise IOError("connection closed in the middle of base64 payload")
                data += chunk
            if head:
                blk = base64.b64decode(head)
                fp.write(blk); h.update(blk); written += len(blk)
            # 尾部元信息很小 (<=200B), 读干净
            while True:
                chunk = _next()
                if not chunk:
                    break
                tail += chunk
                if len(tail) > (1 << 20):
                    break
            fp.flush()
            os.fsync(fp.fileno())
        except Exception:
            try: fp.close()
            except Exception: pass
            try:
                if tmp.exists(): tmp.unlink()
            except Exception: pass
            raise
        finally:
            try: fp.close()
            except Exception: pass

        md5 = h.hexdigest()
        try:
            # tail 以结束引号开头: 补一个占位字符即可还原成合法 JSON
            meta = json.loads('{"content_b64": "x' + tail.decode("utf-8", "replace"))
        except Exception:
            meta = {}
        if meta.get("ok") is False:
            try:
                if tmp.exists(): tmp.unlink()
            except Exception:
                pass
            return {"ok": False, "code": meta.get("code"),
                    "error": meta.get("error") or meta.get("message") or "server error"}
        srv_md5 = (meta.get("md5") or "").lower()
        srv_size = meta.get("size")
        # 流式响应现在带 mode/mtime: 落盘前 chmod, 落盘后 utime, 拉取也原样保留状态。
        # 旧服务端没有这两个字段 → 保留目标已有普通文件的 mode (不被 replace 重置成 0644)。
        srv_mode = meta.get("mode")
        mode_int = None
        if srv_mode not in (None, ""):
            try:
                mode_int = int(str(srv_mode), 8) & 0o777
            except Exception:
                mode_int = None
        srv_mtime = meta.get("mtime")
        try:
            mtime_int = int(srv_mtime) if srv_mtime not in (None, "") else None
        except Exception:
            mtime_int = None
        bad = ""
        if isinstance(srv_size, int) and srv_size != written:
            bad = "size 不一致 (server=%d local=%d)" % (srv_size, written)
        elif srv_md5 and srv_md5 != md5:
            bad = "md5 不一致 (server=%s local=%s)" % (srv_md5[:8], md5[:8])
        elif expect_md5 and expect_md5.lower() != md5:
            bad = "md5 与期望不符 (expect=%s got=%s)" % (expect_md5[:8], md5[:8])
        if bad:
            try:
                if tmp.exists(): tmp.unlink()
            except Exception:
                pass
            return {"ok": False, "error": bad, "size": written, "md5": md5}
        try:
            if mode_int is None:
                # 服务端未给 mode: 保留将被替换的本地普通文件权限
                try:
                    if os.path.isfile(dest) and not os.path.islink(str(dest)):
                        mode_int = os.stat(dest).st_mode & 0o777
                except Exception:
                    mode_int = None
            if mode_int is not None:
                try: os.chmod(tmp, mode_int)
                except Exception: pass
            os.replace(tmp, dest)
            if mtime_int is not None:
                try: os.utime(dest, (mtime_int, mtime_int))
                except Exception: pass
        except Exception as e:
            try:
                if tmp.exists(): tmp.unlink()
            except Exception:
                pass
            return {"ok": False, "error": "replace failed: %s" % e}
        return {"ok": True, "size": written, "md5": md5,
                "server_md5": srv_md5, "server_size": srv_size,
                "mode": mode_int, "mtime": mtime_int}

    def stat_remote(self, path: str) -> dict:
        self.safe_rel_path(path)
        return self._req({"action": "stat", "path": path})

    def list_remote(self, path: str, recursive: bool = False, offset: int = 0, limit: int = 200,
                    include_hidden: bool = False, max_depth: int | None = None) -> dict:
        self.safe_rel_path(path, allow_empty=True)
        # 空字符串代表根目录: safe_path("") 会被后端拒绝, 转成 "." (当前目录, 合法且等价含义)
        if path == "":
            path = "."
        payload = {"action": "list", "path": path, "recursive": recursive,
                   "offset": offset, "limit": limit}
        if include_hidden:
            payload["include_hidden"] = True
        # v4.38: max_depth=0 表示不限深度 (服务端约定); 不传则沿用服务端默认 10 层
        if max_depth is not None:
            payload["max_depth"] = max_depth
        return self._req(payload)

    def delete_remote(self, path: str, recursive: bool = False) -> dict:
        self.safe_rel_path(path)
        payload = {"action": "delete", "path": path}
        if recursive:
            payload["recursive"] = True
        return self._req(payload)

    def rename_remote(self, from_path: str, to_path: str) -> dict:
        self.safe_rel_path(from_path)
        self.safe_rel_path(to_path)
        return self._req({"action": "rename", "from": from_path, "to": to_path})

    def copy_remote(self, from_path: str, to_path: str, overwrite: bool = False) -> dict:
        self.safe_rel_path(from_path)
        self.safe_rel_path(to_path)
        req = {"action": "copy", "from": from_path, "to": to_path}
        if overwrite:
            req["overwrite"] = True
        return self._req(req)

    def mkdir_remote(self, path: str, recursive: bool = True, mode: str = "") -> dict:
        self.safe_rel_path(path)
        payload = {"action": "mkdir", "path": path, "recursive": recursive}
        if mode:
            payload["mode"] = mode
        return self._req(payload)

    def checksum_remote(self, path: str, algo: str = "md5") -> dict:
        self.safe_rel_path(path, allow_empty=True)
        if path == "":
            path = "."
        return self._req({"action": "checksum", "path": path, "algo": algo})

    def diff_remote(self, path_a: str, path_b: str) -> dict:
        self.safe_rel_path(path_a, allow_empty=True)
        self.safe_rel_path(path_b, allow_empty=True)
        if path_a == "": path_a = "."
        if path_b == "": path_b = "."
        return self._req({"action": "diff", "path_a": path_a, "path_b": path_b})

    # ---- 分片上传 (v3.2) ----
    def chunked_init(self, path: str, size: int, md5: str, chunk_size: int,
                     mode: str = "", mtime: float | None = None) -> dict:
        """初始化分片上传。mode/mtime 会随 meta 保存, assemble 落盘后恢复。"""
        self.safe_rel_path(path)
        payload = {
            "action": "chunked_upload",
            "op": "init",
            "path": path,
            "size": size,
            "md5": md5,
            "chunk_size": chunk_size,
        }
        if mode:
            payload["mode"] = mode
        if mtime is not None:
            payload["mtime"] = int(mtime)
        return self._req(payload)

    def chunked_put(self, upload_id: str, chunk_index: int, data: bytes) -> dict:
        """上传分片。"""
        return self._req({
            "action": "chunked_upload",
            "op": "put",
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "content_b64": base64.b64encode(data).decode(),
            "md5": hashlib.md5(data).hexdigest(),
        })

    def chunked_assemble(self, upload_id: str) -> dict:
        """组装分片。"""
        return self._req({"action": "chunked_upload", "op": "assemble", "upload_id": upload_id})

    def chunked_status(self, upload_id: str) -> dict:
        """查询分片状态。"""
        return self._req({"action": "chunked_upload", "op": "status", "upload_id": upload_id})

    def upload_file_chunked(self, path: str, data: bytes, chunk_size: int = 4 * 1024 * 1024) -> dict:
        """使用分片上传大文件。"""
        total_size = len(data)
        total_md5 = hashlib.md5(data).hexdigest()

        # init
        r = self.chunked_init(path, total_size, total_md5, chunk_size)
        if not r.get("ok"):
            return r

        upload_id = r["upload_id"]
        total_chunks = r["total_chunks"]
        # BUG-34 修复 (审计 Finding 8): 服务端返回 total_chunks=0 (空文件路由错误/服务端 bug)
        # 会导致 div-by-zero 或空循环后错误 assemble. 显式校验.
        if not total_chunks or total_chunks <= 0:
            return {"ok": False, "error": f"server returned total_chunks={total_chunks}",
                    "code": "bad_response"}

        # upload chunks
        for i in range(total_chunks):
            start = i * chunk_size
            end = min(start + chunk_size, total_size)
            chunk_data = data[start:end]
            r = self.chunked_put(upload_id, i, chunk_data)
            if not r.get("ok"):
                return r
            self.log.debug(f"chunk {i+1}/{total_chunks} uploaded")

        # assemble
        return self.chunked_assemble(upload_id)

    # ---- 开发辅助 (v3.2) ----
    def search(self, path: str, pattern: str, file_pattern: str = None,
               regex: bool = False, case_insensitive: bool = False) -> dict:
        """全文搜索。"""
        payload = {
            "action": "search",
            "path": path,
            "pattern": pattern,
            "regex": regex,
            "case_insensitive": case_insensitive,
        }
        if file_pattern:
            payload["file_pattern"] = file_pattern
        return self._req(payload)

    def todo(self, path: str, tags: list[str] = None) -> dict:
        """收集代码中的 TODO/FIXME 标记。"""
        return self._req({"action": "todo", "path": path, "tags": tags or ["TODO", "FIXME", "HACK", "XXX"]})

    def stats(self, path: str, group_by: str = "ext") -> dict:
        """代码统计。"""
        return self._req({"action": "stats", "path": path, "group_by": group_by})

    def read_range(self, path: str, start: int, end: int) -> dict:
        """读取文件指定行范围 [start, end]。"""
        self.safe_rel_path(path)
        count = max(1, end - start + 1)
        return self._req({"action": "read_range", "path": path, "start_line": start, "count": count})

    def format_code(self, path: str, dry_run: bool = False) -> dict:
        """格式化代码。"""
        self.safe_rel_path(path)
        return self._req({"action": "format", "path": path, "dry_run": dry_run})

    def lint(self, path: str, fix: bool = False) -> dict:
        """语法检查。"""
        self.safe_rel_path(path, allow_empty=True)
        r = self._req({"action": "lint", "path": path, "fix": fix})
        # 客户端二次清洗: 过滤服务器端老版本 lint 产生的已知误报
        # (php_outside_tags: 模板混写合法, trailing_whitespace: 不影响运行)
        if r.get("issues"):
            before = len(r["issues"])
            r["issues"] = [
                i for i in r["issues"]
                if (i.get("rule") or "") not in ("php_outside_tags", "trailing_whitespace")
            ]
            if len(r["issues"]) != before:
                r["_filter_dropped"] = before - len(r["issues"])
                # 更新 summary
                r["summary"] = self._lint_summary_client_side(r["issues"])
        return r

    @staticmethod
    def _lint_summary_client_side(issues):
        """客户端过滤后重新计算 summary。"""
        s = {"error": 0, "warning": 0, "info": 0, "hint": 0}
        for i in issues:
            sev = (i.get("severity") or "info").lower()
            if sev in s:
                s[sev] += 1
            else:
                s["info"] += 1
        return s

    def doc(self, path: str, format: str = "markdown") -> dict:
        """生成 API 文档。"""
        self.safe_rel_path(path)
        return self._req({"action": "doc", "path": path, "format": format})

    # ---- 代码片段管理 (v3.2) ----
    def snippet_set(self, name: str, content: str, language: str = "",
                    tags: list[str] = None, description: str = "") -> dict:
        """保存代码片段。"""
        return self._req({
            "action": "snippet_set",
            "name": name,
            "content": content,
            "language": language,
            "tags": tags or [],
            "description": description,
        })

    def snippet_get(self, name: str) -> dict:
        """获取代码片段。"""
        return self._req({"action": "snippet_get", "name": name})

    def snippet_list(self, tag: str = None, language: str = None, keyword: str = None) -> dict:
        """列出代码片段。"""
        payload = {"action": "snippet_list"}
        if tag: payload["tag"] = tag
        if language: payload["language"] = language
        if keyword: payload["keyword"] = keyword
        return self._req(payload)

    def snippet_delete(self, name: str) -> dict:
        """删除代码片段。"""
        return self._req({"action": "snippet_delete", "name": name})

    # ---- 备份管理 ----
    # backup_ls / backup_rb 定义见下方 "远程命令" 区 (带 path 参数)

    # ---- Webhook ----
    def webhook_set(self, url: str, events: list[str] = None, secret: str = "") -> dict:
        """设置 Webhook。"""
        return self._req({
            "action": "webhook_set",
            "url": url,
            "events": events or ["upload", "delete"],
            "secret": secret,
        })

    def webhook_list(self) -> dict:
        """列出 Webhook。"""
        return self._req({"action": "webhook_list"})

    def webhook_delete(self, webhook_id: str) -> dict:
        """删除 Webhook。"""
        return self._req({"action": "webhook_delete", "id": webhook_id})

    # ---- 远程命令 ----
    def exec_cmd(self, cmd: str, timeout: int = 10, cwd: str = "") -> dict:
        """执行远程命令（需服务器启用）。cwd 透传服务端原生参数 (相对以 WORK_DIR 为基准,
        目录不存在返回结构化 cwd_not_found, 而非静默失败)。"""
        p = {"action": "exec", "cmd": cmd, "timeout": timeout}
        if cwd:
            p["cwd"] = cwd
        return self._req(p)

    def shell_call(self, action: str, **params) -> dict:
        """调用 shell_* 系列 action (对齐原生 ssh 的持久会话工具)。"""
        payload = {"action": action}
        payload.update({k: v for k, v in params.items() if v is not None})
        return self._req(payload)

    def php_run(self, path: str, timeout: int = 15) -> dict:
        """安全执行工作目录内 PHP 文件 (lint + 沙箱执行)。"""
        self.safe_rel_path(path)
        return self._req({"action": "php_run", "path": path, "timeout": timeout})

    def backup_ls(self, path: str = "") -> dict:
        """列出某文件的历史备份。"""
        self.safe_rel_path(path, allow_empty=True)
        return self._req({"action": "backup_ls", "path": path})

    def backup_rb(self, path: str, backup_file: str) -> dict:
        """从指定备份恢复文件。"""
        self.safe_rel_path(path)
        # AI 修复: 字段名与服务端一致 (backup_file, 此前传 backup 导致服务端收到空值报 bad_backup_file)
        return self._req({"action": "backup_rb", "path": path, "backup_file": backup_file})

# ---------------------------------------------------------------------------
# 5. 辅助函数
# ---------------------------------------------------------------------------
_YES_MODE = False  # main() 中根据 --yes 设置; 危险操作二次确认的全局开关
_ALLOW_DESTRUCTIVE = False  # [AI-SAFE v1.1] 非交互环境「需授权」操作放行开关 (仅用户显式 --allow-destructive)

# 操作日志上下文 (main() 中设置, 入口包装函数读取)
_OPLOG_CTX = {"conf": None, "args": None}
OPLOG_FILE = ".sync_oplog.jsonl"

def _auto_intent(args) -> str:
    """--auto 的意图 (pull/save/fix/status), 无法识别返回空串。"""
    auto = getattr(args, "auto", None)
    if not auto:
        return ""
    try:
        return str(auto[0]).strip().lower()
    except Exception:
        return ""


def _detect_command(args) -> str:
    """检测用户执行了什么操作 (用于操作日志)。
    覆盖全部 CLI 入口 (含 --auto / --api-help / --upload-file / --shell-* 等),
    避免管理面板把大量操作显示成「其他操作」。"""
    checks = [
        ("op_log",       lambda a: getattr(a, "op_log", None) is not None),
        # --auto 意图: 必须排在其他单命令检测之前 (--auto pull 时 args.pull 为空)
        ("auto_pull",    lambda a: _auto_intent(a) == "pull"),
        ("auto_save",    lambda a: _auto_intent(a) == "save"),
        ("auto_fix",     lambda a: _auto_intent(a) == "fix"),
        ("auto_status",  lambda a: _auto_intent(a) == "status"),
        ("auto",         lambda a: bool(getattr(a, "auto", None))),
        ("api_help",     lambda a: getattr(a, "api_help", None) is not None),
        ("help_ai",      lambda a: getattr(a, "help_ai", None) is not None),
        ("exec",         lambda a: bool(a.exec) or bool(a.exec_b64) or bool(getattr(a, "exec_file", None))),
        ("upload",       lambda a: bool(getattr(a, "upload_file", None))),
        ("push",         lambda a: a.push is not None),
        ("pull",         lambda a: a.pull is not None),
        ("pull_smart",   lambda a: getattr(a, "pull_smart", None) is not None),
        ("deploy",       lambda a: a.deploy is not None),
        # --http-check 独立使用时也要有名字 (它没实现独立执行, 但意图要记录, 不能落 other)
        ("http_check",   lambda a: bool(getattr(a, "http_check", ""))),
        ("check_update", lambda a: getattr(a, "check_update", False)),
        ("health",       lambda a: getattr(a, "health", False)),
        ("doctor",       lambda a: getattr(a, "doctor", False)),
        ("fix",          lambda a: getattr(a, "fix", False)),
        ("preflight",    lambda a: bool(getattr(a, "preflight", None))),
        ("self_update",  lambda a: getattr(a, "self_update", False)),
        ("sync_check",   lambda a: bool(getattr(a, "sync_check", None))),
        ("rm",           lambda a: bool(a.rm)),
        ("mv",           lambda a: bool(a.mv)),
        ("cp",           lambda a: bool(a.cp)),
        ("ls",           lambda a: a.ls is not None),
        ("cat",          lambda a: bool(a.cat)),
        ("read_remote",  lambda a: bool(getattr(a, "read_remote", None))),
        ("write_remote", lambda a: bool(getattr(a, "write_remote", None))),
        ("edit_remote",  lambda a: bool(getattr(a, "edit_remote", None))),
        ("replace",      lambda a: bool(getattr(a, "replace", None))),
        ("search",       lambda a: bool(a.search) or bool(getattr(a, "grep_remote", None))),
        ("lint",         lambda a: bool(a.lint)),
        ("lint_batch",   lambda a: bool(a.lint_batch)),
        ("rollback",     lambda a: bool(a.rollback)),
        ("batch_rollback", lambda a: bool(a.batch_rollback)),
        ("verify",       lambda a: a.verify is not None),
        ("diff",         lambda a: a.diff is not None),
        ("sync",         lambda a: bool(a.sync)),
        ("smart",        lambda a: bool(a.smart)),
        ("pipeline",     lambda a: bool(getattr(a, "pipeline", False))),
        ("batch",        lambda a: bool(getattr(a, "batch", False))),
        ("spider",       lambda a: bool(getattr(a, "spider", None))),
        ("watch",        lambda a: a.watch is not None or bool(a.start) or bool(a.stop)),
        ("status",       lambda a: bool(a.status)),
        ("selfcheck",    lambda a: bool(a.selfcheck)),
        ("whoami",       lambda a: bool(a.whoami)),
        ("history",      lambda a: bool(a.history)),
        ("recent_changes", lambda a: a.recent_changes is not None),
        ("list_since",   lambda a: bool(a.list_since)),
        ("error_log",    lambda a: a.error_log is not None),
        ("todo",         lambda a: a.todo is not None),
        ("read_range",   lambda a: bool(a.read_range)),
        ("info",         lambda a: bool(a.info)),
        ("mkdir",        lambda a: bool(a.mkdir)),
        ("test",         lambda a: bool(a.test)),
        ("stats",        lambda a: bool(a.stats)),
        ("snippets",     lambda a: bool(a.snippets)),
        ("backup_list",  lambda a: a.backup_list is not None),
        ("package",      lambda a: bool(getattr(a, "package", None))),
        ("shell_open",   lambda a: getattr(a, "shell_open", None) is not None),
        ("shell_close",  lambda a: getattr(a, "shell_close", None) is not None),
        ("shell_list",   lambda a: bool(getattr(a, "shell_list", False))),
        ("shell_job",    lambda a: bool(getattr(a, "shell_job", None))),
        ("shell_kill",   lambda a: bool(getattr(a, "shell_kill", None))),
        ("shell_bg",     lambda a: bool(getattr(a, "shell_bg", None))),
        ("shell",        lambda a: getattr(a, "shell", None) is not None),
        ("admin",        lambda a: (getattr(a, "admin", None) is not None
                                    or bool(getattr(a, "token_add", None))
                                    or bool(getattr(a, "token_del", None))
                                    or bool(getattr(a, "scope_add", None))
                                    or bool(getattr(a, "scope_del", None)))),
        ("sync_once",    lambda a: bool(a.once)),
    ]
    for name, check in checks:
        try:
            if check(args): return name
        except Exception:
            pass
    return "other"

def _args_summary(args) -> dict:
    """生成参数摘要 (截断防过长, 用于操作日志)。
    🆕 覆盖全部命令入口, 管理面板不再只看到 "other/{}"。"""
    s = {}

    def _clip(v, n=200):
        try:
            if not isinstance(v, str):
                v = str(v)
            return v if len(v) <= n else v[:n] + "…"
        except Exception:
            return ""

    def _files(v, limit=10):
        """把 nargs/append 结构 (含嵌套 list) 展平成字符串路径列表。"""
        if v is None:
            return []
        if not isinstance(v, (list, tuple)):
            v = [v]
        out = []
        for x in list(v)[:limit]:
            if isinstance(x, (list, tuple)):
                if x:
                    out.append(_clip(x[0], 200))
            else:
                out.append(_clip(x, 200))
        return out

    try:
        # --auto 意图
        auto = getattr(args, "auto", None)
        if auto:
            s["intent"] = _clip(auto[0], 32)
            rest = list(auto[1:])
            if rest:
                if str(auto[0]).lower() == "save":
                    s["files"] = _files(rest)
                else:
                    s["target"] = _clip(rest[0], 200)

        if getattr(args, "api_help", None) is not None:
            s["action"] = _clip(getattr(args, "api_help", "") or "*", 64)
        if getattr(args, "help_ai", None) is not None:
            s["topic"] = _clip(getattr(args, "help_ai", "") or "overview", 64)

        # 文件/路径类
        if getattr(args, "upload_file", None):
            s["files"] = _files(args.upload_file)
        if getattr(args, "push", None) is not None:
            _pf = _files(args.push)
            s["files"] = _pf or ["ALL_DIRTY"]
        if getattr(args, "pull", None) is not None:
            s["target"] = "ALL" if args.pull == "__ALL__" else _clip(args.pull, 200)
        if getattr(args, "pull_smart", None) is not None:
            s["target"] = "ALL" if args.pull_smart == "__ALL__" else _clip(args.pull_smart, 200)
        if getattr(args, "deploy", None) is not None:
            _df = _files(args.deploy)
            s["files"] = _df or ["ALL_WORKDIR"]
            if getattr(args, "smart_verify", ""): s["smart_verify"] = _clip(args.smart_verify, 16)
            if getattr(args, "lint_first", False): s["lint_first"] = True
            if getattr(args, "pre_check", ""): s["pre_check"] = _clip(args.pre_check, 60)
            if getattr(args, "post_check", ""): s["post_check"] = _clip(args.post_check, 100)
            if getattr(args, "post_expect", ""): s["post_expect"] = _clip(args.post_expect, 100)
            if getattr(args, "nginx_reload", False): s["nginx_reload"] = True
            if getattr(args, "health_url", ""): s["health_url"] = _clip(args.health_url, 300)
            if getattr(args, "http_check", ""): s["http_check"] = _clip(args.http_check, 300)
            if getattr(args, "expect", ""): s["expect"] = _clip(args.expect, 100)
        if getattr(args, "http_check", "") and getattr(args, "deploy", None) is None:
            # 独立 --http-check (当前版本会报"未指定操作", 仍记录意图与参数, 便于排查)
            s["url"] = _clip(args.http_check, 300)
            if getattr(args, "expect", ""): s["expect"] = _clip(args.expect, 100)
        if getattr(args, "preflight", None):
            s["files"] = _files(args.preflight)
        if getattr(args, "sync_check", None):
            s["files"] = _files(args.sync_check)
        if getattr(args, "read_remote", None):
            s["files"] = _files(args.read_remote)
        if getattr(args, "write_remote", None):
            s["files"] = _files(args.write_remote)
        if getattr(args, "edit_remote", None):
            s["target"] = _clip(args.edit_remote, 200)
        if getattr(args, "replace", None):
            s["target"] = _clip(args.replace, 200)
            s["old_len"] = len(str(getattr(args, "old", "") or ""))
            s["new_len"] = len(str(getattr(args, "new", "") or ""))
            if getattr(args, "regex", False): s["regex"] = True
            if getattr(args, "count", 0): s["count"] = args.count
        if getattr(args, "rm", None):
            s["files"] = _files(args.rm)
        if getattr(args, "mv", None):
            s["from"] = _clip(args.mv[0], 200)
            s["to"] = _clip(args.mv[1], 200)
        if getattr(args, "cp", None):
            s["from"] = _clip(args.cp[0], 200)
            s["to"] = _clip(args.cp[1], 200)
        if getattr(args, "recursive", False):
            s["recursive"] = True
        if getattr(args, "range", None):
            s["range"] = list(args.range)
        if getattr(args, "ls", None) is not None:
            s["path"] = "ALL" if args.ls == "__ALL__" else _clip(args.ls, 200)
        if getattr(args, "diff", None) is not None:
            s["path"] = "ALL" if args.diff == "__ALL__" else _clip(args.diff, 200)
        if getattr(args, "cat", None):
            s["path"] = _clip(args.cat, 200)
        if getattr(args, "info", None):
            s["path"] = _clip(args.info, 200)
        if getattr(args, "mkdir", None):
            s["path"] = _clip(args.mkdir, 200)
        if getattr(args, "todo", None) is not None:
            s["path"] = _clip(args.todo or ".", 200)
        if getattr(args, "stats", None):
            s["path"] = _clip(args.stats, 200)
        if getattr(args, "test", None):
            s["target"] = _clip(args.test, 200)
        if getattr(args, "lint", None):
            s["target"] = _clip(args.lint, 200)
        if getattr(args, "lint_batch", None):
            s["files"] = _files(args.lint_batch)
        if getattr(args, "verify", None) is not None:
            _vf = _files(args.verify)
            s["files"] = _vf or ["ALL"]
        if getattr(args, "smart", None):
            s["files"] = _files(args.smart)
        if getattr(args, "sync", None):
            s["path"] = _clip(args.sync, 200)
        if getattr(args, "backup_list", None) is not None:
            s["target"] = "ALL" if args.backup_list == "__ALL__" else _clip(args.backup_list, 200)

        # 文本搜索
        if getattr(args, "search", None):
            s["pattern"] = _clip(args.search, 100)
            s["path"] = _clip(getattr(args, "grep_path", ".") or ".", 200)
            if getattr(args, "any_ext", False): s["any_ext"] = True
            if getattr(args, "grep_regex", False): s["regex"] = True
        elif getattr(args, "grep_remote", None):
            s["pattern"] = _clip(args.grep_remote, 100)
            s["path"] = _clip(getattr(args, "grep_path", ".") or ".", 200)
            if getattr(args, "grep_regex", False): s["regex"] = True

        # 执行类
        if getattr(args, "exec", None):
            s["cmd"] = _clip(args.exec, 200)
            s["cmd_len"] = len(args.exec)
        if getattr(args, "exec_b64", None):
            try:
                import base64 as _b64
                decoded = _b64.b64decode(args.exec_b64).decode("utf-8", errors="replace")
                s["cmd"] = _clip(decoded, 200)
                s["cmd_len"] = len(decoded)
            except Exception:
                s["exec_b64_len"] = len(args.exec_b64)
        if getattr(args, "exec_file", None):
            try:
                with open(args.exec_file, "r", encoding="utf-8") as _ef:
                    _efc = _ef.read()
                s["cmd"] = _clip(_efc, 200)
                s["cmd_len"] = len(_efc)
                s["exec_file"] = _clip(args.exec_file, 200)
            except Exception:
                s["exec_file"] = _clip(args.exec_file, 200)
        if getattr(args, "shell", None) is not None and args.shell not in ("__STDIN__", ""):
            s["cmd"] = _clip(args.shell, 200)
            s["cmd_len"] = len(args.shell)
        if getattr(args, "shell_open", None) is not None:
            s["session_name"] = _clip(args.shell_open or "default", 64)
        if getattr(args, "shell_close", None) is not None:
            s["session_id"] = _clip(args.shell_close or "default", 64)
        if getattr(args, "shell_job", None):
            s["job_id"] = _clip(args.shell_job, 64)
            if getattr(args, "lines", 0): s["lines"] = args.lines
        if getattr(args, "shell_kill", None):
            s["job_id"] = _clip(args.shell_kill, 64)
        if getattr(args, "shell_bg", None) is not None and args.shell_bg not in ("__STDIN__", ""):
            s["cmd"] = _clip(args.shell_bg, 200)
            s["background"] = True
        if getattr(args, "shell_session", None):
            s["session_id"] = _clip(args.shell_session, 64)
        if getattr(args, "shell_env", None):
            s["env_keys"] = [_clip(str(x).split("=", 1)[0], 64) for x in (args.shell_env or [])[:10]]
        if getattr(args, "cwd", None):
            s["cwd"] = _clip(args.cwd, 200)
        if getattr(args, "batch", False):
            s["mode"] = "stdin_batch"
        if getattr(args, "pipeline", False):
            s["mode"] = "stdin_pipeline"

        # 抓取/管理/守护/日志
        if getattr(args, "spider", None):
            s["url"] = _clip(args.spider, 300)
            s["method"] = _clip(getattr(args, "sp_method", "GET"), 10)
            if getattr(args, "sp_remote", False): s["remote"] = True
            if getattr(args, "via_m3u8", False): s["via_m3u8"] = True
        if getattr(args, "watch", None) is not None or getattr(args, "start", False) or getattr(args, "stop", False):
            s["mode"] = _clip(getattr(args, "watch", "") or ("stop" if getattr(args, "stop", False) else "start"), 16)
            if getattr(args, "watch_interval", None): s["interval"] = args.watch_interval
            if getattr(args, "watch_mode", None): s["watch_mode"] = _clip(args.watch_mode, 16)
            if getattr(args, "watch_subdir", None): s["subdir"] = _clip(args.watch_subdir, 200)
        if getattr(args, "admin", None) is not None:
            s["subcmd"] = _clip(args.admin or "stats", 32)
        elif getattr(args, "token_add", None):
            s["subcmd"] = "token_add"; s["token_len"] = len(str(args.token_add[0]))
            s["name"] = _clip(args.token_add[1], 64)
            s["scopes"] = _clip(args.token_add[2], 100)
            s["perms"] = _clip(args.token_add[3], 100)
        elif getattr(args, "token_del", None):
            s["subcmd"] = "token_del"; s["token_len"] = len(str(args.token_del))
        elif getattr(args, "scope_add", None):
            s["subcmd"] = "scope_add"; s["name"] = _clip(args.scope_add[0], 64)
            if len(args.scope_add) > 1: s["work_dir"] = _clip(args.scope_add[1], 200)
        elif getattr(args, "scope_del", None):
            s["subcmd"] = "scope_del"; s["name"] = _clip(args.scope_del, 64)
        if getattr(args, "package", None):
            s["scope"] = _clip(args.package[1], 64) if len(args.package) > 1 else ""
            s["output_dir"] = _clip(args.package[2], 200) if len(args.package) > 2 else ""
            s["token_len"] = len(str(args.package[0]))
        if getattr(args, "error_log", None) is not None:
            s["name"] = _clip(args.error_log or "list", 64)
            if getattr(args, "error_grep", ""): s["grep"] = _clip(args.error_grep, 80)
            if getattr(args, "error_lines", 0) != 100: s["lines"] = args.error_lines
            if getattr(args, "error_since", 0): s["since"] = args.error_since
        if getattr(args, "recent_changes", None) is not None:
            s["limit"] = args.recent_changes
        if getattr(args, "list_since", None):
            s["since"] = _clip(args.list_since, 32)
        if getattr(args, "rollback", None):
            s["target"] = _clip(args.rollback, 200)
            if getattr(args, "backup", None): s["backup"] = _clip(args.backup, 200)
        if getattr(args, "history", False):
            s["scope"] = "all"
        if getattr(args, "op_log", None) is not None:
            try:
                s["limit"] = int(args.op_log or 20)
            except Exception:
                s["limit"] = _clip(args.op_log, 20)

        # 通用 flag
        if getattr(args, "timeout", None) and args.timeout != 60:
            s["timeout"] = args.timeout
        if getattr(args, "yes", False): s["yes"] = True
        if getattr(args, "dry_run", False): s["dry_run"] = True
        if getattr(args, "plain", False): s["plain"] = True
        if getattr(args, "json", False): s["json"] = True
        if getattr(args, "brief", False): s["brief"] = True
    except Exception:
        pass
    return s

# 命令 → 中文摘要 (客户端本地生成, 服务端/admin 面板无需再维护一份完整映射)
_OPLOG_CMD_CN = {
    "op_log": "查看操作日志",
    "auto": "自动模式", "auto_pull": "自动拉取", "auto_save": "自动保存",
    "auto_fix": "自动排查修复", "auto_status": "自动状态",
    "api_help": "API 帮助", "help_ai": "AI 帮助",
    "exec": "远程执行", "upload": "直传文件", "push": "推送", "pull": "拉取",
    "pull_smart": "智能拉取", "deploy": "部署", "http_check": "HTTP 检查", "check_update": "检查更新",
    "health": "健康检查", "doctor": "环境诊断", "fix": "自动修复",
    "preflight": "上线预检", "self_update": "自更新", "sync_check": "同步检查",
    "rm": "删除", "mv": "移动", "cp": "复制", "ls": "列目录", "cat": "读文件",
    "read_remote": "读取远程文件", "write_remote": "回写远程文件",
    "edit_remote": "编辑远程文件", "replace": "批量替换", "search": "搜索",
    "lint": "语法检查", "lint_batch": "批量语法检查",
    "rollback": "回滚", "batch_rollback": "批量回滚",
    "verify": "校验", "diff": "差异对比", "sync": "单文件同步", "smart": "智能同步",
    "pipeline": "批量管道", "batch": "批量命令", "spider": "抓取 URL",
    "watch": "守护同步", "status": "状态", "selfcheck": "自检", "whoami": "身份查询",
    "history": "变更历史", "recent_changes": "最近变更", "list_since": "增量清单",
    "error_log": "错误日志", "todo": "TODO 扫描", "read_range": "范围读取",
    "info": "路径信息", "mkdir": "建目录", "test": "远程测试", "stats": "统计",
    "snippets": "代码片段", "backup_list": "备份列表", "package": "打包",
    "shell_open": "会话打开", "shell_close": "会话关闭", "shell_list": "会话列表",
    "shell_job": "后台任务输出", "shell_kill": "后台任务终止", "shell_bg": "后台执行",
    "shell": "会话命令", "admin": "管理面板", "sync_once": "单次同步",
}

def _oplog_meta() -> dict:
    """采集客户端元数据 (主机/用户/工作目录/版本), 供服务端与 admin 面板审计。"""
    meta = {"version": APP_VERSION}
    try:
        meta["host"] = socket.gethostname()[:64]
    except Exception:
        meta["host"] = ""
    try:
        import getpass as _getpass
        meta["user"] = (_getpass.getuser() or "")[:64]
    except Exception:
        meta["user"] = str(os.environ.get("USER") or os.environ.get("USERNAME") or "")[:64]
    try:
        meta["cwd"] = os.getcwd()[:300]
    except Exception:
        meta["cwd"] = ""
    return meta

def _oplog_summary(command: str, s: dict) -> str:
    """生成一句话中文摘要 (admin 面板直接显示, 不必再解析 args)。"""
    label = _OPLOG_CMD_CN.get(command, command)
    parts = []
    if command == "auto" and s.get("intent"):
        parts.append(str(s["intent"]))
    if command == "admin" and s.get("subcmd"):
        parts.append(str(s["subcmd"]))
    if command == "watch" and s.get("mode"):
        parts.append(str(s["mode"]))
    files = s.get("files") or []
    if files:
        shown = ", ".join(str(x) for x in files[:3])
        if len(files) > 3:
            shown += f" 等{len(files)}个"
        parts.append(shown)
    elif command == "search" and s.get("pattern"):
        parts.append(str(s["pattern"]))
        if s.get("path") and s["path"] != ".":
            parts.append(f"@ {s['path']}")
    else:
        for k in ("target", "path", "url", "name", "cmd", "job_id",
                  "session_id", "scope", "pattern"):
            if s.get(k):
                parts.append(str(s[k]))
                break
    text = label + (": " + " | ".join(parts) if parts else "")
    return text[:240]

def _upload_oplog_to_server(conf: dict, entry: dict):
    """把操作日志条目上传到服务器 (失败静默跳过, 不影响主流程)
    服务器端 action=oplog_append, 存储在 {WORK_DIR}/.sync_oplog/{client_id}.jsonl
    """
    try:
        import json as _json, urllib.request as _req
        payload = _json.dumps({
            "action": "oplog_append",
            "token": conf.get("api_token", ""),
            "scope": conf.get("scope", ""),
            "entry": entry,
        }, ensure_ascii=False).encode("utf-8", "backslashreplace")
        r = _req.Request(
            conf["api_url"],
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": f"ai-sync-oplog/{APP_VERSION}"},
            method="POST",
        )
        with _req.urlopen(r, timeout=5) as resp:
            resp.read()  # 忽略响应内容
    except Exception:
        pass  # 上传失败不影响主流程 (网络抖动/服务器不可用等)

def _append_oplog(conf: dict, entry: dict, upload: bool = True) -> bool:
    """统一入口: 追加一条操作日志到本地 JSONL, 并推送服务器。
    watch 等内部路径也走这里, 保证 admin 面板能看到守护同步记录。"""
    try:
        p = Path(conf["watch_dir"]) / OPLOG_FILE
        with open(p, "ab") as f:
            # 同 Snapshot: 非 UTF-8 链接目标转成 JSON \udcXX 转义, 保持日志可解析
            f.write((json.dumps(entry, ensure_ascii=False) + "\n")
                    .encode("utf-8", "backslashreplace"))
        if upload:
            _upload_oplog_to_server(conf, entry)
        return True
    except Exception:
        return False

def _record_oplog(conf: dict, args, rc: int, duration_ms: int):
    """记录操作到本地 JSONL 日志并上传服务端 (失败不影响主流程)"""
    try:
        command = _detect_command(args)
        summary_args = _args_summary(args)
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts_epoch": int(time.time()),
            "op_id": f"{int(time.time() * 1000)}-{os.getpid()}-{os.urandom(4).hex()}",
            "command": command,
            "args": summary_args,
            "summary": _oplog_summary(command, summary_args),
            "rc": rc,
            "duration_ms": duration_ms,
        }
        entry.update(_oplog_meta())
        _append_oplog(conf, entry)
    except Exception:
        pass

def cmd_oplog(conf: dict, limit: int = 20, grep_str: str = "", plain: bool = False):
    """查看本地操作日志"""
    import json as _json
    p = Path(conf["watch_dir"]) / OPLOG_FILE
    if not p.is_file():
        print("📋 暂无操作日志 (执行过命令后自动记录)" if not plain else "NO_OPLOG")
        return 0
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in lines:
        if not line.strip():
            continue
        try:
            e = _json.loads(line)
            if grep_str and grep_str.lower() not in _json.dumps(e, ensure_ascii=False).lower():
                continue
            entries.append(e)
        except Exception:
            pass
    entries = entries[-limit:]
    if "--json" in sys.argv:
        # 🆕 v4.12.5-JSON: op-log 结构化 (AI 审计: 刚才干了什么/耗时/结果)
        print(_json.dumps({"command": "op-log", "total": len(lines), "limit": len(entries),
                           "entries": [{"ts": e.get("ts", ""), "command": e.get("command", ""),
                                        "summary": e.get("summary", ""),
                                        "op_id": e.get("op_id", ""),
                                        "rc": e.get("rc", 0), "duration_ms": e.get("duration_ms", 0),
                                        "host": e.get("host", ""), "user": e.get("user", ""),
                                        "cwd": e.get("cwd", ""), "version": e.get("version", ""),
                                        "args": e.get("args", {})} for e in entries]},
                          ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --op-log --plain 无 emoji/装饰线, 每行: ts command rc duration summary
        for e in entries:
            desc = e.get("summary") or _json.dumps(e.get("args", {}), ensure_ascii=False)
            if len(desc) > 65:
                desc = desc[:62] + "..."
            print(f"{e.get('ts','')} {e.get('command','')} rc={e.get('rc',0)} ms={e.get('duration_ms',0)} {desc}")
        return 0
    print(f"\n📋 操作日志 (显示最近 {len(entries)} 条, 共 {len(lines)} 条)\n")
    print(f"{'时间':<20} {'命令':<16} {'结果':<6} {'耗时ms':<8} 详情")
    print("-" * 110)
    for e in entries:
        rc_str = "✅0" if e.get("rc", 0) == 0 else f"❌{e.get('rc')}"
        desc = e.get("summary") or _json.dumps(e.get("args", {}), ensure_ascii=False)
        if len(desc) > 65:
            desc = desc[:62] + "..."
        print(f"{e.get('ts',''):<20} {e.get('command',''):<16} {rc_str:<6} {e.get('duration_ms',0):<8} {desc}")
    return 0

def _confirm_danger(action: str, summary: str, *, irreversible: bool = False,
                    hint_cmd: str = "", target: str = "") -> bool:
    """危险操作二次确认 (AI 视角防误操作).
    - --yes 模式: 直接放行
    - 非交互环境 (stdin 非 tty, 如 AI 调用/管道): 拒绝执行, 提示加 --yes (🆕 FIX-yeshint-v4.6: 有 hint_cmd 时输出真实完整命令, AI 可直接照抄执行)
    - 交互环境: 打印摘要, 要求输入 "yes" 确认
    [AI-SAFE v1.1.2] 非交互下 --allow-destructive 授权支持路径前缀: "*"=全部 / 路径前缀=仅放行该前缀下操作
    返回 True=放行, False=取消/拒绝.
    """
    if _YES_MODE:
        return True
    # 🆕 FIX-json-stdout-v4.16: --json 模式确认提示走 stderr — 否则混入 stdout 污染 JSON 契约
    #   (AI 侧 json.loads(stdout) 失败: "⚠️危险操作...\n{JSON}" 无法解析)
    print(f"\n⚠️  危险操作: {action}", file=sys.stderr if "--json" in sys.argv else None)
    print(f"   {summary}", file=sys.stderr if "--json" in sys.argv else None)
    if irreversible:
        print("   ⛔ 此操作不可逆!", file=sys.stderr if "--json" in sys.argv else None)
    if not sys.stdin.isatty():
        # [AI-SAFE v1.1] 非交互拒绝: 需授权操作在用户显式 --allow-destructive 时放行;
        #   绝对禁止项 (关机/格式化/系统根删除等 exec BLOCK) 不受该开关影响, 永远拒绝。
        # [AI-SAFE v1.1.2] 授权粒度: 无值("*")=本次进程全部放行; 带路径前缀=仅放行 target 在前缀内的操作。
        _ad = _ALLOW_DESTRUCTIVE
        if _ad:
            if _ad == "*":
                return True
            if target and (target.rstrip("/") == _ad.rstrip("/")
                           or target.startswith(_ad.rstrip("/") + "/")):
                return True
        if "--json" in sys.argv:
            # [AI-SAFE v1.1.2] 非交互拒绝结构化输出
            print(json.dumps({"command": action, "blocked": True, "level": "REQUIRE_AUTH",
                              "message": f"非交互环境危险操作已拒绝: {summary}",
                              "category": "confirm_danger", "rule_id": "aisafe-confirm",
                              "target": target, "hint": "用户本人加 --allow-destructive 重试"
                              }, ensure_ascii=False))
        else:
            print(f"\n[BLOCKED] 非交互环境, 危险操作已拒绝执行 (未执行任何操作)")
            print(f"   操作: {action}")
            print(f"   影响: {summary}")
            print(f"   如确需执行, 请由用户本人确认后使用 --allow-destructive 重试 (可带路径前缀仅授权该范围, 该参数应由人工输入)。")
        return False
    try:
        ans = input('\n输入 "yes" 确认执行, 其他任意键取消: ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return False
    if ans == "yes":
        return True
    print("已取消")
    return False
def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def md5_file(p: Path, bufsize: int = 1 << 20) -> str:
    """流式计算文件 MD5 (1MB 分块)。

    PERF-v4.25: 原实现 `p.read_bytes()` 会把整个文件读进内存, pull 后的落盘校验
    (线程池并发 N 个文件) 峰值 = N x 文件大小 → 拉 50MB 文件 RSS 从 36MB 飙到 114MB。
    改为分块 hash 后与文件大小解耦。
    """
    h = hashlib.md5()
    try:
        with open(p, "rb") as f:
            for blk in iter(lambda: f.read(bufsize), b""):
                h.update(blk)
    except Exception:
        return ""
    return h.hexdigest()

def _is_relative_to(p: Path, other: Path) -> bool:
    """BUG-37 修复 (审计 Finding 13): Path.is_relative_to 是 Py3.9+ API.
    老版 Python 部署时 --push <dir> / --diff <dir> 会 AttributeError. 提供兼容回退."""
    try:
        return p.is_relative_to(other)
    except AttributeError:
        try:
            # FIX-symlink-path: 纯词法比较, 不 resolve() — 否则软链接指向目录外时
            # --diff <dir> 会把链接本身误判成"目录内文件"(或反向漏掉), 且链接环会抛异常.
            p = Path(os.path.abspath(str(p))); other = Path(os.path.abspath(str(other)))
            return str(p) == str(other) or str(p).startswith(str(other) + os.sep)
        except Exception:
            return False

def _real_md5(m: dict, local_path: Path | str | None = None) -> str:
    """BUG-8/BUG-9 通用修复: 从文件元数据 dict 取真 md5.
    scan_watch_dir 的 fast 模式 md5="f:size:mtime_ns" 是占位符, 不能用来与远程真 hash 比对.
    命中 "f:" 前缀时重算文件真 md5; 无 local_path 则尝试 m["local"]."""
    raw = m.get("md5", "")
    if m.get("is_symlink"):
        # FIX-symlink: 链接的 md5 是 hash(规范化目标), 绝不能读目标内容.
        return raw if raw and not raw.startswith("f:") else ""
    if raw and not raw.startswith("f:"):
        return raw  # 已是真 md5
    # fake md5 → 重算
    lp = local_path or m.get("local", "")
    if lp:
        try:
            return md5_file(Path(lp))
        except Exception:
            return ""
    return ""

def finger(p: Path, *, mode: str = "fast", max_read_bytes: int = 20_000_000) -> dict:
    """返回文件指纹 dict.
    
    mode="fast" (默认, watch 场景用):
      只做 stat 系统调用 + size/mtime, 不读文件内容 (避免 I/O 风暴/OOM).
      sha1/md5 填占位 "size:mtime_ns" 字符串, 等真正需要时再算.
    mode="hash" (deploy/smart/verify 等需要真 hash 时用):
      读文件算 md5/sha1, 但单文件最多读 max_read_bytes (默认 20MB) 防 OOM.
      超过部分用 "size:mtime_ns" 做后缀, 兼顾完整性/速度/内存.
    """
    try:
        st = p.stat()
        base = {
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mtime_ns": int(st.st_mtime_ns),
            "mode": stat.S_IMODE(st.st_mode),
            "sha1": "",
            "md5": "",
        }
        if mode == "fast":
            # watch 扫描专用: 不读文件, 用 size+mtime_ns 做唯一标识 (快照比对足够)
            tag = f"{st.st_size}:{st.st_mtime_ns}"
            base["sha1"] = "f:" + tag
            base["md5"]  = "f:" + tag
            return base
        # mode == "hash": 读内容算真 hash
        sha1 = hashlib.sha1()
        md5 = hashlib.md5()
        if st.st_size == 0:
            base["sha1"] = sha1.hexdigest()
            base["md5"]  = md5.hexdigest()
            return base
        read_sz = min(st.st_size, max_read_bytes)
        # 流式分块读取: 4MB 一块, 内存上限 ~ 4MB
        CHUNK = 4 * 1024 * 1024
        with open(p, "rb") as f:
            remaining = read_sz
            while remaining > 0:
                sz = CHUNK if remaining >= CHUNK else remaining
                buf = f.read(sz)
                if not buf: break
                sha1.update(buf)
                md5.update(buf)
                remaining -= len(buf)
        base["sha1"] = sha1.hexdigest()
        base["md5"]  = md5.hexdigest()
        # 超大文件: 在 hash 后追加 size, 防止真 hash (只读前 20MB) 碰撞
        if st.st_size > max_read_bytes:
            tag = f":{st.st_size}"
            base["sha1"] += tag
            base["md5"]  += tag
        return base
    except Exception:
        return {"size": -1, "mtime": 0.0, "mtime_ns": 0, "sha1": "", "md5": "", "mode": 0}

def _path_in_base_abs(abs_path: str, base_real: str) -> bool:
    a = os.path.normpath(abs_path).replace("\\", "/").rstrip("/") or "/"
    b = os.path.normpath(base_real).replace("\\", "/").rstrip("/") or "/"
    return a == b or a.startswith(b + "/")


def _lexical_norm_abs_posix(abs_path: str) -> str | None:
    """纯词法归一化 POSIX 绝对路径 (不访问文件系统); 越界返回 None。"""
    p = str(abs_path).replace("\\", "/")
    if not p.startswith("/"):
        return None
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


def _norm_rel_target(raw: str) -> str:
    """归一化"相对链接目标"(相对链接所在目录), 与服务器 _norm_rel_target 一致。

    例: ./a -> a; sub/../a -> a; ../a -> ../a; . -> .
    与同步根无关, 因此客户端/服务端、不同 REMOTE_BASE 下 hash 相同。
    """
    out: list[str] = []
    for seg in str(raw).replace("\\", "/").split("/"):
        if seg == "" or seg == ".":
            continue
        if seg == "..":
            if out and out[-1] != "..":
                out.pop()
            else:
                out.append("..")
            continue
        out.append(seg)
    return "/".join(out) if out else "."


def _remote_target_rel_to_watch(remote_target_rel: str | None, remote_base: str) -> str | None:
    """把服务器 work_dir 相对的链接目标转换为 watch_dir 相对。

    remote_base 为空时两者同根; 非空时只有落在 remote_base 子树内的目标才能本地还原,
    指向 work_dir 其他位置的链接在本地无法表示 → 返回 None (调用方跳过)。
    """
    if not remote_target_rel:
        return None
    rb = (remote_base or "").strip("/")
    if not rb:
        return remote_target_rel
    if remote_target_rel == rb:
        return "."
    if remote_target_rel.startswith(rb + "/"):
        return remote_target_rel[len(rb) + 1:]
    return None


def _remote_link_target(item: dict) -> str | None:
    """从服务器返回的链接条目取 raw target.

    FIX-link-b64: 非 UTF-8 链接目标无法用 JSON 字符串传输, 服务器同时给
    link_target_b64 (权威) 和可读的 link_target; 有 b64 时必须优先用它,
    否则会把替换字符当成真实目标重建出错误链接.
    """
    if not isinstance(item, dict):
        return None
    b64 = item.get("link_target_b64")
    if isinstance(b64, str) and b64:
        try:
            raw = base64.b64decode(b64.encode("ascii"), validate=True)
            return raw.decode("utf-8", "surrogateescape")
        except Exception:
            pass
    lt = item.get("link_target")
    return lt if isinstance(lt, str) and lt != "" else None


def _remote_link_target_rel(item: dict) -> str | None:
    """从服务器条目取「work_dir 相对规范化目标」, 优先无损 b64.

    FIX-link-rel-b64: 目标含非 UTF-8 字节时, 普通字符串字段会被服务端替换成 U+FFFD,
    直接拿来与本地规范化路径比较会误报 "链接目标规范化不一致" 并拒绝落盘.
    """
    if not isinstance(item, dict):
        return None
    b64 = item.get("link_target_rel_b64")
    if isinstance(b64, str) and b64:
        try:
            return base64.b64decode(b64.encode("ascii"), validate=True) \
                .decode("utf-8", "surrogateescape")
        except Exception:
            pass
    rel = item.get("link_target_rel")
    return rel if isinstance(rel, str) else None


def _link_target_wire(raw: str) -> tuple[str | None, str | None]:
    """把本地 raw link target 编码为 (link_target, link_target_b64) 两个协议字段。

    合法 UTF-8 → 只给 link_target (兼容旧服务端);
    含 surrogateescape (非 UTF-8 字节) → 给 b64, link_target 用替换字符仅供人读。
    """
    try:
        raw.encode("utf-8")
        return raw, None
    except UnicodeEncodeError:
        b = raw.encode("utf-8", "surrogateescape")
        return b.decode("utf-8", "replace"), base64.b64encode(b).decode("ascii")


def _safe_text(s: str) -> str:
    """把可能含 surrogateescape 的字符串变为可 print/json 的文本 (\\udcXX 转义)。"""
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "backslashreplace").decode("utf-8")


def _remote_entry_has_binary_path(item: dict) -> bool:
    """服务端标记该条目路径含非 UTF-8 字节 (JSON 无法无损表达).

    这类路径当前不能通过普通字符串字段安全寻址; 服务端同时给了 path_b64,
    自动化同步先显式跳过并在摘要中计数, 避免用 U+FFFD 替换后的错误路径下载/落盘.
    """
    return isinstance(item, dict) and (
        item.get("encoding") == "binary" or bool(item.get("path_b64"))
    )


def _utf8_ok(s: str) -> bool:
    """JSON 协议只能承载合法 UTF-8; 非 UTF-8 文件名/路径需在扫描阶段跳过并提示。"""
    try:
        s.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def _nearest_existing_real(path: Path) -> str:
    p = Path(path)
    while not os.path.exists(str(p)):
        if p.parent == p:
            break
        p = p.parent
    return os.path.realpath(str(p))


def _write_parent_within(path: Path, base_real: Path) -> bool:
    """写入目标 path 的最近已存在祖先 realpath 是否仍在 base_real 内。"""
    return _path_in_base_abs(_nearest_existing_real(path.parent), base_real)


def _link_resolve_chain_abs(start: str, base_real: str) -> dict:
    """沿链接链解析 (与服务器 _link_resolve_chain 一致).

    返回 {'status': 'ok'|'broken'|'cycle'|'outside', 'resolved': str|None}:
      - ok: 最终落到存在的普通文件/目录
      - broken: 链中某环 readlink 失败 / 最终目标不存在
      - cycle: 40 跳内重复出现同一路径, 或超过 40 跳
      - outside: 链中某环目标（含绝对路径）落到 base_real 外
    只做词法边界判断 + os.readlink, 不使用 realpath(避免链接环异常).
    """
    cur = start
    seen: set[str] = set()
    for _ in range(40):
        cn = _lexical_norm_abs_posix(cur)
        if cn is None:
            return {"status": "broken", "resolved": None}
        if cn in seen:
            return {"status": "cycle", "resolved": None}
        seen.add(cn)
        if not os.path.islink(cn):
            return {"status": "ok" if os.path.exists(cn) else "broken", "resolved": cn}
        try:
            raw = os.readlink(cn)
        except OSError:
            return {"status": "broken", "resolved": None}
        rn = str(raw).replace("\\", "/")
        if rn != "" and (rn.startswith("/") or re.match(r"^[A-Za-z]:/", rn)):
            nxt = _lexical_norm_abs_posix(rn)
        else:
            nxt = _lexical_norm_abs_posix(os.path.dirname(cn) + "/" + rn)
        if nxt is None or not _path_in_base_abs(nxt, base_real):
            return {"status": "outside", "resolved": None}
        cur = nxt
    return {"status": "cycle", "resolved": None}


def local_link_target_meta(link_path: Path, raw: str, watch_dir: Path) -> dict:
    """校验一个拟创建/已存在的软链接目标 (不要求链接已存在), 规则与服务器一致。"""
    def unsafe(reason: str) -> dict:
        return {"safe": False, "target": None, "target_rel": None,
                "target_norm": None, "broken": False, "cycle": False,
                "resolvable": False, "reason": reason}

    if not isinstance(raw, str) or raw == "":
        return unsafe("empty_target")
    if "\0" in raw:
        return unsafe("nul_byte")
    norm = raw.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:/", norm):
        return unsafe("absolute_target")
    base_real = os.path.realpath(str(watch_dir))
    if not base_real:
        return unsafe("base_unresolvable")
    parent_real = os.path.realpath(os.path.dirname(str(link_path)))
    if not parent_real:
        return unsafe("parent_unresolvable")
    if not _path_in_base_abs(parent_real, base_real):
        return unsafe("parent_outside")
    lex = _lexical_norm_abs_posix(parent_real + "/" + norm)
    if lex is None or not _path_in_base_abs(lex, base_real):
        return unsafe("outside_workdir")
    base_trim = base_real.replace("\\", "/").rstrip("/")
    target_rel = lex[len(base_trim):].lstrip("/") or "."
    # FIX-symlink-mid-escape: 目标路径中间经过的链接目录也必须校验
    #   (outdir -> /etc, 目标 outdir/hostname 时最终项不是链接, 只查最后一级会漏).
    try:
        real_target = os.path.realpath(lex)
    except OSError:
        real_target = None
    if real_target is not None and not _path_in_base_abs(real_target, base_real):
        return unsafe("target_escapes_workdir")
    # FIX-symlink-chain: 不再只用 exists()/islink() 两态判断.
    #   链接链/环/中间链接逃逸需要逐环解析, 与服务器 broken/cycle/resolvable 语义对齐.
    res = _link_resolve_chain_abs(lex, base_real)
    if res["status"] == "outside":
        return unsafe("target_escapes_workdir")
    broken = res["status"] != "ok"
    return {"safe": True, "target": raw, "target_rel": target_rel,
            "target_norm": _norm_rel_target(raw),
            "broken": broken, "cycle": res["status"] == "cycle",
            "resolvable": res["status"] == "ok", "reason": None}


def local_link_meta(link_path: Path, watch_dir: Path) -> dict:
    """计算本地软链接元数据, 规则与服务器 _link_meta_from_target 完全一致。

    安全规则:
      - 目标必须是相对路径 (绝对路径不可移植, 也容易指向 watch_dir 外)
      - 词法归一化后必须仍在 watch_dir 内
      - 目标已存在时 realpath 也必须仍在 watch_dir 内 (防中间链接逃逸)
    返回 {safe, target, target_rel, broken, reason}; target_rel 为相对 watch_dir 的 POSIX 路径。
    """
    try:
        raw = os.readlink(str(link_path))
    except OSError:
        return {"safe": False, "target": None, "target_rel": None,
                "target_norm": None, "broken": False, "cycle": False,
                "resolvable": False, "reason": "readlink_fail"}
    return local_link_target_meta(link_path, raw, watch_dir)


def link_finger(link_path: Path, watch_dir: Path) -> dict:
    """软链接指纹: size=0, md5/sha1 = hash(归一化相对目标), 与服务器 checksum 对齐。"""
    try:
        st = os.lstat(str(link_path))
        lm = local_link_meta(link_path, watch_dir)
        target_norm = lm["target_norm"] if lm["safe"] else ""
        base = {
            "size": 0,
            "mtime": st.st_mtime,
            "mtime_ns": int(st.st_mtime_ns),
            "mode": stat.S_IMODE(st.st_mode),
            "sha1": hashlib.sha1(target_norm.encode("utf-8", "surrogateescape")).hexdigest() if target_norm else "",
            "md5": hashlib.md5(target_norm.encode("utf-8", "surrogateescape")).hexdigest() if target_norm else "",
            "is_symlink": True,
            "link_safe": lm["safe"],
            "link_broken": lm["broken"],
            "link_cycle": lm.get("cycle", False),
            "link_resolvable": lm.get("resolvable", False),
            "link_target": lm["target"],
            "link_target_rel": lm["target_rel"],
            "link_reason": lm.get("reason"),
        }
        return base
    except Exception:
        return {"size": -1, "mtime": 0.0, "mtime_ns": 0, "sha1": "", "md5": "", "mode": 0,
                "is_symlink": True, "link_safe": False, "link_broken": False,
                "link_cycle": False, "link_resolvable": False,
                "link_target": None, "link_target_rel": None, "link_reason": "lstat_fail"}


def _rel_under_watch(path: Path, watch_dir: Path) -> str | None:
    """返回 path 相对 watch_dir 的词法 POSIX 路径 (不跟随最后一级)。"""
    base = Path(os.path.abspath(str(watch_dir)))
    p = Path(os.path.abspath(str(path)))
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        pass
    try:
        # watch_dir 本身可能是软链接: 只解析父目录
        parent = Path(os.path.realpath(str(p.parent)))
        rb = Path(os.path.realpath(str(base)))
        return (parent.relative_to(rb) / p.name).as_posix()
    except Exception:
        return None


def glob_match(pattern: str, path: str) -> bool:
    """glob 匹配，同时检查完整路径、文件名和路径尾部子路径。
    支持 cache/* / cache/** / dir/sub/* 这类目录前缀模式匹配绝对路径。"""
    import fnmatch
    basename = Path(path).name
    if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
        return True
    # 对含 / 的模式, 依次尝试路径的尾部子路径, 使 cache/* 能匹配 /abs/cache/x.cache
    if "/" in pattern:
        parts = path.replace("\\", "/").split("/")
        for i in range(len(parts)):
            sub = "/".join(parts[i:])
            if sub and fnmatch.fnmatch(sub, pattern):
                return True
    return False

def should_ignore(local_path: str, ignore_globs: list, allow_exts: list,
                  watch_dir: Path | None = None) -> bool:
    """判断文件是否应被忽略。

    当传入 watch_dir 时，先转成相对路径再匹配，避免绝对路径尾部子串误匹配
    (如 IGNORE 中的 'ai-sync/*' 误匹配 watch_dir 自身 /workspace/ai-sync/hello.py)。
    """
    basename = Path(local_path).name
    # 🆕 FIX-P0-internal: 客户端内部状态文件硬排除 (与 IGNORE 规则无关, 任何情况下不参与同步,
    #   避免 .sync.lock/.ai-sync-watch.* 等被推送造成远端永久差异抖动)
    if (basename.startswith(".ai-sync-watch") or basename.startswith(".ai_sync_")
            or basename in (
            ".sync.lock", ".sync.log", ".sync_history.json", ".sync_oplog.jsonl",
            ".sync_oplog", ".sync_backup", ".sync_state.json", ".sync_conflicts.json",
            ".sync_snapshot.json", ".sync_snapshot.json.bak", ".sync_snapshot.json.tmp")):
        return True
    # 优先用相对路径匹配，消除 watch_dir 前缀带来的误判
    # FIX-symlink: 用 abspath 词法相对路径, 不 resolve() —— resolve 会跟随软链接,
    #   导致链接文件被当成目标文件路径匹配 IGNORE (甚至 relative_to 失败退化为绝对路径匹配).
    match_path = local_path
    if watch_dir is not None:
        try:
            _lp = Path(os.path.abspath(str(local_path)))
            _wd = Path(os.path.abspath(str(watch_dir)))
            match_path = _lp.relative_to(_wd).as_posix()
        except Exception:
            match_path = local_path
    for pat in ignore_globs:
        # 去掉 pattern 中的路径前缀 (如 .git/** → .git)
        pat_clean = pat.replace("/**", "").replace("/*", "")
        if glob_match(pat, match_path) or glob_match(pat_clean, basename):
            return True
    if allow_exts:
        ext = Path(local_path).suffix.lstrip(".")
        if ext and ext not in allow_exts:
            return True
    return False

def atomic_write_bytes(path: Path, data: bytes, mtime: float | None = None,
                       mode: int | None = None) -> bool:
    """原子写入字节: 写临时文件 → fsync → rename → 恢复 mtime/mode。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp_{os.getpid()}_{random.randint(0, 1 << 32)}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            try: os.chmod(tmp, mode)
            except Exception: pass
        os.replace(tmp, path)  # 原子重命名
        if mtime is not None:
            try: os.utime(path, (mtime, mtime))
            except Exception: pass
        return True
    except Exception:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception: pass
        return False

def atomic_write_text(path: Path, text: str) -> bool:
    """原子写入文本 (用于状态/冲突文件)。"""
    return atomic_write_bytes(path, text.encode("utf-8"))


def create_local_symlink(local_path: Path, link_target: str, remote_target_rel: str | None,
                         remote_md5: str, snapshot, backup, watch_dir: Path,
                         log, remote_path: str = "", mtime: float | None = None
                         ) -> tuple[bool, str, bool]:
    """在本地原子创建/替换软链接 (不下载、不写目标内容)。

    返回 (ok, detail, skipped)。已存在且规范目标一致的链接 → skipped=True。
    """
    wd = watch_dir.resolve()
    lm = local_link_target_meta(local_path, link_target, wd)
    if not lm["safe"]:
        return (False, f"不安全的链接目标({lm.get('reason')}): {link_target}", False)
    if remote_target_rel and lm["target_rel"] != remote_target_rel:
        return (False, f"链接目标规范化不一致 (server={remote_target_rel} local={lm['target_rel']})", False)

    # 已存在同目标链接 → 无需重建; 但仍把远程链接自身 mtime 补齐 (原样状态)
    if os.path.islink(str(local_path)):
        cur = local_link_meta(local_path, wd)
        if cur["safe"] and cur["target_rel"] == lm["target_rel"]:
            if mtime:
                try:
                    os.utime(str(local_path), (mtime, mtime), follow_symlinks=False)
                except Exception:
                    pass
            f = link_finger(local_path, wd)
            if snapshot:
                snapshot.update(str(local_path), {
                    "size": 0, "sha1": f["sha1"], "md5": remote_md5 or f["md5"],
                    "remote": remote_path, "mtime": f["mtime"], "mode": f["mode"],
                    "source": f"pull:{remote_path}", "is_symlink": True,
                    "link_target": f["link_target"], "link_target_rel": f["link_target_rel"],
                })
            return (True, f"软链接已一致: {remote_path}", True)

    # 父目录安全: 最近已存在祖先的 realpath 必须在 watch_dir 内 (防本地父级软链接逃逸)
    parent = local_path.parent
    if not _write_parent_within(local_path, wd):
        return (False, f"父目录越界, 拒绝写入: {parent}", False)

    # 备份将被替换的本地内容
    if os.path.islink(str(local_path)):
        try:
            if backup: backup._do_backup_link(local_path)
        except Exception:
            pass
    elif os.path.isdir(str(local_path)):
        return (False, f"目标路径是目录, 拒绝替换为软链接: {local_path}", False)
    elif os.path.exists(str(local_path)):
        try:
            if backup: backup._do_backup(local_path)
        except Exception:
            pass

    tmp = parent / f".tmp_link_{os.getpid()}_{random.randint(0, 1 << 32)}"
    try:
        parent.mkdir(parents=True, exist_ok=True)
        os.symlink(link_target, str(tmp))
        os.replace(str(tmp), str(local_path))
        if mtime:
            try:
                os.utime(str(local_path), (mtime, mtime), follow_symlinks=False)
            except Exception:
                try: os.utime(str(local_path), (mtime, mtime))
                except Exception: pass
    except Exception as e:
        try:
            if os.path.lexists(str(tmp)): os.unlink(str(tmp))
        except Exception:
            pass
        return (False, f"创建软链接失败: {e}", False)

    f = link_finger(local_path, wd)
    if f["size"] < 0 or not f.get("link_safe"):
        try: os.unlink(str(local_path))
        except Exception: pass
        return (False, f"创建后校验失败: {f.get('link_reason') or 'unsafe'}", False)
    _md5 = remote_md5 if (remote_md5 and remote_md5.lower() == f["md5"].lower()) else f["md5"]
    if snapshot:
        snapshot.update(str(local_path), {
            "size": 0, "sha1": f["sha1"], "md5": _md5,
            "remote": remote_path, "mtime": f["mtime"], "mode": f["mode"],
            "source": f"pull:{remote_path}", "is_symlink": True,
            "link_target": f["link_target"], "link_target_rel": f["link_target_rel"],
        })
    if log:
        try:
            log.info(f"  🗎 软链接: {local_path} -> {link_target}")
        except Exception:
            pass
    return (True, f"LINK {remote_path} -> {link_target}", False)

def scan_watch_dir(watch_dir: Path, remote_base: str, ignore_globs: list, allow_exts: list) -> list[dict]:
    """扫描监听目录，返回待同步文件项列表 (含 md5/mode/mtime)。
    进度反馈: 全树遍历在 virtiofs 上实测 24~27s, 若完全无输出会被误判卡死.
    每 500 个文件打印一次计数 (stdout flush), 全程只输出 1~3 行.

    FIX-symlink: 用 os.walk(followlinks=False) 遍历, 软链接自身作为独立条目
    (is_symlink=True) 收集, 绝不进入链接目录/读取目标内容. 越界或绝对路径链接
    直接跳过并提示, 不上传目标内容.
    """
    items: list[dict] = []
    if not watch_dir.is_dir():
        return items
    wd = watch_dir.resolve()
    # 🆕 v4.8-SCAN-OPT: snapshot 复用——mtime+size 未变且 md5 非 fake 的文件, 直接复用 snapshot 的
    #   sha1/md5/mode, 跳过读文件算 hash. 实测 finger 占扫描耗时 ~91% (遍历 0.17s vs 计算 1.75s/1047文件).
    #   复用条件: snapshot 条目存在 ∧ size 匹配 ∧ mtime 匹配(浮点秒级 1e-6) ∧ md5 非 fake(排除 BUG-26 占位).
    #   安全性: size+mtime 不变 ⇒ 内容不变; dirty 判定(3617) 用 size+sha1 双比对, 复用后行为完全一致.
    snap_cache: dict = {}
    try:
        _sp = wd / ".sync_snapshot.json"
        if _sp.is_file():
            _raw = json.loads(_sp.read_text(encoding="utf-8"))
            if isinstance(_raw, dict):
                snap_cache = _raw
    except Exception:
        snap_cache = {}
    _reuse_n = 0
    _scan_cnt = 0
    _scan_reported = 0
    _link_n = 0
    _unsafe_links: list[str] = []
    # JSON 协议无法表达非 UTF-8 文件名: 跳过并提示, 避免整次 push 因一条坏名字失败
    _bad_names: list[str] = []

    def _entry(lp: Path) -> None:
        nonlocal _scan_cnt, _scan_reported, _reuse_n, _link_n
        _scan_cnt += 1
        # 进度: 每 500 个文件节流输出 (不刷屏), 标出正在扫描的位置, 避免 AI 误判卡死
        if _scan_cnt - _scan_reported >= 500 and not bool(getattr(sys, "_AI_SYNC_QUIET", False)):
            try:
                _rel_cur = lp.relative_to(wd).as_posix()
            except Exception:
                _rel_cur = str(lp)
            print(f"  [scan] 已扫描 {_scan_cnt} 个文件 ... (当前: {_rel_cur})", flush=True)
            _scan_reported = _scan_cnt
        try:
            rel = lp.relative_to(wd).as_posix()
        except Exception:
            return
        if should_ignore(rel, ignore_globs, allow_exts, watch_dir=None):
            return
        if not _utf8_ok(rel):
            _bad_names.append(rel)
            return
        rp = (remote_base.rstrip("/") + "/" + rel) if remote_base else rel
        if os.path.islink(str(lp)):
            f = link_finger(lp, wd)
            if f["size"] < 0:
                return
            if not f.get("link_safe"):
                _unsafe_links.append(f"{rel} -> {f.get('link_target') or '?'}")
                return
            _link_n += 1
            items.append({
                "local": str(lp),
                "remote": rp,
                "size": 0,
                "mtime": f["mtime"],
                "mtime_ns": f["mtime_ns"],
                "sha1": f["sha1"],
                "md5": f["md5"],
                "mode": f["mode"],
                "type": "watch",
                "source": f"watch:{rel}",
                "is_symlink": True,
                "link_target": f["link_target"],
                "link_target_rel": f["link_target_rel"],
                "link_broken": f["link_broken"],
            })
            return
        # 🆕 snapshot 复用: 命中则跳过读文件算 hash (实测占扫描耗时 91%)
        f = None
        try:
            _prev = snap_cache.get(str(lp))
            if _prev and isinstance(_prev, dict) and not _snap_has_fake_md5(_prev):
                _st = lp.stat()
                if (_prev.get("size") == _st.st_size
                        and abs(float(_prev.get("mtime", 0.0)) - _st.st_mtime) < 1e-6):
                    # mode 必须取实时 lstat: chmod 不改 mtime, 用快照旧 mode 会漏掉纯权限变更
                    f = {"size": _st.st_size, "mtime": _st.st_mtime,
                         "mtime_ns": _st.st_mtime_ns, "sha1": _prev.get("sha1", ""),
                         "md5": _prev.get("md5", ""), "mode": stat.S_IMODE(_st.st_mode)}
                    _reuse_n += 1
        except Exception:
            f = None
        if f is None:
            f = finger(lp)
        if f["size"] >= 0:
            items.append({
                "local": str(lp),
                "remote": rp,
                "size": f["size"],
                "mtime": f["mtime"],
                "mtime_ns": f["mtime_ns"],
                "sha1": f["sha1"],
                "md5": f["md5"],
                "mode": f["mode"],
                "type": "watch",
                "source": f"watch:{rel}",
            })

    for root, dirnames, filenames in os.walk(str(wd), followlinks=False):
        rootp = Path(root)
        # 软链接目录出现在 dirnames 中, 但 followlinks=False 不会递归进去
        for name in dirnames:
            lp = rootp / name
            if lp.is_symlink():
                _entry(lp)
        for name in filenames:
            _entry(rootp / name)

    if _scan_cnt > 0 and not bool(getattr(sys, "_AI_SYNC_QUIET", False)):
        _link_msg = f", 其中软链接 {_link_n}" if _link_n else ""
        print(f"  [scan] 扫描完成: 共 {_scan_cnt} 个文件{_link_msg}, 命中 {len(items)} 项 (snapshot复用 {_reuse_n})", flush=True)
    if _bad_names:
        _show = "; ".join(_safe_text(x) for x in _bad_names[:3])
        _more = f" 等 {len(_bad_names)} 个" if len(_bad_names) > 3 else ""
        print(f"  [scan] 跳过非 UTF-8 文件名{_more}: {_show} (JSON 协议无法表达, 建议改名)",
              file=sys.stderr, flush=True)
    if _unsafe_links:
        _show = "; ".join(_unsafe_links[:3])
        _more = f" 等 {len(_unsafe_links)} 个" if len(_unsafe_links) > 3 else ""
        print(f"  [scan] 跳过越界/绝对路径软链接{_more}: {_show}" +
              (" (目标在 watch_dir 外, 不上传目标内容)" if _unsafe_links else ""),
              file=sys.stderr, flush=True)
    return items

def reconcile_dir_cache(api, conf: dict, cache: dict) -> int:
    """用一次远程目录列举校验目录缓存, 丢弃远端已不存在的条目 (返回丢弃数)。

    watch 每 60s 调用一次: 其他 AI 删除远程目录后, 本地目录仍应被重新创建。
    列举失败时不清缓存 (避免网络抖动导致全量重建)。
    """
    if not cache:
        return 0
    base = (conf.get("remote_base") or "").rstrip("/")
    list_path = base or "."
    remote: set[str] = set()
    offset = 0
    while True:
        try:
            r = api.list_remote(list_path, recursive=True, offset=offset, limit=1000,
                                include_hidden=True, max_depth=0)
        except Exception:
            return 0
        if not r.get("ok"):
            return 0
        items = r.get("items") or []
        for it in items:
            if it.get("type") == "dir" or it.get("is_dir"):
                remote.add(it.get("path") or "")
        if not r.get("has_more") or not items:
            break
        offset += len(items)
        if offset > 200000:
            break
    dropped = 0
    for rel in list(cache):
        rp = (base + "/" + rel) if base else rel
        if rp not in remote:
            cache.pop(rel, None)
            dropped += 1
    return dropped

def _remote_dir_to_local(conf: dict, rp: str) -> "Path | None":
    """远程目录路径 → 本地 watch 路径。

    不在 remote_base 子树内 / 含 `..` / 同步根本身 → None (不参与本地目录回填)。
    """
    watch = Path(conf["watch_dir"])
    remote_base = (conf.get("remote_base") or "").strip("/")
    rp = (rp or "").strip()
    if not rp:
        return None
    if remote_base:
        if not rp.startswith(remote_base + "/"):
            return None
        rel = rp[len(remote_base) + 1:]
    else:
        rel = rp
    rel = rel.strip("/")
    if not rel or rel == ".":
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return watch.joinpath(*parts)

def _fetch_remote_dir_items(engine: "SyncEngine") -> list[dict]:
    """分页列举远程目录项 (type=dir, 含 mode)。失败返回 [] (目录属于尽力而为状态)。"""
    api = engine.api
    conf = engine.conf
    list_path = (conf.get("remote_base") or "").rstrip("/") or "."
    ignore_globs = conf.get("ignore", []) or []
    out: list[dict] = []
    offset = 0
    while True:
        try:
            r = api.list_remote(list_path, recursive=True, offset=offset, limit=1000,
                                include_hidden=True, max_depth=0)
        except Exception:
            break
        if not r.get("ok"):
            break
        items = r.get("items") or []
        for it in items:
            # 兼容旧服务端: 未返回 type 时用 is_dir 兜底 (与 reconcile_dir_cache 一致)
            if it.get("type") != "dir" and not it.get("is_dir"):
                continue
            rp = it.get("path") or ""
            if not rp or any(glob_match(g, rp) for g in ignore_globs):
                continue
            out.append(it)
        if not r.get("has_more") or not items:
            break
        offset += len(items)
        if offset > 200000:  # 安全上限
            break
    return out

def _watch_ensure_local_dirs(engine: "SyncEngine", dirs: list[dict]) -> int:
    """two-way watch: 远程有、本地无的目录在本地补建 (默认权限, mode 后续回填)。

    - 本地删除保护: 本会话内曾同步过的目录 (在 _dir_cache 中) 现已不存在 →
      视为用户主动删除, 不重建 (与文件 snapshot 的删除语义一致).
    - 越界 (远程路径指向 watch_dir 外) 跳过并告警.
    返回新建目录数.
    """
    watch = Path(engine.conf["watch_dir"])
    try:
        watch_real = watch.resolve()
    except Exception:
        return 0
    created = 0
    # 按深度排序保证父目录先建 (mkdir parents=True 也能建, 但排序后日志/计数更直观)
    for it in sorted(dirs, key=lambda x: str(x.get("path", "")).count("/")):
        rp = it.get("path") or ""
        lp = _remote_dir_to_local(engine.conf, rp)
        if lp is None:
            continue
        try:
            if os.path.islink(str(lp)) or os.path.exists(str(lp)):
                continue
            try:
                rel = lp.relative_to(watch).as_posix()
            except Exception:
                continue
            if rel in engine._dir_cache:
                engine.log.debug(f"[watch] 远程目录在本地被删除, 跳过重建: {rp}")
                continue
            if not _write_parent_within(lp, watch_real):
                engine.log.warn(f"[watch] 跳过越界远程目录: {rp}")
                continue
            lp.mkdir(parents=True, exist_ok=True)
            created += 1
        except Exception as e:
            engine.log.warn(f"[watch] 远程目录创建失败 {rp}: {e}")
    if created:
        engine.log.info(f"[watch] 远程目录: 本地新建 {created} 个")
    return created

def _watch_apply_remote_dir_modes(engine: "SyncEngine", dirs: list[dict]) -> int:
    """two-way watch: 把远程目录 mode 回填到本地 (文件落盘后调用)。

    - 只 chmod 与远程不一致的目录; 成功后写 _dir_cache, 避免下一轮把默认 0755 推回远端。
    - 冲突策略与文件一致: 本地和远程都相对上次同步值变了 → 保留本地并告警 (推送侧重试)。
    返回修正数.
    """
    watch = Path(engine.conf["watch_dir"])
    fixed = 0
    for it in dirs:
        rp = it.get("path") or ""
        lp = _remote_dir_to_local(engine.conf, rp)
        if lp is None:
            continue
        try:
            if os.path.islink(str(lp)) or not lp.is_dir():
                continue
            dm = it.get("mode")
            if dm in (None, ""):
                continue
            dmi = int(str(dm), 8) & 0o777
            cur = lp.stat().st_mode & 0o777
            try:
                rel = lp.relative_to(watch).as_posix()
            except Exception:
                rel = ""
            if rel and cur != dmi:
                # _dir_cache 记录"上次同步时本地 mode": 两边都偏离 → 冲突, 本地保留
                _cm = engine._dir_cache.get(rel)
                _cache_mode = int(_cm, 8) if _cm else None
                if (_cache_mode is not None and cur != _cache_mode
                        and dmi != _cache_mode):
                    engine.log.warn(f"[watch] 目录冲突(两边都改): {rp} "
                                    f"本地 {format(cur,'04o')} 远程 {format(dmi,'04o')}, "
                                    f"保留本地 (推送侧重试)")
                    continue
                os.chmod(lp, dmi)
                fixed += 1
                cur = dmi
            if rel:
                engine._dir_cache[rel] = format(cur, "04o")
        except Exception:
            pass
    if fixed:
        engine.log.info(f"[watch] 远程目录: 本地回填权限 {fixed} 个")
    return fixed


def scan_local_dirs(watch_dir: Path, ignore_globs: list,
                    roots: list[str] | None = None) -> list[dict]:
    """扫描本地真实目录 (跳过软链接目录), 返回 [{local, rel, mode}]。

    roots: 只同步这些相对目录前缀 (None=全部). 用于 --push <目录> 时限定范围.
    """
    out: list[dict] = []
    if not watch_dir.is_dir():
        return out
    wd = watch_dir.resolve()
    skip_prefixes: list[str] = []
    for root, dirnames, _filenames in os.walk(str(wd), followlinks=False):
        rootp = Path(root)
        for name in dirnames:
            lp = rootp / name
            if lp.is_symlink():
                continue
            try:
                rel = lp.relative_to(wd).as_posix()
            except Exception:
                continue
            if not _utf8_ok(rel):
                continue
            if any(rel == s or rel.startswith(s + "/") for s in skip_prefixes):
                continue
            if should_ignore(rel, ignore_globs, [], watch_dir=None):
                skip_prefixes.append(rel)
                continue
            if roots:
                if not any(rel == r or rel.startswith(r + "/") or r.startswith(rel + "/")
                           for r in roots):
                    continue
            try:
                st = lp.stat()
            except Exception:
                continue
            out.append({"local": str(lp), "rel": rel,
                        "mode": stat.S_IMODE(st.st_mode) & 0o777})
    return out

def sync_local_dirs(api, conf: dict, log, roots: list[str] | None = None,
                    dry_run: bool = False, cache: dict | None = None) -> int:
    """把本地目录 (含空目录与 mode) 同步到远程。返回成功创建/修正数。

    cache: rel -> "0755" 内存缓存 (watch 模式用), 已一致则跳过请求.
    """
    watch_dir = Path(conf["watch_dir"])
    remote_base = conf["remote_base"]
    dirs = scan_local_dirs(watch_dir, conf["ignore"], roots)
    dirs.sort(key=lambda d: d["rel"].count("/"))
    n = 0
    for d in dirs:
        mode_s = format(d["mode"] & 0o777, "04o")
        rel = d["rel"]
        if cache is not None and cache.get(rel) == mode_s:
            continue
        rp = (remote_base.rstrip("/") + "/" + rel) if remote_base else rel
        if dry_run:
            n += 1
            continue
        try:
            r = api.mkdir_remote(rp, mode=mode_s)
        except Exception as e:
            log.warn(f"  ⚠ 目录同步失败 {rp}: {e}")
            continue
        if r.get("ok"):
            n += 1
            if cache is not None:
                cache[rel] = mode_s
        else:
            log.warn(f"  ⚠ 目录同步失败 {rp}: {r.get('error') or r.get('message') or 'unknown'}")
    if n:
        log.info(f"  目录同步: 创建/修正 {n} 个远程目录 (含 mode)")
    return n

def _snap_has_fake_md5(prev: dict) -> bool:
    """BUG-26 修复: snapshot.md5 是 "f:..." 占位符 → 该文件之前从未真正同步成功
    (旧 bug 或初始化时 fast 模式直接写入脏数据). 此时 fake_md5==fake_md5 永远不等 dirty,
    导致 watch 永远不推送, verify 报 "远程缺失" 但守护不动. 必须强制视为 dirty 触发重传.

    注意: 只查 md5 不查 sha1. 因为 BUG-8 修复后 _watch_do_push 写快照时只覆盖 md5 为真值,
    sha1 仍是 fast 模式 fake (用于下一轮 size+mtime_ns 变更检测), 这是预期行为不算脏.
    """
    md5 = prev.get("md5", "") if isinstance(prev, dict) else ""
    return (not md5) or (isinstance(md5, str) and md5.startswith("f:"))

def _mode_changed(prev: dict | None, m: dict) -> bool:
    """快照里记录的上次同步 mode 与当前本地 mode 不同。

    服务端没有独立 chmod 动作, 因此仅权限变化 (chmod +x) 也必须走一次上传,
    由 upload/upload_batch/chunked init 的 mode 参数把权限带到远端。
    mode=0 / 缺失视为旧快照未知, 不触发重传 (避免升级后全量重传)。
    """
    if not isinstance(prev, dict):
        return False
    pm = prev.get("mode")
    if not isinstance(pm, int) or pm == 0:
        return False
    return (pm & 0o7777) != (int(m.get("mode") or 0) & 0o7777)

def compute_todos(mon: list[dict], snapshot: "Snapshot", ignore_globs: list) -> tuple[list[dict], list[dict]]:
    """对比快照，计算上传列表和删除列表。"""
    now = time.time()
    upload: list[dict] = []
    for m in mon:
        prev = snapshot.get(m["local"])
        # BUG-26 修复: snapshot.md5 是 fake 占位符 → 之前从未真正同步成功, 强制视为 dirty 触发重传.
        dirty = _snap_has_fake_md5(prev) or (prev.get("size") != m["size"]
                 or prev.get("sha1") != m["sha1"]
                 or prev.get("remote") != m.get("remote")
                 or _mode_changed(prev, m))
        if dirty:
            upload.append(m)

    delete: list[dict] = []
    current_paths = set(str(m["local"]) for m in mon)
    for local_path, prev_snap in list(snapshot.data.items()):
        if not isinstance(prev_snap, dict): continue
        if str(local_path) in current_paths: continue
        if not prev_snap.get("remote"): continue
        delete.append({
            "local": local_path,
            "remote": prev_snap["remote"],
            "source": prev_snap.get("source", ""),
            "detected_at": now,
        })

    seen = set()
    dedup = []
    for d in delete:
        if d["remote"] in seen: continue
        seen.add(d["remote"])
        dedup.append(d)
    return upload, dedup

# ---------------------------------------------------------------------------
# 6. 快照管理 (v4.0: 原子保存 + 备份 + 清理)
# ---------------------------------------------------------------------------
class Snapshot:
    # save 节流默认值: 两次真实写盘之间至少间隔这么多秒
    SAVE_THROTTLE_S = 30.0
    # 即使节流, 累积脏数据超过这么多秒也强制落盘 (防断电丢状态)
    SAVE_FORCE_AFTER_DIRTY_S = 300.0

    def __init__(self, path: Path, log: "Logger | None" = None):
        self.path = path
        self.data: dict = {}
        self._dirty = False
        self._last_save_ts = 0.0
        self._dirty_since_ts = 0.0
        self._log = log  # BUG-27: save 失败时记日志
        self._save_fail_count = 0
        self._load()

    def _load(self):
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.data, dict):
                    self.data = {}
            except Exception:
                self.data = {}
        # 尝试从 .bak 恢复
        if not self.data:
            bak = self.path.with_name(self.path.name + ".bak")
            if bak.is_file():
                try:
                    d = json.loads(bak.read_text(encoding="utf-8"))
                    if isinstance(d, dict):
                        self.data = d
                except Exception:
                    pass
        self._dirty = False
        self._last_save_ts = time.time()
        self._dirty_since_ts = 0.0

    def _mark_dirty(self):
        self._dirty = True
        if self._dirty_since_ts <= 0:
            self._dirty_since_ts = time.time()

    def save(self, force: bool = False):
        """原子保存: 写 .tmp → 备份旧文件 → rename。
        force=False 时启用节流 (SAVE_THROTTLE_S), 避免频繁写盘。
        若脏数据持续 SAVE_FORCE_AFTER_DIRTY_S 以上则强制落盘。
        """
        now = time.time()
        if not force and self._dirty:
            # 节流: 没到时间也没超上限就跳过
            if ((now - self._last_save_ts) < self.SAVE_THROTTLE_S
                    and (now - self._dirty_since_ts) < self.SAVE_FORCE_AFTER_DIRTY_S):
                return
        if not self._dirty and not force:
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, indent=2)
            # 链接目标可能含非 UTF-8 字节 (surrogateescape): 直接按 utf-8 写会抛 UnicodeEncodeError,
            # 一条坏链接就导致整份快照永远写不出去 → 重启后全量重传.
            # backslashreplace 把 lone surrogate 变成 JSON 合法转义 \udcXX, 读回后字节不变.
            with open(tmp, "wb") as f:
                f.write(text.encode("utf-8", "backslashreplace"))
                f.flush()
                os.fsync(f.fileno())
            # 备份旧快照
            if self.path.exists():
                bak = self.path.with_name(self.path.name + ".bak")
                try:
                    if bak.exists(): bak.unlink()
                    os.replace(self.path, bak)
                except Exception:
                    pass
            os.replace(tmp, self.path)
            self._dirty = False
            self._last_save_ts = now
            self._dirty_since_ts = 0.0
        except Exception as e:
            # BUG-27 修复 (审计 Finding 5): snapshot 写盘失败不能静默吞!
            # 否则 daemon 跑数小时后状态丢失, 每次重启都全量重传 (浪费配额/带宽),
            # 操作员却毫无察觉. 改为: 记日志 + 累计失败计数 + 清理 .tmp 残留.
            # _dirty 保持 True (下一轮还会再试, 直到成功).
            try:
                if hasattr(self, "_log") and self._log:
                    self._log.err(f"snapshot save 失败 (将重试): {e}")
                else:
                    sys.stderr.write(f"[snapshot] save 失败 (将重试): {e}\n")
            except Exception:
                pass
            self._save_fail_count = getattr(self, "_save_fail_count", 0) + 1
            # 清理 .tmp 残留, 避免占盘
            try:
                if tmp.exists(): tmp.unlink()
            except Exception:
                pass

    def get(self, local: str) -> dict:
        return self.data.get(local, {})

    def update(self, local: str, info: dict):
        # 轻微优化: 若 key 存在且值完全一致, 不标记脏 (省一次 I/O)
        existing = self.data.get(local)
        if existing == info:
            return
        self.data[local] = info
        self._mark_dirty()

    def remove(self, local: str):
        if local in self.data:
            self.data.pop(local, None)
            self._mark_dirty()

    def cleanup(self, current_local_paths: set) -> int:
        """清理快照中已不在本地的文件条目, 返回清理数量。"""
        removed = 0
        for local_path in list(self.data.keys()):
            if local_path not in current_local_paths:
                self.data.pop(local_path, None)
                removed += 1
        if removed > 0:
            self._mark_dirty()
        return removed

# ---------------------------------------------------------------------------
# 7. 本地备份 (v4.0: 拉取前备份, 防数据丢失)
# ---------------------------------------------------------------------------
class LocalBackup:
    def __init__(self, watch_dir: Path, base_dir: str, keep: int, log: Logger):
        self.watch_dir = watch_dir.resolve()
        self.base_dir = (watch_dir / base_dir).resolve()
        self.keep = max(keep, 0)
        self.log = log
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _rel_path(self, local_path: Path) -> str:
        # FIX-symlink: 词法相对路径, 不 resolve 最后一级 (否则备份名会变成链接目标)
        try:
            p = Path(os.path.abspath(str(local_path)))
            return p.relative_to(self.watch_dir).as_posix()
        except Exception:
            try:
                parent = Path(os.path.realpath(str(local_path.parent)))
                return (parent.relative_to(self.watch_dir) / local_path.name).as_posix()
            except Exception:
                return local_path.name

    def backup_if_modified(self, local_path: Path, snapshot: "Snapshot") -> str:
        """本地文件相对快照有修改时备份, 返回备份路径 (无备份返回 '')。"""
        try:
            if os.path.islink(str(local_path)):
                snap = snapshot.get(str(local_path))
                if not snap:
                    return ""
                cur = link_finger(local_path, self.watch_dir)
                if snap.get("is_symlink") and snap.get("md5") == cur.get("md5"):
                    return ""
                return self._do_backup_link(local_path)
            if not local_path.is_file():
                return ""
            snap = snapshot.get(str(local_path))
            if not snap:
                return ""  # 无基线, 无需备份
            # BUG-21 修复: 用真 md5 判断本地是否改动, 不用 fast 模式的 "f:..." 占位 sha1.
            #   内容改了但 mtime 被保留 (touch -d / cp -p) 时 fast sha1 不变 → 漏备份 → 本地改动被覆盖.
            cur_md5 = md5_file(local_path)
            snap_md5 = snap.get("md5", "")
            if snap_md5 and not snap_md5.startswith("f:") and cur_md5 == snap_md5:
                return ""  # 本地未修改, 无需备份
            # snap_md5 是 fake 或不匹配 → 保守备份 (宁可多备份也不丢数据)
            return self._do_backup(local_path)
        except Exception as e:
            self.log.warn(f"备份失败 {local_path}: {e}")
            return ""

    def _do_backup(self, local_path: Path) -> str:
        """强制备份一个文件, 命名: 原路径.bak.时间戳。软链接备份为文本形式。"""
        if os.path.islink(str(local_path)):
            return self._do_backup_link(local_path)
        try:
            rel = self._rel_path(local_path)
            ts = time.strftime("%Y%m%d_%H%M%S")
            # 同秒内多次备份加随机后缀避免覆盖
            suffix = f"{ts}_{random.randint(1000, 9999)}"
            dest = self.base_dir / f"{rel}.bak.{suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dest)
            try:
                disp = str(dest.resolve().relative_to(self.watch_dir))
            except Exception:
                disp = str(dest)
            self.log.info(f"  🗎 本地备份: {rel} → {disp}")
            self._prune(rel)
            return str(dest)
        except Exception as e:
            self.log.warn(f"备份写入失败 {local_path}: {e}")
            return ""

    def _do_backup_link(self, local_path: Path) -> str:
        """备份软链接本身: AISYNC-SYMLINK-V1 + base64(raw target), 绝不复制目标内容。

        用 base64 而非直接写文本: POSIX 链接目标可以是非 UTF-8 字节,
        按 utf-8 写会抛 UnicodeEncodeError → 备份被静默跳过 → 覆盖后无法恢复。
        """
        try:
            rel = self._rel_path(local_path)
            raw = os.readlink(str(local_path))
            raw_b = os.fsencode(raw) if not isinstance(raw, bytes) else raw
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = f"{ts}_{random.randint(1000, 9999)}"
            dest = self.base_dir / f"{rel}.bak.{suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"AISYNC-SYMLINK-V1\n" + base64.b64encode(raw_b) + b"\n")
            self.log.info(f"  🗎 本地备份(软链接): {rel} -> {raw_b.decode('utf-8', 'replace')}")
            self._prune(rel)
            return str(dest)
        except Exception as e:
            self.log.warn(f"软链接备份写入失败 {local_path}: {e}")
            return ""

    def _prune(self, rel: str):
        """按时间保留最近 keep 份备份。"""
        if self.keep <= 0:
            return
        try:
            backups = sorted(self.base_dir.glob(f"{rel}.bak.*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[self.keep:]:
                try: old.unlink()
                except Exception: pass
        except Exception:
            pass

    def backup_remote(self, remote_path: str, data: bytes) -> str:
        """备份远程旧版本到本地 .sync_backup/remote/ 目录, 命名: 路径.bak.时间戳。"""
        try:
            rel = remote_path.replace('\\', '/').lstrip('/')
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = f"{ts}_{random.randint(1000, 9999)}"
            dest = self.base_dir / "remote" / f"{rel}.bak.{suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            try:
                disp = str(dest.resolve().relative_to(self.watch_dir))
            except Exception:
                disp = str(dest)
            self.log.info(f"  🗎 远程备份: {remote_path} → {disp}")
            self._prune(f"remote/{rel}")
            return str(dest)
        except Exception as e:
            self.log.warn(f"远程版本备份失败 {remote_path}: {e}")
            return ""

# ---------------------------------------------------------------------------
# 8. 冲突检测 (v4.0: 双向同步安全)
# ---------------------------------------------------------------------------
class ConflictDetector:
    STRATEGIES = ("local", "remote", "skip", "backup_both")

    def __init__(self, strategy: str, conflicts_file: Path, log: Logger):
        self.strategy = strategy if strategy in self.STRATEGIES else "backup_both"
        self.conflicts_file = conflicts_file
        self.log = log
        self.records: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.conflicts_file.is_file():
            try:
                d = json.loads(self.conflicts_file.read_text(encoding="utf-8"))
                if isinstance(d, list):
                    return d[-500:]  # 最多保留 500 条
            except Exception:
                pass
        return []

    def detect(self, local_path: str, snapshot: "Snapshot", local_md5: str, remote_md5: str) -> bool:
        """冲突定义: 快照MD5 != 本地当前MD5 且 快照MD5 != 远程MD5 (两端都改了)。"""
        if not remote_md5:
            return False
        snap = snapshot.get(local_path)
        snap_md5 = snap.get("md5", "")
        # BUG-9 修复: snap_md5 可能是历史写入的 "f:..." 占位符, 不能用来判断
        if not snap_md5 or snap_md5.startswith("f:"):
            return False  # 无有效基线不算冲突 (避免误报)
        if snap_md5 == local_md5:
            return False  # 本地未改
        if snap_md5 == remote_md5:
            return False  # 远程未改 (仅本地改 → 上传)
        return True  # 两端都改 → 冲突

    def record(self, local_path: str, remote_path: str, snap_md5: str,
               local_md5: str, remote_md5: str, action: str):
        """记录一条冲突到内存与磁盘。"""
        rec = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "local": local_path,
            "remote": remote_path,
            "snap_md5": snap_md5,
            "local_md5": local_md5,
            "remote_md5": remote_md5,
            "strategy": self.strategy,
            "action": action,
        }
        self.records.append(rec)
        self.records = self.records[-500:]
        try:
            atomic_write_text(self.conflicts_file, json.dumps(self.records, ensure_ascii=False, indent=2))
        except Exception:
            pass
        self.log.warn(f"  ⚡ 冲突: {Path(local_path).name} 策略={self.strategy} 动作={action}")

# ---------------------------------------------------------------------------
# 9. 同步状态 (v4.0: 断点恢复)
# ---------------------------------------------------------------------------
class SyncState:
    def __init__(self, path: Path, log: Logger):
        self.path = path
        self.log = log
        self.data: dict = {}
        self._load()

    def _load(self):
        if self.path.is_file():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    self.data = d
            except Exception:
                self.data = {}

    def is_interrupted(self) -> bool:
        return bool(self.data.get("phase"))

    def interrupted_info(self) -> dict:
        return {
            "phase": self.data.get("phase", ""),
            "started_at": self.data.get("started_at", 0),
            "pending": [x for x in self.data.get("queue", []) if x not in self.data.get("done", [])],
        }

    def begin(self, phase: str, queue: list | None = None):
        self.data = {
            "phase": phase,
            "started_at": time.time(),
            "queue": queue or [],
            "done": [],
        }
        self._save()

    def progress(self, item: str):
        if not self.data: return
        self.data.setdefault("done", []).append(item)
        # 每 5 个落盘一次, 平衡 IO 与恢复粒度
        if len(self.data["done"]) % 5 == 0:
            self._save()

    def _save(self):
        try:
            atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False))
        except Exception as e:
            # BUG-30 修复 (审计 Finding 6): SyncState 是断点恢复依据, 写盘失败不能静默吞!
            # 否则崩溃后 is_interrupted() 返回 stale phase, interrupted_info() 返回 bogus queue.
            self.log.err(f"SyncState save 失败 (断点恢复可能失效): {e}")

    def clear(self):
        self.data = {}
        try:
            if self.path.exists(): self.path.unlink()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# 10. 上传器 (v4.0: MD5对比 + 重试 + 进度 + 带宽限制)
# ---------------------------------------------------------------------------
class Uploader:
    def __init__(self, api: ApiClient, log: Logger, conf: dict):
        self.api = api
        self.log = log
        self.conf = conf
        self.max_retries = max(conf.get("max_retries", 3), 0)
        self.fail_threshold = max(conf.get("fail_threshold", 3), 1)
        # 带宽限制 (bytes/sec, 0=不限制)
        self.bandwidth_bps = conf.get("bandwidth_limit_kb", 0) * 1024
        self._fail_counts: dict[str, int] = {}  # local_path -> 连续失败次数

    def _backoff_delay(self, attempt: int) -> float:
        bo = self.conf.get("retry_backoff") or [1.2, 5, 12]
        idx = min(attempt, len(bo) - 1)
        try:
            return float(bo[idx])
        except Exception:
            return 1.2

    def _is_skipped(self, local_path: str) -> bool:
        """连续失败超过阈值 → 跳过该文件。"""
        return self._fail_counts.get(local_path, 0) >= self.fail_threshold

    def upload_items(self, items: list[dict], snapshot: Snapshot, dry_run: bool = False,
                     remote_md5_map: dict | None = None, state: SyncState | None = None,
                     progress_cb: Callable[[int, int], None] | None = None
                     ) -> tuple[int, int, int, int]:
        """上传一批文件, 返回 (成功数, 失败数, 跳过数, 批量请求数)。
        小文件分多批上传 (每批 batch_size, 按 batch_max_kb 估算) 。"""
        if not items:
            return (0, 0, 0, 0)

        ok_count = fail_count = skip_count = batched = 0
        self.failed_list: list = []  # 🆕 v4.23: 收集失败文件的远程路径 (cmd_push 读取输出清单)
        chunked_threshold = self.conf.get("chunked_threshold_kb", 8192) * 1024
        # BUG-33 修复 (审计 Finding 11): chunk_size_kb=0 会让分片大小=0, 服务端 ceil(size/0) 500,
        # 客户端 data[0:0] 永远空 chunk. 强制下限 64KB.
        chunk_size = max(64 * 1024, self.conf.get("chunk_size_kb", 4096) * 1024)
        batch_max_kb = self.conf.get("batch_max_kb", 3)
        batch_size = self.conf.get("batch_size", 8)
        rmap = remote_md5_map or {}

        total = len(items)
        # FIX-symlink: 软链接走独立协议 (upload link_target), 不读目标内容, 不参与小文件批量/分片.
        link_items = [m for m in items if m.get("is_symlink")]
        file_items = [m for m in items if not m.get("is_symlink")]
        small_items = [m for m in file_items if m["size"] < batch_max_kb * 1024]
        large_items = [m for m in file_items if m["size"] >= batch_max_kb * 1024]

        # ========= P1-upload fix: 循环把 small_items 按 batch_size 全量传 ============
        if small_items and batch_size > 1:
            # 先按 size 分桶, 避免任意 batch 超过接口 batch_max_kb (服务端的 batch 校验)
            # 这里保守做法: 按 batch_size 切片, 每片再二次 size 限制
            idx = 0
            _pend = []  # [AI-SAFE v1.1.2] 收集批次, 循环后并发发送 (批间不再串行阻塞 RTT)
            while idx < len(small_items):
                batch_files = []
                batch_refs = []
                batch_kb = 0
                # 修复 BUG-1 (死循环): 原版 idx 仅在 batch_files.append 后自增,
                #   一批全部命中 continue (远程一致/失败超阈值/读失败/文件消失) 时 idx 永不前进, while 重新切同一批, 100% CPU 卡死.
                #   改为: 每处理一个 item 后立即推进 idx, 仅 batch_kb 超限 break 时回退 idx 让当前 item 下一批重新处理.
                for m in list(small_items[idx:idx + batch_size]):
                    idx += 1  # 先推进, break 时下面回退
                    lp = Path(m["local"])
                    if not lp.is_file():
                        continue
                    if self._is_skipped(m["local"]):
                        skip_count += 1
                        self.log.debug(f"  ⊘ 跳过(失败超阈值): {lp.name}")
                        continue
                    try:
                        data = lp.read_bytes()
                    except Exception as e:
                        skip_count += 1
                        self.log.warn(f"  ⊘ 读取失败跳过: {lp.name}: {e}")
                        continue
                    local_md5 = hashlib.md5(data).hexdigest()
                    _ri = rmap.get(m["remote"])
                    _rmd5 = _ri.get("md5", "") if isinstance(_ri, dict) else (_ri or "")
                    if _rmd5 and _rmd5.lower() == local_md5.lower() and not _mode_changed(snapshot.get(m["local"]), m):
                        skip_count += 1
                        snapshot.update(m["local"], {
                            "size": m["size"], "sha1": m["sha1"], "md5": local_md5,
                            "remote": m["remote"], "mtime": m.get("mtime", 0.0),
                            "mode": m.get("mode", 0), "source": m.get("source", ""),
                        })
                        self.log.debug(f"  ⊘ 远程已一致(批量): {lp.name}")
                        continue
                    # BUG-8 修复: 把真 md5 存进 m, 上传成功后直接取用, 不再重读文件 (TOCTOU + fake md5 回填)
                    m["_upload_md5"] = local_md5
                    # batch_kb 估算: content_b64 ≈ 4/3 * size; 加 3KB 其他字段, 留 buffer
                    est_kb = (len(data) // 1024) + 4
                    if batch_kb > 0 and batch_kb + est_kb > max(128, batch_max_kb * 16):
                        # 单个 batch 超过服务器可接受 body, 提前发送这一批.
                        # 回退 idx: 让当前 m 在下一批重新处理 (它还没加入 batch_files)
                        idx -= 1
                        break
                    batch_files.append({
                        "path": m["remote"],
                        "content_b64": base64.b64encode(data).decode(),
                        "mode": self._mode_str(m.get("mode", 0o644)),
                        # v4.38: mtime=0 是合法时间戳 (epoch), 不能用真值判断, 否则会丢
                        "mtime": int(m["mtime"]) if m.get("mtime") is not None else None,
                    })
                    batch_refs.append(m)
                    batch_kb += est_kb

                if batch_files:
                    _pend.append((batch_files, batch_refs))

            # ===== [AI-SAFE v1.1.2] 批次并发发送 (PUSH_CONCURRENCY 默认 4) =====
            # 大目录上传瓶颈在批间串行 RTT: 并发发批后主线程顺序应用结果 (snapshot 单线程更新, 无竞争)。
            if dry_run:
                for _bf, _refs in _pend:
                    batched += 1
                    ok_count += len(_refs)
                    self.log.info(f"  [DRY-RUN] 批量上传 {len(_refs)} 个小文件 [batch #{batched}]")
            elif _pend:
                _pconc = max(1, int(self.conf.get("push_concurrency") or 4))
                with ThreadPoolExecutor(max_workers=_pconc) as _pex:
                    _futs = {_pex.submit(self._retry_call, self.api.upload_batch, _bf): (_bf, _refs) for _bf, _refs in _pend}
                    for _fut in as_completed(_futs):
                        _bf, _refs = _futs[_fut]
                        batched += 1
                        _r = _fut.result()
                        _done_so_far = ok_count + fail_count + skip_count
                        self.log.info(f"  ↑ 上传批次 {batched} ({len(_refs)} 个, 累计 {_done_so_far}/{total}) ...")
                        if _r.get("ok"):
                            # 修复3: 检查 fail_idx, 不把部分失败的 batch 全部算成功
                            fail_idx = _r.get("fail_idx", [])
                            errors_map = _r.get("errors", {})
                            if fail_idx:
                                # 部分失败: 只更新成功的文件, 失败的降级为单文件重试
                                failed_in_batch = []
                                for i, m in enumerate(_refs):
                                    if i in fail_idx:
                                        fail_count += 1
                                        err = errors_map.get(i, "unknown") if isinstance(errors_map, dict) else "unknown"
                                        self._fail_counts[m["local"]] = self._fail_counts.get(m["local"], 0) + 1
                                        self.failed_list.append(m["remote"])
                                        failed_in_batch.append(f"{m['remote']}({err})")
                                        continue
                                    ok_count += 1
                                    # BUG-8 修复: 用上传前存的 _upload_md5, 不重读文件 (TOCTOU + fake md5 回填)
                                    data_md5 = m.pop("_upload_md5", "") or _real_md5(m)
                                    snapshot.update(m["local"], {
                                        "size": m["size"], "sha1": m["sha1"], "md5": data_md5,
                                        "remote": m["remote"], "mtime": m.get("mtime", 0.0),
                                        "mode": m.get("mode", 0), "source": m.get("source", ""),
                                    })
                                    self._fail_counts.pop(m["local"], None)
                                    if state: state.progress(m["remote"])
                                # 🆕 v4.23-FIX: 批次级进度 (移出逐文件循环, 避免重复刷屏且值不递增)
                                if progress_cb: progress_cb(ok_count + skip_count, total)
                                self.log.warn(f"  ⚠ 批量上传部分失败 {len(fail_idx)}/{len(_refs)} [batch #{batched}]: {', '.join(failed_in_batch[:5])}")
                            else:
                                # 全部成功
                                ok_count += len(_refs)
                                for m in _refs:
                                    # BUG-8 修复: 同上, 用 _upload_md5
                                    data_md5 = m.pop("_upload_md5", "") or _real_md5(m)
                                    snapshot.update(m["local"], {
                                        "size": m["size"], "sha1": m["sha1"], "md5": data_md5,
                                        "remote": m["remote"], "mtime": m.get("mtime", 0.0),
                                        "mode": m.get("mode", 0), "source": m.get("source", ""),
                                    })
                                    self._fail_counts.pop(m["local"], None)
                                    if state: state.progress(m["remote"])
                                # 🆕 v4.23-FIX: 批次级进度 (移出逐文件循环)
                                if progress_cb: progress_cb(ok_count + skip_count, total)
                                self.log.info(f"  ✅ 批量上传 {len(_refs)} 个小文件 [batch #{batched}]")
                        else:
                            # 批量失败: 降级为单文件上传, 避免"整体失败导致一个都没传"
                            self.log.warn(f"  ⚠ 批量上传失败 ({_r.get('error','')}), 降级为单文件 ({len(_refs)} 个)")
                            for _bm in _bf:
                                rp = _bm["path"]
                                try:
                                    raw = base64.b64decode(_bm["content_b64"])
                                except Exception:
                                    fail_count += 1; self.failed_list.append(rp); continue
                                r1 = self._retry_call(
                                    self.api.upload_file, rp, raw,
                                    None if len(_bm.get("mode", "")) != 4 else _bm["mode"],
                                    mtime=_bm.get("mtime"))
                                if r1.get("ok"):
                                    ok_count += 1
                                    mm = next((x for x in _refs if x.get("remote") == rp), None)
                                    if mm:
                                        snapshot.update(mm["local"], {
                                            "size": mm["size"], "sha1": mm["sha1"], "md5": hashlib.md5(raw).hexdigest(),
                                            "remote": mm["remote"], "mtime": mm.get("mtime", 0.0),
                                            "mode": mm.get("mode", 0), "source": mm.get("source", ""),
                                        })
                                        self._fail_counts.pop(mm["local"], None)
                                else:
                                    fail_count += 1
                                    self.failed_list.append(rp)

        # 单独上传大文件 (含 MD5 对比/重试/进度)
        for m in large_items:
            ok, detail, skipped = self._do_single(
                m, dry_run=dry_run,
                chunked_threshold=chunked_threshold, chunk_size=chunk_size,
                remote_md5=rmap.get(m["remote"], ""),
                snapshot=snapshot, state=state,
            )
            if skipped:
                skip_count += 1
            elif ok:
                ok_count += 1
                self._fail_counts.pop(m["local"], None)
            else:
                fail_count += 1
                self.failed_list.append(m["remote"])
            if progress_cb: progress_cb(ok_count + fail_count + skip_count, total)

        # FIX-symlink-order: 先传普通文件再传链接. 链接校验会检查目标当前状态;
        #   若目标此刻还是"指向 work_dir 外的旧链接"而本轮正好要用文件替换它,
        #   先传链接会被服务端误判 target_escapes_workdir. 文件先落盘后链接即可通过.
        if link_items:
            for m in link_items:
                ok, _detail, skipped = self._upload_link_one(
                    m, rmap, snapshot, dry_run=dry_run, state=state)
                if skipped:
                    skip_count += 1
                elif ok:
                    ok_count += 1
                    self._fail_counts.pop(m["local"], None)
                else:
                    fail_count += 1
                    self.failed_list.append(m["remote"])
                if progress_cb: progress_cb(ok_count + fail_count + skip_count, total)

        return (ok_count, fail_count, skip_count, batched)

    def _upload_link_one(self, m: dict, rmap: dict, snapshot: Snapshot | None,
                         dry_run: bool = False, state: SyncState | None = None
                         ) -> tuple[bool, str, bool]:
        """上传单个软链接 (只传 link_target, 不读目标内容)。"""
        lp = Path(m["local"])
        wd = Path(self.conf["watch_dir"]).resolve()
        if not os.path.lexists(str(lp)):
            return (False, f"软链接不存在: {lp}", False)
        if self._is_skipped(m["local"]):
            return (False, "skipped", True)
        f = link_finger(lp, wd)
        if f["size"] < 0 or not f.get("link_safe"):
            reason = f.get("link_reason") or "unsafe"
            self.log.warn(f"  ⊘ 跳过越界/绝对路径软链接: {lp.name} ({reason})")
            return (False, reason, True)
        link_target = f["link_target"]
        target_rel = f["link_target_rel"]
        local_md5 = f["md5"]
        _ri = rmap.get(m["remote"])
        rmd5 = _ri.get("md5", "") if isinstance(_ri, dict) else (_ri or "")
        snap_entry = {
            "size": 0, "sha1": f["sha1"], "md5": local_md5,
            "remote": m["remote"], "mtime": f["mtime"], "mode": f["mode"],
            "source": m.get("source", ""),
            "is_symlink": True, "link_target": link_target, "link_target_rel": target_rel,
        }
        if rmd5 and rmd5.lower() == local_md5.lower():
            if snapshot:
                snapshot.update(m["local"], snap_entry)
            if state: state.progress(m["remote"])
            self.log.debug(f"  ⊘ 远程已一致(软链接): {lp.name}")
            return (True, f"跳过(一致): {lp.name}", True)
        if dry_run:
            return (True, f"[DRY-RUN] 软链接 {lp.name} -> {_safe_text(link_target)}", False)
        last_err = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = self.api.upload_symlink(m["remote"], link_target,
                                            base_md5=rmd5 if rmd5 else "")
                if r.get("ok"):
                    if snapshot:
                        snapshot.update(m["local"], snap_entry)
                    if state: state.progress(m["remote"])
                    detail = f"软链接 {lp.name} -> {_safe_text(link_target)} → {m['remote']}"
                    self.log.debug(f"  ✅ {detail}")
                    return (True, detail, False)
                last_err = r.get("error") or r.get("message") or "unknown"
                # 参数/路径类错误重试无意义
                if r.get("code") in (1004, 1011):
                    break
            except Exception as e:
                last_err = str(e)
            if attempt < self.max_retries:
                self.log.debug(f"  ↻ 重试软链接 {attempt+1}/{self.max_retries} {lp.name}: {last_err}")
                time.sleep(self._backoff_delay(attempt))
        self._fail_counts[m["local"]] = self._fail_counts.get(m["local"], 0) + 1
        self.log.warn(f"  ❌ 软链接上传失败 {lp.name}: {last_err}")
        return (False, f"{lp.name}: {last_err}", False)

    def _mode_str(self, mode: int) -> str:
        try:
            return format(mode & 0o7777, "04o")
        except Exception:
            return "0644"

    def _retry_call(self, fn: Callable, *args, **kwargs) -> dict:
        """带退避重试的 API 调用。"""
        last = {"ok": False, "error": "no_attempt"}
        for attempt in range(self.max_retries + 1):
            try:
                last = fn(*args, **kwargs)
                if last.get("ok"):
                    return last
            except Exception as e:
                last = {"ok": False, "error": str(e)}
            if attempt < self.max_retries:
                _d = self._backoff_delay(attempt)
                # 反馈: 请求失败重试 — 告知失败原因与下一次延时, 避免 AI 误判卡死
                self.log.info(f"  ⏳ 请求失败({str(last.get('error',''))[:60]}) 第 {attempt+1}/{self.max_retries} 次重试, 等待 {_d}s ...")
                time.sleep(_d)
        return last

    def _do_single(self, m: dict, dry_run: bool = False, chunked_threshold: int = 0,
                   chunk_size: int = 0, remote_md5: str = "", snapshot: Snapshot | None = None,
                   state: SyncState | None = None) -> tuple[bool, str, bool]:
        """上传单个文件, 返回 (ok, detail, skipped)。"""
        lp = Path(m["local"])
        if not lp.is_file():
            return (False, f"文件不存在: {lp}", False)

        if self._is_skipped(m["local"]):
            self.log.debug(f"  ⊘ 跳过(失败超阈值): {lp.name}")
            return (False, "skipped", True)

        try:
            size = lp.stat().st_size
        except Exception as e:
            return (False, f"读取失败: {e}", False)

        # 流式计算 md5 (不把整个文件读进内存; 大文件上传内存峰值因此与文件大小无关)
        try:
            local_md5 = self._file_md5_stream(lp)
        except Exception as e:
            return (False, f"读取失败: {e}", False)

        # 上传前 MD5 对比: 远程已一致则跳过
        # remote_md5 可能是 md5 字符串, 也可能是完整 item dict (来自 _fetch_remote_md5_map)
        rmd5 = remote_md5.get("md5") if isinstance(remote_md5, dict) else remote_md5
        if rmd5 and rmd5.lower() == local_md5.lower() and not _mode_changed(snapshot.get(m["local"]) if snapshot else None, m):
            if snapshot:
                snapshot.update(m["local"], {
                    "size": m["size"], "sha1": m["sha1"], "md5": local_md5,
                    "remote": m["remote"], "mtime": m.get("mtime", 0.0),
                    "mode": m.get("mode", 0), "source": m.get("source", ""),
                })
            if state: state.progress(m["remote"])
            self.log.debug(f"  ⊘ 远程已一致: {lp.name}")
            return (True, f"跳过(一致): {lp.name}", True)

        if dry_run:
            return (True, f"[DRY-RUN] {lp.name} ({size} bytes)", False)

        # 上传前: 远程已有不同内容时, 先下载远程旧版本到本地备份目录
        #   (服务端也会 _create_backup, 这里额外存一份本地副本, 双保险)
        if rmd5 and rmd5.lower() != local_md5.lower() and size <= 32 * 1024 * 1024:
            try:
                rb = self.api.download_file(m["remote"])
                if rb.get("ok"):
                    rb_data = base64.b64decode(rb["content_b64"])
                    rb_md5 = hashlib.md5(rb_data).hexdigest()
                    if rb_md5.lower() == rmd5.lower():
                        self.backup.backup_remote(m["remote"], rb_data)
            except Exception as e:
                self.log.debug(f"  远程旧版本备份失败 (不影响上传): {m['remote']}: {e}")

        # 语法检查
        if self.conf.get("lint_enabled") and lp.suffix.lstrip(".") in self.conf.get("lint_exts", []):
            try:
                # P1-5 C6 fix: 传"远程路径(m[remote])"而不是本地绝对路径(str(lp))
                #    服务器 API 需要 remote_base + 相对路径（如 client-test/foo.php），传本地绝对路径会 not_found
                lint_path = m.get("remote") or _resolve_remote_path(self.conf, str(lp))
                r = self.api.lint(lint_path)
                if r.get("issues"):
                    # 只对真正 error 或 warning 级别输出警告 (info/template 类的统计不吓用户)
                    real_issues = [i for i in r["issues"] if (i.get("severity") or "") in ("error", "warning")]
                    if real_issues:
                        self.log.warn(f"  ⚠ 语法问题 {lp.name}: {len(real_issues)} 个")
            except Exception:
                pass

        mode_str = self._mode_str(m.get("mode", 0o644))
        size_kb = size / 1024
        # 大文件进度提示
        if size > 1024 * 1024:
            self.log.info(f"  ↑ 上传中 {lp.name} ({size_kb:.1f}KB) ...")

        # 分片上传 (带带宽限制与进度)
        use_chunked = (chunked_threshold > 0 and size > chunked_threshold
                       and self.conf.get("chunked_upload_enabled"))
        last_err = ""
        for attempt in range(self.max_retries + 1):
            try:
                # 重试时重新读取 (文件可能已被修改)
                if attempt > 0:
                    try:
                        size = lp.stat().st_size
                        local_md5 = self._file_md5_stream(lp)
                    except Exception as e:
                        last_err = f"重试读取失败: {e}"
                        break
                if use_chunked:
                    # 流式分块: 逐片从磁盘读取并上传, 峰值内存 ≈ 单片大小
                    r = self._upload_chunked_from_file(m["remote"], lp, chunk_size, lp.name,
                                                       mode=mode_str, mtime=m.get("mtime"))
                else:
                    data = lp.read_bytes()
                    if len(data) != size:
                        size = len(data); local_md5 = hashlib.md5(data).hexdigest()
                    r = self.api.upload_file(m["remote"], data, mode=mode_str, md5=local_md5,
                                             mtime=m.get("mtime"))
                if r.get("ok"):
                    if snapshot:
                        snapshot.update(m["local"], {
                            "size": m["size"], "sha1": m["sha1"], "md5": local_md5,
                            "remote": m["remote"], "mtime": m.get("mtime", 0.0),
                            "mode": m.get("mode", 0), "source": m.get("source", ""),
                        })
                    if state: state.progress(m["remote"])
                    detail = f"{lp.name} → {m['remote']} ({size} bytes)"
                    self.log.debug(f"  ✅ {detail}")
                    return (True, detail, False)
                last_err = r.get("error", "unknown")
            except Exception as e:
                last_err = str(e)
            if attempt < self.max_retries:
                self.log.debug(f"  ↻ 重试 {attempt+1}/{self.max_retries} {lp.name}: {last_err}")
                time.sleep(self._backoff_delay(attempt))

        self._fail_counts[m["local"]] = self._fail_counts.get(m["local"], 0) + 1
        detail = f"{lp.name}: {last_err}"
        self.log.warn(f"  ❌ {detail}")
        return (False, detail, False)

    def _file_md5_stream(self, lp: Path, bufsize: int = 1024 * 1024) -> str:
        """流式计算文件 md5, 峰值内存 ≈ bufsize (不随文件大小增长)。"""
        h = hashlib.md5()
        with open(lp, "rb") as f:
            for blk in iter(lambda: f.read(bufsize), b""):
                h.update(blk)
        return h.hexdigest()

    def _upload_chunked_from_file(self, path: str, lp: Path, chunk_size: int, name: str,
                                  mode: str = "", mtime: float | None = None) -> dict:
        """流式分片上传: 逐片从磁盘读取, 峰值内存 ≈ 单片 (不再整文件驻留内存)。"""
        total_size = lp.stat().st_size
        total_md5 = self._file_md5_stream(lp)
        r = self.api.chunked_init(path, total_size, total_md5, chunk_size,
                                  mode=mode, mtime=mtime)
        if not r.get("ok"):
            return r
        upload_id = r["upload_id"]
        total_chunks = r["total_chunks"]
        last_progress = 0
        sent = 0
        with open(lp, "rb") as f:
            for i in range(total_chunks):
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                r = self.api.chunked_put(upload_id, i, chunk_data)
                if not r.get("ok"):
                    return r
                sent += len(chunk_data)
                if self.bandwidth_bps > 0 and chunk_size > 0:
                    sleep_s = len(chunk_data) / self.bandwidth_bps
                    if sleep_s > 0:
                        time.sleep(min(sleep_s, 10.0))
                pct = int((i + 1) * 100 / total_chunks)
                if pct - last_progress >= 10 or i + 1 == total_chunks:
                    self.log.info(f"  ↑ {name} 分片 {i+1}/{total_chunks} ({pct}%)")
                    last_progress = pct
        if sent != total_size:
            # 文件在上传过程中被改动 (变大/变小) → 让上层重试
            return {"ok": False, "error": "file_changed_during_upload",
                    "message": "file size changed while uploading (%d != %d)" % (sent, total_size)}
        return self.api.chunked_assemble(upload_id)

    def _upload_chunked_with_limit(self, path: str, data: bytes, chunk_size: int, name: str) -> dict:
        """分片上传, 支持带宽限制与进度报告。"""
        total_size = len(data)
        total_md5 = hashlib.md5(data).hexdigest()
        r = self.api.chunked_init(path, total_size, total_md5, chunk_size)
        if not r.get("ok"):
            return r
        upload_id = r["upload_id"]
        total_chunks = r["total_chunks"]
        last_progress = 0
        for i in range(total_chunks):
            start = i * chunk_size
            end = min(start + chunk_size, total_size)
            chunk_data = data[start:end]
            r = self.api.chunked_put(upload_id, i, chunk_data)
            if not r.get("ok"):
                return r
            # 带宽限制: 按本片大小与限速计算睡眠时间
            if self.bandwidth_bps > 0 and chunk_size > 0:
                sleep_s = len(chunk_data) / self.bandwidth_bps
                if sleep_s > 0:
                    time.sleep(min(sleep_s, 10.0))
            # 进度: 每 10% 报告一次
            pct = int((i + 1) * 100 / total_chunks)
            if pct - last_progress >= 10 or i + 1 == total_chunks:
                self.log.info(f"  ↑ {name} 分片 {i+1}/{total_chunks} ({pct}%)")
                last_progress = pct
        return self.api.chunked_assemble(upload_id)

# ---------------------------------------------------------------------------
# 11. 双向拉取器 (v4.0: 本地备份 + 原子写入 + mtime 恢复)
# ---------------------------------------------------------------------------
class TwoWayPuller:
    def __init__(self, api: ApiClient, log: Logger, conf: dict, backup: LocalBackup):
        self.api = api
        self.log = log
        self.conf = conf
        self.backup = backup

    def pull_file(self, local_path: Path, remote_path: str, remote_md5: str,
                  snapshot: Snapshot, dry_run: bool = False,
                  remote_mtime: float | None = None, remote_mode: int | None = None,
                  state: SyncState | None = None) -> tuple[bool, str]:
        """从远程拉取文件 (含本地备份 + 原子写入 + mtime 恢复)。"""
        if dry_run:
            return (True, f"[DRY-RUN] PULL {remote_path}")

        # 远程元信息可能是 "0755" 字符串 (list/checksum) 或 int, 统一转成数值;
        # 否则 os.chmod(str) 会 TypeError 并被 except 静默吞掉 → 权限丢失.
        remote_mode_int = None
        if remote_mode is not None:
            try:
                remote_mode_int = int(str(remote_mode), 8) if isinstance(remote_mode, str) \
                    else int(remote_mode)
                remote_mode_int &= 0o777
            except Exception:
                remote_mode_int = None
        remote_mtime_int = None
        if remote_mtime not in (None, ""):
            try:
                remote_mtime_int = int(float(remote_mtime))
            except Exception:
                remote_mtime_int = None

        # 拉取前: 本地文件存在且与远程内容不同时, 无条件备份 (不依赖 snapshot)
        #   旧逻辑 backup_if_modified 依赖 snapshot 基线, snapshot 丢失/损坏时不备份 → 数据丢失
        if os.path.islink(str(local_path)):
            # FIX-symlink-pull: 远程是普通文件而本地是链接 → 链接本身会被 os.replace 替换,
            #   必须备份链接 (raw target), 不能只按目标内容 md5 决定是否备份.
            try:
                self.backup._do_backup_link(local_path)
            except Exception:
                pass
        elif local_path.is_file():
            try:
                cur_local_md5 = md5_file(local_path)
            except Exception:
                cur_local_md5 = ""
            if cur_local_md5 and cur_local_md5.lower() != remote_md5.lower():
                self.backup._do_backup(local_path)

        # PERF-v4.25: 优先流式下载 (边收边解码落盘, 峰值内存 O(块)); 失败再走整包老路径
        got_md5 = ""
        _sr = self.api.download_to_file(remote_path, local_path, remote_md5)
        if _sr.get("ok"):
            got_md5 = _sr.get("md5", "")
            # 流式路径已在 _consume_download 内恢复 mode/mtime; 旧服务端无元信息时
            # 用调用方传入的 remote_mode/remote_mtime 兜底.
            if _sr.get("mode") is None and remote_mode_int is not None:
                try: os.chmod(local_path, remote_mode_int)
                except Exception: pass
            if _sr.get("mtime") is None and remote_mtime_int is not None:
                try: os.utime(local_path, (remote_mtime_int, remote_mtime_int))
                except Exception: pass
        else:
            r = self.api.download_file(remote_path)
            if not r.get("ok"):
                return (False, f"下载失败: {_sr.get('error') or r.get('error', '')}")

            try:
                data = base64.b64decode(r["content_b64"])
            except Exception as e:
                return (False, f"解码失败: {e}")

            got_md5 = hashlib.md5(data).hexdigest()
            if got_md5.lower() != remote_md5.lower():
                return (False, f"MD5 不匹配: expect={remote_md5} got={got_md5}")

            # 原子写入 + mtime/mode 恢复
            if not atomic_write_bytes(local_path, data, mtime=remote_mtime_int,
                                      mode=remote_mode_int):
                return (False, f"写入失败: {local_path}")
        f = finger(local_path)
        snapshot.update(str(local_path), {
            "size": f["size"], "sha1": f["sha1"], "md5": got_md5,
            "remote": remote_path,
            "mtime": remote_mtime_int if remote_mtime_int is not None else f["mtime"],
            "mode": remote_mode_int if remote_mode_int is not None else f["mode"],
            "source": f"pull:{remote_path}",
        })
        if state: state.progress(remote_path)
        return (True, f"PULL {remote_path} → {local_path}")

# ---------------------------------------------------------------------------
# 12. 同步引擎 (v4.0: 替代 Watcher, 编排全部功能)
# ---------------------------------------------------------------------------
class SyncEngine:
    def __init__(self, conf: dict, log: Logger, api: ApiClient):
        self.conf = conf
        self.log = log
        self.api = api
        self.watch_dir = Path(conf["watch_dir"])
        self.remote_base = conf["remote_base"]

        self.snapshot_file = self.watch_dir / ".sync_snapshot.json"
        self.summary_file = self.watch_dir / ".sync_summary.json"
        self.state_file = self.watch_dir / ".sync_state.json"
        self.conflicts_file = self.watch_dir / ".sync_conflicts.json"

        self.snapshot = Snapshot(self.snapshot_file, log=self.log)
        self.backup = LocalBackup(self.watch_dir, conf["local_backup_dir"],
                                  conf["local_backup_keep"], log)
        self.conflict = ConflictDetector(conf["conflict_strategy"], self.conflicts_file, log)
        self.state = SyncState(self.state_file, log)
        self.uploader = Uploader(api, log, conf)
        self.uploader.backup = self.backup  # 修复: Uploader 需要访问 backup 做 push 前远程旧版本备份
        self.puller = TwoWayPuller(api, log, conf, self.backup)

        self.pending_up: dict[str, tuple[dict, float]] = {}
        self.pending_del: dict[str, tuple[dict, float]] = {}
        self.running = True
        self.last_two_way_scan = 0.0
        self.last_snapshot_cleanup = 0.0
        # 远程 MD5 缓存 (timestamp, map)
        self._rmd5_cache: tuple[float, dict] = (0.0, {})
        # 目录同步缓存: rel -> "0755" (watch 模式避免每轮重复 mkdir)
        self._dir_cache: dict[str, str] = {}
        self._last_dir_scan = 0.0
        self._last_dir_reconcile = 0.0
        # v4.38: two-way watch 上次拉取远程目录状态的时间 (节流)
        self._last_remote_dir_pull = 0.0
        self.current_phase = "idle"

        for s in (signal.SIGINT, signal.SIGTERM):
            try: signal.signal(s, self._on_signal)
            except Exception: pass
        atexit.register(self._flush)

    def _on_signal(self, signum, frame):
        self.log.info(f"收到信号 {signum}, 准备退出 (phase={self.current_phase})...")
        self.running = False

    def _flush(self):
        # BUG-11 修复: 必须 force=True! save(force=False) 有 30s 节流,
        # --once 模式小项目同步 <30s, 命中节流直接跳过 → 状态丢失 → 下次重复上传.
        try: self.snapshot.save(force=True)
        except Exception: pass

    # ---- 远程信息获取 ----
    def _fetch_remote_md5_map(self, force: bool = False, ttl: float = 30.0) -> dict:
        """批量获取远程文件 MD5 (一次 checksum 调用覆盖整个目录)。带 TTL 缓存。
        返回 {path: md5_string}。
        修复5: 过滤掉匹配 IGNORE 规则的文件 (如 ai-sync/*, cache/*), 避免 checksum 卡死。"""
        ts, m = self._rmd5_cache
        now = time.time()
        if not force and m and (now - ts) < ttl:
            return m
        cs_path = self.remote_base or "."
        # 反馈: 全量 checksum 2.6s+ 无输出会被误判卡死, 请求前打一行
        self.log.info(f"  [checksum] 获取远程 MD5 清单 ({cs_path}) ...")
        r = self.api.checksum_remote(cs_path, "md5")
        if not r.get("ok"):
            self.log.debug(f"checksum 远程失败: {r.get('error', '')}")
            return m  # 返回旧缓存
        self.log.info(f"  [checksum] 远程清单就绪 ({len(r.get('items', []))} 项)")
        out: dict[str, str] = {}
        ignore_globs = self.conf.get("ignore", [])
        skipped = 0
        for it in r.get("items", []):
            p = it.get("path", "")
            # 修复5: 跳过匹配 IGNORE 规则的远程文件 (如 ai-sync/*, cache/*)
            if any(glob_match(g, p) for g in ignore_globs):
                skipped += 1
                continue
            out[p] = it  # 保存完整 item (含 md5/mtime/size)
        if skipped:
            self.log.debug(f"checksum: 过滤 {skipped} 个 IGNORE 文件")
        self._rmd5_cache = (now, out)
        return out

    def _fetch_remote_dirs(self) -> set[str]:
        """递归获取远程目录路径集合 (用于空目录同步/清理)。"""
        dirs: set[str] = set()
        offset = 0
        list_path = self.remote_base or "."
        while True:
            r = self.api.list_remote(list_path, recursive=True, offset=offset, limit=1000,
                                     max_depth=0)
            if not r.get("ok"):
                break
            for it in r.get("items", []):
                if it.get("type") == "dir" or it.get("is_dir"):
                    dirs.add(it.get("path", ""))
            if not r.get("has_more"):
                break
            # BUG-12 修复: 防 limit=0 死循环, offset 至少前进 max(1, items_returned)
            offset += max(1, len(r.get("items", [])))
            if offset > 100000:  # 安全上限
                break
        return dirs

    # ---- 摘要 ----
    def _save_summary(self, up_ok, up_fail, up_skip, del_ok, del_fail,
                      pull_ok, pull_fail, conflict_n, batched, dry_run):
        s = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "up_ok": up_ok, "up_fail": up_fail, "up_skip": up_skip,
            "del_ok": del_ok, "del_fail": del_fail,
            "pull_ok": pull_ok, "pull_fail": pull_fail,
            "conflicts": conflict_n,
            "batched_requests": batched,
            "dry_run": bool(dry_run),
        }
        parts = []
        if s["up_ok"]: parts.append(f"↑{s['up_ok']}")
        if s["up_skip"]: parts.append(f"⊘{s['up_skip']}")
        if s["up_fail"]: parts.append(f"✗{s['up_fail']}")
        if s["pull_ok"]: parts.append(f"↓{s['pull_ok']}")
        if s["pull_fail"]: parts.append(f"✗{s['pull_fail']}")
        if s["del_ok"]: parts.append(f"DEL{s['del_ok']}")
        if s["del_fail"]: parts.append(f"✗{s['del_fail']}")
        if s["conflicts"]: parts.append(f"⚡{s['conflicts']}")
        if s["batched_requests"]: parts.append(f"[batched={s['batched_requests']}]")
        s["line"] = " ".join(parts) or "无变化"
        try: self.summary_file.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception: pass
        self.log.info(f"本轮摘要: {s['line']}")

    # ---- 目录同步 (空目录 + 目录 mode) ----
    def _sync_empty_dirs(self, dry_run: bool) -> int:
        """本地目录(含空目录) → 远程目录, 并回填目录 mode。

        watch 模式走 _dir_cache: 已同步过且 mode 未变的目录不再发请求。
        """
        return sync_local_dirs(self.api, self.conf, self.log,
                               roots=None, dry_run=dry_run, cache=self._dir_cache)

    def _dir_cache_invalidate(self, items: list[dict]) -> None:
        """上传会由服务端自动建父目录 (默认 0755), 必须让缓存中的父目录链失效,
        下一轮目录同步才会重新回填真实 mode。"""
        for m in items:
            try:
                rel = _rel_under_watch(Path(m["local"]), self.watch_dir)
            except Exception:
                continue
            if not rel:
                continue
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            while parent:
                self._dir_cache.pop(parent, None)
                parent = parent.rsplit("/", 1)[0] if "/" in parent else ""

    def _local_dirs_dirty(self) -> bool:
        """本地目录集合/mode 相对缓存是否有变化 (新增空目录、chmod)。

        只做本地 walk, 不发请求; 用于让 watch 在没有任何文件事件时也能同步目录状态。
        """
        try:
            dirs = scan_local_dirs(self.watch_dir, self.conf["ignore"])
        except Exception:
            return False
        for d in dirs:
            if self._dir_cache.get(d["rel"]) != format(d["mode"] & 0o777, "04o"):
                return True
        return False

    def _cleanup_remote_empty_dirs(self, dry_run: bool) -> int:
        """删除同步后清理远程空目录 (deepest first)。"""
        if not self.conf.get("delete_sync"):
            return 0
        try:
            remote_dirs = self._fetch_remote_dirs()
        except Exception:
            return 0
        removed = 0
        # 按路径深度倒序 (先删最深层)
        for rp in sorted(remote_dirs, key=lambda x: -x.count("/")):
            if dry_run:
                removed += 1
                continue
            r = self.api.delete_remote(rp)
            if r.get("ok"):
                removed += 1
        if removed:
            self.log.info(f"  远程空目录清理: 删除 {removed} 个")
        return removed

    # ---- 冲突处理 ----
    def _resolve_conflict(self, m: dict, remote_info: dict) -> str:
        """处理单个冲突, 返回动作 (local/remote/skip/backup_both)。"""
        local_path = Path(m["local"])
        remote_path = m["remote"]
        remote_md5 = remote_info.get("md5", "")
        snap = self.snapshot.get(m["local"])
        snap_md5 = snap.get("md5", "")
        # BUG-9 修复: m["md5"] 可能是 "f:..." 占位符, 必须重算真 md5
        local_md5 = _real_md5(m, local_path)

        strategy = self.conflict.strategy
        action = strategy
        if strategy == "local":
            # 本地优先: 强制上传 (加入待上传, 由上传流程处理)
            action = "local"
        elif strategy == "remote":
            # 远程优先: 备份本地后拉取远程
            self.backup._do_backup(local_path)
            pok, _ = self.puller.pull_file(local_path, remote_path, remote_md5, self.snapshot,
                                  remote_mtime=remote_info.get("mtime"),
                                  remote_mode=remote_info.get("mode"))
            action = "remote" if pok else "remote_fail"
        elif strategy == "skip":
            # 跳过, 不动
            action = "skip"
        elif strategy == "backup_both":
            # 备份两者: 本地版本进备份, 远程版本覆盖本地
            self.backup._do_backup(local_path)
            pok, _ = self.puller.pull_file(local_path, remote_path, remote_md5, self.snapshot,
                                  remote_mtime=remote_info.get("mtime"),
                                  remote_mode=remote_info.get("mode"))
            action = "backup_both" if pok else "backup_both_fail"
        self.conflict.record(str(local_path), remote_path, snap_md5,
                             local_md5, remote_md5, action)
        return action

    # ---- 双向同步 ----
    def _two_way_sync(self, mon: list[dict], dry_run: bool,
                      state: SyncState | None) -> tuple[int, int, int]:
        """双向扫描: 拉取远程更新 + 处理冲突, 返回 (pull_ok, pull_fail, conflict_n)。"""
        remote_map = self._fetch_remote_md5_map(force=True)
        pull_ok = pull_fail = conflict_n = 0
        for m in mon:
            rinfo = remote_map.get(m["remote"])
            if not rinfo:
                continue  # 远程没有 → 上传流程处理
            remote_md5 = rinfo.get("md5", "")
            if not remote_md5:
                continue
            local_md5 = _real_md5(m, m["local"])
            if remote_md5.lower() == local_md5.lower():
                continue  # 已一致
            # 冲突检测
            if self.conflict.detect(m["local"], self.snapshot, local_md5, remote_md5):
                conflict_n += 1
                action = self._resolve_conflict(m, rinfo)
                if action in ("remote", "backup_both"):
                    pull_ok += 1
                elif action.endswith("_fail"):
                    pull_fail += 1
                continue
            # 仅本地改 (远程未改) → 不拉取, 交给上传流程处理, 避免覆盖本地修改
            snap = self.snapshot.get(m["local"])
            snap_md5 = snap.get("md5", "") if snap else ""
            if snap_md5 and snap_md5.lower() == remote_md5.lower():
                continue
            # 远程有更新且本地未改 → 拉取
            ok, det = self.puller.pull_file(
                Path(m["local"]), m["remote"], remote_md5, self.snapshot,
                dry_run=dry_run,
                remote_mtime=rinfo.get("mtime"),
                remote_mode=rinfo.get("mode"), state=state,
            )
            flag = "✅" if ok else "❌"
            self.log.info(f"  {flag} ↓ PULL {Path(m['local']).name:<40s} {det}")
            if ok: pull_ok += 1
            else: pull_fail += 1
        return (pull_ok, pull_fail, conflict_n)

    # ---- 主流程 ----
    def run(self, once: bool = False, dry_run: bool = False) -> int:
        self.log.info(f"===== ai-sync {APP_VERSION} 启动 =====")
        self.log.info(f"  API URL  = {self.conf['api_url']}")
        self.log.info(f"  Watch    = {self.conf['watch_dir']}")
        self.log.info(f"  Remote   = {self.conf['remote_base']}")
        self.log.info(f"  Two-way  = {self.conf['two_way']}  Delete-sync = {self.conf['delete_sync']}  Dry-run = {dry_run}")
        self.log.info(f"  冲突策略 = {self.conf['conflict_strategy']}  最大重试 = {self.conf['max_retries']}  带宽限速 = {self.conf['bandwidth_limit_kb']}KB/s")

        # 断点恢复检测
        if self.state.is_interrupted():
            info = self.state.interrupted_info()
            self.log.warn(f"⚠ 检测到上次未完成同步: phase={info['phase']} 待处理={len(info['pending'])}")

        # ping
        pong = self.api.ping()
        if not pong.get("ok"):
            self.log.err(f"ping 失败: {pong.get('error', '')}")
            return 1
        self.log.info(f"  ping OK: version={pong.get('version')} exec={pong.get('exec_enabled')} probe_dir={pong.get('probe_dir')}")

        # 首次全量同步
        self.current_phase = "scan"
        mon = scan_watch_dir(self.watch_dir, self.remote_base,
                             self.conf["ignore"], self.conf["allow_ext"])
        self.log.info(f"监控文件数 = {len(mon)}")

        up_todo, del_todo = compute_todos(mon, self.snapshot, self.conf["ignore"])

        # 双向: 首次全量拉取 + 冲突处理
        pull_ok = pull_fail = conflict_n = 0
        if self.conf["two_way"]:
            self.current_phase = "two_way"
            self.state.begin("two_way", [m["remote"] for m in mon])
            self.log.info("two-way: 首次全量扫描远程...")
            pull_ok, pull_fail, conflict_n = self._two_way_sync(mon, dry_run, self.state)
            self.state.clear()

        self.log.info(f"首次扫描: 上传 {len(up_todo)} 删除 {len(del_todo)} pull {pull_ok} 冲突 {conflict_n}")

        # 删除同步
        del_ok = del_fail = 0
        if del_todo:
            self.current_phase = "delete"
            del_enabled = bool(self.conf.get("delete_sync"))
            self.state.begin("delete", [d["remote"] for d in del_todo])
            for d in del_todo:
                if dry_run:
                    del_ok += 1
                    continue
                if not del_enabled:
                    # DELETE_SYNC=false: 保留远程文件, 仅从快照移除本地追踪 (避免重复检测)
                    self.snapshot.remove(d["local"])
                    self.state.progress(d["remote"])
                    self.log.info(f"  ⊘ SKIP-DEL {d['remote']}  (delete_sync=false, 远程保留)")
                    continue
                r = self.api.delete_remote(d["remote"])
                flag = "✅" if r.get("ok") else "❌"
                self.log.info(f"  {flag} DEL {d['remote']}    ({d['source']})")
                if r.get("ok"):
                    del_ok += 1
                    self.snapshot.remove(d["local"])
                    self.state.progress(d["remote"])
                else:
                    del_fail += 1
            self.state.clear()

        # 远程空目录清理 (删除同步后)
        if self.conf.get("delete_sync") and not dry_run:
            self._cleanup_remote_empty_dirs(dry_run)

        # 上传 (含 MD5 对比/重试/进度)
        up_ok = up_fail = up_skip = batched = 0
        if up_todo:
            self.current_phase = "upload"
            self.state.begin("upload", [m["remote"] for m in up_todo])
            rmap = self._fetch_remote_md5_map(force=True)
            up_ok, up_fail, up_skip, batched = self.uploader.upload_items(
                up_todo, self.snapshot, dry_run=dry_run,
                remote_md5_map=rmap, state=self.state,
            )
            self.state.clear()

        # 上传成功可能由服务端自动创建了父目录 (默认 0755) → 让目录缓存失效后回填 mode
        if up_ok:
            self._dir_cache_invalidate(up_todo)

        # 目录同步 (上传完成后回填 mode, 避免上传自动建目录的默认权限覆盖)
        self.current_phase = "mkdir"
        self._sync_empty_dirs(dry_run)

        # 快照清理 (定期)
        self._maybe_cleanup_snapshot(mon)

        self._flush()
        self._save_summary(up_ok, up_fail, up_skip, del_ok, del_fail,
                           pull_ok, pull_fail, conflict_n, batched, dry_run)
        self.current_phase = "idle"

        if once:
            self.log.info("--once 完成, 退出")
            return 0

        # watch 循环
        self.log.info(f"进入 watch 循环: 轮询={self.conf['poll_interval']}s 去抖={self.conf['debounce_sec']}s")
        last_status = 0.0
        tick = 0

        while self.running:
            # BUG-31 修复 (审计 Finding 9): poll_interval=0 会让守护 100% CPU 死循环
            # (time.sleep(0) 不让出 CPU), rglob 全树扫描 + 100% CPU = 看似卡死. 加 0.05s 下限.
            time.sleep(max(0.05, float(self.conf["poll_interval"])))
            tick += 1

            # 修复 BUG-2 (守护崩溃): engine.run 的 watch 循环无异常兜底,
            #   网络抖动 / JSON 解析失败 / 服务端异常返回 / snapshot 损坏 任一未捕获异常都会让守护直接崩溃退出.
            #   cmd_watch_start 已经有两层 try-except, 这里也要包一层, 保证守护进程长跑自愈.
            try:
                # 心跳日志 (含当前状态)
                if time.time() - last_status > 60:
                    self.log.info(
                        f"heartbeat tick={tick} phase={self.current_phase} "
                        f"up_pending={len(self.pending_up)} del_pending={len(self.pending_del)} "
                        f"snap={len(self.snapshot.data)}"
                    )
                    last_status = time.time()

                self.current_phase = "scan"
                mon = scan_watch_dir(self.watch_dir, self.remote_base,
                                     self.conf["ignore"], self.conf["allow_ext"])
                up_todo, del_todo = compute_todos(mon, self.snapshot, self.conf["ignore"])
                now = time.time()

                # 目录变化检测 (新增空目录 / chmod): 没有文件事件时也要触发目录同步.
                # 本地 walk 有成本, 限制为最多每秒一次.
                # 另外每 60s 用远程目录列表校准缓存, 修复其他 AI 删除远程目录后的自愈.
                dir_dirty = False
                _dir_reconcile_s = float(self.conf.get("dir_reconcile_interval", 60.0) or 0)
                if _dir_reconcile_s > 0 and now - self._last_dir_reconcile >= _dir_reconcile_s:
                    self._last_dir_reconcile = now
                    try:
                        _dropped = reconcile_dir_cache(self.api, self.conf, self._dir_cache)
                        if _dropped:
                            self.log.info(f"  目录缓存校准: 远端缺失 {_dropped} 个目录, 将重新创建")
                    except Exception:
                        pass
                if now - self._last_dir_scan >= 1.0:
                    self._last_dir_scan = now
                    dir_dirty = self._local_dirs_dirty()

                # two-way 周期扫描
                if self.conf["two_way"] and (now - self.last_two_way_scan) >= self.conf["twoway_scan_interval"]:
                    self.last_two_way_scan = now
                    self.current_phase = "two_way"
                    p_ok, p_fail, c_n = self._two_way_sync(mon, dry_run, None)
                    pull_ok += p_ok
                    pull_fail += p_fail
                    conflict_n += c_n

                # 更新待处理队列
                for d in up_todo:
                    if d["remote"] in self.pending_up:
                        self.pending_up[d["remote"]] = (d, self.pending_up[d["remote"]][1])
                    else:
                        self.pending_up[d["remote"]] = (d, now)
                for d in del_todo:
                    if d["remote"] in self.pending_del:
                        self.pending_del[d["remote"]] = (d, self.pending_del[d["remote"]][1])
                    else:
                        self.pending_del[d["remote"]] = (d, now)

                ready_up = [k for k, (_, t0) in self.pending_up.items() if (now - t0) >= self.conf["debounce_sec"]]
                ready_del = [k for k, (_, t0) in self.pending_del.items() if (now - t0) >= self.conf["debounce_sec"]]

                if not (ready_up or ready_del or dir_dirty):
                    continue

                self.log.info(f"本轮: ready_upload {len(ready_up)} ready_delete {len(ready_del)}")
                self.current_phase = "process"
                succ = fail = skip = batched_round = 0

                # 删除
                del_enabled = bool(self.conf.get("delete_sync"))
                for k in ready_del:
                    item, _ = self.pending_del.pop(k)
                    if dry_run:
                        succ += 1
                        continue
                    if not del_enabled:
                        # DELETE_SYNC=false: 保留远程文件, 仅从快照移除本地追踪
                        self.snapshot.remove(item["local"])
                        self.log.info(f"  ⊘ SKIP-DEL {item['remote']}  (delete_sync=false, 远程保留)")
                        continue
                    r = self.api.delete_remote(item["remote"])
                    flag = "✅" if r.get("ok") else "❌"
                    self.log.info(f"  {flag} DEL {item['remote']}    ({item['source']})")
                    if r.get("ok"):
                        succ += 1
                        self.snapshot.remove(item["local"])
                    else:
                        fail += 1
                        self.pending_del[k] = (item, now + 20.0)

                # 上传 (重新 finger + MD5 对比)
                items = []
                for k in ready_up:
                    item, _ = self.pending_up.pop(k)
                    lp = Path(item["local"])
                    f = finger(lp)
                    if f["size"] < 0:
                        self.log.warn(f"文件在去抖窗口内被删, 转为删除: {lp}")
                        self.pending_del[item["remote"]] = (
                            {"local": item["local"], "remote": item["remote"],
                             "source": item["source"] + "(reclassify)", "detected_at": now}, now)
                        continue
                    item = {**item, "size": f["size"], "mtime": f["mtime"],
                            "mtime_ns": f["mtime_ns"], "sha1": f["sha1"],
                            "md5": f["md5"], "mode": f["mode"]}
                    items.append(item)

                if items:
                    self.current_phase = "upload"
                    self.state.begin("upload", [m["remote"] for m in items])
                    rmap = self._fetch_remote_md5_map(force=False, ttl=30.0)
                    ok_count, fail_count, skip_count, b = self.uploader.upload_items(
                        items, self.snapshot, dry_run=dry_run,
                        remote_md5_map=rmap, state=self.state,
                    )
                    succ += ok_count
                    fail += fail_count
                    skip += skip_count
                    batched_round = b
                    self.state.clear()
                    if items:
                        self._dir_cache_invalidate(items)

                # 周期性空目录同步
                self._sync_empty_dirs(dry_run)
                self._maybe_cleanup_snapshot(mon)

                self._flush()
                if succ or fail or skip:
                    self._save_summary(succ, fail, skip, len(ready_del), 0,
                                       0, 0, 0, batched_round, dry_run)
                self.current_phase = "idle"
            except Exception as e:
                # 守护进程长跑自愈: 单轮异常不退出, 记录后等下一轮.
                # 状态可能不一致 (snapshot / pending), 但远比直接崩溃让 AI 失去同步守护好.
                self.log.warn(f"⚠ watch 循环本轮异常 (将持续重试): {type(e).__name__}: {e}")
                try:
                    self.state.clear()
                except Exception:
                    pass
                self.current_phase = "idle"
                # 异常后短暂 sleep, 避免服务端持续 5xx 时本地 busy-loop
                time.sleep(min(5.0, float(self.conf.get("poll_interval", 1.5))))

        self.log.info("watch 循环结束, 再见")
        return 0

    def _maybe_cleanup_snapshot(self, mon: list[dict]):
        """定期清理快照中已不存在的本地文件条目。"""
        interval = self.conf.get("snapshot_cleanup_interval", 3600)
        if interval <= 0:
            return
        now = time.time()
        if (now - self.last_snapshot_cleanup) < interval:
            return
        self.last_snapshot_cleanup = now
        current = set(str(m["local"]) for m in mon)
        removed = self.snapshot.cleanup(current)
        if removed > 0:
            self.log.info(f"  快照清理: 移除 {removed} 个残留条目")

# ---------------------------------------------------------------------------
# 13. CLI 子命令
# ---------------------------------------------------------------------------
def cmd_status(conf: dict, log: Logger, api: ApiClient) -> int:
    # 🆕 FIX-accept-v4.6: 顶部验收行 — README 要求 "--status 必须看到 ok=true 和 scope=",
    #   但此前 cmd_status 只列文件状态, 验收信息只在 --selfcheck, 新 AI 按文档找不到验收标志。
    #   现在顶部补一行 (连接失败时也明确提示用 --selfcheck 排查)。
    pong = api.ping()
    if not pong.get("ok"):
        print(f"ok=false 连接失败: {pong.get('error', '')} (用 --selfcheck 排查)", file=sys.stderr)
        return 2
    mon = scan_watch_dir(Path(conf["watch_dir"]), conf["remote_base"], conf["ignore"], conf["allow_ext"])
    snap_path = Path(conf["watch_dir"]) / ".sync_snapshot.json"
    snap = Snapshot(snap_path)
    if "--json" in sys.argv:
        # 🆕 v4.12.3-JSON: status 结构化 (AI 直接拿 counts/dirty 清单)
        _st_cnt = {"ok": 0, "dirty": 0, "missing": 0}
        _dirty_list = []
        for m in mon:
            prev = snap.get(m["local"])
            dirty = _snap_has_fake_md5(prev) or (prev.get("size") != m["size"] or prev.get("sha1") != m["sha1"]
                                                 or _mode_changed(prev, m))
            st = "missing" if m["size"] < 0 else ("dirty" if dirty else "ok")
            _st_cnt[st] += 1
            if st == "dirty":
                _dirty_list.append(m["remote"])
        print(json.dumps({"command": "status",
                          "ok": True, "scope": conf.get('scope') or pong.get('scope', '?'),
                          "server_version": pong.get('version', '?'),
                          "exec": pong.get('exec_enabled', '?'),
                          "work_dir": pong.get('work_dir', '?'),
                          "monitored": len(mon), "snapshot_entries": len(snap.data),
                          "counts": _st_cnt, "dirty_files": _dirty_list[:500]},
                         ensure_ascii=False))
        return 0
    if "--brief" not in sys.argv:   # --brief 只给一行摘要, 顶部验收行跳过
        print(f"ok=true scope={conf.get('scope') or pong.get('scope', '?')} server_version={pong.get('version', '?')} exec={pong.get('exec_enabled', '?')} work_dir={pong.get('work_dir', '?')}")
        print()
    # FIX-v4.33 (P1): status 此前把每个监控文件都逐行打印, watch 目录 187 个文件时输出
    #   17.9KB / 192 行 —— 其中绝大多数是 OK, 而 AI 真正要的只是"哪些待推送/缺失"。
    #   新行为: 默认只列非 OK 行; --full 才全列; --brief 只给一行 (此前 --brief 对
    #   status 完全不生效)。注意 --json 分支本来就是精简结构, 不受影响。
    _full  = "--full" in sys.argv
    _brief = "--brief" in sys.argv
    _rows, _cnt = [], {"OK": 0, "DIRTY": 0, "MISSING": 0}
    for m in mon:
        lp = Path(m["local"])
        prev = snap.get(m["local"])
        # BUG-26: snapshot.md5 是 fake 占位符 → 强制 dirty (未真正同步过).
        dirty = _snap_has_fake_md5(prev) or (prev.get("size") != m["size"] or prev.get("sha1") != m["sha1"]
                                             or _mode_changed(prev, m))
        st = "MISSING" if m["size"] < 0 else ("DIRTY" if dirty else "OK")
        _cnt[st] = _cnt.get(st, 0) + 1
        _rows.append((st, m, lp))
    _need = [r for r in _rows if r[0] != "OK"]
    if _brief:
        print(f"ok=true scope={conf.get('scope') or pong.get('scope', '?')} monitored={len(mon)} "
              f"OK={_cnt['OK']} DIRTY={_cnt['DIRTY']} MISSING={_cnt['MISSING']}")
        return 0
    print(f"监控文件数 = {len(mon)}  快照条目 = {len(snap.data)}   "
          f"OK={_cnt['OK']}  DIRTY={_cnt['DIRTY']}  MISSING={_cnt['MISSING']}")
    # 🆕 FIX-status-hint-v4.7: DIRTY 语义说明 (AI 易误判为失败; 实际=待推送正常状态)
    print("注: DIRTY=待推送(本地有未同步变更, 正常状态); OK=已同步; MISSING=本地缺失", file=sys.stderr)
    if not _need:
        print("全部已同步 ✅ (无待推送/缺失)" + ("" if _full else "  ← OK 详情已省略, --full 看逐文件清单"))
    header = f"{'状态':<7} {'size':>9} {'本地文件':<55} {'→ 远程路径'}"
    print(header)
    print("-" * min(160, len(header) + 50))
    for st, m, lp in (_rows if _full else _need):
        sstr = "—" if m["size"] < 0 else f"{m['size']//1024}KB"
        print(f"{st:<7} {sstr:>9} {str(lp):<55} → {m['remote']}")
    if not _full and _cnt["OK"]:
        print(f"\n({_cnt['OK']} 个 OK 项未列出 — AI 通常不关心; --full 查看全部)")
    summary_file = Path(conf["watch_dir"]) / ".sync_summary.json"
    if summary_file.is_file():
        try:
            s = json.loads(summary_file.read_text(encoding="utf-8"))
            print(f"\n最近同步 [{s.get('t')}]: {s.get('line')}")
        except Exception: pass
    # v4.0 状态文件
    state_file = Path(conf["watch_dir"]) / ".sync_state.json"
    if state_file.is_file():
        try:
            st = json.loads(state_file.read_text(encoding="utf-8"))
            if st.get("phase"):
                print(f"⚠ 未完成同步: phase={st.get('phase')} 待处理={len([x for x in st.get('queue', []) if x not in st.get('done', [])])}")
        except Exception: pass
    return 0

# 🆕 v4.39.2: AI 自动升级 — 服务端参考客户端 sync.py (sync_api.php 同目录) 变更时静默自升级 (默认开)
AUTO_UPGRADE_INTERVAL = 300  # 秒: 状态文件节流, 批量命令只首个进程检查一次
AUTO_UPGRADE_STATE = os.path.join(os.path.expanduser("~"), ".ai-sync-upgrade.json")


def _file_md5(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _upgrade_state_save(server_md5: str, up: bool):
    try:
        with open(AUTO_UPGRADE_STATE, "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "md5": server_md5, "upgraded": up}, f)
    except Exception:
        pass


def _trim_upgrade_backups(d: str, keep: int = 3):
    try:
        baks = sorted((os.path.join(d, f) for f in os.listdir(d) if f.startswith("sync.py.bak.")),
                      key=os.path.getmtime)
        for f in baks[:-keep]:
            os.unlink(f)
    except Exception:
        pass


def _auto_upgrade(conf, log, api, force: bool = False) -> bool:
    """AI 自动升级: 静默对比服务端参考 sync.py 的 md5, 不一致且全链校验通过则原子覆盖本地.
    安全网: md5 校验 + py_compile 预检 + 备份 + 失败回滚(自动跳过保留旧版). 日志走 stderr, 不污染 stdout.
    返回 True=已升级, False=无需/跳过/失败. 任何异常都不抛出, 不影响主命令."""
    tmp = ""
    try:
        if not force:
            try:
                st = json.load(open(AUTO_UPGRADE_STATE, encoding="utf-8"))
                if time.time() - int(st.get("ts") or 0) < AUTO_UPGRADE_INTERVAL:
                    return False
            except Exception:
                pass
        local = _file_md5(__file__)
        try:
            w = api.whoami()
        except Exception:
            return False  # 网络/服务端异常不阻塞主命令
        server = str(w.get("client_md5") or "")
        if not server:
            return False  # 旧服务端无字段 → 不升级
        if server == local:
            _upgrade_state_save(server, up=False)
            return False
        # 下载 → md5 校验 → py_compile 预检 → 备份 → 原子覆盖 (tmp 与 __file__ 同目录, os.replace 原子)
        _dir = os.path.dirname(os.path.abspath(__file__))
        tmp = os.path.join(_dir, ".sync.py.upgrade.tmp")
        import tempfile as _tf
        _tmpd = _tf.mkdtemp(prefix="ai-sync-upg-")
        _dl_path = os.path.join(_tmpd, "sync.py")
        dl = api.client_download(_dl_path)
        if not dl.get("ok"):
            raise RuntimeError("下载失败: " + str(dl.get("error")))
        if str(dl.get("md5") or "") != server:
            raise RuntimeError(f"md5 校验失败: 下载 {str(dl.get('md5'))[:12]} vs 服务端 {server[:12]}")
        import py_compile as _pc
        _pc.compile(_dl_path, doraise=True)   # 预检语法, 防服务端误部署坏版本
        bak = os.path.join(_dir, "sync.py.bak." + local[:12])
        shutil.copy2(__file__, bak)
        _trim_upgrade_backups(_dir, 3)
        shutil.copy2(_dl_path, tmp)            # 先落同目录再原子替换 (跨设备 os.replace 会 EXDEV)
        os.replace(tmp, __file__)
        try:
            shutil.rmtree(_tmpd, ignore_errors=True)
        except Exception:
            pass
        _upgrade_state_save(server, up=True)
        msg = f"sync.py 已自动升级 {local[:12]} → {server[:12]} (本次运行仍为旧版, 下次命令生效)"
        print(msg, file=sys.stderr)
        log.info(msg)
        return True
    except Exception as e:
        try:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        msg = f"自动升级跳过: {e}"
        print(msg, file=sys.stderr)
        log.info(msg)
        return False


def _selfcheck_client_json(conf, api) -> dict:
    """selfcheck JSON 的 client 段: 本地/服务端 md5 对比 + 自动升级开关 (查询失败返回 error)."""
    try:
        _cm5 = _file_md5(__file__)
        _sm5 = str(api.whoami().get("client_md5") or "")
        return {"local_md5": _cm5[:12], "server_md5": _sm5[:12] if _sm5 else "",
                "up_to_date": bool(_sm5) and _sm5 == _cm5,
                "auto_upgrade": bool(conf.get("auto_upgrade", True))}
    except Exception as _e:
        return {"error": str(_e)[:80]}


def cmd_selfcheck(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    ret = 0
    pong = api.ping()
    # 🆕 FIX-accept-v4.6: --plain 时输出单行机器可读验收键值 (新 AI 断言用):
    #   ok=true scope=bxbb version=4.3 exec=True work_dir=... backup_dir=... probe_dir=... chunk_dir=...
    if plain:
        print(f"ok={str(pong.get('ok', False)).lower()} scope={conf.get('scope') or pong.get('scope', '?')} "
              f"server_version={pong.get('version', '?')} exec={pong.get('exec_enabled', '?')} "
              f"work_dir={pong.get('work_dir', '?')} backup_dir={pong.get('backup_dir', '?')} "
              f"probe_dir={pong.get('probe_dir', '?')} chunk_dir={pong.get('chunk_dir', '?')} "
                  f"client_local={_file_md5(__file__)[:12]} auto_upgrade={'on' if conf.get('auto_upgrade', True) else 'off'}")
        # 🆕 FIX-plain-rc-v4.6: 连通性失败时立即退出非0 (旧实现继续跑完后续测试, ok=false 但 rc=0, AI 误判成功)
        if not pong.get("ok"):
            print(f"error={pong.get('error','')}", file=sys.stderr if "--json" in sys.argv else None)
            return 2
    print(file=sys.stderr if "--json" in sys.argv else None)
    print("① 连通性测试:", file=sys.stderr if "--json" in sys.argv else None)
    flag = "✅" if pong.get("ok") else "❌"
    print(f"   {flag} {conf['api_url']}", file=sys.stderr if "--json" in sys.argv else None)
    if pong.get("ok"):
        print(f"      server_version={pong.get('version', '?')}  exec={pong.get('exec_enabled', '?')}", file=sys.stderr if "--json" in sys.argv else None)
        print(f"      work_dir={pong.get('work_dir', '?')}", file=sys.stderr if "--json" in sys.argv else None)
        print(f"      backup_dir={pong.get('backup_dir', '?')}", file=sys.stderr if "--json" in sys.argv else None)
        print(f"      probe_dir={pong.get('probe_dir', '?')}", file=sys.stderr if "--json" in sys.argv else None)
        print(f"      chunk_dir={pong.get('chunk_dir', '?')}", file=sys.stderr if "--json" in sys.argv else None)
    else:
        print(f"   错误: {pong.get('error', '')}"); return 2

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("② 映射展开:", file=sys.stderr if "--json" in sys.argv else None)
    mon = scan_watch_dir(Path(conf["watch_dir"]), conf["remote_base"], conf["ignore"], conf["allow_ext"])
    print(f"   监控 {len(mon)} 个文件", file=sys.stderr if "--json" in sys.argv else None)
    print(f"   Watch dir: {conf['watch_dir']}", file=sys.stderr if "--json" in sys.argv else None)
    print(f"   Remote base: {conf['remote_base']}", file=sys.stderr if "--json" in sys.argv else None)
    print(f"   Ignore: {conf['ignore'][:8]}{'...' if len(conf['ignore'])>8 else ''}", file=sys.stderr if "--json" in sys.argv else None)
    print(f"   Allow ext: {conf['allow_ext'] or '(全部)'}", file=sys.stderr if "--json" in sys.argv else None)

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("③ Round-trip 探针:", file=sys.stderr if "--json" in sys.argv else None)
    probe_dir = api.probe_dir.rstrip("/")
    probe_data = f"ai-sync {APP_VERSION} probe @ {time.time()}".encode()
    probe_path = f"{probe_dir}/probe_{int(time.time())}.txt"
    r = api.upload_file(probe_path, probe_data, mode="0600")
    if r.get("ok"):
        rmd5 = r.get("md5", "")
        expected = md5_bytes(probe_data)
        if rmd5.lower() == expected:
            print(f"   ✅ 上传+MD5 校验通过 → {probe_path}", file=sys.stderr if "--json" in sys.argv else None)
        else:
            print(f"   ❌ MD5 不匹配: expect={expected[:8]} got={rmd5[:8]}"); ret = max(ret, 4, file=sys.stderr if "--json" in sys.argv else None)
        d = api.download_file(probe_path)
        if d.get("ok"):
            got = base64.b64decode(d["content_b64"])
            if got == probe_data:
                print(f"   ✅ 回读内容一致", file=sys.stderr if "--json" in sys.argv else None)
            else:
                print(f"   ❌ 回读内容不一致"); ret = max(ret, 4, file=sys.stderr if "--json" in sys.argv else None)
        else:
            print(f"   ❌ 回读失败: {d.get('error', '')}"); ret = max(ret, 4, file=sys.stderr if "--json" in sys.argv else None)
        api.delete_remote(probe_path)
        print(f"   ✅ 探针已清理", file=sys.stderr if "--json" in sys.argv else None)
    else:
        print(f"   ❌ 上传失败: {r.get('error', '')}"); ret = max(ret, 4, file=sys.stderr if "--json" in sys.argv else None)

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("④ v4.0 新功能测试:", file=sys.stderr if "--json" in sys.argv else None)
    # 原子写入
    test_path = Path(conf["watch_dir"]) / "._atom_test"
    ok = atomic_write_bytes(test_path, b"v4 atomic", mtime=1000.0, mode=0o600)
    flag = "✅" if ok and test_path.read_bytes() == b"v4 atomic" else "❌"
    print(f"   {flag} 原子写入", file=sys.stderr if "--json" in sys.argv else None)
    try:
        if test_path.exists():
            mt = test_path.stat().st_mtime
            print(f"      mtime 恢复: {'✅' if abs(mt-1000.0)<2 else '❌'} (got {mt})", file=sys.stderr if "--json" in sys.argv else None)
            test_path.unlink()
    except Exception: pass
    # checksum 批量 MD5
    r = api.checksum_remote(conf["remote_base"] or ".", "md5")
    flag = "✅" if r.get("ok") and "items" in r else "❌"
    print(f"   {flag} checksum 批量MD5: {len(r.get('items', []))} 项", file=sys.stderr if "--json" in sys.argv else None)
    # 快照原子保存
    snap_t = Path(conf["watch_dir"]) / "._snap_test.json"
    s = Snapshot(snap_t); s.data = {"x": 1}; s._dirty = True; s.save(force=True)
    flag = "✅" if snap_t.is_file() else "❌"
    print(f"   {flag} 快照原子保存", file=sys.stderr if "--json" in sys.argv else None)
    try:
        if snap_t.exists(): snap_t.unlink()
        bak = snap_t.with_name(snap_t.name + ".bak")
        if bak.exists(): bak.unlink()
    except Exception: pass

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("⑤ v3.2 功能测试:", file=sys.stderr if "--json" in sys.argv else None)
    # 搜索
    r = api.search(".", "function", file_pattern="*.php")
    flag = "✅" if r.get("ok") else "❌"
    print(f"   {flag} search: {r.get('total_matches', 0)} 匹配", file=sys.stderr if "--json" in sys.argv else None)
    # 统计
    # 旧逻辑固定测 api.php: 工作目录里没有同名文件时必然 ❌, 误报为功能故障.
    # 改为测目录本身, 找不到再退回 api.php。
    r = api.stats(".")
    if not r.get("ok"):
        r = api.stats("api.php")
    flag = "✅" if r.get("ok") else "❌"
    print(f"   {flag} stats: {r.get('total_lines', 0)} 行", file=sys.stderr if "--json" in sys.argv else None)
    # snippet
    r = api.snippet_set("_selfcheck_test", "test content", "text")
    flag = "✅" if r.get("ok") else "❌"
    print(f"   {flag} snippet_set", file=sys.stderr if "--json" in sys.argv else None)
    if r.get("ok"):
        api.snippet_delete("_selfcheck_test")
        print(f"   ✅ snippet_delete", file=sys.stderr if "--json" in sys.argv else None)

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("⑥ 安全测试:", file=sys.stderr if "--json" in sys.argv else None)
    r = api.exec_cmd("echo hello", timeout=5)
    # exec 是能力开关, 不是"必须关闭才算健康": 开启后 echo hello 应成功,
    # 关闭则应返回 disabled —— 两者都 ✅; 其它异常才 ❌。
    if r.get("ok"):
        _line = "exec 已启用 (echo hello ok)"
        flag = "✅"
    elif "disabled" in (r.get("error") or ""):
        _line = "exec 未启用 (安全默认)"
        flag = "✅"
    else:
        _line = "exec 探测异常: %s" % (r.get("error") or "")
        flag = "❌"
    print(f"   {flag} {_line}", file=sys.stderr if "--json" in sys.argv else None)
    # 路径穿越防护 (客户端会抛 ValueError, 服务器返回 invalid_path)
    try:
        r = api.upload_file("../../etc/passwd", b"hacked", mode="0644")
        flag = "✅" if not r.get("ok") and "invalid" in (r.get("error") or "") else "❌"
    except ValueError:
        flag = "✅"  # 客户端 safe_rel_path 拦截, 符合预期
    print(f"   {flag} 路径穿越防护", file=sys.stderr if "--json" in sys.argv else None)
    wrong_api = ApiClient(conf["api_url"], "wrong_token", conf["upload_timeout"], log, conf.get("verify_ssl", True))
    r = wrong_api.ping()
    flag = "✅" if not r.get("ok") else "❌"
    print(f"   {flag} Token 鉴权", file=sys.stderr if "--json" in sys.argv else None)

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("⑦ 本地写权限:", file=sys.stderr if "--json" in sys.argv else None)
    tests = [("日志目录", Path(conf["log_file"]).parent),
             ("快照文件", Path(conf["watch_dir"]) / ".sync_snapshot.json"),
             ("摘要文件", Path(conf["watch_dir"]) / ".sync_summary.json"),
             ("备份目录", Path(conf["watch_dir"]) / conf["local_backup_dir"]),
             ("状态文件", Path(conf["watch_dir"]) / ".sync_state.json")]
    for name, p in tests:
        try:
            if p.is_dir() or str(p).endswith(conf["local_backup_dir"]):
                p.mkdir(parents=True, exist_ok=True)
                probe = p / f"._probe_{os.getpid()}"; probe.write_bytes(b"ok"); probe.unlink()
            else:
                if not p.parent.is_dir(): p.parent.mkdir(parents=True, exist_ok=True)
                existed = p.exists()
                with open(p, "ab"): pass
                if not existed and p.exists(): p.unlink()
            print(f"   ✅ {name}: {p}", file=sys.stderr if "--json" in sys.argv else None)
        except Exception as e:
            print(f"   ❌ {name}: {p}  {e}"); ret = max(ret, 5, file=sys.stderr if "--json" in sys.argv else None)

    print(file=sys.stderr if "--json" in sys.argv else None)
    print("⑧ 客户端版本:", file=sys.stderr if "--json" in sys.argv else None)
    try:
        _cl_md5 = _file_md5(__file__)
        _w8 = api.whoami()
        _sv8 = str(_w8.get("client_md5") or "")
        _au8 = 'on' if conf.get('auto_upgrade', True) else 'off'
        if _sv8:
            _flag8 = "✅ 最新" if _sv8 == _cl_md5 else "🔄 有新版, 下次运行自动升级"
            print(f"   {_flag8} 本地 {_cl_md5[:12]} / 服务端 {_sv8[:12]} (auto_upgrade={_au8})",
                  file=sys.stderr if "--json" in sys.argv else None)
        else:
            print("   服务端无 client_md5 字段 (旧服务端, 自动升级不可用)", file=sys.stderr if "--json" in sys.argv else None)
    except Exception as _e8:
        print(f"   ⚠ 查询失败: {_e8}", file=sys.stderr if "--json" in sys.argv else None)

    print(file=sys.stderr if "--json" in sys.argv else None)
    print(f"selfcheck 结果: code={ret}", file=sys.stderr if "--json" in sys.argv else None)
    if "--json" in sys.argv:
        # 🆕 v4.12.4-JSON: selfcheck 结构化 (AI 直接拿健康度/服务端信息)
        _mon_n = 0
        try:
            _mon_n = len(scan_watch_dir(Path(conf["watch_dir"]), conf["remote_base"], conf["ignore"], conf["allow_ext"]))
        except Exception:
            pass
        # 🆕 FIX-selfcheck-json-v4.17: 契约自检 — 本地跑安全子命令 --json, 断言 stdout 可解析
        #   (AI 每次改完跑 --selfcheck 即知 32 命令 JSON 契约是否回归)
        _contract = {"checked": 0, "passed": 0, "failed": []}
        try:
            import subprocess as _sp
            _self_path = os.path.abspath(__file__)
            _conf_path = conf.get("conf_path") or "sync.conf"
            # 🆕 FIX-selfcheck-light-v4.17: 契约自检精简为核心 5 命令 (status/whoami/check-update/health/ls)
            #   — 本地/服务器端均秒级完成, 绝不超时. 原 21 命令全量扫描在服务器端跑会 280s+ 超时, 已取消.
            _probe_cmds = [
                "--status", "--whoami", "--check-update", "--health", "--ls",
            ]
            for _pc in _probe_cmds:
                _contract["checked"] += 1
                try:
                    _r = _sp.run([sys.executable, _self_path, "--conf", _conf_path, "--json"] + _pc.split(),
                                 capture_output=True, text=True, timeout=45)
                    _o = _r.stdout.strip()
                    if _o:
                        json.loads(_o)
                        _contract["passed"] += 1
                    else:
                        _contract["failed"].append(f"{_pc}:empty")
                except Exception as _e:
                    _contract["failed"].append(f"{_pc}:{type(_e).__name__}")
            if _contract["failed"]:
                ret = max(ret, 8)
        except Exception:
            _contract = {"checked": 0, "passed": 0, "failed": ["contract_runner_error"]}
            ret = max(ret, 8)
        print(json.dumps({"command": "selfcheck", "ok": ret == 0, "health_code": ret,
                          "server": {"ok": bool(pong.get("ok")), "version": pong.get("version", "?"),
                                     "exec": pong.get("exec_enabled", "?"), "work_dir": pong.get("work_dir", "?")},
                          "monitored_files": _mon_n,
                          "scope": conf.get("scope") or pong.get("scope", "?"),
                          "contract": _contract,
                          "client": _selfcheck_client_json(conf, api)}, ensure_ascii=False))
    return ret

def cmd_sync_one(conf: dict, log: Logger, api: ApiClient, target_path: str) -> int:
    mon = scan_watch_dir(Path(conf["watch_dir"]), conf["remote_base"], conf["ignore"], conf["allow_ext"])
    # AI 修复: 相对路径基于 WATCH_DIR 解析 (与 --push/--deploy 语义一致), 而非 cwd
    watch_real = Path(os.path.realpath(str(conf["watch_dir"])))
    if not os.path.isabs(target_path):
        target_path = str(watch_real / target_path)
    # FIX-symlink-path: 词法匹配, 不 resolve() — 单文件同步的 target 可能就是软链接本身
    target_rel = _rel_under_watch(Path(os.path.abspath(target_path)), watch_real)
    found = next((m for m in mon
                  if _rel_under_watch(Path(m["local"]), watch_real) == target_rel), None)
    if not found:
        if "--json" in sys.argv:
            print(json.dumps({"command": "sync", "ok": False, "path": target_path, "error": "not_in_scope"}, ensure_ascii=False))
        else:
            print(f"❌ 文件不在监控范围内: {target_path} (⚠️ 先 --pull-smart 建立快照后再操作)")
        return 2
    uploader = Uploader(api, log, conf)
    snap = Snapshot(Path(conf["watch_dir"]) / ".sync_snapshot.json")
    # 单文件同步也走 MD5 对比
    rmap = {}
    try:
        print("  [checksum] 远程全量比对 ...", end="", flush=True, file=sys.stderr if "--json" in sys.argv else None)
        r = api.checksum_remote(conf["remote_base"] or ".", "md5")
        print(" ✓", file=sys.stderr if "--json" in sys.argv else None)
        if r.get("ok"):
            for it in r.get("items", []):
                rmap[it.get("path", "")] = it.get("md5", "")
    except Exception:
        print(" ✗")
        pass
    print(f"  [upload] 上传 {found['remote']} ...", flush=True, file=sys.stderr if "--json" in sys.argv else None)
    ok, detail, skipped = uploader._do_single(
        found, dry_run=False,
        chunked_threshold=conf.get("chunked_threshold_kb", 8192) * 1024,
        chunk_size=conf.get("chunk_size_kb", 4096) * 1024,
        remote_md5=rmap.get(found["remote"], ""),
        snapshot=snap,
    )
    if skipped:
        if "--json" in sys.argv:
            print(json.dumps({"command": "sync", "ok": True, "skipped": True, "path": found["remote"], "detail": detail}, ensure_ascii=False))
        else:
            print(f"⊘ {detail}")
        return 0
    snap.save(force=True)  # 修复: force 落盘, 避免节流跳过导致重复同步
    if "--json" in sys.argv:
        print(json.dumps({"command": "sync", "ok": ok, "uploaded": ok, "path": found["remote"], "detail": detail}, ensure_ascii=False))
    else:
        print(("✅ " if ok else "❌ ") + detail)
    return 0 if ok else 4

def cmd_search(conf: dict, log: Logger, api: ApiClient, pattern: str, path: str = ".",
               plain: bool = False, regex: bool = False, any_ext: bool = False,
               command_name: str = "search") -> int:
    """搜索代码。
    🆕 v4.15 合并: --any-ext 不限扩展名(file_pattern=*)覆盖 grep-remote 场景;
    command_name 参数让 grep-remote 作为别名复用本逻辑且保持输出形状兼容."""
    fp = "*" if any_ext else " ".join(conf.get("search_file_pattern", []))
    r = api.search(path, pattern, file_pattern=fp, regex=regex,
                   case_insensitive=conf.get("search_case_insensitive", False))
    if not r.get("ok"):
        if plain:
            print(f"pattern={pattern} ok=false error={r.get('error','')}")
        else:
            print(f"❌ 搜索失败: {r.get('error', '')}")
        return 2
    if "--json" in sys.argv:
        # 🆕 v4.11-JSON: 结构化 hits 数组
        print(json.dumps({"command": command_name, "pattern": pattern, "path": path,
                          "regex": bool(regex), "any_ext": bool(any_ext),
                          "total": r.get('total_matches', 0), "files_scanned": r.get('files_scanned', 0),
                          "truncated": bool(r.get("truncated")),
                          "hits": [{"path": h.get("path", ""), "line": h.get("line", 0),
                                    "match": h.get("match", "")[:200]}
                                   for h in r.get("hits", [])[:200]]}, ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --search --plain 每命中一行 (AI 直接拿 path:line:match)
        for hit in r.get("hits", [])[:200]:
            print(f"{hit['path']}:{hit['line']}: {hit['match'][:120]}")
        print(f"total={r.get('total_matches', 0)} files={r.get('files_scanned', 0)}"
              + (" truncated=true" if r.get("truncated") else ""))
        return 0
    print(f"\n找到 {r.get('total_matches', 0)} 处匹配 (扫描 {r.get('files_scanned', 0)} 文件"
          + (", 任意扩展名" if any_ext else "") + "):\n")
    for hit in r.get("hits", [])[:50]:
        print(f"  {hit['path']}:{hit['line']}: {hit['match'][:60]}")
    if r.get("truncated"):
        print("  ... (更多结果已截断)")
    return 0


def cmd_grep_remote(conf: dict, log: Logger, api: ApiClient, pattern: str,
                    path: str = ".", regex: bool = False, plain: bool = False) -> int:
    """v4.15: --grep-remote 是 --search --any-ext 的别名 (任意扩展名内容检索).
    输出形状保持 v4.13 兼容 (command="grep-remote"). 推荐新写法: --search <p> --any-ext."""
    return cmd_search(conf, log, api, pattern, path=path, plain=plain,
                      regex=regex, any_ext=True, command_name="grep-remote")

def cmd_stats(conf: dict, log: Logger, api: ApiClient, path: str, plain: bool = False) -> int:
    """代码统计。"""
    r = api.stats(path)
    if not r.get("ok"):
        # 🆕 v4.12: 错误走 stderr (对齐 cat/exec, AI 统一从 stderr 取错误)
        if plain:
            print(f"path={path} ok=false error={r.get('error','')}", file=sys.stderr)
        else:
            print(f"❌ 统计失败: {r.get('error', '')}", file=sys.stderr)
        return 2
    if "--json" in sys.argv:
        # 🆕 v4.11-JSON: 结构化统计数字
        print(json.dumps({"command": "stats", "path": path,
                          "files": r.get('total_files', 0), "lines": r.get('total_lines', 0),
                          "code": r.get('total_code', 0), "blank": r.get('total_blank', 0),
                          "comment": r.get('total_comment', 0), "bytes": r.get('total_bytes', 0)},
                         ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --stats --plain 单行键值 (AI 断言代码量)
        print(f"path={path} ok=true files={r.get('total_files', 0)} lines={r.get('total_lines', 0)} "
              f"code={r.get('total_code', 0)} blank={r.get('total_blank', 0)} comment={r.get('total_comment', 0)} "
              f"bytes={r.get('total_bytes', 0)}")
        return 0
    print(f"\n代码统计: {path}\n")
    print(f"  文件数: {r.get('total_files', 0)}")
    print(f"  总行数: {r.get('total_lines', 0)}")
    print(f"  代码行: {r.get('total_code', 0)}")
    print(f"  空白行: {r.get('total_blank', 0)}")
    print(f"  注释行: {r.get('total_comment', 0)}")
    print(f"  总大小: {r.get('total_bytes', 0)} bytes")
    return 0

def cmd_lint(conf: dict, log: Logger, api: ApiClient, path: str, fix: bool = False, plain: bool = False) -> int:
    """语法检查。path 可为本地路径或远程相对路径。
    🆕 FIX-js-lint-v4.6: JS/TS 类文件优先本地 node --check (真实解析器, 零假阳性, 精确行号),
    替代服务端括号计数 (对压缩库/正则/模板字符串假阳性, 行号恒 0). 其他语言走服务端 lint."""
    rp = _resolve_remote_path(conf, path)
    # ---- JS 本地 node --check (先找本地文件) ----
    js_exts = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
    lp_candidate = None
    try:
        watch = Path(conf["watch_dir"]).resolve()
        p = Path(path)
        if p.is_absolute():
            lp_candidate = p
        else:
            lp_candidate = watch / p
        # FIX-symlink-path: 不 resolve() (链接环会抛 RuntimeError); 链接本身不做本地 node 检查
        lp_candidate = Path(os.path.abspath(str(lp_candidate)))
    except Exception:
        lp_candidate = None
    if (lp_candidate and not lp_candidate.is_symlink() and lp_candidate.is_file()
            and lp_candidate.suffix.lower() in js_exts):
        import subprocess
        try:
            r = subprocess.run(["node", "--check", str(lp_candidate)],
                               capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            r = None  # 服务器端没有 node → 回落服务端 lint
        except subprocess.TimeoutExpired:
            r = None
        if r is not None:
            if r.returncode == 0:
                if "--json" in sys.argv:
                    print(json.dumps({"command": "lint", "file": rp, "lang": "js", "ok": True,
                                      "syntax_ok": True, "errors": 0, "errors_list": []},
                                     ensure_ascii=False))
                elif plain:
                    print(f"file={rp} lang=js ok=true errors=0 syntax_ok=true")
                else:
                    print(f"✅ 无语法问题: {rp} [lang=js, node --check]")
                return 0
            # 语法错误: 提取 node 报错 (精确行/列)
            err_line = err_msg = ""
            err_line_num = 0
            for ln in (r.stderr or "").splitlines():
                if ":" in ln and ln.strip():
                    err_line, err_msg = ln.strip(), ""
                    # 尝试提取行号（如 "test.js:4" 或 "test.js:4:12"）
                    import re as _re
                    m = _re.search(r':(\d+)(?::\d+)?$', ln.strip())
                    if m:
                        err_line_num = int(m.group(1))
                    break
            # 🆕 FIX-hint2-v4.6: 通用拼接分片提示 (不针对任何项目目录, 按错误特征)
            #   node 对不完整文件报 "Unexpected end of input" — 可能是拼接分片/文件截断
            if "Unexpected end of input" in (r.stderr or ""):
                hint = " (文件不完整/可能为拼接分片, 若是分片设计请检查拼接后的成品文件)"
            else:
                hint = ""
            if "--json" in sys.argv:
                # 🆕 v4.12: lint --json 结构化 errors[] (line/message/context)
                _jctx = []
                if err_line_num > 0 and lp_candidate and lp_candidate.is_file():
                    try:
                        with open(lp_candidate, 'r', encoding='utf-8', errors='replace') as f:
                            _jlines = f.readlines()
                        _jcs = max(1, err_line_num - 2)
                        _jce = min(len(_jlines), err_line_num + 2)
                        for _jln in range(_jcs, _jce + 1):
                            _jctx.append({"line": _jln,
                                          "content": (_jlines[_jln-1].rstrip() if _jln <= len(_jlines) else "")[:80],
                                          "error": _jln == err_line_num})
                    except Exception:
                        pass
                print(json.dumps({"command": "lint", "file": rp, "lang": "js", "ok": False,
                                  "syntax_ok": False, "errors": 1,
                                  "errors_list": [{"line": err_line_num,
                                                   "message": (err_line or (r.stderr or '').strip()[:200]) + hint,
                                                   "context": _jctx}]}, ensure_ascii=False))
                return 1
            if plain:
                print(f"file={rp} lang=js ok=false error={err_line or 'syntax error'}{hint}")
            else:
                print(f"❌ JS 语法错误: {rp}")
                print(f"   {err_line or (r.stderr or '').strip()[:200]}{hint}")

                # 🆕 v4.9: 显示错误行上下文
                if err_line_num > 0 and lp_candidate and lp_candidate.is_file():
                    try:
                        with open(lp_candidate, 'r', encoding='utf-8', errors='replace') as f:
                            file_lines = f.readlines()
                        ctx_start = max(1, err_line_num - 2)
                        ctx_end = min(len(file_lines), err_line_num + 2)
                        print(f"   上下文:")
                        for ln in range(ctx_start, ctx_end + 1):
                            content = file_lines[ln - 1].rstrip() if ln <= len(file_lines) else ""
                            marker = "❌" if ln == err_line_num else "  "
                            print(f"     {marker} {ln:>4}: {content[:80]}")
                    except Exception:
                        pass
            return 1
    # ---- 非 JS 或 node 不可用: 走服务端 lint ----
    if rp and lp_candidate and lp_candidate.suffix.lower() in js_exts and not lp_candidate.is_file():
        # 🆕 FIX-js-lint-v4.6: JS 但本地文件缺失 → 服务端括号计数不可信, 明确标注降级
        #   (常见于 AI 工作区与服务器不同步; 提示先 --pull 再 lint)
        _fb = True
    else:
        _fb = False
    r = api.lint(rp, fix=fix)
    # 语法错误现在返回 ok=false/code=1014 + 结构化 issues; 这不是传输失败.
    _is_lint_result = (r.get("error") == "syntax_error"
                       and (isinstance(r.get("issues"), list) or r.get("syntax_ok") is False))
    if not r.get("ok") and not _is_lint_result:
        # 🆕 FIX-hint-v4.6: 绝对路径不在 WATCH_DIR 时给友好提示 (AI 常见误用)
        _extra = ""
        if "not_found" in str(r.get("error", "")) and Path(path).is_absolute():
            _extra = " (输入的是绝对路径? 请用 WATCH_DIR 内的相对路径, 如 TV/config.php)"
        # 🆕 v4.12: 错误走 stderr (对齐 cat/exec); --json 时 stdout 保持纯净
        if "--json" in sys.argv:
            print(f"file={rp} ok=false error={r.get('error','')}{_extra}", file=sys.stderr)
        elif plain:
            print(f"file={rp} ok=false error={r.get('error','')}{_extra}", file=sys.stderr)
        else:
            print(f"❌ 检查失败: {r.get('error', '')}{_extra}", file=sys.stderr)
        return 2
    issues = r.get("issues", [])
    lang   = r.get("language", "")
    s_ok   = r.get("syntax_ok", None)
    stats  = r.get("stats", {}) or {}
    if not issues:
        if plain:
            # 🆕 FIX-plain-v4.6: --lint --plain 单行键值 (AI 断言: ok=true errors=0)
            # 🆕 v4.8-LINT-FIX: not issues 分支即无语法问题, syntax_ok 固定 true,
            #   不依赖服务端 syntax_ok 字段 (它在 fallback 路径会误报 false).
            print(f"file={rp} lang={lang or '?'} ok=true errors=0 syntax_ok=true"
                  + (" mode=server_fallback" if _fb else ""))
            return 0
        extras = []
        if s_ok is True:  extras.append("php -l ✅")
        if lang:          extras.append(f"lang={lang}")
        if stats.get("template_lines"): extras.append(f"模板行={stats['template_lines']}")
        if _fb:           extras.append("⚠ 本地无此文件, 服务端括号检查结果可能不准, 建议先 --pull")
        extra = (" [" + ", ".join(extras) + "]") if extras else ""
        if "--json" in sys.argv:
            print(json.dumps({"command": "lint", "file": rp, "lang": lang or "?",
                              "ok": True, "syntax_ok": True, "errors": 0, "errors_list": []},
                             ensure_ascii=False))
            return 0
        print(f"✅ 无语法问题: {rp}{extra}")
        return 0
    if "--json" in sys.argv:
        # 🆕 v4.12: lint --json 结构化 errors_list (AI 直接取 line/message)
        print(json.dumps({"command": "lint", "file": rp, "lang": lang or "?",
                          "ok": False, "syntax_ok": bool(s_ok), "errors": len(issues),
                          "errors_list": [{"line": iss.get("line", 0),
                                           "severity": iss.get("severity", iss.get("type", "?")),
                                           "message": iss.get("message", "")[:200],
                                           "rule": iss.get("rule", "")} for iss in issues[:200]]},
                         ensure_ascii=False))
        return 1
    if plain:
        # plain 模式问题行也单行化 (AI 逐条可解析)
        for iss in issues[:20]:
            print(f"file={rp} lang={lang or '?'} ok=false line={iss.get('line',0)} "
                  f"sev={iss.get('severity', iss.get('type','?'))} msg={iss.get('message','')[:120]}")
        print(f"# WARN: 本地无此文件, 服务端括号检查结果可能不准, 建议先 --pull 再 lint" if _fb else "", end="")
        return 1
    print(f"\n⚠ 发现 {len(issues)} 个问题 (lang={lang}, syntax_ok={s_ok}):\n")

    # 🆕 v4.9: 读取本地文件，为每个错误显示上下文代码
    local_path = None
    try:
        watch = Path(conf["watch_dir"]).resolve()
        # 尝试找本地文件
        if not _fb:
            local_path = lp_candidate  # 前面 JS lint 时已经找过了
        if not local_path or not local_path.is_file():
            # 从 rp 反推本地路径
            rp_norm = rp.lstrip('/')
            local_path = watch / rp_norm
    except Exception:
        local_path = None

    file_lines = None
    if local_path and local_path.is_file():
        try:
            with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
                file_lines = f.readlines()
        except Exception:
            file_lines = None

    for iss in issues[:20]:
        sev = iss.get("severity", iss.get("type", "?")).ljust(7)
        line = iss.get("line", 0)
        msg  = iss.get("message", "")
        rule = iss.get("rule", "")
        print(f"  line {line:<5} [{sev}] {msg}" + (f"  <{rule}>" if rule else ""))

        # 🆕 v4.9: 显示错误行上下文（前后 2 行）
        if file_lines and line > 0:
            ctx_start = max(1, line - 2)
            ctx_end = min(len(file_lines), line + 2)
            print(f"         上下文:")
            for ln in range(ctx_start, ctx_end + 1):
                content = file_lines[ln - 1].rstrip() if ln <= len(file_lines) else ""
                marker = "❌" if ln == line else "  "
                print(f"         {marker} {ln:>4}: {content[:80]}")
    return 1

def cmd_snippet_list(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    """列出代码片段。"""
    r = api.snippet_list()
    if not r.get("ok"):
        print(f"❌ 查询失败: {r.get('error', '')}" if not plain else f"ok=false error={r.get('error','')}")
        return 2
    snippets = r.get("snippets", [])
    if not snippets:
        if "--json" in sys.argv:
            print(json.dumps({"command": "snippets", "ok": True, "count": 0, "snippets": []}, ensure_ascii=False))
        else:
            print("无代码片段" if not plain else "count=0 ok=true")
        return 0
    if "--json" in sys.argv:
        # 🆕 FIX-json-v4.17: --json 结构化片段列表
        print(json.dumps({"command": "snippets", "ok": True, "count": len(snippets),
                          "snippets": [{"name": s["name"], "language": s.get("language", ""),
                                        "tags": s.get("tags", [])} for s in snippets]}, ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --snippets --plain 每片段一行 (AI 直接拿 name/lang/tags)
        for s in snippets:
            print(f"name={s['name']} lang={s.get('language','')} tags={','.join(s.get('tags', []))}")
        print(f"count={len(snippets)} ok=true")
        return 0
    print(f"\n代码片段 ({len(snippets)}):\n")
    for s in snippets:
        tags = ", ".join(s.get("tags", []))
        print(f"  {s['name']:<30} [{s.get('language', '')}] {tags}")
    return 0

# ---------------------------------------------------------------------------
# 13.1 AI 工作流增强命令 (pull/push/test/rollback/history)
# ---------------------------------------------------------------------------
HISTORY_FILE_NAME = ".sync_history.json"

def _history_path(conf: dict) -> Path:
    return Path(conf["watch_dir"]) / HISTORY_FILE_NAME

def _record_history(conf: dict, action: str, items: list[dict]):
    """记录变更历史 (本地 JSON, 最多保留 500 条)。"""
    try:
        p = _history_path(conf)
        data = []
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list): data = []
        data.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, "items": items})
        data = data[-500:]
        atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass

def cmd_pull(conf: dict, log: Logger, api: ApiClient, target: str | None = None,
             dry_run: bool = False, smart: bool = False, plain: bool = False,
             verify_after: bool = True) -> int:
    """从远程拉取文件到本地 (覆盖本地, 拉取前本地有改动则自动备份)。
    target: 指定文件相对路径; None=拉取整个 remote_base。
    🆕 v4.16-pullskip: 默认增量拉取 — 先一次 checksum 拿远程 MD5 清单, 与本地逐文件对比,
                内容一致 (MD5 相同) 跳过不下载, 只拉取远程有变化/本地缺失的文件。
                checksum 失败自动回退全量覆盖 (安全兜底)。smart 参数保留兼容 (--pull-smart)。"""
    # 增量模式只覆盖"确有差异"的文件且自动备份, 不弹全量危险确认
    if not smart and target is None and not dry_run and not _confirm_danger("增量拉取", "将拉取远程差异文件覆盖本地 (MD5 相同自动跳过, 本地有改动会自动备份)", irreversible=False):
        return 99  # v4.29: 阻断退出码对齐契约 99=策略阻断
    watch_dir = Path(conf["watch_dir"])
    remote_base = conf["remote_base"]
    _pull_dir_root = None  # 远程目录元数据来源 (目录 target / 全量 root); 单文件 target 为 None
    _binary_skipped = 0    # 路径含非 UTF-8 字节, 无法用 JSON 字符串无损寻址
    _special_skipped = 0   # FIFO/socket/设备节点: 文件协议不可传输, 服务端明确跳过
    backup = LocalBackup(watch_dir, conf["local_backup_dir"], conf["local_backup_keep"], log)
    snap = Snapshot(watch_dir / ".sync_snapshot.json")

    # 获取远程文件清单
    ignore_globs = conf.get("ignore", [])
    if target:
        # target 可能是文件或目录: 先 stat 探测类型
        rp_target = _resolve_remote_path(conf, target)
        st = api.stat_remote(rp_target)
        if not st.get("ok"):
            print(f"❌ 远程路径不存在或不可访问: {rp_target} ({st.get('error','')})", file=sys.stderr if "--json" in sys.argv else None)
            return 2
        rmd5: dict[str, dict] = {}
        _incremental = False
        if st.get("type") == "dir":
            _pull_dir_root = rp_target
            # 🆕 FIX-hidden-v4.17: 目录分支优先用 checksum 全清单 (含 .hidden 等隐藏文件;
            #   list 在服务端过滤隐藏文件, 会导致隐藏文件永不拉取), 按目录前缀过滤 + IGNORE;
            #   checksum 失败回退原 list 分页 (安全兜底).
            _tgt_prefix = rp_target.rstrip("/") + "/"
            # FIX-large-skip-v4.39.1: checksum 限定目标目录, 不再扫全根
            #   (大分区 3 万文件冷算 30s+; 目录级 O(条目) 秒级)
            _cs = api.checksum_remote(rp_target, "md5")
            if _cs.get("ok"):
                _cs_items = _cs.get("items", [])
                _special_skipped = max(_special_skipped, int(_cs.get("skipped_special") or 0))
                targets = [it["path"] for it in _cs_items
                           if it["path"].startswith(_tgt_prefix)
                           and not _remote_entry_has_binary_path(it)
                           and not any(glob_match(g, it["path"]) for g in ignore_globs)]
                _binary_skipped += sum(1 for it in _cs_items
                                       if _remote_entry_has_binary_path(it))
                rmd5 = {it["path"]: it for it in _cs_items}
                _incremental = True
                if "--json" not in sys.argv:
                    print(f"  (checksum: 目录 {rp_target} 共 {len(targets)} 个文件 MD5 已获取, 开始与本地比对)", file=sys.stderr if "--json" in sys.argv else None)
            else:
                # 回退: 目录递归列举 (分页循环)
                all_items = []
                _list_meta: dict[str, dict] = {}
                offset = 0
                while True:
                    r = api.list_remote(rp_target, recursive=True, offset=offset, limit=1000,
                                        max_depth=0)
                    if not r.get("ok"):
                        print(f"❌ 列举远程目录失败: {r.get('error', '')}", file=sys.stderr if "--json" in sys.argv else None)
                        return 2
                    for it in r.get("items", []):
                        if _remote_entry_has_binary_path(it):
                            _binary_skipped += 1
                            continue
                        if it.get("type") == "file":
                            all_items.append(it["path"])
                        elif it.get("type") == "special":
                            _special_skipped += 1
                        elif it.get("type") == "symlink":
                            # 软链接不是文件: 保留链接元数据, 由拉取循环按链接协议还原
                            all_items.append(it["path"])
                            _list_meta[it["path"]] = it
                    if not r.get("has_more"):
                        break
                    # BUG-12 修复: 防 limit=0 死循环
                    offset += max(1, len(r.get("items", [])))
                    if offset > 100000:  # 安全上限
                        break
                targets = [p for p in all_items if not any(glob_match(g, p) for g in ignore_globs)]
                if "--json" not in sys.argv:
                    print(f"  (目录 {rp_target}: 共 {len(all_items)} 个文件, 过滤后 {len(targets)} 个)", file=sys.stderr if "--json" in sys.argv else None)
                rmd5.update(_list_meta)
                # FIX-large-skip-v4.39.1: 兜底 checksum 同样限定目标目录
                _cs2 = api.checksum_remote(rp_target, "md5")
                if _cs2.get("ok"):
                    _special_skipped = max(_special_skipped, int(_cs2.get("skipped_special") or 0))
                    for _it in _cs2.get("items", []):
                        rmd5[_it.get("path", "")] = _it
                    _incremental = True
                    if "--json" not in sys.argv:
                        print(f"  (checksum: 远程 {len(rmd5)} 个文件 MD5 已获取, 开始与本地比对)", file=sys.stderr if "--json" in sys.argv else None)
                else:
                    if "--json" not in sys.argv:
                        print(f"⚠ checksum 获取失败 ({_cs2.get('error', '')}), 回退为全量覆盖拉取", file=sys.stderr if "--json" in sys.argv else None)
        else:
            # 单文件 target: 走增量比对 —— FIX-large-skip-v4.39.1:
            #   直接用 stat 已返回的 md5/size/mtime (大文件 md5=null 由服务端跳过, 用 size+mtime 兜底),
            #   不再触发全根 checksum (大分区 3 万文件冷算 30s+ 卡死; stat 单文件 O(1) 秒级).
            targets = [rp_target]
            _incremental = True
            _st_md5 = st.get("md5") or ""
            _st_size = st.get("size")
            _st_mtime = st.get("mtime")
            if _st_md5 or _st_size is not None:
                # FIX-symlink-single-v4.39.1: stat 直构条目必须带 type/link_*/safe 字段,
                #   否则单文件 pull 会把远程软链接当普通文件下载目标内容
                #   (目录 pull 走 checksum 完整条目不受影响; 绝对/越界链接 safe=false 会被跳过).
                rmd5[rp_target] = {"path": rp_target, "md5": _st_md5,
                                   "size": _st_size, "mtime": _st_mtime,
                                   "type": st.get("type") or "file",
                                   "link_target": st.get("link_target"),
                                   "link_target_rel": st.get("link_target_rel"),
                                   "safe": st.get("safe"),
                                   "reason": st.get("reason")}
            else:
                # stat 缺字段 (旧服务端): 兜底单文件 checksum (限定目标, 不扫全根)
                _cs = api.checksum_remote(rp_target, "md5")
                if _cs.get("ok"):
                    for _it in _cs.get("items", []):
                        rmd5[_it.get("path", "")] = _it
                    _incremental = True
                elif "--json" not in sys.argv:
                    print(f"⚠ checksum 获取失败 ({_cs.get('error', '')}), 回退为全量覆盖拉取", file=sys.stderr if "--json" in sys.argv else None)
    else:
        # 🆕 v4.17-pullfast: 全量拉取 — 先试一次 checksum (含全部文件路径+MD5),
        #   成功则直接用 MD5 清单当文件列表 (省 list 分页往返 ~1s; 服务端 MD5 缓存命中后总耗时 ~1.5s);
        #   checksum 失败回退原 list 分页 + 全量覆盖 (安全兜底).
        _pull_dir_root = remote_base or "."
        _cs = api.checksum_remote(remote_base or ".", "md5")
        if _cs.get("ok"):
            _cs_items = _cs.get("items", [])
            _special_skipped = max(_special_skipped, int(_cs.get("skipped_special") or 0))
            targets = [it["path"] for it in _cs_items
                       if not _remote_entry_has_binary_path(it)
                       and not any(glob_match(g, it["path"]) for g in ignore_globs)]
            _binary_skipped += sum(1 for it in _cs_items
                                   if _remote_entry_has_binary_path(it))
            rmd5: dict[str, dict] = {it["path"]: it for it in _cs_items}
            _incremental = True
            _skipped_ignore = len(_cs_items) - len(targets)
            if _skipped_ignore and "--json" not in sys.argv:
                print(f"  (忽略 {_skipped_ignore} 个文件: 匹配 IGNORE 规则)", file=sys.stderr if "--json" in sys.argv else None)
            if "--json" not in sys.argv:
                print(f"  (checksum: 远程 {len(rmd5)} 个文件 MD5 已获取, 开始与本地比对)", file=sys.stderr if "--json" in sys.argv else None)
        else:
            # 全量拉取 (checksum 失败兜底): 分页循环直到 has_more=false
            all_items = []
            _list_meta: dict[str, dict] = {}
            offset = 0
            while True:
                r = api.list_remote(remote_base, recursive=True, offset=offset, limit=1000,
                                    max_depth=0)
                if not r.get("ok"):
                    print(f"❌ 列举远程失败: {r.get('error', '')}", file=sys.stderr if "--json" in sys.argv else None)
                    return 2
                for it in r.get("items", []):
                    if _remote_entry_has_binary_path(it):
                        _binary_skipped += 1
                        continue
                    if it.get("type") == "file":
                        all_items.append(it["path"])
                    elif it.get("type") == "special":
                        _special_skipped += 1
                    elif it.get("type") == "symlink":
                        all_items.append(it["path"])
                        _list_meta[it["path"]] = it
                if not r.get("has_more"):
                    break
                # BUG-12 修复: 防 limit=0 死循环
                offset += max(1, len(r.get("items", [])))
                if offset > 100000:  # 安全上限
                    break
            # 过滤掉匹配 IGNORE 规则的远程文件 (如 cache/*, ai-sync/*)
            targets = [p for p in all_items if not any(glob_match(g, p) for g in ignore_globs)]
            skipped_ignore = len(all_items) - len(targets)
            if skipped_ignore and "--json" not in sys.argv:
                print(f"  (忽略 {skipped_ignore} 个文件: 匹配 IGNORE 规则)", file=sys.stderr if "--json" in sys.argv else None)
            # 普通 --pull 也默认增量比对 (checksum 失败时此处回退全量覆盖, 安全兜底不丢文件)
            rmd5: dict[str, dict] = dict(_list_meta)
            _incremental = False
            _cs2 = api.checksum_remote(remote_base or ".", "md5")
            if _cs2.get("ok"):
                _special_skipped = max(_special_skipped, int(_cs2.get("skipped_special") or 0))
                for _it in _cs2.get("items", []):
                    rmd5[_it.get("path", "")] = _it
                _incremental = True
                if "--json" not in sys.argv:
                    print(f"  (checksum: 远程 {len(rmd5)} 个文件 MD5 已获取, 开始与本地比对)", file=sys.stderr if "--json" in sys.argv else None)
            else:
                if "--json" not in sys.argv:
                    print(f"⚠ checksum 获取失败 ({_cs2.get('error', '')}), 回退为全量覆盖拉取", file=sys.stderr if "--json" in sys.argv else None)
    if "--json" not in sys.argv:
        print(f"\n📥 {'增量比对' if _incremental else '拉取'} {len(targets)} 个文件 from 远程 → 本地", file=sys.stderr if "--json" in sys.argv else None)
    if dry_run:
        # 🆕 v4.11-JSON: --json 时输出结构化 files 清单 (与 push --dry-run 对齐)
        if "--json" in sys.argv:
            print(json.dumps({"command": "pull", "dry_run": True, "mode": "smart" if _incremental else "full",
                              "total": len(targets),
                              "files": [{"path": t} for t in targets]}, ensure_ascii=False))
            return 0
        if plain:
            print(f"pull={len(targets)} result=dry_run", file=sys.stderr if "--json" in sys.argv else None)
            for t in targets[:50]: print(f"dry {t}")
            return 0
        for t in targets: print(f"  [DRY-RUN] {t}")
        return 0

    # 远程目录元数据 (mode / 空目录): checksum 只含文件, 目录需 list 获取.
    #   失败不影响文件拉取 (目录权限属于"尽力而为"的状态还原).
    _pull_dirs = []
    if _pull_dir_root is not None:
        _doff = 0
        while True:
            try:
                _dr = api.list_remote(_pull_dir_root, recursive=True, offset=_doff,
                                      limit=1000, include_hidden=True, max_depth=0)
            except Exception:
                break
            if not _dr.get("ok"):
                break
            _dits = _dr.get("items") or []
            for _it in _dits:
                if _it.get("type") != "dir":
                    continue
                _dp = _it.get("path") or ""
                if any(glob_match(g, _dp) for g in ignore_globs):
                    continue
                _pull_dirs.append(_it)
            if not _dr.get("has_more") or not _dits:
                break
            _doff += len(_dits)
            if _doff > 100000:
                break
    # 先建出目录 (默认权限), 保证后续下载可落盘; 最终 mode 等全部文件写完再回填.
    if _pull_dirs:
        for _it in sorted(_pull_dirs, key=lambda x: str(x.get("path", "")).count("/")):
            _drp = _it.get("path") or ""
            _dlp = (watch_dir / _drp[len(remote_base)+1:]
                    if remote_base and _drp.startswith(remote_base + "/") else watch_dir / _drp)
            try:
                if watch_dir.exists() and not _write_parent_within(_dlp, watch_dir.resolve()):
                    continue
                if os.path.islink(str(_dlp)):
                    continue
                if os.path.exists(str(_dlp)) and not _dlp.is_dir():
                    continue
                _dlp.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    ok_n = fail_n = skip_n = backed = 0
    pulled = []
    failed_files = []  # 增强: 记录失败文件以便排查
    _dl_tasks = []  # v4.7 并发下载任务收集 (rp, lp, 序号)
    _link_tasks = []  # FIX-symlink-order: 软链接延后到普通文件落盘后创建 (目标状态可能被本轮更新)
    _pull_i = 0
    _last_prog_log = 0
    for rp in targets:
        _pull_i += 1
        # v4.6 反馈优化: 比对阶段节流进度(每300个打印一次), 避免"卡在比对无输出"
        if _incremental and (_pull_i - _last_prog_log) >= 300:
            _last_prog_log = _pull_i
            print(f"  [比对 {_pull_i}/{len(targets)}] skip={skip_n} 进行中…", file=sys.stderr if "--json" in sys.argv else None)
        lp = watch_dir / rp[len(remote_base)+1:] if rp.startswith(remote_base+"/") else watch_dir / rp
        # FIX-symlink: 远程软链接 → 本地还原链接本身, 绝不下载目标文件内容
        _ri0 = rmd5.get(rp)
        if isinstance(_ri0, dict) and _ri0.get("type") == "symlink":
            # FIX-link-b64: 优先用 b64 还原非 UTF-8 目标
            _lt = _remote_link_target(_ri0)
            if _ri0.get("safe") is False or not isinstance(_lt, str) or not _lt:
                skip_n += 1
                print(f"  ⚠ 跳过不安全的远程软链接: {rp} "
                      f"({_ri0.get('reason') or 'unsafe'}; 目标在 work_dir 外, 不落盘)", file=sys.stderr if "--json" in sys.argv else sys.stderr)
                continue
            _rtr = _remote_link_target_rel(_ri0)
            _ltr = _remote_target_rel_to_watch(_rtr, remote_base)
            if _rtr is not None and _ltr is None:
                skip_n += 1
                print(f"  ⚠ 跳过链接目标不在同步子树内的软链接: {rp} -> {_safe_text(_lt)} "
                      f"(目标 {_safe_text(str(_rtr))} 不在 remote_base={remote_base!r} 内)",
                      file=sys.stderr if "--json" in sys.argv else sys.stderr)
                continue
            if watch_dir.exists() and not _write_parent_within(lp, watch_dir.resolve()):
                fail_n += 1
                failed_files.append({"path": rp, "error": "父目录越界, 拒绝写入软链接"})
                print(f"  ❌ {rp}: 父目录越界, 拒绝写入软链接", file=sys.stderr if "--json" in sys.argv else sys.stderr)
                continue
            # 延后创建: 如果链接目标本轮会被文件替换 (旧本地链接可能指向 watch_dir 外),
            # 先建链接会被 local_link_target_meta 误判 target_escapes_workdir.
            _link_tasks.append((rp, lp, _ri0, _lt, _ltr, _pull_i))
            continue
        # 🆙 增量: 本地已存在且 MD5 与远程一致 → 跳过下载 (同步快照, 不备份)
        # v4.6 性能优化: 两级快速跳过, 避免 virtiofs 上对每个文件读盘算 md5 (3174文件原需数分钟)
        #   快速1: 远程 size ≠ 本地 stat size → 必变更, 直接下载(0 IO)
        #   快速2: size一致 + 快照有记录且文件未改动(size+mtime一致) + 快照md5=远程md5 → 跳过(仅stat)
        #   慢速:  size一致但快照无/不匹配 → 才算真实 md5 (读文件)
        # 🆕 v4.16-pullskip: 普通 --pull 也走增量比对 (MD5 相同跳过, 只下载差异)
        if _incremental:
            _ri = rmd5.get(rp)
            _remote_md5 = _ri.get("md5", "") if _ri else ""
            _remote_size = _ri.get("size") if _ri else None
            _remote_mtime = _ri.get("mtime") if _ri else None
            if lp.is_file() and not os.path.islink(str(lp)):
                if _remote_md5:
                    # FIX-symlink-pull: 本地是链接时不能按目标内容判定"已一致", 否则远程普通文件永远不会替换该链接
                    try:
                        _lst = lp.stat()
                        # 快速1: size 不一致 → 落到下面的下载逻辑 (不读文件)
                        if _remote_size is None or _lst.st_size == _remote_size:
                            # 快速2: 快照命中且文件未改动 → 直接跳过
                            _si = snap.get(str(lp))
                            if (_si and _si.get("size") == _lst.st_size
                                    and abs(float(_si.get("mtime", 0)) - _lst.st_mtime) < 1.0
                                    and _si.get("md5", "") == _remote_md5):
                                skip_n += 1
                                continue
                            # 慢速: 算真实 md5
                            if md5_file(lp).lower() == _remote_md5.lower():
                                f = finger(lp, mode="hash")
                                snap.update(str(lp), {"size": f["size"], "sha1": f["sha1"], "md5": _remote_md5,
                                                      "remote": rp, "mtime": f["mtime"], "mode": f["mode"],
                                                      "source": f"pull-smart:{rp}"})
                                skip_n += 1
                                continue
                    except Exception:
                        pass  # 比对异常 → 走常规下载
                elif _remote_size is not None and _remote_mtime is not None:
                    # FIX-large-skip-v4.39.1: 服务端对大文件跳过 md5 (md5=null),
                    #   用 size+mtime 指纹判差异 (避免大文件每次 pull 全量重下)
                    try:
                        _lst = lp.stat()
                        if _lst.st_size == _remote_size and abs(float(_remote_mtime) - _lst.st_mtime) < 60:
                            skip_n += 1
                            continue
                    except Exception:
                        pass
        # 本地同路径是软链接 → 先备份链接本身 (记录 raw target, 不跟随读取目标内容).
        #   下载会用 os.replace 把链接替换成普通文件; 不备份会静默丢失本地链接.
        if os.path.islink(str(lp)):
            try:
                if backup and backup._do_backup_link(lp):
                    backed += 1
            except Exception:
                pass
        # 本地已存在且与快照一致 → 先检查是否有本地改动, 有则备份
        elif lp.is_file():
            snap_info = snap.get(str(lp))
            if snap_info:
                # BUG-9 修复: 用真 md5 判断本地是否改动, 不用 fast 模式的 "f:..." 占位 sha1
                #   (内容改了但 mtime 被保留时 fast sha1 不变 → 漏备份 → 本地改动被覆盖)
                cur_md5 = md5_file(lp)
                snap_md5 = snap_info.get("md5", "")
                if snap_md5 and snap_md5.startswith("f:"):
                    snap_md5 = ""  # 历史写入的 fake md5, 无法比对, 保守备份
                if not snap_md5 or cur_md5 != snap_md5:
                    b = backup._do_backup(lp)
                    if b: backed += 1
        # 安全: 父目录经软链接逃逸到 watch_dir 外时拒绝落盘
        if watch_dir.exists() and not _write_parent_within(lp, watch_dir.resolve()):
            fail_n += 1
            failed_files.append({"path": rp, "error": "父目录越界, 拒绝写入"})
            print(f"  ❌ {rp}: 父目录越界, 拒绝写入", file=sys.stderr if "--json" in sys.argv else sys.stderr)
            continue
        # 下载 (v4.7: 改为收集任务, 循环结束后线程池并发执行 — 网络 RTT 是串行瓶颈)
        _dl_tasks.append((rp, lp, _pull_i))
    # ===== v4.7 并发下载: 仅收集到的真实差异文件 =====
    # _conc / _dl_worker 提升到 if 块外, 供 v4.8 落盘校验失败重试复用
    _conc = max(1, int(conf.get("pull_concurrency") or 8))

    def _dl_worker(task):
        _rp, _lp, _idx = task
        try:
            # PERF-v4.25: 流式下载到本地 (峰值内存 O(块)), 失败回退整包路径
            _sr = api.download_to_file(_rp, _lp)
            if _sr.get("ok"):
                return (True, _rp, None, _sr.get("md5"))
            _r = api.download_file(_rp)
            if not _r.get("ok"):
                return (False, _rp, _sr.get("error") or _r.get('error', '下载失败'), None)
            try:
                _data = base64.b64decode(_r["content_b64"])
            except Exception as _e:
                return (False, _rp, f"解码失败 {_e}", None)
            # BUG-5 修复: 下载内容校验, 防传输损坏静默写入本地
            _got = hashlib.md5(_data).hexdigest()
            _rm5 = _r.get("md5", "")
            if _rm5 and _rm5.lower() != _got.lower():
                return (False, _rp, f"MD5 校验失败 (远程={_rm5[:8]} 本地={_got[:8]})", None)
            # BUG-6 修复: 恢复 mtime/mode
            try:
                _mode_int = int(str(_r.get("mode")), 8) if _r.get("mode") else None
            except Exception:
                _mode_int = None
            if not atomic_write_bytes(_lp, _data, mtime=_r.get("mtime"), mode=_mode_int):
                return (False, _rp, "写入失败", None)
            return (True, _rp, None, _got)
        except Exception as _e:
            return (False, _rp, f"异常: {_e}", None)

    if _dl_tasks:
        print(f"  [下载] {len(_dl_tasks)} 个文件需下载, 并发 {_conc} 线程…", file=sys.stderr if "--json" in sys.argv else None)

        with ThreadPoolExecutor(max_workers=_conc) as _ex:
            _futs = {_ex.submit(_dl_worker, t): t for t in _dl_tasks}
            for _fut in as_completed(_futs):
                _rp, _lp, _idx = _futs[_fut]
                _ok, _rp2, _err, _got = _fut.result()
                if not _ok:
                    print(f"  ❌ {_rp2}: {_err}", file=sys.stderr if "--json" in sys.argv else None)
                    fail_n += 1
                    failed_files.append({"path": _rp2, "error": _err})
                    continue
                # BUG-4 修复: 用真 md5 (got_md5) 写快照
                f = finger(_lp, mode="hash")
                snap.update(str(_lp), {"size": f["size"], "sha1": f["sha1"], "md5": _got,
                                       "remote": _rp2, "mtime": f["mtime"], "mode": f["mode"],
                                       "source": f"pull:{_rp2}"})
                ok_n += 1
                pulled.append({"remote": _rp2, "local": str(_lp), "md5": _got})
                # FIX-AI-context-budget-v4.39.3: 逐文件进度默认折叠为每 500 条,
                # --verbose 恢复全量。JSON 模式下这些行原本全进顶层 stderr 字段,
                # 一次首拉 4000 行会耗尽 AI 上下文, 而有效结论只有末尾摘要几行。
                if ("--verbose" in sys.argv or "-v" in sys.argv) or _idx % 500 == 0 or _idx == len(targets):
                    print(f"  [{_idx}/{len(targets)}] ✅ {_rp2}", file=sys.stderr if "--json" in sys.argv else None)

    # FIX-symlink-order: 普通文件先落盘, 再创建软链接.
    #   否则"远程 link -> target, 本地 target 仍是旧链接(指向外部)"时会被误判 target_escapes_workdir.
    for rp, lp, _ri0, _lt, _ltr, _idx in _link_tasks:
        if watch_dir.exists() and not _write_parent_within(lp, watch_dir.resolve()):
            fail_n += 1
            failed_files.append({"path": rp, "error": "父目录越界, 拒绝写入软链接"})
            print(f"  ❌ {rp}: 父目录越界, 拒绝写入软链接", file=sys.stderr if "--json" in sys.argv else sys.stderr)
            continue
        _had_local = os.path.lexists(str(lp))
        _lok, _ldet, _lskip = create_local_symlink(
            lp, _lt, _ltr, _ri0.get("md5", "") or "",
            snap, backup, watch_dir, log, remote_path=rp,
            mtime=_ri0.get("mtime"))
        if _lskip:
            skip_n += 1
        elif _lok:
            ok_n += 1
            if _had_local:
                backed += 1  # create_local_symlink 已备份原文件/链接
            pulled.append({"remote": rp, "local": str(lp),
                           "md5": _ri0.get("md5", ""), "type": "symlink",
                           "link_target": _lt})
            if ("--verbose" in sys.argv or "-v" in sys.argv) or _idx % 500 == 0 or _idx == len(targets):
                print(f"  [{_idx}/{len(targets)}] ✅ LINK {rp} -> {_lt}",
                      file=sys.stderr if "--json" in sys.argv else None)
        else:
            fail_n += 1
            failed_files.append({"path": rp, "error": _ldet})
            print(f"  ❌ {rp}: {_ldet}", file=sys.stderr if "--json" in sys.argv else sys.stderr)

    # ===== v4.8 下载后整体回读校验: 落盘MD5 = 快照MD5 = 远程MD5 三方一致 =====
    # 下载时已校验"内存MD5 vs 远程MD5", 但落盘文件本身未被验证.
    # 此步对本次下载的落盘文件回读重算MD5 与快照(=远程)比对, 防写盘损坏静默写入.
    # 校验失败 → 删除坏文件 + 移除快照条目 → 下次 pull-smart 自动重下 (自愈).
    if pulled and ok_n > 0 and verify_after:
        print(f"  [校验] 回读 {len(pulled)} 个落盘文件 MD5, 确认三方一致…", file=sys.stderr if "--json" in sys.argv else None)
        def _verify_worker(item):
            _lp = Path(item["local"])
            if item.get("type") == "symlink":
                return (item, None)  # 软链接由创建阶段校验, 无文件内容可回读
            try:
                if not _lp.is_file():
                    return (item, "文件不存在(可能写入失败)")
                _dm = md5_file(_lp).lower()
                _want = (item.get("md5") or "").lower()
                if _want and _dm != _want:
                    return (item, f"MD5 不符 (快照={_want[:8]} 落盘={_dm[:8]})")
                return (item, None)
            except Exception as _e:
                return (item, f"校验异常: {_e}")
        _vbad = []
        with ThreadPoolExecutor(max_workers=_conc) as _vex:
            for _vit, _verr in _vex.map(_verify_worker, pulled):
                if _verr:
                    _vbad.append((_vit, _verr))
        if _vbad:
            print(f"  ⚠ 落盘校验失败 {len(_vbad)}/{len(pulled)} 个 → 当场重试下载…", file=sys.stderr if "--json" in sys.argv else None)
            # v4.9: 失败文件当场重试一次 (复用 _dl_worker), 重试后再次回读校验
            _retry_ok = 0
            _retry_tasks = [(it["remote"], Path(it["local"]), 0) for it, _e in _vbad]
            with ThreadPoolExecutor(max_workers=_conc) as _rex:
                _rfuts = {_rex.submit(_dl_worker, t): t for t in _retry_tasks}
                for _rfut in as_completed(_rfuts):
                    _rit = _rfuts[_rfut]
                    _rlp = Path(_rit[1])
                    _rok, _rrp, _rerr, _rgot = _rfut.result()
                    if not _rok:
                        print(f"     ❌ 重试失败 {_rrp}: {_rerr}", file=sys.stderr if "--json" in sys.argv else None)
                        # 自愈: 删坏文件 + 移除快照 → 下次增量拉取自动重下
                        try:
                            if _rlp.is_file(): _rlp.unlink()
                        except Exception:
                            pass
                        snap.remove(str(_rlp))
                        fail_n += 1
                        failed_files.append({"path": _rrp, "error": f"重试后仍失败: {_rerr}"})
                        continue
                    # 重试成功后再次回读校验 (确保落盘真的一致)
                    try:
                        _rdm = md5_file(_rlp).lower()
                    except Exception:
                        _rdm = ""
                    if _rdm != (_rgot or "").lower():
                        print(f"     ❌ 重试后回读仍不符 {_rrp}", file=sys.stderr if "--json" in sys.argv else None)
                        try:
                            if _rlp.is_file(): _rlp.unlink()
                        except Exception:
                            pass
                        snap.remove(str(_rlp))
                        fail_n += 1
                        failed_files.append({"path": _rrp, "error": "重试后落盘仍不一致"})
                        continue
                    f = finger(_rlp, mode="hash")
                    snap.update(str(_rlp), {"size": f["size"], "sha1": f["sha1"], "md5": _rgot,
                                            "remote": _rrp, "mtime": f["mtime"], "mode": f["mode"],
                                            "source": f"pull:{_rrp}"})
                    _retry_ok += 1
                    print(f"     ✅ 重试成功 {_rrp}", file=sys.stderr if "--json" in sys.argv else None)
            if _retry_ok:
                print(f"  ✅ 当场重试成功 {_retry_ok} 个, 最终失败 {len(_vbad)-_retry_ok} 个", file=sys.stderr if "--json" in sys.argv else None)
        else:
            print(f"  ✅ 落盘校验全部通过 ({len(pulled)} 个文件, 三方一致)", file=sys.stderr if "--json" in sys.argv else None)
    # 目录 mode 回填 (最后做: 只读/无写权限目录不会阻塞前面的文件落盘)
    _dir_n = 0
    for _it in _pull_dirs:
        _drp = _it.get("path") or ""
        _dlp = (watch_dir / _drp[len(remote_base)+1:]
                if remote_base and _drp.startswith(remote_base + "/") else watch_dir / _drp)
        try:
            if os.path.islink(str(_dlp)) or not _dlp.is_dir():
                continue
            _dm = _it.get("mode")
            if _dm in (None, ""):
                continue
            _dmi = int(str(_dm), 8) & 0o777
            os.chmod(_dlp, _dmi)
            _dir_n += 1
        except Exception:
            pass
    if _dir_n and "--json" not in sys.argv:
        print(f"  [目录] 已恢复 {_dir_n} 个目录权限", file=sys.stderr if "--json" in sys.argv else None)
    snap.save(force=True)
    _record_history(conf, "pull", pulled)
    if "--json" in sys.argv:
        # 🆕 v4.12.2-JSON: 实际执行后结构化结果 (AI 直接拿 pulled/failed)
        print(json.dumps({"command": "pull", "action": "pull", "ok": fail_n == 0,
                          "pulled": ok_n,
                          "skipped": skip_n, "failed": fail_n, "backed_up": backed,
                          "unsupported_names": _binary_skipped,
                          "special_skipped": _special_skipped,
                          "result": "ok" if fail_n == 0 else "fail",
                          "failed_files": [{"path": ff["path"], "error": ff["error"][:120]}
                                           for ff in failed_files]}, ensure_ascii=False))
        return 0 if fail_n == 0 else 4
    if plain:
        # 🆕 FIX-plain-v4.6: --pull --plain 单行汇总 (AI 断言 result=ok)
        print(f"pulled={ok_n} skipped={skip_n} failed={fail_n} backed={backed} "
              f"unsupported_names={_binary_skipped} special_skipped={_special_skipped} "
              f"result={'ok' if fail_n == 0 else 'fail'}",
              file=sys.stderr if "--json" in sys.argv else None)
        for ff in failed_files:
            print(f"fail {ff['path']} error={ff['error']}", file=sys.stderr if "--json" in sys.argv else None)
        return 0 if fail_n == 0 else 4
    print(f"\n摘要: 拉取 {ok_n} 跳过未变 {skip_n} 失败 {fail_n} 本地备份 {backed} "
          f"unsupported_names={_binary_skipped} special_skipped={_special_skipped}",
          file=sys.stderr if "--json" in sys.argv else None)
    if _special_skipped and "--json" not in sys.argv:
        print(f"  ⚠ 跳过 {_special_skipped} 个特殊文件 (FIFO/socket/设备节点): 文件协议只能传输普通文件, "
              f"这些节点未拉取; 如需处理请在服务器端用 --exec。",
              file=sys.stderr if "--json" in sys.argv else None)
    if _binary_skipped and "--json" not in sys.argv:
        print(f"  ⚠ {_binary_skipped} 个远程文件名不是合法 UTF-8, JSON 协议无法无损寻址, 已跳过; "
              f"请用 --exec 在服务器端重命名, 或改用原始 API 的 path_b64 字段。",
              file=sys.stderr if "--json" in sys.argv else None)
    if failed_files:
        print(f"\n❌ 失败文件清单 ({len(failed_files)}):", file=sys.stderr if "--json" in sys.argv else None)
        for ff in failed_files:
            print(f"   {ff['path']}: {ff['error']}", file=sys.stderr if "--json" in sys.argv else None)
    return 0 if fail_n == 0 else 4

def _push_preview(conf: dict, api: ApiClient, up_todo: list, snap: Snapshot, rmap: dict,
                  plain: bool = False, show_diff: bool = False) -> int:
    """🆕 v4.10-PUSH-DIFF: 推送前基线冲突检测 + 内容 diff 预览.
    让 AI 一眼确认: ① 修改是否正确 ② 本地是否基于服务器最新代码.
    - 基线冲突 (默认开启, 复用 checksum 结果 rmap, 零额外请求):
        快照记录的上次同步 MD5 (本地修改的基线) vs 服务器当前 MD5, 不同 → 服务器被他人/其他端改过,
        本地基于旧版本, push 会覆盖他人改动.
    - 内容 diff (仅 --show-diff): 本地 vs 服务器当前 unified diff, 带 +/- 与文件名.
    - 策略 push_conflict: ask(默认, 仅警告不阻断) / block(有冲突直接阻断返回 99) / force(不提示).
    返回 0=继续推送; 99=已阻断. """
    import difflib
    if not up_todo:
        return 0
    # ===== 1. 基线冲突检测 (默认开启) =====
    conflicts = []  # (remote, base_md5, cur_md5)
    for m in up_todo:
        prev = snap.get(m["local"])
        if not prev or _snap_has_fake_md5(prev):
            continue  # 从未真正同步过 → 无基线可判, 不算冲突
        cur = rmap.get(m["remote"], "")
        if cur and cur != prev.get("md5"):
            conflicts.append((m["remote"], prev.get("md5", ""), cur))
    if conflicts:
        if not plain:
            print(f"\n⚠ 基线冲突: {len(conflicts)} 个文件服务器已被他人/其他端修改 (本地基线 ≠ 服务器当前):")
            for rp, b, c in conflicts:
                print(f"    {rp}")
                print(f"      基线(上次同步): {b}")
                print(f"      服务器当前:      {c}")
            print("   → 本地修改基于的是旧版本, push 将覆盖服务器上的他人改动")
        else:
            print(f"conflict={len(conflicts)} baseline_mismatch=1")
            for rp, b, c in conflicts:
                print(f"  baseline {rp} base={b} cur={c}")
        policy = str(conf.get("push_conflict", "ask")).lower()
        if policy == "block":
            if not plain:
                print("  策略 push_conflict=block → 已阻断推送, 请先 --pull 同步最新再重试")
            else:
                print("policy=block blocked=1")
            return 99
        if not plain:
            print(f"  策略 push_conflict=ask → 仅警告不阻断 ({len(conflicts)} 个冲突文件将被覆盖)")
    # ===== 2. 内容 diff 预览 (仅 --show-diff) =====
    if show_diff and not plain:
        print("\n📋 推送前内容 diff (服务器当前 → 本地):")
        shown = 0
        for m in up_todo:
            rp = m["remote"]
            if m.get("is_symlink"):
                print(f"\n  [{rp}] (软链接 -> {m.get('link_target')}, 跳过内容 diff)")
                continue
            try:
                local_text = Path(m["local"]).read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                print(f"\n  [{rp}] (二进制文件, 跳过 diff)")
                continue
            except Exception as e:
                print(f"\n  [{rp}] (本地读取失败: {e})")
                continue
            try:
                r = api.download_file(rp)
                if not r.get("ok"):
                    remote_text = ""  # 远程不存在 → 新增文件
                else:
                    remote_text = base64.b64decode(r["content_b64"]).decode("utf-8")
            except Exception:
                remote_text = ""
            diff_lines = list(difflib.unified_diff(
                remote_text.splitlines(keepends=True),
                local_text.splitlines(keepends=True),
                fromfile=f"服务器: {rp}",
                tofile=f"本地: {m['local']}",
                lineterm="\n",
            ))
            if not diff_lines:
                continue
            shown += 1
            print(f"\n  === {rp} ===")
            head = diff_lines[:80]
            for ln in head:
                print("    " + ln.rstrip("\n"))
            if len(diff_lines) > 80:
                print(f"    ... 还有 {len(diff_lines)-80} 行差异 (已截断, 全文可用 --cat)")
        if shown == 0:
            print("  (无可展示的文本差异)")
    return 0


def cmd_push(conf: dict, log: Logger, api: ApiClient, target: str | None = None,
             targets: list[str] | None = None, dry_run: bool = False, plain: bool = False,
             show_diff: bool = False) -> int:
    """推送本地文件到远程 (上传, 远程被覆盖前服务器自动备份)。
    targets: 指定多个本地文件或目录; target: 单个; None=推送所有 dirty 文件。"""
    watch_dir = Path(conf["watch_dir"])
    snap = Snapshot(watch_dir / ".sync_snapshot.json")
    # 统一处理: targets 优先, 兼容旧 target 参数
    tgt_list = targets if targets else ([target] if target else None)

    # 目录状态同步范围 (目录 mode + 空目录):
    #   None = 全量 (随文件上传后执行; 无文件变更时跳过)
    #   []   = 跳过 (单文件快路径)
    #   list = 显式指定的目录目标 (即使该目录下没有文件变更也执行)
    _dir_roots: list[str] | None = None
    _dir_explicit = False
    _dir_file_parents = False
    if tgt_list:
        _dir_roots = []
        for tgt in tgt_list:
            tp = Path(tgt)
            if not tp.is_absolute():
                tp = Path(conf["watch_dir"]) / tgt
            tp = Path(os.path.abspath(str(tp)))
            if tp.is_dir() and not tp.is_symlink():
                _dir_explicit = True
                _rel = _rel_under_watch(tp, watch_dir)
                if _rel is None or _rel in ("", "."):
                    _dir_roots = None
                    break
                _dir_roots.append(_rel)

    # ===== 快路径 (方案1): 所有 target 都是明确文件(非目录) → 跳过全量 scan_watch_dir =====
    # 实测: 全量扫描 1283 文件需 24.6s (virtiofs 根 rglob 慢), 单文件 stat 仅 ~0.1s.
    # 只对目标文件构造 item (字段与 scan_watch_dir 完全一致), 大幅削减 push 准备阶段耗时.
    fast_mode = False
    mon = None
    if tgt_list:
        tps = []
        ok_files = True
        for tgt in tgt_list:
            tp = Path(tgt)
            if not tp.is_absolute():
                tp = Path(conf["watch_dir"]) / tp
            # 🆕 FIX-symlink-push: 用 abspath 规范化而不 follow symlink (resolve() 会跟随链接,
            #   导致 push "链接.txt" 被改名成目标文件 "源.txt" 上传). 软链接按链接协议上传.
            tp = Path(os.path.abspath(str(tp)))
            if not os.path.lexists(str(tp)):
                print(f"❌ 文件/目录不存在: {tgt} (解析路径: {tp})", file=sys.stderr)
                return 2
            if tp.is_dir() and not tp.is_symlink():
                ok_files = False
                break
            tps.append(tp)
        if ok_files:
            # 全部 target 均为文件/软链接 → 快路径
            fast_mode = True
            # v4.38: 文件目标的父目录链也要同步 mode (上传会自动建父目录, 默认 0755 会覆盖真实权限)
            _parents: set[str] = set()
            for _tp in tps:
                _r = _rel_under_watch(_tp, watch_dir)
                if not _r or "/" not in _r:
                    continue
                _parents.add(_r.rsplit("/", 1)[0])
            if _parents:
                _dir_file_parents = True
                _dir_roots = sorted(_parents)
            wd = watch_dir.resolve()
            mon = []
            unmapped = []
            for tp in tps:
                if should_ignore(str(tp), conf["ignore"], conf["allow_ext"], watch_dir=watch_dir):
                    continue
                rel = _rel_under_watch(tp, watch_dir)
                if not rel:
                    unmapped.append(str(tp))
                    continue
                rp = (conf["remote_base"].rstrip("/") + "/" + rel) if conf["remote_base"] else rel
                if tp.is_symlink():
                    f = link_finger(tp, wd)
                    if f["size"] < 0:
                        continue
                    if not f.get("link_safe"):
                        print(f"⚠ 跳过越界/绝对路径软链接: {rel} -> {f.get('link_target') or '?'} "
                              f"(目标在 watch_dir 外, 不上传目标内容)", file=sys.stderr)
                        continue
                    mon.append({
                        "local": str(tp), "remote": rp,
                        "size": 0, "mtime": f["mtime"], "mtime_ns": f["mtime_ns"],
                        "sha1": f["sha1"], "md5": f["md5"], "mode": f["mode"],
                        "type": "watch", "source": f"watch:{rel}",
                        "is_symlink": True, "link_target": f["link_target"],
                        "link_target_rel": f["link_target_rel"], "link_broken": f["link_broken"],
                    })
                    continue
                f = finger(tp)
                if f["size"] >= 0:
                    mon.append({
                        "local": str(tp), "remote": rp,
                        "size": f["size"], "mtime": f["mtime"], "mtime_ns": f["mtime_ns"],
                        "sha1": f["sha1"], "md5": f["md5"], "mode": f["mode"],
                        "type": "watch", "source": f"watch:{rel}",
                    })
            if unmapped:
                print(f"⚠ {len(unmapped)} 个文件不在监控目录内, 已跳过推送:", file=sys.stderr)
                for _u in unmapped:
                    print(f"   - {_u}  (监控目录: {watch_dir}, 请使用相对该目录的路径)", file=sys.stderr)
            if not mon:
                # 能走到这里说明文件存在但被 IGNORE 忽略或不在扫描范围
                print(f"❌ 文件存在但未被扫描 (可能被 IGNORE 规则忽略): {', '.join(tgt_list)}")
                print(f"   当前 IGNORE: {conf.get('ignore', [])}")
                return 2
    if mon is None:
        mon = scan_watch_dir(watch_dir, conf["remote_base"], conf["ignore"], conf["allow_ext"])
        if tgt_list:
            filtered = []
            for tgt in tgt_list:
                tp = Path(tgt)
                if not tp.is_absolute():
                    tp = Path(conf["watch_dir"]) / tp
                tp = Path(os.path.abspath(str(tp)))
                # AI 友好: 先检查文件是否存在, 区分"不存在"/"被忽略"/"不在范围"
                if not os.path.lexists(str(tp)):
                    print(f"❌ 文件/目录不存在: {tgt} (解析路径: {tp})")
                    return 2
                _tgt_rel = _rel_under_watch(tp, watch_dir)
                if tp.is_dir() and not tp.is_symlink():
                    if _tgt_rel is None:
                        continue
                    _prefix = _tgt_rel.rstrip("/") + "/"
                    filtered.extend(m for m in mon
                                    if (_rel_under_watch(Path(m["local"]), watch_dir) or "").startswith(_prefix))
                else:
                    filtered.extend(m for m in mon
                                    if _rel_under_watch(Path(m["local"]), watch_dir) == _tgt_rel)
            mon = filtered
            if not mon and not _dir_explicit:
                # 能走到这里说明文件存在但被 IGNORE 忽略或不在扫描范围
                print(f"❌ 文件存在但未被扫描 (可能被 IGNORE 规则忽略): {', '.join(tgt_list)}")
                print(f"   当前 IGNORE: {conf.get('ignore', [])}")
                return 2
    if fast_mode and "--json" not in sys.argv:
        # 🆕 v4.12-FIX: --json 时跳过人类提示, stdout 保持纯 JSON (否则 merge 失败丢字段)
        print(f"⚡ 快路径: 跳过全量扫描, 仅处理 {len(mon)} 个目标文件")
    if fast_mode:
        # 文件目标: _dir_roots 已由上面的父目录链设置; 根目录文件保持 [] (不触碰目录状态)
        pass
    # 计算 dirty
    up_todo = []
    for m in mon:
        prev = snap.get(m["local"])
        # BUG-26: snapshot.md5 是 fake 占位符 → 强制 dirty (未真正同步过).
        if _snap_has_fake_md5(prev) or (prev.get("size") != m["size"] or prev.get("sha1") != m["sha1"]
                or prev.get("remote") != m.get("remote")
                or _mode_changed(prev, m)):
            up_todo.append(m)
    # 🆕 v4.8-DRYJSON: --json 时跳过人类打印, stdout 保持纯 JSON 供封装层 merge
    #   (v4.12.2: 扩展为 --json 一律跳过, 含非 dry-run 实际执行)
    if "--json" not in sys.argv:
        print(f"\n📤 推送 {len(up_todo)} 个变更文件 本地 → 远程")
    if dry_run:
        if (_dir_explicit or _dir_file_parents) and _dir_roots != [] and "--json" not in sys.argv:
            print(f"  [DRY-RUN] 目录状态: {len(scan_local_dirs(watch_dir, conf['ignore'], _dir_roots))} 个目录 (mode/空目录)")
        # 🆕 v4.8-DRYJSON: --json 时输出结构化 files 清单 (AI 无需 parse 人类文本).
        #   status: prev 无记录/fake md5 → new (从未真正同步); prev 有记录但变化 → modified.
        #   单行 JSON, main 封装层会自动 merge 到 payload 顶层.
        if "--json" in sys.argv:
            _files = []
            for m in up_todo:
                _pv = snap.get(m["local"])
                _st = "modified" if _pv and not _snap_has_fake_md5(_pv) else "new"
                _files.append({"path": m["remote"], "status": _st})
            print(json.dumps({"command": "push", "dry_run": True, "total": len(up_todo),
                              "files": _files}, ensure_ascii=False))
            return 0
        if plain:
            print(f"push={len(up_todo)} result=dry_run")
            for m in up_todo[:50]: print(f"dry {m['remote']}")
            return 0
        for m in up_todo: print(f"  [DRY-RUN] {m['remote']}")
        return 0
    if not up_todo:
        # 显式目录目标 / 文件目标父目录: 即使无文件变更, 也同步目录 mode + 空目录
        if (_dir_explicit or _dir_file_parents) and _dir_roots != []:
            sync_local_dirs(api, conf, log, roots=_dir_roots)
        # 🆕 v4.23-FIX: 0 变更时 --json 也输出结构化契约 (此前只 print "无变更", AI 拿不到 uploaded/batched)
        if "--json" in sys.argv:
            print(json.dumps({"command": "push", "action": "push", "ok": True,
                              "uploaded": 0, "skipped": 0, "failed": 0, "batched": 0,
                              "failed_files": [], "result": "ok"}, ensure_ascii=False))
            return 0
        if plain:
            print("push=0 skipped=0 failed=0 result=ok")
            return 0
        print("  无变更")
        return 0
    # 远程 MD5 跳过 (修复5: 过滤掉 ai-sync/ 等 IGNORE 目录, 避免 checksum 卡死)
    # 🆕 v4.8-CHECK-OPT: 单点 checksum 条件从 fast_mode 扩展为 fast_mode 或 dirty 文件少(≤10).
    #   背景: snapshot 复用后全量 push 的 up_todo 通常 1-5 个, 但旧逻辑仍拉远程全量 3886 items
    #   (服务端要遍历全树算 hash, 压力大).  dirty 少时只对 up_todo 逐文件单点 checksum,
    #   服务端成本从全树遍历降到 N 个文件.  up_todo>10 视为大面积变更, 全量更划算.
    rmap = {}
    _use_pointwise = fast_mode or len(up_todo) <= 10
    if _use_pointwise:
        # 单点: 只对需要的文件 checksum (~0.3s/个), 跳过全量 3886 items 扫描
        # fast_mode 遍历 mon(目标列表); 否则遍历 up_todo(只对 dirty 文件)
        _todo_list = mon if fast_mode else up_todo
        for m in _todo_list:
            print(f"  [checksum] 远程比对 {m['remote']} ...", end="", flush=True, file=sys.stderr if "--json" in sys.argv else None)
            try:
                r = api.checksum_remote(m["remote"], "md5")
            except Exception:
                print(" ✗", file=sys.stderr if "--json" in sys.argv else None)
                continue
            print(" ✓", file=sys.stderr if "--json" in sys.argv else None)
            if r.get("ok"):
                for it in r.get("items", []):
                    p = it.get("path", "")
                    if any(glob_match(g, p) for g in conf.get("ignore", [])):
                        continue
                    rmap[p] = it.get("md5", "")
    else:
        print("  [checksum] 远程全量比对 ...", end="", flush=True, file=sys.stderr if "--json" in sys.argv else None)
        r = api.checksum_remote(conf["remote_base"] or ".", "md5")
        print(" ✓", file=sys.stderr if "--json" in sys.argv else None)
        if r.get("ok"):
            ignore_globs = conf.get("ignore", [])
            for it in r.get("items", []):
                p = it.get("path", "")
                # 跳过匹配 IGNORE 规则的远程文件 (如 ai-sync/*, cache/*)
                if any(glob_match(g, p) for g in ignore_globs):
                    continue
                rmap[p] = it.get("md5", "")
    # 🆕 v4.10-PUSH-DIFF: 推送前基线冲突检测 + 内容 diff 预览 (AI 一眼确认改对没/是否基于最新)
    _rc = _push_preview(conf, api, up_todo, snap, rmap, plain=plain, show_diff=show_diff)
    if _rc != 0:
        return _rc
    print("  [upload] 开始上传 ...", flush=True, file=sys.stderr if "--json" in sys.argv else None)
    uploader = Uploader(api, log, conf)
    # 🆕 v4.23: push 批次进度 (AI 脚本化解析; --json/plain 下不打进度, 保持输出纯净)
    _pb = None
    if "--json" not in sys.argv and not plain:
        def _pb(_done, _total):
            print(f"  [upload] 进度 {_done}/{_total} 文件", file=sys.stderr, flush=True)
    ok_n, fail_n, skip_n, batched = uploader.upload_items(up_todo, snap, dry_run=False,
                                                           remote_md5_map=rmap, progress_cb=_pb)
    _failed_files = list(getattr(uploader, "failed_list", []))
    snap.save(force=True)  # 修复: force 落盘, 避免节流跳过导致重复上传
    # 目录状态同步 (上传完成后执行: 上传会自动建父目录, 需回填真实 mode; 空目录一并创建)
    if _dir_roots != []:
        sync_local_dirs(api, conf, log, roots=_dir_roots)
    pushed = [{"local": m["local"], "remote": m["remote"], "md5": m.get("md5",""),
               "is_symlink": bool(m.get("is_symlink"))}
              for m in up_todo]
    _record_history(conf, "push", pushed)
    # 🆕 FIX-push-verify-v4.7: push 成功后自动复验远程 MD5 (快路径逐文件, ~0.3s/个)
    #    仅复验真正上传成功的文件; 不一致仅提示不阻断 (plain 模式保持纯净单行)
    if not plain and fail_n == 0 and pushed:
        print("  [verify] 上传后复验 MD5 ...", flush=True, file=sys.stderr if "--json" in sys.argv else None)
        # 🆕 v4.8-VERIFY-PARALLEL: 并发复验 (max_workers=5), 原串行 ~0.3s/个 → 20 文件 6s 降到 ~1.2s.
        # [AI-SAFE v1.1.2] 复验并发提升到默认 12 (VERIFY_CONCURRENCY): 大目录 550 文件并发5 实测 26s → 并发12 ~9s.
        #   限流安全: api._req 有 429 backoff; finger hash 释放 GIL, 真并行. bad 收集保持原顺序.
        def _verify_one(m):
            try:
                r = api.checksum_remote(m["remote"], "md5")
                rmd5 = ""
                for it in r.get("items", []):
                    p = it.get("path", "").lstrip("/")
                    if p == m["remote"].lstrip("/") or p.endswith(m["remote"].lstrip("/")):
                        rmd5 = it.get("md5", "")
                        break
                if rmd5:
                    _lp = Path(m["local"])
                    if m.get("is_symlink"):
                        _lf = link_finger(_lp, Path(conf["watch_dir"]).resolve())
                        if _lf.get("link_safe") and _lf.get("md5") == rmd5:
                            return None
                        return m["remote"]
                    # fast 模式 md5 是 "size:mtime_ns" 占位符 → 复验必须重算真实 hash
                    # >20MB 大文件本地只算前缀hash, 与远程全量md5不可比 → 跳过
                    if _lp.stat().st_size > 20_000_000:
                        return None
                    loc_md5 = finger(_lp, mode="hash")["md5"]
                    if loc_md5 != rmd5:
                        return m["remote"]
            except Exception:
                return None
            return None
        bad = [res for res in ThreadPoolExecutor(max_workers=max(1, int(conf.get("verify_concurrency") or 12))).map(_verify_one, pushed) if res]
        if bad:
            print(f"⚠ {len(bad)} 个文件上传后 MD5 不一致(可能被服务端改写): {', '.join(bad)}", file=sys.stderr)
        elif "--json" not in sys.argv:
            print("  [verify] 全部一致 ✓")
    if "--json" in sys.argv:
        # 🆕 v4.12.2-JSON: 实际执行后结构化结果 (AI 直接拿 uploaded/failed, 无需 parse 人类摘要)
        # 🆕 v4.23: 追加 batched 与 failed_files 具体清单
        print(json.dumps({"command": "push", "action": "push", "ok": fail_n == 0,
                          "uploaded": ok_n, "skipped": skip_n, "failed": fail_n,
                          "batched": batched, "failed_files": _failed_files,
                          "result": "ok" if fail_n == 0 else "fail"}, ensure_ascii=False))
        _record_history(conf, "push", pushed)
        return 0 if fail_n == 0 else 4
    if plain:
        # 🆕 FIX-plain-v4.6: --push --plain 单行汇总 (AI 断言 result=ok 即全部成功)
        # 🆕 v4.23: 追加 batched 与失败文件清单
        print(f"uploaded={ok_n} skipped={skip_n} failed={fail_n} batched={batched} "
              f"failed_files={','.join(_failed_files)} result={'ok' if fail_n == 0 else 'fail'}")
        _record_history(conf, "push", pushed)
        return 0 if fail_n == 0 else 4
    print(f"\n摘要: 上传 {ok_n} 跳过 {skip_n} 失败 {fail_n}")
    # 🆕 v4.23: 列出具体失败文件 (AI 可直接取路径重试), 替代仅失败计数
    if fail_n > 0:
        print(f"\n⚠ {fail_n} 个文件推送失败:")
        for _fp in _failed_files or []:
            print(f"   - {_fp}  (可重试: --push {_fp} --allow-destructive)")
        print("  失败原因查 --error-log / 服务器磁盘权限; 或逐文件 --push 重试")
    return 0 if fail_n == 0 else 4

# BUG-25 修复: 移除重复的 _resolve_remote_path 定义 (此处在下方 line ~3654 有同名函数覆盖,
#   此定义是死代码, 维护者改这里以为生效实际无效). 下方版本逻辑等价 (as_posix vs str().replace 等价).

def cmd_test(conf: dict, log: Logger, api: ApiClient, target: str,
             timeout: int = 15, plain: bool = False) -> int:
    """在服务器安全执行 PHP 文件并返回输出 (lint + 沙箱)。"""
    rp = _resolve_remote_path(conf, target)
    if not plain and "--json" not in sys.argv:
        print(f"\n🧪 服务器执行 PHP: {rp} (timeout={timeout}s)\n")
    r = api.php_run(rp, timeout=timeout)
    if not r.get("ok"):
        _extra = ""
        _err = str(r.get("error", ""))
        if "not_found" in _err:
            if Path(target).is_absolute():
                _extra = " (输入的是绝对路径? 请用 WATCH_DIR 内的相对路径, 如 TV/config.php)"
            else:
                _extra = " (⚠️ --test 执行【远程】文件: 本地文件先 --upload-file <相对WATCH_DIR路径> 再 --test)"
        if "--json" in sys.argv:
            # 🆕 FIX-test-json-v4.21: 失败路径也输出纯 JSON 契约 (此前 stdout 无输出, AI json.loads 失败)
            print(json.dumps({"command": "test", "file": rp, "ok": False, "rc": None,
                              "error": r.get("error", ""), "message": r.get("message", ""),
                              "fix_hint": "本地文件先 --upload-file <相对WATCH_DIR路径> 再 --test; 或确认远程路径存在"},
                             ensure_ascii=False))
        elif plain:
            print(f"file={rp} ok=false error={r.get('error','')} {r.get('message','')}{_extra}", file=sys.stderr)
        else:
            print(f"❌ 执行失败: {r.get('error','')} {r.get('message','')}{_extra}", file=sys.stderr)
        return 2
    rc = r.get("return_code", -1)
    stdout = r.get("stdout", "")
    stderr = r.get("stderr", "")
    if "--json" in sys.argv:
        # 🆕 v4.12.4-JSON: test 结构化 (AI 直接拿 rc/stdout/stderr)
        print(json.dumps({"command": "test", "file": rp, "rc": rc,
                          "ok": rc == 0, "stdout": stdout[:4000], "stderr": stderr[:2000]},
                         ensure_ascii=False))
        _record_history(conf, "test", [{"remote": rp, "rc": rc}])
        return 0 if rc == 0 else 1
    if plain:
        # 🆕 FIX-plain-v4.6: --test --plain 单行键值 + 原样 stdout
        out_first = (stdout.strip().splitlines() or [""])[0][:80] if stdout.strip() else ""
        print(f"file={rp} rc={rc} ok={'true' if rc == 0 else 'false'}"
              + (f" out1={out_first!r}" if out_first else "")
              + (f" err={stderr[:120]!r}" if stderr.strip() else ""))
        if stdout.strip(): print(stdout)
        return 0 if rc == 0 else 1
    print(f"[return_code={rc}]")
    if stdout:
        print("--- stdout ---")
        print(stdout)
    if stderr:
        print("--- stderr ---")
        print(stderr)
    _record_history(conf, "test", [{"remote": rp, "rc": rc}])
    return 0 if rc == 0 else 1

def cmd_rollback(conf: dict, log: Logger, api: ApiClient, target: str,
                 backup_file: str | None = None) -> int:
    """从远程备份恢复文件。不指定 backup_file 则列出可用备份。"""
    # 绝对路径(/opt/... 等设备真实路径)必须经 _server_rel 换算成 scope 相对路径,
    # 否则会拼出 client-test/opt/... 这种不存在的路径, 备份永远查不到。
    rp = _server_rel(conf, api, target)
    if not backup_file:
        r = api.backup_ls(rp)
        if not r.get("ok"):
            if "--json" in sys.argv:
                print(json.dumps({"command": "rollback", "ok": False, "error": r.get("error", "")}, ensure_ascii=False))
            else:
                print(f"❌ 查询备份失败: {r.get('error','')}")
            return 2
        backups = r.get("backups") or r.get("items") or []
        if isinstance(backups, dict): backups = list(backups.values())
        if not backups:
            if "--json" in sys.argv:
                print(json.dumps({"command": "rollback", "ok": False, "error": "no_backups", "path": rp}, ensure_ascii=False))
            else:
                print(f"ℹ {rp} 无历史备份")
            return 0
        if "--json" in sys.argv:
            _bl = [{"file": (b.get("file") or b.get("backup_file") or b.get("backup") or b.get("name") or "?"),
                    "size": b.get("size", "-"), "mtime": b.get("mtime", 0)} for b in backups]
            print(json.dumps({"command": "rollback", "ok": True, "path": rp, "backups": _bl}, ensure_ascii=False))
            return 0
        print(f"\n📜 {rp} 的历史备份 ({len(backups)}):\n")
        for i, b in enumerate(backups):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b.get("mtime",0)))
            fn = (b.get("file") or b.get("backup_file") or b.get("backup") or b.get("name") or "?")
            size = b.get("size","-")
            size_str = f"{size}B" if isinstance(size,(int,float)) else str(size)
            print(f"  [{i}] {fn}  {size_str}  {ts}")
        print(f"\n用法: --rollback <file> --backup <backup_file>")
        return 0
    # 执行恢复
    # 🆕 FIX-yeshint-v4.6: 非交互拒绝时给真实完整命令 (AI 可直接加 --yes 照抄执行)
    conf_name = os.path.basename(conf.get("conf_path", "sync.conf")) if conf.get("conf_path") else "sync.conf"
    hint = f"python3 sync.py --conf {conf_name} --rollback {target} --backup {backup_file} --yes"
    if not _confirm_danger("回滚文件", f"将用备份 {backup_file} 覆盖 {rp} (当前版本会被自动备份)", hint_cmd=hint):
        return 99  # v4.29: 阻断退出码对齐契约
    r = api.backup_rb(rp, backup_file)
    if not r.get("ok"):
        # 🆕 FIX-rollback-json-fail-v4.17: 失败路径 --json 也输出纯 JSON (对齐 mv/cp 契约)
        if "--json" in sys.argv:
            print(json.dumps({"command": "rollback", "ok": False, "restored": False,
                              "from": rp, "backup": backup_file,
                              "error": r.get("error", ""), "message": r.get("message", "")}, ensure_ascii=False))
        else:
            print(f"❌ 恢复失败: {r.get('error','')} {r.get('message','')}")
        return 2
    # 🆕 FIX-rollback-json-v4.17: --json 模式 stdout 保持纯净 —
    #   此前恢复成功会 print "远程已恢复/正在拉回本地" 再叠加 pull 的 JSON, 混行导致 json.loads 失败.
    if "--json" in sys.argv:
        import io as _io
        _old_stdout = sys.stdout
        sys.stdout = _io.StringIO()
        try:
            _pull_rc = cmd_pull(conf, log, api, target=rp)
        finally:
            _captured = sys.stdout.getvalue()
            sys.stdout = _old_stdout
        _pull_json = {}
        try:
            _pull_json = json.loads(_captured.strip())
        except Exception:
            _pull_json = {"raw": _captured.strip()[:200]}
        print(json.dumps({"command": "rollback", "ok": True, "restored": True,
                          "from": rp, "backup": backup_file,
                          "pull": _pull_json, "result": "ok"}, ensure_ascii=False))
        return _pull_rc
    # 远程恢复后, 拉回本地保持一致
    print(f"✅ 远程已恢复: {rp} ← {backup_file}")
    print("  正在拉回本地...")
    return cmd_pull(conf, log, api, target=rp)

def cmd_history(conf: dict, log: Logger, api: ApiClient, limit: int = 20, plain: bool = False) -> int:
    """查看变更历史。"""
    p = _history_path(conf)
    if not p.is_file():
        print("无历史记录")
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        print("❌ 历史文件损坏")
        return 2
    if not data:
        print("无历史记录")
        return 0
    if "--brief" in sys.argv:
        # 🆕 FIX-brief-v4.17: 只输出 total + 最近 3 条 action (AI 快速判断有无异常操作)
        _recent = [{"time": h.get("t", ""), "action": h.get("action", "?"),
                    "count": len(h.get("items", []))} for h in data[-3:]]
        if "--json" in sys.argv:
            print(json.dumps({"command": "history", "total": len(data), "recent": _recent}, ensure_ascii=False))
        else:
            print(f"total={len(data)} recent={','.join(r['action'] + '@' + r['time'] for r in _recent)} ok=true")
        return 0
    if "--json" in sys.argv:
        # 🆕 v4.12.3-JSON: history 结构化 entries[] (AI 回滚决策依据)
        print(json.dumps({"command": "history", "total": len(data), "limit": min(limit, len(data)),
                          "entries": [{"time": h.get('t', ''), "action": h.get('action', '?'),
                                       "count": len(h.get('items', [])),
                                       "files": [it.get("remote", it.get("local", it.get("file", ""))).split("/")[-1]
                                                 for it in h.get("items", [])[:20]]}
                                      for h in data[-limit:]]}, ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --history --plain 无 emoji/装饰
        for h in data[-limit:]:
            items = h.get("items", [])
            sample_parts = []
            for it in items[:5]:
                if "remote" in it:
                    sample_parts.append(it["remote"].split("/")[-1])
                elif "local" in it:
                    sample_parts.append(Path(it["local"]).name)
                elif "count" in it:
                    extra = []
                    if "ok" in it: extra.append(f"ok={it['ok']}")
                    if "diff" in it and it['diff']: extra.append(f"diff={it['diff']}")
                    if "miss" in it and it['miss']: extra.append(f"miss={it['miss']}")
                    sample_parts.append(f"count={it['count']}" + (f"({','.join(extra)})" if extra else ""))
                elif "file" in it:
                    sample_parts.append(it["file"])
                else:
                    sample_parts.append("?")
            sample = ", ".join(sample_parts)
            if len(items) > 5: sample += f" +{len(items)-5}"
            print(f"{h.get('t')} {h.get('action','?')} {len(items)} {sample}")
        return 0
    print(f"\n📜 变更历史 (最近 {min(limit, len(data))} 条, 共 {len(data)}):\n")
    for h in data[-limit:]:
        items = h.get("items", [])
        sample_parts = []
        for it in items[:5]:
            if "remote" in it:
                sample_parts.append(it["remote"].split("/")[-1])
            elif "local" in it:
                sample_parts.append(Path(it["local"]).name)
            elif "count" in it:
                # verify/backup_list 等摘要型记录
                extra = []
                if "ok" in it: extra.append(f"ok={it['ok']}")
                if "diff" in it and it['diff']: extra.append(f"diff={it['diff']}")
                if "miss" in it and it['miss']: extra.append(f"miss={it['miss']}")
                sample_parts.append(f"count={it['count']}" + (f"({','.join(extra)})" if extra else ""))
            elif "file" in it:
                sample_parts.append(it["file"])
            else:
                sample_parts.append("?")
        sample = ", ".join(sample_parts)
        if len(items) > 5: sample += f" +{len(items)-5}"
        print(f"  [{h.get('t')}] {h.get('action','?'):6s} {len(items)} 项  {sample}")
    return 0

def cmd_verify(conf: dict, log: Logger, api: ApiClient, target: str | list | None = None,
               plain: bool = False) -> int:
    """端到端一致性验证: 本地 MD5 vs 远程 MD5 vs 快照 MD5, 三方比对。
    target: 单文件/多文件列表 (本地路径或远程相对路径); None/__ALL__=全部 watch 文件。
    🆕 v4.18: 支持多文件批量 --verify a b c (单文件输出形状不变, 多文件累积 results[])."""
    watch_dir = Path(conf["watch_dir"]).resolve()
    # 🆕 FIX-verify-hint-v4.7: 无 target 时全量扫描较慢, 提前提示 (AI 可指定文件走快路径)
    if not target and not bool(getattr(sys, "_AI_SYNC_QUIET", False)):
        print("⏳ 全量校验: 本地全量扫描约需 25s; 建议 --verify <file> 指定单文件(快路径)", file=sys.stderr)
    mon = scan_watch_dir(watch_dir, conf["remote_base"], conf["ignore"], conf["allow_ext"])

    def _local_verify_md5(m: dict) -> str:
        """本地内容指纹: 普通文件=md5(内容), 软链接=md5(规范化目标) (绝不读目标内容)."""
        lp = Path(m["local"])
        if m.get("is_symlink") or os.path.islink(str(lp)):
            lf = link_finger(lp, watch_dir)
            if lf["size"] < 0 or not lf.get("link_safe"):
                raise OSError(f"不安全的软链接: {lf.get('link_reason') or 'unsafe'}")
            return lf["md5"]
        return hashlib.md5(lp.read_bytes()).hexdigest()

    if target:
        if isinstance(target, str):
            target = [target]
        targets = [t for t in target if t != "__ALL__"]
        if targets:
            sel: dict[str, dict] = {}
            for t in targets:
                rp = _resolve_remote_path(conf, t)
                rb = (conf.get("remote_base") or "").strip().strip("/")
                rp_prefixed = (rb + "/" + rp) if rb else rp
                # 同时按本地绝对路径、无前缀远程相对路径、带前缀远程路径匹配 (FIX-path-v4.6 统一语义)
                # FIX-symlink-path: 词法相对路径匹配, 不 resolve() — 链接本身也要可 --verify
                tp_local = None
                if Path(t).is_absolute():
                    tp_local = _rel_under_watch(Path(t), watch_dir)
                matched = [m for m in mon
                           if m["remote"] == rp_prefixed or m["remote"] == rp
                           or (tp_local is not None
                               and _rel_under_watch(Path(m["local"]), watch_dir) == tp_local)]
                if not matched:
                    print(f"❌ 文件不在监控范围: {t} (⚠️ --verify 依赖监控快照, 先 --pull-smart {t} 建立后再验)")
                    return 2
                for m in matched:
                    sel[m["remote"]] = m
            mon = list(sel.values())

    # 1) 拉取远程 MD5 (一次 checksum 调用)
    rmap: dict[str, str] = {}
    r = api.checksum_remote(conf["remote_base"] or ".", "md5")
    if r.get("ok"):
        for it in r.get("items", []):
            p = it.get("path", "")
            # 兼容 P0/P1 后可能产生的 md5/md5 字段 (算法作为 key 的字符串) 或统一 md5 key
            rmap[p] = (it.get("md5") or it.get("MD5") or it.get("md5sum")
                       or next((str(v) for k, v in it.items()
                                if isinstance(k, str) and k.lower() in ("md5","sha1","sha256","sha384","sha512","crc32","crc32b","adler32") and isinstance(v, str) and len(v) >= 8), ""))
    else:
        print(f"❌ 远程 checksum 失败: {r.get('error', '')}")
        return 2

    snap = Snapshot(watch_dir / ".sync_snapshot.json")
    # 🆕 v4.11-JSON: --json 时收集结构化结果, stdout 只输出单行 JSON (自动 merge 到顶层)
    _json_mode = "--json" in sys.argv
    if plain:
        # 🆕 FIX-plain-v4.6: --verify --plain 只输出单行汇总 + 不一致明细 (AI 断言 ok=true 即全部一致)
        ok_n = diff_n = miss_n = snap_drift_n = 0
        snap_fixed = 0
        for m in mon:
            lp = Path(m["local"])
            try:
                # FIX-symlink-verify: 链接用 md5(规范化目标), 不能读目标内容
                local_md5 = _local_verify_md5(m)
            except Exception:
                miss_n += 1
                continue
            remote_md5 = rmap.get(m["remote"], "")
            prev = snap.get(m["local"])
            snap_md5 = prev.get("md5", "") if prev else ""
            snap_md5_is_fake = snap_md5.startswith("f:") if snap_md5 else False
            if not remote_md5:
                miss_n += 1
            elif local_md5 == remote_md5:
                if not snap_md5 or snap_md5_is_fake or snap_md5 == local_md5:
                    if snap_md5_is_fake:
                        snap.update(m["local"], {**prev, "md5": local_md5})
                        snap_fixed += 1
                    elif not snap_md5 or snap_md5 == local_md5:
                        pass
                    else:
                        snap_drift_n += 1
                    ok_n += 1
                else:
                    snap_drift_n += 1
                    snap.update(m["local"], {**prev, "md5": local_md5})
                    snap_fixed += 1
            else:
                diff_n += 1
                print(f"diff file={m['remote']} local={local_md5[:8]} remote={remote_md5[:8]}")
        if snap_fixed > 0:
            snap.save(force=True)
        print(f"ok={ok_n} diff={diff_n} miss={miss_n} snap_drift={snap_drift_n} fixed={snap_fixed} "
              f"result={'pass' if (diff_n + miss_n) == 0 else 'fail'}")
        _record_history(conf, "verify", [{"count": len(mon), "ok": ok_n, "diff": diff_n,
                                           "miss": miss_n, "snap_drift": snap_drift_n, "fixed": snap_fixed}])
        return 0 if (diff_n + miss_n) == 0 else 1
    if _json_mode:
        _json_results = []
    else:
        print(f"\n🔍 一致性验证 ({len(mon)} 个文件)\n")
        print(f"  {'文件':<40} {'本地':>10} {'远程':>10} {'快照':>10}  状态")
        print(f"  {'-'*40} {'-'*10} {'-'*10} {'-'*10}  ----")
    ok_n = diff_n = miss_n = snap_drift_n = 0
    snap_fixed = 0
    for m in mon:
        lp = Path(m["local"])
        try:
            # FIX-symlink-verify: 链接用 md5(规范化目标), 不能读目标内容
            local_md5 = _local_verify_md5(m)
        except Exception as e:
            if not _json_mode:
                print(f"  {m['remote'][:40]:<40} {'-':>10} {'-':>10} {'-':>10}  ❌ 本地读取失败: {e}")
            miss_n += 1
            if _json_mode:
                _json_results.append({"path": m["remote"], "local": "", "remote": "",
                                      "snapshot": "", "status": "read_error"})
            continue
        remote_md5 = rmap.get(m["remote"], "")
        prev = snap.get(m["local"])
        snap_md5 = prev.get("md5", "") if prev else ""
        # BUG-9: snap_md5 可能是历史写入的 "f:..." fake md5
        snap_md5_is_fake = snap_md5.startswith("f:") if snap_md5 else False
        if not remote_md5:
            status = "❌ 远程缺失"
            miss_n += 1
        elif local_md5 == remote_md5:
            # BUG-18 修复: 快照落后单独计数, 不混入 ok_n
            if not snap_md5 or snap_md5_is_fake or snap_md5 == local_md5:
                if snap_md5_is_fake:
                    # BUG-19 修复: 自动修正 fake md5 快照漂移
                    snap.update(m["local"], {**prev, "md5": local_md5})
                    snap_fixed += 1
                    status = "✅ 一致 (快照已修正)"
                elif snap_md5 == local_md5:
                    status = "✅ 一致"
                else:
                    status = "✅ 一致 (无快照基线)"
                ok_n += 1
            else:
                # 快照记录与实际不符 → 漂移
                snap_drift_n += 1
                # BUG-19: 自动修正快照漂移 (本地=远程, 快照落后)
                snap.update(m["local"], {**prev, "md5": local_md5})
                snap_fixed += 1
                status = "⚠ 快照漂移 (已自动修正)"
        else:
            status = "⚠ 不一致 (本地≠远程)"
            diff_n += 1
        if _json_mode:
            _json_results.append({"path": m["remote"], "local": local_md5[:8],
                                  "remote": remote_md5[:8], "snapshot": snap_md5[:8],
                                  "status": ("identical" if status.startswith("✅")
                                             else "snap_drift" if status.startswith("⚠ 快照")
                                             else "mismatch" if status.startswith("⚠")
                                             else "missing")})
        else:
            print(f"  {m['remote'][:40]:<40} {local_md5[:8]:>10} {remote_md5[:8]:>10} {snap_md5[:8]:>10}  {status}")
    # BUG-19: 如果修正了快照, 强制落盘
    if snap_fixed > 0:
        snap.save(force=True)
    if _json_mode:
        print(json.dumps({"command": "verify", "total": len(mon), "identical": ok_n,
                          "mismatch": diff_n, "missing": miss_n, "snap_drift": snap_drift_n,
                          "fixed": snap_fixed,
                          "ok": (diff_n + miss_n) == 0,
                          "result": "pass" if (diff_n + miss_n) == 0 else "fail",
                          "results": _json_results}, ensure_ascii=False))
    else:
        print(f"\n摘要: 一致 {ok_n}  不一致 {diff_n}  缺失 {miss_n}  快照漂移 {snap_drift_n} (已修正 {snap_fixed})")
    _record_history(conf, "verify", [{"count": len(mon), "ok": ok_n, "diff": diff_n,
                                       "miss": miss_n, "snap_drift": snap_drift_n, "fixed": snap_fixed}])
    return 0 if (diff_n + miss_n) == 0 else 1

def _deploy_note_snapshot(conf: dict, files: list, deploy_id: str) -> int:
    """部署成功后把本地文件记入快照, 让 --status 立即显示已同步.
    否则 batch_deploy 成功了但快照仍是旧值, 每次 deploy 完都残留 DIRTY, AI 会误判需要再 push."""
    try:
        watch = Path(conf["watch_dir"])
        snap = Snapshot(watch / ".sync_snapshot.json")
        n = 0
        for f in files:
            lp = Path(f.get("local", ""))
            if not lp.is_absolute():
                lp = watch / lp
            try:
                lp = Path(os.path.abspath(str(lp)))
                if not lp.is_file():
                    continue
                fg = finger(lp, mode="hash")
                snap.update(str(lp), {
                    "size": fg["size"], "sha1": fg["sha1"], "md5": fg["md5"],
                    "remote": f.get("path", ""), "mtime": fg["mtime"], "mode": fg["mode"],
                    "source": f"deploy:{deploy_id}",
                })
                n += 1
            except Exception:
                continue
        if n:
            snap.save(force=True)
        return n
    except Exception:
        return 0

def cmd_deploy(conf: dict, log: Logger, api: ApiClient, paths: list[str],
               pre_check: str = "", post_check: str = "", post_expect: str = "",
               dry_run: bool = False, plain: bool = False, smart_verify: str = "",
               nginx_reload: bool = False, health_url: str = "", health_expect: str = "",
               lint_first: bool = False, http_check: str = "", http_expect: str = "") -> int:
    """原子批量部署: 将多个文件一次性推到服务器, pre/post check + 失败自动回滚。
    smart_verify="md5" 时部署成功后逐个复验远程 MD5 (与本地真实 hash 比对)。
    🆕 FIX-lint-first-v4.17: --lint-first 部署前本地 node --check 校验 JS/TS/HTML (PHP 已由服务端 php -l 覆盖),
    lint 失败则不部署 (返回 lint_errors), 防"坏代码上线后靠回滚". 只查待部署的少数文件, 服务器零负担."""
    watch = Path(conf["watch_dir"])
    remote_base = conf.get("remote_base") or "."
    # 🆕 FIX-ad-prefix-v4.21: --allow-destructive <前缀> 对 deploy 生效 —
    #    前置校验: 待部署所有文件必须落在授权前缀内 (AI 用路径前缀限定范围, 不再只能无值=全部放行)
    _ad = _ALLOW_DESTRUCTIVE
    if _ad and _ad != "*" and not dry_run and paths:
        _bad_prefix = []
        for _p in paths:
            _lp2 = Path(_p)
            if not _lp2.is_absolute():
                _lp2 = watch / _lp2
            try:
                _rel2 = str(_lp2.relative_to(watch)).replace("\\", "/")
            except ValueError:
                _rel2 = str(_lp2)
            if not (_rel2.rstrip("/") == _ad.rstrip("/") or _rel2.startswith(_ad.rstrip("/") + "/")):
                _bad_prefix.append(_rel2)
        if _bad_prefix:
            if "--json" in sys.argv:
                print(json.dumps({"command": "deploy", "blocked": True, "level": "REQUIRE_AUTH",
                                  "message": f"前缀授权未覆盖待部署文件: {', '.join(_bad_prefix[:3])}",
                                  "authorized_prefix": _ad, "category": "aisafe-prefix",
                                  "hint": f"授权前缀需用 scope 相对路径 (如 TV/assets/js/): --allow-destructive {_ad.rstrip('/') + '/'} 覆盖全部路径, 或无值 --allow-destructive"},
                                 ensure_ascii=False))
            else:
                print(f"\n[BLOCKED] 前缀授权未覆盖待部署文件: {', '.join(_bad_prefix[:3])}")
                print(f"   授权前缀: {_ad}")
                print(f"   如需放行: 授权前缀需用 scope 相对路径 (如 TV/assets/js/, 勿用服务器绝对路径), 或无值 --allow-destructive 全部放行")
            return 2
    if not dry_run and not _confirm_danger("批量部署", f"将部署 {len(paths)} 个文件到远程 (远程文件被覆盖前自动备份, 失败自动回滚)",
                                           target=(_ad or "")):
        return 99  # v4.29: 阻断退出码对齐契约
    # 🆕 FIX-lint-first-v4.17: 本地 JS/TS/HTML 语法预检 (不占用服务器; PHP 走服务端 pre_check php -l)
    if lint_first and not dry_run:
        _lint_errs = []
        _js_exts = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".html")
        for _f in paths:
            _lp = Path(_f)
            if not _lp.is_absolute():
                _lp = Path(conf["watch_dir"]) / _lp
            _lp = Path(os.path.abspath(str(_lp)))
            if _lp.suffix.lower() not in _js_exts:
                continue
            # HTML 不能直接 node --check (会报 ERR_UNKNOWN_FILE_EXTENSION);
            # 复用 _syntax_check_local: 它会提取 <script> 再检查, 无 script 视为通过.
            try:
                _sc = _syntax_check_local(_lp.read_bytes(), _lp.name)
            except Exception as _le2:
                _lint_errs.append({"path": _f, "error": str(_le2)[:200]})
                continue
            if _sc.get("checked") and not _sc.get("ok"):
                _lint_errs.append({"path": _f, "error": _sc.get("error", "syntax error")})
        if _lint_errs:
            if "--json" in sys.argv:
                print(json.dumps({"command": "deploy", "ok": False, "result": "lint-fail",
                                  "lint_errors": _lint_errs,
                                  "hint": "修复本地语法错误后重新 --deploy (lint-first 已拦截, 未部署)"},
                                 ensure_ascii=False))
            else:
                for _le in _lint_errs:
                    print(f"❌ lint 失败: {_le['path']}: {_le['error'][:120]}", file=sys.stderr)
                print("⛔ --lint-first 拦截: 存在语法错误, 未部署", file=sys.stderr)
            return 2
    files = []
    for p in paths:
        local = Path(p)
        if not local.is_absolute():
            local = watch / local
        local = Path(os.path.abspath(str(local)))
        # FIX-symlink: deploy 是原子批处理, 不展开软链接 (否则会静默上传目标内容).
        #   软链接请用 --push / --upload-file, 走链接协议还原链接本身.
        if local.is_symlink():
            _f = link_finger(local, watch.resolve())
            _rp = _rel_under_watch(local, watch) or local.name
            _msg = (f"deploy 不支持软链接 (避免解引用目标内容): {_rp} -> {_f.get('link_target')}; "
                    f"请改用 --push {_rp} 或 --upload-file {_rp}")
            if "--json" in sys.argv:
                print(json.dumps({"command": "deploy", "ok": False, "result": "fail",
                                  "files": len(paths), "error": "symlink_not_supported",
                                  "path": _rp, "link_target": _f.get("link_target"),
                                  "hint": _msg}, ensure_ascii=False))
            else:
                print(f"❌ {_msg}", file=sys.stderr)
            return 1
        if not local.is_file():
            # 🆕 FIX-deploy-json-fail-v4.17: 失败路径 --json 也输出纯 JSON (对齐其他命令契约)
            if "--json" in sys.argv:
                print(json.dumps({"command": "deploy", "ok": False, "result": "fail",
                                  "files": len(paths), "error": f"not_found:{local}"}, ensure_ascii=False))
            else:
                print(f"❌ 文件不存在: {local}", file=sys.stderr)
            return 1
        data = local.read_bytes()
        # 计算远程相对路径
        try:
            rel = str(local.relative_to(watch)).replace("\\", "/")
        except ValueError:
            rel = local.name
        rp = _resolve_remote_path(conf, rel) if remote_base else rel
        # BUG-22 修复: 保留本地文件真实权限, 不再硬编码 "0644" (丢执行位/密钥权限过宽)
        try:
            file_mode = oct(local.stat().st_mode & 0o777)[2:].zfill(4)
        except Exception:
            file_mode = "0644"
        # FIX-deploy-mtime: 补 mtime, 与 --push 行为对齐 (服务端 batch_deploy 支持该字段;
        #   原实现只传 mode, 导致 --deploy 后远端文件时间戳全变成当前时刻)
        try:
            file_mtime = int(local.stat().st_mtime)
        except Exception:
            file_mtime = None
        files.append({
            "path": rp,
            "local": str(local),
            "content_b64": base64.b64encode(data).decode(),
            "mode": file_mode,
            "mtime": file_mtime,
        })

    # 🆕 FIX-P0-skip: 智能跳过 — 内容一致 (md5) 的文件不上传 (对齐 --push; 同样东西不重复传输)
    _deploy_total = len(files)
    _deploy_skip = 0
    if files:
        _skip_strategy = "md5" if _deploy_total <= 200 else "size_mtime"
        files = api._smart_filter_files(files, strategy=_skip_strategy)
        _deploy_skip = _deploy_total - len(files)

    if dry_run:
        if "--json" in sys.argv:
            # 🆕 v4.12-JSON: 结构化 files 清单 (对齐 push/pull dry-run)
            print(json.dumps({"command": "deploy", "dry_run": True, "total": _deploy_total,
                              "skipped": _deploy_skip, "to_deploy": len(files),
                              "files": [{"path": f["path"], "size": len(base64.b64decode(f["content_b64"])),
                                         "mode": f["mode"]} for f in files]}, ensure_ascii=False))
            return 0
        if plain:
            print(f"files={_deploy_total} skipped={_deploy_skip} to_deploy={len(files)} result=dry_run")
            for f in files: print(f"dry {f['path']}")
            return 0
        print(f"\n[演练] 将部署 {len(files)} 个文件 (跳过未变更 {_deploy_skip}), 不实际写入")
        if not files:
            print("✅ 全部文件未变更, 无需部署")
        return 0

    # 全部未变更 → 无需部署
    if not files:
        if "--json" in sys.argv:
            print(json.dumps({"command": "deploy", "ok": True, "result": "noop",
                              "total": _deploy_total, "skipped": _deploy_skip, "deployed": 0}, ensure_ascii=False))
        else:
            print(f"✅ 全部 {_deploy_total} 个文件未变更, 无需部署 (跳过 {_deploy_skip})")
        return 0

    # 默认对 PHP 文件做 pre_check
    if not pre_check:
        php_files = [f for f in files if f["path"].endswith(".php")]
        if php_files:
            pre_check = "php -l {file}"

    print(f"\n🚀 开始原子部署 {len(files)} 个文件 (deploy_id 待分配)...", file=sys.stderr if "--json" in sys.argv else None)
    if pre_check:
        print(f"   pre_check: {pre_check}", file=sys.stderr if "--json" in sys.argv else None)
    if post_check:
        print(f"   post_check: {post_check}" + (f" (期望: {post_expect})" if post_expect else ""),
              file=sys.stderr if "--json" in sys.argv else None)

    r = api.batch_deploy(files, pre_check=pre_check, post_check=post_check,
                         post_check_expect=post_expect, rollback_on_fail=True,
                         smart_skip=False)  # 🆕 FIX-P0-skip: 已在收集后手动 md5 过滤, 此处不再二次比对

    if not r.get("ok"):
        # 🆕 FIX-deploy-api-fail-json-v4.17: API 失败路径 --json 输出结构化 (deploy_id/rolled_back/errors),
        #   AI 部署失败后能直接断言是否回滚/错误详情, 不再只有人类文案
        if "--json" in sys.argv:
            print(json.dumps({
                "command": "deploy", "ok": False, "result": "fail",
                "deploy_id": r.get("deploy_id", "?"),
                "error": r.get("message", r.get("error", "unknown")),
                "rolled_back": r.get("rolled_back", {}),
                "pre_check": r.get("pre_check", {}),
                "errors": r.get("errors", []),
                "hint": "部署失败: pre_check 未过则文件未变更(无需回滚); 已部署部分看 rolled_back; 修本地文件后重试 --deploy",
            }, ensure_ascii=False))
            _record_history(conf, "deploy", [{"ok": False, "deploy_id": r.get("deploy_id", "?"),
                                              "files": [f["path"] for f in files]}])
            return 1
        print(f"\n❌ 部署失败: {r.get('message', r.get('error', 'unknown'))}")
        deploy_id = r.get("deploy_id", "?")
        print(f"   deploy_id: {deploy_id}")
        if "rolled_back" in r:
            rb = r["rolled_back"]
            if isinstance(rb, dict):
                ok_rb = sum(1 for v in rb.values() if isinstance(v, dict) and v.get("ok"))
                print(f"   已回滚: {ok_rb}/{len(rb)} 文件")
            elif isinstance(rb, list):
                ok_rb = sum(1 for v in rb if isinstance(v, dict) and v.get("ok"))
                print(f"   已回滚: {ok_rb}/{len(rb)} 文件")
        # 修复4: pre_check 可能是 string 而非 dict, 需类型检查防崩溃
        pre_resp = r.get("pre_check")
        if pre_resp:
            if isinstance(pre_resp, dict):
                for fpath, info in pre_resp.items():
                    if isinstance(info, dict) and not info.get("passed"):
                        print(f"   ❌ {fpath}: {info.get('output','').strip()}")
            elif isinstance(pre_resp, str):
                print(f"   ❌ pre_check: {pre_resp}")
        if "errors" in r:
            errs = r["errors"]
            if isinstance(errs, list):
                for e in errs:
                    print(f"   ❌ {e}")
            elif isinstance(errs, dict):
                for k, v in errs.items():
                    print(f"   ❌ {k}: {v}")
        if plain:
            err_summary = ""
            if "errors" in r:
                errs = r["errors"]
                if isinstance(errs, list) and errs:
                    err_summary = str(errs[0])[:120]
                elif isinstance(errs, dict) and errs:
                    err_summary = str(list(errs.items())[0])[:120]
            print(f"deploy_id={deploy_id} files={len(files)} result=fail"
                  + (f" error={err_summary}" if err_summary else ""))
        _record_history(conf, "deploy", [{"ok": False, "deploy_id": deploy_id, "files": [f["path"] for f in files]}])
        return 1

    deploy_id = r.get("deploy_id", "?")
    backed = r.get("backed_up")
    # 兼容 list 与 dict 两种返回 (list=[{"rel","backup"}], dict={rel:backup})
    backed_map = {}
    if isinstance(backed, list):
        for b in backed:
            if isinstance(b, dict): backed_map[b.get("rel") or b.get("path")] = b.get("backup")
    elif isinstance(backed, dict):
        backed_map = backed
    # 🆕 v4.17-deploy: 部署后链路验证 — nginx -t + reload + HTTP 健康检查
    #   (先执行存结果, 再合并到各输出分支 — 避免 --json+smart-verify 提前 return 导致检查不执行)
    _nginx_ok = _health_ok = True
    _nginx_out = _health_out = ""
    _http_ok = _http_out = ""
    if nginx_reload and not dry_run:
        _nr = api.exec_cmd("nginx -t 2>&1 && nginx -s reload 2>&1 && echo NGINX_OK", timeout=20)
        _nginx_ok = "NGINX_OK" in (_nr.get("stdout") or "")
        _nginx_out = (_nr.get("stdout") or "").strip()[:200]
    if health_url and not dry_run:
        _expect = health_expect
        if _expect:
            _hc = api.exec_cmd(f'curl -sk --max-time 15 "{health_url}" | grep -q "{_expect}" && echo HEALTH_OK', timeout=25)
            _health_ok = "HEALTH_OK" in (_hc.get("stdout") or "")
        else:
            # 🆕 FIX-health-pseudo404-v4.17: 该服务器 nginx 伪 404 连状态码都返回 200, body 为纯 IP 短串
            #   (某些服务器的伪 404 会返回一段纯 IP 短串, 特征串会变, 不可硬编码).
            #   无 expect 时改用 body 长度门槛 (真实页面远大于 200B, 伪 404 仅 10B) 判活.
            _hc = api.exec_cmd(f'curl -sk --max-time 15 "{health_url}" | wc -c', timeout=25)
            _body_len = (_hc.get("stdout") or "").strip()
            _health_ok = _body_len.isdigit() and int(_body_len) >= 200
            _health_out = f"body_len={_body_len}"
        _health_out = (_hc.get("stdout") or "").strip()[:200]
    # 🆕 FIX-http-check-v4.17: --http-check <url> [--expect <str>] 部署后抓取页面内容断言 —
    #   AI 确认"线上页面真的对了" (expect 命中即 ok; 未命中/抓取失败 ok=false). body_preview 给 AI 看实际内容.
    if http_check and not dry_run:
        _hc2 = api.exec_cmd(f'curl -sk --max-time 15 "{http_check}"', timeout=25)
        _hc2_out = (_hc2.get("stdout") or "").strip()
        if http_expect:
            _http_ok = http_expect in _hc2_out
            _http_out = f"expect_found={_http_ok} body_len={len(_hc2_out)}"
        else:
            _http_ok = bool(_hc2_out)
            _http_out = f"body_len={len(_hc2_out)}"
        _http_preview = _hc2_out[:200]
    _post_fail = (nginx_reload and not _nginx_ok) or (health_url and not _health_ok) or (http_check and not _http_ok)
    if plain:
        # 🆕 FIX-plain-v4.6: --deploy --plain 单行汇总 (AI 断言 result=ok 即部署成功)
        _deploy_note_snapshot(conf, files, deploy_id)
        _record_history(conf, "deploy", [{"ok": True, "deploy_id": deploy_id, "files": [f["path"] for f in files]}])
        print(f"deploy_id={deploy_id} files={r.get('file_count', len(files))} result=ok")
        if nginx_reload:
            print(f"nginx={'ok' if _nginx_ok else 'fail'}")
        if health_url:
            print(f"health={'ok' if _health_ok else 'fail'}")
        if _post_fail:
            return 4
        return 0
    if "--json" in sys.argv and not smart_verify:
        # 🆕 v4.12.4: --json 无 smart-verify 时输出结构化部署结果 (stdout 保持纯 JSON)
        _deploy_note_snapshot(conf, files, deploy_id)
        _record_history(conf, "deploy", [{"ok": True, "deploy_id": deploy_id, "files": [f["path"] for f in files]}])
        _deploy_extra = {}
        if nginx_reload: _deploy_extra["nginx_reload"] = "ok" if _nginx_ok else "fail"
        if health_url: _deploy_extra["health"] = "ok" if _health_ok else "fail"
        if http_check:
            _deploy_extra["http_check"] = {"url": http_check, "ok": bool(_http_ok),
                                           "expect": http_expect or "", "detail": _http_out,
                                           "body_preview": _http_preview if http_check else ""}
        print(json.dumps({"command": "deploy", "deploy_id": deploy_id, "ok": not _post_fail,
                          "result": "ok",
                          "files": r.get('file_count', len(files)),
                          "batched": r.get("batched"), "batches": r.get("batches"),
                          "total": r.get("total"), "skipped": _deploy_skip,
                          **_deploy_extra}, ensure_ascii=False))
        if _post_fail:
            return 4
        return 0
    if "--json" not in sys.argv:
        if http_check:
            print(f"\n🌐 HTTP 检查: {'✅' if _http_ok else '❌'} {http_check} ({_http_out})")
            if http_check and not _http_ok and _http_preview:
                print(f"    页面片段: {_http_preview[:120]}")
        print(f"\n✅ 部署成功! deploy_id={deploy_id}")
        print(f"   文件数: {r.get('file_count', len(files))}")
        for f in files:
            bk = backed_map.get(f["path"], "")
            print(f"   {f['path']:<40} {'备份: '+bk if bk else '(新文件)'}")
        # 修复4: pre_check 可能是 string, 需类型检查
        pre = r.get("pre_check")
        if pre:
            if isinstance(pre, dict):
                passed_n = sum(1 for v in pre.values() if isinstance(v, dict) and v.get("passed"))
                print(f"   pre_check: {passed_n}/{len(pre)} 通过")
            elif isinstance(pre, str):
                print(f"   pre_check: {pre}")
        post = r.get("post_check")
        if post:
            if isinstance(post, dict):
                print(f"   post_check: {'✅ 通过' if post.get('passed') else '⚠ 未通过'} (输出: {post.get('output','').strip()})")
            elif isinstance(post, str):
                print(f"   post_check: {post}")
        if nginx_reload:
            print(f"   nginx: {'✅ -t 通过 + reload 完成' if _nginx_ok else '⚠ -t 失败: ' + _nginx_out}")
        if health_url:
            print(f"   健康检查: {'✅ ' + health_url if _health_ok else '⚠ 失败: ' + _health_out}")
    _record_history(conf, "deploy", [{"ok": True, "deploy_id": deploy_id, "files": [f["path"] for f in files]}])
    # 🆕 v4.12.4: --smart-verify md5 — 部署成功后逐个复验远程 MD5 (AI 确认部署真正生效)
    if smart_verify and not dry_run:
        _bad_verify = []
        for f in files:
            try:
                _loc_md5 = ""
                _lp = Path(f.get("local", ""))
                if _lp.is_file() and _lp.stat().st_size <= 20_000_000:
                    _loc_md5 = finger(_lp, mode="hash")["md5"]
                _rr = api.checksum_remote(f["path"], "md5")
                _rmd5 = ""
                for _it in _rr.get("items", []):
                    if _it.get("path", "").lstrip("/") == f["path"].lstrip("/"):
                        _rmd5 = _it.get("md5", ""); break
                _ok = bool(_rmd5) and (not _loc_md5 or _loc_md5 == _rmd5)
                if not _ok:
                    _bad_verify.append({"path": f["path"], "remote_md5": _rmd5, "local_md5": _loc_md5})
            except Exception:
                _bad_verify.append({"path": f["path"], "error": "verify_exception"})
        if "--json" in sys.argv:
            _deploy_extra = {}
            if nginx_reload: _deploy_extra["nginx_reload"] = "ok" if _nginx_ok else "fail"
            if health_url: _deploy_extra["health"] = "ok" if _health_ok else "fail"
            if http_check:
                _deploy_extra["http_check"] = {"url": http_check, "ok": bool(_http_ok),
                                               "expect": http_expect or "", "detail": _http_out,
                                               "body_preview": _http_preview if http_check else ""}
            if not _bad_verify:
                _deploy_note_snapshot(conf, files, deploy_id)
            print(json.dumps({"command": "deploy", "deploy_id": deploy_id, "ok": not _bad_verify,
                              "result": "ok",
                              "files": len(files), "smart_verify": smart_verify,
                              "verify_ok": len(files) - len(_bad_verify),
                              "verify_failed": _bad_verify, **_deploy_extra}, ensure_ascii=False))
            if _bad_verify or _post_fail:
                return 4
            return 0
        if _bad_verify:
            print(f"\n⚠ smart-verify: {len(_bad_verify)}/{len(files)} 个文件远程 MD5 与预期不符:", file=sys.stderr)
            for b in _bad_verify:
                print(f"   {b['path']}  remote={b.get('remote_md5','?')[:8]} local={b.get('local_md5','?')[:8]}", file=sys.stderr)
            return 4
        print(f"\n✅ smart-verify ({smart_verify}): {len(files)}/{len(files)} 个文件远程 MD5 全部一致")
    _deploy_note_snapshot(conf, files, deploy_id)
    if _post_fail:
        if nginx_reload and not _nginx_ok:
            print(f"⚠ nginx: {_nginx_out}", file=sys.stderr)
        if health_url and not _health_ok:
            print(f"⚠ 健康检查失败: {_health_out}", file=sys.stderr)
        return 4
    return 0

# ---------------------------------------------------------------------------
# 增强: AI 辅助命令 --ls / --diff / --cat
# ---------------------------------------------------------------------------

def cmd_ls(conf: dict, log: Logger, api: ApiClient, target: str | None = None,
           recursive: bool = False, plain: bool = False) -> int:
    """列出远程目录内容 (AI 探索远程文件系统用)。
    target=None → 列出 remote_base 根目录; 指定路径 → 列出该子目录。
    plain=True → 纯文本输出 (无 emoji, 便于 grep/awk 解析)。"""
    remote_base = conf.get("remote_base", "").strip("/")
    if target and target != "__ALL__":
        rp = _resolve_remote_path(conf, target)
    else:
        rp = remote_base or "."
    # 分页循环获取完整列表
    all_items = []
    offset = 0
    page_no = 0
    while True:
        page_no += 1
        r = api.list_remote(rp, recursive=recursive, offset=offset, limit=1000)
        if not r.get("ok"):
            # 🆕 v4.26 AI 易用性: ls 一个文件路径不再报错, 直接回显该文件元信息 (对齐 shell ls 语义)
            if r.get("error") == "not_dir" and target:
                _st = api.stat_remote(rp)
                if _st.get("ok"):
                    return cmd_info(conf, log, api, target)
            # 🆕 v4.12-FIX: 错误走 stderr (统一通道)
            print(f"❌ 列举失败: {r.get('error', '')} (path={rp})", file=sys.stderr)
            return 2
        all_items.extend(r.get("items", []))
        if page_no > 1 and "--json" not in sys.argv:
            print(f"  (已获取 {len(all_items)} 项, 继续翻页 ...)", flush=True)
        if not r.get("has_more"):
            break
        # BUG-12 修复: 防 limit=0 死循环
        offset += max(1, len(r.get("items", [])))
        if offset > 100000:
            break
    # 过滤 IGNORE 文件
    ignore_globs = conf.get("ignore", [])
    items = [it for it in all_items if not any(glob_match(g, it.get("path", "")) for g in ignore_globs)]
    # 🆕 v4.11-JSON: --json 时输出结构化 entries (自动 merge 到顶层); 人类头部打印一律跳过
    _json_mode = "--json" in sys.argv
    if len(items) < len(all_items):
        if plain and not _json_mode:
            print(f"count={len(items)} filtered_from={len(all_items)} ok=true")
        elif not _json_mode:
            print(f"  (共 {len(all_items)} 项, 过滤 IGNORE 后 {len(items)} 项)")
    else:
        if plain and not _json_mode:
            print(f"count={len(items)} recursive={str(recursive).lower()} ok=true")
        elif not _json_mode:
            print(f"\n📁 {rp} ({len(items)} 项{'递归' if recursive else ''}):\n")
    if not items:
        if _json_mode:
            print(json.dumps({"command": "ls", "path": rp, "entries": []}, ensure_ascii=False))
        elif plain:
            print("count=0 ok=true")
        else:
            print("  (空目录)")
        return 0
    # 🆕 FIX-brief-v4.17: --brief 精简模式 — 只输出 count/summary (目录/文件数/总大小), 不列条目
    if "--brief" in sys.argv:
        _dirs = sum(1 for it in items if it.get("type") == "dir")
        _files = len(items) - _dirs
        _total_size = sum(it.get("size", 0) for it in items if it.get("type") != "dir")
        if _json_mode:
            print(json.dumps({"command": "ls", "path": rp, "count": len(items),
                              "dirs": _dirs, "files": _files, "total_size": _total_size}, ensure_ascii=False))
        elif plain:
            print(f"count={len(items)} dirs={_dirs} files={_files} total_size={_total_size} ok=true")
        else:
            print(f"📁 {rp}: {len(items)} 项 ({_dirs} 目录, {_files} 文件, {_total_size}B)")
        return 0
    from datetime import datetime
    _json_entries = []
    for it in items:
        t = it.get("type", "?")
        icon = "" if plain else ("📁" if t == "dir" else "📄")
        size = "—" if t == "dir" else f"{it.get('size', 0)}B"
        mt = datetime.fromtimestamp(it.get("mtime", 0)).strftime("%Y-%m-%d %H:%M:%S") if it.get("mtime") else "—"
        if _json_mode:
            _json_entries.append({"path": it.get("path", ""), "type": t,
                                  "size": it.get("size", 0) if t != "dir" else 0,
                                  "mtime": mt})
        elif plain:
            # 🆕 FIX-plain-v4.6: --ls --plain 每行: type=T size=S path=P (AI 直接解析)
            print(f"type={t} size={it.get('size', 0) if t != 'dir' else 0} path={it.get('path', '')}")
        else:
            print(f"  {icon} {t:<4} {size:>10} {mt:<20} {it.get('path', '')}")
    if _json_mode:
        print(json.dumps({"command": "ls", "path": rp, "count": len(_json_entries),
                          "entries": _json_entries}, ensure_ascii=False))
    return 0

def cmd_diff(conf: dict, log: Logger, api: ApiClient, target: str | None = None,
             plain: bool = False) -> int:
    """对比本地与远程文件差异 (AI 判断需要推/拉哪些文件用)。
    target=None → 对比整个 remote_base; 指定路径 → 只对比该文件/目录。"""
    watch_dir = Path(conf["watch_dir"])
    remote_base = conf.get("remote_base", "").strip("/")
    # 收集本地文件清单
    mon = scan_watch_dir(watch_dir, remote_base, conf["ignore"], conf["allow_ext"])
    if target and target != "__ALL__":
        # 相对路径基于 watch_dir 解析 (与 push 命令一致)
        tp = Path(target)
        if not tp.is_absolute():
            tp = watch_dir / tp
        # FIX-symlink-path: 纯词法路径; 链接本身作为单个对象, 不跟随到目标目录/文件.
        tp = Path(os.path.abspath(str(tp)))
        if os.path.islink(str(tp)):
            mon = [m for m in mon
                   if (_rel_under_watch(Path(m["local"]), watch_dir) == _rel_under_watch(tp, watch_dir))]
        elif tp.is_dir():
            tprel = _rel_under_watch(tp, watch_dir)
            mon = [m for m in mon
                   if tprel is not None
                   and (lambda r: r is not None and (r == tprel or r.startswith(tprel + "/")))(
                       _rel_under_watch(Path(m["local"]), watch_dir))]
        else:
            tprel = _rel_under_watch(tp, watch_dir)
            mon = [m for m in mon if _rel_under_watch(Path(m["local"]), watch_dir) == tprel]
    # 构造 local_items 供 diff API 使用
    # PERF-diff-md5: 原实现串行算每个文件的真 md5 (3767 文件实测 ~15s), 改线程池并发
    def _mk_item(m: dict) -> dict:
        # BUG-3 修复: m["md5"] 可能是 "f:..." 占位符, 服务器拿来做比对永远不等 → 全判 modified.
        #   必须用真 md5. 大项目可指定 --diff <path> 限定范围减少计算量.
        return {
            "path": m["remote"],
            "size": m["size"],
            "mtime": int(m.get("mtime", 0)),
            "md5": _real_md5(m),
        }
    if len(mon) > 200:
        _dconc = max(1, int(conf.get("pull_concurrency") or 8))
        with ThreadPoolExecutor(max_workers=_dconc) as _dx:
            local_items = list(_dx.map(_mk_item, mon))
    else:
        local_items = [_mk_item(m) for m in mon]
    # 调用远程 diff API
    # 指定 target 时, 只对比该子目录 (避免全量 diff)
    # 注意: 服务器 diff API 只接受目录, 单文件时用父目录
    diff_filter = None
    if target and target != "__ALL__":
        diff_path = _resolve_remote_path(conf, target)
        # 检查远程是否为文件: 如果路径含扩展名且无尾斜杠, 当作文件处理
        tp = Path(target)
        if not tp.is_absolute():
            tp = Path(conf["watch_dir"]) / tp
        # FIX-symlink-path: 链接本身按单文件处理, 不 resolve() 到目标
        tp = Path(os.path.abspath(str(tp)))
        if os.path.islink(str(tp)) or tp.is_file() or (not os.path.lexists(str(tp)) and "." in tp.name):
            # 单文件 diff: 用父目录做 API 调用, 过滤结果
            diff_filter = diff_path
            # FIX-win-remote-path: 远程路径是 POSIX 语义, 不能用 Path.parent —— Windows 上会产出
            #   "share\nginx\html" 反斜杠路径, 服务端直接回 not_dir。
            diff_path = diff_path.rsplit("/", 1)[0] if "/" in diff_path else "."
    else:
        diff_path = remote_base or "."
    r = api._req({
        "action": "diff",
        "path": diff_path,
        "local_items": local_items,
        "compute_hash": True,
        "algorithm": "md5",
    })
    if not r.get("ok"):
        print(f"❌ diff 失败: {r.get('error', '')}", file=sys.stderr)
        return 2
    # 过滤掉匹配 IGNORE 规则的远程文件 (如 ai-sync/*, cache/*)
    ignore_globs = conf.get("ignore", [])
    only_local = [p for p in r.get("only_local", []) if not any(glob_match(g, p) for g in ignore_globs)]
    only_remote = [p for p in r.get("only_remote", []) if not any(glob_match(g, p) for g in ignore_globs)]
    modified = [m for m in r.get("modified", []) if not any(glob_match(g, m.get("path", "")) for g in ignore_globs)]
    # 单文件 diff 过滤: 只保留目标文件
    if diff_filter:
        only_local = [p for p in only_local if p == diff_filter]
        only_remote = [p for p in only_remote if p == diff_filter]
        modified = [m for m in modified if m.get("path") == diff_filter]
    summary = r.get("summary", {})
    # 用过滤后的数量覆盖 summary (原 summary 含 IGNORE 文件)
    display_path = diff_filter or diff_path
    if "--json" in sys.argv:
        # 🆕 v4.12.3-JSON: diff 结构化 (AI 直接决策: push/pull/conflict 清单)
        # 🆕 v4.12.6: 增加内容级摘要 (行数差, 类似 git diff --stat) — 只对 ≤20 个文件做轻量探测
        def _local_stat(p: str) -> dict:
            try:
                lp = Path(conf["watch_dir"]) / p
                # FIX-symlink: 链接是单对象, 不读目标内容/不跟随 is_file()
                if os.path.islink(str(lp)):
                    return {"local_bytes": 0}
                if lp.is_file():
                    n = sum(1 for _ in lp.open('rb'))
                    return {"local_lines": n, "local_bytes": lp.stat().st_size}
            except Exception:
                pass
            return {}
        def _remote_lines(p: str) -> int:
            try:
                rr = api.read_range(p, 1, 1)
                return int(rr.get("total_lines", 0) or 0)
            except Exception:
                return 0
        _c = []
        for m in modified[:20]:
            _c.append({"path": m.get("path", ""), "reason": m.get("reason", "?"),
                       **_local_stat(m.get("path", ""))})
        _ol, _or_ = [], []
        for p in only_local[:20]:
            _ol.append({"path": p, **_local_stat(p)})
        for p in only_remote[:20]:
            _or_.append({"path": p, "remote_lines": _remote_lines(p)})
        for i, e in enumerate(_c):
            if e.get("path") and e.get("local_lines") is not None:
                e["remote_lines"] = _remote_lines(e["path"])
        print(json.dumps({"command": "diff", "path": display_path,
                          "identical": summary.get('identical', 0),
                          "only_local": len(only_local), "only_remote": len(only_remote),
                          "modified": len(modified),
                          "to_push": _ol, "to_pull": _or_,
                          "conflicts": _c,
                          "changes": sum(e.get("local_lines", 0) for e in _ol)
                                     + sum(e.get("local_lines", 0) for e in _c)},
                         ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --diff --plain 单行汇总 + 待操作清单 (AI 直接决定 push/pull)
        print(f"path={display_path} identical={summary.get('identical', 0)} "
              f"push={len(only_local)} pull={len(only_remote)} conflict={len(modified)}")
        for p in only_local[:100]:
            print(f"push {p}")
        for p in only_remote[:100]:
            print(f"pull {p}")
        for m in modified[:100]:
            print(f"conflict {m.get('path', '')} reason={m.get('reason', '?')}")
        return 0
    print(f"\n🔍 本地 vs 远程 差异对比 ({display_path}):\n")
    print(f"  一致: {summary.get('identical', 0)}")
    print(f"  仅本地 (需 push): {len(only_local)}")
    print(f"  仅远程 (需 pull): {len(only_remote)}")
    print(f"  已修改 (冲突): {len(modified)}")
    raw_total = summary.get('only_local', 0) + summary.get('only_remote', 0) + summary.get('modified', 0)
    filtered_total = len(only_local) + len(only_remote) + len(modified)
    if raw_total > filtered_total:
        print(f"  (已过滤 {raw_total - filtered_total} 个 IGNORE 文件)")
    if only_local:
        print(f"\n  📤 仅本地 (建议 --push):")
        for p in only_local[:20]:
            print(f"     {p}")
        if len(only_local) > 20:
            print(f"     ... 还有 {len(only_local)-20} 个")
    if only_remote:
        print(f"\n  📥 仅远程 (建议 --pull):")
        for p in only_remote[:20]:
            print(f"     {p}")
        if len(only_remote) > 20:
            print(f"     ... 还有 {len(only_remote)-20} 个")
    if modified:
        print(f"\n  ⚡ 已修改 (两边都变了, 需人工决策):")
        for m in modified[:20]:
            reason = m.get("reason", "?")
            print(f"     {m.get('path', '')} [{reason}]")
        if len(modified) > 20:
            print(f"     ... 还有 {len(modified)-20} 个")
    return 0

# --cat 大文件阈值: 超过则改走服务端 read_range 分页 (避免整包下载 + 客户端截断)
_CAT_BIG_THRESHOLD = 4 * 1024 * 1024


def cmd_cat(conf: dict, log: Logger, api: ApiClient, target: str,
            start: int = 0, end: int = 0, command_name: str = "cat") -> int:
    """读取远程文件内容并输出到 stdout (AI 快速查看远程文件用, 不修改本地)。
    支持: 文本文件直接输出; 二进制文件输出 base64。
    🆕 v4.14: --json 结构化 {"command":"cat","content":...,"size":...} 直接拿内容 (不污染 stdout).
    🆕 v4.15 合并: --cat <path> --range <start> <end> 行范围切片 (复用服务端 read_range,
    read-range 命令是其别名; command_name 保持 read-range 输出形状兼容)."""
    rp = _resolve_remote_path(conf, target)
    # range 模式: 复用服务端 read_range action (按行切片, 省带宽), 与 read-range 同源
    if start > 0 or end > 0:
        r = api.read_range(rp, start, end)
        if not r.get("ok"):
            if "--json" in sys.argv:
                print(json.dumps({"command": command_name, "path": rp, "range": True,
                                  "ok": False, "error": r.get("error", "")}, ensure_ascii=False))
            else:
                print(f"❌ 读取失败: {r.get('error', '')} (path={rp})", file=sys.stderr)
            return 2
        lines = r.get("content", [])
        total = r.get("total_lines", 0)
        if "--json" in sys.argv:
            print(json.dumps({"command": command_name, "path": rp, "range": True, "ok": True,
                              "start": start, "end": end, "total_lines": total,
                              "lines": [{"line": it.get("line", "?"), "text": it.get("text", "")}
                                        for it in lines]}, ensure_ascii=False))
        else:
            print(f"\n📖 {rp} (第 {start}-{end} 行, 共 {total} 行):\n")
            for item in lines:
                print(f"  {item.get('line', '?'):>6}│ {item.get('text', '')}")
        return 0
    # ---- AI-SAFE v4.26: 大文件先探大小, 再决定怎么读 ----
    # 旧行为: 先整包 download(base64) 再在客户端截断 —— 一个 50MB 文件会占约 165MB 内存,
    #         且把全部带宽花在最终只会显示 200 行的内容上。
    # 新行为: 非 --full 时先 stat; 超阈值改走服务端 read_range 分页; 二进制只回元信息。
    _full_req = "--full" in sys.argv
    _cap_req = 30 if "--brief" in sys.argv else 200
    if not _full_req:
        try:
            _st = api.stat_remote(rp)
        except Exception:
            _st = {}
        _sz = int(_st.get("size") or 0) if isinstance(_st, dict) and _st.get("ok") else 0
        if _sz > _CAT_BIG_THRESHOLD:
            _rr = api.read_range(rp, 1, _cap_req)
            if not _rr.get("ok"):
                if "--json" in sys.argv:
                    print(json.dumps({"command": "cat", "path": rp, "ok": False,
                                      "error": _rr.get("error", "")}, ensure_ascii=False))
                else:
                    print(f"❌ 读取失败: {_rr.get('error', '')} (path={rp})", file=sys.stderr)
                return 2
            _items = _rr.get("content") or []
            _txt = "\n".join(str(it.get("text", "")) for it in _items)
            _fffd = _txt.count("\ufffd")
            if ("\x00" in _txt) or (_txt and _fffd > max(8, len(_txt) // 50)):
                # 二进制: 不回 base64 (AI 拿到也用不上, 且体积等于文件本身)
                if "--json" in sys.argv:
                    print(json.dumps({"command": "cat", "path": rp, "ok": True, "binary": True,
                                      "size": _sz, "truncated": True,
                                      "hint": f"二进制文件 ({_sz}B), 内容未返回; 用 --pull {rp} 拉到本地再处理"},
                                     ensure_ascii=False))
                else:
                    print(f"⚠ 二进制文件 ({_sz}B), 未输出内容 (用 --pull {rp} 下载到本地查看)", file=sys.stderr)
                return 0
            _total = _rr.get("total_lines", 0)
            _trunc = bool(_rr.get("has_more"))
            if "--json" in sys.argv:
                print(json.dumps({"command": "cat", "path": rp, "ok": True, "content": _txt,
                                  "size": _sz, "binary": False, "total_lines": _total,
                                  "shown_lines": len(_items), "truncated": _trunc,
                                  "hint": (f"文件较大 ({_sz}B, 共 {_total} 行), 仅显示前 {len(_items)} 行; "
                                           f"用 --range START END 按行切片 或 --full 看全部") if _trunc else ""},
                                 ensure_ascii=False))
            else:
                if _trunc:
                    print(f"⚠ 大文件 ({_sz}B, 共 {_total} 行), 仅显示前 {len(_items)} 行 "
                          f"(--cat --full 看全部; --range START END 按行切片)", file=sys.stderr)
                print(_txt, end="\n" if _txt else "")
            return 0
    r = api.download_file(rp)
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "cat", "path": rp, "ok": False,
                              "error": r.get("error", "")}, ensure_ascii=False))
        else:
            print(f"❌ 读取失败: {r.get('error', '')} (path={rp})", file=sys.stderr)
        return 2
    try:
        data = base64.b64decode(r["content_b64"])
    except Exception as e:
        print(f"❌ 解码失败: {e}", file=sys.stderr)
        return 2
    # 尝试作为 UTF-8 文本输出
    try:
        text = data.decode("utf-8")
        # 🆕 FIX-cat-limit-v4.17: 大文件自动截断 — 默认最多输出 200 行, 防 cat 大文件
        #   (如 JS 700KB) 打爆 AI 上下文. --cat --full 强制全量; --range 精确切片不受限;
        #   --brief 时仅前 30 行. JSON 带 truncated/total_lines 供 AI 断言后决定是否切片.
        _lines = text.splitlines()
        _total_lines = len(_lines)
        _full = "--full" in sys.argv
        _cap = 30 if "--brief" in sys.argv else 200
        _truncated = (not _full) and _total_lines > _cap
        _shown = _lines if _full else _lines[:_cap]
        if "--json" in sys.argv:
            print(json.dumps({"command": "cat", "path": rp, "ok": True,
                              "content": "\n".join(_shown), "size": len(data), "binary": False,
                              "total_lines": _total_lines, "truncated": _truncated,
                              "hint": (f"文件共 {_total_lines} 行, 仅显示前 {len(_shown)} 行; "
                                       f"用 --range START END 按行切片 或 --full 看全部") if _truncated else ""},
                             ensure_ascii=False))
        else:
            if _truncated:
                print(f"⚠ 文件共 {_total_lines} 行, 仅显示前 {len(_shown)} 行 (--cat --full 看全部; --range START END 按行切片)",
                      file=sys.stderr)
            print("\n".join(_shown), end="\n" if _shown else "")
        return 0
    except UnicodeDecodeError:
        # 二进制文件: 输出 base64 + 元信息到 stderr
        if "--json" in sys.argv:
            print(json.dumps({"command": "cat", "path": rp, "ok": True,
                              "binary": True, "size": len(data),
                              "content_b64": base64.b64encode(data).decode()},
                             ensure_ascii=False))
        else:
            print(f"⚠ 二进制文件 ({len(data)}B), 输出 base64:", file=sys.stderr)
            print(base64.b64encode(data).decode())
        return 0

# ==================== [AI-SAFE v1.1.2] 远程编辑命令 (read-remote / write-remote / edit-remote) ====================
# 目标: AI "像本地一样"编辑服务器文件。工作流: read-remote 下载到缓存 → AI 本地 Read/Edit →
#      write-remote 回传 (自动 CAS 冲突检测 + 服务器备份 + PHP lint + 回读 md5 验证)。
# 边界: 仅当前 scope work_dir 内 (服务器 safe_path 强校验); scope 外文件走 exec。
_REMOTE_CACHE_ROOT = os.path.expanduser("~/.ai-sync-remote")

def _server_rel(conf: dict, api: ApiClient, target: str) -> str:
    """远程编辑路径统一解析: 支持「服务器绝对路径」→ scope 相对 (基于服务器 work_dir, ping 一次)。
    相对路径/带前缀路径由 _resolve_remote_path 处理; 服务器绝对路径 (如 /mnt/data/wwwroot/default/bxbb/x)
    剥掉 work_dir 前缀得到 scope 相对 (陷阱2: 路径双轨制 的消除)。"""
    rp = _resolve_remote_path(conf, target)
    if rp.startswith("/") or rp.startswith("mnt/") or rp.startswith("data/") or rp.startswith("wwwroot/"):
        try:
            _wd = (api.ping() or {}).get("work_dir") or ""
            _wd_norm = "/" + _wd.lstrip("/")
            if _wd_norm and _wd_norm != "/":
                _pre = _wd_norm.rstrip("/")
                if rp == _pre or rp.startswith(_pre + "/"):
                    return rp[len(_pre):].lstrip("/")
        except Exception:
            pass
    return rp

def _remote_cache_path(scope: str, rel: str) -> Path:
    """远程文件缓存路径: ~/.ai-sync-remote/<scope>/<rel> (AI 可直接 Read/Edit)。"""
    return Path(_REMOTE_CACHE_ROOT) / (scope or "default") / rel

def _read_remote_meta(cache: Path) -> str:
    """读取缓存元信息 (上次拉取的远程 md5)。"""
    try:
        return (Path(str(cache) + ".remote_md5").read_text().strip().split()[0])
    except Exception:
        return ""

def _write_remote_meta(cache: Path, md5: str) -> None:
    try:
        Path(str(cache) + ".remote_md5").write_text(f"{md5} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    except Exception:
        pass

def _read_remote_one(conf: dict, log: Logger, api: ApiClient, target: str) -> tuple[int, dict]:
    """下载单个远程文件到缓存。返回 (rc, result)。"""
    rp = _server_rel(conf, api, target)
    r = api.download_file(rp)
    if not r.get("ok"):
        return 2, {"path": rp, "ok": False, "error": r.get("error", "")}
    try:
        data = base64.b64decode(r["content_b64"])
    except Exception as e:
        return 2, {"path": rp, "ok": False, "error": f"decode_failed: {e}"}
    cache = _remote_cache_path(conf.get("scope", ""), rp)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    md5 = hashlib.md5(data).hexdigest()
    _write_remote_meta(cache, md5)
    return 0, {"path": rp, "ok": True, "cache": str(cache), "md5": md5, "size": len(data),
               "next_hint": f"修改缓存后执行: --write-remote {rp} 回传"}

def cmd_replace(conf: dict, log: Logger, api: ApiClient, target: str,
                old: str, new: str, use_regex: bool = False, want_count: int = 0,
                dry_run: bool = False) -> int:
    """远程就地替换字符串 —— 把 AI 改一行内容的 4 步压缩成 1 步。

    此前 AI 必须: --read-remote → 自己写 python/sed 改缓存 → --write-remote → --cat 验证,
    中间还要从输出里解析缓存绝对路径 (/root/.ai-sync-remote/<scope>/...)。本命令内部复用
    _read_remote_one + _write_remote_one, 因此天然继承 CAS 乐观锁 / 服务器备份 / PHP lint /
    回读 md5 验证 / JS·HTML 语法校验, 不会为了省事牺牲安全性。

    关键 AI 安全设计: 一处都没匹配到时必须报错退出(rc=2), 绝不静默"成功"——
    AI 常因为记错原文而写出匹配不上的替换, 静默成功会让它以为改了其实没改。
    """
    _json = "--json" in sys.argv
    if not target:
        _err = {"command": "replace", "ok": False, "error": "missing_path",
                "hint": "用法: --replace <远程文件> --old '原文' --new '新文'"}
        _emit(_err, _json, lambda: print("❌ --replace 缺少文件路径; 用法: --replace FILE --old '原文' --new '新文'", file=sys.stderr))
        return 2
    if old == "":
        _err = {"command": "replace", "path": target, "ok": False, "error": "missing_old",
                "hint": "必须传 --old '要被替换的原文' (空字符串一律视为漏传, 防止整文件被误替换)"}
        _emit(_err, _json, lambda: print("❌ --replace 缺少 --old 原文 (空串不允许, 防误替换整个文件)", file=sys.stderr))
        return 2

    # ① 拉到缓存 (复用 read-remote 的预处理: 服务端 srcdir 相对路径转换全在里面)
    rc, res = _read_remote_one(conf, log, api, target)
    if not res.get("ok"):
        _err = {"command": "replace", "path": res.get("path", target), "ok": False,
                "error": res.get("error", "read_fail"),
                "hint": "文件读取失败; 用 --ls 确认路径, --cat 确认可读"}
        _emit(_err, _json, lambda: print(f"❌ 读取远程失败: {res.get('error','')} (path={res.get('path',target)})", file=sys.stderr))
        return rc

    cache = Path(res["cache"])
    rp = res["path"]
    old_md5 = res.get("md5", "")
    try:
        raw = cache.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _err = {"command": "replace", "path": rp, "ok": False, "error": "binary_file",
                "hint": "文件不是 UTF-8 文本, 不支持字符串替换; 二进制请用 --upload-file 整体覆盖"}
        _emit(_err, _json, lambda: print(f"❌ {rp} 不是 UTF-8 文本文件, 不支持 --replace; 请用 --upload-file 覆盖", file=sys.stderr))
        return 2

    # ② 计算替换结果 (先看数量, 数量不合预期绝不落盘)
    if use_regex:
        try:
            rx = re.compile(old)
        except re.error as e:
            _err = {"command": "replace", "path": rp, "ok": False, "error": "bad_regex",
                    "detail": str(e), "hint": "--old 不是合法正则; 普通文本替换请去掉 --regex"}
            _emit(_err, _json, lambda: print(f"❌ --old 不是合法正则: {e} (普通文本请去掉 --regex)", file=sys.stderr))
            return 2
        hits = len(rx.findall(text))
        new_text = rx.sub(new, text) if hits else text
    else:
        hits = text.count(old)
        new_text = text.replace(old, new) if hits else text

    if hits == 0:
        preview = "\n".join(text.splitlines()[:5])
        _err = {"command": "replace", "path": rp, "ok": False, "error": "pattern_not_found",
                "old": old, "regex": use_regex, "preview": preview,
                "hint": "原文在文件中一次都没匹配到, 未做任何写入。常见原因: 空白/换行/引号不一致。"
                        "先用 --cat <文件> 看真实内容(注意转义), 或改用 --regex 模糊匹配"}
        def _t():
            print(f"❌ 未匹配到任何内容, 已放弃写入(远程未改动): {rp}", file=sys.stderr)
            print(f"   要替换的原文: {old!r}", file=sys.stderr)
            print(f"   文件前 5 行:\n" + "\n".join("     " + l for l in preview.splitlines()), file=sys.stderr)
            print("   修法: 先 --cat <文件> 核对真实文本(注意空白/换行/引号), 或加 --regex", file=sys.stderr)
        _emit(_err, _json, _t)
        return 2

    if want_count and hits != want_count:
        _err = {"command": "replace", "path": rp, "ok": False, "error": "count_mismatch",
                "expected": want_count, "actual": hits,
                "hint": f"期望替换 {want_count} 处, 实际有 {hits} 处, 已放弃写入(防误伤)。"
                        f"确认无误请 --count 0(不限) 或改成实际数量"}
        _emit(_err, _json,
              lambda: print(f"❌ 替换处数不符: 期望 {want_count} 实际 {hits}, 未写入。确认后请 --count 0 或改成实际数量", file=sys.stderr))
        return 2

    # ③ 写回缓存 → 交给 write-remote 的既有链路(含 CAS/备份/lint/回读)
    if dry_run:
        _d = {"command": "replace", "path": rp, "ok": True, "dry_run": True,
              "replaced": hits, "old_md5": old_md5,
              "preview_new": "\n".join(new_text.splitlines()[:5])}
        _emit(_d, _json,
              lambda: (print(f"🔍 dry-run: 将在 {rp} 替换 {hits} 处(未写入)"),
                       print("   替换后前 5 行:\n" + "\n".join("     " + l for l in new_text.splitlines()[:5]))))
        return 0

    ndata = new_text.encode("utf-8")
    if ndata == raw:
        _ok = {"command": "replace", "path": rp, "ok": True, "changed": False, "replaced": 0,
               "note": "替换后内容与原文一致(幂等), 无需回传"}
        _emit(_ok, _json, lambda: print(f"✓ 替换后内容未变, 无需回传: {rp}"))
        return 0
    cache.write_bytes(ndata)
    rc2, res2 = _write_remote_one(conf, log, api, rp, dry_run=False)
    if not res2.get("ok"):
        cache.write_bytes(raw)   # 回写失败 → 还原缓存, 避免本地与服务端认知漂移
        _err = {"command": "replace", "path": rp, "ok": False,
                "error": res2.get("error", "write_fail"),
                "hint": res2.get("hint", "回传失败, 本地缓存已还原为修改前内容")}
        _emit(_err, _json, lambda: print(f"❌ 回传失败: {res2.get('error','')} (本地缓存已还原)", file=sys.stderr))
        return rc2 or 2

    new_md5 = hashlib.md5(ndata).hexdigest()
    _ok = {"command": "replace", "path": rp, "ok": True, "changed": True, "replaced": hits,
           "regex": use_regex, "old_md5": old_md5, "new_md5": new_md5,
           "backup": res2.get("backed_up", ""), "lint": res2.get("lint")}
    def _t_ok():
        print(f"✅ 已替换 {hits} 处: {rp}")
        print(f"   md5 {old_md5[:8]}… → {new_md5[:8]}…  回读验证=✓  备份={'是' if res2.get('backed_up') else '否'}")
    _emit(_ok, _json, _t_ok)
    return 0


def _emit(payload: dict, json_mode: bool, text_fn) -> None:
    """结构化/人类可读输出的统一出口。"""
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        text_fn()


def cmd_read_remote(conf: dict, log: Logger, api: ApiClient, target: str | list) -> int:
    """下载远程文件到本地缓存, AI 直接 Read/Edit 缓存文件, 改完 write-remote 回传。
    🆕 v4.14: 支持多路径批量 --read-remote a b c (单文件输出形状与 v4.13 完全一致)."""
    targets = target if isinstance(target, list) else [target]
    if len(targets) == 1:
        rc, res = _read_remote_one(conf, log, api, targets[0])
        if not res.get("ok"):
            if "--json" in sys.argv:
                print(json.dumps({"command": "read-remote", "path": res["path"], "ok": False,
                                  "error": res.get("error", "")}, ensure_ascii=False))
            else:
                print(f"❌ 读取失败: {res.get('error', '')} (path={res['path']})", file=sys.stderr)
            return rc
        if "--json" in sys.argv:
            print(json.dumps({"command": "read-remote", **res}, ensure_ascii=False))
        else:
            print(f"📥 已下载到缓存: {res['cache']}")
            print(f"   远程路径: {res['path']}  大小: {res['size']}B  md5: {res['md5']}")
            print(f"   改完回传: sync.py --conf sync.conf --write-remote {res['path']}")
        return 0
    # 批量模式
    results, rc_all = [], 0
    for t in targets:
        rc, res = _read_remote_one(conf, log, api, t)
        results.append(res)
        if rc != 0:
            rc_all = 2 if rc_all == 0 else rc_all
    if "--json" in sys.argv:
        print(json.dumps({"command": "read-remote", "batch": True, "count": len(results),
                          "ok": rc_all == 0, "results": results}, ensure_ascii=False))
    else:
        for res in results:
            if res.get("ok"):
                print(f"📥 {res['path']} → {res['cache']} ({res['size']}B md5={res['md5']})")
            else:
                print(f"❌ {res['path']}: {res.get('error', '')}", file=sys.stderr)
        print(f"batch=done ok={rc_all == 0} count={len(results)}")
    return rc_all

def _write_remote_one(conf: dict, log: Logger, api: ApiClient, target: str,
                      dry_run: bool = False) -> tuple[int, dict]:
    """回传单个缓存文件。返回 (rc, res)。"""
    rp = _server_rel(conf, api, target)
    cache = _remote_cache_path(conf.get("scope", ""), rp)
    if not cache.is_file():
        return 2, {"command": "write-remote", "path": rp, "ok": False,
                   "error": "cache_missing", "cache": str(cache),
                   "hint": "请先 --read-remote 下载, 或新文件用 --upload-file <本地文件> 直传"}
    data = cache.read_bytes()
    local_md5 = hashlib.md5(data).hexdigest()
    base_md5 = _read_remote_meta(cache)
    # 陷阱1 防护: 文件未修改 → 拒绝回传 (防 AI "改了缓存忘回传/空写")
    if base_md5 and base_md5 == local_md5:
        return 2, {"command": "write-remote", "path": rp, "ok": False,
                   "error": "unchanged", "md5": local_md5,
                   "hint": "缓存未修改, 无需回传; 若确需强制覆盖请先修改缓存文件"}
    return _upload_one_compute(conf, log, api, rp, data, local_md5, base_md5, cache,
                               dry_run=dry_run, op_name="write_remote")

def cmd_write_remote(conf: dict, log: Logger, api: ApiClient, target: str | list,
                     dry_run: bool = False) -> int:
    """把本地缓存文件回传到远程 (自动 CAS + 备份 + lint + 回读验证 + JS/HTML 语法校验)。
    🆕 v4.14: 支持多路径批量 --write-remote a b c (单文件输出形状与 v4.13 完全一致)."""
    targets = target if isinstance(target, list) else [target]
    if len(targets) == 1:
        rc, res = _write_remote_one(conf, log, api, targets[0], dry_run=dry_run)
        if res.get("error") == "cache_missing":
            if "--json" in sys.argv:
                print(json.dumps({"command": "write-remote", "path": res["path"], "ok": False,
                                  "error": "cache_missing", "cache": str(res.get("cache", "")),
                                  "hint": "请先 --read-remote 下载, 或新文件用 --upload-file <本地文件> 直传"}, ensure_ascii=False))
            else:
                print(f"❌ 缓存文件不存在: {res.get('cache', '')} (请先 read-remote {res['path']} 下载, 或新文件用 --upload-file 直传)", file=sys.stderr)
            return rc
        if res.get("error") == "unchanged":
            # 🆕 v4.29: 无变更=幂等成功 (ok:true changed:false rc=0) — AI 重试回写不再误判为失败
            if "--json" in sys.argv:
                print(json.dumps({"command": "write-remote", "path": res["path"], "ok": True,
                                  "changed": False, "md5": res.get("md5", ""),
                                  "note": "缓存未修改, 远程内容已一致"}, ensure_ascii=False))
            else:
                print(f"✓ 文件未修改 (本地 md5 == 远程 md5): {res['path']} — 远程已一致, 无需回传")
            return 0
        # 正常路径: _write_remote_one 已执行计算/上传, 直接输出结果 (不重复执行)
        _emit_upload_res(res)
        return rc
    # 批量模式: 逐文件独立判定, 互不阻塞
    results, rc_all = [], 0
    for t in targets:
        rc, res = _write_remote_one(conf, log, api, t, dry_run=dry_run)
        results.append(res)
        if rc != 0:
            rc_all = max(rc_all, rc) if rc_all != 99 else 99
            if rc == 99:
                rc_all = 99
    if "--json" in sys.argv:
        print(json.dumps({"command": "write-remote", "batch": True, "count": len(results),
                          "ok": rc_all == 0, "dry_run": bool(dry_run), "results": results},
                         ensure_ascii=False))
    else:
        for res in results:
            if res.get("ok"):
                print(f"✅ 已回传: {res['path']} (md5={res.get('md5')} 备份={'是' if res.get('backed_up') else '否'} 回读验证={'✓' if res.get('verified') else '⚠失败'})")
            elif res.get("dry_run"):
                print(f"[dry-run] 将回传 {res['path']} (md5={res.get('md5')} base={res.get('base_md5') or '(新文件)'})")
            else:
                print(f"❌ {res['path']}: {res.get('error', '')}", file=sys.stderr)
        print(f"batch=done ok={rc_all == 0} count={len(results)}")
    return rc_all

def cmd_edit_remote(conf: dict, log: Logger, api: ApiClient, target: str) -> int:
    """远程编辑一步入口 = read-remote (AI 改缓存后 write-remote 回传)。"""
    return cmd_read_remote(conf, log, api, target)


# ==================== v4.13: 本地文件直传远程 (创建新文件) ====================

def _syntax_check_local(data: bytes, filename: str) -> dict:
    """本地 node --check 语法校验 (.js/.html). 返回 {checked, ok, error}.
    无 node / 非支持类型 → checked=False (跳过, 不阻断). AI 环境有 node 时自动生效."""
    import subprocess
    ext = Path(filename).suffix.lower()
    js_exts = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
    if ext not in js_exts and ext != ".html":
        return {"checked": False}
    try:
        import tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=ext, delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            if ext == ".html":
                # HTML: 提取 <script> 内容做 node --check (无 script 则视为 OK)
                import re as _re
                m = _re.search(r"<script[^>]*>(.*?)</script>", data.decode("utf-8", "replace"), _re.S)
                if not m:
                    return {"checked": True, "ok": True}
                with tempfile.NamedTemporaryFile("wb", suffix=".js", delete=False) as jf:
                    jf.write(m.group(1).encode("utf-8"))
                    jtmp = jf.name
                r = subprocess.run(["node", "--check", jtmp], capture_output=True, text=True, timeout=30)
                try: os.unlink(jtmp)
                except Exception: pass
            else:
                r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=30)
        finally:
            try: os.unlink(tmp)
            except Exception: pass
        if r.returncode == 0:
            return {"checked": True, "ok": True}
        err = (r.stderr or "").strip().splitlines()
        return {"checked": True, "ok": False, "error": (err[0] if err else "syntax error")[:200]}
    except FileNotFoundError:
        return {"checked": False}  # 无 node → 跳过
    except Exception as e:
        return {"checked": False, "skip_reason": str(e)[:80]}


def _upload_one_compute(conf: dict, log: Logger, api: ApiClient, rp: str, data: bytes,
                        local_md5: str, base_md5: str, cache: Path, dry_run: bool = False,
                        op_name: str = "write_remote", mode: str = "",
                        mtime: float | None = None) -> tuple[int, dict]:
    """write-remote / upload-file 共用回传计算 (纯逻辑, 无输出):
    root 保护 → CAS 上传 → 回读验证 → 本地语法校验. 返回 (rc, res_dict)."""
    if rp in ("", "."):
        return 99, {"command": op_name, "path": rp, "ok": False, "blocked": True,
                    "level": "BLOCKED", "error": "scope_root",
                    "message": "拒绝覆盖 scope 根路径"}
    if dry_run:
        return 0, {"command": op_name, "path": rp, "ok": True, "dry_run": True,
                   "md5": local_md5, "size": len(data), "base_md5": base_md5,
                   "cache": str(cache), "action": "would_upload"}
    # FIX-upload-mtime: 透传本地 mtime (服务端 upload 支持该字段), 与 --push 行为对齐
    r = api.upload_file(rp, data, mode=mode, md5=local_md5, base_md5=base_md5, mtime=mtime)
    if not r.get("ok"):
        err = r.get("error", "")
        if "cas_conflict" in err or r.get("code") == 409:
            return 2, {"command": op_name, "path": rp, "ok": False,
                       "error": "cas_conflict", "expected_md5": base_md5,
                       "current_md5": r.get("current_md5", ""),
                       "hint": "read-remote 拉取最新, 合并后再回传"}
        return 2, {"command": op_name, "path": rp, "ok": False, "error": err}
    # 回读验证 (🆕 FIX-readback-retry-v4.21: 偶发失败自动重试 1 次, 消除上传后立即校验的时序/网络瞬断误报)
    verified = False
    for _attempt in range(2):
        verified = False
        try:
            r2 = api.checksum_remote(rp, "md5")
            for it in r2.get("items", []):
                p = it.get("path", "").lstrip("/")
                if p == rp.lstrip("/") or p.endswith(rp.lstrip("/")):
                    verified = it.get("md5", "") == local_md5
                    break
            if verified:
                break
        except Exception:
            verified = False
        if _attempt == 0:
            time.sleep(1.0)
    _write_remote_meta(cache, local_md5)
    # 🆕 v4.14: 服务端 upload 响应不带备份文件名 → 成功后自动 backup_ls 取最新备份
    #   (AI 拿到 backup_file 可直接 --rollback <path> --backup <它> 回滚)
    bk = r.get("backup_file") or r.get("backup") or ""
    if not bk and r.get("backed_up"):
        try:
            bl = api.backup_ls(rp)
            items = bl.get("backups") or bl.get("items") or []
            if isinstance(items, dict):
                items = list(items.values())
            if items:
                bk = (items[0].get("file") or items[0].get("backup_file")
                      or items[0].get("backup") or items[0].get("name") or "")
        except Exception:
            bk = ""
    syntax = _syntax_check_local(data, rp)
    res = {"command": op_name, "path": rp, "ok": True,
           "md5": local_md5, "base_md5": base_md5, "size": len(data),
           "cache": str(cache),
           "backed_up": bool(r.get("backed_up")),
           "backup_file": bk,
           "verified": verified, "lint": r.get("lint")}
    if r.get("mode") or mode:
        res["mode"] = r.get("mode") or mode
    if syntax.get("checked"):
        res["syntax_check"] = syntax
    return 0, res


def _emit_upload_res(res: dict) -> None:
    """按 res 输出 (--json 纯净 JSON / 文本人类可读)。"""
    if "--json" in sys.argv:
        print(json.dumps({k: v for k, v in res.items() if k != "command"}, ensure_ascii=False))
        return
    if res.get("blocked"):
        print(f"[BLOCKED] {res.get('command','')} 危险路径保护: 拒绝覆盖 scope 根路径 (已拦截, 未执行)", file=sys.stderr)
        return
    if res.get("dry_run"):
        print(f"[dry-run] 将回传 {res.get('path','')} (md5={res.get('md5','')} base={res.get('base_md5') or '(新文件)'})")
        return
    if not res.get("ok"):
        if res.get("error") == "cas_conflict":
            print(f"❌ CAS 冲突: 远程已被修改 (期望 {res.get('expected_md5','')} 实际 {res.get('current_md5','')})", file=sys.stderr)
            print(f"   处理: read-remote {res.get('path','')} 拉取最新, 合并后再回传", file=sys.stderr)
        else:
            print(f"❌ 上传失败: {res.get('error', '')} (path={res.get('path','')})", file=sys.stderr)
        return
    rp = res.get("path", "")
    print(f"✅ 已回传: {rp} (md5={res.get('md5')} 备份={'是' if res.get('backed_up') else '否'} 回读验证={'✓' if res.get('verified') else '⚠失败'})")
    if res.get("backed_up") and res.get("backup_file"):
        print(f"   备份: {res.get('backup_file')}")
    sc = res.get("syntax_check")
    if sc and sc.get("checked"):
        if sc.get("ok"):
            print(f"   语法: JS/HTML node --check 通过 ✓")
        else:
            print(f"   语法: ⚠ node --check 失败: {sc.get('error', '')}")
    if res.get("lint"):
        print(f"   PHP lint: {'通过' if res['lint'].get('passed') else '失败: ' + str(res['lint'].get('output',''))[:200]}")


def _do_remote_upload(conf: dict, log: Logger, api: ApiClient, rp: str, data: bytes,
                      local_md5: str, base_md5: str, cache: Path, dry_run: bool = False,
                      op_name: str = "write_remote") -> int:
    """write-remote / upload-file 共用回传输出层 (兼容 v4.13 输出格式)."""
    rc, res = _upload_one_compute(conf, log, api, rp, data, local_md5, base_md5, cache,
                                  dry_run=dry_run, op_name=op_name)
    _emit_upload_res(res)
    return rc


def _local_mode_str(path: Path) -> str:
    """本地文件权限 → "0755" 形式; 非 POSIX(Windows) 返回 "" 表示不改变远程权限。
    用于 upload-file 保留可执行位 —— 之前统一按 0644 上传, AI 传上去的脚本还得再 chmod。"""
    if os.name != "posix":
        return ""
    try:
        return format(path.stat().st_mode & 0o777, "04o")
    except OSError:
        return ""


def _upload_file_one(conf: dict, log: Logger, api: ApiClient, local_path: str,
                     dry_run: bool = False) -> tuple[int, dict]:
    """直传单个本地文件到远程。返回 (rc, res)。"""
    lp = Path(local_path).expanduser()
    if not lp.is_absolute():
        lp = Path(conf["watch_dir"]) / lp
    # FIX-symlink: 不 resolve 最后一级, 否则软链接会被替换成目标文件
    lp = Path(os.path.abspath(str(lp)))
    if not os.path.lexists(str(lp)):
        return 2, {"command": "upload_file", "path": local_path, "ok": False,
                   "error": "local_not_found", "hint": "文件不存在或不在 WATCH_DIR 内"}
    if lp.is_symlink():
        wd = Path(conf["watch_dir"]).resolve()
        rel = _rel_under_watch(lp, wd)
        if not rel:
            return 99, {"command": "upload_file", "path": local_path, "ok": False,
                        "blocked": True, "error": "path_outside_scope",
                        "message": f"软链接不在 WATCH_DIR 内: {lp}"}
        f = link_finger(lp, wd)
        if f["size"] < 0 or not f.get("link_safe"):
            return 99, {"command": "upload_file", "path": local_path, "ok": False,
                        "blocked": True, "error": "unsafe_symlink",
                        "message": f"软链接目标越界({f.get('link_reason')}), 拒绝上传: {f.get('link_target')}"}
        rp = rel
        try:
            _cr = api.checksum_remote(rp, "md5")
            _it = (_cr.get("items") or [{}])[0]
            if (_it.get("md5") or "").lower() == f["md5"] and _it.get("type") == "symlink":
                return 0, {"command": "upload_file", "path": local_path, "ok": True,
                           "skipped": True, "reason": "remote_same_link",
                           "md5": f["md5"], "type": "symlink",
                           "link_target": f["link_target"], "dry_run": bool(dry_run)}
        except Exception:
            pass
        if dry_run:
            return 0, {"command": "upload_file", "path": local_path, "ok": True,
                       "md5": f["md5"], "type": "symlink", "link_target": f["link_target"],
                       "dry_run": True}
        r = api.upload_symlink(rp, f["link_target"])
        if r.get("ok"):
            return 0, {"command": "upload_file", "path": rp, "ok": True,
                       "md5": f["md5"], "type": "symlink",
                       "link_target": f["link_target"], "link_target_rel": f["link_target_rel"]}
        return 4, {"command": "upload_file", "path": rp, "ok": False,
                   "error": r.get("error") or "upload_failed",
                   "message": r.get("message", "")}
    if not lp.is_file():
        return 2, {"command": "upload_file", "path": local_path, "ok": False,
                   "error": "local_not_found", "hint": "文件不存在或不在 WATCH_DIR 内"}
    try:
        rp = _resolve_remote_path(conf, str(lp))
    except Exception:
        rp = str(lp)
    # 远程路径必须在 scope 内 (相对路径, 不能是绝对路径)
    if rp.startswith("/") or ".." in rp.split("/"):
        _wd = conf.get("watch_dir", "?")
        return 99, {"command": "upload_file", "path": local_path, "ok": False,
                    "blocked": True, "level": "BLOCKED", "error": "path_outside_scope",
                    "message": (f"路径越界: {rp!r} 是绝对路径或含 ../，scope 只接受相对 WATCH_DIR 的相对路径(如 TV/config.php)。"
                                f"把目标文件放到 WATCH_DIR={_wd} 内，再用 --push <相对路径> 或 --upload-file <该文件> 重试")}
    data = lp.read_bytes()
    local_md5 = hashlib.md5(data).hexdigest()
    # POSIX 下带本地权限: 可执行脚本上传后仍是可执行文件(write-remote 不传 mode → 服务端保留原权限)
    _mode = _local_mode_str(lp)
    _want_x = bool(_mode) and bool(int(_mode, 8) & 0o111)
    # 🆕 FIX-P0-skip: 远程已有相同内容 (md5 一致) → 跳过上传, 不重复传输
    # 例外: 本地是可执行文件时不跳过, 否则 755 的脚本可能停在远程 644。
    try:
        _cr = api.checksum_remote(rp, "md5")
        _ritems = _cr.get("items") or []
        if (not _want_x) and _ritems and (_ritems[0].get("md5") or "").lower() == local_md5:
            return 0, {"command": "upload_file", "path": local_path, "ok": True,
                       "skipped": True, "reason": "remote_same_content", "md5": local_md5,
                       "dry_run": bool(dry_run)}
    except Exception:
        pass
    cache = _remote_cache_path(conf.get("scope", ""), rp)
    # 写入缓存, 使 read/write-remote 链路无缝衔接 (新文件: base_md5 为空)
    base_md5 = _read_remote_meta(cache) if cache.is_file() else ""
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    try:
        _lmtime = os.stat(str(lp)).st_mtime
    except Exception:
        _lmtime = None
    return _upload_one_compute(conf, log, api, rp, data, local_md5, base_md5, cache,
                               dry_run=dry_run, op_name="upload_file", mode=_mode, mtime=_lmtime)

def cmd_upload_file(conf: dict, log: Logger, api: ApiClient, local_path: str | list,
                    dry_run: bool = False) -> int:
    """v4.13: 本地文件直传远程 (创建新文件场景, 免手动建缓存).
    远程路径 = 相对 WATCH_DIR 的路径; 自动写 read-remote 缓存, 后续可 read/write-remote 继续改.
    🆕 v4.14: 支持多文件批量 --upload-file a.html b.js (单文件输出形状与 v4.13 一致)."""
    targets = local_path if isinstance(local_path, list) else [local_path]
    if len(targets) == 1:
        rc, res = _upload_file_one(conf, log, api, targets[0], dry_run=dry_run)
        if res.get("error") == "local_not_found":
            if "--json" in sys.argv:
                print(json.dumps({"command": "upload_file", "path": res["path"], "ok": False,
                                  "error": "local_not_found", "hint": "文件不存在或不在 WATCH_DIR 内"}, ensure_ascii=False))
            else:
                print(f"❌ 本地文件不存在: {res['path']}", file=sys.stderr)
            return rc
        if res.get("blocked"):
            if "--json" in sys.argv:
                print(json.dumps({k: v for k, v in res.items() if k != "command"}, ensure_ascii=False))
            else:
                print(f"[BLOCKED] {res.get('message', res.get('path',''))}", file=sys.stderr)
            return rc
        _emit_upload_res(res)
        return rc
    # 批量模式
    results, rc_all = [], 0
    for t in targets:
        rc, res = _upload_file_one(conf, log, api, t, dry_run=dry_run)
        results.append(res)
        if rc != 0:
            if rc == 99:
                rc_all = 99
            elif rc_all != 99:
                rc_all = max(rc_all, rc)
    if "--json" in sys.argv:
        print(json.dumps({"command": "upload-file", "batch": True, "count": len(results),
                          "ok": rc_all == 0, "dry_run": bool(dry_run), "results": results},
                         ensure_ascii=False))
    else:
        for res in results:
            if res.get("ok"):
                print(f"✅ 已直传: {res['path']} (md5={res.get('md5')} 备份={'是' if res.get('backed_up') else '否'} 回读验证={'✓' if res.get('verified') else '⚠失败'})")
            elif res.get("dry_run"):
                print(f"[dry-run] 将直传 {res['path']} (md5={res.get('md5')})")
            elif res.get("blocked"):
                print(f"[BLOCKED] {res['path']}: {res.get('message','')}", file=sys.stderr)
            else:
                print(f"❌ {res['path']}: {res.get('error', '')}", file=sys.stderr)
        print(f"batch=done ok={rc_all == 0} count={len(results)}")
    return rc_all

# ==================== v4.5 AI 远程文件管理命令 ====================

def cmd_info(conf: dict, log: Logger, api: ApiClient, target: str) -> int:
    """查看远程文件详细信息。"""
    rp = _resolve_remote_path(conf, target)
    r = api.stat_remote(rp)
    if not r.get("ok"):
        print(f"❌ 查询失败: {r.get('error', '')} (path={rp})", file=sys.stderr)
        return 2
    info = r.get("info", r)
    _is_link = info.get("type") == "symlink" or bool(info.get("is_symlink"))
    if "--json" in sys.argv:
        # 🆕 v4.12: info --json 结构化
        import datetime as _dt
        _m = info.get("mtime", 0) or 0
        _out = {"command": "info", "path": rp,
                "type": "symlink" if _is_link else ("dir" if info.get('type') == 'dir' else ("special" if info.get('type') == 'special' else "file")),
                "size": info.get("size", 0),
                "mtime": _dt.datetime.fromtimestamp(_m).strftime("%Y-%m-%d %H:%M:%S") if _m else "",
                "md5": info.get("md5", ""), "mode": info.get("mode", ""),
                "lines": info.get("lines")}
        if _is_link:
            # FIX-symlink-info: 链接必须能看到目标/安全/断链/环, 而不是被当作普通文件.
            _out.update({"link_target": info.get("link_target"),
                         "link_target_b64": info.get("link_target_b64"),
                         "link_target_rel": info.get("link_target_rel"),
                         "safe": info.get("safe"), "broken": info.get("broken"),
                         "cycle": info.get("cycle"), "resolvable": info.get("resolvable"),
                         "reason": info.get("reason")})
        print(json.dumps(_out, ensure_ascii=False))
        return 0
    print(f"\n📊 文件信息: {rp}\n")
    print(f"  类型:     {'软链接' if _is_link else ('目录' if info.get('type') == 'dir' else ('特殊文件' if info.get('type') == 'special' else '文件'))}")
    if _is_link:
        print(f"  链接目标: {info.get('link_target')}")
        print(f"  目标(规范化): {info.get('link_target_rel')}")
        print(f"  可解析:   {'否' if info.get('broken') else '是'}"
              + (" (循环链接)" if info.get('cycle') else ""))
    print(f"  大小:     {info.get('size', 0):,} B ({info.get('size', 0)/1024:.1f} KB)")
    import datetime
    mtime = info.get("mtime", 0)
    if mtime:
        dt = datetime.datetime.fromtimestamp(mtime)
        print(f"  修改时间: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
    if info.get("md5"):
        print(f"  MD5:      {info['md5']}")
    if info.get("mode"):
        print(f"  权限:     {info['mode']}")
    if info.get("lines") is not None:
        print(f"  行数:     {info['lines']}")
    return 0

def cmd_mkdir(conf: dict, log: Logger, api: ApiClient, target: str) -> int:
    """在服务器上创建目录。"""
    rp = _resolve_remote_path(conf, target)
    r = api.mkdir_remote(rp)
    if not r.get("ok"):
        # 🆕 FIX-json-v4.17: --json 失败/成功路径均输出纯 JSON
        if "--json" in sys.argv:
            print(json.dumps({"command": "mkdir", "ok": False, "path": rp, "error": r.get("error", "")}, ensure_ascii=False))
        else:
            print(f"❌ 创建失败: {r.get('error', '')} (path={rp})")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "mkdir", "ok": True, "created": True, "path": rp}, ensure_ascii=False))
    else:
        print(f"✅ 目录已创建: {rp}")
    return 0

# ============ [AI-SAFE v1.1] 客户端防护闸: rm 危险路径保护 + exec 毁灭命令黑名单 ============
# 定位: 工具仅 AI 使用场景的第一道闸 (L1 硬拦截)。用途: AI 编写/部署网站、维护服务器 (不绑定任何项目)。
# 原则: ① 默认放行制 (黑名单), 保证开发/部署/维护命令零卡顿;
#       ② 拦截强度 ∝ 不可逆性: 不可逆毁灭操作 BLOCK 硬拦 / 危险但运维可能合法 WARN 提醒 / 其余放行;
#       ③ 保护「根」不保护「子树」: 系统目录/scope根自身 BLOCK, 业务子树放行 (服务端备份/回滚/非root兜底);
#       ④ 拦「执行效果」不拦「字符串」: 变量展开 + 路径归一化 + 通配形态识别后再判定。
def _rm_path_guard(rp: str) -> str | None:
    """删除前危险路径保护 (系统级通用, 不绑定项目)。
    保护对象: scope 根本身 + 系统一级目录「自身」(绝对形式)。
    项目内/系统目录子树任意删除一律放行 (AI 自主, 服务器端有备份/审计兜底)。"""
    if rp is None:
        return None
    norm = rp.strip()
    if norm in ("", "."):
        return "[BLOCKED] rm 危险路径保护: 拒绝删除 scope 根路径 (已拦截, 未执行)"
    # 绝对路径形式: 只拦系统一级目录自身 (rm -rf /etc), 子树 (/etc/xxx /tmp/xxx) 放行
    if norm.startswith("/"):
        try:
            while "//" in norm:
                norm = norm.replace("//", "/")
            norm = os.path.normpath(norm)
        except Exception:
            pass
        for d in _SYS_ROOT_DIRS:
            if norm == d:
                return f"[BLOCKED] rm 危险路径保护: 拒绝删除系统目录 '{d}' 自身 (已拦截, 未执行)"
    return None

def _shell_rm_scan(cmd: str) -> str | None:
    """--shell 命令的 rm 危险路径客户端预检: 复用 _rm_path_guard, 补 exec/shell 防护对称。

    只匹配命令起始或 ;/&&/| 分隔后的 rm 形态, 避免误伤 echo 'rm -rf /etc' 等打印场景;
    变量/命令替换目标无法静态判断, 跳过不误伤; 业务目录子树 (rm -rf /var/log/old) 照常放行。
    """
    if not cmd:
        return None
    _pat = re.compile(r'(?:^|[;&|]\s*)\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*[a-zA-Z-]*\s+(\S+)')
    for _m in _pat.finditer(cmd):
        _tgt = _m.group(1).strip().strip('"\'')
        if _tgt.startswith(('$', '`', '(')):
            continue
        _blk = _rm_path_guard(_tgt)
        if _blk:
            return _blk
    return None

# 系统一级目录 (保护「自身」): 覆盖常见 Linux/安卓盒子 (/data /boot)
_SYS_ROOT_DIRS = ("/", "/etc", "/var", "/usr", "/root", "/home", "/mnt", "/tmp", "/opt", "/srv", "/bin", "/sbin", "/lib", "/proc", "/sys", "/dev", "/boot", "/data")
_SYS_ROOT_NAMES = tuple(_d.lstrip("/") for _d in _SYS_ROOT_DIRS if _d != "/")
_SENSITIVE_FILES = ("/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/gshadow", "/etc/fstab", "/etc/group", "/etc/crontab")
# 业务部署根: scope 的 remote_base 均在旗下; 自身/直接子树清空视为删光 scope, 子树内业务操作放行
_DEPLOY_ROOT = "/mnt/data/wwwroot"

def _expand_home(p: str) -> str:
    """~ 前缀展开为 $HOME 占位 (服务器家目录值未知, 用 $HOME 标记交给前缀判定)。"""
    p = (p or "").strip()
    if p == "~" or p.startswith("~/"):
        return "$HOME" + p[1:]
    return p

def _norm_abs(p: str) -> str:
    """绝对路径归一化 (折叠 // 与 ..); 非绝对路径原样返回。"""
    if p.startswith("/"):
        try:
            while "//" in p:
                p = p.replace("//", "/")
            return os.path.normpath(p)
        except Exception:
            return p
    return p

def _is_glob_all(t: str) -> bool:
    """通配全删形态: * / ./* / dir/* / dir/**。"""
    b = (t or "").rstrip()
    return b in ("*", "./*", "**", "./**") or b.endswith("/*") or b.endswith("/**")

def _is_tmp_tree(t: str) -> bool:
    """临时区子树 (/tmp、/data/local/tmp): 清空/清理是合法高频运维, 不按系统目录拦截。"""
    if not t:
        return False
    return t == "/tmp" or t.startswith("/tmp/") or t == "/data/local/tmp" or t.startswith("/data/local/tmp/")

def _is_sys_tree(t: str) -> bool:
    """t 是否命中系统目录自身或其子树 (用于 cd 组合判定); 业务部署区/临时区视为非系统。"""
    if not t:
        return False
    if _is_tmp_tree(t):
        return False
    if t == "/" or t in _SYS_ROOT_DIRS:
        return True
    if t.startswith(_DEPLOY_ROOT):
        return False
    return t.startswith("/") and t.split("/")[1] in _SYS_ROOT_NAMES

def _is_scope_root_level(p: str) -> bool:
    """p 是否位于部署根/scope 根级 (删除 = 删光整个 scope 或全部线上代码)。"""
    if not p.startswith(_DEPLOY_ROOT):
        return False
    return len(p.split("/")) <= 6  # /mnt/data/wwwroot[/default[/bxbb]]

def _split_command(cmd: str) -> list:
    """按顶级 && / || / ; 分段 (引号内分隔符不分段, 防解释器脚本被误拆)。"""
    segs, cur, q, i, n = [], [], None, 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if q:
            if ch == q:
                q = None
            cur.append(ch); i += 1; continue
        if ch in ("'", '"'):
            q = ch; cur.append(ch); i += 1; continue
        if ch == "\\":
            cur.append(ch)
            if i + 1 < n:
                cur.append(cmd[i + 1]); i += 2
            else:
                i += 1
            continue
        if cmd.startswith("&&", i) or cmd.startswith("||", i) or ch == ";":
            segs.append("".join(cur)); cur = []
            i += 2 if cmd[i:i + 2] in ("&&", "||") else 1
            continue
        cur.append(ch); i += 1
    segs.append("".join(cur))
    return [s for s in segs if s.strip()]

def _script_danger_scan(script: str) -> tuple | None:
    """解释器 -c 脚本内容检测 (v1.1: 删除类调用+危险目标组合判定, 不再枚举关键字)。
    返回: None=放行 / ('BLOCKED'|'WARN', msg 片段)。"""
    if not script:
        return None
    # ① shell 式毁灭命令 (保留原规则; [rR] 覆盖 rm -Rf 大小写)
    if re.search(r"rm\s+-[a-z]*[rR][a-z]*f?[a-z]*\s+[/*]", script) or \
       re.search(r"\bmkfs\b", script) or \
       re.search(r"\b(shutdown|reboot|poweroff)\b", script) or \
       re.search(r"dd\s+.*\bof=/dev/", script) or \
       re.search(r"chmod\s+-R\s+777\s+/", script) or \
       re.search(r"crontab\s+-r\b", script) or \
       re.search(r"iptables\s+-[FXZ]", script):
        return ("BLOCKED", "含毁灭命令, 已拦截 (未执行)")
    # ② 删除类调用: 提取首个字符串参数 → 危险目标 BLOCK / 一般删除 WARN
    _DEL_CALLS = [
        (r"shutil\.rmtree\s*\(\s*(['\"])?(/?(?:[^'\"\s)]*))", "shutil.rmtree"),
        (r"shutil\.rmtree\s*\(\s*Path\s*\(\s*(['\"])([^'\"]+)", "shutil.rmtree(Path(...))"),
        (r"os\.removedirs\s*\(\s*(['\"])?(/?(?:[^'\"\s)]*))", "os.removedirs"),
        (r"os\.remove\s*\(\s*(['\"])?(/?(?:[^'\"\s)]*))", "os.remove"),
        (r"os\.unlink\s*\(\s*(['\"])?(/?(?:[^'\"\s)]*))", "os.unlink"),
        (r"os\.rmdir\s*\(\s*(['\"])?(/?(?:[^'\"\s)]*))", "os.rmdir"),
        (r"\.unlink\s*\(\s*(['\"])?(/?(?:[^'\"\s)]*))", "Path.unlink"),
    ]
    for pat, label in _DEL_CALLS:
        m = re.search(pat, script)
        if m:
            tgt = (m.group(2) or "").strip()
            tgt_n = _norm_abs(_expand_home(tgt))
            if (tgt_n in _SYS_ROOT_DIRS or tgt_n == _DEPLOY_ROOT or tgt_n in _SENSITIVE_FILES
                    or tgt.startswith("$HOME") or tgt.startswith("${HOME}") or tgt.startswith("~")):
                return ("BLOCKED", f"调用 {label} 删除危险目标 '{tgt}', 已拦截 (未执行)")
            return ("WARN", f"调用 {label}('{tgt}') — 请确认脚本内容")
    # ③ 代码执行类 (保留原 WARN)
    if re.search(r"os\.system\s*\(|subprocess\.(run|call|Popen)|shell_exec|passthru|eval\s*\(|exec\s*\(", script):
        return ("WARN", "含代码执行能力 — 请确认脚本内容")
    return None

def _check_one_segment(seg: str, prev_cd: str | None = None) -> tuple | None:
    """单命令段检查 (含递归解包)。返回 None=放行 / ('BLOCKED'|'WARN', msg)。
    prev_cd: 前一段 cd 的目标目录, 用于 `cd 系统目录 && rm -rf *` 组合判定。"""
    seg = seg.strip()
    if not seg:
        return None
    try:
        toks = shlex.split(seg)
    except Exception:
        toks = seg.split()
    if not toks:
        return None
    # ---- 递归解包: 包装器(sudo/env/nohup/time/command/nice/watch/stdbuf/xargs) 与 解释器(bash/sh -c 递归, python/perl/php -c 脚本检测) ----
    depth = 0
    while toks and depth < 6:
        depth += 1
        name0 = os.path.basename(toks[0]).lower()
        args0 = toks[1:]
        if name0 in ("sudo", "env", "nohup", "time", "command", "nice", "stdbuf", "watch", "xargs"):
            toks = args0
            continue
        if name0 == "su":
            nxt = None
            for i, a in enumerate(args0):
                if a in ("-c", "--command") and i + 1 < len(args0):
                    try:
                        nxt = shlex.split(args0[i + 1])
                    except Exception:
                        nxt = args0[i + 1].split()
                    break
            if nxt:
                toks = nxt
                continue
            return ("WARN", "[WARN] exec 提醒: su 切换用户 — 请确认权限需要")
        if name0 in ("bash", "sh", "zsh", "dash", "ksh", "ash"):
            nxt = None
            for i, a in enumerate(args0):
                if a == "-c" and i + 1 < len(args0):
                    try:
                        nxt = shlex.split(args0[i + 1])
                    except Exception:
                        nxt = args0[i + 1].split()
                    break
            if nxt:
                toks = nxt
                continue
            return None  # bash script.sh 等放行
        if name0 in ("python3", "python", "perl", "php", "ruby", "node"):
            for i, a in enumerate(args0):
                if a in ("-c", "-e", "-E", "-r") and i + 1 < len(args0):
                    script = args0[i + 1]
                    _sr = _script_danger_scan(script)
                    if _sr:
                        return (_sr[0], f"[{'BLOCKED' if _sr[0] == 'BLOCKED' else 'WARN'}] exec {'黑名单' if _sr[0] == 'BLOCKED' else '提醒'}: 解释器脚本({name0} -c){_sr[1]}")
                    return None
            return None
        break
    # ---- 解包完成, toks = 真实命令 ----
    name = os.path.basename(toks[0]).lower()
    args = toks[1:]
    argstr = " ".join(args)
    # ===== BLOCK ① 命令名级: 关机/重启/格式化 (用户管理降 WARN: 维护场景建部署账号合法) =====
    if name in ("shutdown", "reboot", "halt", "poweroff", "telinit", "init") \
       or name.startswith("mkfs.") or name in ("mkfs", "mkswap"):
        return ("BLOCKED", f"[BLOCKED] exec 黑名单: 命令 '{name}' 被禁止执行 (已拦截, 未执行; 如确需请人工处理)")
    # ===== BLOCK ② systemctl 整机级 =====
    if name == "systemctl":
        for t in args:
            if t in ("reboot", "poweroff", "halt"):
                return ("BLOCKED", "[BLOCKED] exec 黑名单: systemctl 整机关机/重启已拦截 (服务级 restart/stop 放行)")
    # ===== BLOCK ③ 防火墙规则破坏 =====
    if name in ("iptables", "ip6tables"):
        for t in args:
            if t in ("-F", "-X", "-P", "-Z"):
                return ("BLOCKED", f"[BLOCKED] exec 黑名单: {name} 规则修改/清空已拦截 (查看用 -L/-S/-n 放行)")
    if name == "nft" and any(t in ("flush", "delete") for t in args):
        return ("BLOCKED", "[BLOCKED] exec 黑名单: nft 规则清空/删除已拦截")
    # ===== BLOCK ④ 分区/格式化写操作 =====
    if name in ("fdisk", "parted", "cfdisk", "sfdisk"):
        if not any(t in ("-l", "--list", "-s", "--print") for t in args):
            return ("BLOCKED", f"[BLOCKED] exec 黑名单: '{name}' 写分区操作已拦截 (查看用 -l 放行)")
    # ===== BLOCK ⑤ docker 数据卷/资源毁灭 (部署场景核心, 正常 docker 命令放行) =====
    if name == "docker":
        if args[:1] == ["volume"] and "rm" in args:
            return ("BLOCKED", "[BLOCKED] exec 黑名单: docker volume rm 删除数据卷已拦截 (数据不可逆)")
        if "prune" in args and any(t in ("--volumes", "-v") for t in args):
            return ("BLOCKED", "[BLOCKED] exec 黑名单: docker system prune 带数据卷清理已拦截")
        if "down" in args and any(t in ("-v", "--volumes") for t in args):
            return ("BLOCKED", "[BLOCKED] exec 黑名单: docker compose down -v 删除数据卷已拦截")
        if "prune" in args and any(t in ("-a", "-af") for t in args):
            return ("WARN", "[WARN] exec 提醒: docker system prune -a 清理全部未使用资源 — 确认后可执行")
        if args[:1] == ["rm"] and any(t in ("-f", "--force") for t in args):
            return ("WARN", "[WARN] exec 提醒: docker rm -f 强制删除容器 — 容器可重建, 确认目标后执行")
    if name == "docker-compose" and "down" in args and any(t in ("-v", "--volumes") for t in args):
        return ("BLOCKED", "[BLOCKED] exec 黑名单: docker-compose down -v 删除数据卷已拦截")
    # ===== BLOCK ⑥ 参数级毁灭模式 (v1.1: 变量展开 + 归一化 + 通配形态 + cd 组合) =====
    if name == "rm":
        # 递归标志: -r/-R/--recursive 或组合短参 (-rf/-fr/-Rf 等)
        _rec = any(a in ("-r", "-R", "--recursive") for a in args) or any(
            re.search(r"^-[a-zA-Z]*[rR][a-zA-Z]*$", a) for a in args
            if a.startswith("-") and not a.startswith("--"))
        _targets = [a for a in args if not a.startswith("-") and a != "--"]
        # 任意形态: 单文件删除敏感系统文件 (rm /etc/passwd)
        for t in _targets:
            _tn = _norm_abs(_expand_home(t))
            if _tn in _SENSITIVE_FILES:
                return ("BLOCKED", f"[BLOCKED] exec 黑名单: 'rm {t}' 删除敏感系统文件已拦截 (未执行)")
        if not _rec:
            return None
        for t in _targets:
            _te = _expand_home(t)
            _tn = _norm_abs(_te)
            # ① 系统目录自身 / 部署根自身 / 家目录前缀 → BLOCK
            if _tn in _SYS_ROOT_DIRS or _tn == _DEPLOY_ROOT or _te.startswith("$HOME") or _te.startswith("~"):
                return ("BLOCKED", f"[BLOCKED] exec 黑名单: 'rm {t}' 危险删除已拦截 (已拦截, 未执行)")
            # 通配全删形态分级
            if _is_glob_all(t):
                _base = t.rstrip()
                if _base.endswith("/**"):
                    _base = _base[:-3]
                elif _base.endswith("/*"):
                    _base = _base[:-2]
                _bn = _norm_abs(_expand_home(_base))
                # ② 系统/部署根/scope根级清空 → BLOCK (rm -rf /etc/* /mnt/data/wwwroot/* /.../bxbb/*);
                #    临时区 (/tmp/* /data/local/tmp/*) 除外 → 走 ④ WARN (清残留合法高频)
                if (_bn in _SYS_ROOT_DIRS and not _is_tmp_tree(_bn)) or _is_scope_root_level(_bn):
                    return ("BLOCKED", f"[BLOCKED] exec 黑名单: 'rm {t}' 系统/部署根级子树清空已拦截 (未执行)")
                # ③ 前一段 cd 在系统目录内 + 全删 → BLOCK (cd /var/log && rm -rf *)
                if prev_cd and _is_sys_tree(_norm_abs(_expand_home(prev_cd))) and not _is_scope_root_level(_bn):
                    return ("BLOCKED", f"[BLOCKED] exec 黑名单: 'rm {t}' 系统目录内通配全删已拦截 (未执行)")
                # ④ 业务子树全删 → WARN (首次提醒+ACK 记忆; 清缓存/清构建产物合法)
                return ("WARN", f"[WARN] exec 提醒: 'rm {t}' 通配全删 — 请确认目标目录与影响范围")
            # ⑤ 明确路径 (系统子树/业务子树) 一律放行: 服务器备份/回滚/非root 兜底
        return None
    if name == "dd":
        for t in args:
            if t.startswith("of=") and "/dev/" in t:
                return ("BLOCKED", "[BLOCKED] exec 黑名单: 'dd of=/dev/...' 写设备已拦截 (写普通文件放行)")
    if name in ("chmod", "chown"):
        if re.search(r"-R|--recursive", argstr) and re.search(r"(^|\s)/(\s|$)", argstr):
            return ("BLOCKED", f"[BLOCKED] exec 黑名单: '{name} -R' 递归修改根目录已拦截")
    if name == "crontab" and "-r" in args:
        return ("BLOCKED", "[BLOCKED] exec 黑名单: 'crontab -r' 清空计划任务已拦截")
    if name == "umount" and "/" in args:
        return ("BLOCKED", "[BLOCKED] exec 黑名单: 'umount /' 卸载根文件系统已拦截")
    if name == "mount":
        # 只拦根文件系统改读写 (mount -o remount,rw /); 业务/数据区挂载运维合法放行
        if "/" in args:
            for i in range(len(args) - 1):
                if args[i] in ("-o", "--options") and "remount" in args[i + 1] and "rw" in args[i + 1]:
                    return ("BLOCKED", "[BLOCKED] exec 黑名单: 'mount -o remount,rw /' 根文件系统改读写已拦截")
    if name == "kill" and any(t in ("-9", "-kill") for t in args) and "1" in args:
        return ("BLOCKED", "[BLOCKED] exec 黑名单: 'kill -9 1' 杀系统主进程已拦截")
    # cd 不拦截: 对齐 sshd 常规操作 (cd /、cd /etc 等查看系统文件合法); cd 本身不毁灭,
    #   组合风险 `cd 系统目录 && rm -rf *` 由 rm 全删形态 + prev_cd 判定兜住 (见 rm ③)
    # 重定向写系统敏感文件: > 覆盖=BLOCK (毁灭) / >> 追加=WARN (维护挂载/配置合法)
    for i, t in enumerate(args):
        if t == ">" and i + 1 < len(args) and args[i + 1] in _SENSITIVE_FILES:
            return ("BLOCKED", f"[BLOCKED] exec 黑名单: 覆盖写入敏感系统文件 '{args[i + 1]}' 已拦截 (未执行)")
        if t == ">>" and i + 1 < len(args) and args[i + 1] in _SENSITIVE_FILES:
            return ("WARN", f"[WARN] exec 提醒: 追加写入系统配置文件 '{args[i + 1]}' — 请确认内容正确")
    # ===== WARN: 危险但运维可能合法 (提醒不阻断) =====
    # 系统账号/权限变更 (维护场景建部署账号、改密码可回滚 → 提醒确认)
    if name in ("useradd", "userdel", "usermod", "passwd", "chpasswd", "groupadd", "groupdel"):
        return ("WARN", f"[WARN] exec 提醒: 系统账号/权限变更 ({name}) — 请确认目标账号与影响")
    if name == "systemctl" and any(t in ("stop", "kill", "mask") for t in args):
        return ("WARN", "[WARN] exec 提醒: systemctl stop/kill/mask 核心服务 — 请确认维护窗口")
    if name in ("mysql", "mariadb"):
        if re.search(r"\bDROP\s+DATABASE\b", argstr, re.I) or re.search(r"\bTRUNCATE\s+TABLE\b", argstr, re.I) or re.search(r"\bDELETE\s+FROM\b", argstr, re.I):
            return ("WARN", "[WARN] exec 提醒: 数据库 DROP/TRUNCATE/DELETE — 请确认有备份且非生产数据")
    if name in ("redis-cli", "redis"):
        if any(t.upper() in ("FLUSHALL", "FLUSHDB") for t in args):
            return ("WARN", "[WARN] exec 提醒: redis FLUSHALL/FLUSHDB 清库 — 请确认目标实例")
    if name in ("ip", "ifconfig", "route"):
        if (name == "ip" and any(t in ("link", "addr", "route") for t in args) and any(t in ("down", "del", "flush") for t in args)) \
           or (name == "ifconfig" and any(t in ("down", "up") for t in args) and len(args) > 1) \
           or (name == "route" and any(t in ("del", "add") for t in args)):
            return ("WARN", "[WARN] exec 提醒: 网络接口/路由变更 — 请确认不会导致失联")
    if name == "find" and "-exec" in args and re.search(r"rm\s", argstr):
        return ("WARN", "[WARN] exec 提醒: find -exec rm 批量删除 — 请确认目标范围")
    if name in ("wget", "curl"):
        if re.search(r"\|\s*(sh|bash|zsh)\b", argstr):
            return ("WARN", "[WARN] exec 提醒: 检测到「下载即执行」(curl|sh) — 请确认安装来源")
    return None

# ===== [AI-SAFE v5] 一般危险命令分级: 第一次 WARN 提醒并记录, 之后 ACKED 放行 =====
# 绝对危险 (BLOCK) 永不记忆永不放行; 一般危险 (WARN) 用持久化文件跨进程记忆。
_WARN_ACK_FILE = os.path.expanduser("~/.ai_sync_warn_ack.json")

def _warn_norm_key(seg: str) -> str:
    """一般危险命令的规范化 key (命令级粒度: 同一条命令独立计数)。"""
    try:
        return " ".join(shlex.split(seg))
    except Exception:
        return seg.strip()

def _warn_already_acked(key: str) -> bool:
    """[AI-SAFE v1.1.2] ACK 时效 10 分钟: 过期后重新提醒 (防"很久前确认过"永久放行)。
    记录含 acked_ts (epoch 秒), 距确认时间 < 600s 视为有效 ACK。"""
    try:
        with open(_WARN_ACK_FILE, encoding="utf-8") as f:
            d = json.load(f)
        _rec = d.get(key)
        if not _rec:
            return False
        _ts = float(_rec.get("acked_ts", 0) or 0)
        return _ts > 0 and (time.time() - _ts) < 600
    except Exception:
        return False

def _warn_mark_acked(key: str) -> None:
    try:
        d = {}
        try:
            with open(_WARN_ACK_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        d[key] = d.get(key, {})
        d[key]["count"] = d[key].get("count", 0) + 1
        d[key]["acked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        d[key]["acked_ts"] = time.time()  # [AI-SAFE v1.1.2] epoch 秒, 供 10 分钟时效判定
        with open(_WARN_ACK_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _safety_meta(level: str, msg: str, cmd: str = "") -> dict:
    """[AI-SAFE v1.1.2] 防护消息结构化: 从消息推断 category + 生成稳定 rule_id + 提取 target。
    输出层使用 (exit 99 契约不变, 人类文本保留), 供 AI 程序化解析拦截原因与处理方式。"""
    m = msg or ""
    cat = "blacklist"
    if "危险路径保护" in m:
        cat = "rm_path_guard"
    elif "解释器脚本" in m or "解释器" in m:
        cat = "script_danger"
    elif "通配全删" in m or "通配" in m:
        cat = "wildcard"
    elif "敏感系统文件" in m or "系统文件" in m:
        cat = "sysfile"
    elif "删除数据卷" in m or "volume" in m:
        cat = "docker_volume"
    elif "写设备" in m:
        cat = "device_write"
    elif "写分区" in m:
        cat = "partition_write"
    elif "系统账号/权限变更" in m or "账号/权限" in m:
        cat = "account_change"
    elif "追加写入系统配置" in m or "系统配置" in m:
        cat = "sysconfig_write"
    elif "整机关机/重启" in m or "关机/重启" in m:
        cat = "service_control"
    elif "crontab" in m:
        cat = "crontab"
    elif "umount" in m:
        cat = "mount"
    elif "docker" in m:
        cat = "docker"
    elif "提醒" in m or "确认" in m:
        cat = "warn_general"
    rule_id = "aisafe-" + hashlib.md5(m.encode("utf-8", "replace")).hexdigest()[:10]
    # target 提取: 引号内路径/命令 (如 rm 'x', 调用 label('tgt')), 否则取 cmd 本身
    target = ""
    for _pat in (r"'([^']*)'", r'"([^"]*)"', r"([/\w][\w./\-]*) "):
        _mm = re.search(_pat, m)
        if _mm and _mm.group(1) not in ("BLOCKED", "WARN", "ACKED"):
            target = _mm.group(1)
            break
    if not target:
        _mm = re.search(r"([/][\w.\-/]+)", m)
        if _mm:
            target = _mm.group(1)
    return {"category": cat, "rule_id": rule_id, "target": target, "level": level}

def _exec_block_check(cmd: str) -> tuple | None:
    """exec 命令检查 (系统级通用, 不绑定项目; 含包装绕过防护; 一般危险分级记忆)。
    返回: None=放行 / ('BLOCKED', msg)=绝对危险硬拦截 / ('WARN', msg)=一般危险首次提醒 / ('ACKED', msg)=一般危险已确认过放行。
    默认放行制: 只拦系统级毁灭性命令与毁灭性参数; 开发/部署/维护服务器的正常命令零卡顿。
    防绕过: ① 按 && / || / ; 分段逐段检查 (防 cd / && rm -rf *);
            ② 递归解包 sudo/env/nohup/time/bash -c 等包装 (防 sudo rm -rf /);
            ③ 解释器 -c 脚本内容检测 (防 python3 -c os.system(...))。"""
    if not cmd or not cmd.strip():
        return None
    # fork bomb (整串检测, 最优先)
    if re.search(r":\(\)\s*\{\s*:\|:&\s*\};:", cmd):
        return ("BLOCKED", "[BLOCKED] exec 黑名单: fork bomb 已拦截 (已拦截, 未执行)")
    # 按顶级 && || ; 分段 (引号感知), 每段独立检查; 传递前一段 cd 目标 (组合全删判定)
    prev_cd = None
    for seg in _split_command(cmd):
        r = _check_one_segment(seg, prev_cd=prev_cd)
        if not r:
            # 提取本段 cd 目标, 供下一段 rm 全删组合判定
            try:
                _toks = shlex.split(seg)
                if _toks and os.path.basename(_toks[0]).lower() == "cd" and len(_toks) > 1:
                    prev_cd = _toks[1]
            except Exception:
                pass
            continue
        if r[0] == "WARN":
            # 一般危险: 第一次提醒并记录; 之后放行 (AI 已看到提醒仍执行 = 有意操作)
            key = _warn_norm_key(seg)
            if _warn_already_acked(key):
                return ("ACKED", f"[ACKED] exec: 该命令此前已提醒确认, 本次放行 ({key})")
            _warn_mark_acked(key)
        return r
    return None


def cmd_rm(conf: dict, log: Logger, api: ApiClient, targets: list, recursive: bool = False) -> int:
    """删除远程文件或目录(支持多文件)。recursive=True 时递归删除非空目录。"""
    rps = [_resolve_remote_path(conf, t) for t in targets]
    # [AI-SAFE v1] 危险路径保护 (硬拦截, 先于确认); 99=策略阻断 (README 退出码契约)
    for rp in rps:
        _rg = _rm_path_guard(rp)
        if _rg:
            if "--json" in sys.argv:
                _rg_meta = _safety_meta("BLOCKED", _rg, cmd=f"rm {rp}")
                print(json.dumps({"command": "rm", "blocked": True, "level": "BLOCKED",
                                  "message": _rg, "category": _rg_meta["category"],
                                  "rule_id": _rg_meta["rule_id"], "target": _rg_meta["target"]},
                                 ensure_ascii=False))
            else:
                print(_rg, file=sys.stderr)
            return 99
    # 🆕 FIX-rm-multi-v4.21: 多文件时前缀授权需覆盖全部目标 (对齐 deploy)
    _ad = _ALLOW_DESTRUCTIVE
    if _ad and _ad != "*" and len(rps) > 1:
        _bad = [rp for rp in rps if not (rp.rstrip("/") == _ad.rstrip("/") or rp.startswith(_ad.rstrip("/") + "/"))]
        if _bad:
            if "--json" in sys.argv:
                print(json.dumps({"command": "rm", "blocked": True, "level": "REQUIRE_AUTH",
                                  "message": f"前缀授权未覆盖目标: {', '.join(_bad[:3])}",
                                  "authorized_prefix": _ad, "category": "aisafe-prefix",
                                  "hint": f"用 --allow-destructive {_ad.rstrip('/') + '/'} 覆盖全部路径, 或无值 --allow-destructive"},
                                 ensure_ascii=False))
            else:
                print(f"\n[BLOCKED] 前缀授权未覆盖删除目标: {', '.join(_bad[:3])}", file=sys.stderr)
                print(f"   授权前缀: {_ad}", file=sys.stderr)
            return 2
    _sum_desc = "\n    ".join(rps)
    if not _confirm_danger("删除远程文件", f"将删除 {len(rps)} 个:\n    {_sum_desc}" + (" (递归删除目录)" if recursive else ""),
                           irreversible=True, target=(rps[0] if len(rps) == 1 else (_ad or ""))):
        return 99  # v4.29: 阻断退出码对齐契约
    _fail = 0
    _missed = []   # FIX-v4.33: 本就不存在的目标 (already_missing)
    for rp in rps:
        r = api.delete_remote(rp, recursive=recursive)
        if not r.get("ok"):
            _fail += 1
            # 🆕 v4.29: 服务端 error=rmdir_fail(详情在 message) — 旧条件永不命中, 提示丢失
            hint = " (加 -R 递归删除)" if (r.get("error") == "rmdir_fail" or "not empty" in (r.get("message") or "")) else ""
            print(f"❌ 删除失败: {r.get('error', '')} (path={rp}){hint}", file=sys.stderr)
            continue
        extra = " (递归)" if recursive and r.get("recursive") else ""
        # FIX-v4.33: 服务端对"文件本就不存在"返回 ok=true + already_missing=true。
        #   此前客户端一律打印"✅ 已删除", AI 无法区分"我删掉了"和"本来就不存在" ——
        #   排查误删或确认幂等时会误判。现在显式标注 (语义仍是幂等成功, rc 不变)。
        if r.get("already_missing"):
            _missed.append(rp)
            # FIX-json-rm-v4.39: json 模式人类行走 stderr, stdout 保持单行 JSON (顶层 merge 契约)
            print(f"✓ 目标本就不存在 (幂等跳过, 未做变更): {rp}",
                  file=sys.stderr if "--json" in sys.argv else None)
        else:
            print(f"✅ 已删除: {rp}{extra}",
                  file=sys.stderr if "--json" in sys.argv else None)
    if _fail:
        if "--json" in sys.argv:
            print(json.dumps({"command": "rm", "ok": False, "deleted": len(rps) - _fail,
                              "failed": _fail, "targets": rps}, ensure_ascii=False))
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "rm", "ok": True, "deleted": len(rps) - len(_missed),
                          "already_missing": _missed, "targets": rps,
                          "recursive": bool(recursive)}, ensure_ascii=False))
    return 0

def cmd_mv(conf: dict, log: Logger, api: ApiClient, from_path: str, to_path: str) -> int:
    """重命名/移动远程文件。"""
    rp_from = _resolve_remote_path(conf, from_path)
    rp_to = _resolve_remote_path(conf, to_path)
    if not _confirm_danger("重命名/移动远程文件", f"{rp_from} → {rp_to} (目标已存在将被覆盖)", target=rp_from):
        return 99  # v4.29: 阻断退出码对齐契约
    r = api.rename_remote(rp_from, rp_to)
    if not r.get("ok"):
        # 🆕 FIX-rm-json-v4.16: --json 模式输出结构化结果
        if "--json" in sys.argv:
            print(json.dumps({"command": "mv", "ok": False, "moved": False,
                              "from": rp_from, "to": rp_to, "error": r.get("error", "")}, ensure_ascii=False))
        else:
            print(f"❌ 重命名失败: {r.get('error', '')}")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "mv", "ok": True, "moved": True, "from": rp_from, "to": rp_to}, ensure_ascii=False))
    else:
        print(f"✅ {rp_from} → {rp_to}")
    return 0

def cmd_cp(conf: dict, log: Logger, api: ApiClient, from_path: str, to_path: str, force: bool = False) -> int:
    """复制远程文件。force=True 时覆盖已存在目标 (覆盖前服务器自动备份)。"""
    rp_from = _resolve_remote_path(conf, from_path)
    rp_to = _resolve_remote_path(conf, to_path)
    if not _confirm_danger("复制远程文件", f"{rp_from} → {rp_to} (目标已存在将被覆盖)", target=rp_from):
        return 99  # v4.29: 阻断退出码对齐契约
    r = api.copy_remote(rp_from, rp_to, overwrite=force)
    if not r.get("ok"):
        # 🆕 FIX-cp-json-v4.17: --json 模式输出结构化结果 (此前成功/失败均纯文本, AI json.loads 失败)
        if "--json" in sys.argv:
            print(json.dumps({"command": "cp", "ok": False, "copied": False,
                              "from": rp_from, "to": rp_to, "error": r.get("error", "")}, ensure_ascii=False))
        else:
            print(f"❌ 复制失败: {r.get('error', '')}")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "cp", "ok": True, "copied": True, "from": rp_from, "to": rp_to}, ensure_ascii=False))
    else:
        print(f"✅ {rp_from} → {rp_to}")
    return 0

_ASYNC_WRAPPER = r"""#!/bin/sh
# ai-sync async runner (由客户端 upload API 写入, 不经过 shell 引号/转义)
# D 用纯参数展开求目录 (不依赖外部 dirname: 精简系统可能没有该命令)
case $0 in
  */*) __ai_d=${0%/*} ;;
  *) __ai_d=. ;;
esac
D=$(cd "$__ai_d" 2>/dev/null && pwd) || exit 97
# 与同步 exec 一致: 命令在 WORK_DIR 下执行 (D 位于 WORK_DIR/ai-sync/tmp/<id>)
# FIX-cwd-v4.39: D/../.. 只到 WORK_DIR/ai-sync (少一层 ..), 导致 exec 默认 cwd 错位,
#   与文件命令虚拟根 (--ls / == WORK_DIR) 不一致; 补为 D/../../.. 对齐注释语义
W=$(cd "$D/../../.." 2>/dev/null && pwd) || W="$D"
# 立即记录 pid: 启动早期失败 (cwd 错误/目录不可用) 也要让客户端能判定进程状态, 避免 WAIT 空轮询
echo "$$" > "$D/pid"
# --cwd 支持 (FIX-cwd-v4.39): __AI_CD__ 非空时覆盖默认目录 (占位符为纯标记, 不写 $ 前缀,
#   避免 replace 后残留 $ 被 shell 当字面量; 相对路径以 WORK_DIR 为基准, 与服务端原生 cwd 一致);
#   目录不存在直接报错退出, 并写 rc=97 让客户端 DONE 立即取回错误
if [ -n "__AI_CD__" ]; then
  case "__AI_CD__" in
    /*) __ai_cdp="__AI_CD__" ;;
    *)  __ai_cdp="$W/__AI_CD__" ;;
  esac
  [ -d "$__ai_cdp" ] || { echo "cwd_not_found: __AI_CD__" >&2; echo 97 > "$D/rc"; exit 97; }
  cd "$__ai_cdp" || exit 97
else
  cd "$W" || exit 97
fi
: > "$D/out"
: > "$D/err"
# FIX-env-v4.39: 对齐 sshd 会话环境 — USER/LOGNAME 由登录进程(pam/login)注入, 直接执行
#   bash 脚本时为空; git/make/crontab 等工具依赖该变量。按当前用户补导出 (envfile 不覆盖,
#   会话式工具如需自定义可后续 export 覆盖)
export USER=$(id -un 2>/dev/null) 2>/dev/null || true
export LOGNAME=$USER
T=__TIMEOUT__
# 子进程清单: /proc children (Linux/Android) → pgrep -P (macOS/BusyBox) → ps+awk (最后兜底)
# 不依赖 setsid/进程组: 精简系统上进程组常不可用, 且 kill 负 pid 在部分 shell 不支持。
__ai_kids() {
  __ak_p=$1
  if [ -r "/proc/$__ak_p/task/$__ak_p/children" ]; then
    cat "/proc/$__ak_p/task/$__ak_p/children" 2>/dev/null
  elif command -v pgrep >/dev/null 2>&1; then
    pgrep -P "$__ak_p" 2>/dev/null
  elif command -v awk >/dev/null 2>&1; then
    ps -A -o pid,ppid 2>/dev/null | awk -v p="$__ak_p" '$2==p {print $1}'
  fi
}
# 先杀父再杀子: 只杀父时子进程(如 sleep)会被 reparent 后继续跑,
# 且父 shell 可能继续执行后续命令(如 `sleep 30; echo done` 把 done 也跑出来)。
__ai_kill_tree() {
  __ak_p=$1; __ak_s=$2
  __ak_k=$(__ai_kids "$__ak_p")
  kill -"$__ak_s" "$__ak_p" 2>/dev/null
  for __ak_c in $__ak_k; do __ai_kill_tree "$__ak_c" "$__ak_s"; done
}
# 只杀后代 (收尾看门狗用, 绝不误杀命令本身)
__ai_kill_kids() {
  __ak_p=$1; __ak_s=$2
  __ak_k=$(__ai_kids "$__ak_p")
  for __ak_c in $__ak_k; do __ai_kill_tree "$__ak_c" "$__ak_s"; done
}
run() {
  if [ "$T" -gt 0 ] 2>/dev/null; then
    # 统一走纯 POSIX sh 看门狗: 外部 timeout 的 exit code/信号分发各系统不一致
    # (BusyBox 不支持 -k, GNU timeout 被杀后内层 EXIT trap 会把 rc 记成 0),
    # 这里到点 TERM 整棵树, 2 秒后 KILL 收尾, 用标记文件把 rc 归一成 124。
    rm -f "$D/timed_out" "$D/done"
    sh "$D/cmd.sh" &
    CPID=$!
    (
      __ai_i=0
      while [ "$__ai_i" -lt "$T" ] && [ ! -f "$D/done" ]; do
        sleep 1
        __ai_i=$((__ai_i+1))
      done
      if [ ! -f "$D/done" ]; then
        : > "$D/timed_out"
        __ai_kill_tree "$CPID" TERM
        sleep 2
        __ai_kill_tree "$CPID" KILL
      fi
    ) >/dev/null 2>&1 </dev/null &
    WDOG=$!
    wait "$CPID" 2>/dev/null; RC=$?
    : > "$D/done"
    __ai_kill_kids "$WDOG" TERM
    wait "$WDOG" 2>/dev/null
    if [ -f "$D/timed_out" ]; then return 124; fi
    return $RC
  fi
  sh "$D/cmd.sh"
}
run > "$D/out" 2> "$D/err"
rc=$?
pwd > "$D/cwd" 2>/dev/null
printf '%s\n' "$rc" > "$D/rc.tmp" 2>/dev/null && mv "$D/rc.tmp" "$D/rc" 2>/dev/null
exit "$rc"
"""


def _exec_cwd_safe(args) -> str:
    """FIX-cwd-v4.39: 取 --cwd 并做 shell 注入安全校验 (值会注入 wrapper 的 sh 脚本)。

    返回净化后的 cwd (空串 = 未指定); 非法返回 None (调用方应直接退出)。
    仅拒绝 shell 元字符, 允许中文等 UTF-8 字节 (POSIX sh 按字节处理路径)。"""
    import re as _re
    _cwd = str(getattr(args, "cwd", None) or "").strip()
    if not _cwd:
        return ""
    if "\x00" in _cwd or "\n" in _cwd or "\r" in _cwd:
        print("❌ --cwd 不能包含 NUL/换行", file=sys.stderr)
        return None
    if _re.search(r"[$\"'\`\\;|&<>()*?\[\]{}~!]", _cwd):
        print(f"❌ --cwd 含 shell 特殊字符: {_cwd!r} (仅允许 字母数字/._-/空格)", file=sys.stderr)
        return None
    return _cwd


def _cmd_exec_async(conf: dict, log: Logger, api: ApiClient, cmd: str, args) -> int:
    """--async / exec 默认路径: 后台执行 + 轮询, 绕过服务端单次请求超时.

    与旧实现的区别 (每一条都是实际踩过的坑):
      - 命令与 wrapper 通过 upload API 落盘: 不再依赖 base64 -d / setsid / /tmp,
        BusyBox / Android / 无 base64 的精简系统都能跑; 也不再受命令行长度限制.
      - 轮询以 rc 文件为权威完成信号 (不再是 kill -0): 容器里 init 不回收僵尸时
        kill -0 对僵尸恒为真, 旧实现会一直轮询到 600s 才放弃.
      - stdout/stderr/rc 用 download API 原样取回: 不再用 cat + "---STDOUT---" 切分,
        不会丢尾随换行 / 多出前导换行.
      - --timeout 真正生效 (远端有 timeout 命令时); 没有 timeout 命令会如实提示.
      - 完成后清理远端临时目录; 轮询超时保留现场并给出取回命令.
    """
    _eb = _exec_block_check(cmd)
    if _eb and _eb[0] == "BLOCKED":
        print(_eb[1], file=sys.stderr)
        return 99
    # FIX-cwd-v4.39: cwd 安全校验 (非法直接退出, 不进入远端)
    _cwd = _exec_cwd_safe(args)
    if _cwd is None:
        return 2
    import time as _time
    import uuid as _uuid
    _t0 = _time.time()
    _id = "aisync_%d_%s" % (int(_t0 * 1000), _uuid.uuid4().hex[:8])
    _dir = "ai-sync/tmp/" + _id
    _rcf = _dir + "/rc"
    _outf = _dir + "/out"
    _errf = _dir + "/err"
    try:
        _to = int(getattr(args, "timeout", None) or 0)
    except Exception:
        _to = 0
    if _to < 0:
        _to = 0

    # 1) 上传 wrapper + 命令脚本 (纯数据通道, 零转义)
    # FIX-cwd-v4.39: cwd 注入 wrapper (__AI_CD__ 占位符), 与服务端原生 cwd 语义一致 (相对 WORK_DIR)
    try:
        _wr = _ASYNC_WRAPPER.replace("__TIMEOUT__", str(_to)).replace("__AI_CD__", _cwd or "")
        _u = api.upload_file(_dir + "/run.sh",
                             _wr.encode("utf-8"),
                             mode="0700")
        if not _u.get("ok"):
            print(f"❌ --async 提交失败(写 wrapper): {_u.get('error') or _u.get('stderr', '')}", file=sys.stderr)
            return 3
        _u = api.upload_file(_dir + "/cmd.sh", cmd.encode("utf-8", "surrogateescape"), mode="0600")
        if not _u.get("ok"):
            print(f"❌ --async 提交失败(写命令): {_u.get('error') or _u.get('stderr', '')}", file=sys.stderr)
            return 3
    except Exception as e:
        print(f"❌ --async 提交失败: {e}", file=sys.stderr)
        return 3

    # 2) 后台启动 (setsid → nohup → 普通 &, 逐级降级)
    _submit = (
        "D='" + _dir + "'; "
        "rm -f \"$D/rc\" \"$D/out\" \"$D/err\" \"$D/pid\" \"$D/boot.out\" \"$D/boot.err\"; "
        "if command -v setsid >/dev/null 2>&1; then DET=setsid; "
        "elif command -v nohup >/dev/null 2>&1; then DET=nohup; else DET=; fi; "
        "($DET sh \"$D/run.sh\" > \"$D/boot.out\" 2> \"$D/boot.err\" & echo $! > \"$D/launcher\"); "
        "cat \"$D/launcher\" 2>/dev/null"
    )
    try:
        _r = api.exec_cmd(_submit, timeout=15)
    except Exception as e:
        print(f"❌ --async 提交失败: {e}", file=sys.stderr)
        return 3
    if not _r.get("ok"):
        # exec 被禁用/IP 不允许等情况: 直接透传服务端错误
        print(f"❌ --async 提交失败: {_r.get('error') or _r.get('stderr', '')}", file=sys.stderr)
        return 3

    # 3) 轮询: rc 文件优先; pid 只用于区分「还在跑」和「死了没留 rc」
    _limit = int(getattr(args, "async_timeout", None) or 600)
    _deadline = _t0 + _limit
    _poll = (
        "D='" + _dir + "'; "
        "if [ -s \"$D/rc\" ]; then echo DONE; "
        "elif [ -s \"$D/pid\" ]; then P=$(cat \"$D/pid\" 2>/dev/null); "
        "if [ -n \"$P\" ] && kill -0 \"$P\" 2>/dev/null; then echo RUN; else echo GONE; fi; "
        "else echo WAIT; fi"
    )
    _interval = 0.4
    _gone = False
    _wait_since = None  # FIX-cwd-v4.39: WAIT 兜底 — wrapper 启动早期失败(语法错/pid未写)时防无限轮询
    while True:
        _time.sleep(_interval)
        _interval = min(3.0, _interval * 1.5)
        if _time.time() > _deadline:
            if "--json" in sys.argv:
                print(json.dumps({"command": "exec", "async": True, "ok": False,
                                  "error": "async_poll_timeout", "poll_timeout": _limit,
                                  "remote_dir": _dir, "remote_rc": None,
                                  "hint": f"任务可能仍在运行: --exec 'cat {_dir}/out' 查看; "
                                          f"调大 --async-timeout 可延长本地轮询"}, ensure_ascii=False))
            else:
                print(f"⚠ --async 轮询超时({_limit}s): 任务仍在后台运行, 远端目录 {_dir}", file=sys.stderr)
                print(f"   查看输出: --exec 'cat {_dir}/out'", file=sys.stderr)
                print(f"   远端清理: --exec 'rm -rf {_dir}'", file=sys.stderr)
            return 3
        try:
            _q = api.exec_cmd(_poll, timeout=10)
        except Exception:
            continue  # 网络瞬断: 截止时间前继续重试
        _s = _q.get("stdout") or ""
        if "DONE" in _s:
            break
        if "GONE" in _s:
            # pid 已退出但 rc 还没出现: 给 trap/文件系统一点时间, 再确认一次
            _time.sleep(0.5)
            try:
                _q2 = api.exec_cmd(_poll, timeout=10)
            except Exception:
                _q2 = {}
            if "DONE" not in (_q2.get("stdout") or ""):
                _gone = True
                break
        elif "WAIT" in _s:
            # FIX-cwd-v4.39: pid 一直未出现 → wrapper 启动早期即失败 (如 sh 语法错误,
            #   cwd 失败未写 pid 的旧版本); 超过 6s 不再空等, 交给 boot.err 兜底
            if _wait_since is None:
                _wait_since = _time.time()
            elif _time.time() - _wait_since > 6:
                _gone = True
                break
        else:
            _wait_since = None

    # 4) 原样取回 out/err/rc (download 是二进制安全通道)
    def _dl(path):
        try:
            _d = api.download_file(path)
        except Exception:
            return None
        if not _d.get("ok"):
            return None
        _b = _d.get("content_b64") or ""
        if not _b:
            return b""
        try:
            return base64.b64decode(_b)
        except Exception:
            return None

    _rc_b = _dl(_rcf)
    _rc = None
    if _rc_b:
        _m = re.search(rb"-?\d+", _rc_b.strip())
        if _m:
            try:
                _rc = int(_m.group(0))
            except Exception:
                _rc = None
    _out_b = _dl(_outf) or b""
    _err_b = _dl(_errf) or b""
    if not _err_b:
        _boot_b = _dl(_dir + "/boot.err") or b""
        if _boot_b.strip():
            _err_b = _boot_b  # 启动阶段失败(无 sh/脚本不可读)时, 这是唯一线索
    if _rc is None and _gone and not _err_b:
        _err_b = "错误: 后台进程已退出但没有写回 rc (可能被 SIGKILL/OOM 终止)".encode("utf-8")
    _timed_out = (_rc == 124)
    _out_s = _out_b.decode("utf-8", "replace")
    _err_s = _err_b.decode("utf-8", "replace")
    # FIX-cwd-v4.39: wrapper 已把实际 cwd 写入 D/cwd (含 --cwd 覆盖后的目录), 取回用于输出标注
    _cwd_b = _dl(_dir + "/cwd") or b""
    _cwd_s = _cwd_b.decode("utf-8", "replace").strip()

    if "--json" in sys.argv:
        print(json.dumps({"command": "exec", "async": True, "ok": _rc == 0,
                          "remote_rc": _rc, "stdout": _out_s, "stderr": _err_s,
                          "timed_out": _timed_out,
                          "timeout": _to or None,
                          "cwd": _cwd_s, "remote_dir": _dir,
                          "elapsed_ms": int((_time.time() - _t0) * 1000)},
                         ensure_ascii=False))
    else:
        try:
            sys.stdout.buffer.write(_out_b)
            sys.stdout.flush()
        except Exception:
            sys.stdout.write(_out_s)
        if _err_s:
            try:
                sys.stderr.buffer.write(_err_b)
                sys.stderr.flush()
            except Exception:
                sys.stderr.write(_err_s)
        # FIX-cwd-v4.39: 输出标注实际 cwd + 远端临时目录 (AI 无需猜测执行位置)
        print(f"[exec] rc={_rc} (async) cwd={_cwd_s or '(none)'} dir={_dir}", file=sys.stderr)
        if _timed_out:
            print(f"[exec] TIMEOUT: 命令超过 --timeout {_to}s 被终止", file=sys.stderr)

    # 5) 清理 (轮询超时的现场在上面的分支里保留)
    # FIX-cleanup-v4.39: 原用 exec rm -rf 会被自身毁灭性命令黑名单拦截 → 每次 exec 都残留 tmp;
    #   改用文件通道 delete_remote (虚拟根内递归删除, 不经 exec 黑名单)
    try:
        api.delete_remote(_dir, recursive=True)
    except Exception:
        pass
    return 0 if _rc == 0 else 3


def cmd_exec(conf: dict, log: Logger, api: ApiClient, cmd: str, timeout: int = 30, cwd: str = "") -> int:
    """🆕 在服务器执行 shell 命令 (AI 实用增强)。

    服务端 action_exec 早已就绪 (sync_api.php → tools/exec.php), 但客户端此前无 CLI 入口,
    AI 跨 scope 部署/清 opcache/查日志/跑 build 都得写 Python 内联 import ApiClient。
    本函数补 CLI 入口: --exec "CMD" 一条命令搞定。

    输出契约 (AI 友好):
      - stdout: 命令真实 stdout, 原样输出 (可管道/重定向, 如 --exec "cat x" > local.txt)
      - stderr: 元信息 [exec] rc=X | TIMEOUT | truncated | stderr=... | ⚠ warning
      - 退出码: rc==0 → 0; rc==-1 但有 stdout (PHP proc_get_status 固有限制) → 0; 否则 → 3
      - --quiet: 抑制 warning 元信息 (FIX-quiet-v4.6), 错误与 rc/TIMEOUT/truncated 仍保留
      - FIX-cwd-v4.39: cwd 透传服务端原生参数, 元信息打印实际执行目录
    """
    # [AI-SAFE v1] 毁灭性命令黑名单 (硬拦截, 先于任何执行)
    _eb = _exec_block_check(cmd)
    if _eb:
        _eb_level, _eb_msg = _eb
        if "--json" in sys.argv:
            # [AI-SAFE v1.1.2] 结构化拦截输出 (exit 99 契约不变): AI 可直接解析 category/rule_id/target
            _eb_meta = _safety_meta(_eb_level, _eb_msg, cmd=cmd)
            print(json.dumps({"command": "exec", "blocked": True, "level": _eb_level,
                              "message": _eb_msg, "category": _eb_meta["category"],
                              "rule_id": _eb_meta["rule_id"], "target": _eb_meta["target"]},
                             ensure_ascii=False))
        else:
            print(_eb_msg, file=sys.stderr)
        if _eb_level == "BLOCKED":
            # 99=策略阻断 (README 退出码契约): AI 可区分「被防护拦截」与「一般错误(1)」, 不再盲目重试
            return 99
    quiet = bool(getattr(sys, "_AI_SYNC_QUIET", False))
    # 🆕 FIX-b64hint-v4.6: 检测命令中疑似"本地 shell 已吃掉一层转义"的危险字符,
    #   提示 AI 可改用 --exec-b64 规避嵌套引号/转义问题 (仅提示, 不打断执行)。
    #   触发条件: 命令含 $()/反引号/反斜杠 或 引号嵌套 (这些在本地 shell 解析后语义易变)。
    if not quiet:
        _b64_hint = None
        # 🆕 FIX-timeout-hint-v4.7: 命令长度预检 (服务器 ~30s 硬边界, 长命令可能被中断)
        if len(cmd) > 300:
            print(f"⚠ 命令较长({len(cmd)}字符), 服务器 ~30s 硬边界; 若执行超时中断建议拆分 <25s 或 --exec-file", file=sys.stderr)
        if "$(" in cmd or "`" in cmd or "\\" in cmd:
            _b64_hint = r"命令含 \$()/反引号/反斜杠, 本地 shell 可能已吃掉一层转义"
        elif (cmd.count('"') >= 2 and cmd.count("'") >= 2):
            _b64_hint = "单双引号嵌套, 建议 --exec-b64 传 base64 规避转义"
        if _b64_hint:
            print(f"⚠ {_b64_hint}; 若命令行为异常, 改用 --exec-b64", file=sys.stderr)
    r = api.exec_cmd(cmd, timeout=timeout, cwd=cwd)
    if not r.get("ok"):
        print(f"❌ exec 失败: {r.get('error', '')} (code={r.get('code', '')})", file=sys.stderr)
        if r.get("code") == 400:
            print("  若命令执行超 ~30s 被服务端中断: 拆分 <25s 执行, 或用 --exec-file 跑脚本", file=sys.stderr)
        return 2
    out = r.get("stdout", "") or ""
    err = r.get("stderr", "") or ""
    if out:
        sys.stdout.write(out); sys.stdout.flush()
    rc = r.get("return_code")
    meta = []
    # FIX-cwd-v4.39: 输出标注实际执行目录 (服务端原生 cwd 返回)
    _cwd_out = r.get("cwd") or ""
    if _cwd_out:
        meta.append(f"cwd={_cwd_out}")
    if rc is not None:
        meta.append(f"rc={rc}")
    if r.get("timed_out"):
        meta.append("TIMEOUT")
    if r.get("output_truncated"):
        meta.append("truncated")
        # 🆕 FIX-tip-v4.6: 显示服务端 tip (如 "输出超过上限已截断, 请重定向到文件后 download"),
        #   此前客户端只打 truncated 标记, AI 不知道如何拿完整输出。
        _tip = r.get("tip")
        if _tip:
            meta.append("tip=" + str(_tip)[:200])
    if err:
        meta.append(f"stderr={err[:500]!r}")
    if r.get("warning") and not quiet:
        _w = str(r["warning"])
        # 🆕 FIX-hide-exec-ip-warning: EXEC_ALLOW_IPS 空配置是常态提示 (token 已鉴权 + 个人工具),
        #   客户端直接屏蔽该条刷屏警告, 其余 warning 正常显示
        if "EXEC_ALLOW_IPS is empty" not in _w:
            meta.append("⚠ " + _w[:200])
    if meta:
        print(f"[exec] {' | '.join(meta)}", file=sys.stderr)
    if rc == 0:
        return 0
    # 🆕 FIX-rc-unknown: PHP proc_get_status 固有限制 — 进程结束后多次调用 exitcode 返回 -1,
    #   proc_close 兜底也不完美。-1 = "无法获取退出码" 而非 "失败"。
    #   故 rc=-1 或 None 一律视为成功 (0); AI 靠 stdout 内容判断命令实际结果 (如 grep "OK")。
    #   之前 rc=-1 且无 stdout 时误返回 3 (rmdir/echo 等无输出命令被误报失败)。
    if rc in (-1, None):
        # 🆕 FIX-rctail-v4.6: rc=-1 (退出码不可知) 时, 若 stdout 为空, 输出明确提示,
        #   避免 AI 无法判断"命令是否真的执行了" (无输出命令如 rm/echo 曾因此误判)。
        if not (out or "").strip():
            print("[exec] rc=-1(unknown) 且无 stdout — 无法确认命令是否执行成功, "
                  "建议用 echo OK 显式确认", file=sys.stderr)
        return 0
    return 3  # rc > 0: 明确失败

# ---------------------------------------------------------------------------
# v4.17 增强命令: check-update / health / preflight / self-update
#   服务器端代码基准: /mnt/data/wwwroot/default/sync/server/ (sync.py 分发版 + sync_api.php API 层)
# ---------------------------------------------------------------------------
def cmd_check_update(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    """🆕 v4.17-check-update: 检测本地客户端与服务器分发版版本一致性。
    基准: 服务器 /mnt/data/wwwroot/default/sync/server/sync.py (客户端分发版) + sync_api.php (API 层)。"""
    import hashlib
    lp = Path(__file__).resolve()
    local_md5 = hashlib.md5(lp.read_bytes()).hexdigest()
    local_lines = sum(1 for _ in open(lp, encoding="utf-8", errors="replace"))
    # FIX-AI-portable-checkupdate-v4.39.3: 旧实现硬编码了另一套部署的路径
    # (/mnt/data/wwwroot/default/sync/server/), 跨设备部署时取不到 md5 -> 把
    # "无法判定"错误编码成 "版本不一致", 且与 selfcheck 的 MD5 结论矛盾。
    import shlex
    _wd = ""
    try:
        _wd = str(api.whoami().get("work_dir") or "").rstrip("/\\")
    except Exception:
        pass
    _srv = (_wd + "/server") if _wd else "server"  # exec 的 cwd 就是 work_dir, 相对路径跨设备可用
    _py, _api = _srv + "/sync.py", _srv + "/sync_api.php"
    r = api.exec_cmd(
        "md5sum " + shlex.quote(_py) + " " + shlex.quote(_api) +
        " 2>/dev/null; wc -l < " + shlex.quote(_py) +
        " 2>/dev/null; wc -l < " + shlex.quote(_api) + " 2>/dev/null",
        timeout=15)
    out = (r.get("stdout") or "").strip().splitlines()
    srv_py_md5 = srv_api_md5 = srv_py_lines = srv_api_lines = ""
    for i, ln in enumerate(out):
        toks = ln.split()
        if i == 0: srv_py_md5 = toks[0] if toks else ""
        elif i == 1: srv_api_md5 = toks[0] if toks else ""
        elif i == 2: srv_py_lines = ln.strip()
        elif i == 3: srv_api_lines = ln.strip()
    py_same = bool(srv_py_md5 and srv_py_md5 == local_md5)
    _status = "ok" if srv_py_md5 else "unavailable"
    if "--json" in sys.argv:
        print(json.dumps({
            "command": "check-update",
            "local": {"md5": local_md5, "lines": local_lines},
            "server": {"sync_py_md5": srv_py_md5, "sync_py_lines": srv_py_lines,
                       "sync_api_md5": srv_api_md5, "sync_api_lines": srv_api_lines},
            "sync_py_consistent": py_same if srv_py_md5 else None,
            "status": _status,
            "result": _status,
        }, ensure_ascii=False))
    else:
        print(f"本地客户端  : md5={local_md5} lines={local_lines}")
        print(f"服务器 sync.py: md5={srv_py_md5} lines={srv_py_lines} "
              f"{'✅ 一致' if py_same else ('⚠ 无法判定 (unavailable)' if not srv_py_md5 else '⚠ 不一致 (需发布)')}")
        print(f"服务器 sync_api.php: md5={srv_api_md5} lines={srv_api_lines}")
    return 0

def cmd_health(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    """服务器健康摘要 (磁盘/内存/负载/进程数)。

    FIX-AI-portable-health-v4.39.3: 旧实现写死 `df -h /`, 在 Android/Termux、
    容器、WSL 等环境里 / 常是只读系统分区(恒 100%), 会让 AI 误判磁盘告急;
    且采集失败静默留空、result 恒 ok。改为取 work_dir 所在挂载点 + 结构化
    字段 + unavailable 标记 + 阈值结论; 原有字段全部保留以兼容既有消费者。
    """
    import shlex
    import re as _re
    _wd = ""
    try:
        _wd = str(api.whoami().get("work_dir") or "").rstrip("/\\")
    except Exception:
        pass
    _target = shlex.quote(_wd) if _wd else "."
    # load/进程数多路径兜底: Toybox 下 cat /proc/loadavg 常为空、ps aux 不支持
    _cmd = (f"df -h {_target} 2>/dev/null | tail -n1; echo __SEP__; "
            f"free -m 2>/dev/null | head -n2 | tail -n1; echo __SEP__; "
            f"(cat /proc/loadavg 2>/dev/null | head -n1; uptime 2>/dev/null | head -n1); "
            f"echo __SEP__; (ps -e 2>/dev/null || ps 2>/dev/null) | wc -l")
    r = api.exec_cmd(_cmd, timeout=15)
    parts = (r.get("stdout") or "").split("__SEP__")
    disk = parts[0].strip() if len(parts) > 0 else ""
    mem = parts[1].strip() if len(parts) > 1 else ""
    load = " | ".join([x.strip() for x in
                       (parts[2].strip() if len(parts) > 2 else "").splitlines()
                       if x.strip()])
    proc = parts[3].strip() if len(parts) > 3 else ""
    _pct_m = _re.search(r"(\d+)%", disk)
    disk_pct = int(_pct_m.group(1)) if _pct_m else None
    unavailable = [k for k, v in (("disk", disk), ("memory", mem),
                                  ("load", load), ("processes", proc)) if not v]
    # 只有能解析出百分比时才下结论, 避免把"读不到"误报成"健康"
    _health = ("unknown" if disk_pct is None else
               "degraded" if disk_pct >= 90 else "ok")
    _payload = {"command": "health", "disk": disk, "memory": mem,
                "load": load, "processes": proc, "disk_percent": disk_pct,
                "unavailable": unavailable, "health": _health,
                "result": "ok"}
    if "--json" in sys.argv:
        print(json.dumps(_payload, ensure_ascii=False))
    else:
        print(f"磁盘: {disk}\n内存: {mem}\n负载: {load}\n进程数: {proc}")
        print(f"health={_health} disk_percent={disk_pct} "
              f"unavailable={','.join(unavailable) or '-'} ok=true")
    return 0

def cmd_preflight(conf: dict, log: Logger, api: ApiClient, files: list, plain: bool = False) -> int:
    """🆕 v4.17-preflight: 批量上线前检查 — lint_batch(服务器语法) + 三方 MD5 一致性 + 备份就绪。
    files: 本地相对 watch_dir 的路径列表。"""
    watch = Path(conf["watch_dir"])
    rels = []
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            p = watch / p
        if not p.is_file():
            if "--json" in sys.argv:
                print(json.dumps({"command": "preflight", "ok": False, "error": f"文件不存在: {f}"}, ensure_ascii=False))
            else:
                print(f"❌ 文件不存在: {f}", file=sys.stderr)
            return 2
        try:
            rel = str(p.relative_to(watch)).replace("\\", "/")
        except ValueError:
            rel = p.name
        rels.append({"local": str(p), "rel": rel})
    # 1) 服务器 lint_batch
    lb = api.lint_batch([x["rel"] for x in rels])
    lb_ok = bool(lb.get("ok"))
    # 2) 远程 MD5 一致性 (checksum 比对)
    rmap = {}
    cs = api.checksum_remote(conf.get("remote_base") or ".", "md5")
    if cs.get("ok"):
        for it in cs.get("items", []):
            rmap[it.get("path", "")] = it.get("md5", "")
    files_state = []
    all_ok = lb_ok
    for x in rels:
        import hashlib as _h
        local_md5 = _h.md5(Path(x["local"]).read_bytes()).hexdigest()
        rp = x["rel"]
        remote_md5 = rmap.get(rp, "")
        same = bool(remote_md5 and remote_md5.lower() == local_md5.lower())
        files_state.append({"path": rp, "local_md5": local_md5,
                            "remote_md5": remote_md5 or "(无)",
                            "consistent": same, "exists_remote": bool(remote_md5)})
        if not same:
            all_ok = False
    if "--json" in sys.argv:
        print(json.dumps({"command": "preflight", "ok": all_ok, "lint_ok": lb_ok,
                          "files": files_state, "result": "ok" if all_ok else "warn"}, ensure_ascii=False))
    else:
        print(f"lint_batch: {'✅ 通过' if lb_ok else '⚠ 有错误'}")
        for f in files_state:
            print(f"  {f['path']:<40} {'✅ 一致' if f['consistent'] else '⚠ 差异/缺失'}"
                  f" (local={f['local_md5'][:8]} remote={f['remote_md5'][:8] if len(f['remote_md5'])>8 else f['remote_md5']})")
    return 0

def cmd_self_update(conf: dict, log: Logger, api: ApiClient, dry_run: bool = False, plain: bool = False) -> int:
    """🆕 v4.39.2 统一升级通道: 与启动时自动升级同一链路 (client_download, read 权限, 无需 admin).
    仅关闭节流强制检查: 本地 md5 与服务端参考 sync.py 不一致 → 下载→md5→py_compile→备份→原子覆盖.
    旧 v4.17 client_package/admin 路径已废弃 (服务端接口保留兼容, 客户端不再依赖 admin)."""
    local = _file_md5(__file__)
    try:
        w = api.whoami()
    except Exception as e:
        if "--json" in sys.argv:
            print(json.dumps({"command": "self-update", "ok": False, "error": f"whoami: {e}"}, ensure_ascii=False))
        else:
            print(f"❌ 无法连接服务端: {e}", file=sys.stderr)
        return 2
    server = str(w.get("client_md5") or "")
    if not server:
        msg = "服务端无 client_md5 字段 (旧服务端), 无法自升级; 请先升级服务端 sync_api.php"
        if "--json" in sys.argv:
            print(json.dumps({"command": "self-update", "ok": False, "error": "old_server", "message": msg}, ensure_ascii=False))
        else:
            print(f"⚠ {msg}", file=sys.stderr)
        return 2
    if server == local:
        msg = f"已是最新: 本地 {local[:12]} = 服务端 {server[:12]}"
        if "--json" in sys.argv:
            print(json.dumps({"command": "self-update", "ok": True, "already_latest": True,
                              "local_md5": local[:12], "server_md5": server[:12]}, ensure_ascii=False))
        else:
            print(f"✅ {msg}")
        return 0
    if dry_run:
        msg = f"[dry-run] 将升级本地 {local[:12]} → 服务端 {server[:12]}"
        if "--json" in sys.argv:
            print(json.dumps({"command": "self-update", "ok": True, "dry_run": True,
                              "local_md5": local[:12], "server_md5": server[:12]}, ensure_ascii=False))
        else:
            print(f"ℹ {msg}")
        return 0
    up = _auto_upgrade(conf, log, api, force=True)
    if up:
        if "--json" in sys.argv:
            print(json.dumps({"command": "self-update", "ok": True, "upgraded": True,
                              "local_md5": local[:12], "server_md5": server[:12]}, ensure_ascii=False))
        else:
            print(f"✅ sync.py 已升级 {local[:12]} → {server[:12]} (下次命令生效)")
        return 0
    # _auto_upgrade 失败时已打印原因到 stderr
    if "--json" in sys.argv:
        print(json.dumps({"command": "self-update", "ok": False, "error": "upgrade_failed",
                          "local_md5": local[:12], "server_md5": server[:12]}, ensure_ascii=False))
    else:
        print("❌ 升级失败 (原因见上)", file=sys.stderr)
    return 3

def cmd_shell(conf: dict, log: Logger, api: ApiClient, args) -> int:
    """🆕 持久 shell 工具 (对齐原生 ssh, 面向 AI 易用性)。

    会话内 cwd/export 跨调用保持; 后台任务可 --shell-job 查询 / --shell-kill 终止。
    --shell 默认复用本地状态文件(.ai_sync_shell_session)里的会话, 让 AI 像本地 shell 一样连续操作。
    """
    import os as _os
    state_file = _os.path.join(conf.get("watch_dir", "."), ".ai_sync_shell_session")
    # FIX-consistency-v4.36: 本地持久化 会话命名映射 + 环境变量 (弥补服务端 name 不落盘 / env 需独立设置)
    name_file = _os.path.join(conf.get("watch_dir", "."), ".ai_sync_shell_names")
    env_file = _os.path.join(conf.get("watch_dir", "."), ".ai_sync_shell_env")

    def _load_json_map(path):
        try:
            with open(path) as f:
                return json.loads(f.read() or "{}")
        except Exception:
            return {}

    def _save_json_map(path, m):
        try:
            with open(path, "w") as f:
                f.write(json.dumps(m, ensure_ascii=False))
        except Exception:
            pass

    def _load_sid():
        try:
            with open(state_file) as f:
                s = f.read().strip()
            return s or None
        except Exception:
            return None

    def _save_sid(sid):
        try:
            with open(state_file, "w") as f:
                f.write(sid or "")
        except Exception:
            pass

    # v4.36 防呆提醒 —— 服务端把提示放进响应里还不够, 客户端不打印 AI 就看不见。
    # 一律走 stderr: stdout 是命令本身的输出, 要留给 AI 解析, 不能被提示串味。
    def _emit_guard_warnings(resp):
        for w in (resp.get("warnings") or []):
            if "--json" in sys.argv:
                continue
            print("⚠️  [防呆] %s" % (w.get("message") or ""), file=sys.stderr)
            if w.get("hint"):
                print("    → %s" % w["hint"], file=sys.stderr)

    def _emit_blocked(resp):
        """被 T1 拦下时把替代方案原样转达, 否则 AI 只看到"不行"然后卡住。"""
        if "--json" in sys.argv:
            return
        print("❌ [防呆拦截] %s" % (resp.get("message") or ""), file=sys.stderr)
        alts = resp.get("alternatives") or []
        if alts:
            for i, a in enumerate(alts, 1):
                print("    %d. %s" % (i, a), file=sys.stderr)
        elif resp.get("fix_hint"):
            print("    → %s" % resp["fix_hint"], file=sys.stderr)

    # --shell-open: 开新会话并设为默认
    if getattr(args, "shell_open", None) is not None:
        r = api.shell_call("shell_open", name=(args.shell_open or None))
        if not r.get("ok"):
            print("❌ shell_open 失败: %s (code=%s)" % (r.get("error", ""), r.get("code", "")), file=sys.stderr)
            return 3
        sid = r["session_id"]; _save_sid(sid)
        # FIX-consistency-v4.36: 服务端不落盘 name, 客户端本地记录 name→sid, --shell-list 可显示
        _nm = (args.shell_open or "").strip()
        if _nm:
            _names = _load_json_map(name_file)
            _names[sid] = _nm
            _save_json_map(name_file, _names)
        if "--json" in sys.argv:
            print(json.dumps({"action": "shell_open", "session_id": sid, "name": _nm, "cwd": r.get("cwd"), "reused": r.get("reused")}, ensure_ascii=False))
        else:
            print("🔓 会话已开启 session_id=%s cwd=%s%s" % (sid, r.get("cwd"), " (复用已有)" if r.get("reused") else ""))
        return 0

    # --shell-close
    if getattr(args, "shell_close", None) is not None:
        # 不带 SID 时关闭本地默认会话 —— AI 不必为了收尾去记 session_id
        sid = args.shell_close or _load_sid()
        if not sid:
            print("❌ 没有可关闭的默认会话: 先 --shell-open, 或显式 --shell-close <SID>", file=sys.stderr)
            return 3
        r = api.shell_call("shell_close", session_id=sid)
        if not r.get("ok"):
            if "--json" in sys.argv:
                print(json.dumps({"action": "shell_close", "ok": False, "code": r.get("code"),
                                  "session_id": sid, "error": r.get("error", "")}, ensure_ascii=False))
            else:
                print("❌ shell_close 失败: %s" % r.get("error", ""), file=sys.stderr)
            return 3
        if _load_sid() == sid:
            _save_sid("")
        if "--json" in sys.argv:
            print(json.dumps({"action": "shell_close", "ok": True, "code": 0,
                              "session_id": sid, "closed": r.get("closed"),
                              "already_closed": r.get("already_closed")}, ensure_ascii=False))
        else:
            print("🔒 会话已关闭 session_id=%s" % sid)
        return 0

    # --shell-list
    if getattr(args, "shell_list", False):
        r = api.shell_call("shell_list")
        if not r.get("ok"):
            print("❌ shell_list 失败: %s" % r.get("error", ""), file=sys.stderr)
            return 3
        if "--json" in sys.argv:
            print(json.dumps(r, ensure_ascii=False))
        else:
            _names = _load_json_map(name_file)
            _rev = {v: k for k, v in _names.items()}
            print("会话(%d):" % r.get("session_count", 0))
            for s in r.get("sessions", []):
                _nm = s.get("name", "") or _rev.get(s["session_id"], "")
                print("  - %s  cwd=%s  idle=%ds  name=%s" % (s["session_id"], s["cwd"], s.get("idle_s", 0), _nm))
            print("后台任务(%d):" % r.get("job_count", 0))
            for j in r.get("jobs", []):
                print("  - %s  [%s] pid=%s  %s" % (j["job_id"], j["status"], j["pid"], j.get("cmd_preview", "")))
        return 0

    # --shell-job
    if getattr(args, "shell_job", None):
        r = api.shell_call("shell_job", job_id=args.shell_job, lines=(args.lines or None))
        if not r.get("ok"):
            print("❌ shell_job 失败: %s" % r.get("error", ""), file=sys.stderr)
            return 3
        if "--json" in sys.argv:
            print(json.dumps(r, ensure_ascii=False))
        else:
            print(r.get("output", ""), end="")
            print("[shell-job] status=%s rc=%s" % (r.get("status"), r.get("return_code")), file=sys.stderr)
        return 0

    # --shell-kill
    if getattr(args, "shell_kill", None):
        r = api.shell_call("shell_kill", job_id=args.shell_kill)
        if not r.get("ok"):
            if "--json" in sys.argv:
                print(json.dumps({"action": "shell_kill", "ok": False, "code": r.get("code"),
                                  "job_id": args.shell_kill, "error": r.get("error", "")}, ensure_ascii=False))
            else:
                print("❌ shell_kill 失败: %s" % r.get("error", ""), file=sys.stderr)
            return 3
        if "--json" in sys.argv:
            print(json.dumps({"action": "shell_kill", "ok": True, "code": 0,
                              "job_id": r.get("job_id") or args.shell_kill,
                              "killed": r.get("killed"), "return_code": r.get("return_code"),
                              "already_finished": r.get("already_finished"),
                              "note": r.get("note")}, ensure_ascii=False))
        else:
            if r.get("already_finished"):
                print("ℹ 后台任务早已结束 job_id=%s (未执行终止动作)" % args.shell_kill)
            else:
                print("🛑 已终止后台任务 job_id=%s" % args.shell_kill)
        return 0

    # --shell / --shell-bg: 需要会话
    shell_cmd = getattr(args, "shell", None)
    shell_bg = getattr(args, "shell_bg", None)
    cmd = shell_cmd if shell_cmd is not None else shell_bg
    # FIX-consistency-v4.36: --shell-env 可独立使用 — 无 shell 命令时=纯设置,
    # 持久化到本地 .ai_sync_shell_env, 后续 --shell 自动注入 (不再强制与 --shell 同次调用)
    if cmd is None and getattr(args, "shell_env", None):
        _envs = _load_json_map(env_file)
        for kv in args.shell_env:
            if "=" in kv:
                k, v = kv.split("=", 1)
                _envs[k] = v
        _save_json_map(env_file, _envs)
        if "--json" in sys.argv:
            print(json.dumps({"action": "shell_env", "ok": True, "code": 0,
                              "set": sorted(_envs.keys()), "persisted": True}, ensure_ascii=False))
        else:
            print("✅ 环境变量已持久化 (下次 --shell 自动注入): %s"
                  % ", ".join("%s=%s" % (k, _envs[k]) for k in sorted(_envs)))
        return 0
    if cmd is None:
        return 0
    if cmd == "__STDIN__":
        cmd = sys.stdin.read()
    sid = getattr(args, "shell_session", None) or _load_sid()
    if not sid:
        r = api.shell_call("shell_open")
        if not r.get("ok"):
            print("❌ 自动开会话失败: %s" % r.get("error", ""), file=sys.stderr)
            return 3
        sid = r["session_id"]; _save_sid(sid)
    # FIX-consistency-v4.36: 会话 env = 持久化 env (独立 --shell-env 写入) + 本次参数 env (参数覆盖)
    env = _load_json_map(env_file) or None
    if getattr(args, "shell_env", None):
        if env is None:
            env = {}
        for kv in args.shell_env:
            if "=" in kv:
                k, v = kv.split("=", 1)
                env[k] = v
    background = shell_bg is not None
    # FIX-consistency-v4.36: shell rm 危险路径客户端预检 (与 --exec/_rm_path_guard 对称)
    # 补缺口: 服务端 T1 只拦 rm -rf / (精确根), rm -rf /etc 等系统根目录自身仅警告放行,
    # 与 exec 的客户端防护不一致。此处客户端先行拦截, 不发请求。
    _rm_block = _shell_rm_scan(cmd)
    if _rm_block:
        if "--json" in sys.argv:
            print(json.dumps({"action": "shell", "ok": False, "code": 99,
                              "error": "rm_guard_blocked", "message": _rm_block,
                              "fix_hint": "被 rm 危险路径保护拦截: 不要删除系统根目录自身(rm -rf /etc 等); 确认是业务目录子树后重试"}, ensure_ascii=False))
        else:
            print("❌ " + _rm_block, file=sys.stderr)
        return 99
    r = api.shell_call("shell_run", session_id=sid, cmd=cmd, cwd=(args.cwd or None),
                       env=env, background=background or None, timeout=getattr(args, "timeout", None))
    if not r.get("ok"):
        if "--json" in sys.argv:
            # 拒绝执行时在 JSON 里带上替代方案, AI 解析后直接照着改, 不必再来一次
            print(json.dumps({"action": "shell", "ok": False, "code": r.get("code"),
                              "error": r.get("error"), "message": r.get("message"),
                              "alternatives": r.get("alternatives") or []}, ensure_ascii=False))
        elif r.get("error") == "destructive_blocked":
            _emit_blocked(r)          # 拒绝执行时必须给出路, 而不是只说"不行"
        else:
            print("❌ shell_run 失败: %s (code=%s)" % (r.get("error", ""), r.get("code", "")), file=sys.stderr)
        if r.get("code") in (1011,):
            _save_sid("")
        return 3
    _emit_guard_warnings(r)
    if background:
        if "--json" in sys.argv:
            print(json.dumps({"action": "shell_bg", "ok": True, "code": 0,
                              "job_id": r.get("job_id"), "session_id": sid,
                              "cwd": r.get("cwd"), "timeout": r.get("timeout"),
                              "timeout_enforced": r.get("timeout_enforced"),
                              "warnings": r.get("warnings") or []}, ensure_ascii=False))
        else:
            print("⏯ 后台任务已提交 job_id=%s (用 --shell-job %s 查询输出)" % (r.get("job_id"), r.get("job_id")))
        return 0
    if "--json" in sys.argv:
            print(json.dumps({"action": "shell", "session_id": sid, "return_code": r.get("return_code"),
                              "stdout": r.get("stdout"), "stderr": r.get("stderr"), "cwd": r.get("cwd"),
                              "timed_out": r.get("timed_out"), "output_truncated": r.get("output_truncated"),
                              "warnings": r.get("warnings") or []}, ensure_ascii=False))
    else:
        out = r.get("stdout", "")
        if out:
            print(out, end="")
        if r.get("stderr"):
            print(r.get("stderr"), file=sys.stderr, end="")
        rc = r.get("return_code", 0)
        print("[shell] rc=%s cwd=%s%s" % (rc, r.get("cwd"), " TIMEOUT" if r.get("timed_out") else ""), file=sys.stderr)
    _save_sid(sid)
    return 0 if r.get("return_code", 0) == 0 else 3


# ---------------------------------------------------------------------------
# v4.17 增强命令: check-update / health / preflight / self-update
#   服务器端代码基准: /mnt/data/wwwroot/default/sync/server/ (sync.py 分发版 + sync_api.php API 层)
# ---------------------------------------------------------------------------


def cmd_todo(conf: dict, log: Logger, api: ApiClient, target: str = ".") -> int:
    """提取远程代码中的 TODO/FIXME/HACK 注释。"""
    rp = _resolve_remote_path(conf, target) if target != "." else (conf.get("remote_base") or ".")
    r = api.todo(rp)
    if not r.get("ok"):
        print(f"❌ 查询失败: {r.get('error', '')}", file=sys.stderr)
        return 2
    items = r.get("items", [])
    if "--json" in sys.argv:
        # 🆕 v4.12.5-JSON: todo 结构化 (AI 代码审查直接拿清单)
        print(json.dumps({"command": "todo", "path": rp, "total": r.get('total', len(items)),
                          "files_scanned": r.get('files_scanned', 0),
                          "by_tag": r.get('by_tag', {}),
                          "items": [{"path": it.get('path', '?'), "line": it.get('line', '?'),
                                     "tag": it.get('tag', 'TODO'), "text": it.get('text', '')[:120]}
                                    for it in items[:500]]}, ensure_ascii=False))
        return 0
    if not items:
        print(f"\n✅ 未发现 TODO/FIXME/HACK 注释 ({rp})")
        return 0
    by_tag = r.get("by_tag", {})
    print(f"\n📝 TODO/FIXME/HACK 注释 ({rp}): {r.get('total', len(items))} 条 (扫描 {r.get('files_scanned', 0)} 文件)\n")
    if by_tag:
        print(f"  统计: {', '.join(f'{k}={v}' for k, v in sorted(by_tag.items()))}\n")
    for item in items[:100]:
        print(f"  {item.get('path', '?')}:{item.get('line', '?')} [{item.get('tag', 'TODO')}] {item.get('text', '')[:80]}")
    if len(items) > 100:
        print(f"  ... (还有 {len(items) - 100} 条)")
    return 0

def cmd_read_range(conf: dict, log: Logger, api: ApiClient, target: str, start: int, end: int) -> int:
    """v4.15: --read-range 是 --cat --range 的别名 (输出形状 command="read-range" 保持兼容)."""
    return cmd_cat(conf, log, api, target, start=start, end=end, command_name="read-range")

def cmd_backup_list(conf: dict, log: Logger, api: ApiClient, target: str | None = None,
                    plain: bool = False) -> int:
    """列出远程服务器上的历史备份。target=None 列出整个 remote_base 下的备份。"""
    # P1-4 X3 fix: 正确把目标路径转成远程相对路径。
    #   旧: target=None → conf.remote_base (client-test); 指定文件 → _resolve_remote_path(conf, target)
    #   问题: _resolve_remote_path 会对"已在 watch_dir 下"的输入正确转换；但用户若传了已加前缀的路径
    #         也应该兼容；同时 backups=[] 时打印 rp 便于用户自行排查。
    if target in (None, "__ALL__"):
        rp = (conf.get("remote_base") or "").strip() or "."
    else:
        # 绝对路径(设备上的 /opt/... )也要能查: _server_rel 会先 ping 拿 work_dir 再换算,
        # _resolve_remote_path 只懂 WATCH_DIR 本地路径, 传 /opt/... 会得到 client-test/opt/... → 0 条备份
        rp = _server_rel(conf, api, target)
    log.info(f"查询远程备份: path={rp}")
    r = api.backup_ls(rp)
    if not r.get("ok"):
        print(f"❌ 查询备份失败: {r.get('error', '')} (remote={rp})")
        return 2
    # 兼容多种响应字段: backups (规范) / items (部分旧版接口)
    backups = r.get("backups") or r.get("items") or []
    if isinstance(backups, dict):
        backups = list(backups.values())
    if not backups:
        if "--json" in sys.argv:
            print(json.dumps({"command": "backup-list", "path": rp, "count": 0,
                              "backups": []}, ensure_ascii=False))
        elif plain:
            print(f"file={rp} count=0 ok=true")
        else:
            print(f"\nℹ {rp} 无历史备份（只列 ai-sync 自动备份; 手动 cp 的 .bak 不显示, 需自己指文件名；服务器 raw={json.dumps({k: v for k, v in r.items() if k not in ('ok','code')}, ensure_ascii=False)[:120]}）")
        _record_history(conf, "backup_list", [{"remote": rp, "count": 0}])
        return 0
    if "--json" in sys.argv:
        # 🆕 v4.11-JSON: 结构化 backups 数组 (AI 直接取 name/size/mtime 决定回滚)
        _json_baks = []
        for b in backups:
            if isinstance(b, str):
                _json_baks.append({"file": b, "orig": b.split(".bak.")[0] if ".bak." in b else b,
                                   "size": 0, "mtime": ""})
                continue
            name = (b.get("file") or b.get("backup_file") or b.get("backup") or b.get("name") or "?")
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b.get("mtime", 0))) if b.get("mtime") else ""
            _json_baks.append({"file": name,
                               "orig": name.split(".bak.")[0] if ".bak." in name else name,
                               "size": b.get("size", 0), "mtime": ts})
        print(json.dumps({"command": "backup-list", "path": rp, "count": len(_json_baks),
                          "backups": _json_baks}, ensure_ascii=False))
        _record_history(conf, "backup_list", [{"remote": rp, "count": len(backups)}])
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --backup-list --plain 单行键值 (AI 取 count/newest 即可)
        # 🆕 FIX-baklist-v4.6: 每份备份单独一行 backup= (AI 可拿到全部备份名, 回滚时选任意旧版)
        newest = ""
        for b in backups:
            if isinstance(b, str):
                if not newest: newest = b
                # 🆕 v4.8-BAKLIST: 从备份名反推原文件名 (.bak.时间戳_随机 之前的部分),
                #   AI 回滚时不用猜备份对应哪个文件.
                _orig = b.split(".bak.")[0] if ".bak." in b else b
                print(f"file={rp} orig={_orig} backup={b}")
                continue
            name = (b.get("file") or b.get("backup_file") or b.get("backup") or b.get("name") or "?")
            if not newest: newest = name
            _orig = name.split(".bak.")[0] if ".bak." in name else name
            print(f"file={rp} orig={_orig} backup={name}")
        print(f"file={rp} count={len(backups)} ok=true"
              + (f" newest={newest}" if newest else ""))
        _record_history(conf, "backup_list", [{"remote": rp, "count": len(backups)}])
        return 0
    print(f"\n📦 {rp} 的历史备份 ({len(backups)}):\n")
    for i, b in enumerate(backups):
        if isinstance(b, str):
            print(f"  [{i}] {b}")
            continue
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b.get("mtime", 0)))
        size = b.get("size", "-")
        size_str = f"{size}B" if isinstance(size, (int, float)) else str(size)
        # 兼容多种备份文件名 key
        name = (b.get("file") or b.get("backup_file") or b.get("backup") or b.get("name") or "?")
        print(f"  [{i}] {name:<48} {size_str:>10}  {ts}")
    print(f"\n恢复命令: --rollback <原文件> --backup <备份文件名>")
    _record_history(conf, "backup_list", [{"remote": rp, "count": len(backups)}])
    return 0

# ---------------------------------------------------------------------------
# 13b. v4.1 管理命令
# ---------------------------------------------------------------------------
def _admin_err(r: dict) -> int:
    """管理操作错误统一输出: 403 给出明确指引, 其余透传服务端错误。🆕 v4.12.5: 走 stderr"""
    if r.get("code") == 403 or "403" in str(r.get("error", "")):
        print("❌ 403: 当前 token 无 admin 权限 — 需在 sync_config.json 配置 admin_token 或使用已授权的 admin token", file=sys.stderr)
    else:
        print(f"❌ 失败: {r.get('error','')} {r.get('message','')}", file=sys.stderr)
    return 2

def cmd_admin(conf: dict, log: Logger, api: ApiClient, subcmd: str) -> int:
    """管理操作 (需 admin token)。subcmd: stats/config/scopes/tokens/audit/reload"""
    subcmd = (subcmd or "stats").lower()
    if subcmd in ("stats", "admin_stats"):
        r = api.admin_stats()
        if not r.get("ok"):
            return _admin_err(r)
        print("\n📊 服务器管理统计:\n")
        for sc in r.get("scopes", []):
            print(f"  [{sc['name']}] work_dir={sc['work_dir']}")
            print(f"      文件数={sc.get('file_count','?')}  磁盘占用={sc.get('size_mb','?')}MB  备份数={sc.get('backup_count','?')}")
        print(f"\n  Token 总数: {r.get('token_count','?')}")
        print(f"  Scope 总数: {r.get('scope_count','?')}")
        return 0
    if subcmd == "config":
        r = api.config_get()
        if not r.get("ok"):
            return _admin_err(r)
        print("\n⚙ 服务器配置 (脱敏):\n")
        print(json.dumps(r.get("config", r), indent=2, ensure_ascii=False))
        return 0
    if subcmd == "reload":
        r = api.config_reload()
        if not r.get("ok"):
            return _admin_err(r)
        print(f"✅ 配置已重载: {r.get('message','ok')}")
        return 0
    if subcmd == "scopes":
        r = api.scopes_list()
        if not r.get("ok"):
            return _admin_err(r)
        print("\n📁 已授权目录 (scopes):\n")
        for sc in r.get("scopes", []):
            print(f"  {sc['name']:<16} {sc.get('work_dir','?')}")
            if sc.get('file_count') is not None:
                print(f"  {'':16} 文件数={sc['file_count']}  备份数={sc.get('backup_count','?')}")
        return 0
    if subcmd == "tokens":
        r = api.tokens_list()
        if not r.get("ok"):
            return _admin_err(r)
        print("\n🔑 已授权 Token:\n")
        show_full = "--show-full-token" in sys.argv
        for tk in r.get("tokens", []):
            # BUG-29 修复 (审计 Finding 17): 完整 token 明文打印会泄露到日志/聊天记录.
            # AI 经常把命令输出贴到 transcript, 一个泄露的 token = 服务器被完全接管.
            # 默认只显示前 8 + 后 4 字符; --show-full-token 显式开启才显示完整.
            full = str(tk.get('token', ''))
            if show_full:
                disp = full
            else:
                disp = (full[:8] + "***" + full[-4:]) if len(full) > 12 else "***"
            print(f"  {disp:<24} {tk.get('name','?'):<16} scopes={tk.get('scopes',[])}  perms={tk.get('permissions',[])}")
        if r.get("admin_token_present"):
            print(f"\n  (admin_token 已配置)")
        if not show_full:
            print(f"\n  (token 已脱敏, 加 --show-full-token 显示完整)")
        return 0
    if subcmd == "audit":
        r = api.audit_log(lines=100)
        if not r.get("ok"):
            return _admin_err(r)
        print(f"\n📜 审计日志 (最近 {len(r.get('entries',[]))} 条):\n")
        for e in r.get("entries", [])[-30:]:
            print(f"  [{e.get('ts','')}] {e.get('action','?'):<12} code={e.get('code',0)}  {e.get('path','')}")
        return 0
    print(f"❌ 未知子命令: {subcmd}。可用: stats/config/scopes/tokens/audit/reload")
    return 2

def cmd_token_add(conf: dict, log: Logger, api: ApiClient,
                  token: str, name: str, scopes_str: str, perms_str: str) -> int:
    """添加 token。"""
    scopes = [s.strip() for s in scopes_str.split(",") if s.strip()]
    perms = [p.strip() for p in perms_str.split(",") if p.strip()]
    r = api.token_add(token, name, scopes, perms)
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "token-add", "ok": False, "error": r.get('error',''), "message": r.get('message',''), "fix_hint": "添加失败: 确认 token 唯一且名称可用 (--admin tokens 查现状)"}, ensure_ascii=False))
        else:
            print(f"❌ 失败: {r.get('error','')} {r.get('message','')}")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "token-add", "ok": True, "name": name, "scopes": scopes, "perms": perms}, ensure_ascii=False))
    else:
        print(f"✅ Token 已添加: {name} (scopes={scopes}, perms={perms})")
    return 0

def cmd_token_del(conf: dict, log: Logger, api: ApiClient, token: str) -> int:
    """删除 token。"""
    if not _confirm_danger("删除 Token", f"将删除 token: {token[:8]}*** (不可逆, 该 token 立即失效)", irreversible=True):
        return 99  # v4.29: 阻断退出码对齐契约
    r = api.token_delete(token)
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "token-del", "ok": False, "error": r.get('error',''), "message": r.get('message',''), "fix_hint": "删除失败: 确认 token 存在 (--admin tokens 查现状)"}, ensure_ascii=False))
        else:
            print(f"❌ 失败: {r.get('error','')} {r.get('message','')}")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "token-del", "ok": True, "name": token[:8]}, ensure_ascii=False))
    else:
        print(f"✅ Token 已删除: {token[:8]}***")
    return 0

def cmd_scope_add(conf: dict, log: Logger, api: ApiClient, args_list: list) -> int:
    """添加 scope: [name, work_dir, backup_keep=N, ...]。"""
    if len(args_list) < 2:
        print("❌ 用法: --scope-add <name> <work_dir> [backup_keep=N] [exec_enabled=true]")
        return 2
    name, work_dir = args_list[0], args_list[1]
    kwargs = {}
    for kv in args_list[2:]:
        if "=" in kv:
            k, v = kv.split("=", 1)
            if v.lower() in ("true","false"): kwargs[k] = v.lower() == "true"
            elif v.isdigit(): kwargs[k] = int(v)
            else: kwargs[k] = v
    r = api.scope_add(name, work_dir, **kwargs)
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "scope-add", "ok": False, "error": r.get('error',''), "message": r.get('message',''), "fix_hint": "添加失败: 确认 scope 名唯一且 work_dir 合法 (--admin scopes 查现状)"}, ensure_ascii=False))
        else:
            print(f"❌ 失败: {r.get('error','')} {r.get('message','')}")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "scope-add", "ok": True, "name": name, "work_dir": work_dir}, ensure_ascii=False))
    else:
        print(f"✅ Scope 已添加: {name} → {work_dir}")
    return 0

def cmd_scope_del(conf: dict, log: Logger, api: ApiClient, name: str) -> int:
    """删除 scope。"""
    if not _confirm_danger("删除 Scope", f"将删除 scope: {name} (不可逆, 关联目录授权失效)", irreversible=True):
        return 99  # v4.29: 阻断退出码对齐契约
    r = api.scope_delete(name)
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "scope-del", "ok": False, "error": r.get('error',''), "message": r.get('message',''), "fix_hint": "删除失败: 确认 scope 存在且无依赖 (--admin scopes 查现状)"}, ensure_ascii=False))
        else:
            print(f"❌ 失败: {r.get('error','')} {r.get('message','')}")
        return 2
    if "--json" in sys.argv:
        print(json.dumps({"command": "scope-del", "ok": True, "name": name}, ensure_ascii=False))
    else:
        print(f"✅ Scope 已删除: {name}")
    return 0

def cmd_package(conf: dict, log: Logger, api: ApiClient, args_list: list) -> int:
    """一键打包客户端: [target_token, scope?, output_dir?]。
    下载服务器生成的 zip (sync.py + sync.conf + README + start.sh), 保存到本地。"""
    if not args_list:
        print("用法: --package <target_token> [scope] [output_dir]")
        return 2
    target_token = args_list[0]
    scope = args_list[1] if len(args_list) > 1 else ""
    output_dir = args_list[2] if len(args_list) > 2 else "."
    r = api.client_package(target_token, scope)
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "package", "ok": False, "error": r.get('error',''), "message": r.get('message',''), "fix_hint": "打包失败: 确认 token 有效 (--whoami) 且 scope 存在 (--admin scopes)"}, ensure_ascii=False))
        else:
            print(f"❌ 失败: {r.get('error','')} {r.get('message','')}")
        return 2
    # 降级模式: 无 zip 时返回单文件
    if r.get("mode") == "single_file":
        out = Path(output_dir) / r.get("filename", "sync.conf")
        out.write_text(r.get("content", ""), encoding="utf-8")
        print(f"⚠ {r.get('message','')}")
        print(f"✅ 已保存: {out}")
        return 0
    # zip 模式
    import base64
    zip_data = base64.b64decode(r["zip_b64"])
    # 校验 sha256
    actual_sha = hashlib.sha256(zip_data).hexdigest()
    if actual_sha != r.get("sha256", ""):
        print(f"❌ SHA256 校验失败: 本地={actual_sha[:16]} 远程={r.get('sha256','')[:16]}")
        return 2
    out = Path(output_dir) / r["filename"]
    out.write_bytes(zip_data)
    print(f"\n📦 客户端包已生成:")
    print(f"  文件: {out}")
    print(f"  大小: {r['size']} 字节")
    print(f"  SHA256: {r['sha256']}")
    print(f"  Scope: {r.get('scope','')}  权限: {r.get('permissions',[])}")
    print(f"  API: {r.get('api_url','')}")
    print(f"  工作目录: {r.get('watch_dir','')}")
    print(f"\n使用方法:")
    print(f"  unzip {out.name} -d ai-sync-client/")
    print(f"  cd ai-sync-client/")
    print(f"  chmod +x start.sh && ./start.sh")
    print(f"\n或手动:")
    print(f"  python3 sync.py --conf sync.conf --status")
    print(f"  python3 sync.py --conf sync.conf --push <文件>")
    return 0

# ---------------------------------------------------------------------------
# 13c. v4.2 AI 增强命令
# ---------------------------------------------------------------------------
def _resolve_remote_path(conf: dict, local_path: str) -> str:
    """本地/远程输入统一解析为「服务端相对路径」(scope 根相对, 不带 remote_base 前缀).
    🆕 FIX-path-v4.6: 统一三形态输入语义:
      - TV/config.php (相对本地)   → TV/config.php
      - bxbb/TV/config.php (带前缀) → TV/config.php   (剥离前缀)
      - /sandboxdata/.../config.php (绝对本地) → TV/config.php
    服务端 lint/test/backup_ls/php_run 均只认 scope 根相对路径.

    FIX-symlink-path: 只解析 watch 根, 不 resolve() 用户路径的最后一级 (也不解析中间链接).
      旧实现 Path(...).resolve() 会把 `--rm link` / `--info link` / `--mv link ...`
      的目标重定向成链接指向的文件, 导致 AI 想删/改名链接本身却操作了目标内容;
      链接环还会直接抛 RuntimeError. 现在用纯词法 abspath, 链接始终作为独立对象寻址.
    """
    watch = Path(os.path.realpath(str(conf["watch_dir"])))
    remote_base = (conf.get("remote_base") or "").strip().strip("/")
    # 🆕 FIX-path-v4.6: scope 名也参与前缀剥离 (REMOTE_BASE 常为空, 但 SCOPE=bxbb 会被 AI 当路径前缀输入)
    scope_name = (conf.get("scope") or conf.get("SCOPE") or "").strip().strip("/")
    ls = (local_path or "").strip()
    if not ls:
        return "."
    # 带 remote_base 或 scope 名前缀的输入剥离前缀 (bxbb/TV/config.php → TV/config.php)
    for pre in (remote_base, scope_name):
        if pre and (ls == pre or ls.startswith(pre + "/")):
            ls = ls[len(pre):].lstrip("/")
            break
    try:
        p = Path(ls)
        if not p.is_absolute():
            p = watch / p
        rel = _rel_under_watch(p, watch)
        if rel is not None:
            return rel or "."
    except Exception:
        pass
    ts = ls
    while ts.startswith("./"): ts = ts[2:]
    return ts
def cmd_spider(conf: dict, log: Logger, api: ApiClient, url: str, method: str = "GET",
               headers: str = None, body: str = None, timeout: int = 15,
               maxlen: int = 200000, via_m3u8: bool = False,
               allow_private: bool = False, remote: bool = False, plain: bool = False) -> int:
    """🆕 抓包爬虫: 抓取任意 URL 内容给 AI 分析 (本机直连; m3u8 可走 M3U8dl 代理防盗链/CF绕过)"""
    import urllib.request, urllib.error, ssl, socket, time, json as _json
    url = (url or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        if "--json" in sys.argv:
            print(_json.dumps({"ok": False, "code": 2, "command": "spider", "error": "url 必须以 http(s):// 开头"}, ensure_ascii=False))
        else:
            print(f"❌ url 必须以 http(s):// 开头: {url}", file=sys.stderr)
        return 2
    # SSRF 防护: 默认拦截明确内网/本机地址 (--sp-allow-private 放行)
    try:
        _h = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        _h = ""
    _ip = _h
    try:
        _ip = socket.gethostbyname(_h)
    except Exception:
        pass
    _is_priv = _h in ("localhost", "127.0.0.1", "::1") or         _ip.startswith("10.") or _ip.startswith("192.168.") or         (_ip.startswith("172.") and len(_ip.split(".")) == 4 and 16 <= int(_ip.split(".")[1]) <= 31) or         _ip.startswith("127.") or _ip == "0.0.0.0"
    if _is_priv and not allow_private:
        if "--json" in sys.argv:
            print(_json.dumps({"ok": False, "code": 3, "command": "spider", "error": f"目标为内网/本机地址被拦截 ({_h}); 如确需抓取请加 --sp-allow-private"}, ensure_ascii=False))
        else:
            print(f"❌ 目标为内网/本机地址被拦截 ({_h}); 如确需抓取请加 --sp-allow-private", file=sys.stderr)
        return 3
    # 若走 M3U8dl 代理 (m3u8 防盗链/CF绕过, 不改代理文件)
    target = url
    if via_m3u8:
        # 开源版不内置任何私有服务地址：代理模板由配置 m3u8_proxy 提供，
        # 未配置时从 api_url 推导同源地址；两者皆无则明确报错，不静默连到第三方。
        _tpl = (conf.get("m3u8_proxy") or "").strip()
        if not _tpl:
            try:
                _pu = urllib.parse.urlparse(conf.get("api_url") or "")
                if _pu.hostname:
                    _origin = f"{_pu.scheme}://{_pu.hostname}" + (f":{_pu.port}" if _pu.port else "")
                    _tpl = _origin + "/bxbb/DL/M3U8dl.php?m3u8={url}"
            except Exception:
                pass
        if not _tpl:
            print("❌ --via-m3u8 需要配置 m3u8_proxy（含 {url} 占位符）或可用的 api_url", file=sys.stderr)
            return 2
        target = _tpl.replace("{url}", urllib.parse.quote(url, safe=""))
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    hdrs = {"User-Agent": "Mozilla/5.0 (ai-sync-spider/1.0)", "Accept": "*/*"}
    if headers:
        try:
            _hd = _json.loads(headers) if isinstance(headers, str) else headers
            if isinstance(_hd, dict):
                for _k, _v in _hd.items(): hdrs[str(_k)] = str(_v)
        except Exception:
            pass
    # 🆕 --sp-remote: 走服务器执行抓取 (ai-sync exec 通道, 服务器 python3 代抓, 结果回传; m3u8 目标走服务器 M3U8dl 代理)
    if remote:
        _api_url = (conf or {}).get("api_url") or ""
        _api_token = (conf or {}).get("api_token") or ""
        if not _api_url or not _api_token:
            print("❌ --sp-remote 需要 sync.conf 配置 API_URL/API_TOKEN (走服务器 exec 通道)", file=sys.stderr)
            return 2
        _srv = "import urllib.request,urllib.error,ssl,time,json,sys\n"
        _srv += "target=" + _json.dumps(target, ensure_ascii=False) + "\n"
        _srv += "method=" + _json.dumps(method.upper(), ensure_ascii=False) + "\n"
        _srv += "headers=" + _json.dumps(hdrs, ensure_ascii=False) + "\n"
        _srv += "body=" + _json.dumps((body or ""), ensure_ascii=False) + "\n"
        _srv += "timeout=" + str(int(timeout)) + "\n"
        _srv += "maxlen=" + str(int(maxlen)) + "\n"
        _srv += "ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE\n"
        _srv += "h={'User-Agent':'Mozilla/5.0 (ai-sync-spider/1.0)','Accept':'*/*'}\n"
        _srv += "if headers: h.update(headers)\n"
        _srv += "data=body.encode('utf-8') if body else None\n"
        _srv += "t0=time.time()\n"
        _srv += "try:\n"
        _srv += "    req=urllib.request.Request(target,data=data,headers=h,method=method)\n"
        _srv += "    with urllib.request.urlopen(req,timeout=timeout,context=ctx) as r:\n"
        _srv += "        raw=r.read();http=getattr(r,'status',r.getcode());ct=r.headers.get('Content-Type','')\n"
        _srv += "except urllib.error.HTTPError as e:\n"
        _srv += "    http=e.code;raw=b''\n"
        _srv += "    try: raw=e.read()\n"
        _srv += "    except Exception: pass\n"
        _srv += "    ct=e.headers.get('Content-Type','') if e.headers else ''\n"
        _srv += "except Exception as e:\n"
        _srv += "    print(json.dumps({'ok':False,'error':str(e),'ms':int((time.time()-t0)*1000)},ensure_ascii=False));sys.exit(0)\n"
        _srv += "ms=int((time.time()-t0)*1000)\n"
        _srv += "text=raw.decode('utf-8','replace');truncated=len(text)>maxlen\n"
        _srv += "print(json.dumps({'ok':200<=http<400,'http':http,'ct':ct,'len':len(raw),'ms':ms,'truncated':truncated,'body':text[:maxlen]},ensure_ascii=False))\n"
        _cmd = "python3 - <<'PY'\n" + _srv + "PY"
        try:
            _api_obj = ApiClient(_api_url, _api_token, timeout=max(30, int(timeout) + 20), log=Logger(None), verify_ssl=False, scope=(conf or {}).get("scope") or "")
            _r = _api_obj.exec_cmd(_cmd, timeout=max(30, int(timeout) + 20))
        except Exception as _e:
            print(f"❌ 服务器 exec 失败: {_e}", file=sys.stderr)
            return 3
        _sout = (_r.get("stdout") or "").strip()
        _parsed = None
        try: _parsed = _json.loads(_sout)
        except Exception: _parsed = None
        if _parsed is None:
            print(f"❌ 服务器返回异常 (rc={_r.get('return_code')}): {_r.get('stderr') or _sout or '无输出'}", file=sys.stderr)
            return 3
        if "--json" in sys.argv:
            print(_json.dumps({"ok": bool(_parsed.get("ok")), "code": 0 if _parsed.get("ok") else 3,
                               "command": "spider", "remote": True, "http": _parsed.get("http"),
                               "content_type": _parsed.get("ct"), "len": _parsed.get("len"), "ms": _parsed.get("ms"),
                               "truncated": bool(_parsed.get("truncated")), "body": _parsed.get("body", "")}, ensure_ascii=False))
            return 0 if _parsed.get("ok") else 3
        if plain:
            print(f"http={_parsed.get('http')} ct={_parsed.get('ct')} len={_parsed.get('len')} ms={_parsed.get('ms')} ok={str(bool(_parsed.get('ok'))).lower()} remote=true")
            return 0 if _parsed.get("ok") else 3
        print(f"HTTP {_parsed.get('http')} · {_parsed.get('ct') or '?'} · {_parsed.get('len')}B · {_parsed.get('ms')}ms · 服务器抓取")
        print("=" * 40)
        print(_parsed.get("body", ""))
        if _parsed.get("truncated"):
            print(f"\n... (已截断, 用 --sp-maxlen 增大, 当前 {maxlen})")
        return 0 if _parsed.get("ok") else 3

    data = None
    if method.upper() == "POST" and body:
        data = body.encode("utf-8") if isinstance(body, str) else body
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    t0 = time.time(); resp_headers = {}
    try:
        req = urllib.request.Request(target, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            http = getattr(resp, "status", resp.getcode())
            ct = resp.headers.get("Content-Type", "")
            resp_headers = {k: v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        http = e.code; raw = b""
        try: raw = e.read()
        except Exception: pass
        ct = e.headers.get("Content-Type", "") if e.headers else ""
        resp_headers = dict(e.headers.items()) if e.headers else {}
    except Exception as e:
        if "--json" in sys.argv:
            print(_json.dumps({"ok": False, "code": 3, "command": "spider", "error": f"抓取失败: {e}"}, ensure_ascii=False))
        else:
            print(f"❌ 抓取失败: {e}", file=sys.stderr)
        return 3
    ms = int((time.time() - t0) * 1000)
    text = raw.decode("utf-8", "replace")
    truncated = len(text) > maxlen
    body_out = text[:maxlen]
    ok = 200 <= http < 400
    out = {"ok": ok, "code": 0 if ok else 3, "command": "spider",
           "http": http, "content_type": ct, "len": len(raw), "ms": ms,
           "truncated": truncated, "via_m3u8": bool(via_m3u8)}
    if "--json" in sys.argv:
        out["body"] = body_out
        print(_json.dumps(out, ensure_ascii=False))
        return 0 if ok else 3
    if plain:
        print(f"http={http} ct={ct} len={len(raw)} ms={ms} ok={str(ok).lower()} via_m3u8={str(bool(via_m3u8)).lower()}")
        return 0 if ok else 3
    print(f"HTTP {http} · {ct or '?'} · {len(raw)}B · {ms}ms" + (" · M3U8dl代理" if via_m3u8 else ""))
    print("=" * 40)
    print(body_out)
    if truncated:
        print(f"\n... (已截断, 用 --sp-maxlen 增大, 当前 {maxlen})")
    return 0 if ok else 3


def cmd_http_check(conf: dict, log: Logger, api: ApiClient, url: str, expect: str = "",
                   timeout: int = 15, maxlen: int = 200000,
                   allow_private: bool = False, plain: bool = False) -> int:
    """🆕 端到端验证: 抓取 URL 并断言页面包含期望内容 (--http-check <url> [--expect <str>]).
    可独立使用(无需工作目录/部署), 也可作为 --deploy 配套 flag; SSRF 防护与 spider 一致."""
    import urllib.request, urllib.error, ssl, socket, time, json as _json
    url = (url or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        if "--json" in sys.argv:
            print(_json.dumps({"ok": False, "code": 2, "command": "http_check", "error": "url 必须以 http(s):// 开头"}, ensure_ascii=False))
        else:
            print(f"❌ url 必须以 http(s):// 开头: {url}", file=sys.stderr)
        return 2
    # SSRF 防护 (与 spider 一致): 默认拦截明确内网/本机地址 (--sp-allow-private 放行)
    try:
        _h = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        _h = ""
    _ip = _h
    try:
        _ip = socket.gethostbyname(_h)
    except Exception:
        pass
    _is_priv = _h in ("localhost", "127.0.0.1", "::1") or \
        _ip.startswith("10.") or _ip.startswith("192.168.") or \
        (_ip.startswith("172.") and len(_ip.split(".")) == 4 and 16 <= int(_ip.split(".")[1]) <= 31) or \
        _ip.startswith("127.") or _ip == "0.0.0.0"
    if _is_priv and not allow_private:
        if "--json" in sys.argv:
            print(_json.dumps({"ok": False, "code": 3, "command": "http_check", "error": f"目标为内网/本机地址被拦截 ({_h}); 如确需抓取请加 --sp-allow-private"}, ensure_ascii=False))
        else:
            print(f"❌ 目标为内网/本机地址被拦截 ({_h}); 如确需抓取请加 --sp-allow-private", file=sys.stderr)
        return 3
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    hdrs = {"User-Agent": "Mozilla/5.0 (ai-sync-httpcheck/1.0)", "Accept": "*/*"}
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            http = getattr(resp, "status", resp.getcode())
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        http = e.code; raw = b""
        try: raw = e.read()
        except Exception: pass
        ct = e.headers.get("Content-Type", "") if e.headers else ""
    except Exception as e:
        if "--json" in sys.argv:
            print(_json.dumps({"ok": False, "code": 3, "command": "http_check", "error": f"抓取失败: {e}"}, ensure_ascii=False))
        else:
            print(f"❌ 抓取失败: {e}", file=sys.stderr)
        return 3
    ms = int((time.time() - t0) * 1000)
    text = raw.decode("utf-8", "replace")[:maxlen]
    http_ok = 200 <= http < 400
    matched = (expect == "") or (expect in text)
    ok = http_ok and matched
    out = {"ok": ok, "code": 0 if ok else 3, "command": "http_check",
           "http": http, "content_type": ct, "len": len(raw), "ms": ms,
           "expect": expect, "matched": matched}
    if "--json" in sys.argv:
        out["body_preview"] = text[:500]
        print(_json.dumps(out, ensure_ascii=False))
        return 0 if ok else 3
    if plain:
        print(f"http={http} len={len(raw)} ms={ms} expect={expect!r} matched={str(matched).lower()} ok={str(ok).lower()}")
        return 0 if ok else 3
    print(f"{'✅' if ok else '❌'} HTTP 检查: {url} (http={http} · {ct or '?'} · {len(raw)}B · {ms}ms)")
    if expect:
        print(f"   断言 '{expect}': {'✅ 命中' if matched else '❌ 未命中'}")
    if not http_ok:
        print(f"   HTTP 状态异常: {http}")
    elif expect and not matched:
        print(f"   响应前 {maxlen} 字节内未找到期望内容")
    if text:
        print("=" * 40)
        print(text[:400])
        if len(text) > 400:
            print("... (body 预览已截断)")
    return 0 if ok else 3


def cmd_api_help(conf: dict, log: Logger, api: ApiClient, name: str = "") -> int:
    """服务端 API 自描述 (转发 action=help)。AI 无需外部文档即可知道每个 action 该传什么。"""
    payload = {"action": "help"}
    if name:
        payload["name"] = name
    r = api._req(payload)
    if not r.get("ok"):
        print(f"❌ {r.get('error', '')} {r.get('message', '')}", file=sys.stderr)
        if r.get("fix_hint"):
            print(f"💡 {r['fix_hint']}", file=sys.stderr)
        if r.get("suggestions"):
            print(f"   候选: {', '.join(r['suggestions'])}", file=sys.stderr)
        return 2
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False))
        return 0
    if name:
        print(f"\n📘 {r.get('action')} — {r.get('desc', '')}\n")
        print("  必填:")
        for x in (r.get("required") or ["(无)"]):
            print(f"    - {x}")
        if r.get("optional"):
            print("  可选:")
            for x in r["optional"]:
                print(f"    - {x}")
        print(f"  返回: {r.get('returns', '')}")
        ex = r.get("example") or {}
        if ex:
            print("  示例: " + json.dumps(ex, ensure_ascii=False))
        if r.get("errors"):
            print("  常见错误: " + "; ".join(r["errors"]))
        if r.get("see_also"):
            print("  相关: " + ", ".join(r["see_also"]))
        print()
        return 0
    print(f"\n📘 服务端 action 契约 (共 {r.get('total')} 个) — {r.get('usage', '')}\n")
    for it in r.get("actions", []):
        req = ("  必填: " + ", ".join(it.get("r") or [])) if it.get("r") else ""
        print(f"  {it.get('n'):<18} {it.get('d', '')}{req}")
    print("\n提示: --api-help <action> 看单个契约; --api-help --json 拿结构化数据\n")
    return 0


def cmd_whoami(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    """查询当前 token 权限边界"""
    r = api.whoami()
    if not r.get("ok"):
        print(f"❌ {r.get('error','')} {r.get('message','')}", file=sys.stderr)
        if r.get("fix_hint"): print(f"💡 {r['fix_hint']}", file=sys.stderr)
        return 2
    if "--json" in sys.argv:
        # 🆕 v4.12.3-JSON: whoami 结构化 (AI 权限确认)
        # 🆕 v4.12.7: 透出服务端能力协商 (capabilities/available_actions/server_time) — AI 无需试错
        print(json.dumps({"command": "whoami", "name": r.get('token_name', ''),
                          "scope": r.get('scope', ''), "is_admin": bool(r.get('is_admin')),
                          "permissions": r.get('permissions', []),
                          "available_scopes": r.get('available_scopes', []),
                          "exec_enabled": bool(r.get('exec_enabled')),
                          "max_upload_kb": r.get('max_size', 0) // 1024,
                          "work_dir": r.get('work_dir', ''), "api_version": r.get('api_version', ''),
                          "capabilities": r.get('capabilities', {}),
                          "available_actions": r.get('available_actions', []),
                          "server_time": r.get('server_time', 0),
                          "server_timezone": r.get('server_timezone', ''),
                          "rate_limit": r.get('rate_limit', {})},
                         ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --whoami --plain 单行键值 (AI 脚本断言用)
        print(f"name={r.get('token_name','')} scope={r.get('scope','')} admin={str(bool(r.get('is_admin'))).lower()} "
              f"perms={','.join(r.get('permissions',[])) or 'none'} scopes={','.join(r.get('available_scopes',[])) or 'none'} "
              f"exec={str(bool(r.get('exec_enabled'))).lower()} max_upload_kb={r.get('max_size',0)//1024} "
              f"work_dir={r.get('work_dir','')} api_version={r.get('api_version','')}")
        return 0
    print(f"🆔 身份信息")
    print(f"  Token名称: {r.get('token_name','')}")
    print(f"  当前Scope: {r.get('scope','')}")
    print(f"  是否管理员: {'是' if r.get('is_admin') else '否'}")
    print(f"  权限: {', '.join(r.get('permissions',[])) or '无'}")
    print(f"  可用Scope: {', '.join(r.get('available_scopes',[])) or '无'}")
    print(f"  exec启用: {'是' if r.get('exec_enabled') else '否'}")
    print(f"  最大上传: {r.get('max_size',0)//1024} KB (分片: {r.get('max_chunked_size',0)//1024//1024} MB)")
    rl = r.get('rate_limit',{})
    if rl:
        print(f"  频率限制: {rl.get('per_token_per_minute','?')}/分钟/token, {rl.get('per_ip_per_second','?')}/秒/ip")
    print(f"  工作目录: {r.get('work_dir','')}")
    print(f"  API版本: {r.get('api_version','')}")
    caps = r.get('capabilities', {})
    if caps:
        on = [k for k, v in caps.items() if v]
        print(f"  服务端能力: {', '.join(on) or '无'}")
    _http_be = "requests(keep-alive, 快)" if _load_requests() is not None else "urllib(慢, pip install requests 加速)"
    print(f"  HTTP后端: {_http_be}")
    print(f"💡 完整命令能力地图: --help-ai  |  单命令用法: --help-ai <命令>  |  常用工作流: --auto pull/save/fix", file=sys.stderr)
    return 0

def cmd_sync_check(conf: dict, log: Logger, api: ApiClient, files: list,
                   plain: bool = False) -> int:
    """一体化同步决策: 判断该不该推"""
    watch = Path(os.path.realpath(str(conf["watch_dir"])))
    payload_files = []
    for fp in files:
        # 🆕 FIX-path-v4.6: 相对路径先拼 watch_dir; 带 scope/remote_base 前缀的先剥离 (旧实现 resolve() 拼 cwd)
        lp = fp.strip()
        rb = conf.get("remote_base", "").strip().strip("/")
        sc = (conf.get("scope") or conf.get("SCOPE") or "").strip().strip("/")
        for pre in (rb, sc):
            if pre and (lp == pre or lp.startswith(pre + "/")):
                lp = lp[len(pre):].lstrip("/")
                break
        if not Path(lp).is_absolute():
            lp = str(watch / lp)
        # FIX-symlink-path: 词法路径 + lstat 判断. 旧实现 resolve() 后 is_file() 会跟随链接,
        #   导致 "本地是链接" 被当成目标文件内容, 链接自身的 md5(规范化目标) 永远对不上.
        p = Path(os.path.abspath(lp))
        is_link = os.path.islink(str(p))
        if not is_link and not p.is_file():
            print(f"⚠ 跳过不存在的文件: {fp}" if not plain else f"skip file={fp} ok=false missing=1")
            continue
        rel = _rel_under_watch(p, watch)
        if rel is None:
            remote_path = fp
        else:
            remote_base = conf.get("remote_base", "").strip("/")
            remote_path = (remote_base + "/" + rel) if remote_base else (rel or ".")
        if not _utf8_ok(remote_path):
            print(f"⚠ 跳过非 UTF-8 文件名: {_safe_text(remote_path)}"
                  if not plain else f"skip file={_safe_text(remote_path)} ok=false non_utf8_name=1")
            continue
        if is_link:
            lf = link_finger(p, watch)
            if lf["size"] < 0 or not lf.get("link_safe"):
                print(f"⚠ 跳过不安全的软链接: {fp} ({lf.get('link_reason') or 'unsafe'})"
                      if not plain else f"skip file={fp} ok=false unsafe_link=1")
                continue
            payload_files.append({
                "path": remote_path,
                "md5": lf["md5"],
                "size": 0,
                "mtime": int(lf["mtime"]),
                "type": "symlink",
            })
            continue
        data = p.read_bytes()
        st = p.stat()
        payload_files.append({
            "path": remote_path,
            "md5": hashlib.md5(data).hexdigest(),
            "size": len(data),
            "mtime": int(st.st_mtime),
        })
    if not payload_files:
        print("❌ 无有效文件" if not plain else "ok=false error=no_valid_files")
        return 2
    r = api.sync_check(payload_files)
    if not r.get("ok"):
        if plain:
            print(f"ok=false error={r.get('error','')} {r.get('message','')}")
        else:
            print(f"❌ {r.get('error','')} {r.get('message','')}")
            if r.get("fix_hint"): print(f"💡 {r['fix_hint']}")
        return 2
    if plain:
        # 🆕 FIX-plain-v4.6: --sync-check --plain 每文件单行 + 汇总 (AI 判断 push 目标集)
        reason_map = {"same":"same","remote_missing":"remote_missing","local_newer":"local_newer",
                      "remote_changed":"remote_changed","conflict":"conflict","size_diff":"size_diff","invalid_path":"invalid_path"}
        for item in r.get("results", []):
            path = item.get("path","")
            reason = item.get("reason","")
            should = "true" if item.get("should_push") else "false"
            conflict = "true" if item.get("conflict") else "false"
            print(f"path={path} reason={reason_map.get(reason,reason)} push={should} conflict={conflict}")
        print(f"total={r.get('total',0)} push={r.get('should_push_count',0)} conflict={r.get('conflict_count',0)}")
        return 0
    if "--json" in sys.argv:
        # 🆕 FIX-json-v4.17: --json 输出结构化决策 (对齐其他命令契约)
        print(json.dumps({"command": "sync-check", "ok": True, "total": r.get("total", 0),
                          "should_push": r.get("should_push_count", 0),
                          "conflict": r.get("conflict_count", 0),
                          "summary": r.get("summary", ""),
                          "results": [{"path": it.get("path", ""), "reason": it.get("reason", ""),
                                       "should_push": bool(it.get("should_push")),
                                       "conflict": bool(it.get("conflict")),
                                       "hint": it.get("hint", "")} for it in r.get("results", [])]},
                         ensure_ascii=False))
        return 0
    print(f"🔍 同步决策 ({r.get('total',0)} 个文件)")
    print(f"  {'文件':<40} {'状态':<16} {'推送':<6} 冲突")
    print(f"  {'-'*40} {'-'*16} {'-'*6} {'-'*4}")
    for item in r.get("results", []):
        path = item.get("path","")
        reason = item.get("reason","")
        should = "✅是" if item.get("should_push") else "⏭否"
        conflict = "⚠是" if item.get("conflict") else "否"
        reason_map = {"same":"内容一致","remote_missing":"远程缺失","local_newer":"本地较新",
                      "remote_changed":"远程已变","conflict":"冲突","size_diff":"大小不同","invalid_path":"路径非法"}
        reason_text = reason_map.get(reason, reason)
        print(f"  {path:<40} {reason_text:<16} {should:<6} {conflict}")
        if item.get("hint"): print(f"    💡 {item['hint']}")
    print(f"\n摘要: {r.get('summary','')}")
    print(f"  需推送: {r.get('should_push_count',0)}  冲突: {r.get('conflict_count',0)}")
    return 0

def cmd_lint_batch(conf: dict, log: Logger, api: ApiClient, files: list,
                   plain: bool = False) -> int:
    """批量语法检查. 🆕 FIX-js-lint-v4.6: JS 类文件本地 node --check (同 cmd_lint), 其余走服务端.
    🆕 FIX-lintdir-v4.6: 支持传目录参数 — 递归展开目录内可 lint 文件 (AI 改完整个子目录一次检查).
    通用设计: 不针对任何项目目录结构做特殊跳过; 拼接分片等特殊场景由 node 错误特征提示."""
    js_exts = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
    lint_exts = (".php", ".json") + js_exts
    watch = Path(conf["watch_dir"]).resolve()
    # ---- 目录参数展开 (递归收集可 lint 文件) ----
    expanded = []
    for f in files:
        try:
            p = Path(f)
            lp = p if p.is_absolute() else (watch / p)
            # FIX-symlink-path: 纯词法; 链接目录不展开 (避免递归到目标目录/链接环)
            lp = Path(os.path.abspath(str(lp)))
        except Exception:
            lp = None
        if lp and lp.is_dir() and not lp.is_symlink():
            for fp in sorted(lp.rglob("*")):
                if fp.is_file() and fp.suffix.lower() in lint_exts and ".bak" not in fp.name.lower() \
                   and not any(seg.startswith(".") for seg in fp.parts):
                    expanded.append(str(fp))
        else:
            expanded.append(f)
    # 🆕 FIX-lintdir-empty-v4.17: 目录内无可 lint 文件时, expanded 为空 → 旧逻辑 files = expanded or files
    #   把目录原样回退给服务端 lint → 报 "is_dir Path is a directory" (误导).
    #   改为: 只要出现过目录参数且 expanded 为空, 明确提示"目录内无 php/js/json 可检查文件", 不再回退目录.
    _had_dir_arg = any(
        (lambda p: (not p.is_symlink()) and p.is_dir())(
            Path(os.path.abspath(str(Path(f) if Path(f).is_absolute() else (watch / f)))))
        for f in files
    )
    if expanded:
        files = expanded
    elif _had_dir_arg:
        if "--json" in sys.argv:
            print(json.dumps({"command": "lint-batch", "ok": False, "error": "no_lintable_files_in_dir",
                              "hint": "目录内无 .php/.js/.mjs/.cjs/.jsx/.ts/.tsx 可检查文件"}, ensure_ascii=False))
        else:
            print("⚠ 目录内无 .php/.js/.json 可检查文件, 跳过", file=sys.stderr if not plain else None)
        return 0
    else:
        files = files
    local_results = {}   # rp → (ok, lang, detail)
    server_files = []    # 需要服务端 lint 的
    for f in files:
        rp = _resolve_remote_path(conf, f)
        lp = None
        try:
            p = Path(f)
            lp = p if p.is_absolute() else (watch / p)
            lp = Path(os.path.abspath(str(lp)))
        except Exception:
            lp = None
        if lp and not lp.is_symlink() and lp.is_file() and lp.suffix.lower() in js_exts:
            import subprocess
            try:
                rr = subprocess.run(["node", "--check", str(lp)],
                                    capture_output=True, text=True, timeout=30)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                rr = None
            if rr is not None:
                if rr.returncode == 0:
                    local_results[rp] = (True, "js", "ok")
                else:
                    err = ""
                    for ln in (rr.stderr or "").splitlines():
                        if ln.strip(): err = ln.strip(); break
                    # 🆕 FIX-hint2-v4.6: 通用拼接分片提示 (按错误特征, 不针对项目目录)
                    if "Unexpected end of input" in (rr.stderr or ""):
                        err += " (文件不完整/可能为拼接分片, 若是分片设计请检查拼接后的成品文件)"
                    local_results[rp] = (False, "js", err or "syntax error")
                continue
        server_files.append(rp)
    # 服务端批量
    r = {"ok": True, "total": len(files), "results": [], "failed": 0, "total_issues": 0, "all_ok": True}
    if server_files:
        r = api.lint_batch(server_files)
        # 语法不通过时服务端返回 ok=false/code=1014, 但 results/summary 完整; 继续渲染.
        if not r.get("ok") and r.get("results") is None:
            if plain:
                print(f"ok=false error={r.get('error','')} {r.get('message','')}")
            else:
                print(f"❌ {r.get('error','')} {r.get('message','')}")
                if r.get("fix_hint"): print(f"💡 {r['fix_hint']}")
            return 2
    # 合并本地 node 结果
    for rp, (ok, lang, detail) in local_results.items():
        if not ok:
            r["failed"] = r.get("failed", 0) + 1
            r["total_issues"] = r.get("total_issues", 0) + 1
            r["all_ok"] = False
        r["results"].append({"path": rp, "ok": ok, "language": lang,
                             "issue_count": 0 if ok else 1,
                             "first_error": None if ok else {"line": 0, "message": detail[:60]}})
    r["total"] = len(files)  # 🆕 FIX-js-lint-v4.6: 服务端 total 只含 server_files, 补上本地 node 数
    if plain:
        # 🆕 FIX-plain-v4.6: --lint-batch --plain 每文件单行 (AI 断言全绿)
        for item in r.get("results", []):
            if item.get("ok"):
                print(f"file={item.get('path')} lang={item.get('language','?')} ok=true errors=0")
            else:
                fe = item.get("first_error") or {}
                print(f"file={item.get('path')} lang={item.get('language','?')} ok=false "
                      f"line={fe.get('line',0)} error={fe.get('message','')[:120]}")
        print(f"total={r.get('total',0)} failed={r.get('failed',0)} issues={r.get('total_issues',0)} "
              f"result={'pass' if r.get('all_ok') else 'fail'}")
        return 0 if r.get("all_ok") else 1
    print(f"🔍 批量语法检查 ({r.get('total',0)} 个文件)")
    for item in r.get("results", []):
        path = item.get("path","")
        # 语法不通过时服务端 item.ok=false 但带 first_error/issue_count;
        # 必须按"已 lint 出结果"渲染, 否则终端只剩一个裸 ❌, 看不出错在哪一行.
        if item.get("ok") or item.get("first_error") or item.get("syntax_ok") is False:
            issues = item.get("issue_count",0)
            status = "✅ 无问题" if issues == 0 else f"⚠ {issues} 个问题"
            lang = item.get("language","")
            print(f"  {path:<40} [{lang}] {status}")
            if item.get("first_error"):
                fe = item["first_error"]
                print(f"    首个错误: 行{fe.get('line',0)} {fe.get('message','')[:60]}")
        else:
            print(f"  {path:<40} ❌ {item.get('error','')} {item.get('message','')[:40]}")
    print(f"\n摘要: 总{r.get('total',0)} 失败{r.get('failed',0)} 问题{r.get('total_issues',0)} 全过={'是' if r.get('all_ok') else '否'}")
    return 0 if r.get("all_ok") else 1

def cmd_recent_changes(conf: dict, log: Logger, api: ApiClient, lines: int) -> int:
    """服务器端变更历史"""
    r = api.recent_changes(lines)
    if not r.get("ok"):
        print(f"❌ {r.get('error','')} {r.get('message','')}"); return 2
    changes = r.get("changes", [])
    if "--brief" in sys.argv:
        # 🆕 FIX-brief-v4.17: 只输出结论字段 (count/最近操作), 不列条目
        _last = changes[0] if changes else {}
        if "--json" in sys.argv:
            print(json.dumps({"command": "recent-changes", "ok": True, "total": r.get("total", 0),
                              "summary": r.get("summary", ""),
                              "latest": {"ts": _last.get("ts", ""), "action": _last.get("action", ""),
                                         "path": _last.get("path", "")} if _last else None},
                             ensure_ascii=False))
        else:
            print(f"total={r.get('total',0)} latest={_last.get('action','')}@{_last.get('ts','')} ok=true")
        return 0
    if "--json" in sys.argv:
        # 🆕 FIX-json-v4.17: --json 输出结构化变更列表
        print(json.dumps({"command": "recent-changes", "ok": True, "total": r.get("total", 0),
                          "summary": r.get("summary", ""),
                          "changes": [{"ts": c.get("ts", ""), "action": c.get("action", ""),
                                       "ok": bool(c.get("ok")), "path": c.get("path", "")}
                                      for c in changes[:50]]}, ensure_ascii=False))
        return 0
    print(f"📜 服务器端最近变更 ({r.get('total',0)} 条)")
    print(f"  {'时间':<22} {'操作':<16} {'状态':<6} {'路径':<40}")
    print(f"  {'-'*22} {'-'*16} {'-'*6} {'-'*40}")
    for c in changes[:50]:
        ts = c.get("ts","")
        action = c.get("action","")
        status = "✅" if c.get("ok") else "❌"
        path = c.get("path","")[:40]
        print(f"  {ts:<22} {action:<16} {status:<6} {path}")
    if len(changes) > 50:
        print(f"  ... 还有 {len(changes)-50} 条")
    print(f"\n摘要: {r.get('summary','')}")
    return 0

# ---------------------------------------------------------------------------
# P1-A: error_log 命令行入口 (AI 排查白屏)
# ---------------------------------------------------------------------------
def cmd_error_log_list(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    r = api.error_log_list()
    if not r.get("ok"):
        if "--json" in sys.argv:
            print(json.dumps({"command": "error-log", "ok": False, "error": r.get("error", "")}, ensure_ascii=False))
        elif plain:
            print(f"ok=false error={r.get('error','')} {r.get('message','')}")
        else:
            print(f"❌ {r.get('error','')} {r.get('message','')}")
        return 2
    if "--json" in sys.argv:
        # 🆕 FIX-json-v4.17: 日志列表 --json 结构化
        print(json.dumps({"command": "error-log", "ok": True, "total": r.get('total', len(r.get('logs', []))),
                          "logs": [{"name": c.get("name", "?"), "path": c.get("path", ""),
                                    "size": c.get("size", 0), "format": c.get("format", "?"),
                                    "mtime": c.get("mtime", 0)} for c in r.get("logs", [])]},
                         ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --error-log --plain 列表每行 name|path|size (AI 选日志名直接传给 --error-log)
        for c in r.get("logs", []):
            print(f"name={c.get('name','?')} path={c.get('path','')} size={c.get('size',0)} format={c.get('format','?')}")
        print(f"total={r.get('total', len(r.get('logs',[])))}")
        return 0
    print(f"发现 {r.get('total', len(r.get('logs',[])))} 个可用日志文件:")
    print(f"{'#':>3} {'name':<14} {'size':>8} {'format':<14} mtime                  path")
    print("-" * 120)
    for i, c in enumerate(r.get("logs", []), 1):
        from datetime import datetime
        mt = datetime.fromtimestamp(c.get("mtime") or 0).strftime("%Y-%m-%d %H:%M:%S") if c.get("mtime") else "—"
        size = c.get("size", 0)
        size_s = f"{size//1024}KB" if size > 2000 else f"{size}B"
        print(f"{i:>3} {c.get('name','?'):<14} {size_s:>8} {c.get('format','?'):<14} {mt} {c.get('path','')}")
    return 0

def cmd_error_log(conf: dict, log: Logger, api: ApiClient, name: str, *,
                  path: str = "", lines: int = 100, grep: str = "", regex: bool = False,
                  since_ts: int = 0, before_ts: int = 0, plain: bool = False) -> int:
    r = api.error_log(name=name, path=path, lines=lines, grep=grep, regex=regex,
                      since_ts=since_ts, before_ts=before_ts)
    if not r.get("ok"):
        # 🆕 v4.12-FIX: 错误一律走 stderr (对齐 stats/lint/cat, AI 统一从 stderr 取错误)
        if plain:
            print(f"name={name} ok=false error={r.get('error','')} {r.get('message','')}", file=sys.stderr)
            if r.get("fix_hint"): print(f"hint={r['fix_hint']}", file=sys.stderr)
        else:
            print(f"❌ {r.get('error','')} {r.get('message','')}", file=sys.stderr)
            if r.get("fix_hint"): print(f"💡 {r['fix_hint']}", file=sys.stderr)
            if r.get("available"):
                print("可用日志:", file=sys.stderr)
                for a in r["available"]: print(f"  - {a}", file=sys.stderr)
        return 2
    fmt = r.get("format") or "text"
    if "--brief" in sys.argv:
        # 🆕 FIX-brief-v4.17: 只输出 line_count + 前 3 条 (判断有无异常用)
        _preview = r.get("parsed") or r.get("lines") or []
        if "--json" in sys.argv:
            print(json.dumps({"command": "error-log", "name": r.get('name', ''), "path": r.get('path', ''),
                              "lines": r.get('line_count', 0), "size": r.get('size') or 0,
                              "format": fmt, "preview": [str(x) for x in list(_preview)[:3]]},
                             ensure_ascii=False))
        else:
            print(f"name={r.get('name','')} lines={r.get('line_count',0)} size={r.get('size') or 0} ok=true")
        return 0
    if "--json" in sys.argv:
        # 🆕 v4.12: error-log --json 结构化 entries[] (AI 直接断言/过滤)
        _j_entries = []
        parsed = r.get("parsed")
        if parsed is not None and fmt == "jsonl":
            for p in parsed:
                if isinstance(p, dict):
                    _j_entries.append({
                        "ts": p.get("ts") or p.get("time") or "",
                        "action": p.get("action") or p.get("level") or "",
                        "code": p.get("code") or "",
                        "message": p.get("message") or p.get("msg") or p.get("error") or "",
                        "path": p.get("path") or "",
                        "cmd_preview": p.get("cmd_preview", ""),
                        "timed_out": bool(p.get("timed_out")),
                    })
                else:
                    _j_entries.append({"raw": str(p)})
        else:
            for ln in r.get("lines", []):
                _j_entries.append({"raw": str(ln)})
        print(json.dumps({"command": "error-log", "name": r.get('name', ''), "path": r.get('path', ''),
                          "lines": r.get('line_count', 0), "size": r.get('size') or 0,
                          "format": fmt, "entries": _j_entries}, ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --error-log <name> --plain: meta 单行 + 原始行 (AI 直接 grep/断言)
        print(f"name={r.get('name','')} path={r.get('path','')} lines={r.get('line_count')} "
              f"size={r.get('size') or 0} format={fmt}")
        parsed = r.get("parsed")
        if parsed is not None and fmt == "jsonl":
            for p in parsed:
                if isinstance(p, dict):
                    ts = p.get("ts") or p.get("time") or "?"
                    act = p.get("action") or p.get("level") or ""
                    code = p.get("code") or ""
                    msg = p.get("message") or p.get("msg") or p.get("error") or ""
                    tail = []
                    for k in ["path","token_hash","ip","size","md5"]:
                        if k in p and p[k]: tail.append(f"{k}={p[k]}")
                    if p.get("cmd_preview"): tail.append(f"cmd={p['cmd_preview'][:100]}")
                    if p.get("cmd_len"): tail.append(f"len={p['cmd_len']}")
                    if p.get("stdout_len") is not None: tail.append(f"out={p['stdout_len']}B")
                    if p.get("timed_out"): tail.append("TIMEOUT")
                    if p.get("output_truncated"): tail.append("truncated")
                    if p.get("reason"): tail.append(f"reason={p['reason']}")
                    print(f"{ts} action={act} code={code} msg={msg}" + (" " + " ".join(tail) if tail else ""))
                else:
                    print(f"{p}")
        else:
            for ln in r.get("lines", []):
                print(ln)
        return 0
    print(f"📜 {r.get('name','')} @ {r.get('path','')}  ({r.get('line_count')} 行, "
          f"{(r.get('size') or 0)//1024} KB, format={fmt})")
    print()
    parsed = r.get("parsed")
    if parsed is not None and fmt == "jsonl":
        # JSONL 友好展示
        for p in parsed:
            if isinstance(p, dict):
                ts = p.get("ts") or p.get("time") or "?"
                act = p.get("action") or p.get("level") or ""
                code = p.get("code") or ""
                msg = p.get("message") or p.get("msg") or p.get("error") or ""
                path = p.get("path") or ""
                tail = []
                for k in ["path","token_hash","ip","size","md5"]:
                    if k in p and p[k]: tail.append(f"{k}={p[k]}")
                # 🆕 FIX-auditcmd-v4.6: 展示 exec 审计的 cmd 预览/长度/超时/输出统计,
                #   此前只显示 action/code/ip, AI 查"刚才干了什么"看不到命令内容。
                if p.get("cmd_preview"): tail.append(f"cmd={p['cmd_preview'][:100]}")
                if p.get("cmd_len"): tail.append(f"len={p['cmd_len']}")
                if p.get("stdout_len") is not None: tail.append(f"out={p['stdout_len']}B")
                if p.get("timed_out"): tail.append("TIMEOUT")
                if p.get("output_truncated"): tail.append("truncated")
                if p.get("reason"): tail.append(f"reason={p['reason']}")
                print(f"  {ts}  {act:<18} code={code:<6}  {msg}  {' '.join(tail)}")
            else:
                print(f"  {p}")
    else:
        for ln in r.get("lines", []):
            print(f"  {ln}")
    return 0

# 🆕 v4.9: 一键排查 — 自动合并多源日志，按时间排序输出
def cmd_doctor(conf: dict, log: Logger, api: ApiClient, lines_per_source: int = 50, plain: bool = False) -> int:
    """一键排查模式: 自动收集 nginx错误 + PHP错误 + 同步审计日志，按时间排序输出最近问题。"""
    import re
    from datetime import datetime

    # 🆕 v4.11-JSON: --json 时 stdout 只输出单行 JSON; ok 语义=工具执行成功, 环境问题用 health 表达
    _json_mode = "--json" in sys.argv
    if not plain and not _json_mode:
        print("🏥 ai-sync 一键排查模式")
        print("=" * 60)

    # 1. 拉取三个关键日志源
    sources = [
        ("nginx_err", "Nginx错误"),
        ("php_error", "PHP错误"),
        ("audit", "同步审计"),
    ]

    all_entries = []  # [(timestamp_str, source_tag, line_text)]
    _error_entries = []  # 结构化错误证据: brief 模式下 AI 可直接据此定位, 不依赖关键词猜测
    stats = {"nginx_err": 0, "php_error": 0, "audit": 0, "errors": 0, "warnings": 0}
    _missing = []
    # FIX-AI-idempotent-diagnosis-v4.39.3: 先取服务端实际存在的日志源清单,
    # 只拉取存在的源。旧实现无条件探测 nginx_err, 缺失时服务端把 code=1005
    # (log_not_found) 写进 audit.log, 下一轮 doctor 又把它计为 error —
    # 诊断自身制造噪声, errors 随诊断次数单调增长, AI 永远收敛不到 ok。
    _available = None
    try:
        # 老服务端没有 error_log_list 时不要试探: 探测未知 action 本身会写一条
        # 非零审计, 等于把噪声换个地方继续制造。用 whoami.available_actions 门控。
        _caps = None
        try:
            _w = api.whoami()
            if _w.get("ok"):
                _acts = _w.get("available_actions")
                _caps = {str(a) for a in _acts} if isinstance(_acts, list) else None
        except Exception:
            _caps = None
        if _caps is None or "error_log_list" in _caps:
            _lst = api.error_log_list()
            if _lst.get("ok"):
                _rows = _lst.get("logs") or []
                # 服务端 _discover_log_candidates 带 readable 标志; 老服务端不带
                # 时退化为只看 name, 保证跨版本/跨设备都可用。
                _has_readable = any(isinstance(c, dict) and "readable" in c for c in _rows)
                _available = {str(c.get("name")) for c in _rows
                              if isinstance(c, dict) and c.get("name")
                              and (not _has_readable or c.get("readable") is not False)}
    except Exception:
        _available = None   # 老服务端无该能力 -> 退回原有探测行为
    _avail_list = sorted(_available) if _available is not None else None

    for source_name, source_label in sources:
        if _available is not None and source_name not in _available:
            # 计数保持 0 (AI 消费方常假定 >=0), 缺失事实放到 missing_sources 字段
            stats[source_name] = 0
            _missing.append(source_name)
            continue
        try:
            r = api.error_log(name=source_name, lines=lines_per_source)
            if not r.get("ok"):
                if not plain and not _json_mode:
                    print(f"⚠️  {source_label}: 不可用 ({r.get('error', r.get('message', ''))})")
                continue

            fmt = r.get("format") or "text"
            stats[source_name] = r.get("line_count", 0)

            if fmt == "jsonl":
                parsed = r.get("parsed") or []
                for p in parsed:
                    if isinstance(p, dict):
                        ts = p.get("ts") or p.get("time") or ""
                        act = p.get("action") or p.get("level") or ""
                        code = p.get("code") or ""
                        # 智能提取消息内容
                        msg = (p.get("message") or p.get("msg") or p.get("error")
                               or p.get("path") or p.get("reason") or "")
                        # exec 类型的日志，提取命令预览
                        if act == "exec" and p.get("cmd_preview"):
                            msg = f"cmd: {p['cmd_preview'][:80]}"
                        line_text = f"[{act}] code={code} {msg}"
                        # FIX-AI-idempotent-diagnosis-v4.39.3: 只读探测类请求的
                        # NOT_FOUND 属于"可选日志源缺失", 不是服务器故障。存量审计
                        # 中这类条目降级为 known_noise, 保证 health 可以收敛。
                        # 只针对探测类 action, 不掩盖 401/403/invalid_path 等真实错误。
                        _probe = str(act) in ("error_log", "error_log_list",
                                              "oplog_view", "oplog_list")
                        _notfound = (str(code) in ("1005", "404")
                                     or "log_not_found" in str(msg))
                        if _probe and _notfound:
                            stats["known_noise"] = stats.get("known_noise", 0) + 1
                            all_entries.append((str(ts), source_label, line_text))
                            continue
                        # 统计错误数（只把数字 code 且非 0 的算错误）
                        if code and str(code).isdigit() and int(code) != 0:
                            stats["errors"] += 1
                            _error_entries.append({"source": source_label, "text": line_text[:120]})
                        # 警告统计（open_exec_no_allowlist 等）
                        elif code and not str(code).isdigit():
                            stats["warnings"] += 1
                        all_entries.append((str(ts), source_label, line_text))
            else:
                lines = r.get("lines") or []
                # 🆕 FIX-doctor-window-v4.17: 历史噪音过滤 — text 日志有时间戳且超出 24h 窗口的错误行
                #   不计入 health 统计 (nginx 历史 FastCGI 等旧错误不再误报 degraded), 但保留在输出列表供排查.
                from datetime import datetime as _dt, timedelta as _td
                _cutoff = _dt.now() - _td(hours=24)
                for ln in lines:
                    # 尝试从文本中提取时间戳（如 [Oct 13 15:30:00]）
                    ts_match = re.match(r'\[([^\]]+)\]', ln)
                    ts = ts_match.group(1) if ts_match else ""
                    _in_window = True
                    if ts:
                        _ts_dt = None
                        # FIX-AI-py315-v4.39.3: syslog 无年份日期先补年份再解析。
                        # strptime("%b %d %H:%M:%S") 在 3.13 触发 DeprecationWarning,
                        # 3.15 起直接抛异常 —— 会污染 AI 依赖的 stderr 契约。
                        for _fmt, _val in (("%Y/%m/%d %H:%M:%S", ts),
                                           ("%Y-%m-%d %H:%M:%S", ts),
                                           ("%b %d %H:%M:%S %Y", f"{ts} {_cutoff.year}")):
                            try:
                                _ts_dt = _dt.strptime(_val, _fmt)
                                break
                            except ValueError:
                                continue
                        if _ts_dt is not None and _ts_dt < _cutoff:
                            _in_window = False
                    # 🆕 v4.12.8: 已知安全噪音降级 (exec 全放行告警) — 不误报 doctor health
                    if "exec called with empty EXEC_ALLOW_IPS" in ln:
                        stats["warnings"] += 1
                        stats["known_noise"] = stats.get("known_noise", 0) + 1
                        all_entries.append((ts, source_label, ln))
                        continue
                    # 统计错误数 (仅 24h 窗口内的行计入 health; 窗口外的只进输出列表)
                    if _in_window and re.search(r'\b(error|fatal|warning|notice|critical)\b', ln, re.I):
                        if re.search(r'\b(error|fatal|critical)\b', ln, re.I):
                            stats["errors"] += 1
                            _error_entries.append({"source": source_label, "text": ln[:120]})
                        else:
                            stats["warnings"] += 1
                    all_entries.append((ts, source_label, ln))
        except Exception as e:
            if not plain and not _json_mode:
                print(f"⚠️  {source_label}: 请求失败 ({e})")

    # 2. 按时间排序（简单字符串排序，大部分日志格式兼容）
    all_entries.sort(key=lambda x: x[0], reverse=True)

    # 3. 输出汇总
    if not plain and not _json_mode:
        print(f"📊 汇总:")
        print(f"  • Nginx错误: {stats['nginx_err']} 行")
        print(f"  • PHP错误: {stats['php_error']} 行")
        print(f"  • 同步审计: {stats['audit']} 行")
        print(f"  • 发现错误: {stats['errors']} 个")
        print(f"  • 发现警告: {stats['warnings']} 个")
        print()
        print(f"📝 最近日志（按时间倒序，最多 {len(all_entries)} 条）:")
        print("-" * 60)

    # 4. 输出合并后的日志
    if "--brief" in sys.argv:
        # 🆕 FIX-brief-v4.17: 精简模式 — 只 health + 计数, 不列条目 (AI 只看健康结论)
        if _json_mode:
            print(json.dumps({"command": "doctor", "brief": True,
                              "health": "ok" if stats["errors"] == 0 else "degraded",
                              "nginx_errors": stats["nginx_err"], "php_errors": stats["php_error"],
                              "errors": stats["errors"], "warnings": stats["warnings"],
                              "known_noise": stats.get("known_noise", 0),
                              "missing_sources": _missing,
                              "available_sources": _avail_list,
                              "top_errors": (_error_entries[:5] or
                                             [{"source": src, "text": ln[:120]}
                                              for ts, src, ln in all_entries[:5]])},
                             ensure_ascii=False))
        else:
            print(f"health={'ok' if stats['errors']==0 else 'degraded'} errors={stats['errors']} "
                  f"warnings={stats['warnings']} nginx={stats['nginx_err']} php={stats['php_error']} "
                  f"known_noise={stats.get('known_noise',0)} "
                  f"missing={','.join(_missing) or '-'} ok=true")
        return 0
    if _json_mode:
        # 🆕 v4.11-JSON: 结构化输出, ok=工具执行成功, health 表达环境问题
        print(json.dumps({
            "command": "doctor",
            "health": "ok" if stats["errors"] == 0 else "degraded",
            "nginx_errors": stats["nginx_err"],
            "php_errors": stats["php_error"],
            "audit_entries": stats["audit"],
            "errors": stats["errors"],
            "warnings": stats["warnings"],
            "known_noise": stats.get("known_noise", 0),
            "missing_sources": _missing,
            "available_sources": _avail_list,
            "entries": [{"source": src, "ts": ts, "text": ln[:200]}
                        for ts, src, ln in all_entries[:lines_per_source * 2]],
        }, ensure_ascii=False))
        return 0  # 工具执行成功; 环境问题用 health 表达
    for ts, source, line in all_entries[:lines_per_source * 2]:  # 最多输出合并后的条数
        if plain:
            print(f"[{source}] {ts} {line}")
        else:
            # 高亮错误/警告
            if re.search(r'\b(error|fatal|critical|FAIL)\b', line, re.I):
                print(f"  🔴 [{source}] {ts}")
                print(f"     {line[:120]}")
            elif re.search(r'\b(warning|notice)\b', line, re.I):
                print(f"  🟡 [{source}] {ts}")
                print(f"     {line[:120]}")
            else:
                print(f"  ⚪ [{source}] {ts} {line[:100]}")

    if plain:
        # FIX-AI-contract-v4.39.3: plain 模式同样输出机器可读的一行结论,
        # 退出码与 JSON 模式统一为 0 (ok=工具执行成功, health=环境结论)。
        print(f"health={'ok' if stats['errors']==0 else 'degraded'} "
              f"errors={stats['errors']} warnings={stats['warnings']} "
              f"nginx={stats['nginx_err']} php={stats['php_error']} "
              f"known_noise={stats.get('known_noise',0)} "
              f"missing={','.join(_missing) or '-'} ok=true")
    else:
        print()
        print("💡 提示:")
        # 只提示真实存在的日志源, 避免把 AI 指去看不存在的日志
        for _nm in ("nginx_err", "php_error", "audit"):
            if _available is None or _nm in _available:
                print(f"  • 想看更多详情: --error-log {_nm} --error-lines 100")
        print("  • 精简结论: --doctor --brief")

    return 0

# ---------------------------------------------------------------------------
# P1-B: --watch 守护模式 (PID+单实例+文件变更自动同步)
# ---------------------------------------------------------------------------
def _watch_pid_file(conf: dict) -> Path:
    return Path(conf["watch_dir"]) / ".ai-sync-watch.pid"

def _watch_state_file(conf: dict) -> Path:
    return Path(conf["watch_dir"]) / ".ai-sync-watch.json"

def _is_watch_process(pid: int) -> bool:
    """BUG-7 修复 (PID 复用误杀): 验证 pid 对应的进程确实是 ai-sync watch 守护,
    而非 OS 复用的无关进程. 仅靠 os.kill(pid,0) 无法区分, PID 被复用时会导致:
      1) cmd_watch_start 误判"已在运行"拒绝启动
      2) cmd_watch_stop 向无关进程发 SIGKILL (灾难性!)
    方案: 读 /proc/{pid}/cmdline 校验含 "sync.py" + "--watch"."""
    if pid <= 0: return False
    # 1) 进程存活检查
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但非当前用户, 保守认为不是我们的 (不会误杀别人)
        return False
    # 2) cmdline 校验 (Linux /proc)
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        # /proc/cmdline 用 \0 分隔参数
        args = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        # 必须同时含 sync.py 和 --watch (排除 python -c "..." 等无关进程)
        if "sync.py" in args and "--watch" in args:
            return True
        # macOS 无 /proc, cmdline 读不到 → 回退: 进程存活就认为是 (非 Linux 保守策略)
        if not Path("/proc").is_dir():
            return True
        return False
    except Exception:
        # 读不到 cmdline 且非 Linux → 回退信任存活检查
        if not Path("/proc").is_dir():
            return True
        return False

def _watch_get_running_pid(conf: dict) -> int:
    """返回运行中的 watch PID，否则 0"""
    pf = _watch_pid_file(conf)
    if not pf.is_file(): return 0
    try:
        pid = int(pf.read_text().strip())
        if pid <= 0: return 0
        # BUG-7: 用 _is_watch_process 替代裸 os.kill(pid, 0), 防 PID 复用误判
        if _is_watch_process(pid):
            return pid
        # PID 文件残留但进程不是 watch (已退出 + PID 被复用), 清理 stale PID 文件
        try: pf.unlink()
        except Exception: pass
        return 0
    except Exception:
        return 0

def _watch_set_state(conf: dict, **extra):
    try:
        st = {"pid": _watch_get_running_pid(conf),
              "ts": int(time.time()), "t": time.strftime("%Y-%m-%d %H:%M:%S")}
        st.update(extra)
        _watch_state_file(conf).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception: pass

def cmd_watch_status(conf: dict, log: Logger, api: ApiClient, plain: bool = False) -> int:
    pid = _watch_get_running_pid(conf)
    state_path = _watch_state_file(conf)
    st = {}
    if state_path.is_file():
        try: st = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception: pass
    if pid:
        if "--json" in sys.argv:
            # 🆕 v4.12.5-JSON: watch status 结构化 (AI 确认守护状态)
            print(json.dumps({"command": "watch", "action": "status", "running": True,
                              "pid": pid, "start": st.get('t', ''), "mode": st.get('mode', ''),
                              "interval_s": st.get('interval_s'),
                              "last_sync": st.get('last_sync_t', ''),
                              "total_synced": st.get('total_synced'),
                              "errors": st.get('errors')}, ensure_ascii=False))
            return 0
        if plain:
            # 🆕 FIX-plain-v4.6: --watch status --plain 单行键值 (AI 断言 state=running)
            extra = []
            if st.get('t'): extra.append(f"start={st['t']}")
            if st.get('mode'): extra.append(f"mode={st['mode']}")
            if st.get('interval_s') is not None: extra.append(f"interval={st['interval_s']}")
            if st.get('last_sync_t'): extra.append(f"last_sync={st['last_sync_t']}")
            if st.get('total_synced') is not None: extra.append(f"total={st['total_synced']}")
            if st.get('errors') is not None: extra.append(f"errors={st['errors']}")
            print(f"state=running pid={pid}" + (" " + " ".join(extra) if extra else ""))
            return 0
        print(f"✅ --watch 守护进程已运行: PID={pid}")
        if st:
            print(f"  启动时间: {st.get('t','?')}  模式: {st.get('mode','?')}  间隔: {st.get('interval_s','?')}s")
            for k in ("last_sync_t","last_n_files","last_strategy","errors","total_synced"):
                if k in st: print(f"  {k}: {st[k]}")
    else:
        if "--json" in sys.argv:
            print(json.dumps({"command": "watch", "action": "status", "running": False,
                              "pid": 0}, ensure_ascii=False))
            return 0
        if plain:
            print("state=stopped")
            return 0
        print("⏸ --watch 守护进程未运行")
    # 额外展示下一轮候选变更
    if not pid:
        return 0
    return 0

def cmd_watch_stop(conf: dict, log: Logger, api: ApiClient) -> int:
    pid = _watch_get_running_pid(conf)
    if not pid:
        print("⏸ --watch 守护进程未运行")
        return 0
    import os, signal
    # BUG-7 双重校验: 发信号前再验一次进程身份 (防 PID 在读取后被复用)
    if not _is_watch_process(pid):
        print(f"⚠  PID={pid} 已不是 watch 守护 (可能已退出且 PID 被复用), 清理 PID 文件")
        try: _watch_pid_file(conf).unlink()
        except Exception: pass
        return 0
    # SIGTERM 优雅退出
    try: os.kill(pid, signal.SIGTERM)
    except Exception as e:
        print(f"❌ 发送 SIGTERM 失败: {e}"); return 2
    print(f"⏳ 已发送 SIGTERM, 等待守护进程退出 ...", flush=True)
    for _ in range(30):
        time.sleep(0.2)
        if not _watch_get_running_pid(conf): break
    if _watch_get_running_pid(conf):
        # BUG-7: SIGKILL 前再次校验进程身份, 绝不向无关进程发 SIGKILL
        if not _is_watch_process(pid):
            print(f"⚠  PID={pid} 已被其他进程复用, 跳过 SIGKILL (避免误杀)")
        else:
            print(f"⚠  进程 PID={pid} 无法优雅退出, 发送 SIGKILL")
            try: os.kill(pid, signal.SIGKILL)
            except Exception as e:
                print(f"❌ SIGKILL 失败: {e}"); return 2
            time.sleep(0.5)
    try: _watch_pid_file(conf).unlink()
    except Exception: pass
    print(f"✅ --watch 守护进程已停止 (PID={pid})")
    return 0

def cmd_watch_start(conf: dict, log: Logger, api: ApiClient, *,
                    interval_s: int = 2, mode: str = "push", smart: bool = True,
                    subdir: str | None = None, once: bool = False) -> int:
    """启动 --watch 守护: 按 interval 检查目录变更，自动推送。
    mode:
      "push"   : 只推送本地变更到远程 (AI 默认写代码场景)
      "two-way": 双向同步 (也会 pull 远程变更回来)
    smart=True 会启用 P1-C 智能跳过 (size+mtime+md5 比对)。
    subdir: 只监控指定子目录 (相对于 watch_dir), 所有扫描/推送/拉取限制在该目录内。

    稳定性优化 (v4.1+):
      * 空闲降频: 连续 N 轮无变更 → 间隔翻倍直到 MAX_BACKOFF_S
      * 连续错误上限: CONSECUTIVE_ERROR_LIMIT 次失败后主动退出 (防止卡死消耗)
      * 顶层 try-except: 单轮异常被吞掉并计数, 守护不退出
      * 内存泄漏防御: 每 SNAPSHOT_CLEANUP_EVERY 轮做一次 snapshot entries 清理
      * 轮次超时保护: 单轮超过 ROUND_TIMEOUT_S 就打 warn 日志
      * Snapshot 节流写盘: save() 默认 SAVE_THROTTLE_S, 退出时 force=True
      * SIGINT/SIGTERM 优雅退出: 设置 running=False, finally 释放锁/删PID/落盘
    """
    # ---- 配置常量 (可按需调, 也可以从 conf 扩展) ----
    IDLE_ROUNDS_BEFORE_BACKOFF = 10   # 连续 N 轮无变更才开始降频
    MAX_BACKOFF_S = 30.0              # 最大睡眠间隔 (30s 不扫描 vs 2s 默认)
    CONSECUTIVE_ERROR_LIMIT = 50      # 连续异常上限 (防止网络/权限卡死无意义重试)
    SNAPSHOT_CLEANUP_EVERY = 100      # 每 N 轮清理一次 snapshot 陈旧条目
    ROUND_TIMEOUT_S = 300.0           # 单轮耗时超过这个阈值就告警
    PULL_INTERVAL_S = 15.0            # two-way 模式: 空闲时远程拉取间隔 (秒)
    # ---------------------------------------------
    # v4.38: 允许 conf 覆盖空闲拉取间隔 (测试/AI 调优), 0 视为立即 (每轮都拉)
    try:
        _pull_interval_s = float(conf.get("watch_pull_interval", PULL_INTERVAL_S) or 0)
    except Exception:
        _pull_interval_s = PULL_INTERVAL_S

    # 🆕 --watch-subdir: 只监控指定子目录, 临时修改 watch_dir 为子目录绝对路径
    if subdir:
        orig_watch_dir = conf["watch_dir"]
        subdir_path = Path(orig_watch_dir) / subdir
        if not subdir_path.exists():
            print(f"❌ 子目录不存在: {subdir_path}")
            return 2
        if not subdir_path.is_dir():
            print(f"❌ 不是目录: {subdir_path}")
            return 2
        # 修复: 同时修改 remote_base, 让远程路径包含子目录
        orig_remote_base = conf.get("remote_base", "") or ""
        subdir_rel = subdir_path.relative_to(Path(orig_watch_dir)).as_posix()
        conf["remote_base"] = (orig_remote_base.rstrip("/") + "/" + subdir_rel).strip("/") if orig_remote_base else subdir_rel
        conf["watch_dir"] = str(subdir_path.resolve())
        print(f"📂 子目录模式: 只监控 {conf['watch_dir']}")
        print(f"📂 远程基础路径: {conf['remote_base']}")

    pid = _watch_get_running_pid(conf)
    if pid:
        print(f"⚠  --watch 已经在运行 PID={pid}, 先 --watch-stop 停止再启动"); return 1
    # 单实例探测: 非阻塞尝试, 命中则退出 (防止两个 watch 守护)
    watch_dir = Path(conf["watch_dir"])
    lock_path = watch_dir / ".sync.lock"
    lock = FileLock(lock_path)
    if not lock.acquire(blocking=False):
        print(f"❌ 主同步实例已在占用 {lock_path}, --watch 无法启动"); return 99
    # BUG-4 修复: 不再长持锁! 仅做探测, 立即释放.
    # 守护循环里每轮 acquire→sync→release, sleep 期间锁是空的,
    # 这样 --push/--pull 能在守护睡眠期拿到锁安全执行, 不会和守护并发改 snapshot.
    lock.release()
    if once:
        # --watch start --once: 单次扫描 + 推送后退出 (不写 PID, 前台阻塞命令的 AI 安全版本)
        api.remote_base = conf.get("remote_base", "")
        api._watch_dir = watch_dir
        engine = SyncEngine(conf, log, api)
        changed = _watch_scan_changes(engine, smart)
        if changed:
            _watch_do_push(engine, changed, smart)
        if "--json" in sys.argv:
            print(json.dumps({"command": "watch", "action": "once", "ok": True,
                              "changed": len(changed), "mode": mode,
                              "subdir": subdir or ""}, ensure_ascii=False))
        else:
            print(f"✅ --watch --once 完成: 变更 {len(changed)} 个文件 mode={mode}")
        return 0
    # 写 PID 文件 (PID 文件才是守护单实例的真正凭证)
    try:
        _watch_pid_file(conf).write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        print(f"❌ 写入 PID 文件失败: {e}"); return 2
    _watch_set_state(conf, mode=mode, interval_s=interval_s,
                     smart=smart, errors=0, total_synced=0)

    api.remote_base = conf.get("remote_base", "")
    api._watch_dir = watch_dir  # 供 smart_filter 使用
    engine = SyncEngine(conf, log, api)
    conf_smart = smart

    # 🆕 v4.12: 启动确认输出 (AI 确认启动成功; --json 结构化, stdout 保持纯净)
    # 🆕 FIX-watch-pipe: 确认输出遇管道关闭(EPIPE) → 清理 PID 后直接退出, 不留空转/残留
    try:
        if "--json" in sys.argv:
            print(json.dumps({"command": "watch", "action": "start", "ok": True,
                              "pid": os.getpid(), "mode": mode, "interval_s": interval_s,
                              "smart": smart, "subdir": subdir or ""}, ensure_ascii=False))
        else:
            print(f"✅ --watch 已启动 PID={os.getpid()} mode={mode} interval={interval_s}s "
                  f"smart={smart}" + (f" subdir={subdir}" if subdir else ""), flush=True)
    except OSError:
        try: _watch_pid_file(conf).unlink()
        except Exception: pass
        return 0

    # ---- 信号处理: SIGINT/SIGTERM → 优雅退出 ----
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    def _graceful_shutdown(signum, frame):
        log.warn(f"[watch] 收到信号 signum={signum}, 准备优雅退出...")
        engine.running = False
    try:
        signal.signal(signal.SIGINT, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)
    except (ValueError, OSError):
        # 非主线程 / 平台不支持, 忽略
        pass

    consecutive_errors = 0   # 连续异常计数
    idle_rounds = 0          # 连续无变更轮数
    current_interval = float(interval_s)
    round_count = 0          # 总轮次计数 (用于 snapshot cleanup)
    last_pull_t = 0.0        # 上次远程拉取时间 (two-way 空闲拉取节流)

    try:
        # ================================================================
        # 顶层 try-except 兜底: 任何未捕获异常都不能让守护崩溃
        # 最多在 except 里打 warn + 计入 consecutive_errors, 超过上限才退出
        # ================================================================
        while engine.running:
            try:
                round_count += 1
                t_round_start = time.time()
                # BUG-4 修复: 每轮 acquire 锁, 同步完 release. sleep 期间不持锁,
                # 让 --push/--pull 有机会拿到锁执行 (它们通常很快, 守护最多等一轮).
                if not lock.acquire(blocking=False):
                    # 锁被 --push/--pull 占着, 跳过本轮 (它们会更新 snapshot 和远程)
                    log.debug(f"[watch] 本轮跳过: .sync.lock 被 --push/--pull 占用")
                    time.sleep(min(2.0, float(current_interval)))
                    continue
                round_lock_held = True
                try:
                    # 0) 目录状态同步 (空目录 / 目录 mode). 与文件事件无关; 本地 walk
                    #    限制为最多每秒一次 (大目录树避免每 0.2s 全树 walk 两次).
                    #    _dir_cache 让未变化的目录不发请求; 每 N 秒用远程列表校准缓存,
                    #    其他 AI 删除远程目录后能自愈重建.
                    dir_synced = 0
                    _dir_due = (time.time() - engine._last_dir_scan) >= max(1.0, float(interval_s))
                    if _dir_due:
                        engine._last_dir_scan = time.time()
                        try:
                            _dir_reconcile_s = float(conf.get("dir_reconcile_interval", 60.0) or 0)
                            if _dir_reconcile_s > 0 and time.time() - engine._last_dir_reconcile >= _dir_reconcile_s:
                                engine._last_dir_reconcile = time.time()
                                _dropped = reconcile_dir_cache(engine.api, engine.conf, engine._dir_cache)
                                if _dropped:
                                    log.info(f"[watch] 目录缓存校准: 远端缺失 {_dropped} 个目录, 将重新创建")
                            dir_synced = sync_local_dirs(engine.api, engine.conf, engine.log,
                                                         cache=engine._dir_cache)
                        except Exception as de:
                            log.debug(f"[watch] 目录同步异常 (下轮重试): {de}")

                    # 1) 只扫描一次 → 统计变更 → 走 P1-C 智能跳过
                    changed = _watch_scan_changes(engine, smart=conf_smart)
                    if changed:
                        idle_rounds = 0
                        current_interval = float(interval_s)  # 一有变更立刻恢复默认频率
                        try:
                            if mode == "push":
                                # 自己实现 push 轮 (不依赖 engine.run once)，以便直接复用 smart
                                _watch_do_push(engine, changed, smart=conf_smart)
                            else:
                                # two-way: push 本地变更 + pull 远程变更 (轻量, 不走 engine.run)
                                _watch_do_push(engine, changed, smart=conf_smart)
                                _watch_do_pull(engine)
                                last_pull_t = time.time()
                            # 上传/部署会自动建父目录 (默认 0755): 失效父目录缓存,
                            # 下一轮(≤1s)再回填真实 mode; 不在此处重复全树 walk.
                            engine._dir_cache_invalidate(list(changed.values()))
                            engine._last_dir_scan = 0.0
                            _watch_set_state(conf, mode=mode, interval_s=interval_s, smart=conf_smart,
                                             last_sync_t=time.strftime("%Y-%m-%d %H:%M:%S"),
                                             last_n_files=len(changed), last_strategy=("smart" if conf_smart else "full"),
                                             last_sync_ok=True, errors=0)
                            consecutive_errors = 0
                        except Exception as exc:
                            import traceback
                            if isinstance(exc, OSError) and exc.errno == 32:
                                log.warn("[watch] 输出管道已关闭(EPIPE), 守护优雅退出")
                                engine.running = False
                                break
                            log.warn(f"[watch] 同步轮次异常: {exc}\n{traceback.format_exc()}")
                            consecutive_errors += 1
                            st_prev = (json.loads(_watch_state_file(conf).read_text() or "{}")
                                       if _watch_state_file(conf).is_file() else {})
                            _watch_set_state(conf, mode=mode, interval_s=interval_s, smart=conf_smart,
                                             last_sync_ok=False,
                                             errors=int(st_prev.get("errors", 0)) + 1,
                                             last_error=str(exc))
                    elif dir_synced:
                        # 只有目录变化 (新增空目录 / chmod): 已同步, 视为有效轮次不降频
                        idle_rounds = 0
                        current_interval = float(interval_s)
                        _watch_set_state(conf, mode=mode, interval_s=interval_s, smart=conf_smart,
                                         last_sync_t=time.strftime("%Y-%m-%d %H:%M:%S"),
                                         last_n_files=0, last_strategy="dir", last_sync_ok=True)
                        consecutive_errors = 0
                    else:
                        # ---- 空闲降频 ----
                        idle_rounds += 1
                        if idle_rounds >= IDLE_ROUNDS_BEFORE_BACKOFF:
                            current_interval = min(MAX_BACKOFF_S, current_interval * 1.5)
                        # (无变更就不更新 last_sync_t / last_sync_ok, 保留上一次成功信息)

                        # ---- two-way: 空闲时定期拉取远程变更 ----
                        if mode != "push" and engine.running and (time.time() - last_pull_t) >= _pull_interval_s:
                            try:
                                _watch_do_pull(engine)
                                last_pull_t = time.time()
                            except Exception as pull_exc:
                                log.warn(f"[watch] 空闲拉取异常 (不影响守护): {pull_exc}")

                    # ---- 每轮都尝试落盘 snapshot ----
                    # BUG-6 修复: 有变更时 force=True 立即落盘; 无变更时 force=False 走节流.
                    try:
                        engine.snapshot.save(force=bool(changed))
                    except Exception:
                        pass

                    # ---- 每 SNAPSHOT_CLEANUP_EVERY 轮: 清理 snapshot 中已不存在的条目 ----
                    # (防内存泄漏: 老项目里 snapshot.data 可能有上万条陈旧 path)
                    if round_count % SNAPSHOT_CLEANUP_EVERY == 0:
                        try:
                            # scan_watch_dir 返回当前所有现存文件
                            mon_all = scan_watch_dir(
                                Path(conf["watch_dir"]), conf["remote_base"],
                                conf["ignore"], conf["allow_ext"])
                            current_paths = set(str(m["local"]) for m in mon_all if m.get("size", -1) >= 0)
                            removed_cnt = engine.snapshot.cleanup(current_paths)
                            if removed_cnt > 0:
                                log.info(f"[watch] snapshot 清理了 {removed_cnt} 个过期条目")
                        except Exception as ce:
                            log.warn(f"[watch] snapshot 清理异常 (不影响同步): {ce}")
                finally:
                    # 同步段结束, 释放锁让 --push/--pull 能跑
                    try: lock.release()
                    except Exception: pass
                    round_lock_held = False

                # ---- 轮次耗时告警 (防某些调用卡死导致守护看起来像挂了) ----
                round_elapsed = time.time() - t_round_start
                if round_elapsed > ROUND_TIMEOUT_S:
                    log.warn(f"[watch] 本轮耗时 {round_elapsed:.1f}s > {ROUND_TIMEOUT_S}s,"
                             f" 可能是 scan 或 batch_deploy 卡住, 请关注网络/磁盘/大文件情况")

                # ---- 连续错误上限: 达到就主动退出 (防止无限消耗资源) ----
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                    log.warn(f"[watch] 连续异常 {consecutive_errors} 次 ≥ {CONSECUTIVE_ERROR_LIMIT},"
                             f" 为避免无意义消耗, 守护主动退出。最后一次成功同步状态请查 --watch status")
                    break

                # ---- 睡眠 (当前 interval 分小段 sleep, 以便 SIGINT 能快速响应) ----
                # BUG-4: sleep 期间不持锁, --push/--pull 可安全执行
                sleep_left = max(0.05, current_interval - round_elapsed)
                seg = 0.1
                while sleep_left > 0 and engine.running:
                    s = min(seg, sleep_left)
                    time.sleep(s)
                    sleep_left -= s

            except Exception as round_exc:
                # ========================================================
                # 最外层兜底: 单轮 for 里任何漏网异常都在这里被吞掉
                # 只要不是主动退出条件, 守护继续下一轮
                # ========================================================
                if isinstance(round_exc, OSError) and round_exc.errno == 32:
                    log.warn("[watch] 输出管道已关闭(EPIPE), 守护优雅退出")
                    break
                consecutive_errors += 1
                import traceback
                log.warn(f"[watch] 单轮顶层未捕获异常 (守护不退出, 连续异常计数={consecutive_errors}):"
                         f" {round_exc}\n{traceback.format_exc()}")
                # 兜底也记一下 state, 方便 --watch status 看
                try:
                    st_prev = (json.loads(_watch_state_file(conf).read_text() or "{}")
                               if _watch_state_file(conf).is_file() else {})
                    _watch_set_state(conf, mode=mode, interval_s=interval_s, smart=conf_smart,
                                     last_sync_ok=False,
                                     errors=int(st_prev.get("errors", 0)) + 1,
                                     last_error=f"(顶层兜底) {round_exc}")
                except Exception:
                    pass
                # 连续异常太多也退
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                    log.warn(f"[watch] 连续异常达上限 {CONSECUTIVE_ERROR_LIMIT}, 退出")
                    break
                # 异常轮睡眠一小段, 避免死循环刷日志
                try:
                    time.sleep(min(5.0, max(1.0, current_interval)))
                except Exception:
                    pass
    finally:
        # ---- 退出必做: 恢复信号 → flush → 强制 save → 释放锁 → 删 PID ----
        try:
            signal.signal(signal.SIGINT, original_sigint or signal.SIG_DFL)
            signal.signal(signal.SIGTERM, original_sigterm or signal.SIG_DFL)
        except Exception:
            pass
        try:
            engine._flush()
        except Exception:
            pass
        # 强制落盘 snapshot (退出前必须最后一次落盘, 防止节流导致的状态丢失)
        try:
            engine.snapshot.save(force=True)
        except Exception:
            pass
        try:
            lock.release()
        except Exception:
            pass
        try:
            _watch_pid_file(conf).unlink()
        except Exception:
            pass
        log.info(f"[watch] 守护已退出 round_count={round_count}")
    return 0

def _watch_scan_changes(engine: SyncEngine, smart: bool = True) -> dict[str, dict]:
    """扫描监控目录，返回 {local_path: {size, mtime, sha1, remote}} 变更项（与 snapshot 不同）。"""
    conf = engine.conf
    mon = scan_watch_dir(Path(conf["watch_dir"]), conf["remote_base"], conf["ignore"], conf["allow_ext"])
    diff: dict[str, dict] = {}
    for m in mon:
        if m["size"] < 0: continue  # 缺失不处理 (AI 场景只 push)
        prev = engine.snapshot.get(m["local"])
        # BUG-26 修复: snapshot.md5 是 fake 占位符 → 之前从未真正同步成功, 强制 dirty 触发重传.
        # 这是导致 verify 报"远程缺失"但 watch 不动的根本原因 (fake_md5 == fake_md5 → 永远不脏).
        dirty = _snap_has_fake_md5(prev) or (prev.get("size") != m["size"] or prev.get("sha1") != m["sha1"]
                                             or _mode_changed(prev, m))
        if dirty:
            diff[m["local"]] = m
    return diff

def _watch_do_push(engine: SyncEngine, changed: dict[str, dict], smart: bool):
    """将变更字典里的文件通过 batch_deploy (带 smart 跳过) 推到远程。
    增强2: batch_deploy 失败时, 逐个文件重试, 跳过持续失败的文件, 不阻塞其他文件推送。"""
    if not changed: return
    watch = Path(engine.conf["watch_dir"])
    remote_base = engine.conf.get("remote_base", "") or ""
    # 组装 files payload
    files: list[dict] = []
    local_map: dict[str, dict] = {}  # remote_path -> {local, m, real_md5} 映射, 用于失败后重试 + 真 md5 写快照
    # FIX-symlink: 软链接单独走链接协议, 不读目标内容
    link_items: dict[str, dict] = {local: m for local, m in changed.items()
                                   if Path(local).is_symlink()}
    link_ok = link_fail = 0
    for local, m in link_items.items():
        f = link_finger(Path(local), watch.resolve())
        if f["size"] < 0 or not f.get("link_safe"):
            engine.log.warn(f"[watch] 跳过越界/绝对路径软链接: {local} ({f.get('link_reason') or 'unsafe'})")
            link_fail += 1
            continue
        r = engine.api.upload_symlink(m["remote"], f["link_target"])
        if r.get("ok"):
            engine.snapshot.update(local, {
                "size": 0, "sha1": f["sha1"], "md5": f["md5"],
                "remote": m["remote"], "mtime": f["mtime"], "mode": f["mode"],
                "source": m.get("source", ""), "is_symlink": True,
                "link_target": f["link_target"], "link_target_rel": f["link_target_rel"],
            })
            link_ok += 1
        else:
            link_fail += 1
            engine.log.warn(f"[watch] 软链接上传失败: {m['remote']}: {r.get('error','')}")
    if link_ok:
        try: engine.snapshot.save(force=True)
        except Exception: pass
        engine.log.info(f"[watch] pushed links n={link_ok} fail={link_fail}")
    # 修复 BUG-8 (fake md5 污染快照): scan_watch_dir fast 模式 md5="f:..." 是占位符,
    #   _watch_do_push 成功后直接 engine.snapshot.update(local, m) 会把 fake md5 写进快照,
    #   下一轮 _watch_do_pull 拿 fake 与真 remote md5 比对永远不等 → 误判冲突 + 反复重传.
    #   修复: 上传时已有 data, 顺手算真 md5, snapshot 用真 md5 覆盖.
    for local, m in changed.items():
        p = Path(local)
        if p.is_symlink() or not p.is_file(): continue
        # BUG-20 修复 (TOCTOU): is_file() 后文件可能被删/改, read_bytes/stat 需 try-except
        #   否则单文件异常会冒泡导致整个 push 轮失败, 同批其他文件本轮都不推送.
        try:
            data = p.read_bytes()
            real_md5 = hashlib.md5(data).hexdigest()
            rel = p.relative_to(watch).as_posix()
            file_mode = oct(p.stat().st_mode & 0o777)[2:].zfill(4)
        except Exception as e:
            engine.log.debug(f"[watch] 跳过 (读取失败/已删除): {p.name}: {e}")
            continue
        # 客户端到远程的路径变换
        # FIX-watch-remotebase: scan 已按 REMOTE_BASE 生成 m["remote"] (含前缀).
        #   旧代码用 _resolve_remote_path() 会剥掉前缀 → REMOTE_BASE 非空时文件被推到 scope 根,
        #   与链接/拉取使用的路径不一致, 守护同步会反复错位.
        rp = m.get("remote") or ((remote_base.rstrip("/") + "/" + rel) if remote_base else rel)
        files.append({
            "path": rp,
            "content_b64": base64.b64encode(data).decode(),
            "mode": file_mode,
            # v4.38: watch 推送也要保留 mtime (0 是合法 epoch, 不能用真值判断)
            "mtime": int(m["mtime"]) if m.get("mtime") is not None else None,
        })
        # 用真 md5 替换 m 中的 fake md5, snapshot 拿到的是真值
        m_real = dict(m, md5=real_md5)
        local_map[rp] = {"local": local, "m": m_real, "data": data}
    if not files: return
    _t0 = time.time()
    # 默认 pre_check: PHP 走 php -l
    pre = ""
    if any(f["path"].endswith(".php") for f in files):
        pre = "php -l {file}"
    # P1-C 智能跳过
    engine.api.remote_base = remote_base
    engine.api._watch_dir = watch
    resp = engine.api.batch_deploy(files, pre_check=pre, post_check="", rollback_on_fail=True,
                                   smart_skip=smart)
    if resp.get("ok"):
        engine.log.info(f"[watch] pushed n={len(files)} deploy_id={resp.get('deploy_id')}")
        # 🆕 v4.12.6: watch 自动同步写 oplog (AI 事后审计: 守护同步了什么)
        # v4.36: 统一走 _append_oplog, 同时上传服务端 (admin 面板可见)
        try:
            _wa = {"n": len(files), "deploy_id": resp.get("deploy_id"),
                   "files": [f["path"] for f in files[:20]]}
            _we = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "ts_epoch": int(time.time()),
                   "op_id": f"{int(time.time() * 1000)}-{os.getpid()}-{os.urandom(4).hex()}",
                   "command": "watch", "args": _wa,
                   "summary": f"守护同步: 推送 {len(files)} 个文件",
                   "rc": 0, "duration_ms": int((time.time() - _t0) * 1000)}
            _we.update(_oplog_meta())
            _append_oplog(engine.conf, _we)
        except Exception:
            pass
        # 更新 snapshot (用真 md5 的 m_real, 而非原 m 含 fake md5)
        for rp, info in local_map.items():
            engine.snapshot.update(info["local"], info["m"])
        # BUG-6 修复 (中断状态丢失): 上传成功后立即落盘 snapshot, 不等轮末节流写.
        # 否则守护被 SIGKILL/崩溃时, 已上传文件的 snapshot 状态丢失, 下轮重复上传 (浪费配额).
        try: engine.snapshot.save(force=True)
        except Exception: pass
    else:
        engine.log.warn(f"[watch] batch_deploy 失败 code={resp.get('code')} "
                         f"err={resp.get('error','')} msg={resp.get('message','')[:200]}")
        # 增强2: batch_deploy 整体失败时, 逐个文件重试 (不依赖 batch_deploy 的原子性)
        # 这样即使 batch_deploy 有问题, 单个文件仍有机会推送成功
        retry_ok = retry_fail = 0
        for f in files:
            rp = f["path"]
            info = local_map.get(rp)
            if not info:
                continue
            # 跳过持续失败的文件 (防卡死)
            fail_count = getattr(engine, '_watch_fail_counts', {}).get(info["local"], 0)
            if fail_count >= 3:
                engine.log.debug(f"[watch] 跳过持续失败文件: {rp} (已失败 {fail_count} 次)")
                continue
            r1 = engine.api.upload_file(rp, info["data"],
                                        None if len(f.get("mode","")) != 4 else f["mode"])
            if r1.get("ok"):
                engine.snapshot.update(info["local"], info["m"])
                retry_ok += 1
                # 重置失败计数
                if hasattr(engine, '_watch_fail_counts'):
                    engine._watch_fail_counts.pop(info["local"], None)
            else:
                retry_fail += 1
                if not hasattr(engine, '_watch_fail_counts'):
                    engine._watch_fail_counts = {}
                engine._watch_fail_counts[info["local"]] = engine._watch_fail_counts.get(info["local"], 0) + 1
                engine.log.warn(f"[watch] 单文件重试失败: {rp}: {r1.get('error','')}")
        if retry_ok:
            engine.log.info(f"[watch] 降级单文件推送: 成功 {retry_ok}, 失败 {retry_fail}")
            # 🆕 v4.12.6: 降级路径同样写 oplog (AI 审计: 守护降级同步了什么)
            # v4.36: 统一走 _append_oplog, 同时上传服务端
            try:
                _wa = {"n": retry_ok, "mode": "fallback_single",
                       "files": [f["path"] for f in files if f["path"] in local_map][:20]}
                _we = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "ts_epoch": int(time.time()),
                       "op_id": f"{int(time.time() * 1000)}-{os.getpid()}-{os.urandom(4).hex()}",
                       "command": "watch", "args": _wa,
                       "summary": f"守护同步(降级): 成功 {retry_ok} 个, 失败 {retry_fail} 个",
                       "rc": 0 if retry_fail == 0 else 1,
                       "duration_ms": int((time.time() - _t0) * 1000)}
                _we.update(_oplog_meta())
                _append_oplog(engine.conf, _we)
            except Exception:
                pass
            # BUG-6: 降级推送成功的文件也要立即落盘 snapshot
            try: engine.snapshot.save(force=True)
            except Exception: pass
        # 部分成功的文件已更新 snapshot, 失败的下次再试

def _watch_do_pull(engine: SyncEngine) -> int:
    """轻量拉取: 获取远程 MD5 map (1 次 checksum API), 只拉取远程有变更的文件。

    与 engine.run(once=True) 不同, 此函数不做全量 ping/scan/upload,
    只做 1 次 checksum + 按需 download_file, 避免触发服务端限流 (60/min)。
    冲突策略: 两边都改时不覆盖本地, 仅警告 (与 backup_both 一致)。
    返回成功拉取的文件数。
    """
    conf = engine.conf
    watch = Path(conf["watch_dir"])
    remote_base = conf.get("remote_base", "") or ""

    # 0) 远程目录状态 (空目录 / mode): 拉文件前先补建本地缺失目录,
    #    mode 等文件全部落盘后再回填 (只读/无写权限目录不阻塞下载).
    #    节流: dir_pull_interval 秒 (0=每次 pull 都拉); 失败不影响文件拉取.
    _rdirs: list[dict] = []
    _dpi = float(conf.get("dir_pull_interval", 15.0) or 0)
    _now = time.time()
    if _dpi <= 0 or (_now - getattr(engine, "_last_remote_dir_pull", 0.0)) >= _dpi:
        engine._last_remote_dir_pull = _now
        try:
            _rdirs = _fetch_remote_dir_items(engine)
            _watch_ensure_local_dirs(engine, _rdirs)
        except Exception as de:
            engine.log.debug(f"[watch] 远程目录状态拉取异常 (下轮重试): {de}")

    # 1) 获取远程 MD5 map (1 次 API)
    rmap = engine._fetch_remote_md5_map(force=True)
    if not rmap:
        engine.log.debug("[watch] pull: 远程 MD5 map 为空, 跳过")
        if _rdirs:
            try: _watch_apply_remote_dir_modes(engine, _rdirs)
            except Exception as de:
                engine.log.debug(f"[watch] 远程目录权限回填异常: {de}")
        return 0
    engine.log.info(f"[watch] pull: 远程文件数={len(rmap)}, remote_base={remote_base}")

    # 2) 扫描本地文件 (本地操作, 无 API)
    mon = scan_watch_dir(watch, remote_base, conf["ignore"], conf["allow_ext"])
    local_by_remote: dict[str, dict] = {m["remote"]: m for m in mon}
    engine.log.info(f"[watch] pull: 本地文件数={len(mon)}, 本地remote路径={list(local_by_remote.keys())[:5]}")

    # 3) 找出需要拉取的文件
    to_pull: list[tuple[str, str, str, dict]] = []
    conflicts = 0
    for rp, rinfo in rmap.items():
        if not isinstance(rinfo, dict):
            continue
        remote_md5 = rinfo.get("md5", "")
        if not remote_md5:
            continue
        _is_link = rinfo.get("type") == "symlink"
        if _is_link and (rinfo.get("safe") is False or not _remote_link_target(rinfo)):
            engine.log.warn(f"[watch] 跳过不安全的远程软链接: {rp} ({rinfo.get('reason') or 'unsafe'})")
            continue
        # 修复 BUG-3 (文件错位): 远程路径 rp 可能含 remote_base 前缀, 拼本地路径时必须剥离,
        #   否则文件被拉到 watch_dir/remote_base/... 子目录而非 watch_dir 根, 下一轮又判定"远程有本地无", 反复拉取无法收敛.
        #   与 cmd_pull 保持一致: rp 以 remote_base+"/" 开头时去掉前缀.
        if remote_base and rp.startswith(remote_base + "/"):
            local_rel = rp[len(remote_base) + 1:]
        else:
            local_rel = rp
        # 跳过匹配 IGNORE 规则的文件 (如 ai-sync/*, .sync_*, *.log 等)
        if should_ignore(str(watch / local_rel), conf["ignore"], conf["allow_ext"], watch_dir=watch):
            continue
        m = local_by_remote.get(rp)
        if m:
            # 本地有此文件
            # 修复 BUG-8 (fake md5): scan_watch_dir 的 fast 模式 md5="f:size:mtime_ns" 是占位符不是真 hash,
            #   拿来与 remote_md5 (真 hash) 比对必然不等 → "远程一致"分支永远走不到 → 误判冲突.
            #   fast md5 标识符为 "f:" 开头, 命中时必须重算真 md5.
            # FIX-symlink: 本地软链接的 md5 是链接目标规范化 hash, 不能读目标内容.
            if m.get("is_symlink"):
                local_md5 = m.get("md5", "")
            else:
                raw_md5 = m.get("md5", "")
                if raw_md5 and not raw_md5.startswith("f:"):
                    local_md5 = raw_md5
                else:
                    local_md5 = md5_file(Path(m["local"]))
            if remote_md5.lower() == local_md5.lower() and (not _is_link or m.get("is_symlink")):
                # 已一致, 更新 snapshot 以保持追踪 (用真 md5 覆盖 fake)
                _entry = {
                    "size": m["size"], "sha1": m["sha1"], "md5": local_md5,
                    "remote": rp, "mtime": m.get("mtime", 0.0),
                    "mode": m.get("mode", 0), "source": m.get("source", ""),
                }
                if m.get("is_symlink"):
                    _entry.update({"is_symlink": True, "link_target": m.get("link_target"),
                                   "link_target_rel": m.get("link_target_rel")})
                engine.snapshot.update(m["local"], _entry)
                continue
            # MD5 不一致: 判断是远程变了还是本地变了
            snap = engine.snapshot.get(m["local"])
            snap_md5 = snap.get("md5", "") if snap else ""
            # 修复 BUG-8: snap_md5 可能也是历史写入的 fake md5, 不能用来比对, 只在真 hash 时参与判断
            snap_md5_valid = snap_md5 and not snap_md5.startswith("f:")
            if snap_md5_valid and snap_md5.lower() == local_md5.lower():
                # 本地未变, 远程变了 → 拉取
                to_pull.append((rp, m["local"], remote_md5, rinfo))
            elif snap_md5_valid and snap_md5.lower() == remote_md5.lower():
                # 远程未变, 本地变了 → 上传侧处理, 跳过
                continue
            else:
                # 两边都变了 / snap 是 fake 无法判断 → 冲突, 不覆盖本地
                conflicts += 1
                engine.log.warn(f"[watch] 冲突(两边都改): {rp}, 跳过拉取 (本地保留)")
        else:
            # 本地没有此文件 → 检查是否是本地主动删除 (snapshot 有记录但文件不存在)
            local_path = str(watch / local_rel)
            snap_entry = engine.snapshot.get(local_path)
            if snap_entry and not os.path.lexists(local_path):
                # snapshot 有记录但本地文件已删除 → 用户主动删除, 不拉回
                # 如果 DELETE_SYNC=true, 上传侧会通知远程删除; 否则保留远程副本
                engine.log.debug(f"[watch] 本地已删除, 跳过拉取: {rp}")
                continue
            # 真正的远程新增 → 拉取
            to_pull.append((rp, local_path, remote_md5, rinfo))

    if not to_pull and conflicts == 0:
        if _rdirs:
            try: _watch_apply_remote_dir_modes(engine, _rdirs)
            except Exception as de:
                engine.log.debug(f"[watch] 远程目录权限回填异常: {de}")
        return 0

    # 4) 拉取变更文件 (每个文件 1 次 download_file API)
    pull_ok = pull_fail = 0
    for rp, local_path, remote_md5, rinfo in to_pull:
        if not engine.running:
            break  # SIGTERM/SIGINT: 优雅中断拉取
        lp = Path(local_path)
        if rinfo.get("type") == "symlink":
            # FIX-link-b64: 优先用 b64 还原非 UTF-8 目标
            _lt = _remote_link_target(rinfo)
            if not isinstance(_lt, str) or not _lt or rinfo.get("safe") is False:
                engine.log.warn(f"[watch] 跳过不安全的远程软链接: {rp}")
                pull_fail += 1
                continue
            _rtr = _remote_link_target_rel(rinfo)
            _ltr = _remote_target_rel_to_watch(_rtr, remote_base)
            if _rtr is not None and _ltr is None:
                engine.log.warn(f"[watch] 跳过链接目标不在同步子树内的软链接: {rp} -> {_safe_text(_lt)}")
                continue
            ok, det, _skip = create_local_symlink(
                lp, _lt, _ltr, remote_md5,
                engine.snapshot, engine.backup, watch, engine.log,
                remote_path=rp, mtime=rinfo.get("mtime"))
        else:
            ok, det = engine.puller.pull_file(
                lp, rp, remote_md5, engine.snapshot,
                dry_run=False,
                remote_mtime=rinfo.get("mtime"),
                remote_mode=rinfo.get("mode"),
            )
        flag = "✅" if ok else "❌"
        engine.log.info(f"  [watch] {flag} ↓ PULL {Path(rp).name}  {det}")
        if ok:
            pull_ok += 1
            # BUG-6 修复 (中断状态丢失): 每拉取成功 1 个文件就立即落盘 snapshot.
            # 否则守护崩溃/被杀时, 已拉取文件的 snapshot 还是旧状态, 下轮守护会误判"本地变了"
            # → 把刚拉下来的远程版本推回去 → 远程变更被本地旧版本覆盖 (数据丢失!).
            # 每 3 个文件存一次 (兼顾性能与安全, 最多丢 2 个文件状态).
            if pull_ok % 3 == 0:
                try: engine.snapshot.save(force=True)
                except Exception: pass
        else:
            pull_fail += 1

    # 目录 mode 最后回填: 文件已全部落盘, 只读目录不会阻塞下载
    if _rdirs:
        try: _watch_apply_remote_dir_modes(engine, _rdirs)
        except Exception as de:
            engine.log.debug(f"[watch] 远程目录权限回填异常: {de}")
    # BUG-6: 拉取结束 (无论正常结束还是 SIGINT 中断) 都强制落盘一次
    if pull_ok > 0:
        try: engine.snapshot.save(force=True)
        except Exception: pass
    if pull_ok or pull_fail or conflicts:
        engine.log.info(f"[watch] pulled ok={pull_ok} fail={pull_fail} conflicts={conflicts}")
    return pull_ok

# ---------------------------------------------------------------------------
# P1-C: --smart 内容对比跳过 独立命令 (用户显式调用)
# ---------------------------------------------------------------------------
def cmd_smart_diff(conf: dict, log: Logger, api: ApiClient, paths: list[str],
                   strategy: str = "auto", apply_push: bool = False,
                   dry_run: bool = False) -> int:
    """显式对 N 个路径做 smart 比对，打印跳过列表。
    apply_push=True 时对差异部分执行 batch_deploy。
    """
    watch = Path(conf["watch_dir"])
    api.remote_base = conf.get("remote_base", "") or ""
    api._watch_dir = watch
    # 构建 files payload (🆕 v4.12-FIX: 支持目录参数, 递归收集文件 + IGNORE/扩展名过滤;
    #   旧行为: 目录被当"不存在"跳过 → total=0 假成功, AI 误以为已比对)
    files: list[dict] = []
    _collected: list[Path] = []
    for p in paths:
        local = Path(p)
        if not local.is_absolute(): local = watch / local
        if local.is_dir():
            for _f in sorted(local.rglob("*")):
                if _f.is_file() and not should_ignore(str(_f), conf["ignore"], conf["allow_ext"],
                                                      watch_dir=watch):
                    _collected.append(_f)
        elif local.is_file():
            if should_ignore(str(local), conf["ignore"], conf["allow_ext"], watch_dir=watch):
                print(f"⚠  被 IGNORE 规则跳过: {local}")
            else:
                _collected.append(local)
        else:
            print(f"⚠  跳过不存在: {local}")
    for local in _collected:
        data = local.read_bytes()
        rel = local.relative_to(watch).as_posix()
        rp = _resolve_remote_path(conf, rel)
        files.append({
            "path": rp,
            "content_b64": base64.b64encode(data).decode(),
            "mode": oct(local.stat().st_mode & 0o777)[2:].zfill(4),
        })
    n_total = len(files)
    if strategy == "auto":
        strategy = "md5" if n_total <= 20 else "size_mtime"
    filtered = api._smart_filter_files(files, strategy=strategy)
    skipped = n_total - len(filtered)
    # 🆕 v4.12-JSON: --json 时输出结构化 (对齐 push/pull dry-run)
    _json_mode = "--json" in sys.argv
    _up_files = [{"path": f["path"],
                  "size": len(base64.b64decode(f["content_b64"])) if f.get("content_b64") else 0}
                 for f in filtered]
    if _json_mode:
        print(json.dumps({"command": "smart", "strategy": strategy, "ok": True,
                          "total": n_total,
                          "skipped": skipped, "to_upload": len(filtered),
                          "dry_run": dry_run, "files": _up_files}, ensure_ascii=False))
        return 0
    print(f"--smart strategy={strategy}  total={n_total}  跳过未变更={skipped}  需上传={len(filtered)}")
    if skipped:
        # 列出跳过项 (从 payload 推导)
        # 为日志目的打印本地相对路径
        pass
    if not filtered:
        print("✅ 全部文件未变更，无需上传")
        return 0
    # 列出将要上传的文件
    for f in filtered:
        rp = f["path"]
        size = len(base64.b64decode(f["content_b64"])) if f.get("content_b64") else 0
        print(f"  ↑ {rp}  ({size//1024}KB)")
    if apply_push and not dry_run:
        pre = ""
        if any(f["path"].endswith(".php") for f in filtered):
            pre = "php -l {file}"
        r = api.batch_deploy(filtered, pre_check=pre, post_check="", rollback_on_fail=True, smart_skip=False)
        if r.get("ok"):
            print(f"\n✅ 已推送到远程, deploy_id={r.get('deploy_id')}")
            if r.get("backed_up"):
                print("  备份:")
                for k, v in r["backed_up"].items():
                    print(f"    {k} → {v}")
        else:
            print(f"\n❌ 推送失败: code={r.get('code')} err={r.get('error','')} msg={r.get('message','')}")
            return 2
    elif dry_run:
        print("\n(dry-run 模式，未实际上传)")
    return 0

def cmd_batch_rollback(conf: dict, log: Logger, api: ApiClient, args_list: list) -> int:
    """按时间点批量回滚: [unix_ts, file1?, file2?, ...]"""
    if not args_list:
        print("用法: --batch-rollback <unix_ts> [file1 file2 ...]"); return 2
    try:
        ts = int(args_list[0])
    except ValueError:
        print("❌ 第一个参数必须是 unix 时间戳"); return 2
    paths = [_resolve_remote_path(conf, f) for f in args_list[1:]] if len(args_list) > 1 else None
    from datetime import datetime
    try:
        ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        print(f"❌ 时间戳超出有效范围: {ts} (需为 Unix 秒级时间戳, 如 $(date +%s))")
        return 2
    print(f"⏪ 批量回滚到 {ts_str} (ts={ts})")
    if paths:
        print(f"  指定文件: {len(paths)} 个")
    else:
        print(f"  扫描所有有备份的文件")
    _scope = f"{len(paths)} 个指定文件" if paths else "全部有备份的文件"
    if not _confirm_danger("批量时间点回滚", f"将回滚到 {ts_str} 之前的版本, 范围: {_scope}", irreversible=not paths):
        return 99  # v4.29: 阻断退出码对齐契约
    r = api.batch_rollback(ts, paths)
    if not r.get("ok"):
        print(f"❌ {r.get('error','')} {r.get('message','')}")
        if r.get("fix_hint"): print(f"💡 {r['fix_hint']}")
        return 2
    print(f"\n{r.get('summary','')}")
    for item in r.get("results", []):
        path = item.get("path","")
        if item.get("ok"):
            print(f"  ✅ {path:<40} ← {item.get('restored_from','')}")
        else:
            print(f"  ❌ {path:<40} {item.get('error','')}")
    return 0 if r.get("failed_count",0) == 0 else 1

def cmd_list_since(conf: dict, log: Logger, api: ApiClient, ts_str: str, plain: bool = False) -> int:
    """列出某时间戳后变更的文件"""
    try:
        ts = int(ts_str)
    except ValueError:
        # 支持日期格式
        from datetime import datetime
        try:
            ts = int(datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp())
        except ValueError:
            print("❌ 时间格式: unix 时间戳 或 'YYYY-MM-DD HH:MM:SS'"); return 2
    from datetime import datetime
    ts_dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    # BUG: remote_base 为空时传 "" 会被服务端判 invalid_path; 与 --ls 一致传 "."
    remote_base = conf.get("remote_base", "").strip("/") or "."
    r = api.list_since(remote_base, ts)
    if not r.get("ok"):
        if plain:
            print(f"ok=false error={r.get('error','')} {r.get('message','')}")
        else:
            print(f"❌ {r.get('error','')} {r.get('message','')}")
        return 2
    items = r.get("items", [])
    if "--brief" in sys.argv:
        # 🆕 FIX-brief-v4.17: 只输出 count + 前 5 条 (决策用)
        if "--json" in sys.argv:
            print(json.dumps({"command": "list-since", "ok": True, "since": ts_dt, "count": len(items),
                              "items": [{"path": it.get("path", ""), "size": it.get("size", 0)}
                                        for it in items[:5]]}, ensure_ascii=False))
        else:
            print(f"count={len(items)} since={ts_dt} sample={[it.get('path','') for it in items[:3]]}")
        return 0
    if "--json" in sys.argv:
        # 🆕 FIX-json-v4.17: --json 结构化变更清单
        print(json.dumps({"command": "list-since", "ok": True, "since": ts_dt, "count": len(items),
                          "items": [{"path": it.get("path", ""), "size": it.get("size", 0),
                                     "mtime": it.get("mtime", 0)} for it in items[:200]]},
                         ensure_ascii=False))
        return 0
    if plain:
        # 🆕 FIX-plain-v4.6: --list-since --plain 每文件一行 (AI 拿清单直接决定 pull)
        for it in items[:200]:
            print(f"path={it.get('path','')} size={it.get('size',0)} mtime={it.get('mtime',0)}")
        print(f"count={len(items)} since={ts_dt}")
        return 0
    print(f"📋 {ts_dt} 之后变更的文件 ({len(items)} 个)")
    if not items:
        print("  无变更"); return 0
    print(f"  {'路径':<50} {'大小':<10} {'修改时间'}")
    print(f"  {'-'*50} {'-'*10} {'-'*20}")
    from datetime import datetime
    for it in items:
        path = it.get("path","")[:50]
        size = f"{it.get('size',0)}B"
        mt = datetime.fromtimestamp(it.get("mtime",0)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {path:<50} {size:<10} {mt}")
    return 0

# ---------------------------------------------------------------------------
# 14. 主入口
# ---------------------------------------------------------------------------

def _lock_holder_hint(conf: dict) -> str:
    """探测 .sync.lock 当前持有者 (读 pid 文件), 用于锁冲突报错提示. 找不到则返回空串."""
    try:
        lp = Path(conf["watch_dir"]) / ".sync.lock"
        if not lp.exists():
            return ""
        txt = lp.read_text(encoding="utf-8", errors="ignore").strip()
        if not txt:
            return ""
        m = re.search(r"pid=(\d+)", txt)
        if not m:
            return ""
        pid = int(m.group(1))
        alive = True
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            alive = False
        if alive:
            return f" (持有者 pid={pid} 存活, 疑似 --watch 守护)"
        return f" (锁文件残留 pid={pid}, 进程已退出)"
    except Exception:
        return ""

def _acquire_op_lock(conf: dict, log: Logger, op_name: str, timeout_s: float = 30.0,
                     fast_fail: bool = False) -> FileLock | None:
    """BUG-4 修复: --push/--pull/--deploy 等写操作获取 .sync.lock,
    与 --watch 守护互斥 (守护每轮也 acquire 同一把锁).
    timeout_s 内拿不到则返回 None (调用方自行决定降级或退出).
    fast_fail=True (方案2, push 单文件场景): 非阻塞试一次, 拿不到立即返回 None,
    不空等 30s —— 手动 push 遇到 watch 在跑时, 快速失败让用户知道要等 watch 或停掉守护.
    AI 视角: 拿不到锁时给清晰提示, 而不是静默失败."""
    lock_path = Path(conf["watch_dir"]) / ".sync.lock"
    lock = FileLock(lock_path)
    if fast_fail:
        if lock.acquire(blocking=False):
            return lock
        log.info(f"锁被占用, 快速失败: {op_name} {_lock_holder_hint(conf)}")
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if lock.acquire(blocking=False):
            return lock
        # 守护正在同步, 等一小段再试
        time.sleep(0.3)
    return None


# ==================== v4.15: 分层 AI 帮助 (渐进式发现) ====================
# 新 AI 视角: 默认 --help-ai = 精简能力地图(500 token 内选型);
# 决定用某命令后 --help-ai <cmd> 查单命令详情; --help-ai full 查完整 40+ 命令清单.

def _build_ai_detail(name: str) -> dict | None:
    """从完整命令库提取单命令详情 (参数/示例/所在分类/输出契约/错误码/通用flag提示)."""
    # FIX-v4.32: 带值参数/通用flag也可用 --help-ai 查询 — 此前 start_here 指引第一步
    #   --workdir 自举, 但 help-ai 词典无此条目报"未知命令" → 新AI引导链断裂死循环;
    #   补齐高频带值参数与布尔flag, 查询时返回结构化详情(不改变命令本身行为).
    _param_docs = {
        "workdir": {"kind": "带值参数", "args": "<DIR>", "desc": "自举第一步: 指定稳定工作目录并回写 sync.conf (目录须持久如 ~/ai-sync-client, 勿放 /tmp; 指定后后续命令无需再带; 💡换新包后 --workdir 指定原目录即复用已有工作区与配置)", "example": "--workdir /稳定/目录", "note": "非独立命令, 是全局带值参数; 无参直接调用会报 code=2 参数错误 — 必须带目录"},
        "conf":     {"kind": "带值参数", "args": "<PATH>", "desc": "配置文件路径(默认 sync.py 同目录 sync.conf; 可用环境变量 AI_SYNC_CONF)", "example": "--conf /路径/sync.conf"},
        "scope":    {"kind": "带值参数", "args": "<NAME>", "desc": "命令行覆盖配置中的 SCOPE(多账号时用)", "example": "--scope xbxbb"},
        "timeout":  {"kind": "带值参数", "args": "<SEC>", "desc": "exec 超时秒数(默认 conf EXEC_TIMEOUT, 上限 120)", "example": "--exec 'ls' --timeout 30"},
        "cwd":      {"kind": "带值参数", "args": "<DIR>", "desc": "--exec 的服务器端工作目录 (默认=WORK_DIR; 相对路径以 WORK_DIR 为基准, 目录不存在报 cwd_not_found; FIX-cwd-v4.39 起 exec 默认 cwd 已对齐 WORK_DIR)", "example": "--exec 'pwd' --cwd /var/www"},
        "dry-run":  {"kind": "布尔flag", "args": "", "desc": "试运行(不实际写服务器, 输出将执行的操作)", "example": "--push --dry-run"},
        "yes":      {"kind": "布尔flag", "args": "", "desc": "危险操作免确认(如 --rm 需配合)", "example": "--rm test.php --yes"},
        "json":     {"kind": "布尔flag", "args": "", "desc": "结构化JSON输出(顶层 {ok,code,stdout,stderr,command})", "example": "--status --json"},
    }
    if name in _param_docs:
        _d = dict(_param_docs[name]); _d["command"] = name
        return _d
    full_cats = {
        "文件同步": {"push": {"args": "[file...]", "desc": "推送本地变更到远程(不指定=推全部); 自动基线冲突检测; --show-diff 逐行差异; conf 可设 PUSH_CONFLICT=ask|block|force; 💡单文件快速改配置用 push, 多文件上线/需校验回滚用 --deploy", "example": "--push TV/config.php --show-diff"},
                     "pull": {"args": "[file] [--no-verify]", "desc": "从远程拉取(本地有改动自动备份); --no-verify 跳过落盘 MD5 回读校验", "example": "--pull --no-verify"},
                     "pull-smart": {"args": "[file]", "desc": "增量拉取(MD5比对,只下载变更)", "example": "--pull-smart"},
                     "deploy": {"args": "<file...>", "desc": "原子部署(lint+verify+失败自动回滚); --http-check URL --expect 串 部署后断言页面内容; 💡多文件上线/需校验回滚用 deploy, 单文件快速改配置用 --push", "example": "--deploy TV/config.php --http-check https://x/ --expect 播放器"},
                     "sync": {"args": "<file>", "desc": "单文件同步(推送本地单文件到远程)", "example": "--sync TV/config.php"},
                     "auto": {"args": "pull|save [files...]|fix|status", "desc": "意图模式(最高层封装): pull=增量拉取, save=lint预检+部署+md5复验, fix=doctor一键排查, status=同步状态", "example": "--auto save TV/config.php --yes"},
                     "workdir": {"args": "<DIR>", "desc": "覆盖本地工作目录(优先级最高); 自举时指定本地同步根目录", "example": "--workdir /workspace/xbxbb"},
                     "smart": {"args": "<path...>", "desc": "智能比对(内容一致跳过,减少传输; 支持目录递归)", "example": "--smart TV/assets/js/*.js"}},
        "远程执行": {"exec": {"args": "[cmd]", "desc": "远程执行命令: 不带 cmd 时从 stdin 读取(推荐 heredoc, $/引号/多行全原样免转义); 带 cmd 为旧直传(注意 $ 本地展开); rc!=0 返回码3; --cwd 指定工作目录", "example": "--exec <<'CMD'\nls -la\nCMD"},
                     "exec-file": {"args": "<file>", "desc": "执行服务器脚本文件(文件放当前工作目录, 用相对路径)", "example": "--exec-file run.sh"},
                     "exec-b64": {"args": "<b64>", "desc": "执行base64编码的命令(防特殊字符)", "example": "--exec-b64 bHMgLWxh"}, "shell": {"args": "[cmd]", "desc": "持久shell会话(对齐ssh: cwd/export跨调用保持, --shell-env持久化); ⚠️保护边界: 服务端拦毁灭命令(rm -rf /、dd写盘、mkfs、fork炸弹、关机重启, 给替代方案); 客户端额外拦系统根目录自身(rm -rf /etc等); rm -rf <业务目录>/mv覆盖照常执行只提醒 — AI 使用前自行确认目标路径", "example": "--shell 'ls -la' --shell-env MYVAR=1"}},
        "调试排查": {"doctor": {"args": "", "desc": "环境健康检查", "example": "--doctor"},
                     "error-log": {"args": "<name>", "desc": "查看服务器错误日志(--error-grep过滤, --error-since按时间戳)", "example": "--error-log php_error --error-grep 'fatal'"},
                     "error-lines": {"args": "<N>", "desc": "日志行数(配合--error-log使用)", "example": "--error-log php_error --error-lines 100"},
                     "error-grep": {"args": "<关键词>", "desc": "日志关键词过滤(配合--error-log使用)", "example": "--error-log php_error --error-grep Fatal"},
                     "lint": {"args": "<path>", "desc": "语法检查(JS/TS本地node --check, 其他服务端lint; --fix自动修复)", "example": "--lint TV/config.php"},
                     "lint-batch": {"args": "<dir>", "desc": "批量语法检查", "example": "--lint-batch TV/"},
                     "test": {"args": "<path>", "desc": "服务器安全执行PHP(⚠️执行【远程】文件, 本地文件先--upload-file再--test; lint+沙箱)", "example": "--test tests/run.php"},
                     "status": {"args": "", "desc": "同步状态(dirty/missing清单)", "example": "--status"},
                     "replace": {"args": "<file> --old S --new S", "desc": "远程就地替换字符串(一步到位: 读+改+CAS回写+备份+lint+回读验证; 0处匹配会报错而非静默成功; --regex正则, --count N限定处数防误伤); 💡只改几处字符串用 replace, 整文件大改用 read-remote→write-remote, 本地新建文件用 upload-file", "example": "--replace TV/config.php --old 'db=old' --new 'db=new'"},
                     "batch": {"args": "", "desc": "批量执行: stdin 传 argv 数组, 同进程逐条跑(省每条冷启动, 但每条仍一次 HTTP)", "example": "echo '[[\"--ls\"],[\"--cat\",\"a.txt\"]]' | sync.py --batch"},
                     "pipeline": {"args": "", "desc": "批量执行(比 batch 更快): stdin 传 action 对象数组, 一次 HTTP 往返跑完所有步骤, 省 N 次网络往返(生产约省 70%)", "example": "echo '[{\"action\":\"stat\",\"path\":\"a.php\"}]' | sync.py --pipeline"},
                     "selfcheck": {"args": "", "desc": "连通性+服务端能力探测(新AI第一步)", "example": "--selfcheck"},
                     "verify": {"args": "[path]", "desc": "端到端三方一致性验证(本地/远程/快照MD5)", "example": "--verify"},
                     "diff": {"args": "[file]", "desc": "对比本地与远程差异(需push/pull/冲突清单)", "example": "--diff TV/config.php"},
                     "health": {"args": "", "desc": "服务器健康摘要: 磁盘/内存/负载/进程数(快速判断服务端是否异常)", "example": "--health"},
                     "check-update": {"args": "", "desc": "检测本地客户端与服务器分发版版本是否一致", "example": "--check-update"},
                     "self-update": {"args": "", "desc": "强制从服务端更新本地客户端(与自动升级同链路, read 权限, 无需 admin; 已是最新则提示)", "example": "--self-update"},
                     "preflight": {"args": "<file1> [file2...]", "desc": "批量上线前检查: 语法预检+冲突检测, 不实际部署", "example": "--preflight TV/a.php TV/b.php"},
                     "spider": {"args": "<URL>", "desc": "抓取任意 URL 内容(HTTP/JSON/HTML)给 AI 分析; 配套 --sp-method/--sp-headers/--sp-body/--sp-remote/--sp-timeout", "example": "--spider https://x.com/api"},
                     "http-check": {"args": "<URL>", "desc": "端到端验证: 抓取页面并断言包含期望内容(--expect <str> 缺省仅断言 HTTP 可达; 也可作 --deploy 配套 flag)", "example": "--http-check https://x.com --expect OK"},
                     "sync-check": {"args": "<file...>", "desc": "同步决策(该不该推)", "example": "--sync-check a.php"}},
        "备份回滚": {"backup-list": {"args": "[file]", "desc": "列出历史备份", "example": "--backup-list TV/config.php"},
                     "rollback": {"args": "<file>", "desc": "从备份恢复文件(--backup指定备份名)", "example": "--rollback TV/config.php --backup 20261013_120000 --yes"},
                     "batch-rollback": {"args": "<ts> [file...]", "desc": "按时间戳批量回滚(ts=unix秒级, 用$(date +%s))", "example": "--batch-rollback $(date -d '2026-10-23 14:00' +%s) TV/"},
                     "recent-changes": {"args": "[N]", "desc": "服务器最近变更记录", "example": "--recent-changes 5"},
                     "list-since": {"args": "<ts>", "desc": "列出指定时间戳后变更的文件", "example": "--list-since 1700000000"},
                     "history": {"args": "", "desc": "本地变更历史", "example": "--history"}},
        "目录文件操作": {"ls": {"args": "[path]", "desc": "列远程目录", "example": "--ls TV/assets/"},
                     "cat": {"args": "<path>", "desc": "查看远程文件内容(--json直接拿content; --range START END行切片)", "example": "--cat TV/config.php --range 1 50"},
                     "rm": {"args": "<path...>", "desc": "删除远程文件/目录(支持多文件, 需--yes)", "example": "--rm test.php --yes"},
                     "mkdir": {"args": "<path>", "desc": "创建远程目录", "example": "--mkdir TV/newdir/"},
                     "mv": {"args": "<from> <to>", "desc": "重命名/移动远程文件", "example": "--mv old.php new.php"},
                     "cp": {"args": "<from> <to>", "desc": "复制远程文件", "example": "--cp a.php b.php"},
                     "info": {"args": "<path>", "desc": "远程文件元信息(type/size/md5/mode)", "example": "--info TV/config.php"},
                     "read-remote": {"args": "<path> [...]", "desc": "下载远程文件到本地缓存(只读; 多文件批量; 改缓存后--write-remote回传); 💡整文件编辑走 read-remote→改缓存→write-remote", "example": "--read-remote TV/config.php TV/db.php"},
                     "read-range": {"args": "<path> <start> <end>", "desc": "[别名] = --cat <path> --range <start> <end>", "example": "--read-range TV/config.php 1 50"},
                     "write-remote": {"args": "<path> [...]", "desc": "回传缓存文件到远程(CAS+自动备份+回读验证+JS/HTML语法校验; 多文件批量)", "example": "--write-remote TV/config.php"},
                     "edit-remote": {"args": "<path>", "desc": "[别名] = read-remote (下载-改缓存-回传工作流)", "example": "--edit-remote TV/config.php"},
                     "upload-file": {"args": "<本地文件> [...]", "desc": "本地文件直传远程(⚠️文件先放WATCH_DIR下, 参数=相对WATCH_DIR路径; 自动写缓存+备份+回读验证; 多文件批量); 💡本地新建文件上传用这个", "example": "--upload-file Test/new.html"},
                     "grep-remote": {"args": "<pattern>", "desc": "[别名] = --search <p> --any-ext (任意扩展名检索)", "example": "--grep-remote sfxClear --grep-path Test/"}},
        "搜索统计": {"search": {"args": "<pattern>", "desc": "远程文件内容搜索(默认仅*.php/*.py/*.js; --any-ext 不限扩展名)", "example": "--search 'function test' --any-ext"},
                     "stats": {"args": "<path>", "desc": "代码行数统计", "example": "--stats TV/"},
                     "snippets": {"args": "", "desc": "列出服务器已配置代码片段", "example": "--snippets"},
                     "todo": {"args": "[path]", "desc": "提取TODO/FIXME/HACK注释", "example": "--todo TV/"}},
        "守护模式": {"watch": {"args": "start|status|stop", "desc": "守护模式(⚠️start为前台阻塞守护占终端, 真后台用setsid nohup; --watch-interval/--watch-mode/--watch-subdir/--once)", "example": "--watch start --watch-interval 2"}},
        "管理": {"whoami": {"args": "", "desc": "当前token权限边界", "example": "--whoami"},
                 "admin": {"args": "[subcmd]", "desc": "管理操作(stats/config/scopes/tokens)", "example": "--admin stats"},
                 "token-add": {"args": "<token> <name> <scopes> <perms>", "desc": "创建令牌(需admin)", "example": "--token-add xxx name scope read,write"},
                 "token-del": {"args": "<token>", "desc": "删除令牌(需admin)", "example": "--token-del xxx"},
                 "scope-add": {"args": "<name> [work_dir...]", "desc": "创建scope(需admin)", "example": "--scope-add newscope /data/www"},
                 "scope-del": {"args": "<name>", "desc": "删除scope(需admin)", "example": "--scope-del newscope"},
                     "api-help": {"args": "[ACTION]", "desc": "服务端 API 自描述: 不带值列全部 action 及必填参数; 带 action 名看完整契约(必填/可选/返回/示例/错误码) —— 查服务端能力用这个", "example": "--api-help upload"},
                 "package": {"args": "<token> [scope...]", "desc": "打包客户端zip(需admin)", "example": "--package xxx"}},
    }
    for cat_name, cmds in full_cats.items():
        if name in cmds:
            return {"command": name, "category": cat_name, **cmds[name],
                    "output_contract": f"默认 JSON 输出 (conf JSON_DEFAULT=false 或 --plain 回退文本); 顶层 {{ok,code,stdout,stderr,command}}; 失败原因在 stderr",
                    "exit_codes": {"0": "成功", "2": "参数/文件错误", "99": "策略阻断(危险路径/锁)"},
                    "flags_hint": "通用: --json 结构化(默认) | --plain 纯文本 | --dry-run 试运行 | --yes 免确认 | --conf <路径> 指定配置"}
    return None


def _help_ai_overview() -> dict:
    """精简能力地图: 新 AI 500 token 内完成选型 (全 8 族可达决策树 + 启动三步 + 契约)."""
    return {
        "tool": "ai-sync", "version": APP_VERSION,
        "what": "本地工作区 ↔ 远程服务器 双向同步客户端 (AI 脚本化调用, 全程 --json 结构化)",
        "start_here": ["python3 sync.py --workdir /稳定/目录 (自举: 配置稳定工作目录, 末行 READY; 同步工具目录也应持久如 ~/ai-sync-client, 勿放 /tmp)",
                       "--auto pull (首次拉代码) / --auto save [files] --yes (部署) / --auto fix (排查)",
                       "--selfcheck 探测服务端能力 (HTTP 直连时服务端自带自描述: POST {\"action\":\"help\"} 拿全部 action 契约)", "--whoami 确认权限边界"],
        "ai_usage": {
            "命令速选(先看这行)": "开工拉取 --auto pull; 收工部署 --auto save --yes; 单文件快改 --read-remote→--write-remote; 新建文件 --upload-file; 快速同步已改文件 --push <文件>; 生产发布需校验回滚 --deploy <文件> --smart-verify; 后台自动同步 --watch start --once (单次) 或 setsid nohup --watch start (常驻); 出问题排查 --auto fix; 抓接口/网页 --spider <url> --json (抓包爬虫)",
            "省参数": "export AI_SYNC_CONF=/路径/sync.conf 后, 命令可省略 --conf (所有命令自动读该环境变量)",
            "输出契约": "--json 时顶层恒为 {ok,code,stdout,stderr,command}; 成功时 stdout 为单行合法 JSON (已 merge 到顶层); 失败时 stdout 可能为人类文案, 错误在 stderr — 断言用顶层 ok/code",
            "两条改码路径": "①工作区: 本地 WATCH_DIR 文件改后 --push/--deploy (适合整个项目工作流); ②缓存: --read-remote 下载到 ~/.ai-sync-remote/ 缓存改后 --write-remote 回传 (适合单文件快改, 自动CAS冲突检测+备份+回读). 新文件用 --upload-file",
            "完整工作流示例": "①看代码: --search 关键词 / --cat <文件> → ②改代码: 工作区本地改 或 --read-remote 缓存改 → ③部署: --diff 预览 → --deploy --smart-verify (md5复验) → ④端到端验证: --verify [path] / --http-check <url> --expect <内容> → ⑤回滚: --backup-list <file> → --rollback <file> --backup <名> --yes",
            "部署后验证": "--deploy --smart-verify md5 复验哈希; 需要断言页面内容用 --http-check <url> --expect <字符串> (内置抓取, 无需手写curl)",
        },
        "decision_tree": {
            "首次接入": {
                "一条命令": "python3 sync.py --workdir /稳定/目录 (自举: 自动建工作区/日志跟随/SSL降级/回写 sync.conf, 末行 READY; 同步工具目录应持久如 ~/ai-sync-client)",
                "日常三条": "--auto pull (增量拉取) | --auto save [files] --yes (lint-first+deploy+md5复验) | --auto fix (doctor 一键排查)",
            },
            "高频开发": {
                "看远程文件内容": "--cat <path> [--json | --range START END | --full | --brief] (>4MB 先探大小走服务端按行切片, 只回 200 行/--brief 30 行; 二进制只回元信息不下载)",
                "找远程代码": "--search <p> [--any-ext] [--grep-path <dir>] [--grep-regex]",
                "改远程文件": "--read-remote <p> → (改缓存) → --write-remote <p> (只改几处字符串用 --replace <p> --old X --new Y 一步完成; 本地新建文件用 --upload-file)",
                "创建远程新文件": "--upload-file <本地文件> [...]",
                "批量同步": "--diff 预览 → 选命令: 快速推本地已改文件用 --push <路径>; 上线发布要 lint+备份+回滚+md5复验用 --deploy --smart-verify (判据: 需校验回滚=deploy, 仅同步=push)",
                "语法检查": "--lint <p> | --lint-batch <dir>",
                "跑远程命令": "--exec <<'CMD' …命令… CMD (heredoc 免转义; 多步连续/需保持cwd或env → --shell)",
                "端到端验证": "--verify [path]",
                "连续只读探查(省冷启动)": "--batch: stdin 传 JSON 数组, 每项=一条命令的 argv 列表 (例: [[\"--ls\",\".\"],[\"--cat\",\"a.php\"]]); 同进程逐条执行(每条约 10ms, 单发约 220ms), 输出 {results:[{argv,rc,ms,stdout,stderr}]}; 适合 ls/cat/info/read-range/grep 连发",
            },
            "排查与回滚": {
                "环境健康": "--selfcheck | --doctor",
                "服务器日志": "--error-log <name> [--error-grep 关键词] [--error-since <ts>]",
                "最近变更": "--recent-changes [N] | --list-since <ts>",
                "回滚单文件": "--backup-list <file> → --rollback <file> --backup <名> --yes",
                "批量回滚": "--batch-rollback <ts> [path...]",
                "审计操作": "--op-log [N]",
            },
            "统计与代码质量": {
                "代码统计": "--stats <dir>",
                "TODO清单": "--todo <dir>",
                "代码片段": "--snippets",
            },
            "抓包爬虫": {
                "抓远程接口/网页": "--spider <url> [--json | --plain] [--sp-method GET|POST] [--sp-body <json>] [--sp-headers <json>] [--sp-timeout <秒>] [--sp-maxlen <字节>] (GET/POST/自定义头/body/超时/截断, --json 机器可解析)",
                "抓视频/流媒体地址走代理": "--spider <m3u8地址> --via-m3u8 (走 M3U8dl 代理, 服务器可抓任意公网流)",
                "安全": "默认拦截内网/本机 (SSRF防护), 确需抓内网加 --sp-allow-private",
            },
            "权限与管理": {
                "权限边界": "--whoami",
                "令牌管理": "--admin tokens | --token-add | --token-del",
                "作用域管理": "--admin scopes | --scope-add | --scope-del",
            },
            "自动化": {
                "守护监听": "--watch start [--watch-interval N] (start 为前台阻塞守护, AI/脚本请用 --watch start --once 单次同步, 或 setsid nohup 真后台; 一次性部署不必开)",
            },
        },
        "aliases": {"grep-remote": "--search --any-ext", "read-range": "--cat --range", "edit-remote": "--read-remote"},
        "output_contract": "--json 顶层 {ok, code, stdout, stderr, command}; 各命令结构化字段自动 merge 到顶层; 错误一律走 stderr",
        "exit_codes": {"0": "成功", "1": "不一致/环境问题", "2": "参数/文件错误", "3": "远程命令失败", "4": "部署/回滚失败", "99": "策略阻断"},
        "path_rule": "路径相对 WATCH_DIR/scope work_dir; 前导 / 不是服务器绝对路径, 会按 work_dir 虚拟根解析(/tmp/x → <work_dir>/tmp/x); 建议始终用相对路径",
        "next": {"查单命令": "--help-ai <命令名>", "查完整清单": "--help-ai full", "查服务端API契约": "--api-help [action]  (必填/可选参数/返回字段/示例/常见错误, AI 零文档接入)"},
    }


def _api_host_port(api_url: str):
    """从 API_URL 解析探测用 host/port; 解析失败返回 ("", 0)。"""
    try:
        sp = urllib.parse.urlsplit(api_url)
        host = sp.hostname or ""
        if not host:
            return "", 0
        port = sp.port or (443 if sp.scheme == "https" else 80)
        return host, port
    except Exception:
        return "", 0


def _direct_reachable(api_url: str, timeout: float = 3.0) -> bool:
    """裸 TCP 探测(不走代理), 判断目标是否可直连; 无法判定时按可达处理。"""
    host, port = _api_host_port(api_url)
    if not host:
        return True
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _tls_verify_ok(api_url: str, timeout: float = 6.0) -> bool:
    """带证书校验访问一次 API; 证书不可信返回 False, 网络不通按 True(交给后续命令报错)。"""
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(urllib.request.Request(api_url), timeout=timeout, context=ctx):
            return True
    except urllib.error.HTTPError:
        return True  # TLS 正常, 只是 HTTP 层报错
    except urllib.error.URLError as e:
        return not isinstance(getattr(e, "reason", None), ssl.SSLError)
    except ssl.SSLError:
        return False
    except Exception:
        return True


def _persist_conf_key(conf_path: Path, key: str, value: str) -> bool:
    r"""把 key=value 写回 sync.conf: 已有该键则替换, 否则追加; 失败不阻断主流程但会告警。
    FIX-conf-persist: 原实现 except:pass 静默吞错, 导致 Windows 路径 (\U \A 被当正则转义)
    回写失败却无任何提示, 表现为「--workdir 每次都要重传」。"""
    try:
        text = conf_path.read_text(encoding="utf-8")
        line = f"{key} = {value}"
        pat = rf"^\s*{re.escape(key)}\s*=.*$"
        if re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE):
            # FIX-win-path: 用 lambda 作替换体, 避免 Windows 路径里的 \U \A 等被当成 re 转义而静默失败
            text = re.sub(pat, lambda _m: line, text, count=1, flags=re.IGNORECASE | re.MULTILINE)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
        conf_path.write_text(text, encoding="utf-8")
        # SEC-conf-perm: conf 内含 token, POSIX 下收紧到 0600 (原默认 0644 任何本地用户可读)
        if os.name == "posix":
            try:
                os.chmod(conf_path, 0o600)
            except Exception:
                pass
        return True
    except Exception as _pe:
        print(f"⚠️  配置回写失败 ({key}): {_pe}\n    本次运行已生效, 但下次启动需重新指定", file=sys.stderr)
        return False



def _try_make_dir(p: Path) -> bool:
    """尝试创建并验证可写 (create + write + delete 探针)。"""
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / "._ai_sync_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _resolve_workdir(conf: dict, cli_workdir: str = "") -> str:
    """P2 工作区解析: 只接受 AI 显式指定的稳定目录, 不自动猜。
    优先级: --workdir > AI_SYNC_WORKDIR > conf 中合法路径
    不再自动猜 /workspace/<scope> / ~/ai-sync/<scope> / ./<scope> —— 实测这些非 AI 工作目录
    不稳定(可能被清理/重启丢失), 强制 AI 用 --workdir 指定一个它实际工作的稳定目录。
    安全: 跳过客户端自身目录(含 sync.py/sync.conf), 防止把 token 推上服务器。"""
    scope = (conf.get("scope") or "workspace").strip("/\\") or "workspace"
    client_dir = Path(__file__).resolve().parent
    cands = []
    if cli_workdir.strip():
        cands.append(cli_workdir.strip())
    env_dir = os.environ.get("AI_SYNC_WORKDIR", "").strip()
    if env_dir:
        cands.append(env_dir)
    cur = (conf.get("watch_dir") or "").strip()
    if cur and cur not in (".", "./"):
        cands.append(cur)
    for c in cands:
        try:
            p = Path(c).expanduser().resolve()
        except Exception:
            continue
        if p == client_dir or (p / "sync.conf").exists() or (p / "sync.py").exists():
            continue  # 客户端目录或含凭证 → 拒绝
        if p.exists() and not p.is_dir():
            continue
        if _try_make_dir(p):
            return str(p)
    return ""


def _expand_deploy_paths(conf: dict, paths: list) -> list:
    """P1: --deploy 支持目录/空参数 — 目录递归展开为文件, 空=整个 WATCH_DIR。"""
    out: list = []
    if not paths:
        paths = [conf["watch_dir"]]
    for p in paths:
        lp = Path(p)
        if not lp.is_absolute():
            lp = Path(conf["watch_dir"]) / lp
        if lp.is_dir():
            try:
                items = scan_watch_dir(lp, conf.get("remote_base") or "", conf["ignore"], conf["allow_ext"])
            except Exception:
                items = []
            out.extend(it["local"] for it in items)
        else:
            out.append(p)
    return out


def _bootstrap_env(conf: dict, conf_path: Path, cli_workdir: str = "") -> list:
    """P0: 首次运行自动适配环境, 只回写安全项(WATCH_DIR/LOG_FILE/VERIFY_SSL), 返回变更列表。"""
    changes = []
    # ① 工作区
    wd = _resolve_workdir(conf, cli_workdir)
    if wd:
        old = str(conf.get("watch_dir") or "")
        if wd != old:
            conf["watch_dir"] = wd
            _persist_conf_key(conf_path, "WATCH_DIR", f'"{wd}"')
            changes.append(f"WATCH_DIR={wd}")
    # ② 日志跟随工作区 (只有与工作区同目录的绝对路径才保留)
    raw_log = str(conf.get("log_file") or "")
    lp = Path(raw_log).expanduser() if raw_log else None
    log_ok = bool(lp) and lp.is_absolute() and lp.parent.is_dir() and wd and str(lp.parent) == str(Path(wd))
    if wd and not log_ok:
        new_log = str(Path(wd) / ".sync.log")
        if new_log != raw_log:
            conf["log_file"] = new_log
            _persist_conf_key(conf_path, "LOG_FILE", f'"{new_log}"')
            changes.append(f"LOG_FILE={new_log}")
    # ③ SSL 校验: VERIFY_SSL=auto 时先验后降级
    if conf.get("verify_ssl_auto"):
        ok = _tls_verify_ok(conf.get("api_url", ""))
        conf["verify_ssl"] = ok
        if not ok:
            # SEC-tls-downgrade: 原实现把 VERIFY_SSL=false 永久写回 conf 且此后再不提示,
            # 一次中间人 (DNS 投毒/ARP) 即可让后续所有流量永久失去证书校验。
            # 现改为: 默认只本次运行降级, conf 保持 auto (下次仍会校验), 并打印醒目警告;
            # 确实要长期跳过校验, 需显式设 AI_SYNC_INSECURE_TLS=1。
            if os.environ.get("AI_SYNC_INSECURE_TLS") == "1":
                _persist_conf_key(conf_path, "VERIFY_SSL", "false")
                changes.append("VERIFY_SSL=false (按 AI_SYNC_INSECURE_TLS=1 显式关闭校验)")
            else:
                print("⚠️  服务端证书校验失败, 本次运行已临时降级为不校验 (不写入配置)。\n"
                      "    这会让链路可被中间人篡改。请优先修复服务端证书。\n"
                      "    确需长期跳过: 在 conf 显式写 VERIFY_SSL = false, 或设环境变量 AI_SYNC_INSECURE_TLS=1。",
                      file=sys.stderr)
        else:
            _persist_conf_key(conf_path, "VERIFY_SSL", "true")
            changes.append("VERIFY_SSL=true (证书校验通过)")
    return changes


def _run_pipeline(conf: dict, log: Logger, api: ApiClient) -> int:
    """--pipeline: 把 N 条 action 压进**一次 HTTP 往返**(服务端 pipeline action)。

    与 --batch 的区别(AI 该选哪个):
      --batch     : stdin 是 argv 列表, 同进程逐条跑 → 省了 N 次 Python 冷启动,
                    但每条仍是**一次独立 HTTP**, 生产 ~105ms/条的往返一次都少不了。
      --pipeline  : stdin 是 action 对象列表, 服务端一次跑完 → 连往返也省了。
                    生产 10 步实测: 逐条 ~2.8s / --batch ~1.2s / --pipeline ~0.3s。
    适用: AI 已知道要做什么的一串连续动作(批量查看/批量校验/批量读)。
    不适用: 需要看过上一步结果才能决定下一步的自适应流程(那本来就不能并行)。
    """
    raw = sys.stdin.read().strip()
    try:
        steps = json.loads(raw) if raw else []
    except Exception as e:
        _emit({"command": "pipeline", "ok": False, "code": 2, "error": "bad_pipeline_input",
               "message": "stdin 必须是 JSON 数组: [%s]" %
                          json.dumps({"action": "stat", "path": "a.txt"}, ensure_ascii=False),
               "detail": str(e)[:160]}, True, None)
        return 2
    if not isinstance(steps, list):
        steps = [steps]
    if not steps:
        _emit({"command": "pipeline", "ok": False, "code": 2, "error": "empty_steps",
               "message": "steps 为空, 没有要执行的动作"}, True, None)
        return 2
    # 每项必须是带 action 字段的对象。缺 action 提前报错 —— 绝不让 AI 拿到
    # "看起来成功但什么都没做" 的结果 (这是本项目反复强调的静默降级陷阱)。
    bad = [i for i, s in enumerate(steps)
           if not isinstance(s, dict) or not str(s.get("action", "")).strip()]
    if bad:
        _emit({"command": "pipeline", "ok": False, "code": 2, "error": "bad_step",
               "message": "以下 steps 缺少 action 字段: %s (每项形如 {\"action\":\"stat\",\"path\":\"a.txt\"})"
                          % bad[:10]}, True, None)
        return 2

    r = api._req({"action": "pipeline", "steps": steps})
    okall = bool(r.get("ok"))
    results = r.get("results") or []
    out = {"command": "pipeline", "ok": okall, "code": r.get("code", 0 if okall else -1),
           "total": r.get("total"), "succeeded": r.get("succeeded"), "failed": r.get("failed"),
           "stopped_at": r.get("stopped_at"), "max_steps": r.get("max_steps"),
           "results": results}
    if not okall:
        # 服务端整体拒绝(黑名单 action / 参数非法) —— 保留完整错误上下文给 AI
        out.update({"error": r.get("error"), "message": r.get("message"),
                    "fix_hint": r.get("fix_hint"), "blocked": r.get("blocked")})
    _emit(out, True, None)
    # stderr 给一行/步的人类可读摘要, AI 不必逐个翻 JSON
    for it in results:
        tag = "✅" if it.get("ok") else "❌"
        extra = "" if it.get("ok") else "  code=%s %s" % (it.get("code", ""), it.get("error") or "")
        print("[%d] %s %s%s" % (it.get("index", -1), tag, it.get("action"), extra), file=sys.stderr)
    return 0 if (okall and r.get("failed", 0) == 0) else 1


def _run_batch(conf: dict, log: Logger, api: ApiClient) -> int:
    """--batch 批处理: stdin 传 JSON 数组, 每项 = 一条命令的 argv 列表(或 shell 字符串)。
    同进程逐条执行(进程内递归复用 main), 复用配置加载; AI 连续只读操作每条省 ~200ms 冷启动。
    输出单个 JSON: {command:"batch", ok, code, count, results:[{argv,rc,ms,stdout,stderr}]}"""
    import contextlib, shlex, io as _io
    # FIX-batch-workdir: 子命令只继承 --conf, 外层 --workdir 会丢 → 子命令报「未指定工作目录」。
    # 走 AI_SYNC_WORKDIR 环境变量下发 (与 _resolve_workdir 的优先级一致), 子进程无需再传参。
    if conf.get("watch_dir") and not os.environ.get("AI_SYNC_WORKDIR"):
        os.environ["AI_SYNC_WORKDIR"] = str(conf["watch_dir"])
    raw = sys.stdin.read().strip()
    try:
        items = json.loads(raw) if raw else []
    except Exception as e:
        print(json.dumps({"command": "batch", "ok": False, "code": 2,
                          "error": "bad_batch_input", "message": str(e)[:200]}, ensure_ascii=False))
        return 2
    if not isinstance(items, list):
        items = [items]
    # 外层真实 --conf 路径 (conf dict 不一定带 conf_path 键)
    _oa = sys.argv
    # v4.28 修复: 外层用 AI_SYNC_CONF 环境变量时, 旧实现只查 argv 找 --conf,
    # 找不到就回退相对路径 "sync.conf" (通常不存在) → batch 子命令全挂。
    # 解析顺序与主流程一致: argv --conf > AI_SYNC_CONF 环境变量 > sync.conf
    if "--conf" in _oa:
        conf_path = _oa[_oa.index("--conf") + 1]
    else:
        conf_path = os.environ.get("AI_SYNC_CONF") or "sync.conf"
    results = []
    rc_max = 0
    for item in items:
        if isinstance(item, str):
            argv = shlex.split(item)
        else:
            argv = [str(x) for x in item]
        argv = [a for a in argv if a != "--batch"]   # 防递归
        # 递归执行时继承外层 --conf (否则每条命令都回退默认配置, WATCH_DIR 为空)
        if argv and argv[0] != "--conf" and "--conf" not in argv:
            argv = ["--conf", conf_path] + argv
        if not argv:
            results.append({"argv": argv, "rc": 2, "ms": 0, "stdout": "", "stderr": "empty argv"})
            rc_max = max(rc_max, 2)
            continue
        buf_out, buf_err = _io.StringIO(), _io.StringIO()
        _t0 = time.time()
        _old_argv = sys.argv
        try:
            sys.argv = [sys.argv[0]] + argv
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                rc = main()
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code in (None, 0) else 2)
        except Exception as e:
            rc = 2
            buf_err.write(f"exception: {type(e).__name__}: {e}")
        finally:
            sys.argv = _old_argv
        results.append({"argv": argv, "rc": rc, "ms": round((time.time() - _t0) * 1000),
                        "stdout": buf_out.getvalue(), "stderr": buf_err.getvalue()})
        rc_max = max(rc_max, rc or 0)
    print(json.dumps({"command": "batch", "ok": rc_max == 0, "code": 0 if rc_max == 0 else rc_max,
                      "count": len(results), "results": results}, ensure_ascii=False))
    return 0 if rc_max == 0 else rc_max


class _AIParser(argparse.ArgumentParser):
    """紧凑报错: AI 调用方参数写错时, 旧版倾倒 3.2KB/46 行 usage 墙, 纯浪费上下文。
    现在只回一行错误 + 相似 flag 纠正 + 自愈指引。"""

    def error(self, message):
        import difflib as _dif
        _flags = []
        for _act in self._actions:
            _flags.extend(_act.option_strings)
        _tok = re.findall(r"--[\w-]+", str(message))
        _sug = _dif.get_close_matches(_tok[0], _flags, n=4, cutoff=0.45) if _tok else []
        _msg = f"参数错误: {message}"
        if _sug:
            _msg += f" | 是否想找: {', '.join(_sug)}"
        _msg += " | 自愈: --help-ai <命令> 查该命令参数; --help-ai 场景速查"
        print(f"❌ {_msg}", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    # 🆕 P0: 编码/版本自检 (LANG=C 环境下中文输出不崩; 版本不符给出明确提示而非 traceback)
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.version_info < (3, 8):
        print(f"❌ 需要 Python >= 3.8, 当前 {sys.version.split()[0]}", file=sys.stderr)
        return 1
    # 🆕 v4.9: 不加任何操作参数时，自动显示功能概览（新 AI 零文档上手）
    # 判断：除了纯修饰参数之外，有没有实质操作参数
    # 修饰参数不改变"无参自举"语义: --conf/--workdir/--scope 带值, --json/--plain/--quiet/--verbose 不带值
    _args = sys.argv[1:]
    _has_operation = False
    _skip_next = False
    _MOD_WITH_VALUE = {"--conf", "--workdir", "--scope"}
    _MOD_NO_VALUE = {"--json", "--plain", "--quiet", "--verbose"}
    for a in _args:
        if _skip_next:
            _skip_next = False
            continue
        if a in _MOD_WITH_VALUE:
            _skip_next = True  # 跳过修饰参数的值
            continue
        if a in _MOD_NO_VALUE:
            continue
        if a.startswith("-"):
            _has_operation = True
            break

    # 🆕 P0: 无操作参数 = 自举 + 状态 (首次接入只需这一条命令, 不再打印 40 条命令清单)
    _BOOTSTRAP_ONLY = not _has_operation

    ap = _AIParser(description=f"ai-sync {APP_VERSION} 双向同步客户端",
                                 epilog="退出码: 0=成功  1=一般错误  2=参数/文件错误  3=远程命令失败  4=推送失败\n--help-ai: 输出 JSON 命令清单 (AI 脚本化调用用)",
                                 add_help=False)
    # 🆕 FIX-arg-hint-v4.22: 参数错误时追加 AI 自愈指路 (argparse 报错是最容易迷路处:
    #    之前只打 113 参数平铺 usage, AI 不知下一步; 现在报错行后直接给查法)
    _ap_orig_error = ap.error
    def _ap_ai_error(msg: str):
        _ap_orig_error(f"{msg}\n[AI 自愈] 参数格式看这里: --help-ai <命令> 查单命令用法 | --help-ai 场景速查")
    ap.error = _ap_ai_error
    # 🆕 v4.15: -h/--help 与 --help-ai 同走分层帮助 (默认精简能力地图, 非 40+ 命令全量倒出)
    ap.add_argument("-h", "--help", nargs="?", const="__OVERVIEW__", metavar="[CMD|full]",
                    help="AI 帮助 (默认=精简能力地图+全族决策树; --help <命令> 看单命令详情; --help full 看完整清单)")
    ap.add_argument("--conf", default=None, help="sync.conf 路径")
    ap.add_argument("--spider", metavar="URL", default=None, help="🆕 抓包爬虫: 抓取任意 URL 内容 (HTTP/JSON/HTML) 给 AI 分析")
    ap.add_argument("--sp-method", default="GET", help="抓包请求方法 (GET/POST)")
    ap.add_argument("--sp-headers", default=None, metavar="JSON", help="抓包请求头 JSON, 如 {\"Referer\":\"https://x.com/\"}")
    ap.add_argument("--sp-body", default=None, help="POST 请求体")
    ap.add_argument("--sp-timeout", type=int, default=15, help="抓包超时秒 (默认15)")
    ap.add_argument("--sp-maxlen", type=int, default=200000, help="返回 body 最大字节 (默认200000)")
    ap.add_argument("--sp-allow-private", action="store_true", help="允许抓取内网/本机地址 (默认拦截)")
    ap.add_argument("--via-m3u8", action="store_true", help="m3u8 抓取走 M3U8dl 代理 (防盗链/CF绕过)")
    ap.add_argument("--sp-remote", action="store_true", help="抓取走服务器执行 (ai-sync exec 通道: 普通URL服务器代抓, m3u8走服务器M3U8dl代理)")
    # 🆕 P0: 工作区覆盖 (默认按环境自动解析, 一般无需传)
    ap.add_argument("--workdir", default="", metavar="DIR",
                    help="覆盖 WATCH_DIR (优先级最高; 默认自动解析 /workspace/{scope} > ~/ai-sync/{scope} > ./{scope})")
    # 🆕 P1: 意图级命令 — AI 只需记 3 条
    ap.add_argument("--auto", nargs="+", metavar="INTENT",
                    help="意图模式: --auto pull | --auto save [files...] --yes | --auto fix | --auto status "
                         "(pull=增量拉取, save=lint-first+deploy+md5复验, fix=doctor 一键排查)")
    ap.add_argument("--once", action="store_true", help="同步一次后退出")
    ap.add_argument("--dry-run", action="store_true", help="演练模式")
    ap.add_argument("--status", action="store_true", help="查看状态")
    ap.add_argument("--selfcheck", action="store_true", help="自检")
    ap.add_argument("--sync", metavar="LOCAL_PATH", help="立即同步单文件")
    # v3.2 新增
    ap.add_argument("--search", metavar="PATTERN",
                    help="全文搜索 (默认仅搜 *.php *.py *.js; --any-ext 搜全部扩展名; 也可在 sync.conf 设 SEARCH_FILE_PATTERN=\"*\")")
    ap.add_argument("--any-ext", action="store_true",
                    help="配合--search: 不限扩展名搜索 (file_pattern=*, 覆盖 html/css/json 等; grep-remote 即此模式的别名)")
    ap.add_argument("--stats", metavar="PATH", help="代码统计")
    ap.add_argument("--lint", metavar="PATH", help="语法检查")
    ap.add_argument("--fix", action="store_true", help="自动修复")
    ap.add_argument("--snippets", action="store_true", help="列出代码片段 (片段由服务端 AI 增强接口维护, 添加方式见 AI_GUIDE.md)")
    ap.add_argument("--start", action="store_true", help="后台启动")
    ap.add_argument("--stop", action="store_true", help="停止后台")
    # AI 工作流增强命令
    ap.add_argument("--pull", nargs="?", const="__ALL__", metavar="REMOTE_PATH",
                    help="从远程拉取文件到本地 (不指定路径=拉全部); 远程覆盖本地前自动备份本地改动")
    ap.add_argument("--pull-smart", nargs="?", const="__ALL__", metavar="REMOTE_PATH",
                    help="增量拉取: 先 checksum 比对 MD5, 只下载远程有变化/本地缺失的文件, 内容一致的跳过 (不指定路径=比对全部)")
    ap.add_argument("--no-verify", action="store_true",
                    help="拉取后跳过落盘 MD5 回读校验 (大目录可省数秒; 代价是不再发现写盘损坏)")
    ap.add_argument("--push", nargs="*", metavar="LOCAL_PATH", action="append",
                    help="推送本地变更到远程 (不指定路径=推全部 dirty; 可指定多个文件/目录); 远程被覆盖前服务器自动备份")
    ap.add_argument("--show-diff", action="store_true",
                    help="推送前展示本地 vs 服务器当前的内容 diff + 基线冲突警告 (AI 确认改对没/是否基于最新代码); 冲突检测默认开启, 此参数额外展示逐行差异")
    ap.add_argument("--test", metavar="FILE", help="在服务器安全执行 PHP 文件 (lint+沙箱)")
    ap.add_argument("--timeout", type=int, default=None,
                    help="exec/php_run/shell 超时(秒). exec 由远端 timeout 命令强制(默认不限制, 适合长任务); "
                         "php_run/shell 服务端上限 EXEC_TIMEOUT_MAX(默认 120)")
    ap.add_argument("--rollback", metavar="FILE", help="从远程备份恢复文件 (配合 --backup)")
    ap.add_argument("--backup", metavar="BACKUP_FILE", help="指定要恢复的备份文件名")
    ap.add_argument("--backup-list", nargs="?", const="__ALL__", metavar="FILE",
                    help="列出远程历史备份 (不指定=列全部 remote_base)")
    ap.add_argument("--verify", nargs="*", metavar="FILE",
                    help="端到端一致性验证: 本地 vs 远程 vs 快照 三方 MD5 比对 (不指定=全部)")
    ap.add_argument("--history", action="store_true", help="查看变更历史")
    # v4.1 多目录管理命令
    ap.add_argument("--scope", metavar="NAME", help="指定操作的 scope (多目录授权)")
    ap.add_argument("--admin", nargs="?", const="stats", metavar="SUBCMD",
                    help="管理操作: stats/config/scopes/tokens/audit/reload (需 admin token)")
    ap.add_argument("--token-add", nargs=4, metavar=("TOKEN", "NAME", "SCOPES", "PERMS"),
                    help="添加 token: --token-add <token> <name> <scopes逗号分隔> <perms逗号分隔>")
    ap.add_argument("--token-del", metavar="TOKEN", help="删除 token")
    ap.add_argument("--scope-add", nargs="+", metavar=("NAME", "WORK_DIR"),
                    help="添加 scope: --scope-add <name> <work_dir> [backup_keep=N]")
    ap.add_argument("--scope-del", metavar="NAME", help="删除 scope")
    ap.add_argument("--package", nargs="+", metavar=("TOKEN", "SCOPE"),
                    help="打包客户端: --package <target_token> [scope] [output_dir]")
    # v4.2 AI 增强命令
    ap.add_argument("--whoami", action="store_true", help="查询当前 token 权限边界")
    ap.add_argument("--batch", action="store_true",
                    help="批量执行: stdin 传 JSON 数组, 每项=一条命令的 argv 列表(如 [[\"--ls\",\".\"],[\"--cat\",\"a.txt\"]]); "
                         "同进程逐条执行, AI 连续只读操作每条省约 200ms 冷启动; 输出 JSONL 汇总")
    ap.add_argument("--pipeline", action="store_true",
                    help="管道执行(比 --batch 更快): stdin 传 JSON 数组的 steps, "
                         "每项=一个完整 action 请求对象 (如 [{\"action\":\"stat\",\"path\":\"a.php\"},"
                         "{\"action\":\"read_range\",\"path\":\"a.php\"}]); "
                         "一次 HTTP 往返跑完所有步骤 —— --batch 是省进程启动(N 次往返), --pipeline 连同往返一起省(1 次)。"
                         "生产实测 10 步: 逐条 ~2.8s / --batch ~1.2s / --pipeline ~0.3s。不适用时会明确报错")
    ap.add_argument("--api-help", nargs="?", const="", metavar="ACTION",
                    help="服务端 API 自描述: 不带值列全部 action 及必填参数; 带 action 名看该 action 的完整契约 "
                         "(必填/可选/返回/示例/常见错误)。AI 零文档接入用")
    ap.add_argument("--sync-check", nargs="+", metavar="FILE",
                    help="一体化同步决策: --sync-check <本地file> [file2] ... (检查本地文件 vs 远程, 判断该不该推)")
    ap.add_argument("--lint-batch", nargs="+", metavar="FILE",
                    help="批量语法检查: --lint-batch <file1> [file2] ...")
    ap.add_argument("--recent-changes", nargs="?", type=int, const=200, metavar="N",
                    help="服务器端最近变更历史 (默认200条)")
    ap.add_argument("--batch-rollback", nargs="+", metavar=("TS", "FILE"),
                    help="按时间点批量回滚: --batch-rollback <unix秒时间戳> [file1 file2 ...] (ts 用 $(date +%s), 不是 deploy_id)")
    ap.add_argument("--list-since", metavar="TS",
                    help="列出某时间戳后变更的文件: --list-since <unix_ts 或 \"YYYY-MM-DD HH:MM:SS\">")
    # 🆕 v4.3 原子批量部署 (P1: 不带文件 = 部署整个 WATCH_DIR)
    ap.add_argument("--deploy", nargs="*", default=None, metavar="FILE",
                    help="原子批量部署: --deploy [file1 file2 ...] (省略文件=整个 WATCH_DIR; PHP 自动 php -l, 失败自动回滚)")
    ap.add_argument("--pre-check", metavar="php_lint", default="",
                    help="部署前检查 (⚠️ 只接受特殊值 php_lint 或留空=自动 php -l; 任意 shell 命令会被拒绝-防注入)")
    # 🆕 FIX-lint-first-v4.17: 部署前本地 JS/TS/HTML 语法预检 (PHP 已由服务端 php -l 覆盖)
    ap.add_argument("--lint-first", action="store_true",
                    help="部署前先本地 node --check 校验 JS/TS/HTML 语法, lint 失败则不部署 (防坏代码上线)")
    ap.add_argument("--post-check", metavar="CMD", default="",
                    help="部署后检查: HTTP(S) URL (curl 结果匹配 --post-expect, 默认要求 exit_code=0)")
    ap.add_argument("--post-expect", metavar="STR", default="",
                    help="post-check 输出期望包含的字符串 (默认: exit_code=0)")
    # 🆕 v4.12.4: --smart-verify (修复 help-ai 推荐流程参数缺失)
    ap.add_argument("--smart-verify", metavar="ALGO", nargs="?", const="md5", default="", choices=["md5", "sha1"],
                    help="部署成功后自动复验远程哈希 (裸用默认 md5), AI 确认部署真正生效. 示例: --deploy TV/config.php --smart-verify")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="DEBUG 日志 + 批量命令逐文件明细 (默认折叠进度, AI 上下文友好)")
    # 🆕 v4.29: --version — AI 会话自识别工具版本 (此前 unrecognized)
    ap.add_argument("--version", action="store_true", help="输出版本 JSON 后退出")
    # 🆕 FIX-cat-limit-v4.17: --full 强制 cat 全量输出 (默认 cat 大文件自动截断前 200 行保护 AI 上下文)
    ap.add_argument("--full", action="store_true",
                    help="cat 大文件时强制全量输出 (默认自动截断前 200 行; --brief 前 30 行; --range 精确切片)")
    # P1-A: Error Log
    ap.add_argument("--error-log", nargs="?", const="__LIST__", metavar="NAME",
                    help="读取服务器日志: 不带参数=列出可用日志名; --error-log <name> 读取 (可用名取决于服务器配置, 常见: audit/nginx_err/nginx_acc/php_error)")
    ap.add_argument("--error-log-path", metavar="PATH", default="",
                    help="name=custom 时的日志文件路径")
    ap.add_argument("--error-lines", type=int, default=100, metavar="N",
                    help="tail 最近 N 行, 默认 100")
    ap.add_argument("--error-grep", metavar="STR", default="",
                    help="关键词过滤 (大小写不敏感)")
    ap.add_argument("--error-regex", action="store_true",
                    help="--error-grep 按正则匹配")
    ap.add_argument("--error-since", metavar="TS", type=int, default=0,
                    help="只显示 since_ts 之后的 JSONL 记录")
    # 🆕 v4.9: 一键排查模式 — 自动合并 nginx/php/audit 日志，按时间排序输出
    ap.add_argument("--doctor", action="store_true",
                    help="一键排查: 自动收集 nginx错误 + PHP错误 + 同步审计日志，按时间排序输出最近问题")
    # 🆕 v4.17: 增强命令 — 版本检测/健康摘要/上线前检查/客户端自更新
    ap.add_argument("--check-update", action="store_true",
                    help="检测本地客户端与服务器分发版 (/mnt/data/wwwroot/default/sync/server/sync.py) 版本一致性")
    ap.add_argument("--health", action="store_true",
                    help="服务器健康摘要: 磁盘/内存/负载/进程数")
    ap.add_argument("--preflight", nargs="+", metavar="FILE",
                    help="批量上线前检查: --preflight <file1> [file2] ... (lint_batch + 三方MD5一致性)")
    ap.add_argument("--self-update", action="store_true",
                    help="从服务器拉取新版客户端 (需 admin token; 非 admin 时给出提示)")
    # 🆕 v4.17: deploy 链路增强 — 部署后 nginx reload + HTTP 健康检查
    ap.add_argument("--nginx-reload", action="store_true",
                    help="部署成功后自动执行 nginx -t && nginx -s reload (需 root 权限)")
    ap.add_argument("--health-url", metavar="URL", default="",
                    help="部署成功后 curl 健康检查 URL (配合 --health-expect)")
    ap.add_argument("--health-expect", metavar="STR", default="",
                    help="健康检查响应期望包含的字符串 (默认仅要求 HTTP 可达)")
    # 🆕 FIX-http-check-v4.17: 部署后页面内容断言 — AI 确认"线上页面真的对了"
    ap.add_argument("--http-check", metavar="URL", default="",
                    help="端到端 HTTP 检查: 抓取页面内容断言 (可独立用 --http-check <url> [--expect <字符串>], 也可作 --deploy 配套 flag 部署后校验; 输出 body_preview 供AI核对)")
    ap.add_argument("--expect", metavar="STR", default="",
                    help="配合 --http-check: 页面必须包含的字符串 (缺省仅断言非空)")
    # P1-B: Watch 守护模式
    ap.add_argument("--watch", nargs="?", const="start", choices=["start", "status", "stop"], metavar="start|status|stop",
                    help="守护模式: --watch 启动 | --watch status 状态 | --watch stop 停止 (⚠️ start 为前台阻塞守护, 占住终端; 真后台用 setsid nohup python3 sync.py --watch start &)")
    ap.add_argument("--watch-interval", type=int, default=None, metavar="SEC",
                    help="目录扫描间隔 (秒), 默认读配置 WATCH_INTERVAL (缺省 2); 命令行优先")
    ap.add_argument("--watch-mode", choices=["push","two-way"], default=None,
                    help="守护模式: push=只推本地变更到远程(默认); two-way=双向同步(远程变更也自动拉取到本地, 拉取前自动备份本地文件到 .sync_backup/). 命令行参数优先于配置文件 WATCH_MODE. 示例: --watch start --watch-mode two-way")
    ap.add_argument("--watch-subdir", metavar="SUBDIR", default=None,
                    help="只监控指定子目录 (相对于 watch_dir, 如 ai-test/ 或 TV/assets/js/). 所有扫描/推送/拉取自动限制在该目录内, 不影响其他目录. 纯客户端实现, 服务器端无需修改. 常配合 --watch-mode two-way 使用. 示例: --watch start --watch-subdir ai-test/ --watch-mode two-way --watch-interval 3")
    # P1-C: Smart 跳过
    ap.add_argument("--smart", nargs="+", metavar="FILE",
                    help="智能跳过: 本地 vs 远程 内容比对 (未变更跳过); 改 1 个文件不等 200 个全传")
    ap.add_argument("--smart-strategy", choices=["auto","md5","size_mtime"], default="auto",
                    help="auto=≤20个用md5,>20个用size_mtime")
    ap.add_argument("--smart-apply", action="store_true",
                    help="比对完成后直接部署(配合 --smart)")
    # v4.4 AI 辅助命令
    ap.add_argument("--ls", nargs="?", const="__ALL__", metavar="REMOTE_PATH",
                    help="列出远程目录内容 (不指定=根目录); 配合 -R 递归列举 (-R 可放 --ls 前后任意位置)")
    ap.add_argument("-R", "--recursive", action="store_true",
                    help="--ls 递归列举子目录")
    ap.add_argument("--diff", nargs="?", const="__ALL__", metavar="PATH",
                    help="对比本地与远程差异 (不指定=全部); 显示需 push/pull/冲突的文件")
    ap.add_argument("--cat", metavar="REMOTE_PATH",
                    help="读取远程文件内容输出到 stdout (不修改本地); --range START END 行切片")
    ap.add_argument("--range", nargs=2, type=int, metavar=("START", "END"),
                    help="配合--cat: 只输出指定行范围 (行号从1开始; read-range 命令即此别名)")
    # [AI-SAFE v1.1.2] 远程编辑命令: AI 像本地一样编辑服务器文件
    ap.add_argument("--read-remote", metavar="PATH", nargs="+",
                    help="下载远程文件到本地缓存 (~/.ai-sync-remote/<scope>/), AI 直接 Read/Edit 缓存, 改完用 --write-remote 回传; 支持多路径批量")
    ap.add_argument("--write-remote", metavar="PATH", nargs="+",
                    help="把本地缓存文件回传到远程 (自动 CAS 冲突检测 + 服务器备份 + PHP lint + 回读 md5 验证 + JS/HTML 语法校验); 支持多路径批量")
    ap.add_argument("--edit-remote", metavar="REMOTE_PATH",
                    help="远程编辑入口 (= read-remote, AI 改缓存后 write-remote 回传)")
    # FIX-v4.33 (P1): 远程字符串替换 — AI 改一行内容此前必须 4 步
    #   (read-remote → 自己写 python/sed 改缓存 → write-remote → cat 验证),
    #   且第 2 步 AI 得知道缓存绝对路径. 现在一条命令原子完成, 且带 CAS/备份/lint/回读。
    ap.add_argument("--replace", metavar="REMOTE_PATH",
                    help="远程就地替换字符串(一步完成): --replace FILE --old '原文' --new '新文' "
                         "(自动 CAS 冲突检测+备份+lint+回读验证; 0 处匹配会明确报错而非静默成功)")
    ap.add_argument("--old", metavar="STR", help="配合 --replace: 要被替换的原文")
    ap.add_argument("--new", metavar="STR", default="", help="配合 --replace: 替换后的新内容 (空串=删除)")
    ap.add_argument("--regex", action="store_true", help="配合 --replace: --old 按正则表达式处理")
    ap.add_argument("--count", metavar="N", type=int, default=0,
                    help="配合 --replace: 期望替换处数; 不符则拒绝写入(防误伤多处), 0=不限")
    # 🆕 v4.13: 本地文件直传远程 (创建新文件场景); v4.14: 支持多文件批量
    ap.add_argument("--upload-file", metavar="LOCAL_PATH", nargs="+",
                    help="本地文件直传远程 (⚠️ 本地文件须先放 WATCH_DIR 如 /tmp/sync_ai 下, 参数传相对 WATCH_DIR 的路径; 当前目录/绝对路径不认; 自动写缓存+备份+lint+回读验证; 支持--dry-run与多文件批量)")
    # 🆕 v4.13: 远程任意文件内容检索
    ap.add_argument("--grep-remote", metavar="PATTERN",
                    help="[别名] = --search <p> --any-ext (远程任意扩展名内容检索; 默认搜全目录, --grep-path 指定范围)")
    ap.add_argument("--grep-path", metavar="PATH", default=".",
                    help="配合--grep-remote/--search: 检索范围 (远程相对路径, 默认 '.' 全目录)")
    ap.add_argument("--grep-regex", action="store_true",
                    help="配合--grep-remote/--search: 把 pattern 当正则解释")
    # v4.5 AI 远程文件管理命令
    ap.add_argument("--info", metavar="REMOTE_PATH",
                    help="查看远程文件详细信息 (大小/修改时间/MD5/权限)")
    ap.add_argument("--mkdir", metavar="REMOTE_PATH",
                    help="在服务器上创建目录")
    ap.add_argument("--rm", metavar="REMOTE_PATH", nargs="+",
                    help="删除远程文件或目录(支持多文件, 逐个确认; 危险需 --yes / --allow-destructive)")
    ap.add_argument("--mv", nargs=2, metavar=("FROM", "TO"),
                    help="重命名/移动远程文件")
    ap.add_argument("--cp", nargs=2, metavar=("FROM", "TO"),
                    help="复制远程文件: --cp <from> <to> (目标已存在默认拒绝, 加 --cp-force 覆盖)")
    ap.add_argument("--cp-force", action="store_true",
                    help="--cp 时覆盖已存在目标 (覆盖前服务器自动备份)")
    # 🆕 AI 实用增强: --exec (服务端 action_exec 早已就绪, 此前客户端无 CLI 入口)
    ap.add_argument("--exec", nargs="?", const="__STDIN__", metavar="CMD",
                    help="在服务器执行 shell 命令; 不带 CMD 时从 stdin 读取(推荐 heredoc 免转义: "
                         "--exec <<'CMD' ... CMD); 带 CMD 为旧用法直传(注意 $ 会被本地 shell 展开); "
                         "stdout 原样输出可重定向, 元信息走 stderr; 配合 --timeout")
    ap.add_argument("--async", action="store_true",
                    help="(默认开启, 向后兼容) exec 自动后台执行+轮询, 免超时无感等待; --async-timeout 调轮询上限")
    ap.add_argument("--async-timeout", type=int, default=None,
                    help="本地轮询上限秒数(默认 600); 超时后任务仍在远端, 会打印取回输出/清理的命令")
    ap.add_argument("--exec-b64", metavar="B64",
                    help="以 base64 形式传命令给服务器执行 (本地 base64 编码后传入, "
                         "彻底规避嵌套引号/shell 转义问题; 解码后同 --exec, 配合 --timeout)")
    # 🆕 --exec-file: 从本地文件读取命令内容执行 — 绕过 argv 单参数 128KB (MAX_ARG_STRLEN) 限制;
    #   命令大小只受 HTTP body 限制 (nginx client_max_body_size / PHP post_max_size, 通常 1MB+);
    #   适合大脚本/大文件直写场景, 且无需 base64 编码 (文件原样读取作为 cmd 发送)
    ap.add_argument("--exec-file", metavar="FILE",
                    help="从本地文件读取命令内容并发送服务器执行 (文件请放当前工作目录用相对路径; 不受命令行参数 128KB 限制; "
                         "命令原样作为 cmd, 无需 base64; 大小受服务器 HTTP body 限制, 配合 --timeout)")
    # 🆕 FIX-cwd-v4.39: --exec 支持 --cwd 指定服务器端工作目录
    #   服务端 exec.php 原生支持 cwd 参数 (相对路径以 WORK_DIR 为基准, realpath 校验, cwd_not_found 报错);
    #   async 通道由客户端注入 wrapper __AI_CD__ (语义与服务端一致); 不再拼接 'cd DIR && CMD' 前缀
    ap.add_argument("--cwd", metavar="DIR",
                    help="--exec 的服务器端工作目录 (默认远程 WORK_DIR); "
                         "相对路径以 WORK_DIR 为基准 (与服务端原生 cwd 一致), 目录不存在会明确报错")
    # 🆕 持久 shell 工具 (对齐原生 ssh, 面向 AI 易用性): 会话内 cwd/export 跨调用保持, 后台任务可查/可杀
    ap.add_argument("--shell", nargs="?", const="__STDIN__", metavar="CMD",
                    help="持久 shell: 在会话内执行命令, cwd/export 跨调用保持 (AI 最常用入口). "
                         "默认复用本地状态文件里的会话; 不带 CMD 从 stdin 读取 (heredoc 免转义). 配合 --cwd/--shell-session/--shell-env")
    ap.add_argument("--shell-open", nargs="?", const="", metavar="NAME",
                    help="新建/复用持久 shell 会话, 打印 session_id (可带 NAME 命名); 并写入本地状态作为默认会话")
    ap.add_argument("--shell-close", nargs="?", const="", metavar="SID",
                    help="关闭 shell 会话; 不带 SID 则直接关闭本地默认会话 (AI 无需记住 id); "
                         "其后台任务仍可 --shell-job 查询")
    ap.add_argument("--shell-list", action="store_true",
                    help="列举当前活跃 shell 会话与后台任务")
    ap.add_argument("--shell-job", metavar="JID",
                    help="查询后台任务输出/状态 (配合 --lines 只看末尾 N 行)")
    ap.add_argument("--shell-kill", metavar="JID",
                    help="终止后台任务")
    ap.add_argument("--shell-bg", nargs="?", const="__STDIN__", metavar="CMD",
                    help="在 shell 会话内后台执行命令, 立即返回 job_id; 用 --shell-job 轮询输出")
    ap.add_argument("--shell-session", metavar="SID",
                    help="指定 --shell/--shell-bg 使用的会话 (否则用本地默认会话状态); 不指定且无名则自动开新会话")
    ap.add_argument("--shell-env", metavar="KEY=VAL", action="append",
                    help="--shell 时注入/持久的环境变量 (可多次; 如 --shell-env FOO=1), 跨调用保持")
    ap.add_argument("--lines", type=int, default=0,
                    help="--shell-job 只看末尾 N 行 (默认返回完整输出)")

    ap.add_argument("--todo", nargs="?", const=".", metavar="PATH",
                    help="提取 TODO/FIXME/HACK 注释 (不指定=当前目录)")
    ap.add_argument("--read-range", nargs=3, metavar=("PATH", "START", "END"),
                    help="读取远程文件指定行范围 (行号从1开始)")
    # AI 纯文本输出模式 (--ls 等表格无 emoji, 便于 grep/awk/脚本解析)
    # 🆕 v4.39.2: 关闭自动升级 (默认开; 也可 conf AUTO_UPGRADE=false)
    ap.add_argument("--no-auto-upgrade", action="store_true",
                    help="跳过本次自动升级检查 (默认: 启动时静默对比服务端 sync.py, 有新版自动升级)")
    ap.add_argument("--plain", action="store_true",
                    help="纯文本输出: --ls 等表格去掉 emoji 图标, 便于脚本解析")
    ap.add_argument("--json", action="store_true",
                    help="结构化 JSON 输出 (ok/code/stdout/stderr), AI 脚本化断言用")
    # 🆕 FIX-brief-v4.17: 全局精简模式 — AI 调用时省上下文: 大列表只输出 count/summary 结论字段,
    #   条目默认裁剪 (ls 目录/doctor/recent-changes/list-since/error-log/history/stats 等).
    #   与 --json 可组合 (JSON 内 items 裁剪, 保留 total/count/summary).
    ap.add_argument("--brief", action="store_true",
                    help="精简模式: 大输出只保留结论字段 (count/summary), 裁剪条目 — AI 上下文友好")
    # 🆕 FIX-quiet-v4.6: 全局静默模式 — AI 脚本化调用时过滤框架噪音
    #   (启动 [INFO] 日志 / [exec] warning 元信息), stdout 只剩命令真实输出。
    #   注意: 错误(ERR)与关键元信息(rc/TIMEOUT/truncated)仍保留, 避免静默吞错。
    ap.add_argument("--quiet", action="store_true", default=True,
                    help="静默模式(默认开启): 抑制启动日志与 warning 元信息 (错误与关键元信息仍保留; --no-quiet/--verbose 可关闭)")
    ap.add_argument("--no-quiet", dest="quiet", action="store_false",
                    help="恢复完整 INFO 日志与 warning (覆盖默认静默)")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="跳过危险操作二次确认 (删除/回滚/删token等; AI/脚本环境用)")
    ap.add_argument("--allow-destructive", nargs="?", const="*", metavar="PREFIX",
                    help="[AI-SAFE v1.1.2] 非交互环境放行「需授权」危险操作。无值=本次进程内全部放行; "
                         "带路径前缀=仅放行该前缀下的操作 (如 --allow-destructive /mnt/data/wwwroot/default/bxbb)。"
                         "绝对禁止项如关机/格式化/系统根删除不受影响, 永远拒绝")
    ap.add_argument("--help-ai", nargs="?", const="__OVERVIEW__", metavar="[CMD|full]",
                    help="AI 帮助 (默认=精简能力地图+全族决策树; --help-ai <命令> 看单命令详情; --help-ai full 看完整40+命令清单)")
    ap.add_argument("--op-log", nargs="?", const="20", metavar="N",
                    help="查看本地操作日志 (默认最近20条; --op-log 50 看50条; 配合 --op-log-grep 过滤)")
    ap.add_argument("--op-log-grep", metavar="STR", default="",
                    help="操作日志关键词过滤 (如 exec/push/deploy)")
    # 兼容两种 -R 写法: 把紧跟 --ls 之后的 -R/--recursive 挪到 --ls 前面
    # (argparse 的 nargs='?' 遇选项会截断, --ls -R PATH 的 PATH 会变多余位置参数)
    _argv = list(sys.argv[1:])
    if "--ls" in _argv:
        _i = _argv.index("--ls")
        while _i + 1 < len(_argv) and _argv[_i + 1] in ("-R", "--recursive"):
            _flag = _argv.pop(_i + 1)
            _argv.insert(_i, _flag)
            _i += 1
    args = ap.parse_args(_argv)

    # 🆕 [EXEC-STDIN] --exec 不带值(或 '-') 时从 stdin 读取命令 (heredoc/管道原样传入, 免本地 shell 转义)
    if getattr(args, "exec", None) in ("__STDIN__", "-"):
        _stdin_cmd = sys.stdin.read().rstrip("\n")
        if not _stdin_cmd.strip():
            print("错误: --exec 未带命令且 stdin 为空 (请用 heredoc/管道传入命令)", file=sys.stderr)
            return 2
        args.exec = _stdin_cmd

    if getattr(args, "version", False):
        print(json.dumps({"tool": "ai-sync", "version": APP_VERSION, "command": "version"}, ensure_ascii=False))
        return 0

    global _YES_MODE, _ALLOW_DESTRUCTIVE
    _YES_MODE = bool(getattr(args, "yes", False))
    # 🆕 [AI-SAFE v1.1.2] --allow-destructive 授权通道: None=未授权 / "*"=全部 / str=路径前缀 (仅用户显式输入; 不影响 exec 绝对禁止项)
    _ALLOW_DESTRUCTIVE = getattr(args, "allow_destructive", None)
    # 🆕 FIX-single-flag: --yes 隐含授权全部危险操作 (AI 不用再同时加 --allow-destructive)
    #    绝对禁止项 (exec 黑名单: 关机/格式化/系统根删除) 不受此影响, 永远拒绝
    if _YES_MODE and not _ALLOW_DESTRUCTIVE:
        _ALLOW_DESTRUCTIVE = "*"
    # 🆕 FIX-quiet-v4.6: --quiet 全局静默 (抑制启动日志 + exec warning)
    sys._AI_SYNC_QUIET = bool(getattr(args, "quiet", True)) and not bool(getattr(args, "verbose", False))
    # 🆕 FIX-help-ai-v4.9 + v4.15: 分层 AI 帮助 — -h/--help/--help-ai 同走: 默认精简能力地图; <cmd> 单命令详情; full 完整清单
    if getattr(args, "help", False) or getattr(args, "help_ai", False):
        import json as _json
        _topic = args.help_ai if getattr(args, "help_ai", False) else args.help
        if _topic == "__OVERVIEW__":
            print(_json.dumps({**_help_ai_overview(), "command": "help-ai"}, ensure_ascii=False))
            return 0
        if _topic in ("full", "all"):
            # ---- 完整 40+ 命令清单 (老 AI / 深度使用) ----
            print(_json.dumps({
            "tool": "ai-sync", "version": APP_VERSION,
            "categories": {
                "文件同步": {
                    "desc": "本地 ↔ 服务器文件双向同步",
                    "commands": {
                        "push":       {"args": "[file...]", "desc": "推送本地变更到远程(不指定=推全部); 推送前自动基线冲突检测(服务器被他人改过会警告); --show-diff 额外展示逐行内容差异; conf 可设 PUSH_CONFLICT=ask|block|force; 💡单文件快速改配置用 push, 多文件上线/需校验回滚用 --deploy", "example": "--push TV/config.php --show-diff"},
                        "pull":       {"args": "[file]", "desc": "从远程拉取(本地有改动自动备份)", "example": "--pull"},
                        "pull-smart": {"args": "[file]", "desc": "增量拉取(MD5比对,只下载变更)", "example": "--pull-smart"},
                        "deploy":     {"args": "<file...>", "desc": "原子部署(lint+verify+失败自动回滚); --http-check URL --expect 串 部署后断言页面内容; 💡多文件上线/需校验回滚用 deploy, 单文件快速改配置用 --push", "example": "--deploy TV/config.php --http-check https://x/ --expect 播放器"},
                        "sync":       {"args": "<file>", "desc": "单文件同步(推送本地单文件到远程)", "example": "--sync TV/config.php"},
                        "smart":      {"args": "<path...>", "desc": "智能比对(内容一致跳过,减少传输; 支持目录参数自动递归)", "example": "--smart TV/assets/js/*.js"},
                        "auto":         {"args": "pull|save [files...]|fix|status", "desc": "意图模式(最高层封装): pull=增量拉取, save=lint预检+部署+md5复验, fix=doctor一键排查, status=同步状态", "example": "--auto save TV/config.php --yes"},
                        "workdir":      {"args": "<DIR>", "desc": "覆盖本地工作目录(优先级最高); 自举时指定本地同步根目录", "example": "--workdir /workspace/xbxbb"},
                    }
                },
                "远程执行": {
                    "desc": "在服务器上执行 shell 命令",
                    "commands": {
                        "exec":       {"args": "[cmd]", "desc": "远程执行(推荐: 不带 cmd 用 heredoc, $/引号/多行全原样免转义; 带 cmd 旧直传)", "example": "--exec <<'CMD'\nls -la\nCMD"},
                        "exec-file":  {"args": "<本地脚本>", "desc": "执行长脚本(文件放当前工作目录, 用相对路径)", "example": "--exec-file run.sh"},
                        "exec-b64":   {"args": "<base64>", "desc": "base64编码命令(复杂转义用)", "example": "--exec-b64 'Y2QgL3RtcA=='"},
                        "cwd":        {"args": "<目录>", "desc": "指定exec工作目录", "example": "--exec 'pwd' --cwd /var/www"},
                        "shell":     {"args": "[cmd]", "desc": "持久shell会话(对齐ssh: cwd/export跨调用保持, --shell-env持久化); ⚠️保护边界: 服务端拦毁灭命令(rm -rf /、dd写盘、mkfs、fork炸弹、关机重启, 给替代方案); 客户端额外拦系统根目录自身(rm -rf /etc等); rm -rf <业务目录>/mv覆盖照常执行只提醒 — AI 使用前自行确认目标路径", "example": "--shell 'ls -la' --shell-env MYVAR=1"},
                    }
                },
                "调试排查": {
                    "desc": "出问题时快速定位",
                    "commands": {
                        "doctor":     {"args": "", "desc": "一键排查:合并nginx+PHP+审计日志按时间排序", "example": "--doctor"},
                        "error-log":  {"args": "[name]", "desc": "读取服务器日志(不带=列可用日志)", "example": "--error-log nginx_err"},
                        "error-lines":{"args": "<N>", "desc": "日志行数(配合--error-log)", "example": "--error-log php_error --error-lines 100"},
                        "error-grep": {"args": "<关键词>", "desc": "日志关键词过滤", "example": "--error-log php_error --error-grep Fatal"},
                        "lint":       {"args": "<file>", "desc": "语法检查(JS/PHP,带错误行上下文)", "example": "--lint TV/config.php"},
                        "lint-batch": {"args": "<file...>", "desc": "批量语法检查(可传目录/多文件)", "example": "--lint-batch TV/"},
                        "sync-check": {"args": "<file...>", "desc": "检查本地文件是否有待推送变更(决策用)", "example": "--sync-check TV/config.php"},
                        "test":       {"args": "<file>", "desc": "服务器安全执行PHP(lint+沙箱)", "example": "--test TV/test.php"},
                        "status":     {"args": "", "desc": "同步状态概览", "example": "--status"},
                        "replace":    {"args": "<file> --old S --new S", "desc": "远程就地字符串替换(一步完成,含CAS/备份/lint/回读验证); 💡只改几处字符串用 replace, 整文件大改用 read-remote→write-remote, 本地新建文件用 upload-file", "example": "--replace TV/config.php --old 'old' --new 'new'"},
                        "batch":      {"args": "", "desc": "批量执行: stdin 传 argv 数组, 同进程逐条跑(省冷启动)", "example": "echo '[[\"--ls\"]]' | sync.py --batch"},
                        "pipeline":   {"args": "", "desc": "批量执行(最快): stdin 传 action 数组, 一次 HTTP 往返跑完, 省 N 次网络往返(生产约省 70%)", "example": "echo '[{\"action\":\"stat\",\"path\":\"a.php\"}]' | sync.py --pipeline"},
                        "selfcheck":  {"args": "", "desc": "客户端自检", "example": "--selfcheck"},
                        "verify":     {"args": "[file]", "desc": "三方MD5一致性验证", "example": "--verify TV/config.php"},
                        "diff":       {"args": "[path]", "desc": "本地vs远程差异对比", "example": "--diff TV/"},
                        "health":       {"args": "", "desc": "服务器健康摘要: 磁盘/内存/负载/进程数(快速判断服务端是否异常)", "example": "--health"},
                        "check-update": {"args": "", "desc": "检测本地客户端与服务器分发版版本是否一致", "example": "--check-update"},
                        "self-update":  {"args": "", "desc": "强制从服务端更新本地客户端(与自动升级同链路, read 权限, 无需 admin; 已是最新则提示)", "example": "--self-update"},
                        "preflight":    {"args": "<file1> [file2...]", "desc": "批量上线前检查: 语法预检+冲突检测, 不实际部署", "example": "--preflight TV/a.php TV/b.php"},
                        "spider":       {"args": "<URL>", "desc": "抓取任意 URL 内容(HTTP/JSON/HTML)给 AI 分析; 配套 --sp-method/--sp-headers/--sp-body/--sp-remote/--sp-timeout", "example": "--spider https://x.com/api"},
                        "http-check":   {"args": "<URL>", "desc": "端到端验证: 抓取页面并断言包含期望内容(--expect <str> 缺省仅断言 HTTP 可达; 也可作 --deploy 配套 flag)", "example": "--http-check https://x.com --expect OK"},
                    }
                },
                "备份回滚": {
                    "desc": "文件备份与恢复",
                    "commands": {
                        "backup-list": {"args": "[file]", "desc": "列出历史备份", "example": "--backup-list TV/config.php"},
                        "rollback":    {"args": "<file>", "desc": "从备份恢复文件", "example": "--rollback TV/config.php --backup 20261013_120000"},
                        "batch-rollback": {"args": "<ts> [file...]", "desc": "按时间戳批量回滚(ts=unix秒级, 用$(date +%s))", "example": "--batch-rollback $(date -d '2026-10-23 14:00' +%s) TV/"},
                        "recent-changes": {"args": "[N]", "desc": "服务器最近变更记录", "example": "--recent-changes 5"},
                        "list-since":  {"args": "<ts>", "desc": "列出指定Unix时间戳后变更的文件", "example": "--list-since 1700000000"},
                        "history":     {"args": "", "desc": "本地变更历史", "example": "--history"},
                    }
                },
                "目录文件操作": {
                    "desc": "远程目录文件管理",
                    "commands": {
                        "ls":         {"args": "[path]", "desc": "列远程目录", "example": "--ls TV/assets/"},
                        "cat":        {"args": "<path>", "desc": "查看远程文件内容 (--json直接拿content; --range START END行切片)", "example": "--cat TV/config.php --range 1 50"},
                        "rm":         {"args": "<path...>", "desc": "删除远程文件/目录(支持多文件, 危险需--yes/--allow-destructive)", "example": "--rm test.php a.php --yes"},
                        "mkdir":      {"args": "<path>", "desc": "创建远程目录", "example": "--mkdir TV/newdir/"},
                        "mv":         {"args": "<from> <to>", "desc": "重命名/移动远程文件", "example": "--mv old.php new.php"},
                        "cp":         {"args": "<from> <to>", "desc": "复制远程文件", "example": "--cp a.php b.php"},
                        "info":       {"args": "<path>", "desc": "远程文件元信息(type/size/md5/mode)", "example": "--info TV/config.php"},
                        "read-remote":{"args": "<path> [...]", "desc": "下载远程文件到本地缓存(只读; 支持多文件批量; 改缓存后--write-remote回传); 💡整文件编辑走 read-remote→改缓存→write-remote", "example": "--read-remote TV/config.php TV/db.php"},
                        "read-range": {"args": "<path> <start> <end>", "desc": "[别名] = --cat <path> --range <start> <end> (输出形状兼容)", "example": "--read-range TV/config.php 1 50"},
                        "write-remote":{"args": "<path> [...]", "desc": "回传缓存文件到远程(缓存=read-remote下载所得; CAS冲突检测+自动备份+回读验证; 支持多文件批量; 新文件用--upload-file直传)", "example": "--write-remote TV/config.php TV/db.php"},
                        "edit-remote":{"args": "<path>", "desc": "[别名] = read-remote (下载-改缓存-回传工作流入口)", "example": "--edit-remote TV/config.php"},
                        "upload-file":{"args": "<本地文件> [...]", "desc": "本地文件直传远程(⚠️文件先放WATCH_DIR下, 参数=相对WATCH_DIR路径; 自动写缓存+备份+回读验证; 支持多文件)", "example": "--upload-file Test/new.html Test/new.js"},
                        "grep-remote":{"args": "<pattern>", "desc": "[别名] = --search <p> --any-ext (远程任意扩展名内容检索; --grep-path指定范围, --grep-regex正则)", "example": "--grep-remote sfxClear --grep-path Test/"},
                    }
                },
                "搜索统计": {
                    "desc": "代码搜索与统计",
                    "commands": {
                        "search":     {"args": "<pattern>", "desc": "远程文件内容搜索 (默认仅*.php/*.py/*.js; --any-ext 不限扩展名, 覆盖grep-remote)", "example": "--search 'function test' --any-ext"},
                        "stats":      {"args": "<path>", "desc": "代码行数统计", "example": "--stats TV/"},
                        "snippets":   {"args": "", "desc": "列出服务器已配置代码片段", "example": "--snippets"},
                        "todo":       {"args": "[path]", "desc": "提取TODO/FIXME/HACK注释", "example": "--todo TV/"},
                    }
                },
                "守护模式": {
                    "desc": "自动同步监听",
                    "commands": {
                        "watch":      {"args": "start|status|stop", "desc": "启动/状态/停止守护模式; start 输出启动确认(PID/间隔/模式); 支持--watch-interval/--watch-mode/--watch-subdir/--once; --start/--stop为快捷方式", "example": "--watch start --watch-interval 2"},
                    }
                },
                "管理": {
                    "desc": "权限与配置管理",
                    "commands": {
                        "whoami":     {"args": "", "desc": "当前token权限边界", "example": "--whoami"},
                        "admin":      {"args": "[subcmd]", "desc": "管理操作(stats/config/scopes/tokens)", "example": "--admin stats"},
                        "token-add":  {"args": "<token> <name> <scopes> <perms>", "desc": "创建令牌(需admin权限)", "example": "--token-add xxx name scope read,write"},
                        "token-del":  {"args": "<token>", "desc": "删除令牌(需admin权限)", "example": "--token-del xxx"},
                        "scope-add":  {"args": "<name> [work_dir...]", "desc": "创建scope(需admin权限)", "example": "--scope-add newscope /data/www"},
                        "scope-del":  {"args": "<name>", "desc": "删除scope(需admin权限)", "example": "--scope-del newscope"},
                        "package":    {"args": "<token> [scope...]", "desc": "打包生成客户端zip(需admin权限)", "example": "--package xxx"},
                        "api-help":     {"args": "[ACTION]", "desc": "服务端 API 自描述: 不带值列全部 action 及必填参数; 带 action 名看完整契约(必填/可选/返回/示例/错误码) —— 查服务端能力用这个", "example": "--api-help upload"},
                    }
                },
            },
            "flags": {
                "--plain": "纯文本输出(去emoji, AI可解析)",
                "--json": "结构化JSON输出(见 protocol.json; 顶层 {ok,code,stdout,stderr,command} + 各命令结构化字段自动merge)",
                "--quiet": "默认开启(抑制INFO/warning)",
                "--no-quiet": "恢复完整INFO日志与warning(覆盖默认静默)",
                "--verbose": "DEBUG详细日志(-v)",
                "--yes": "危险操作免确认(-y)",
                "--timeout": "exec超时秒数(默认读conf EXEC_TIMEOUT,上限120)",
                "--conf": "配置文件路径(默认./sync.conf; 可用环境变量AI_SYNC_CONF)",
                "--scope": "命令行覆盖配置文件中的SCOPE",
                "--dry-run": "试运行(不实际写服务器, 输出将执行的操作)",
                "--show-diff": "push时额外展示逐行内容差异",
                "--smart-apply": "--smart 分析后实际应用推送",
                "--smart-strategy": "--smart 比对策略 {auto,md5,size_mtime}",
                "--smart-verify": "deploy后按算法(如md5)复验远程MD5",
                "--fix": "--lint 时自动修复可修复问题",
                "--pre-check": "deploy前在服务器执行检查命令",
                "--post-check": "deploy后在服务器执行检查命令",
                "--post-expect": "deploy后检查期望输出",
                "--backup": "配合--rollback指定备份文件名",
                "--recursive": "配合--ls/--rm 递归操作(-R)",
                "--allow-destructive": "非交互环境放行需授权危险操作(仅用户显式输入; 带路径前缀=限定范围)",
                "--error-log-path": "指定--error-log日志路径",
                "--error-regex": "把--error-grep当正则解释(布尔开关)",
                "--error-since": "--error-log按Unix时间戳过滤",
                "--watch-interval": "watch轮询间隔秒数",
                "--watch-mode": "watch模式 {push,two-way}",
                "--watch-subdir": "watch仅监控子目录",
                "--once": "只运行一轮同步后退出",
                "--op-log-grep": "操作日志关键词过滤(字面匹配)",
                "--cwd": "--exec的服务器端工作目录",
                "--upload-file": "本地文件直传远程(新文件场景; 支持多文件)",
                "--grep-remote": "[别名] = --search --any-ext (远程任意扩展名内容检索)",
                "--grep-path": "配合--grep-remote/--search指定检索范围(默认.全目录)",
                "--grep-regex": "配合--grep-remote/--search按正则解释pattern",
                "--any-ext": "配合--search不限扩展名搜索(html/css/json等, 同grep-remote)",
                "--range": "配合--cat按行切片(--cat <path> --range START END)",
                "--brief": "精简模式: 大输出只保留结论字段(count/summary), 裁剪条目 — AI 上下文友好",
                "--full": "配合 --cat/--status: 强制全量输出(默认大文件自动截断以保护上下文)",
                "--count": "配合 --replace: 限定替换处数, 实际处数不符则拒绝写入(防误伤闸门)",
                "--regex": "配合 --replace/--search: 按正则解释 pattern",
                "--async": "(默认开启, 向后兼容保留) 异步执行",
                "--async-timeout": "异步执行超时秒数",
                "--cp-force": "配合 --cp: 目标已存在时强制覆盖(默认拒绝)",
                "--lint-first": "配合 --deploy: 部署前先做本地语法预检(JS/TS/HTML)",
                "--nginx-reload": "配合 --deploy: 部署后执行 nginx reload",
                "--health-url": "配合 --deploy: 部署后 HTTP 健康检查的 URL",
                "--health-expect": "配合 --deploy: 健康检查期望包含的响应内容",
                "--via-m3u8": "配合 --spider: m3u8 走 M3U8dl 代理(防盗链/CF 绕过)",
                "--sp-method": "配合 --spider: 请求方法 (GET/POST)",
                "--sp-headers": "配合 --spider: 请求头 JSON, 如 {'Referer':'https://x.com/'}",
                "--sp-body": "配合 --spider: 请求体",
                "--sp-maxlen": "配合 --spider: 响应截断长度",
                "--sp-remote": "配合 --spider: 抓取走服务器执行(ai-sync exec 通道)",
                "--sp-timeout": "配合 --spider: 超时秒数",
                "--sp-allow-private": "配合 --spider: 允许访问内网/私有地址(默认禁止)",
            },
            "protocol": {
                "exit_codes": {
                    "0": "成功",
                    "1": "不一致或环境问题 (verify/doctor 检测出差异时 / deploy 文件不存在)",
                    "2": "参数或文件错误 (路径不存在/格式错误)",
                    "3": "远程命令失败 (exec 在服务器端 rc!=0)",
                    "4": "部署/推送/回滚失败 (deploy smart-verify MD5 不符等)",
                    "99": "锁定或策略阻断 (watch 守护占用锁 / push_conflict=block / 危险路径硬拦截)",
                    "1011": "shell 会话失效 (远端会话被清理/超时, 客户端自动重建, 无需人工干预)"
                },
                "json_contract": "--json 时顶层固定含 {ok, code, stdout, stderr, command}; 各命令再 merge 自身结构化字段. 解析方法: 读顶层 command 判断命令, ok=true 且 code=0 表示成功, 失败原因在 stderr. 建议流程: 先 --selfcheck 探测服务端能力 → --whoami 确认权限 → --diff/--status 决策 → 执行 → --verify/--smart-verify 确认 → --op-log 审计; 结构化命令清单见 structured_commands",
                "stderr_contract": "错误信息一律走 stderr; stdout 保持纯净 (JSON 正文或结构化单行 JSON), AI 从 stderr 取错误、从 stdout 取数据",
                "warning_contract": "warnings=提示不阻断 (如 rm -rf <业务目录> 仅警告放行, 命令照常执行, error 为空); error=阻断决定 ok/code; alternatives 字段仅在服务端硬拦 (destructive_blocked) 时出现, AI 照其改命令重试",
                "structured_commands": [
                    "push --dry-run (files[]) / push 实际执行 (uploaded/skipped/failed/batched/failed_files[]/result)",
                    "pull/pull-smart --dry-run (files[]) / pull 实际执行 (pulled/skipped/failed/backed_up)",
                    "smart (strategy/skipped/to_upload/files[])", "deploy --dry-run (total/files[])",
                    "verify (total/identical/mismatch/missing/snap_drift/results[])", "ls (count/entries[])",
                    "backup-list (count/backups[])", "doctor (health/errors/warnings/entries[])",
                    "stats (files/lines/bytes)", "search (hits[])",
                    "lint (ok/syntax_ok/errors/errors_list[] 含 line/severity/message/context)",
                    "info (type/size/mtime/md5/mode/lines)", "read-range (total_lines/lines[])",
                    "cat (content/size/binary; --range 时 lines[])", "cat --json (content 直接拿全文)",
                    "read-remote (path/cache/md5/size)", "write-remote/edit-remote (path/size/result)",
                    "upload-file (path/md5/backed_up/verified/cache)", "grep-remote (pattern/path/hits[])",
                    "read-remote/write-remote/upload-file 批量 (batch/count/results[])",
                    "error-log (format/entries[])", "watch start (pid/mode/interval_s)",
                    "diff (identical/only_local/only_remote/modified/to_push/to_pull/conflicts[])",
                    "status (monitored/counts{ok,dirty,missing}/dirty_files[])",
                    "history (total/entries[]{time,action,count,files})",
                    "whoami (scope/permissions/exec_enabled/work_dir)",
                    "deploy (deploy_id/result/files; --smart-verify md5 追加 verify_ok/verify_failed)",
                    "selfcheck (ok/health_code/server/monitored_files)",
                    "test (rc/ok/stdout/stderr)",
                    "op-log (total/entries[]{ts,command,rc,duration_ms,args})",
                    "watch status (running/pid/mode/interval_s)",
                    "todo (total/by_tag/items[]{path,line,tag,text})",
                    "sync-check (per-file ok/missing/dirty)", "lint-batch (total/failed/result)",
                    "recent-changes (entries[]{time,action,status,path})",
                    "list-since (entries[]{path,size,mtime})", "batch-rollback (total/restored/failed)",
                    "spider (http/content_type/len/truncated/via_m3u8/ms/body)"
                ]
            },
            "quick_start": {
                "开发流程": "pull → 改代码 → push → verify",
                "出问题排查": "doctor → error-log → rollback",
                "批量部署": "smart → deploy --smart-verify",
                "watch同步回滚": "op-log(找watch同步条ts+files) → backup-list <file>(按时间匹配) → rollback <file> <backup>",
                "能力探测": "whoami --json (capabilities/available_actions 一次知道服务端能调什么)",
            },
            "decision_tree": {
                "看远程文件内容": "--cat <path> [--json | --range START END | --full | --brief] (>4MB 先探大小走服务端按行切片, 只回 200 行/--brief 30 行; 二进制只回元信息不下载)",
                "找远程代码": "--search <pattern> [--any-ext] [--grep-path <dir>] [--grep-regex]",
                "改远程单个文件": "--read-remote <path> → (AI改缓存) → --write-remote <path>",
                "改远程多个文件": "--read-remote a b c → (AI改缓存) → --write-remote a b c",
                "创建远程新文件": "--upload-file <本地文件> [更多文件...]",
                "批量同步工作区": "--diff 预览 → 快速推选 --push; 生产发布选 --deploy --smart-verify (判据: 需校验回滚=deploy, 仅同步=push)",
                "语法检查": "--lint <path> | --lint-batch <dir>",
                "端到端一致性验证": "--verify [path]",
                "回滚到备份": "--backup-list <file> → --rollback <file> --backup <备份名> --yes",
                "跑远程命令": "--exec <<'CMD' …命令… CMD (heredoc 免转义; 多步连续/需保持cwd或env → --shell)",
                "抓包爬虫": "--spider <url> [--json|--plain] [--sp-method GET|POST] [--sp-body <json>] [--sp-headers <json>] [--sp-maxlen <N>] [--via-m3u8] [--sp-allow-private] (GET/POST/自定义头/截断/SSRF防护/代理, --json 机器可解析)",
                "查环境与权限": "--selfcheck | --whoami",
                "审计刚才的操作": "--op-log [N]",
                "自动监听同步": "--watch start --once [--watch-interval N] (单次) | setsid nohup --watch start (常驻)",
                "⚠ 别名(勿混淆)": "grep-remote=search --any-ext; read-range=cat --range; edit-remote=read-remote",
            },
            "note": "路径相对watch_dir; 长命令用--exec-file免引号; rm需--yes; 客户端版本见顶层 version 字段(服务端server_version=4.3为服务端版本); 命令选择先看decision_tree; --watch已加choices校验(start/status/stop); 上传家族: 单文件编辑用read/write-remote, 新文件用upload-file, 批量同步用push/deploy; read/write-remote/upload-file支持多文件批量",
        }, ensure_ascii=False))
            return 0
        # ---- 单命令详情 (--help-ai <cmd>) ----
        _detail = _build_ai_detail(_topic)
        if _detail is None:
            print(_json.dumps({"error": f"未知命令: {_topic}",
                               "hint": "--help-ai 查看能力地图 | --help-ai full 查看完整清单"},
                              ensure_ascii=False))
            return 2
        print(_json.dumps(_detail, ensure_ascii=False))
        return 0

    # P2: --conf 内置 — 默认用 sync.py 同目录的 sync.conf (打包时二者同目录),
    #     AI 解压后直接 `python3 sync.py --auto pull` 即可, 无需每次带 --conf
    if args.conf:
        conf_path = Path(args.conf)
    elif os.environ.get("AI_SYNC_CONF"):
        conf_path = Path(os.environ["AI_SYNC_CONF"])
    else:
        conf_path = Path(__file__).resolve().parent / "sync.conf"
    if not conf_path.is_file():
        print(f"❌ 找不到配置文件: {conf_path}", file=sys.stderr)
        # 🆕 FIX-firstuse-v4.7: 首次使用指引 (新 AI 无 conf 时的下一步)
        print("   首次使用: 复制 sync.conf.example 为 sync.conf 并填入 API_URL/API_TOKEN/WATCH_DIR", file=sys.stderr)
        print("   或阅读 README.txt; 能力清单用 --help-ai", file=sys.stderr)
        return 1
    conf = load_conf(conf_path)
    # AI 友好: 配置缺必填字段时友好提示, 不 traceback (urllib unknown url type)
    _missing = []
    if not conf.get("api_url"): _missing.append("API_URL (服务端 sync_api.php 地址, 如 http://host/sync/sync_api.php)")
    if not conf.get("api_token"): _missing.append("API_TOKEN (访问令牌)")
    # P2: WATCH_DIR 允许为空 — 由 _bootstrap_env 按当前环境自动解析并回写
    if _missing:
        print(f"❌ 配置文件 {conf_path} 缺少必填字段:", file=sys.stderr)
        for _m in _missing: print(f"   - {_m}", file=sys.stderr)
        print(f"\n最小配置示例:\n  API_URL = http://your-server/sync/sync_api.php\n  API_TOKEN = your-token\n  WATCH_DIR = /path/to/watch\n  SCOPE = your-scope", file=sys.stderr)
        return 1
    # 兼容: 配置文件同时支持 snake_case 和 UPPER_CASE key (snake_case 作为主格式)
    def _fallback(new_key: str, old_key: str):
        if not conf.get(new_key) and conf.get(old_key):
            conf[new_key] = conf[old_key]
    _fallback("api_url", "api-url"); _fallback("api_url", "server_url"); _fallback("api_url", "url")
    _fallback("api_token", "token"); _fallback("api_token", "access_token")
    _fallback("watch_dir", "watch"); _fallback("watch_dir", "base_dir")
    _fallback("remote_base", "remote")
    _fallback("log_level", "level")
    _fallback("chunked_threshold_kb", "size_threshold")
    _fallback("upload_timeout", "timeout")
    # 🆕 P0 自举: 解析/创建工作区 + 日志跟随 + SSL 自动降级 (只回写安全项; 变更打印到 stderr)
    _boot_changes: list = []
    try:
        _boot_changes = _bootstrap_env(conf, conf_path, getattr(args, "workdir", "") or "")
    except Exception:
        _boot_changes = []
    # 🆕 抓包爬虫: 独立功能, 无需工作目录
    if getattr(args, 'spider', None):
        return cmd_spider(conf, None, None, args.spider,
                          method=args.sp_method, headers=args.sp_headers, body=args.sp_body,
                          timeout=args.sp_timeout, maxlen=args.sp_maxlen,
                          via_m3u8=args.via_m3u8, allow_private=args.sp_allow_private, remote=args.sp_remote, plain=args.plain)
    # 🆕 FIX-http-check-v4.17+: --http-check <url> [--expect <str>] 独立端到端断言
    #    (不再只能当 --deploy 的配套 flag; 有 deploy/auto 时仍走部署路径, 由 cmd_deploy 内部处理)
    if getattr(args, "http_check", "") and getattr(args, "deploy", None) is None and getattr(args, "auto", None) is None:
        return cmd_http_check(conf, None, None, args.http_check, expect=args.expect,
                              timeout=args.sp_timeout or 15, maxlen=args.sp_maxlen or 200000,
                              allow_private=args.sp_allow_private, plain=args.plain)
    if not conf.get("watch_dir"):
        print("❌ 未指定工作目录 (WATCH_DIR 为空)", file=sys.stderr)
        print("   请用 --workdir 指定一个稳定的工作目录 (非临时目录, 重启后仍存在), 例如:", file=sys.stderr)
        print("     python3 sync.py --workdir /你的/稳定/工作目录", file=sys.stderr)
        print("   指定后会自动写入 sync.conf, 后续命令无需再带 --workdir。", file=sys.stderr)
        return 1
    # 🆕 FIX-proxy-auto-v4.7: 自动清除代理环境变量 (AI 无需 env -u 前缀)
    #    默认清除 HTTP_PROXY/HTTPS_PROXY 及小写变体; conf 配 keep_proxy=true 则保留 (内网穿透等特殊场景)
    # 🆕 AUTO-PROXY-DETECT: 清空前先探测直连; 直连不通且存在代理环境变量 → 保留代理并写回 conf
    _proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
    if not conf.get("keep_proxy"):
        _env_proxy = next((os.environ.get(_k) for _k in _proxy_keys if os.environ.get(_k)), "")
        if _env_proxy and not _direct_reachable(conf.get("api_url", "")):
            conf["keep_proxy"] = True
            _persist_conf_key(conf_path, "KEEP_PROXY", "true")
            _h, _p = _api_host_port(conf.get("api_url", ""))
            _boot_changes.append(f"KEEP_PROXY=true (直连 {_h}:{_p} 不通, 走代理 {_env_proxy})")
        else:
            for _k in _proxy_keys:
                os.environ.pop(_k, None)
    if _boot_changes:
        print("[ai-sync] 环境自适配: " + "; ".join(_boot_changes), file=sys.stderr)
    # 🆕 FIX-auto-yes-v4.7: conf 配 auto_yes=true 时危险操作免二次确认 (AI 场景)
    if conf.get("auto_yes"):
        _YES_MODE = True
    if args.scope or conf.get("SCOPE"):
        conf.setdefault("scope", conf.get("SCOPE") or "")
    for k, v in dict(conf).items():
        if k.isupper() and k.lower() not in conf and not (k == "SCOPE" and conf.get("scope")):
            conf[k.lower()] = v

    if args.verbose: conf["log_level"] = "debug"
    # 🆕 FIX-quiet-v4.6: --quiet 提升日志级别到 WARN, 抑制 [INFO] 启动日志噪音
    if getattr(args, "quiet", True) and not getattr(args, "verbose", False):
        conf["log_level"] = "WARN"
    # 🆕 FIX-exectimeout-v4.6: --timeout 未显式指定时读 sync.conf EXEC_TIMEOUT (服务端上限 120)
    if getattr(args, "timeout", None) is None:
        args.timeout = conf.get("exec_timeout", 60)
    # 操作日志上下文 (入口包装函数读取)
    _OPLOG_CTX["conf"] = conf
    _OPLOG_CTX["args"] = args
    # --scope 命令行覆盖配置文件中的 SCOPE
    if args.scope:
        conf["scope"] = args.scope

    log = Logger(Path(conf["log_file"]), max_mb=conf["log_max_mb"], level=conf["log_level"])
    log.info(f"===== ai-sync {APP_VERSION} 启动 =====")

    api = ApiClient(conf["api_url"], conf["api_token"], conf["upload_timeout"], log,
                    conf.get("verify_ssl", True), scope=conf.get("scope", ""))
    # 🆕 v4.39.2: AI 自动升级 — 服务端参考客户端 sync.py 有新版时静默自升级 (默认开)
    #   关闭: --no-auto-upgrade 或 conf AUTO_UPGRADE=false. 日志走 stderr, 不污染 stdout JSON.
    if not getattr(args, "no_auto_upgrade", False) and conf.get("auto_upgrade", True):
        _auto_upgrade(conf, log, api)

    # 🆕 v4.34: 管道批处理 — 比 --batch 更快: 连同 HTTP 往返一起省掉 (N 次往返 → 1 次)
    if getattr(args, "pipeline", False):
        return _run_pipeline(conf, log, api)

    # 🆕 v4.26: 批处理 — AI 连续只读操作一次进程跑完 (省每条 ~200ms 解释器+import 冷启动)
    if getattr(args, "batch", False):
        return _run_batch(conf, log, api)

    # 🆕 P0: 无操作参数 = 自举 + 一条状态摘要 + READY 行 (首次接入只需这一条命令)
    if _BOOTSTRAP_ONLY:
        rc = cmd_status(conf, log, api)
        # --json 时 cmd_status 已输出单行 JSON, 不再追加 READY 以免破坏 JSON 契约
        if "--json" not in sys.argv:
            _next = "--auto pull" if rc == 0 else "--auto fix"
            print(f'READY scope={conf.get("scope", "")} work_dir={conf.get("watch_dir", "")} '
                  f'remote_base={conf.get("remote_base") or "."} '
                  f'proxy={"on" if conf.get("keep_proxy") else "off"} next="{_next}"')
        return rc

    if args.status: return cmd_status(conf, log, api)
    if args.selfcheck: return cmd_selfcheck(conf, log, api, plain=bool(getattr(args, "plain", False)))
    # BUG-4 修复: --sync/--push/--pull/--deploy/--rollback 等写操作需获取 .sync.lock,
    # 防止与 --watch 守护并发改 snapshot 导致状态错乱.
    # dry-run 不改任何状态, 无需锁.
    _WRITE_OPS_NEED_LOCK = not args.dry_run
    # 🆕 P1: 意图模式 — --auto pull|save|fix|status (AI 只需记这 3 条 + 无参启动)
    if getattr(args, "auto", None):
        _intent = args.auto[0]
        _extra = args.auto[1:]
        if _intent == "status":
            rc = cmd_status(conf, log, api)
            # FIX-json-default-v4.39: json 模式不追加 READY, 否则 stdout 混合行导致顶层 merge 失败 (AI 拿不到结构化字段)
            if "--json" not in sys.argv:
                print(f'READY scope={conf.get("scope", "")} work_dir={conf.get("watch_dir", "")} next="--auto pull"')
            return rc
        if _intent == "pull":
            _lk = _acquire_op_lock(conf, log, "auto_pull") if _WRITE_OPS_NEED_LOCK else None
            if _WRITE_OPS_NEED_LOCK and not _lk:
                print("❌ 同步锁被占用, 请稍后重试或 --watch stop", file=sys.stderr)
                return 99
            try:
                return cmd_pull(conf, log, api, target=_extra[0] if _extra else None,
                                dry_run=args.dry_run, smart=True, plain=args.plain,
                                verify_after=not getattr(args, "no_verify", False))
            finally:
                if _lk: _lk.release()
        if _intent == "fix":
            return cmd_doctor(conf, log, api, plain=args.plain)
        if _intent == "save":
            # 防呆: AI 必须显式 --yes 才部署 (防止漏文件名导致部署整个工作区)
            if not args.yes:
                print("⚠ --auto save 需要 --yes 确认 (防呆: 防止漏文件名导致部署整个工作区)", file=sys.stderr)
                print("  正确用法: --auto save [文件...] --yes   (省略文件=部署整个工作区)", file=sys.stderr)
                return 2
            _paths = _expand_deploy_paths(conf, _extra)
            if not _paths:
                print("⚠ 工作区没有可部署文件 (先 --auto pull 或写代码)", file=sys.stderr)
                return 1
            _lk = _acquire_op_lock(conf, log, "auto_save") if _WRITE_OPS_NEED_LOCK else None
            if _WRITE_OPS_NEED_LOCK and not _lk:
                print("❌ 同步锁被占用, 请稍后重试或 --watch stop", file=sys.stderr)
                return 99
            try:
                return cmd_deploy(conf, log, api, _paths, dry_run=args.dry_run, plain=args.plain,
                                  smart_verify="md5", lint_first=True,
                                  http_check=args.http_check, http_expect=args.expect)
            finally:
                if _lk: _lk.release()
        print(f"❌ 未知意图: {_intent} (可用: pull | save | fix | status)", file=sys.stderr)
        return 2
    if args.sync:
        if _WRITE_OPS_NEED_LOCK:
            _lk = _acquire_op_lock(conf, log, "sync")
            if not _lk:
                print("❌ --watch 守护正在同步, 30s 内未拿到锁. 请稍后重试或 --watch stop", file=sys.stderr)
                return 99
            try: return cmd_sync_one(conf, log, api, args.sync)
            finally: _lk.release()
        return cmd_sync_one(conf, log, api, args.sync)
    if args.search: return cmd_search(conf, log, api, args.search, plain=args.plain,
                                      regex=args.grep_regex, any_ext=args.any_ext)
    if args.stats: return cmd_stats(conf, log, api, args.stats, plain=args.plain)
    if args.lint: return cmd_lint(conf, log, api, args.lint, fix=args.fix, plain=args.plain)
    if args.snippets: return cmd_snippet_list(conf, log, api, plain=args.plain)
    # AI 工作流命令
    if args.pull is not None:
        tgt = None if args.pull == "__ALL__" else args.pull
        if _WRITE_OPS_NEED_LOCK:
            _lk = _acquire_op_lock(conf, log, "pull")
            if not _lk:
                print("❌ --watch 守护正在同步, 30s 内未拿到锁. 请稍后重试或 --watch stop", file=sys.stderr)
                return 99
            try: return cmd_pull(conf, log, api, target=tgt, dry_run=args.dry_run, plain=args.plain,
                                 verify_after=not getattr(args, "no_verify", False))
            finally: _lk.release()
        return cmd_pull(conf, log, api, target=tgt, dry_run=args.dry_run, plain=args.plain,
                        verify_after=not getattr(args, "no_verify", False))
    if getattr(args, "pull_smart", None) is not None:
        tgt = None if args.pull_smart == "__ALL__" else args.pull_smart
        if _WRITE_OPS_NEED_LOCK:
            _lk = _acquire_op_lock(conf, log, "pull_smart")
            if not _lk:
                print("❌ --watch 守护正在同步, 30s 内未拿到锁. 请稍后重试或 --watch stop", file=sys.stderr)
                return 99
            try: return cmd_pull(conf, log, api, target=tgt, dry_run=args.dry_run, smart=True, plain=args.plain)
            finally: _lk.release()
        return cmd_pull(conf, log, api, target=tgt, dry_run=args.dry_run, smart=True, plain=args.plain)
    if args.push:
        # 🐛 修复: action='append' + nargs='*' 会产生嵌套列表 [[file1], [file2]], 需展平
        tgts = []
        for sub in args.push:
            if sub:
                tgts.extend(sub)
        tgts = tgts if tgts else None
        if _WRITE_OPS_NEED_LOCK:
            # 方案2: push 单文件场景用 fast_fail —— watch 在跑时立即报错, 不空等 30s.
            # 只有指定了具体文件(非全量)时才 fast_fail; 全量 push 仍等 30s (可等 watch 本轮结束).
            _fast = bool(tgts)
            _lk = _acquire_op_lock(conf, log, "push", fast_fail=_fast)
            if not _lk:
                _hint = _lock_holder_hint(conf) if _fast else ""
                print("❌ 同步锁被占用, 无法执行 push." + (_hint or " --watch 守护正在同步, 30s 内未拿到锁.")
                      + " 请稍后重试, 或先 --watch stop", file=sys.stderr)
                return 99
            try: return cmd_push(conf, log, api, targets=tgts, dry_run=args.dry_run, plain=args.plain,
                                 show_diff=args.show_diff)
            finally: _lk.release()
        return cmd_push(conf, log, api, targets=tgts, dry_run=args.dry_run, plain=args.plain,
                        show_diff=args.show_diff)
    if args.test: return cmd_test(conf, log, api, args.test, timeout=args.timeout, plain=args.plain)
    if args.rollback:
        _lk = _acquire_op_lock(conf, log, "rollback")
        if not _lk:
            print("❌ --watch 守护正在同步, 30s 内未拿到锁. 请稍后重试或 --watch stop", file=sys.stderr)
            return 99
        try: return cmd_rollback(conf, log, api, args.rollback, backup_file=args.backup)
        finally: _lk.release()
    if args.backup_list is not None:
        tgt = None if args.backup_list == "__ALL__" else args.backup_list
        return cmd_backup_list(conf, log, api, target=tgt, plain=args.plain)
    if args.verify is not None:
        # 🆕 v4.18: --verify 支持多文件 (nargs="*"); 零参数(空列表)=全量
        tgt = None if args.verify == [] else args.verify
        return cmd_verify(conf, log, api, target=tgt, plain=args.plain)
    if args.history: return cmd_history(conf, log, api, plain=bool(getattr(args, "plain", False)))
    # v4.1 管理命令
    if args.admin is not None: return cmd_admin(conf, log, api, args.admin)
    if args.token_add: return cmd_token_add(conf, log, api, *args.token_add)
    if args.token_del: return cmd_token_del(conf, log, api, args.token_del)
    if args.scope_add: return cmd_scope_add(conf, log, api, args.scope_add)
    if args.scope_del: return cmd_scope_del(conf, log, api, args.scope_del)
    if args.package: return cmd_package(conf, log, api, args.package)
    # v4.2 AI 增强命令
    if args.api_help is not None: return cmd_api_help(conf, log, api, args.api_help)
    if args.whoami: return cmd_whoami(conf, log, api, plain=bool(getattr(args, "plain", False)))
    if args.sync_check: return cmd_sync_check(conf, log, api, args.sync_check, plain=args.plain)
    if args.lint_batch: return cmd_lint_batch(conf, log, api, args.lint_batch, plain=args.plain)
    if args.recent_changes is not None: return cmd_recent_changes(conf, log, api, args.recent_changes)
    if args.batch_rollback:
        _lk = _acquire_op_lock(conf, log, "batch_rollback")
        if not _lk:
            print("❌ --watch 守护正在同步, 30s 内未拿到锁. 请稍后重试或 --watch stop", file=sys.stderr)
            return 99
        try: return cmd_batch_rollback(conf, log, api, args.batch_rollback)
        finally: _lk.release()
    if args.list_since: return cmd_list_since(conf, log, api, args.list_since, plain=args.plain)
    # v4.3 原子批量部署 (P1: --deploy 省略文件 = 部署整个 WATCH_DIR)
    if args.deploy is not None:
        _deploy_paths = _expand_deploy_paths(conf, args.deploy)
        if not _deploy_paths:
            print("⚠ 工作区没有可部署文件 (先 --auto pull 或写代码)", file=sys.stderr)
            return 1
        if _WRITE_OPS_NEED_LOCK:
            _lk = _acquire_op_lock(conf, log, "deploy")
            if not _lk:
                print("❌ --watch 守护正在同步, 30s 内未拿到锁. 请稍后重试或 --watch stop", file=sys.stderr)
                return 99
            try:
                return cmd_deploy(conf, log, api, _deploy_paths,
                                  pre_check=args.pre_check, post_check=args.post_check,
                                  post_expect=args.post_expect, dry_run=args.dry_run, plain=args.plain,
                                  smart_verify=args.smart_verify or "",
                                  nginx_reload=args.nginx_reload, health_url=args.health_url,
                                  health_expect=args.health_expect, lint_first=args.lint_first,
                                  http_check=args.http_check, http_expect=args.expect)
            finally: _lk.release()
        return cmd_deploy(conf, log, api, _deploy_paths,
                          pre_check=args.pre_check, post_check=args.post_check,
                          post_expect=args.post_expect, dry_run=args.dry_run, plain=args.plain,
                          smart_verify=args.smart_verify or "",
                          nginx_reload=args.nginx_reload, health_url=args.health_url,
                          health_expect=args.health_expect, lint_first=args.lint_first,
                          http_check=args.http_check, http_expect=args.expect)
    # P1-A: Error Log
    if args.error_log is not None:
        if args.error_log == "__LIST__":
            return cmd_error_log_list(conf, log, api, plain=args.plain)
        return cmd_error_log(conf, log, api, args.error_log,
                             path=args.error_log_path, lines=args.error_lines,
                             grep=args.error_grep, regex=args.error_regex,
                             since_ts=args.error_since, before_ts=0, plain=args.plain)
    # 🆕 v4.9: 一键排查模式
    if getattr(args, 'doctor', False):
        return cmd_doctor(conf, log, api, plain=args.plain)
    # 🆕 v4.17: 版本检测 / 健康摘要 / 上线前检查 / 客户端自更新
    if getattr(args, 'check_update', False):
        return cmd_check_update(conf, log, api, plain=args.plain)
    if getattr(args, 'health', False):
        return cmd_health(conf, log, api, plain=args.plain)
    if getattr(args, 'preflight', None):
        return cmd_preflight(conf, log, api, args.preflight, plain=args.plain)
    if getattr(args, 'self_update', False):
        return cmd_self_update(conf, log, api, dry_run=args.dry_run, plain=args.plain)
    # P1-B: Watch 守护模式
    # --start / --stop 快捷方式 (等价于 --watch start/stop)
    if args.stop:
        return cmd_watch_stop(conf, log, api)
    if args.start:
        # 🆕 v4.12-FIX: 命令行 --watch-interval 优先于配置文件 (旧: conf 默认 2 吞掉命令行值)
        cfg_interval = args.watch_interval if args.watch_interval is not None else (conf.get("watch_interval") or 2)
        cfg_mode = args.watch_mode or conf.get("watch_mode") or "push"
        return cmd_watch_start(conf, log, api,
                               interval_s=cfg_interval,
                               mode=cfg_mode,
                               smart=True,
                               subdir=args.watch_subdir,
                               once=args.once)
    if args.watch is not None:
        if args.watch == "status": return cmd_watch_status(conf, log, api, plain=args.plain)
        if args.watch == "stop":   return cmd_watch_stop(conf, log, api)
        # start (默认或显式 start)
        # 🆕 v4.12-FIX: 命令行 --watch-interval 优先于配置文件 (旧: conf 默认 2 吞掉命令行值)
        cfg_interval = args.watch_interval if args.watch_interval is not None else (conf.get("watch_interval") or 2)
        cfg_mode = args.watch_mode or conf.get("watch_mode") or "push"
        return cmd_watch_start(conf, log, api,
                               interval_s=cfg_interval,
                               mode=cfg_mode,
                               smart=True,
                               subdir=args.watch_subdir,
                               once=args.once)
    # P1-C: Smart 跳过
    if args.smart:
        return cmd_smart_diff(conf, log, api, args.smart,
                              strategy=args.smart_strategy,
                              apply_push=args.smart_apply,
                              dry_run=args.dry_run)
    # v4.4 AI 辅助命令
    if args.ls is not None:
        tgt = None if args.ls == "__ALL__" else args.ls
        return cmd_ls(conf, log, api, target=tgt, recursive=args.recursive, plain=args.plain)
    if args.diff is not None:
        tgt = None if args.diff == "__ALL__" else args.diff
        return cmd_diff(conf, log, api, target=tgt, plain=args.plain)
    if args.cat:
        if args.range:
            return cmd_cat(conf, log, api, args.cat, start=int(args.range[0]), end=int(args.range[1]))
        return cmd_cat(conf, log, api, args.cat)
    if args.read_remote:
        return cmd_read_remote(conf, log, api, args.read_remote)
    if args.replace:
        return cmd_replace(conf, log, api, args.replace, old=args.old or "", new=args.new,
                           use_regex=args.regex, want_count=args.count or 0, dry_run=args.dry_run)
    if args.write_remote:
        return cmd_write_remote(conf, log, api, args.write_remote, dry_run=args.dry_run)
    if args.edit_remote:
        return cmd_edit_remote(conf, log, api, args.edit_remote)
    # 🆕 v4.13: 本地文件直传 + 远程内容检索
    if args.upload_file:
        return cmd_upload_file(conf, log, api, args.upload_file, dry_run=args.dry_run)
    if args.grep_remote:
        return cmd_grep_remote(conf, log, api, args.grep_remote,
                               path=args.grep_path or ".", regex=args.grep_regex,
                               plain=bool(getattr(args, "plain", False)))
    # v4.5 AI 远程文件管理命令
    if args.info:
        return cmd_info(conf, log, api, args.info)
    if args.mkdir:
        return cmd_mkdir(conf, log, api, args.mkdir)
    if args.rm:
        return cmd_rm(conf, log, api, args.rm, recursive=args.recursive)
    if args.mv:
        return cmd_mv(conf, log, api, args.mv[0], args.mv[1])
    if args.cp:
        return cmd_cp(conf, log, api, args.cp[0], args.cp[1], force=args.cp_force)
    if args.exec_file:
        # 🆕 --exec-file: 从本地文件读取命令内容 (绕过 argv 单参数 128KB 限制; 大小仅受 HTTP body 限制)
        try:
            with open(args.exec_file, "r", encoding="utf-8") as _ef:
                _exec_file_cmd = _ef.read()
        except Exception as _e:
            print(f"❌ --exec-file 读取失败: {_e} (文件请放当前工作目录用相对路径; /tmp 等绝对路径跨调用可能失效)", file=sys.stderr)
            return 2
        if not _exec_file_cmd.strip():
            print("❌ --exec-file 文件为空", file=sys.stderr)
            return 2
        _exec_cmd = _exec_file_cmd
    elif args.exec_b64:
        # 🆕 --exec-b64: 本地 base64 编码命令, 解码后走与 --exec 相同通道 (规避嵌套引号转义)
        try:
            _exec_b64_cmd = base64.b64decode(args.exec_b64.encode("utf-8")).decode("utf-8")
        except Exception as _e:
            print(f"❌ --exec-b64 解码失败: {_e}", file=sys.stderr)
            return 2
        if not _exec_b64_cmd.strip():
            print("❌ --exec-b64 解码后为空命令", file=sys.stderr)
            return 2
        _exec_cmd = _exec_b64_cmd
    elif args.exec:
        _exec_cmd = args.exec
    else:
        _exec_cmd = None
    if _exec_cmd is not None:
        # FIX-cwd-v4.39: --cwd 不再拼 'cd DIR && CMD' 前缀 (旧实现与服务端原生 cwd 语义冲突:
        #   拼在 wrapper 内以当前 cwd=WORK_DIR/ai-sync 为基准, 而服务端原生以 WORK_DIR 为基准);
        #   cwd 由 _cmd_exec_async 注入 wrapper __AI_CD__ (相对 WORK_DIR, 目录校验+结构化报错)
        # 🆕 v4.18: exec 默认走 async 后台+轮询 (免服务端超时, 长任务无感等待; --async 参数向后兼容)
        return _cmd_exec_async(conf, log, api, _exec_cmd, args)
    # 🆕 持久 shell 工具 (对齐原生 ssh, 面向 AI 易用性)
    if (args.shell is not None or getattr(args, "shell_bg", None) is not None
            or getattr(args, "shell_open", None) is not None or getattr(args, "shell_close", None) is not None
            or getattr(args, "shell_list", False) or getattr(args, "shell_job", None)
            or getattr(args, "shell_kill", None)
            or getattr(args, "shell_env", None)):   # FIX-consistency-v4.36: --shell-env 可独立调用(纯设置持久化)
        return cmd_shell(conf, log, api, args)

    if args.todo is not None:
        return cmd_todo(conf, log, api, args.todo)
    if args.read_range:
        # 🆕 FIX-readrange-arg-v4.17: 参数错误优雅报错, 不再抛 Traceback
        try:
            _rr_start, _rr_end = int(args.read_range[1]), int(args.read_range[2])
        except (ValueError, TypeError):
            print("❌ read-range 的 start/end 必须是整数 (用法: --read-range <path> <start> <end>)", file=sys.stderr)
            return 2
        return cmd_read_range(conf, log, api, args.read_range[0], _rr_start, _rr_end)

    # 操作日志
    if args.op_log is not None:
        try:
            _n = int(args.op_log)
        except (ValueError, TypeError):
            _n = 20
        return cmd_oplog(conf, limit=_n, grep_str=args.op_log_grep, plain=bool(getattr(args, "plain", False)))

    # AI 友好: 未指定任何操作时显示帮助, 不直接开始全量同步 (新用户第一次运行就改远程文件)
    _ACTION_ATTRS = {'status','selfcheck','sync','search','stats','lint','snippets','start','stop',
        'pull','push','test','rollback','backup_list','verify','history','admin','token_add',
        'token_del','scope_add','scope_del','package','whoami','sync_check','lint_batch',
        'recent_changes','batch_rollback','list_since','deploy','error_log','watch','smart',
        'ls','diff','cat','info','mkdir','rm','mv','cp','exec','exec_b64','todo','read_range',
        'shell','shell_bg','shell_open','shell_close','shell_list','shell_job','shell_kill'}
    _has_action = any(getattr(args, _a, None) not in (None, False, [], '') for _a in _ACTION_ATTRS)
    if not _has_action:
        # 🆕 v4.29: 未指定操作时 3 行紧凑指引 (旧版倾倒全部 help, AI 场景纯浪费上下文)
        print("❌ 未指定操作。示例: --ls . | --cat <文件> | --push <文件> | --exec 'ls' | --whoami", file=sys.stderr)
        print("   全部命令: --help-ai (JSON 能力地图) / --help-ai full (完整清单) / -h (人类帮助)", file=sys.stderr)
        return 2

    # 单实例锁
    lock_path = Path(conf["watch_dir"]) / ".sync.lock"
    lock = FileLock(lock_path)
    if not lock.acquire():
        print("❌ 另一个 ai-sync 实例已在运行", file=sys.stderr)
        return 99

    try:
        engine = SyncEngine(conf, log, api)
        return engine.run(once=args.once, dry_run=args.dry_run)
    finally:
        lock.release()
        log.close()

def _main_with_oplog():
    """包装 main(), 记录操作日志 (rc/耗时/命令/参数)"""
    # 🆕 FIX-fixhint-v4.17: 失败时统一注入 fix_hint — AI 失败后直接照抄下一步命令, 不用猜
    _FIX_HINTS = {
        "push": "本地推送失败: 先 --exec 'ls -la <远程路径>' 查权限/磁盘, 重试 --push <file> --allow-destructive; 服务器空间满需清理",
        "pull": "拉取失败: 检查网络/token (--whoami), 重试 --pull --allow-destructive; 本地有改动会被自动备份",
        "deploy": "部署失败已自动回滚: 查 --error-log php_error 看语法/运行时错误, 修本地文件后重新 --deploy <file> --smart-verify",
        "exec": "远程命令失败(rc=3): 看 stderr 的 rc 与错误行, 修正命令重试 --exec (heredoc 免转义)",
        "lint": "语法检查未过: 按输出错误行号修复本地文件, 重新 --push 后 --lint 复验",
        "lint-batch": "批量检查未过: 逐文件修复后重跑 --lint-batch <文件>",
        "mv": "移动失败: 确认源存在/目标不冲突 (目标存在会先备份), 重试 --mv <src> <dst> --allow-destructive",
        "cp": "复制失败: 目标已存在需 --cp-force 覆盖 (覆盖前自动备份)",
        "rollback": "回滚失败: 用 --backup-list <file> 查可用备份名, 重试 --rollback <file> --backup <名> --yes",
        "upload-file": "上传失败: 先确认本地路径存在且是文件(不是目录); 若报 path_outside_scope, 把文件放到 WATCH_DIR 内或改用相对 WATCH_DIR 的路径; 重试 --upload-file <file>",
        "write-remote": "回传失败: 先 --read-remote <p> 下载缓存, 修改后 --write-remote <p>",
        "verify": "校验不一致: 用 --diff <path> 看差异, --push 或 --pull 对齐后重验",
        "preflight": "上线前检查未过: 按 lint/MD5 差异修复后重跑 --preflight",
        "doctor": "环境异常: 按 entries 中错误逐项处理, 修复后重跑 --doctor 验证",
        "health": "健康检查异常: 查 --doctor 详情, 磁盘/内存不足需清理",
        "sync": "单文件同步失败: 确认文件在监控范围内, 重试 --sync <file>",
        "sync-check": "推送决策异常: 确认文件存在, 重试 --sync-check <file>",
        "spider": "抓取失败: 确认 URL 以 http(s):// 开头且可访问; 内网/本机地址需加 --sp-allow-private; 超时用 --sp-timeout <秒> 加大; 大响应用 --sp-maxlen <字节> 截断; 无法直连的流媒体用 --via-m3u8 走代理",
        "ls": "列目录失败: 确认远程路径存在 (--info <path> 查), 权限不足用 --exec 'ls -la <path>' 排查",
        "shell": "远程命令失败: 看 stderr 的 rc 与错误行, 修正命令重试 --shell; 多步连续/需保持cwd或env 用 --shell 会话",
        "whoami": "身份/权限查询失败: 检查 conf 的 token 是否有效 (--selfcheck 探测连通), 失效需重新自举或换 token",
        "info": "查询失败: 确认路径存在 (--ls 父目录), 权限不足用 --exec 'ls -la <path>' 排查",
        "cat": "读文件失败: 确认文件存在 (--ls 父目录), 二进制/大文件用 --range START END 或 --brief 切片",
        "search": "搜索失败: 确认 pattern 正确, 扩大范围用 --any-ext 或 --grep-path <dir>",
        "http-check": "页面断言失败: 确认 URL 可访问 (--spider <url> 抓取看), 页面内容不符调整 --expect 字符串",
        "status": "状态查询失败: 确认工作目录已配置 (--workdir <稳定目录> 自举), 重试 --status",
        "diff": "差异比对失败: 确认路径存在且是文件/目录, 重试 --diff <path>",
        "backup-list": "查询备份失败: 确认文件在监控范围, 重试 --backup-list <file>",
        "error-log": "日志读取失败: 先不带名字 --error-log 列可用日志, 再指定 --error-log <名>",
        "recent-changes": "变更记录查询失败: 确认服务器同步正常 (--status), 重试 --recent-changes [N]",
        "whoami": "权限查询失败: 确认 token/conf 正确 (export AI_SYNC_CONF=同步.conf), 重试 --whoami --json",
        "todo": "TODO 提取失败: 确认目录存在且含 *.php/*.py/*.js, 重试 --todo <dir>",
        "stats": "统计失败: 确认目录存在, 重试 --stats <dir>",
        "snippets": "代码片段查询失败: 确认服务端已配置 snippets, 重试 --snippets",
        # 🆕 FIX-fixhint-v4.22: 补齐 19 个无 fix_hint 的主命令 (AI 失败后直接照抄下一步)
        "auto": "auto 失败: 拆成单步执行 --auto pull (拉取) / --auto save [文件] --yes (部署) / --auto fix (排查) 定位是哪步出错",
        "pull-smart": "增量拉取失败: 检查网络/token (--whoami), 或退级用全量 --pull <路径> 拉取",
        "rm": "删除失败: 确认路径在 scope 内且需 --allow-destructive 授权 (危险操作默认拦截), 重试 --rm <路径> --allow-destructive --yes",
        "backup": "备份失败: 确认文件在监控范围 (--status), 重试 --backup <文件>",
        "batch-rollback": "批量回滚失败: 用 --backup-list <文件> 确认备份存在, 或单文件 --rollback <文件> --backup <名> --yes",
        "mkdir": "创建目录失败: 确认父路径存在且需授权 (--allow-destructive), 重试 --mkdir <路径> --allow-destructive",
        "info": "路径信息查询失败: 确认远程路径存在, 用 --ls <父目录> 排查",
        "admin": "管理操作失败: --admin stats 看总览, 子命令用 --admin scopes/tokens/audit, 未知子命令见报错列出的可用项",
        "token-add": "令牌添加失败: 语法 --token-add <TOKEN> <名称> <作用域> <权限>, 用 --admin tokens 看现状",
        "token-del": "令牌删除失败: 先 --admin tokens 拿令牌名, 重试 --token-del <TOKEN> --yes",
        "package": "打包失败: 确认 token 有效 (--whoami) 且 scope 存在 (--admin scopes), 重试 --package <TOKEN> <SCOPE>",
        "selfcheck": "自检失败: 按输出条目逐项处理 (网络/token/权限), 修复后重跑 --selfcheck",
        "test": "远程测试失败: 确认文件存在且 php 语法正确 (--lint), 修好后重试 --test <文件>",
        "check-update": "更新检查失败: 网络异常, 重试 --check-update; 或跳过直接 --self-update",
        "self-update": "自更新失败: 网络/服务端旧版问题, 用 --check-update 看差异, 稍后重试 --self-update",
        "watch": "守护失败: --watch status 看状态, --watch stop 停旧实例后 --watch start 重启 (后台常驻用 nohup)",
        "op-log": "操作日志查询失败: 确认已运行过操作 (日志按次记录), 重试 --op-log [N]",
        "history": "变更历史查询失败: 确认服务器同步正常 (--status), 重试 --history",
        "help-ai": "帮助查询失败: 该参数接受 <命令名> 或 full, 如 --help-ai deploy / --help-ai full",
    }
    start = time.time()
    rc = 1
    # 🆕 FIX-json-default-v4.39: 面向 AI 默认结构化输出 — conf 缺省/JSON_DEFAULT=true 时自动注入
    #   --json, 顶层恒为 {ok,code,stdout,stderr,command} (AI 零参数拿契约);
    #   --plain 或 conf JSON_DEFAULT=false 保持人类文本; 显式 --json 恒生效.
    #   注入必须在 _json_mode 判断之前, 否则命令走 JSON 分支但缺顶层包装.
    if "--json" not in sys.argv and "--plain" not in sys.argv:
        try:
            _jconf = None
            for _a in sys.argv[1:]:
                if _a == "--conf":
                    _ji = sys.argv.index(_a)
                    if _ji + 1 < len(sys.argv): _jconf = sys.argv[_ji + 1]; break
            if not _jconf: _jconf = os.environ.get("AI_SYNC_CONF", "")
            if not _jconf: _jconf = str(Path(__file__).resolve().parent / "sync.conf")
            _jv = load_conf(Path(_jconf)).get("json_default", True) if Path(_jconf).is_file() else True
        except Exception:
            _jv = True
        if _jv:
            sys.argv.append("--json")
    # 🆕 FIX-json-v4.7: --json 捕获命令 stdout/stderr, 输出结构化 JSON (AI 断言 ok 即完成)
    _json_mode = "--json" in sys.argv
    _out = _err = None
    _old_out = _old_err = None
    if _json_mode:
        import io as _io
        _out, _err = _io.StringIO(), _io.StringIO()
        _old_out, _old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _out, _err
    try:
        rc = main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        rc = 2
        import traceback as _tb
        _tb.print_exc()
    finally:
        duration_ms = int((time.time() - start) * 1000)
        _conf = _OPLOG_CTX.get("conf")
        _args = _OPLOG_CTX.get("args")
        if _conf is not None and _args is not None:
            _record_oplog(_conf, _args, rc, duration_ms)
        if _json_mode:
            sys.stdout, sys.stderr = _old_out, _old_err
            import json as _json
            import re as _re
            _out_s, _err_s = _out.getvalue().strip(), _err.getvalue().strip()
            _payload = {"ok": rc == 0, "code": rc, "stdout": _out_s, "stderr": _err_s}
            # 🆕 v4.11-JSON: 顶层统一 command 字段 (首个非 flag 的 --xxx 操作参数; AI 无需自己 parse)
            _CMD_FLAGS = ("--json", "--plain", "--quiet", "--verbose", "--yes", "--conf",
                          "--timeout", "--dry-run", "--show-diff", "--cwd", "--backup",
                          "--error-lines", "--error-grep", "--watch-interval",
                          "--grep-path", "--grep-regex", "--fix", "--smart-strategy",
                          "--smart-verify", "--pre-check", "--post-check", "--post-expect",
                          "--recursive", "--error-log-path", "--error-since", "--watch-mode",
                          "--watch-subdir", "--once", "--op-log-grep")
            _cmd_found = None
            for _a in sys.argv[1:]:
                if _a.startswith("--") and _a not in _CMD_FLAGS:
                    _cmd_found = _a[2:].split("=")[0]
                    break
            if _cmd_found:
                _payload["command"] = _cmd_found
            else:
                # FIX-json-default-v4.39: argparse 错误场景(如 --error-lines 独立缺参)可能全是修饰参数,
                # fallback 跳过值参数(conf/workdir/scope)取第一个 -- 参数, 保证顶层契约恒有 command 字段
                _skip_next = False
                for _a in sys.argv[1:]:
                    if _skip_next:
                        _skip_next = False
                        continue
                    if _a in ("--conf", "--workdir", "--scope"):
                        _skip_next = True
                        continue
                    if _a.startswith("--"):
                        _payload["command"] = _a[2:].split("=")[0]
                        break
            # 🆕 v4.8-DRYJSON: stdout 是单行合法 JSON → merge 到顶层 (AI 一次 parse 拿结构化字段,
            #   不用二次 parse stdout 字符串). 任何命令都可用此模式透传结构化结果.
            try:
                _merged = _json.loads(_out_s)
                if isinstance(_merged, dict):
                    # FIX-remote-stderr-v4.36: 内层与顶层同名语义字段会互相遮蔽。
                    #   典型: exec/shell 远程 stderr 非空, 但顶层 stderr 只是客户端元信息(常为空),
                    #   "if _k not in _payload" 把远程 stderr 吞掉 → AI 永远看不到真实报错。
                    #   这里把所有同名语义字段同时暴露为 remote_*, 保证信息不丢。
                    _SEM = ("ok", "code", "stdout", "stderr", "return_code")
                    for _k in _SEM:
                        if _k in _merged and ("remote_" + _k) not in _payload:
                            _payload["remote_" + _k] = _merged[_k]
                    for _k, _v in _merged.items():
                        if _k not in _payload:
                            _payload[_k] = _v
                    # FIX-consistency-v4.36: 消除双层 JSON — 当命令打印的正是内层 JSON 对象
                    # (如 --shell --json 的 {"action":"shell","stdout":...}),
                    # 顶层 stdout 字符串与内层 JSON 串完全一致, AI 被迫二次 parse。
                    # 此处: 顶层 stdout == 内层 JSON 串本身时, 提升内层 stdout 为顶层 stdout,
                    # 其余内层字段照常 merge → AI 一次 json.loads 拿全, 无需剥两层。
                    if "stdout" in _merged and isinstance(_merged["stdout"], str) \
                       and _payload.get("stdout", "").strip() == _out_s:
                        _payload["stdout"] = _merged["stdout"]
            except Exception:
                pass
            # 🆕 FIX-remote-rc-v4.7: exec 的远程退出码从 stderr 提取到顶层 (AI 无需二次 parse)
            _m = _re.search(r"\[exec\] rc=(-?\d+)", _err_s)
            if _m:
                _payload["remote_rc"] = int(_m.group(1))
            # 🆕 FIX-fixhint-v4.17: 失败时统一注入 fix_hint — AI 失败后直接照抄下一步命令
            if _payload.get("code", 0) != 0 or (rc != 0 and _payload.get("ok", True) is not False):
                _hint = _FIX_HINTS.get(_payload.get("command", ""))
                # FIX-consistency-v4.36: 错误级细分指路优先 — 按错误特征给精确下一步,
                #   覆盖命令级通用 hint (not_found 指"确认路径"而非"查用法", 避免 AI 绕路)
                _err_l2 = _err_s.lower()
                _pout_s = str(_payload.get("stdout", ""))
                if "not_found" in _err_l2 or "不存在" in _err_s or '"error": "not_found"' in _pout_s:
                    _hint = "目标不存在: 用 --ls <目录> 确认远程路径, 或 --search '<关键词>' 定位后重试"
                elif "bad_step" in _err_l2 or "缺 action" in _err_s or "missing_steps" in _err_l2:
                    _hint = "steps 格式: {\"action\":\"stat\",\"path\":\"a.txt\"}, 契约见 --api-help pipeline"
                elif "permission" in _err_l2 or "无权" in _err_s or "forbidden" in _err_l2 or "blocked" in _err_l2 or "越界" in _err_s:
                    _hint = "权限/策略被拒: --whoami 看权限边界; 被拦时照替代方案改命令重试"
                # 🆕 FIX-fixhint-v4.22: 参数格式错误时用精确指路覆盖通用 hint
                #    (实测 --search x TV/ 报"位置参数多余"却给"确认pattern"通用文案 — 指错方向比没指更糟)
                elif "usage:" in _err_s:
                    _hint = "参数格式错误: 用 --help-ai <命令> 查该命令正确参数 (场景速查: --help-ai; 多余的位置参数需用 --grep-path 等命名参数)"
                # exec 异步结果: 超时/远端非零码要给出精确下一步, 不能沿用通用 "rc=3" 文案
                if _payload.get("command") == "exec" and _payload.get("async"):
                    _rrc = _payload.get("remote_rc")
                    if _payload.get("timed_out"):
                        _hint = ("远程命令超时被终止(remote_rc=124, --timeout=%s): 确实需要更久就调大 --timeout; "
                                 "长任务改用 --shell-bg 后台执行再 --shell-job 轮询") % _payload.get("timeout")
                    elif isinstance(_rrc, int) and _rrc != 0:
                        _hint = "远程命令失败(remote_rc=%d): 看 remote_stderr 修正命令后重试 --exec" % _rrc
                if _hint:
                    _payload["fix_hint"] = _hint
            print(_json.dumps(_payload, ensure_ascii=False))
        else:
            # 🆕 FIX-help-on-error: 非 --json 模式失败时统一指路, 避免 AI 失败后瞎摸索绕路
            if rc != 0:
                _CMD_FLAGS_S = ("--json","--plain","--quiet","--verbose","--yes","--conf",
                                "--timeout","--dry-run","--cwd","--backup","--help","--version")
                _cmd = ""
                for _a in sys.argv[1:]:
                    if _a.startswith("--") and _a not in _CMD_FLAGS_S:
                        _cmd = _a[2:].split("=")[0]
                        break
                if _cmd:
                    # 非 json 模式统一查用法 (细分指路见 --json 的 fix_hint 逻辑)
                    print(f"💡 查 '{_cmd}' 用法: --help-ai {_cmd}  |  全部场景速查: --help-ai", file=sys.stderr)
                else:
                    print(f"💡 场景速查: --help-ai  |  单命令详情: --help-ai <命令>", file=sys.stderr)
    # 🆕 FIX-json-summary: 非 --json 模式 stderr 末尾附一行结构化结果, AI 忘加 --json 也能 parse
    #    格式: <<AI_RESULT>>{"ok":...,"code":...,"command":...}<<END>>
    if not _json_mode:
        try:
            import json as _json_s
            _CMD_FLAGS_S2 = ("--json","--plain","--quiet","--verbose","--yes","--conf",
                             "--timeout","--dry-run","--cwd","--backup","--help")
            # FIX-v4.31: --version 是一条真正的一级命令 (有自己的 command 名), 旧代码把它当成
            #   普通 flag 过滤掉, 导致摘要行 command="" 与 stdout 里的 "command":"version" 自相矛盾.
            _cmd_s = "version" if "--version" in sys.argv[1:] else ""
            if not _cmd_s:
                for _a in sys.argv[1:]:
                    if _a.startswith("--") and _a not in _CMD_FLAGS_S2:
                        _cmd_s = _a[2:].split("=")[0]
                        break
            _sum = {"ok": rc == 0, "code": rc, "command": _cmd_s}
            print(f"<<AI_RESULT>>{_json_s.dumps(_sum, ensure_ascii=False)}<<END>>", file=sys.stderr)
        except Exception:
            pass
    return rc

if __name__ == "__main__":
    sys.exit(_main_with_oplog())
