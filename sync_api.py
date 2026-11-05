#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 金鑫

"""
ai-sync v4.3 服务端 —— Python 还原版 (sync_api.py)
=================================================
单 POST 端点 + JSON body，严格对齐 PHP 版 sync_api.php 的协议契约：
  - 响应信封: ok() -> {ok:true, code:0, ...} / fail() -> {ok:false, code, error, message, fix_hint, ...}
  - 错误码:   1001 鉴权 / 1002 权限 / 1003 限流 / 1004 路径 / 1005 不存在
              / 1006 过大 / 1007 校验 / 1008 写入 / 1009 exec禁用 / 1010 备份不存在
              / 1011 参数 / 1012 分片缺失 / 1013 webhook / 1099 内部
  - 鉴权:     多目录模式, tokens 字典 + admin_token, scope 选择 + action->permission 映射
  - 安全:     IP 白名单(精确+CIDR), safe_path 防穿越, safe_mode 强制权限下限, gzip 压缩
  - 文件传输: base64 content_b64, base_md5 CAS 乐观锁, 原子写(tmp+rename), 备份命名
  - exec:     timeout 包裹 sh -s, 退出码落临时文件, 124=超时

用法:
  python3 sync_api.py --config sync_config.json --host 0.0.0.0 --port 5443
"""

import argparse
import base64
import binascii
import fnmatch
import glob
import hashlib
import secrets
import io
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import threading
import zlib
from datetime import datetime, timezone
from math import ceil
from contextlib import contextmanager

try:
    from shlex import quote as _shq
except ImportError:  # pragma: no cover
    def _shq(s):
        return "'" + str(s).replace("'", "'\\''") + "'"
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ==================== 错误码定义 ====================
E = {
    'OK': 0,
    'UNAUTHORIZED': 1001,
    'FORBIDDEN': 1002,
    'RATE_LIMITED': 1003,
    'INVALID_PATH': 1004,
    'NOT_FOUND': 1005,
    'FILE_TOO_LARGE': 1006,
    'CHECKSUM_MISMATCH': 1007,
    'UPLOAD_FAILED': 1008,
    'EXEC_DISABLED': 1009,
    'BACKUP_NOT_FOUND': 1010,
    'INVALID_PARAM': 1011,
    'CHUNK_MISSING': 1012,
    'WEBHOOK_FAILED': 1013,
    'VALIDATION_FAILED': 1014,
    'INTERNAL': 1099,
}

# ==================== 默认配置 (无 sync_config.json 时回退, fail-closed) ====================
DEFAULT_CONFIG = {
    'TOKEN': '',
    'WORK_DIR': '/tmp/ai-sync-default/',
    'BACKUP_DIR': 'ai-sync/backups/',
    'PROBE_DIR': 'ai-sync/probes/',
    'CHUNK_DIR': 'ai-sync/chunks/',
    'WEBHOOK_DIR': 'ai-sync/webhooks/',
    'AUDIT_LOG': 'ai-sync/audit.log',
    'AUDIT_MAX_SIZE': 10 * 1024 * 1024,
    'AUDIT_MAX_KEEP': 5,
    'METRICS_FILE': 'ai-sync/metrics.json',
    'BACKUP_KEEP': 5,
    'MAX_SIZE': 64 * 1024 * 1024,
    'MAX_CHUNKED_SIZE': 1024 * 1024 * 1024,
    'CHUNK_TTL_DAYS': 7,
    'CHUNK_SIZE_DEFAULT': 4 * 1024 * 1024,
    # batch_deploy 单次最多文件数 (可由 scope 配置 max_files_per_deploy 覆盖)
    'MAX_FILES_PER_DEPLOY': 50,
    'EXEC_ENABLED': False,
    'EXEC_ALLOW_IPS': [],
    'EXEC_TIMEOUT_MAX': 120,
    'ALLOW_IPS': [],
    'RATE_LIMIT': {'enabled': True, 'per_token_per_minute': 60, 'per_ip_per_second': 10, 'upload_per_minute': 30},
    'GZIP_THRESHOLD_KB': 16,
    'GZIP_FORCE': False,
    # 超过该大小 (KB) 的 download 走流式输出, 峰值内存 O(块)
    'STREAM_THRESHOLD_KB': 2048,
    # 客户端安装包 (含 token) 默认放 Web 根之外, 下载走 pkg_key 能力 URL
    'PACKAGE_DIR': os.path.join(tempfile.gettempdir(), 'ai-sync-packages'),
    'PACKAGE_TTL': 86400,
    'AUDIT_ENABLED': True,
    'DEBUG': False,
    'API_VERSION': '3.2',
}

ALL_PERMS = ['read', 'write', 'delete', 'exec', 'lint', 'backup', 'admin']

# action => permission 映射
ACTION_PERMS = {
    'ping': 'read', 'help': 'read', 'health': 'read', 'metrics': 'read',
    'download': 'read', 'stat': 'read', 'list': 'read', 'diff': 'read', 'checksum': 'read',
    'search': 'read', 'todo': 'read', 'stats': 'read', 'read_range': 'read', 'doc': 'read',
    'snippet_get': 'read', 'snippet_list': 'read', 'backup_ls': 'read', 'webhook_list': 'read',
    'webhook_test': 'read', 'whoami': 'read', 'sync_check': 'read', 'recent_changes': 'read',
    'client_download': 'read',
    'upload': 'write', 'upload_batch': 'write', 'chunked_upload': 'write', 'batch_deploy': 'write',
    'rename': 'write', 'copy': 'write', 'mkdir': 'write', 'format': 'write',
    'snippet_set': 'write', 'webhook_set': 'write',
    'delete': 'delete', 'snippet_delete': 'delete', 'webhook_delete': 'delete',
    'php_run': 'exec', 'exec': 'exec',
    # 持久 shell 工具(对齐原生 ssh): 与 exec 同一权限位
    'shell_open': 'exec', 'shell_run': 'exec', 'shell_job': 'exec',
    'shell_kill': 'exec', 'shell_close': 'exec', 'shell_list': 'exec',
    'lint': 'lint', 'lint_batch': 'lint',
    'oplog_append': 'write', 'oplog_list': 'admin', 'oplog_view': 'admin',
    'backup_rb': 'backup', 'batch_rollback': 'backup',
    'error_log': 'read', 'error_log_list': 'read',
    'config_get': 'admin', 'config_reload': 'admin', 'scopes_list': 'admin',
    'tokens_list': 'admin', 'token_add': 'admin', 'token_delete': 'admin',
    'scope_add': 'admin', 'scope_delete': 'admin', 'audit_log': 'admin',
    'admin_stats': 'admin', 'client_package': 'admin', 'package_list': 'admin', 'package_delete': 'admin',
    # FIX-v4.34: pipeline 自身只需最低权限 —— 它本身不读写任何东西,
    #   每个 step 都会独立走 dispatch 做鉴权/scope/权限位校验, 所以真实权限下沉到 step。
    #   若这里不注册, ACTION_PERMS.get(...) 默认回落到 'admin', AI 普通 token 就用不了这个功能了。
    'pipeline': 'read',
}

# ==================== 全局状态 ====================
STATE = {
    'RAW_CONFIG': None,
    'CONFIG': dict(DEFAULT_CONFIG),
    'MULTI_DIR': False,
    'lock': threading.RLock(),          # 配置写锁 (config 原子更新)
    'rate_lock': threading.Lock(),
    'start_time': time.time(),
    'server_version': '4.3-py',
    '_chunk_cleanup_ts': 0.0,           # 上次过期分片清理时间 (节流用)
}

# 每请求上下文 (thread-local)。
# 原因: STATE['CONFIG'] / STATE['_client_ip'] 是进程级全局, 但服务端是多线程并发处理。
# 若 audit_log/client_ip/白名单等直接读全局, 并发不同 scope 时会串数据 ——
# 实测: backup scope 的请求被写进了 xbxbb scope 的 audit.log;
# 更严重的是 exec 的 IP 白名单可能用到别的请求的 IP (并发窗口内可绕过白名单)。
# 通用错误码提示: HELP_SPEC 里没写 err 的 action, help 会自动补上这几条。
# (它们对任何 action 都成立; 手写进每个条目反而会随实现演进而过时)
COMMON_ERRORS_HINT = [
    '1001 未授权(token 无效或缺失)',
    '1002 无权限(权限位不足/scope 未授权)',
    '1003 限流(请求过快, 稍后重试或改用 pipeline 合并请求)',
    '1011 参数非法(缺失或类型错误)',
    '1099 服务器内部错误',
]

_REQUEST_CTX = threading.local()

# FIX-v4.34 pipeline: 单次请求的批量上限 / 黑名单 / 写类集合
PIPELINE_MAX_STEPS = 50
# 不可逆或高危 —— 不允许在管道里执行, 必须单独调用(单独调用有独立审计与确认)
PIPELINE_FORBIDDEN = frozenset({
    'pipeline',                                          # 防嵌套放大
    'exec', 'php_run',                                   # 任意代码执行
    'delete',                                            # 删除不可逆
    'rename', 'copy',                                    # 路径变更不可逆(易误伤)
    'batch_rollback', 'backup_rb',                       # 回滚/还原不可逆
    'config_reload',                                     # 全局副作用
    'token_add', 'token_delete',                         # 凭据增删
    'scope_add', 'scope_delete',                         # scope 增删
    'webhook_set', 'webhook_delete', 'webhook_test',     # 外部副作用
    'client_package',                                    # 重操作(打包易被放大成 DoS)
})
# 写类 —— 这些在管道内仍照常计限流配额(防 सुंदr绕过 upload_per_minute)
PIPELINE_MUTATING = frozenset({
    'upload', 'upload_batch', 'batch_deploy', 'chunked_upload',
    'mkdir', 'snippet_set', 'oplog_append', 'todo', 'doc', 'format', 'lint_batch',
})


def _ctx_cfg():
    """当前请求生效的配置 (线程内); 未设置时回退全局默认。"""
    c = getattr(_REQUEST_CTX, 'cfg', None)
    return c if c is not None else STATE['CONFIG']


def _ctx_ip():
    """当前请求的客户端 IP (线程内, 已按 TRUSTED_PROXY 解析 XFF)。"""
    ip = getattr(_REQUEST_CTX, 'ip', None)
    return ip if ip is not None else STATE.get('_client_ip', '')

# 错误码短标签 (help 索引用): 显式短名, 不从长文案切词, 保证两端逐字一致
ERROR_LABELS = {
    1001: '未授权(token 无效或缺失)', 1002: '无权限(权限位不足/scope 未授权)',
    1003: '限流(请求过快)', 1004: '路径非法(越界/绝对路径/../)',
    1005: '未找到(文件或目录不存在)', 1006: '文件过大(超过 MAX_SIZE)',
    1007: '校验失败(md5/乐观锁冲突)', 1008: '写入失败(权限/磁盘)',
    1009: 'exec 未启用', 1010: '备份不存在', 1011: '参数非法(缺失或类型错误)',
    1012: '分片缺失', 1099: '服务器内部错误',
}

ERROR_HINTS = {
    1001: '检查请求是否携带正确 token; admin 操作需 admin_token',    1002: '当前 token 无此权限, 用 whoami API 查询权限边界; 或确认 scope 是否在 token 授权范围',
    1003: '请求过快, 降低频率或联系管理员调整 rate_limit',
    1004: '路径越界: 只接受相对 work_dir 的相对路径, 不要用绝对路径或含 ../',
    1005: '文件/目录不存在, 先用 list 确认路径; 创建用 mkdir/upload',
    1006: '文件过大, 改用 chunked_upload 分片上传; 或联系管理员调大 max_size',
    1007: 'MD5 校验失败, 重新计算本地 md5 后重传; content_b64 可能被截断',
    1008: '服务器写入失败, 检查目录权限/磁盘空间',
    1009: 'exec 未启用, 联系管理员在 sync_config.json 开启 exec_enabled',
    1010: '备份不存在, 用 backup_ls 列出可用备份',
    1011: '参数缺失或非法, 查看错误详情确认字段名',
    1012: '分片缺失, 重新上传缺失的分片; 或用 chunked_upload resume 查询已传分片',
    1014: '语法检查未通过, 查看 issues/first_error 的行号并修复; 修复后用 lint 复检',
    1099: '服务器内部错误, 查看日志; 可尝试回滚 (backup_rb)',
}


# ==================== 自描述: action 契约表 (AI 零文档接入) ====================
# help action 的唯一数据源。PHP 端保持一致 (compare_all 有 help 对齐用例)。
# 结构: name -> d(描述) r(必填) o(可选) ret(返回) eg(示例) err(常见错误) see(相关)
HELP_SPEC = {
    'ping': {
        'd': '探测连通性/版本号/全部可用 action/当前 token 权限',
        'r': [],
        'eg': {'action': 'ping'},
        'ret': 'version,time,work_dir,backup_dir,probe_dir,chunk_dir,exec_enabled,actions,scope,token_name,is_admin,permissions,available_scopes,help(help 命令用法提示)',
        'see': ['whoami', 'health', 'help'],
    },
    'health': {
        'd': '健康检查: 目录可写/磁盘空间/内部目录完整性',
        'r': [],
        'eg': {'action': 'health'},
        'ret': 'version,php_version,uptime_s,work_dir,work_dir_writable,disk_free_mb,disk_total_mb,extensions',
        'see': ['ping', 'admin_stats'],
    },
    'metrics': {
        'd': '调用统计: 各 action 次数与错误码分布',
        'r': [],
        'eg': {'action': 'metrics'},
        'ret': 'actions{name:count},errors[{code,count}],total,since,last_ts',
        'see': ['admin_stats', 'audit_log'],
    },
    'help': {
        'd': '自描述: 无参返回全部 action 索引; 传 name=<action> 返回该 action 的参数契约、示例与返回字段',
        'r': [],
        'o': ['name:str 要详查的 action 名(别名 about/topic/target)'],
        'ret': '无参: actions[{n:名,d:描述,r:必填}],total,usage,envelope,error_codes; 详查: action,desc,required,optional,returns,example,errors,see_also',
        'eg': {'action': 'help', 'name': 'upload'},
        'see': ['ping'],
    },
    'client_download': {
        'd': '返回服务端参考客户端 sync.py 原始字节 (客户端 --self-update 自动升级用; 响应头 X-Client-Md5 携带 md5)',
        'r': [],
        'eg': {'action': 'client_download'},
        'ret': '原始字节流 (application/octet-stream)',
        'see': ['whoami'],
    },
    'whoami': {
        'd': '当前 token 的身份、scope、权限位、可用 scope 列表',
        'r': [],
        'eg': {'action': 'whoami'},
        'ret': 'token_name,scope,is_admin,permissions,available_scopes,available_actions,capabilities(各能力开关),workflow_hint(推荐工作流),work_dir,exec_enabled,api_version,server_time,server_timezone,max_size,max_chunked_size,max_files_per_deploy,chunk_size_default,chunk_ttl_days,backup_keep,rate_limit',
        'see': ['ping'],
    },
    'upload': {
        'd': '写单个文件(覆盖)或创建/替换软链接(传 link_target)。自动备份 + PHP 文件语法检查',
        'r': ['path:str 相对 work_dir 的路径', 'content_b64:str 文件内容 base64(空文件请显式传 ""); 软链接改用 link_target'],
        'o': ['link_target:str 软链接目标(相对路径); 传此字段时忽略 content_b64',
              'link_target_b64:str link_target 的 base64 原始字节(用于非 UTF-8 目标, 优先于 link_target)',
              'link_target_rel_b64:str 规范化目标(相对 work_dir)的 base64, 供客户端无损校验; 输出时提供',
              'expect_md5:str 乐观锁: 期望远端当前 md5, 不符拒绝并回 1007(别名 base_md5)',
              'mode:str 文件权限; 省略/空串=保留目标原权限(新文件 0644)',
              'mtime:int 文件修改时间(Unix 秒), 落盘后 touch 恢复'],
        'ret': 'size, md5, mtime, backed_up, lint; 软链接额外: type=symlink,link_target,link_target_b64,link_target_rel,link_target_rel_b64,broken,resolvable,cycle',
        'eg': {'action': 'upload', 'path': 'src/a.txt', 'content_b64': 'aGVsbG8='},
        'err': ['1004 路径越界', '1006 超 MAX_SIZE(改用 chunked_upload)', '1007 expect_md5 冲突', '1011 bad_path 以/结尾; bad_dest_path 形如 dir/.; dest_is_dir 目标是目录'],
        'see': ['chunked_upload', 'upload_batch', 'download'],
    },
    'upload_batch': {
        'd': '批量写文件(部分成功也返回 200, 逐条结果看 results)',
        'r': ['items:array 每项 {path, content_b64, mode?, mtime?}'],
        'o': ['items[].mode:str 权限; 省略/空串=保留目标原权限(新文件 0644)', 'items[].mtime:int 修改时间(Unix 秒)', 'files:array (items 的历史别名, 与 items 等价)'],
        'ret': 'ok_idx[],fail_idx[],errors[] (与 items 等长, 成功项 null),results[],total,ok_count,failed_count,partial(部分成功=true)',
        'eg': {'action': 'upload_batch', 'items': [{'path': 'a.txt', 'content_b64': 'aGk='}]},
        'err': ['1004 路径越界', '1011 目标为特殊文件时该项失败(dest_is_special)', '1011 bad_path/bad_dest_path/dest_is_dir 逐项失败'],
        'see': ['upload', 'batch_deploy', 'chunked_upload'],
    },
    'chunked_upload': {
        'd': '大文件分片上传。op=init 建会话 → put 逐片 → assemble 合并(status 查进度/abort 放弃)',
        'r': ['op:str init|put|assemble|status|abort', 'path:str 目标路径(init 必填)'],
        'o': ['total_size:int 总字节数(init)', 'chunk_size:int 分片大小(init, 默认取 scope 的 chunk_size_default, 通常 4MB)', 'upload_id:str 会话 id(put/assemble/status/abort)', 'chunk_index:int 片序号(put, 从 0 起)', 'content_b64:str 该片内容(put)', 'md5:str 该片 md5(put, 可选校验)', 'final_md5:str 整文件 md5(assemble, 可选校验)', 'mode:str 权限(init 可选; 省略=保留目标原权限, 新文件 0644)', 'mtime:int 修改时间(Unix 秒, init 可选)', 'total:int (size 的别名)'],
        'ret': 'upload_id,chunk_size,total_chunks,chunk_count(init 阶段); progress(put 阶段); md5,mode,mtime(assemble 阶段),backed_up(assemble 阶段)',
        'eg': {'action': 'chunked_upload', 'op': 'init', 'path': 'big.bin', 'total_size': 10485760, 'chunk_size': 1048576},
        'err': ['1006 too_large 超 max_chunked_size', '1011 invalid_size total_size 必须>0', '1012 分片缺失', '1007 final_md5 不符', '1011 目标为目录/特殊文件', '1011 chunk_index 缺失/类型错误/越界'],
        'see': ['upload', 'download'],
    },
    'batch_deploy': {
        'd': '批量部署: 先全部暂存再切换, 可带 pre/post 检查, 失败自动回滚',
        'r': ['items:array 每项 {path, content_b64, mode?, mtime?}'],
        'o': ['items[].mode:str 权限; 省略/空串=保留目标原权限(新文件 0644)', 'items[].mtime:int 修改时间(Unix 秒)', 'pre_check:str 暂存后执行的命令, {file} 替换为路径', 'post_check:str 切换后执行的命令', 'post_check_expect:str post_check 输出需包含此串', 'rollback_on_fail:bool 默认 true', 'files:array (items 的历史别名, 与 items 等价)'],
        'ret': 'file_count,files[],sizes,backed_up[],pre_check,post_check,deploy_id,rolled_back',
        'eg': {'action': 'batch_deploy', 'items': [{'path': 'a.php', 'content_b64': 'PD9waHA='}]},
        'err': ['1004 路径越界', '1011 重复路径/目录目标/根目标/父子路径冲突', '1099 部署失败(已回滚)'],
        'see': ['upload_batch', 'batch_rollback'],
    },
    'mkdir': {
        'd': '创建目录(递归); 目录已存在时传 mode 会 chmod 修正权限',
        'r': ['path:str 目录路径'],
        'o': ['mode:str 目录权限, 默认 0755; 已存在时会 chmod (owner 不可读的 mode 回退 0755)'],
        'ret': 'path,mode,recursive,already_exists(目录已存在时为 true)',
        'eg': {'action': 'mkdir', 'path': 'src/new'},
        'err': ['1004 路径越界'],
        'see': ['list'],
    },
    'download': {
        'd': '读取文件内容(base64)。大于 stream_threshold 时走流式响应, Content-Length 精确',
        'r': ['path:str 文件路径'],
        'ret': 'content_b64, size, md5, mode, mtime; 特殊文件(FIFO/socket/设备)拒绝读取: ok=false,error=not_regular_file',
        'eg': {'action': 'download', 'path': 'a.txt'},
        'err': ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可读'],
        'see': ['read_range', 'stat', 'checksum'],
    },
    'stat': {
        'd': '单文件/目录元信息(不读内容)',
        'r': ['path:str 路径'],
        'ret': 'path,type,size,mtime,mode,uid,gid,nlink,ino,blocks,md5,sha1(文件时); ctime,atime,child_count(目录时); 特殊文件 type=special,special_type=fifo|socket|char_device|block_device,readable=false(不计算 hash); 链接: type=symlink,size=0,link_target,link_target_b64,link_target_rel,link_target_rel_b64,safe,broken,resolvable,cycle,md5(target_norm)',
        'eg': {'action': 'stat', 'path': 'a.txt'},
        'err': ['1004 路径越界', '1005 不存在'],
        'see': ['list', 'download'],
    },
    'list': {
        'd': '目录列举。非递归默认 200 条; 隐藏目录整体剪枝; 结果按名排序',
        'r': [],
        'o': ['path:str 目录, 默认 .', 'recursive:bool 递归', 'max_depth:int 递归深度, 默认 10; 0=不限', 'limit:int 默认 200, 上限 1000', 'offset:int 分页偏移', 'pattern:str 文件名通配, 如 *.php', 'include_hidden:bool 含点开头的项', 'since_mtime:int 只返回此时间后修改的'],
        'ret': 'items[{name,path,type,size,mtime,mode,child_count(目录项); 特殊文件 type=special,special_type,readable=false; 链接额外 link_target,link_target_b64,link_target_rel,link_target_rel_b64,safe,broken,resolvable,cycle; 非 UTF-8 名称额外 encoding=binary,path_b64,name_b64}],total,has_more,limit,offset,path,since_mtime(传了才有)',
        'eg': {'action': 'list', 'path': '.', 'recursive': True, 'pattern': '*.php', 'limit': 100},
        'err': ['1004 路径越界', '1005 不存在'],
        'see': ['stat', 'search', 'checksum'],
    },
    'read_range': {
        'd': '按行读文本(带行号). format=text 只回纯文本块, 省约 29% token(实测)',
        'r': ['path:str 文本文件路径'],
        'o': ['start_line:int 起始行, 默认 1', 'count:int 行数', 'end_line:int 结束行', 'max_line_len:int 单行截断, 默认 2000', 'format:str json(默认)|text'],
        'ret': 'path,start_line,end_line(本次最后一行),returned,total_lines,truncated(=has_more),truncated_line,has_more,content[{line,text}](format=json 时),text(format=text 时的整段文本)',
        'eg': {'action': 'read_range', 'path': 'a.php', 'start_line': 1, 'count': 80, 'format': 'text'},
        'err': ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可读', '1011 binary_file 二进制文件不能按行读取', '1011 bad_range end_line<start_line', '1011 bad_max_line_len 必须>=1', '1011 bad_format 只支持 json|text'],
        'see': ['download', 'search'],
    },
    'checksum': {
        'd': '文件/目录校验和(PHP 端为流式增量)',
        'r': ['path:str 文件或目录'],
        'o': ['algorithm:str md5|sha1|sha224|sha256|sha384|sha512|crc32b|adler32, 默认 md5', 'pattern:str 文件名过滤'],
        'ret': 'items[{path,size,mtime,mode,<algorithm>; 非 UTF-8 名称额外 encoding=binary,path_b64; 链接额外 type=symlink,link_target,link_target_b64,link_target_rel,link_target_rel_b64,safe,broken,resolvable,cycle,<algorithm>=hash(target_norm)}],total,algorithm,elapsed_ms,skipped_special,skipped[{path,special_type}]',
        'eg': {'action': 'checksum', 'path': '.', 'algorithm': 'md5', 'pattern': '*.php'},
        'err': ['1004 路径越界', '1011 顶层路径是特殊文件不可 hash'],
        'see': ['diff', 'sync_check'],
    },
    'diff': {
        'd': '客户端清单与服务端现状比对, 得出待推/待拉/冲突',
        'r': ['local_items:array 每项 {path, md5, size, mtime}', 'path:str 比对子目录, 必填, 全库用 "."'],
        'o': ['compute_hash:bool 对缺失 md5 的项现算', 'algorithm:str 仅 compute_hash=true 时生效, 白名单同 checksum'],
        'ret': 'only_local[](仅本地有=待推送),only_remote[](仅服务端有=待拉取),modified[{path,local,remote,reason}](双方都有但内容不同=冲突),summary{only_local,only_remote,modified,identical}',
        'eg': {'action': 'diff', 'path': '.', 'local_items': [{'path': 'a.txt', 'md5': '', 'size': 0, 'mtime': 0}]},
        'err': ['1004 路径越界'],
        'see': ['sync_check', 'checksum'],
    },
    'sync_check': {
        'd': '批量比对指定文件, 逐项给出 should_push / should_pull / conflict 与原因',
        'r': ['files:array 每项 {path, md5, size, mtime}'],
        'ret': 'results[{path,local_md5,local_size,remote_exists,remote_md5,remote_mtime(远端存在时),should_push,reason,conflict,hint(有处理建议时)}],total,should_push_count,conflict_count,summary',
        'eg': {'action': 'sync_check', 'files': [{'path': 'a.txt', 'md5': 'd41d8cd98f00b204e9800998ecf8427e', 'size': 0, 'mtime': 0}]},
        'err': ['1011 files 缺失'],
        'see': ['diff', 'stat'],
    },
    'delete': {
        'd': '删除文件或目录。目录需 recursive=true',
        'r': ['path:str 路径'],
        'o': ['recursive:bool 递归删除目录'],
        'ret': 'deleted,type,backed_up,recursive; 目标不存在时: already_missing=true(幂等, 非错误)',
        'eg': {'action': 'delete', 'path': 'tmp/x.txt'},
        'err': ['1004 路径越界', '1005 不存在', '1011 是目录但未传 recursive', '1011 root_delete_not_allowed 拒绝删除 work_dir 根'],
        'see': ['backup_ls'],
    },
    'rename': {
        'd': '重命名/移动(同 scope 内)',
        'r': ['from:str 源路径', 'to:str 目标路径'],
        'o': ['path:str (from 的别名)', 'new_path:str (to 的别名)'],
        'ret': 'from,to,type,backed_up',
        'eg': {'action': 'rename', 'from': 'a.txt', 'to': 'b.txt'},
        'err': ['1004 路径越界', '1005 源不存在', '1011 dst_exists 目标已存在', '1011 root_rename_not_allowed 拒绝移动 work_dir 根'],
        'see': ['copy', 'stat'],
    },
    'copy': {
        'd': '复制文件',
        'r': ['from:str 源路径', 'to:str 目标路径'],
        'o': ['overwrite:bool 覆盖已存在目标, 默认 false', 'path:str (from 的别名)', 'new_path:str (to 的别名)'],
        'ret': 'from,to,size,md5,backed_up',
        'eg': {'action': 'copy', 'from': 'a.txt', 'to': 'b.txt', 'overwrite': True},
        'err': ['1004 路径越界', '1005 源不存在', '1011 dst_exists 目标已存在(传 overwrite 或换名), 或源/目标为特殊文件(src_is_special/dst_is_special)'],
        'see': ['rename', 'backup_ls'],
    },
    'format': {
        'd': '格式化源码(php/js/css/json...)',
        'r': ['path:str 文件路径'],
        'o': ['type:str 指定格式类型', 'write:bool 写回, 默认 false 仅预览'],
        'ret': 'path,type,valid,changed,written,before_bytes,after_bytes,backed_up,content(格式化后的完整内容, write=false 预览时返回)',
        'eg': {'action': 'format', 'path': 'a.php', 'write': True},
        'err': ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可格式化'],
        'see': ['lint'],
    },
    'lint': {
        'd': '语法检查单个文件',
        'r': ['path:str 文件路径'],
        'ret': 'language,valid,syntax_ok,issues[{line,severity,rule,message}],stats{template_lines},summary{error,warning,info}; 语法错误时顶层 ok=false/code=1014',
        'eg': {'action': 'lint', 'path': 'a.php'},
        'err': ['1004 路径越界', '1005 不存在', '1011 是目录/特殊文件不可 lint'],
        'see': ['lint_batch', 'format'],
    },
    'lint_batch': {
        'd': '批量语法检查',
        'r': ['files:array 路径数组'],
        'ret': 'results[{path,ok,valid,syntax_ok,issue_count,language,first_error{line,message}}],total,failed(无法执行),syntax_failed(语法不通过),total_issues,all_ok; 有语法错误时顶层 ok=false/code=1014',
        'eg': {'action': 'lint_batch', 'files': ['a.php']},
        'err': ['1011 files 缺失'],
        'see': ['lint'],
    },
    'php_run': {
        'd': '用 php-cli 执行 work_dir 内的 PHP 脚本并捕获输出',
        'r': ['path:str 脚本路径'],
        'o': ['args:array 命令行参数', 'stdin_b64:str 标准输入 base64', 'timeout:int 秒, 默认 30'],
        'ret': 'exit_code,stdout,stderr,timed_out',
        'eg': {'action': 'php_run', 'path': 't.php'},
        'err': ['1004 路径越界', '1009 未启用', '1099 超时'],
        'see': ['exec', 'lint'],
    },
    'search': {
        'd': '全文搜索(带上下文行)',
        'r': ['pattern:str 关键词或正则'],
        'o': ['path:str 搜索根目录, 默认 .', 'regex:bool pattern 视为正则', 'case_insensitive:bool 忽略大小写', 'whole_word:bool 全词匹配', 'file_pattern:str 文件名过滤, 如 *.php', 'context_before:int 前置上下文行', 'context_after:int 后置上下文行', 'max_results:int 上限, 默认 200', 'context:int (context_before 的别名)'],
        'ret': 'hits[{path,line,match,context?}],total_matches,truncated,files_scanned,elapsed_ms,pattern,regex,case_insensitive,file_pattern,tip(扫描文件多且未指定 file_pattern 时才给)',
        'eg': {'action': 'search', 'pattern': 'TODO', 'path': '.', 'file_pattern': '*.php', 'max_results': 20},
        'err': ['1004 路径越界', '1011 pattern 缺失'],
        'see': ['todo', 'read_range', 'stats'],
    },
    'todo': {
        'd': '扫描 TODO/FIXME/XXX/HACK 等标记, 按 tag 聚合',
        'r': [],
        'o': ['path:str 根目录, 默认 .', 'tags:array 自定义标记, 默认 TODO/FIXME/XXX/HACK', 'file_pattern:str 文件名过滤', 'max_results:int 上限, 默认 200'],
        'ret': 'tags[],by_tag{},files_scanned,total,items[{path,line,tag,note,text}]',
        'eg': {'action': 'todo', 'path': '.', 'file_pattern': '*.php'},
        'err': ['1004 路径越界'],
        'see': ['search', 'stats'],
    },
    'stats': {
        'd': '代码统计: 文件数/行数/按扩展名或目录分组',
        'r': ['path:str 根目录'],
        'o': ['group_by:str ext|dir, 默认 ext', 'max_files:int 扫描上限', 'max_size:int 单文件大小上限', 'file_pattern:str 过滤'],
        'ret': 'total_files,total_lines,total_bytes,total_code,total_comment,total_blank,group_by,groups[],largest[]',
        'eg': {'action': 'stats', 'path': '.', 'group_by': 'ext'},
        'err': ['1004 路径越界'],
        'see': ['todo', 'checksum'],
    },
    'doc': {
        'd': '生成本 API 的文档(HTML/Markdown/JSON)',
        'r': [],
        'o': ['format:str html|md|json, 默认 md'],
        'ret': 'format,total,content(markdown 格式时)|actions(json 格式时)',
        'eg': {'action': 'doc', 'format': 'md'},
        'see': ['help'],
    },
    'snippet_set': {
        'd': '保存代码片段(同 name 覆盖)',
        'r': ['name:str 片段名', 'content:str 内容'],
        'o': ['description:str 说明', 'language:str 语言', 'tags:array 标签'],
        'ret': 'name,language,safe,tags',
        'eg': {'action': 'snippet_set', 'name': 'x', 'content': 'echo 1;'},
        'err': ['1011 name/content 缺失'],
        'see': ['snippet_get', 'snippet_list'],
    },
    'snippet_get': {
        'd': '按名取代码片段',
        'r': ['name:str 片段名'],
        'ret': 'name,content,language,description,tags,safe,created_at,updated_at',
        'eg': {'action': 'snippet_get', 'name': 'x'},
        'err': ['1005 不存在'],
        'see': ['snippet_list', 'snippet_delete'],
    },
    'snippet_list': {
        'd': '列代码片段(可按关键词/语言/标签过滤)',
        'r': [],
        'o': ['keyword:str 名称/说明关键词', 'language:str 语言', 'tag:str 标签'],
        'ret': 'snippets[{name,description,language,tags,size,updated_at}],total',
        'eg': {'action': 'snippet_list', 'language': 'php'},
        'see': ['snippet_get', 'snippet_set'],
    },
    'snippet_delete': {
        'd': '删除代码片段',
        'r': ['name:str 片段名'],
        'ret': 'deleted,name',
        'eg': {'action': 'snippet_delete', 'name': 'x'},
        'err': ['1005 不存在'],
        'see': ['snippet_list'],
    },
    'backup_ls': {
        'd': '列备份文件(默认最近 50 条)。不传 path 列全部',
        'r': [],
        'o': ['path:str 只看某文件的备份',
              'limit:int 返回条数, 默认 50 上限 500; 超过 500 条时用 path 收窄'],
        'ret': 'backups[{file,path,size,mtime}],total,returned,truncated,tip(截断时才有)',
        'eg': {'action': 'backup_ls', 'path': 'a.txt', 'limit': 20},
        'see': ['backup_rb', 'batch_rollback'],
    },
    'backup_rb': {
        'd': '用备份回滚单个文件',
        'r': ['path:str 目标文件', 'backup_file:str 备份名(来自 backup_ls)'],
        'ret': 'path,restored_from,size,md5',
        'eg': {'action': 'backup_rb', 'path': 'a.txt', 'backup_file': 'a.txt.bak.20260101_000000_ab12'},
        'err': ['1004 路径越界', '1010 备份不存在'],
        'see': ['backup_ls', 'batch_rollback'],
    },
    'batch_rollback': {
        'd': '按时间点批量回滚(回滚到 before_ts 之前的最近备份)',
        'r': ['before_ts:int unix 时间戳'],
        'o': ['x:int 同 before_ts(兼容旧参数)', 'paths:array 限定路径', 'dry_run:bool 仅预演'],
        'ret': 'results[],restored_count,failed_count,before_ts,dry_run,summary',
        'eg': {'action': 'batch_rollback', 'before_ts': 1760000000, 'dry_run': True},
        'err': ['1011 before_ts 缺失'],
        'see': ['backup_ls', 'backup_rb', 'recent_changes'],
    },
    'pipeline': {
        'd': '一次往返执行多个 action(省 RTT):AI 连续操作用它替代逐条请求, 10 步约快 4 倍',
        'r': ['steps:array 每项是一个完整 action 请求对象 {action, ...}'],
        'o': ['stop_on_error:bool 首错即停(默认 false, 继续执行后续步)'],
        'ret': 'results[](每项含 index/action + 该 action 的原始响应),total,succeeded,failed,stopped_at,stop_on_error,max_steps,mutating_actions_declared',
        'eg': {'action': 'pipeline', 'steps': [
            {'action': 'stat', 'path': 'a.php'},
            {'action': 'read_range', 'path': 'a.php'},
            {'action': 'checksum', 'path': 'a.php'}]},
        'err': ['1011 steps 缺失/非数组/为空/超上限', '1011 含黑名单 action(pipeline_blocked_action)',
                '1002 某个 step 越权(该 step 独立失败, 不影响其它步)'],
        'see': ['upload', 'batch_deploy', 'upload_batch', 'help'],
    },
    'audit_log': {
        'd': '读审计日志(最近 N 行; 每个请求至少一条记录)',
        'r': [],
        'o': ['lines:int 默认 100, 上限 2000'],
        'ret': 'lines[],count,file',
        'eg': {'action': 'audit_log', 'lines': 50},
        'see': ['recent_changes', 'oplog_list', 'metrics'],
    },
    'recent_changes': {
        'd': '最近写操作摘要(倒序)',
        'r': [],
        'o': ['lines:int 默认 50'],
        'ret': 'changes[{ts,action,path,code,ok,ip,size,md5,error_line}],total,success,failed,by_action,by_path,summary',
        'eg': {'action': 'recent_changes', 'lines': 20},
        'see': ['audit_log', 'batch_rollback'],
    },
    'error_log': {
        'd': '读服务器错误日志(可按关键词/正则/时间过滤)',
        'r': [],
        'o': ['name:str 日志源, 默认 php_error (见 error_log_list)', 'lines:int 行数, 默认 100', 'grep:str 包含关键词', 'regex:str 正则过滤', 'since_ts:int 起始时间', 'before_ts:int 截止时间', 'path:str 仅在 name=custom 时生效', 'relate_to:str 关联文件'],
        'ret': 'lines[],source,path,total',
        'eg': {'action': 'error_log', 'name': 'php_error', 'grep': 'Fatal', 'lines': 50},
        'err': ['1005 日志不存在'],
        'see': ['error_log_list'],
    },
    'error_log_list': {
        'd': '列出可读的日志源',
        'r': [],
        'ret': 'logs[{name,path,scope,format,readable,size,mtime}],total',
        'eg': {'action': 'error_log_list'},
        'see': ['error_log'],
    },
    'oplog_append': {
        'd': '追加一条操作日志(客户端自报, 服务端补 ip/ua/token_name/server_ts)',
        'r': ['entry:object ts/ts_epoch/op_id/command/args/summary/rc/duration_ms/version/host/user/cwd'],
        'ret': 'bytes,client_id,log_file,duplicate?',
        'eg': {'action': 'oplog_append', 'entry': {'command': 'deploy', 'args': {'files': ['a.php']},
                                                   'summary': '部署: a.php', 'rc': 0}},
        'err': ['1011 entry 缺失'],
        'see': ['oplog_list', 'oplog_view'],
    },
    'oplog_list': {
        'd': '列出操作日志的客户端/会话(含成功/失败计数)',
        'r': [],
        'ret': 'clients[{client_id,scope,log_file,size_bytes,size_human,mtime,mtime_str,line_count,ok_count,error_count,first_ts,last_ts,last_epoch,last_command,last_summary,last_ip,last_rc,commands}],scopes,total',
        'eg': {'action': 'oplog_list'},
        'see': ['oplog_view', 'oplog_append'],
    },
    'oplog_view': {
        'd': '按客户端 id 读操作日志(筛选/分页/统计)',
        'r': ['client_id:str 客户端 id(见 oplog_list)'],
        'o': ['limit:int 默认 100 最大 1000', 'offset:int 从最新往前跳过 N 条(加载更早)',
              'grep:str 关键词过滤', 'command:str 命令名(逗号分隔多个)', 'result:str ok|error',
              'since/until:epoch 或日期时间'],
        'ret': 'entries[],total,matched,returned,limit,offset,has_more,filters,stats{ok,error,avg_ms,max_ms,by_command,first_ts,last_ts}',
        'eg': {'action': 'oplog_view', 'client_id': 'cli-abc', 'command': 'deploy,exec',
               'result': 'error', 'limit': 50},
        'err': ['1011 client_id 缺失'],
        'see': ['oplog_list'],
    },
    'admin_stats': {
        'd': '管理员总览: 各 scope 用量/请求量/错误率',
        'r': [],
        'ret': 'mode,work_dir(单目录模式),file_count(单目录模式),size_mb(单目录模式),scopes[](多目录模式),cached_at(多目录模式),totals(多目录模式)',
        'eg': {'action': 'admin_stats'},
        'err': ['1002 需 admin_token'],
        'see': ['health', 'metrics'],
    },
    'config_get': {
        'd': '读当前生效配置(敏感字段打码)',
        'r': [],
        'o': ['section:str 只取某个配置节'],
        'ret': 'mode,version,global,scopes,tokens,admin_token_present',
        'eg': {'action': 'config_get'},
        'see': ['config_reload', 'scopes_list'],
    },
    'config_reload': {
        'd': '重新加载 sync_config.json',
        'r': [],
        'ret': 'reloaded,version,tokens,scopes,note(本次请求仍用旧配置, 下次请求才生效)',
        'eg': {'action': 'config_reload'},
        'err': ['1002 需 admin_token', '1099 配置非法'],
        'see': ['config_get'],
    },
    'scopes_list': {
        'd': '列出全部 scope 及其 work_dir',
        'r': [],
        'ret': 'scopes[{name,work_dir,writable,file_count,size_mb,backup_count,backup_keep,max_size_mb,max_files_per_deploy,exec_enabled}],mode',
        'eg': {'action': 'scopes_list'},
        'see': ['scope_add', 'scope_delete'],
    },
    'scope_add': {
        'd': '新增/更新 scope',
        'r': ['name:str scope 名'],
        'o': ['work_dir:str 工作目录', 'backup_dir:str', 'probe_dir:str', 'chunk_dir:str', 'max_size:int', 'max_chunked_size:int', 'chunk_size_default:int', 'chunk_ttl_days:int', 'max_files_per_deploy:int 单次 batch_deploy 文件数上限, 默认 50', 'exec_enabled:bool', 'exec_timeout_max:int', 'exec_allow_ips:array', 'audit_log:bool', 'metrics_file:str', 'webhook_dir:str', 'backup_keep:int'],
        'ret': 'name,created,config',
        'eg': {'action': 'scope_add', 'name': 'proj2', 'work_dir': '/srv/proj2'},
        'err': ['1002 需 admin_token', '1011 name 缺失'],
        'see': ['scopes_list', 'scope_delete'],
    },
    'scope_delete': {
        'd': '删除 scope',
        'r': ['name:str scope 名'],
        'ret': 'deleted,name',
        'eg': {'action': 'scope_delete', 'name': 'proj2'},
        'err': ['1002 需 admin_token', '1005 不存在'],
        'see': ['scopes_list'],
    },
    'tokens_list': {
        'd': '列出 token(仅显示前缀与权限)',
        'r': [],
        'ret': 'tokens[{name,token(admin 可见明文; 非 admin 为掩码),scopes,permissions}],admin_token_present,mode',
        'eg': {'action': 'tokens_list'},
        'err': ['1002 需 admin_token'],
        'see': ['token_add', 'token_delete'],
    },
    'token_add': {
        'd': '新增 token',
        'r': ['name:str token 名'],
        'o': ['new_token:str 指定 token 值(否则自动生成)', 'scopes:array 授权 scope', 'permissions:array 权限位'],
        'ret': 'added,generated,name,token(自动生成时返回明文; 手动指定时为掩码),scopes,permissions',
        'eg': {'action': 'token_add', 'name': 'ci', 'scopes': ['xbxbb']},
        'err': ['1002 需 admin_token'],
        'see': ['tokens_list'],
    },
    'token_delete': {
        'd': '删除 token',
        'r': ['target_token:str 要删的 token 名或值前缀'],
        'ret': 'deleted,target',
        'eg': {'action': 'token_delete', 'target_token': 'ci'},
        'err': ['1002 需 admin_token', '1005 不存在'],
        'see': ['tokens_list'],
    },
    'webhook_set': {
        'd': '注册/更新 webhook(事件回调)',
        'r': ['url:str 回调地址'],
        'o': ['events:array 事件名, 默认 [upload,delete]', 'filter:str 路径过滤', 'secret:str HMAC 签名密钥'],
        'ret': 'id,url,events',
        'eg': {'action': 'webhook_set', 'url': 'https://x/hook', 'events': ['upload', 'delete']},
        'err': ['1011 bad_url', '1011 内网地址被拒(需 WEBHOOK_ALLOW_INTERNAL)'],
        'see': ['webhook_list', 'webhook_test'],
    },
    'webhook_list': {
        'd': '列 webhook 配置',
        'r': [],
        'ret': 'hooks[{id,url,events,filter,fail_count,paused,created_at,last_fired_at,last_http_code,last_error}],total',
        'eg': {'action': 'webhook_list'},
        'see': ['webhook_set', 'webhook_delete'],
    },
    'webhook_delete': {
        'd': '删除 webhook',
        'r': ['id:str webhook id'],
        'ret': 'deleted,id',
        'eg': {'action': 'webhook_delete', 'id': 'wh_x'},
        'err': ['1005 不存在'],
        'see': ['webhook_list'],
    },
    'webhook_test': {
        'd': '发送测试请求验证 webhook 可达',
        'r': [],
        'o': ['url:str 临时地址', 'path:str 模拟路径', 'secret:str 签名密钥'],
        'ret': 'passed,http_code,total_time,url,curl_error,tip(失败时才给)',
        'eg': {'action': 'webhook_test', 'url': 'https://x/hook'},
        'err': ['1011 bad_url', '1099 不可达'],
        'see': ['webhook_set'],
    },
    'package_list': {
        'd': '列出已生成的客户端安装包',
        'r': [],
        'ret': 'packages[{file,size,mtime,url}],total',
        'eg': {'action': 'package_list'},
        'see': ['client_package', 'package_delete'],
    },
    'package_delete': {
        'd': '删除客户端安装包',
        'r': ['file:str 包文件名'],
        'ret': 'deleted,file',
        'eg': {'action': 'package_delete', 'file': 'ai-sync-client-x.zip'},
        'err': ['1005 不存在'],
        'see': ['package_list'],
    },
    'client_package': {
        'd': '生成定制客户端安装包(zip, 内含预置配置)',
        'r': [],
        'o': ['target_token:str 打包进配置的 token', 'api_url:str 服务端地址', 'doc_mode:bool 附带文档', 'local_dir:str 本地目录', 'remote_subdir:str 远端子目录'],
        'ret': 'filename,download_url,sha256,size,scope,api_url,permissions,entries,package_file,watch_dir,token_name,doc,zip_b64(包的 base64, 可直接落盘解包)',
        'eg': {'action': 'client_package', 'target_token': 'xbxbb123'},
        'err': ['1002 需 admin_token'],
        'see': ['package_list'],
    },
    'exec': {
        'd': '在服务器执行 shell 命令(受 exec_enabled 与 IP 白名单约束)',
        'r': ['cmd:str 命令'],
        'o': ['timeout:int 秒, 默认 30', 'cwd:str 工作目录'],
        'ret': 'return_code,stdout,stderr,cwd,stdout_len,stderr_len,elapsed_ms,timed_out,output_truncated,timeout,warning,tip(安全提示)',
        'eg': {'action': 'exec', 'cmd': 'ls -la'},
        'err': ['1009 未启用', '1002 IP 不在白名单', '1011 cmd/超时参数非法',
                '超时不是错误: ok=true, return_code=124, timed_out=true'],
        'see': ['php_run', 'exec'],
    },
    # ---- 持久 shell (对齐原生 ssh): 会话内 cwd/export 跨调用保持 ----
    'shell_open': {
        'd': '打开持久 shell 会话(对齐原生 ssh): 会话内 cd / export 跨调用保持, 不必每条命令都重复绝对路径',
        'r': [],
        'o': ['session_id:str 自定义会话 id(省略则自动生成, 客户端会记下作后续默认)', 'cwd:str 起始工作目录',
              'env:obj 初始环境变量', 'name:str 备注名'],
        'ret': 'session_id,cwd,name,reused(复用已有会话时 true),tip(新会话),note(复用且传了 cwd/env 时说明未生效)',
        'eg': {'action': 'shell_open', 'cwd': '/var/www'},
        'err': ['1009 exec 未启用', '1002 IP 不在白名单', '1011 cwd_not_found cwd 目录不存在',
                '1011 invalid_env_key env 键名非法'],
        'see': ['shell_run', 'shell_list', 'shell_close', 'exec'],
    },
    'shell_run': {
        # 与 PHP 端逐字一致(compare 有 help 对齐用例)
        'd': '在持久会话内执行命令: cd / export 会持续生效到后续调用(对齐原生 ssh), 不用每条命令都 cd 一遍',
        'r': ['session_id:str 会话 id', 'cmd:str 要执行的命令(支持多行/管道/&&/exit)'],
        'o': ['cwd:str 本次覆盖工作目录(相对会话 cwd, 绝对路径直接用)', 'env:obj 本次追加环境变量',
              'timeout:int 秒, 默认 30', 'background:bool true 时后台执行并立即返回 job_id'],
        'ret': '前台: session_id,return_code,stdout,stderr,cwd(执行后真实 cwd),timed_out,output_truncated,stdout_len,stderr_len,elapsed_ms,tip; 后台: job_id,background,session_id,cwd,timeout,tip; 破坏性命令额外返回 warnings[]',
        'eg': {'action': 'shell_run', 'session_id': 'sh_xxxxxxxxxxxx', 'cmd': 'cd /tmp && ls'},
        'note': '防呆(只防误操作, 不拦定向破坏): rm -rf/rm -fr 这类破坏性命令照常执行, 响应里带 warnings[] 提醒(如目标含 shell 变量, 为空时会退化成删根目录); rm -rf /、mkfs、dd 写磁盘、fork 炸弹、reboot 等毁灭性命令会被拒绝并返回 alternatives 替代方案',
        'err': ['1011 unknown_session 会话不存在(先 shell_open)', '1011 empty_cmd 缺 cmd',
                '1011 cmd_must_be_string cmd 必须是字符串', '1011 cwd_not_found cwd 目录不存在',
                '1011 invalid_timeout timeout 非法', '1011 invalid_env_key env 键名非法',
                '1002 destructive_blocked 毁灭性命令被拒(响应带 alternatives)',
                '1099 超时(timed_out=true, return_code=124)'],
        'see': ['shell_open', 'shell_job', 'shell_kill', 'shell_list', 'exec'],
    },
    'shell_job': {
        'd': '查询后台任务: 轮询输出与退出码(对齐 nohup/& 后回来看结果)',
        'r': ['job_id:str shell_run background=true 返回的 job_id'],
        'o': ['lines:int 只返回末尾 N 行'],
        'ret': 'job_id,status(running|done),return_code,timed_out,killed,output_truncated,output,stdout,stderr,pid,cwd,timeout,elapsed_ms',
        'eg': {'action': 'shell_job', 'job_id': 'jb_xxxxxxxxxxxx'},
        'err': ['1011 unknown_job job_id 不存在', '1009 exec 未启用'],
        'see': ['shell_run', 'shell_kill', 'shell_list'],
    },
    'shell_kill': {
        'd': '终止后台任务(先 TERM 后 KILL, 杀整棵进程树)',
        'r': ['job_id:str'],
        'o': [],
        'ret': 'job_id,killed,pid,return_code,already_finished,note',
        'eg': {'action': 'shell_kill', 'job_id': 'jb_xxxxxxxxxxxx'},
        'err': ['1011 unknown_job job_id 不存在', '1009 exec 未启用'],
        'see': ['shell_run', 'shell_job'],
    },
    'shell_close': {
        'd': '关闭持久会话并清除其 cwd/env 状态(该会话的后台任务仍可 shell_job 查询); 幂等: 关一个本就不存在的会话也返回成功',
        'r': ['session_id:str'],
        'o': [],
        'ret': 'session_id,closed,already_closed(会话本就不存在时 true),note',
        'eg': {'action': 'shell_close', 'session_id': 'sh_xxxxxxxxxxxx'},
        'err': ['1011 missing_session_id 未带 session_id', '1009 exec 未启用'],
        'see': ['shell_open', 'shell_list'],
    },
    'shell_list': {
        'd': '列举当前所有持久会话与后台任务(排查"我开过哪些会话/还有哪些任务在跑")',
        'r': [],
        'o': [],
        'ret': 'sessions[session_id,cwd,name,idle_s],jobs[job_id,status,session_id,cmd_preview,pid],session_count,job_count',
        'eg': {'action': 'shell_list'},
        'err': ['1009 exec 未启用', '1002 IP 不在白名单'],
        'see': ['shell_open', 'shell_run', 'shell_close'],
    },
}


# ==================== 响应信封 ====================
def fail(code, error, message='', extra=None):
    base = {
        'ok': False,
        'code': code,
        'error': error,
        'message': message,
        'fix_hint': ERROR_HINTS.get(code, ''),
    }
    if extra:
        base.update(extra)
    # fix_hint 永远给"最具体"的那条: 调用点带了 hint 就用它覆盖错误码通用文案。
    # 调用方往往只读 fix_hint 一个字段就决定下一步, 通用文案("参数非法")会导致盲目重试。
    if base.get('hint'):
        base['fix_hint'] = base['hint']
    return base


def ok(data=None):
    data = dict(data) if isinstance(data, dict) else {}
    data['ok'] = True
    if 'code' not in data:
        data['code'] = 0
    return data


def mask_token(tk):
    tk = str(tk)
    return '***' if len(tk) <= 6 else tk[:6] + '***'


# ==================== 配置加载 ====================
def load_sync_config(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_sync_config(path, data):
    # 带锁的原子写 (锁在调用方持有)
    tmp = path + '.tmp'
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(payload)
    os.replace(tmp, path)
    return True


def default_scope_config():
    return {
        'work_dir': '',
        'backup_keep': 5,
        'max_size': 64 * 1024 * 1024,
        'max_chunked_size': 1024 * 1024 * 1024,
        'chunk_ttl_days': 7,
        'chunk_size_default': 4 * 1024 * 1024,
        'max_files_per_deploy': 50,
        'exec_enabled': False,
        'exec_allow_ips': [],
        'exec_timeout_max': 30,
        'backup_dir': 'ai-sync/backups/',
        'probe_dir': 'ai-sync/probes/',
        'chunk_dir': 'ai-sync/chunks/',
        'webhook_dir': 'ai-sync/webhooks/',
        'audit_log': 'ai-sync/audit.log',
        'metrics_file': 'ai-sync/metrics.json',
    }


def build_boot_config(raw):
    g = raw.get('global', {})
    cfg = {
        'TOKEN': '',
        'WORK_DIR': '',
        'BACKUP_DIR': 'ai-sync/backups/',
        'PROBE_DIR': 'ai-sync/probes/',
        'CHUNK_DIR': 'ai-sync/chunks/',
        'WEBHOOK_DIR': 'ai-sync/webhooks/',
        'AUDIT_LOG': 'ai-sync/audit.log',
        'METRICS_FILE': 'ai-sync/metrics.json',
        'BACKUP_KEEP': 5,
        'MAX_SIZE': 64 * 1024 * 1024,
        'MAX_CHUNKED_SIZE': 1024 * 1024 * 1024,
        'CHUNK_TTL_DAYS': 7,
        'CHUNK_SIZE_DEFAULT': 4 * 1024 * 1024,
        'MAX_FILES_PER_DEPLOY': 50,
        'EXEC_ENABLED': False,
        'EXEC_ALLOW_IPS': [],
        'EXEC_TIMEOUT_MAX': 120,
        'ALLOW_IPS': g.get('allow_ips', []),
        'TRUSTED_PROXY': g.get('trusted_proxy', False),
        'TRUSTED_PROXY_IPS': g.get('trusted_proxy_ips', []),
        'RATE_LIMIT': g.get('rate_limit', {'enabled': True, 'per_token_per_minute': 60, 'per_ip_per_second': 10, 'upload_per_minute': 30}),
        'GZIP_THRESHOLD_KB': g.get('gzip_threshold_kb', 16),
        'GZIP_FORCE': g.get('gzip_force', False),
        'STREAM_THRESHOLD_KB': g.get('stream_threshold_kb', 2048),
        'PACKAGE_DIR': g.get('package_dir') or os.path.join(tempfile.gettempdir(), 'ai-sync-packages'),
        'PACKAGE_TTL': int(g.get('package_ttl', 86400) or 86400),
        'AUDIT_ENABLED': g.get('audit_enabled', True),
        'DEBUG': g.get('debug', False),
        'API_VERSION': g.get('api_version', raw.get('version', '4.3')),
    }
    scopes = raw.get('scopes', {})
    if scopes:
        first = next(iter(scopes.values()))
        cfg['WORK_DIR'] = first.get('work_dir', '')
        cfg['AUDIT_LOG'] = first.get('audit_log', cfg['AUDIT_LOG'])
    return cfg


def merge_scope_into_cfg(cfg, scope_cfg, scope_name, token_info, is_admin, token):
    cfg['TOKEN'] = token
    cfg['WORK_DIR'] = scope_cfg.get('work_dir', cfg['WORK_DIR'])
    cfg['BACKUP_DIR'] = scope_cfg.get('backup_dir', cfg['BACKUP_DIR'])
    cfg['PROBE_DIR'] = scope_cfg.get('probe_dir', cfg['PROBE_DIR'])
    cfg['CHUNK_DIR'] = scope_cfg.get('chunk_dir', cfg['CHUNK_DIR'])
    cfg['WEBHOOK_DIR'] = scope_cfg.get('webhook_dir', cfg['WEBHOOK_DIR'])
    cfg['AUDIT_LOG'] = scope_cfg.get('audit_log', cfg['AUDIT_LOG'])
    cfg['AUDIT_MAX_SIZE'] = scope_cfg.get('audit_max_size', cfg.get('AUDIT_MAX_SIZE', 10 * 1024 * 1024))
    cfg['AUDIT_MAX_KEEP'] = scope_cfg.get('audit_max_keep', cfg.get('AUDIT_MAX_KEEP', 5))
    cfg['METRICS_FILE'] = scope_cfg.get('metrics_file', cfg['METRICS_FILE'])
    cfg['BACKUP_KEEP'] = scope_cfg.get('backup_keep', cfg['BACKUP_KEEP'])
    cfg['MAX_SIZE'] = scope_cfg.get('max_size', cfg['MAX_SIZE'])
    cfg['MAX_CHUNKED_SIZE'] = scope_cfg.get('max_chunked_size', cfg['MAX_CHUNKED_SIZE'])
    cfg['CHUNK_TTL_DAYS'] = scope_cfg.get('chunk_ttl_days', cfg['CHUNK_TTL_DAYS'])
    cfg['CHUNK_SIZE_DEFAULT'] = scope_cfg.get('chunk_size_default', cfg['CHUNK_SIZE_DEFAULT'])
    cfg['MAX_FILES_PER_DEPLOY'] = max(1, _php_intval(scope_cfg.get('max_files_per_deploy'))) \
        if scope_cfg.get('max_files_per_deploy') is not None else cfg.get('MAX_FILES_PER_DEPLOY', 50)
    cfg['EXEC_ENABLED'] = scope_cfg.get('exec_enabled', cfg['EXEC_ENABLED'])
    cfg['EXEC_ALLOW_IPS'] = scope_cfg.get('exec_allow_ips', cfg['EXEC_ALLOW_IPS'])
    cfg['EXEC_TIMEOUT_MAX'] = scope_cfg.get('exec_timeout_max', cfg['EXEC_TIMEOUT_MAX'])
    cfg['STREAM_THRESHOLD_KB'] = scope_cfg.get('stream_threshold_kb', cfg.get('STREAM_THRESHOLD_KB', 2048))
    cfg['PACKAGE_DIR'] = cfg.get('PACKAGE_DIR') or os.path.join(tempfile.gettempdir(), 'ai-sync-packages')
    cfg['PACKAGE_TTL'] = int(cfg.get('PACKAGE_TTL', 86400) or 86400)
    cfg['SCOPE'] = scope_name
    cfg['TOKEN_NAME'] = token_info.get('name', '')
    cfg['PERMISSIONS'] = token_info.get('permissions', [])
    cfg['IS_ADMIN'] = is_admin
    cfg['AVAILABLE_SCOPES'] = token_info.get('scopes', [])
    return cfg


def update_sync_config_atomic(mutator):
    """原子 read-modify-write sync_config.json, 返回 [ok, data_or_errcode, extra]"""
    path = STATE['config_path']
    with STATE['lock']:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return [False, 'read_fail', None]
        if not isinstance(data, dict):
            return [False, 'read_fail', None]
        r = mutator(data)
        if not (isinstance(r, (list, tuple)) and r and r[0]):
            return [False, r[1] if len(r) > 1 else 'rejected', r[2] if len(r) > 2 else None]
        try:
            save_sync_config(path, r[1])
        except Exception as e:
            return [False, 'write_fail', str(e)]
        # FIX-R12: 同步刷新内存缓存。
        #   旧版只写文件、不更新 STATE['RAW_CONFIG'], 于是 token_add/token_delete、
        #   scope_add/scope_delete 之后 tokens_list / config_get / scopes_list 仍然
        #   返回**旧数据**, 必须重启进程或调 config_reload 才可见 —— 而 PHP 端每次
        #   请求重新读文件, 删除后立刻生效。双端行为不一致, 且调用方拿到的是过期
        #   凭据清单(刚加的 token 查不到、刚删的 token 还能用)。
        #   注意: 只刷新数据面 RAW_CONFIG; 控制面 STATE['CONFIG'] 仍由 config_reload
        #   负责(工作目录等启动期资源变更需要显式重载, 不该被单次写操作顺带改动)。
        try:
            STATE['RAW_CONFIG'] = r[1]
        except Exception:
            pass
        return [True, r[1], r[2] if len(r) > 2 else None]


# ==================== IP / CIDR ====================
def ip_in_cidr(ip, cidr):
    import ipaddress
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return ipaddress.ip_address(ip) in net
    except Exception:
        return False


def client_ip(remote_addr, headers):
    cfg = _ctx_cfg()
    trusted_ips = cfg.get('TRUSTED_PROXY_IPS', [])
    trusted_proxy = cfg.get('TRUSTED_PROXY', False)
    if not trusted_proxy and not trusted_ips:
        return remote_addr
    xff = headers.get('X-Forwarded-For', '')
    if not xff:
        return remote_addr
    parts = [p.strip() for p in xff.split(',') if p.strip()]
    parts = [p for p in parts if _is_valid_ip(p)]
    if not parts:
        return remote_addr
    if trusted_ips:
        remote_is_proxy = any(remote_addr == tip or (('/' in tip) and ip_in_cidr(remote_addr, tip)) for tip in trusted_ips)
        if not remote_is_proxy:
            return remote_addr
        for cand in reversed(parts):
            if not any(cand == tip or ('/' in tip and ip_in_cidr(cand, tip)) for tip in trusted_ips):
                return cand
        return parts[0]
    return parts[0]


def _is_valid_ip(s):
    import ipaddress
    try:
        ipaddress.ip_address(s)
        return True
    except Exception:
        return False


# ==================== 路径安全 ====================
def safe_path_nofollow(rel, base):
    """与 safe_path 相同的词法校验, 但不 realpath 最后一级.

    safe_path 会把 .../link 解析成 link 指向的目标, 导致 upload/delete 操作到目标而非链接本身.
    本函数只解析父目录, 返回 "已校验父目录 + 原始文件名", 专供软链接操作使用.
    """
    if not isinstance(rel, str):
        return None
    if rel == '':
        return None
    if '\0' in rel:
        return None
    if rel in ('.', './'):
        rb = os.path.realpath(base.rstrip('/\\'))
        return rb if rb else None
    norm = rel.replace('\\', '/')
    norm = re.sub(r'/+', '/', norm)
    trimmed = norm.strip('/')
    if trimmed != '':
        for seg in trimmed.split('/'):
            if seg in ('.', ''):
                continue
            if seg == '..':
                return None
    final_rel = rel.lstrip('/')
    while final_rel.startswith('./'):
        final_rel = final_rel[2:].lstrip('/')
    final_rel = final_rel.rstrip('/\\')
    if final_rel == '':
        rb = os.path.realpath(base.rstrip('/\\'))
        return rb if rb else None
    real_base = os.path.realpath(base.rstrip('/\\'))
    if not real_base:
        return None
    # 只解析父目录, 最后一级 (basename) 保持原样, 否则会把软链接解析成目标.
    final_posix = final_rel.replace('\\', '/')
    dir_rel, basename = os.path.split(final_posix)
    cur = os.path.join(real_base, dir_rel) if dir_rel else real_base
    missing = []
    while True:
        if os.path.exists(cur):
            cur_real = os.path.realpath(cur)
            break
        new_parent, name = os.path.split(cur)
        if new_parent == cur:
            return None
        missing.append(name)
        cur = new_parent
    if not _path_in_base(cur_real, real_base):
        return None
    if not basename:
        return None
    return os.path.join(cur_real, *reversed(missing), basename)


def _lexical_norm_abs(abs_path):
    """纯词法归一化绝对路径 (不访问文件系统), 越界返回 None."""
    p = str(abs_path).replace('\\', '/')
    if not p.startswith('/'):
        return None
    parts = []
    for seg in p.split('/'):
        if seg in ('', '.'):
            continue
        if seg == '..':
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(seg)
    return '/' + '/'.join(parts)


def _path_in_base(abs_path, base_real):
    a = str(abs_path).replace('\\', '/').rstrip('/')
    b = str(base_real).replace('\\', '/').rstrip('/')
    if a == '':
        a = '/'
    if b == '':
        b = '/'
    return a == b or a.startswith(b + '/')


def _norm_rel_target(raw):
    """归一化"相对链接目标"(相对链接所在目录), 用于跨根一致的指纹.

    例: ./a -> a; sub/../a -> a; ../a -> ../a; . -> .
    与 work_dir 无关, 因此客户端/服务端、不同 remote_base 下可得到相同 hash.
    """
    out = []
    for seg in str(raw).replace('\\', '/').split('/'):
        if seg == '' or seg == '.':
            continue
        if seg == '..':
            if out and out[-1] != '..':
                out.pop()
            else:
                out.append('..')
            continue
        out.append(seg)
    return '/'.join(out) if out else '.'


def _link_resolve_chain(start, base_real):
    """解析链接链, 返回 status = ok|broken|cycle|outside.

    与 os.path.realpath 的区别: 环/悬空链/中间层逃逸都能区分, 且不读取目标内容.
    """
    cur = start
    seen = set()
    for _ in range(40):
        cn = _lexical_norm_abs(cur)
        if cn is None:
            return 'broken', None
        if cn in seen:
            return 'cycle', None
        seen.add(cn)
        if not os.path.islink(cur):
            return ('ok' if os.path.exists(cur) else 'broken'), cur
        try:
            raw = os.readlink(cur)
        except OSError:
            return 'broken', None
        rn = raw.replace('\\', '/')
        if rn.startswith('/') or re.match(r'^[A-Za-z]:/', rn):
            nxt = _lexical_norm_abs(rn)
        else:
            nxt = _lexical_norm_abs(os.path.join(os.path.dirname(cur), rn))
        if nxt is None or not _path_in_base(nxt, base_real):
            return 'outside', None
        cur = nxt
    return 'cycle', None


def _link_meta_from_target(raw, link_full, base):
    """计算软链接元数据.

    安全规则:
      - 目标必须是相对路径 (绝对路径不可移植, 也容易指向 work_dir 外)
      - 词法归一化后必须仍在 work_dir 内
      - 目标已存在时 realpath 也必须仍在 work_dir 内 (防止中间链接逃逸)
    """
    unsafe = lambda reason: {'safe': False, 'target': None, 'target_rel': None,
                             'target_norm': None, 'broken': False, 'cycle': False,
                             'resolvable': False, 'reason': reason}
    if not isinstance(raw, str) or raw == '':
        return unsafe('empty_target')
    if '\0' in raw:
        return unsafe('nul_byte')
    norm = raw.replace('\\', '/')
    if norm.startswith('/') or re.match(r'^[A-Za-z]:/', norm):
        return unsafe('absolute_target')
    real_base = os.path.realpath(base.rstrip('/\\'))
    if not real_base:
        return unsafe('base_unresolvable')
    parent_real = os.path.realpath(os.path.dirname(link_full))
    if not parent_real:
        return unsafe('parent_unresolvable')
    if not _path_in_base(parent_real, real_base):
        return unsafe('parent_outside')
    lex = _lexical_norm_abs(parent_real + '/' + norm)
    if lex is None or not _path_in_base(lex, real_base):
        return unsafe('outside_workdir')
    rel = lex[len(real_base.rstrip('/')):].lstrip('/')
    if rel == '':
        rel = '.'
    # FIX-link-mid-escape: 目标路径中间经过的链接目录 (outdir -> /etc, 目标 outdir/hostname)
    #   也必须校验. realpath 非严格模式会解析已存在前缀, 不存在部分原样保留,
    #   因此对「最终不存在但中间链接已逃逸」同样有效.
    try:
        real_target = os.path.realpath(lex)
    except OSError:
        real_target = None
    if real_target is not None and not _path_in_base(real_target, real_base):
        return unsafe('target_escapes_workdir')
    status, _resolved = _link_resolve_chain(lex, real_base)
    if status == 'outside':
        return unsafe('target_escapes_workdir')
    return {'safe': True, 'target': raw, 'target_rel': rel,
            'target_norm': _norm_rel_target(raw),
            'broken': (status != 'ok'), 'cycle': (status == 'cycle'),
            'resolvable': (status == 'ok'), 'reason': None}


def _link_meta(link_full, base):
    try:
        raw = os.readlink(link_full)
    except OSError:
        return {'safe': False, 'target': None, 'target_rel': None,
                'target_norm': None, 'broken': False, 'cycle': False,
                'resolvable': False, 'reason': 'readlink_fail'}
    return _link_meta_from_target(raw, link_full, base)


def _node_md5(dest, base):
    """节点当前内容指纹: 普通文件=md5(内容), 软链接=md5(归一化相对目标), 其他=''."""
    if os.path.islink(dest):
        lm = _link_meta(dest, base)
        return hashlib.md5(lm['target_norm'].encode('utf-8', 'surrogateescape')).hexdigest() \
            if lm['safe'] and lm.get('target_norm') is not None else ''
    if os.path.isfile(dest):
        try:
            with open(dest, 'rb') as fh:
                return hashlib.md5(fh.read()).hexdigest()
        except OSError:
            return ''
    return ''


def safe_path(rel, base):
    if not isinstance(rel, str):
        return None
    if rel == '':
        return None
    # NUL 字节: os.lstat/realpath 会抛 ValueError("embedded null character"),
    # 旧实现被入口兜成 1011 bad_param 并回显 Python 内部异常文本. 词法层直接拒绝 → 1004.
    if '\0' in rel:
        return None
    if rel in ('.', './'):
        rb = os.path.realpath(base.rstrip('/\\'))
        return rb if rb else None
    # 词法层: 仅拦整段 '..'
    norm = rel.replace('\\', '/')
    norm = re.sub(r'/+', '/', norm)
    trimmed = norm.strip('/')
    if trimmed != '':
        for seg in trimmed.split('/'):
            if seg in ('.', ''):
                continue
            if seg == '..':
                return None
    final_rel = rel.lstrip('/')
    while final_rel.startswith('./'):
        final_rel = final_rel[2:].lstrip('/')
    base_n = base.rstrip('/\\') + '/'
    full = base_n + final_rel
    real_base = os.path.realpath(base_n)
    if not real_base:
        return None
    real_base_norm = real_base.rstrip(os.sep) + os.sep
    real_full = os.path.realpath(full)
    if real_full:
        if (real_full + os.sep).startswith(real_base_norm):
            return real_full
    # 新文件: 向上递归找已存在父目录
    parent = full
    basename_parts = []
    while True:
        if len(parent) < len(base.rstrip('/')):
            return None
        parent_real = os.path.realpath(parent)
        if parent_real:
            break
        new_parent, name = os.path.split(parent)
        if new_parent == parent:
            return None
        basename_parts.append(name)
        parent = new_parent
    if not (parent_real + os.sep).startswith(real_base_norm):
        return None
    return os.path.join(parent_real, *reversed(basename_parts))


def _special_kind(path):
    """分类特殊文件 (不跟随软链接): fifo/socket/char_device/block_device."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISFIFO(st.st_mode):
        return 'fifo'
    if stat.S_ISSOCK(st.st_mode):
        return 'socket'
    if stat.S_ISCHR(st.st_mode):
        return 'char_device'
    if stat.S_ISBLK(st.st_mode):
        return 'block_device'
    return None


def _path_b64_fields(rel_p):
    """非 UTF-8 路径的无损回传字段; UTF-8 路径返回空 dict."""
    try:
        rel_p.encode('utf-8')
        return {}
    except UnicodeEncodeError:
        return {'encoding': 'binary',
                'path_b64': base64.b64encode(
                    rel_p.encode('utf-8', 'surrogateescape')).decode('ascii')}


def safe_mode(mode_input, default=0o644):
    if mode_input is None or mode_input == '':
        return default
    if isinstance(mode_input, str):
        # "0000" 是合法输入 (4 位八进制), 不能 lstrip 成空后擅自替换成 644;
        # 全 0 交给统一策略: owner 不可读 → 回退调用方 default (文件 0644 / 目录 0755).
        m = int(mode_input, 8) if mode_input != '' else 0
    elif isinstance(mode_input, int):
        m = mode_input
    else:
        return default
    m = m & 0o777
    # owner 不可读 (0000/0200/0300) 时服务端无法 checksum/备份 → 回退默认;
    # 0400/0444/0500 等只读权限是合法同步状态, 原样保留 (与 PHP 端一致).
    if (m & 0o400) == 0:
        m = default
    return m


def valid_mtime_input(m):
    """mtime 入参校验 (0..253402300799 的秒级 unix 时间戳; int/整数值 float/数字字符串)."""
    _MAX_MTIME = 253402300799  # 9999-12-31T23:59:59Z
    if isinstance(m, bool):
        return False
    if isinstance(m, int):
        return 0 <= m <= _MAX_MTIME
    if isinstance(m, float):
        return 0 <= m <= _MAX_MTIME and m == int(m)
    return isinstance(m, str) and re.fullmatch(r'[0-9]{1,12}', m) is not None \
        and int(m) <= _MAX_MTIME


def bad_mtime(m):
    if isinstance(m, str):
        shown = '"%s"' % m
    elif isinstance(m, bool):
        shown = 'true' if m else 'false'
    elif isinstance(m, (int, float)):
        shown = str(m)
    elif m is None:
        shown = 'null'
    else:
        shown = _php_type_name(m)
    return fail(E['INVALID_PARAM'], 'bad_mtime',
                'mtime 必须是 0..253402300799 的秒级 unix 时间戳 (整数), 收到 %s' % shown,
                {'hint': 'mtime 用于保留文件修改时间, 如 "mtime": 1700000000; 不传则使用服务器当前时间。'
                         '若你手上是 13 位毫秒时间戳, 先除以 1000。'})


_PHP_NUMERIC_RE = re.compile(r'^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*$')


def _php_is_numeric(v):
    """PHP is_numeric() 语义 (bool 不算数字; 字符串必须是完整数字, 不接受 "10s")。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        return _PHP_NUMERIC_RE.match(v) is not None
    return False


def valid_mode_input(m):
    """mode 只接受 1-4 位八进制字符串, 与 PHP valid_mode_input() 完全一致。

    旧实现直接 safe_mode(): JSON 传整数 644 会被当成十进制按位与 -> 0o204 -> 低于 0600
    被静默提升为 0644; 传 "9" / "abc" 也被静默忽略。AI 以为权限已设置, 实际文件权限
    与请求毫无关系 —— 而且 PHP/Python 两端对同一输入的静默结果还不同。
    """
    return isinstance(m, str) and re.fullmatch(r'[0-7]{1,4}', m) is not None


def bad_mode(m):
    shown = ('"%s"' % m) if isinstance(m, str) else _php_type_name(m)
    return fail(E['INVALID_PARAM'], 'bad_mode',
                'mode 必须是 1-4 位八进制字符串 (如 "644"/"0644"/"4755"), 收到 %s' % shown,
                {'hint': 'JSON 中请用字符串: "mode": "0644"; setuid/setgid/sticky 位会被剥离; '
                         '0400 等只读权限会原样保留 (owner 不可读的 0000/0200 才回退 0644); 不传则保留目标文件原权限。'})


def _bool_flag(v, default=False):
    """严格布尔解析, 与 PHP bool_flag() 逐字对齐; 无法识别返回 None (调用方回 1011)。

    修复 bool() 的两个坑:
      1) 字符串 "false"/"no"/"off" 为真值 —— 调用方明确说"不要", 服务端却照做
         (delete recursive / copy overwrite / format write 都可能造成数据丢失);
      2) bool("0")=True 而 PHP !empty("0")=false, 同一请求双端结果相反。
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ('1', 'true', 'yes', 'on'):
            return True
        if s in ('0', 'false', 'no', 'off', ''):
            return False
    return None


def bad_bool(field, v):
    shown = ('"%s"' % v) if isinstance(v, str) else _php_type_name(v)
    return fail(E['INVALID_PARAM'], 'bad_bool',
                '%s 必须是布尔值 (true/false), 收到 %s' % (field, shown),
                {'hint': '支持 true/false、1/0、"true"/"false"、"1"/"0"、"yes"/"no"、"on"/"off"; '
                         '字符串 "false" 不会再被当成 true。'})


def reject_path(rel, action, req):
    # 空/缺 path 与"越界"是两类问题, 对调用方(尤其 AI)的修复动作完全不同:
    # 前者补字段, 后者改路径写法。旧实现统一回 1004 "security policy rejected", 会误导。
    if rel is None or (isinstance(rel, str) and rel.strip() == ''):
        return fail(E['INVALID_PARAM'], 'missing_path',
                    "Required field 'path' is missing or empty (received fields: %s)" % _brief_keys(req),
                    {'hint': '该 action 必须传 path (相对 work_dir 的路径); 列目录根用 path="."; '
                             '本次收到的完整字段见 message。'})
    if not isinstance(rel, str):
        return fail(E['INVALID_PARAM'], 'bad_path_type',
                    "'path' must be a string, got %s (received fields: %s)"
                    % (_php_type_name(rel), _brief_keys(req)),
                    {'hint': 'path 只能是字符串; 数组/对象请改为字符串路径。'})
    audit_log(action, req, E['INVALID_PATH'], {'blocked_rel': str(rel), 'blocked_reason': 'path_traversal'})
    return fail(E['INVALID_PATH'], 'invalid_path', "Path '%s' rejected" % rel,
                {'hint': '路径越界: 只接受相对 work_dir 的相对路径(如 src/a.php); '
                         '前导 / 会按 work_dir 虚拟根解析(/tmp/x → <work_dir>/tmp/x), 不是服务器绝对路径; '
                         '不要含 .. 或空字节。'})


def bad_path(rel, which='path'):
    """路径校验失败的统一出口 (供各 action 直接调用):
    缺字段/空值 -> 1011 missing_<which>; 类型不对 -> 1011 bad_path_type; 否则 -> 1004 越界。
    action 与请求体从请求上下文取, 用于审计与字段回显。"""
    req = getattr(_REQUEST_CTX, 'body', None) or {}
    act = getattr(_REQUEST_CTX, 'action', '') or ''
    return reject_path(rel, act, {**req, '_which': which})


# ==================== 审计日志 ====================
def _json_safe_links(obj):
    """把非 UTF-8 的 link_target 替换为可编码字符串, 并保证附带 link_target_b64.

    POSIX 链接目标可以是任意字节; Python json.dumps 遇到 surrogate 会在 encode 时抛错,
    一条坏链接就能让整个 list/stat 响应 500. 这里统一降级为 replace 字符串 + 无损 base64.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == 'link_target' and isinstance(v, str):
                try:
                    v.encode('utf-8')
                    out[k] = v
                    out.setdefault('link_target_b64', base64.b64encode(v.encode('utf-8')).decode('ascii'))
                except UnicodeEncodeError:
                    raw = v.encode('utf-8', 'surrogateescape')
                    out[k] = raw.decode('utf-8', 'replace')
                    out['link_target_b64'] = base64.b64encode(raw).decode('ascii')
                continue
            out[k] = _json_safe_links(v)
        return out
    if isinstance(obj, list):
        return [_json_safe_links(v) for v in obj]
    if isinstance(obj, str):
        try:
            obj.encode('utf-8')
            return obj
        except UnicodeEncodeError:
            return obj.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
    return obj


def audit_log(action, req, code, extra=None):
    cfg = _ctx_cfg()
    if not cfg.get('AUDIT_ENABLED', True):
        return
    log_file = os.path.join(cfg['WORK_DIR'].rstrip('/\\'), cfg['AUDIT_LOG'])
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        # 轮转
        amax = cfg.get('AUDIT_MAX_SIZE', 10 * 1024 * 1024)
        akeep = cfg.get('AUDIT_MAX_KEEP', 5)
        if os.path.isfile(log_file) and os.path.getsize(log_file) >= amax:
            for i in range(akeep, 0, -1):
                old = log_file + '.' + str(i)
                if i == akeep:
                    try: os.remove(old)
                    except OSError: pass
                else:
                    try: os.rename(log_file + '.' + str(i), log_file + '.' + str(i + 1))
                    except OSError: pass
            try: os.rename(log_file, log_file + '.1')
            except OSError: pass
        token = req.get('token', '') if isinstance(req, dict) else ''
        # token 只取标量: 数组/对象参与字符串拼接会抛 TypeError, 整条审计丢失
        if not isinstance(token, str):
            token = str(token) if isinstance(token, (int, float)) and not isinstance(token, bool) else ''
        ip = _ctx_ip()
        # 与 PHP 对齐: path 统一成字符串再落盘 (客户端可能传 int/list, 直接存会让下游当 dict key 时崩)
        _p = req.get('path', '') if isinstance(req, dict) else ''
        if not isinstance(_p, str):
            _p = str(_p) if isinstance(_p, (int, float)) and not isinstance(_p, bool) \
                else json.dumps(_p, ensure_ascii=False, separators=(',', ':'))
        entry = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            'action': action,
            'token_hash': (hashlib.sha256((cfg.get('TOKEN', '') + token).encode()).hexdigest()[:12]) if token else 'none',
            'ip': ip,
            'code': code,
            'path': _p,
            # 请求关联 id: 同一次 HTTP 请求的明细条目与通用条目可串起来排查
            'rid': getattr(_REQUEST_CTX, 'rid', ''),
        }
        if extra:
            entry.update(extra)
        _REQUEST_CTX.detail = getattr(_REQUEST_CTX, 'detail', 0) + 1
        entry = _json_safe_links(entry)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _audit_req_keys(req):
    """请求字段名列表 (只记名字不记值, 永不落 token/密码/content 等敏感内容)."""
    if not isinstance(req, dict):
        return ''
    return ','.join(sorted(str(k) for k in req.keys()
                           if str(k) != 'token' and not str(k).startswith('_')))


def _audit_action_extra(action, req):
    """按 action 补充少量可定位问题的字段 (命令预览/会话号/批量步骤名)."""
    if not isinstance(req, dict):
        return {}
    x = {}
    if isinstance(action, str) and action.startswith('shell_'):
        if isinstance(req.get('session_id'), str):
            x['session_id'] = req['session_id']
        if isinstance(req.get('job_id'), str):
            x['job_id'] = req['job_id']
        if isinstance(req.get('cmd'), str):
            x['cmd_preview'] = req['cmd'][:120]
        if 'timeout' in req:
            x['timeout'] = req['timeout']
    if action == 'pipeline' and isinstance(req.get('steps'), list):
        names = []
        for st in req['steps'][:30]:
            names.append(st.get('action', '') if isinstance(st, dict) and isinstance(st.get('action'), str) else '')
        x['steps'] = names
        x['step_count'] = len(req['steps'])
    return x


def audit_request(action, req, result, cfg, resp_bytes=0):
    """请求级兜底审计: action 内部未写明细时补一条通用记录.

    旧实现只记录写操作/鉴权失败, list/stat/diff/checksum/search/whoami/help 等
    查询类行为完全无记录。这里每个请求至少落一条 (明细优先, 不重复).
    """
    try:
        # pipeline 汇总始终记录 (内部 step 的明细无法体现整体结果)
        if getattr(_REQUEST_CTX, 'detail', 0) and action != 'pipeline':
            return
        if not isinstance(result, dict):
            return
        code = result.get('code', 0)
        if not isinstance(code, int):
            try:
                code = int(code)
            except Exception:
                code = 0
        _h = getattr(_REQUEST_CTX, 'headers', None)
        try:
            ua = str(_h.get('User-Agent', '') if _h is not None else '')[:120]
        except Exception:
            ua = ''
        extra = {
            'ok': code == 0,
            'method': 'POST',
            'ua': ua,
            'req_bytes': int(getattr(_REQUEST_CTX, 'req_bytes', 0) or 0),
            'resp_bytes': int(resp_bytes or 0),
            'elapsed_ms': int(round((time.time() - getattr(_REQUEST_CTX, 't0', time.time())) * 1000)),
            'req_keys': _audit_req_keys(req),
        }
        if isinstance(cfg, dict):
            if cfg.get('SCOPE'):
                extra['scope'] = str(cfg['SCOPE'])
            if 'TOKEN_NAME' in cfg:
                extra['token_name'] = str(cfg.get('TOKEN_NAME', ''))
            if 'IS_ADMIN' in cfg:
                extra['is_admin'] = bool(cfg.get('IS_ADMIN'))
        if code != 0:
            extra['error'] = str(result.get('error', ''))
        sf = result.get('_stream_file')
        if sf:
            extra['stream'] = True
            if not extra['resp_bytes']:
                try:
                    extra['resp_bytes'] = os.path.getsize(sf)
                except OSError:
                    pass
        extra.update(_audit_action_extra(action, req))
        audit_log(action, req, code, extra)
    except Exception:
        pass


def _read_tail_lines(path, n):
    """读文件末尾 n 行 (不整文件读入)"""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, n * 4096)
            f.seek(max(0, size - chunk), os.SEEK_SET)
            data = f.read()
        lines = data.splitlines()
        return lines[-n:]
    except Exception:
        return []


# ==================== 备份 ====================
def _utf8_basename(p):
    p = p.replace('\\', '/')
    i = p.rfind('/')
    return p[i + 1:] if i >= 0 else p


def _utf8_dirname(p):
    p = p.replace('\\', '/')
    i = p.rfind('/')
    if i < 0:
        return '.'
    if i == 0:
        return '/'
    return p[:i]


def _backup_validate_filename(fn):
    if not isinstance(fn, str) or fn == '':
        return False
    if '/' in fn or '\\' in fn:
        return False
    if fn in ('.', '..'):
        return False
    return bool(re.match(r'^[^\/\\]+\.bak\.\d{8}_\d{6}_[0-9a-f]{6}$', fn))


def _safe_mtime(path):
    """并发清理时文件可能刚被别的线程删掉; 排序键不能抛异常."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# CAS (expect_md5) 的进程内线程锁 + 跨进程 flock.
#   key=真实目标路径, flock 文件放在 work_dir/ai-sync/locks/<md5>.lock,
#   与 PHP 端使用同一路径/算法, 两个服务端进程操作同一 work_dir 时也能互斥.
_CAS_TLOCKS = {}
_CAS_TLOCKS_GUARD = threading.Lock()


@contextmanager
def _cas_file_lock(dest, cfg):
    key = hashlib.md5(dest.encode('utf-8', 'surrogateescape')).hexdigest()
    with _CAS_TLOCKS_GUARD:
        tlock = _CAS_TLOCKS.setdefault(key, threading.Lock())
    tlock.acquire()
    fh = None
    try:
        try:
            lock_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), 'ai-sync', 'locks')
            os.makedirs(lock_dir, exist_ok=True)
            fh = open(os.path.join(lock_dir, key + '.lock'), 'a+')
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            fh = None  # 锁不可用时不阻塞上传; CAS 退化为尽力而为
        yield
    finally:
        if fh is not None:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass
        tlock.release()


def _create_backup(dest, rel, cfg):
    backup_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['BACKUP_DIR'])
    os.makedirs(backup_dir, exist_ok=True)
    file_backup_dir = os.path.join(backup_dir, _utf8_dirname(rel), _utf8_basename(rel))
    os.makedirs(file_backup_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + hashlib.md5((rel + str(time.time())).encode()).hexdigest()[:6]
    backup_file = os.path.join(file_backup_dir, _utf8_basename(rel) + '.bak.' + ts)
    if _special_kind(dest) is not None:
        return ''   # FIFO/socket/设备节点无法安全复制 (会阻塞/无限流)
    try:
        shutil.copy2(dest, backup_file)
    except Exception:
        return ''
    pattern = os.path.join(file_backup_dir, _utf8_basename(rel) + '.bak.*')
    try:
        files = sorted(glob.glob(pattern), key=_safe_mtime, reverse=True)
        keep = cfg.get('BACKUP_KEEP', 5)
        if len(files) > keep:
            for old in files[keep:]:
                try: os.remove(old)
                except OSError: pass
    except Exception:
        pass
    return _utf8_basename(backup_file)


def _link_backup_body(target):
    """链接备份内容: 自描述头 + base64(raw target). 可表达换行/非 UTF-8/任意字节."""
    raw = target.encode('utf-8', 'surrogateescape') if isinstance(target, str) else bytes(target)
    return b'AISYNC-SYMLINK-V1\n' + base64.b64encode(raw) + b'\n'


def _create_link_backup(dest, rel, cfg):
    """软链接备份: 不跟随目标, 只记录 raw target (目标可能在 work_dir 外)."""
    try:
        target = os.readlink(dest)
    except OSError:
        return ''
    backup_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['BACKUP_DIR'])
    os.makedirs(backup_dir, exist_ok=True)
    file_backup_dir = os.path.join(backup_dir, _utf8_dirname(rel), _utf8_basename(rel))
    os.makedirs(file_backup_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + hashlib.md5((rel + str(time.time())).encode()).hexdigest()[:6]
    backup_file = os.path.join(file_backup_dir, _utf8_basename(rel) + '.bak.' + ts)
    try:
        with open(backup_file, 'wb') as fh:
            fh.write(_link_backup_body(target))
        os.chmod(backup_file, 0o644)
    except Exception:
        return ''
    pattern = os.path.join(file_backup_dir, _utf8_basename(rel) + '.bak.*')
    try:
        files = sorted(glob.glob(pattern), key=_safe_mtime, reverse=True)
        keep = cfg.get('BACKUP_KEEP', 5)
        if len(files) > keep:
            for old in files[keep:]:
                try: os.remove(old)
                except OSError: pass
    except Exception:
        pass
    return _utf8_basename(backup_file)


def _link_backup_target(backup_path):
    """读取链接备份的 raw target; 返回 str 或 None (普通文件/无法识别)."""
    if os.path.islink(backup_path):
        return None
    try:
        with open(backup_path, 'rb') as fh:
            raw = fh.read(8192)
    except OSError:
        return None
    magic = b'AISYNC-SYMLINK-V1\n'
    if raw.startswith(magic):
        line = raw[len(magic):].split(b'\n', 1)[0]
        try:
            return base64.b64decode(line, validate=True).decode('utf-8', 'surrogateescape')
        except Exception:
            return None
    if raw.startswith(b'symlink -> '):
        t = raw[11:]
        if t.endswith(b'\n'):
            t = t[:-1]
        if t and b'\n' not in t:
            return t.decode('utf-8', 'surrogateescape')
    return None


def _backup_entry_type(backup_path):
    return 'symlink' if _link_backup_target(backup_path) is not None else 'file'


def _restore_backup_to_dest(backup_path, dest, cfg=None):
    """按备份类型恢复: 链接→链接 (临时链接+replace), 文件→文件 (临时文件+replace).

    返回 'symlink'|'file'|None. 任何情况下都不写穿 dest 现有的链接.
    """
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, 0o755, exist_ok=True)
    link_target = _link_backup_target(backup_path)
    tmp = dest + '.restore_tmp.' + os.urandom(4).hex()
    if link_target is not None:
        try:
            os.symlink(link_target, tmp)
            os.replace(tmp, dest)
            return 'symlink'
        except OSError:
            try: os.remove(tmp)
            except OSError: pass
            return None
    try:
        shutil.copy2(backup_path, tmp)
        os.replace(tmp, dest)
        return 'file'
    except OSError:
        try: os.remove(tmp)
        except OSError: pass
        return None


def _backup_scope_rel(dest, work_dir):
    d = str(dest).replace('\\', '/')
    wd = str(work_dir).replace('\\', '/').rstrip('/')
    if wd == '':
        return d.lstrip('/')
    if d == wd:
        return ''
    if d.startswith(wd + '/'):
        return d[len(wd) + 1:]
    # work_dir 本身可能是链接: 用 realpath 再比一次
    rd = os.path.realpath(str(dest)).replace('\\', '/')
    rwd = os.path.realpath(str(work_dir)).replace('\\', '/').rstrip('/')
    if rd == rwd:
        return ''
    if rd.startswith(rwd + '/'):
        return rd[len(rwd) + 1:]
    return d.lstrip('/')


# ==================== 限流 ====================
# 内存滑动窗口计数: key=(类型,'ip'/'tk', 值, 窗口起点) -> count。单进程多线程下无文件 I/O, 锁内仅字典操作。
_RATE = {}

def rate_limit_check(req, cfg):
    # FIX-v4.34: pipeline 的多步会在同一秒内集中打到同一个 IP/token,
    #   若每步都按独立请求计费, 10 步就可能撞满 per_ip_per_second(默认 10) →
    #   AI 用批量反而被限流, 与 pipeline "省往返" 的目的直接冲突。
    #   处理原则: **只读 step 不重复计费**(管道外层已计 1 次); **写类 step 照常计费**,
    #   否则 pipeline 会成为绕过 upload_per_minute 的后门。
    if getattr(_REQUEST_CTX, 'pipeline_readonly_step', False):
        return {'limited': False}
    rl = cfg.get('RATE_LIMIT', {})
    if not rl.get('enabled', False):
        return {'limited': False}
    ip = _ctx_ip() or 'unknown'
    token = req.get('token', '')
    token_h = hashlib.sha256((cfg.get('TOKEN', '') + str(token)).encode()).hexdigest()[:12]
    action = req.get('action', 'unknown')
    is_upload = action in ('upload', 'upload_batch', 'chunked_upload')
    now = int(time.time())
    minute = now // 60
    with STATE['rate_lock']:
        ipk = ('ip', ip, now)
        c = _RATE.get(ipk, 0) + 1
        _RATE[ipk] = c
        if c > rl.get('per_ip_per_second', 10):
            return {'limited': True, 'retry_after': 1, 'limit': rl.get('per_ip_per_second', 10),
                    'window': '1s', 'message': 'IP rate limit exceeded'}
        mk = ('tk', token_h, minute)
        tc = _RATE.get(mk, 0) + 1
        _RATE[mk] = tc
        tk_limit = rl.get('upload_per_minute', 30) if is_upload else rl.get('per_token_per_minute', 60)
        if tc > tk_limit:
            retry = 60 - (now % 60)
            return {'limited': True, 'retry_after': retry, 'limit': tk_limit, 'window': '1m',
                    'message': 'Token rate limit exceeded'}
        # 惰性 GC: 只保留当前秒/当前分钟的窗口 key
        if len(_RATE) > 4000:
            for k in [k for k in _RATE
                      if (k[0] == 'ip' and k[2] != now) or (k[0] == 'tk' and k[2] != minute)]:
                _RATE.pop(k, None)
    return {'limited': False}


def _atomic_count_inc(path):
    try:
        with open(path, 'a+') as f:
            pass
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except Exception:
        return sys.maxsize  # fail-closed
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, 'r+') as f:
            val = int(f.read().strip() or '0') + 1
            f.seek(0); f.truncate(); f.write(str(val))
            f.flush()
        return val
    except Exception:
        return sys.maxsize
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


# ==================== 工具: 递归删除 ====================
def _rmtree(dir_path, root=None):
    real_dir = os.path.realpath(dir_path)
    if root:
        real_root = os.path.realpath(root)
        if real_dir != real_root and not (real_dir + os.sep).startswith(real_root + os.sep):
            return {'ok': False, 'reason': 'out_of_bounds', 'path': real_dir}
    failed = []
    try:
        for entry in os.scandir(dir_path):
            p = entry.path
            if entry.is_symlink():
                try: os.unlink(p)
                except OSError: failed.append(p)
            elif entry.is_dir():
                sub = _rmtree(p, root or dir_path)
                if not sub['ok']:
                    failed.extend(sub.get('failed', [p]))
            else:
                try: os.unlink(p)
                except OSError: failed.append(p)
        try: os.rmdir(dir_path)
        except OSError:
            if os.path.isdir(dir_path):
                failed.append(dir_path)
    except Exception as e:
        return {'ok': False, 'reason': 'scandir_fail', 'failed': [str(e)]}
    return {'ok': len(failed) == 0, 'failed': failed}


# 内存 metrics 累加 + 批量落盘 (去掉每请求读改写 metrics.json 的重 I/O)
_METRICS = {'data': None, 'dirty': 0, 'last_flush': 0.0}

def _metrics_load(cfg):
    f = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['METRICS_FILE'])
    d = {}
    if os.path.isfile(f):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                d = json.load(fh) or {}
        except Exception:
            d = {}
    d.setdefault('since', int(time.time()))
    # 与 PHP 对齐: 空状态时 actions/errors 是空数组 (PHP 用 [] 初始化), 首次计数后才变成对象
    d.setdefault('actions', [])
    d.setdefault('errors', [])
    d.setdefault('total', 0)
    return d

def _metrics_flush(cfg):
    m = _METRICS
    if m['data'] is None:
        return
    try:
        f = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['METRICS_FILE'])
        with open(f, 'w', encoding='utf-8') as fh:
            json.dump(m['data'], fh, ensure_ascii=False)
        m['dirty'] = 0
        m['last_flush'] = time.time()
    except Exception:
        pass

def _metrics_inc(cfg, action, code):
    try:
        with STATE['rate_lock']:
            m = _METRICS
            if m['data'] is None:
                m['data'] = _metrics_load(cfg)
            d = m['data']
            # PHP 用 [] 初始化, 首次 $arr[$k]=.. 时自动变对象; Python 需显式转换以保持同样 JSON 形态
            if not isinstance(d.get('actions'), dict):
                d['actions'] = {}
            d['actions'][action] = d['actions'].get(action, 0) + 1
            if code != 0:
                if not isinstance(d.get('errors'), dict):
                    d['errors'] = {}
                d['errors'][str(code)] = d['errors'].get(str(code), 0) + 1
            d['total'] = d.get('total', 0) + 1
            d['last_ts'] = int(time.time())
            m['dirty'] += 1
            if m['dirty'] >= 50 or (time.time() - m['last_flush']) > 5:
                _metrics_flush(cfg)
    except Exception:
        pass


def _scope_disk_stats(work_dir):
    file_count = 0
    size_bytes = 0
    if os.path.isdir(work_dir):
        for root, dirs, files in os.walk(work_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    file_count += 1
                    size_bytes += os.path.getsize(fp)
                except OSError:
                    pass
    return {'file_count': file_count, 'size_bytes': size_bytes}


def _scope_backup_count(work_dir, backup_dir_rel):
    bd = os.path.join(work_dir.rstrip('/'), backup_dir_rel)
    if not os.path.isdir(bd):
        return 0
    return sum(1 for _ in glob.glob(os.path.join(bd, '**', '*.zip'), recursive=True))


# ==================== 文件类 action ====================
# 服务端簿记目录: 业务搜索/列举/校验和默认跳过 (显式以这些目录为根时仍可用)
_INTERNAL_DIRS = ('ai-sync', '.sync_oplog', '.ai-deploy-tmp')


def _is_internal_dir(name):
    return name in _INTERNAL_DIRS or name.startswith('.sync')


# Python 类型 -> PHP gettype() 词表, 保证错误文案两端逐字一致
_PHP_TYPES = {'str': 'string', 'int': 'integer', 'float': 'double', 'bool': 'boolean',
              'list': 'array', 'dict': 'array', 'tuple': 'array', 'NoneType': 'NULL'}


def _php_type_name(v):
    return _PHP_TYPES.get(type(v).__name__, type(v).__name__)


def _lev(a, b):
    """Levenshtein 距离 (与 PHP levenshtein() 同语义, 用于 help 的拼写纠正建议)。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
        prev = cur
    return prev[lb]


def _brief_keys(req, limit=8):
    """回显请求字段名 (排除 token), 供调用方自查是否拼错字段。
    AI 客户端最常犯的错是字段名笔误 (content vs content_b64), 只回 "参数非法" 无法自愈。"""
    if not isinstance(req, dict):
        return '<not an object>'
    # _orig_keys: _normalize_body 会剔除 null 值字段 (等价 PHP 的 ??), 但它仍在"已收到字段"里,
    # 否则 path:null 时 Python 回 <none>、PHP 回 path, 两端提示不一致。
    _keys_src = req.get('_orig_keys')
    if not isinstance(_keys_src, list):
        _keys_src = list(req.keys())
    # 排除协议字段与内部标记 (_which 等下划线键), 只回显调用方真正传的业务字段
    ks = [k for k in _keys_src if k not in ('token', 'scope', 'action') and not str(k).startswith('_')]
    if not ks:
        return '<none>'
    s = ', '.join(ks[:limit])
    if len(ks) > limit:
        s += ', ...(+%d more)' % (len(ks) - limit)
    return s


def _b64_decode_php(s):
    """等价 PHP base64_decode($s, true): 忽略任意空白, 允许省略尾部 padding,
    但拒绝非字母表字符; 非法输入返回 None (PHP 返回 false)。

    Python 的 b64decode 要求长度 4 的倍数, 对 "123" 这种 3 字符输入直接报错,
    而 PHP 会解出 2 字节 → 同样的请求两端结果不一致 (fuzz 用例 upload content_b64="123")。
    """
    if not isinstance(s, str):
        return None
    s = ''.join(s.split())
    # PHP strict 模式: 出现 '=' 时总长必须是 4 的倍数 ("YQ=" -> false, "YWI=" -> true)
    if '=' in s and len(s) % 4 != 0:
        return None
    pad = (-len(s)) % 4
    if pad == 3:          # 长度 % 4 == 1, 无法补齐 → PHP 同样返回 false
        return None
    try:
        return base64.b64decode(s + '=' * pad, validate=True)
    except Exception:
        return None


def _action_upload_impl(req, cfg):
    rel = req.get('path', '')
    # 不跟随最后一级软链接: 目标已是链接时要"替换链接本身", 而不是把内容写进它指向的文件.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    if not dest:
        return reject_path(rel, 'upload', req)
    # path 以 / 结尾表示目录, 但 upload 写的是文件:
    #   旧行为在 PHP 上拼出 "a/" → 写失败 1008; 在 Python 上 realpath 规范化掉尾斜杠
    #   → 静默创建名为 a 的文件。两端不一致且都反直觉, 统一在入口拒绝。
    if isinstance(rel, str) and re.search(r'[/\\]+$', rel):
        return fail(E['INVALID_PARAM'], 'bad_path',
                    "upload 的 path 不能以 / 结尾 (它是文件路径): '%s'" % rel,
                    {'hint': '要写入目录请给出完整文件名, 如 "dir/a.txt"; 要创建目录请用 mkdir。'})
    # 形如 "dir/." / "dir/./" 的路径等价于目录本身, 不可能作为文件写入:
    # 旧行为先 makedirs(dir) 再 replace 到 dir/. → 1008 write_fail, AI 无法从错误定位.
    if os.path.basename(dest) == '.':
        return fail(E['INVALID_PARAM'], 'bad_dest_path',
                    "upload 的目标必须以文件名结尾, 不能是目录路径: '%s'" % rel,
                    {'hint': '形如 "dir/." 的 path 等价于目录 dir; 请给出具体文件名, 如 "dir/a.txt"。'})
    # ---- 软链接上传: link_target / link_target_b64 存在时创建/替换链接本身, 不需要 content_b64 ----
    has_link_target = (('link_target' in req and req.get('link_target') is not None)
                       or ('link_target_b64' in req and req.get('link_target_b64') is not None))
    if has_link_target:
        # b64 是权威值 (可表达非 UTF-8 目标); link_target 保留给旧客户端和可读性.
        if req.get('link_target_b64') is not None:
            if not isinstance(req.get('link_target_b64'), str):
                audit_log('upload', req, E['INVALID_PARAM'])
                return fail(E['INVALID_PARAM'], 'link_target_b64_not_string',
                            'link_target_b64 must be a base64 string, got %s'
                            % _php_type_name(req.get('link_target_b64')))
            try:
                link_target = base64.b64decode(req['link_target_b64'], validate=True) \
                    .decode('utf-8', 'surrogateescape')
            except Exception:
                audit_log('upload', req, E['INVALID_PARAM'])
                return fail(E['INVALID_PARAM'], 'bad_link_target_b64',
                            'link_target_b64 decode failed',
                            {'hint': 'link_target_b64 必须是 raw target 字节的 base64 编码'})
        else:
            if not isinstance(req.get('link_target'), str):
                audit_log('upload', req, E['INVALID_PARAM'])
                return fail(E['INVALID_PARAM'], 'link_target_not_string',
                            'link_target must be a string, got %s' % _php_type_name(req.get('link_target')),
                            {'hint': 'link_target 是软链接指向的相对路径 (如 "../lib/a.so" 或 "target.txt")。'})
            link_target = req['link_target']
        # 父目录可能尚不存在 (首次向新目录上传链接): 先按已校验的安全路径创建父目录,
        # 否则 _link_meta_from_target() 里 realpath(dirname) 失败 → 误报 invalid_link_target.
        # safe_path_nofollow 已解析最近存在祖先并确保仍在 work_dir 内, 这里 makedirs 不会逃逸.
        parent = os.path.dirname(dest)
        if not os.path.isdir(parent):
            if os.path.exists(parent):
                return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                            'Cannot create parent directory: a path component is a file, not a directory',
                            {'hint': '目标路径的父级已存在同名文件; 请换路径或先删除/重命名该文件。'})
            try:
                os.makedirs(parent, 0o755, exist_ok=True)
            except OSError:
                pass
            if not os.path.isdir(parent):
                return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                            'Cannot create parent directory (permission denied or path component is a file)',
                            {'hint': '检查 WORK_DIR 写权限, 以及目标路径中是否存在同名文件。'})
        lm = _link_meta_from_target(link_target, dest, cfg['WORK_DIR'])
        if not lm['safe']:
            audit_log('upload', req, E['INVALID_PATH'],
                      {'reason': 'invalid_link_target', 'why': lm.get('reason') or ''})
            return fail(E['INVALID_PATH'], 'invalid_link_target',
                        '软链接目标被拒绝: %s' % (lm.get('reason') or 'invalid'),
                        {'hint': 'link_target 必须是相对路径, 且归一化后仍在 work_dir 内; '
                                 '绝对路径/../ 越界/中间链接逃逸都会被拒绝。',
                         'link_target': link_target})
        link_rel = lm['target_rel']
        link_md5 = hashlib.md5(lm['target_norm'].encode('utf-8', 'surrogateescape')).hexdigest()
        base_md5 = req.get('expect_md5')
        if base_md5 is None:
            base_md5 = req.get('base_md5')
        if base_md5 is None:
            base_md5 = ''
        if base_md5 and (os.path.islink(dest) or os.path.exists(dest)):
            cur_md5 = _node_md5(dest, cfg['WORK_DIR'])
            if cur_md5 != base_md5:
                audit_log('upload', req, E['CHECKSUM_MISMATCH'], {'reason': 'cas_conflict'})
                return fail(E['CHECKSUM_MISMATCH'], 'cas_conflict',
                            '远程对象已被修改, 期望 md5=%s 实际 md5=%s' % (base_md5, cur_md5),
                            {'current_md5': cur_md5, 'expected_md5': base_md5,
                             'hint': '先 stat/checksum 重新读取最新状态再改; 若确认覆盖请去掉 expect_md5。'})
        if os.path.isdir(dest) and not os.path.islink(dest):
            return fail(E['INVALID_PARAM'], 'dest_is_dir',
                        "目标 '%s' 已存在且是目录, 不能用软链接替换" % rel,
                        {'hint': '先删除或重命名该目录, 或换一个链接路径。'})
        if not os.path.islink(dest):
            sk = _special_kind(dest)
            if sk is not None:
                return fail(E['INVALID_PARAM'], 'dest_is_special',
                            "目标 '%s' 已存在且是特殊文件 (%s), 拒绝直接覆盖" % (rel, sk),
                            {'path': rel, 'special_type': sk,
                             'hint': '特殊文件无法备份/读取; 先 delete 删除该节点, 再上传链接或文件。'})
        backed_up = ''
        if (os.path.islink(dest) or os.path.exists(dest)) and cfg.get('BACKUP_KEEP', 0) > 0:
            backed_up = _create_link_backup(dest, rel, cfg) if os.path.islink(dest) \
                else _create_backup(dest, rel, cfg)
        tmp_link = dest + '.upload_tmp.' + os.urandom(4).hex()
        try:
            os.symlink(link_target, tmp_link)
        except OSError:
            audit_log('upload', req, E['UPLOAD_FAILED'], {'reason': 'symlink_fail'})
            return fail(E['UPLOAD_FAILED'], 'symlink_fail', 'Cannot create symlink (permission denied?)',
                        {'hint': '目标文件系统可能不支持符号链接 (FAT/exFAT), 或父目录不可写。'})
        try:
            os.replace(tmp_link, dest)
        except OSError:
            try: os.remove(tmp_link)
            except OSError: pass
            audit_log('upload', req, E['UPLOAD_FAILED'], {'reason': 'link_replace_fail'})
            return fail(E['UPLOAD_FAILED'], 'write_fail', 'Cannot replace destination with symlink')
        # 创建后再算一次 broken: 自指/环链在创建前目标不存在 (broken=true), 创建后链接本身已存在.
        lm_after = _link_meta_from_target(link_target, dest, cfg['WORK_DIR'])
        try:
            lst = os.lstat(dest)
            mtime = int(lst.st_mtime)
            mode = '%04o' % stat.S_IMODE(lst.st_mode)
        except OSError:
            mtime = int(time.time())
            mode = '0777'
        _metrics_inc(cfg, 'upload', 0)
        audit_log('upload', req, 0, {'type': 'symlink', 'link_target': link_target, 'md5': link_md5})
        return ok({'type': 'symlink', 'size': 0, 'md5': link_md5, 'mtime': mtime,
                   'mode': mode, 'link_target': link_target,
                   'link_target_b64': base64.b64encode(
                       link_target.encode('utf-8', 'surrogateescape')).decode('ascii'),
                   'link_target_rel': lm_after['target_rel'] if lm_after['safe'] else link_rel,
                   'link_target_rel_b64': base64.b64encode(lm_after['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm_after['safe'] else None,
                   'broken': lm_after['broken'], 'cycle': bool(lm_after.get('cycle')),
                   'resolvable': bool(lm_after.get('resolvable')),
                   'backed_up': backed_up, 'lint': None})
    # 缺 content_b64 必须拒绝: base64 缺省解码为 "" == 上传空文件, 会静默清空已有内容,
    # 且旧行为返回 ok:true+size:0, 调用方(尤其 AI)无法察觉 (静默数据损坏)。
    if 'content_b64' not in req or req.get('content_b64') is None:
        audit_log('upload', req, E['INVALID_PARAM'])
        return fail(E['INVALID_PARAM'], 'missing_content_b64',
                    "Required field 'content_b64' is missing or null (received fields: %s)" % _brief_keys(req),
                    {'hint': 'upload 必须带 content_b64 (文件内容的 base64 字符串)。'
                             '写空文件请显式传 content_b64=""; 若你用的是 content/text/data/body '
                             '等字段名, 改名为 content_b64; 超过 MAX_SIZE 改用 chunked_upload '
                             '(op=init → put ×N → assemble)。'})
    # 类型必须显式校验: base64_decode(123) 会隐式转成 "123" 并解出 2 字节, 是静默错误。
    if not isinstance(req.get('content_b64'), str):
        audit_log('upload', req, E['INVALID_PARAM'])
        return fail(E['INVALID_PARAM'], 'content_b64_not_string',
                    'content_b64 must be a base64 string, got %s' % _php_type_name(req.get('content_b64')),
                    {'hint': 'content_b64 必须是字符串: 空文件传 ""; 二进制先做 base64 编码再传。'})
    data = _b64_decode_php(req.get('content_b64', ''))
    if data is None:
        return fail(E['INVALID_PARAM'], 'bad_base64',
                    'content_b64 base64 解码失败 (长度 %d)' % len(req.get('content_b64', '')),
                    {'hint': 'content_b64 必须是标准 base64 (A-Z a-z 0-9 + /, 可带 = 填充); '
                             '不要传 data: URI 前缀、文件路径或原始文本; 空文件传 "".'})
    if len(data) > cfg.get('MAX_SIZE', 64 * 1024 * 1024):
        audit_log('upload', req, E['FILE_TOO_LARGE'])
        return fail(E['FILE_TOO_LARGE'], 'too_large', 'Size %d exceeds MAX_SIZE' % len(data))
    # FIX-v4.33 (P0): 单文件 upload 此前完全不支持乐观锁 —— 只有 batch_deploy 的每项支持
    #   base_md5, 且契约(HELP_SPEC)里没声明任何 CAS 字段, AI 无从防覆盖:
    #   并发场景下 AI 读到旧 md5 → 改内容 → 写回, 中间别人的改动被静默吃掉。
    #   现在统一接受 expect_md5 / base_md5 两个名字(expect_md5 是 HELP_SPEC 对外契约名,
    #   base_md5 是 batch_deploy 历史内部名), 冲突返回 1007 + 当前真实 md5。
    # 与 PHP `??` 语义一致: expect_md5 显式传空串时不再回退到 base_md5
    base_md5 = req.get('expect_md5')
    if base_md5 is None:
        base_md5 = req.get('base_md5')
    if base_md5 is None:
        base_md5 = ''
    if base_md5 and (os.path.islink(dest) or os.path.exists(dest)):
        cur = _node_md5(dest, cfg['WORK_DIR'])
        if cur != base_md5:
            audit_log('upload', req, E['CHECKSUM_MISMATCH'], {'reason': 'cas_conflict'})
            return fail(E['CHECKSUM_MISMATCH'], 'cas_conflict',
                        '远程文件已被修改, 期望 md5=%s 实际 md5=%s' % (base_md5, cur),
                        {'current_md5': cur, 'expected_md5': base_md5,
                         'hint': '先 --cat 重新读取最新内容再改; 若确认覆盖请去掉 expect_md5。'
                                 '字段名提示: 叫 expect_md5(对外契约名), 历史 batch_deploy 里叫 base_md5'})
    if os.path.isdir(dest) and not os.path.islink(dest):
        return fail(E['INVALID_PARAM'], 'dest_is_dir',
                    "目标 '%s' 已存在且是目录, 不能用文件替换" % rel,
                    {'hint': '先删除或重命名该目录, 或换一个文件路径。'})
    if not os.path.islink(dest):
        sk = _special_kind(dest)
        if sk is not None:
            return fail(E['INVALID_PARAM'], 'dest_is_special',
                        "目标 '%s' 已存在且是特殊文件 (%s), 拒绝直接覆盖" % (rel, sk),
                        {'path': rel, 'special_type': sk,
                         'hint': 'FIFO/socket/设备节点无法备份/校验; 先 delete 删除该节点, 再上传文件。'})
    # 未显式传 mode 时保留目标原权限(新文件 0644): 与 PHP 端一致,
    # 避免 upload_file/write-remote 把 0755 脚本降级成 0644。
    mode_req = req.get('mode')
    if mode_req in (None, ''):
        if not os.path.islink(dest) and os.path.exists(dest):
            upload_mode = os.stat(dest).st_mode & 0o777
        else:
            upload_mode = 0o644
    else:
        if not valid_mode_input(mode_req):
            return bad_mode(mode_req)
        upload_mode = safe_mode(mode_req)
    if upload_mode <= 0:
        upload_mode = 0o644
    # mtime: 客户端推送时带本地 mtime, 落盘后原样恢复 (默认用当前时间)
    upload_mtime = None
    mtime_req = req.get('mtime')
    if mtime_req not in (None, ''):
        if not valid_mtime_input(mtime_req):
            audit_log('upload', req, E['INVALID_PARAM'])
            return bad_mtime(mtime_req)
        upload_mtime = int(mtime_req)
    backed_up = ''
    if (os.path.islink(dest) or os.path.exists(dest)) and cfg.get('BACKUP_KEEP', 0) > 0:
        backed_up = _create_link_backup(dest, rel, cfg) if os.path.islink(dest) \
            else _create_backup(dest, rel, cfg)
    # 父目录: 必须真的能建出来. 旧实现 makedirs 遇"中间某段是文件"直接抛
    #   FileExistsError → 1099 内部错误, 调用方不知道是哪一层出了问题。
    parent = os.path.dirname(dest)
    if not os.path.isdir(parent):
        if os.path.exists(parent):
            return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                        'Cannot create parent directory: a path component is a file, not a directory',
                        {'hint': '目标路径的父级已存在同名文件 (如 "a" 是文件, 却要写 "a/b.txt"); '
                                 '请换路径或先删除/重命名该文件。'})
        try:
            os.makedirs(parent, 0o755, exist_ok=True)
        except OSError:
            pass
        if not os.path.isdir(parent):
            return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                        'Cannot create parent directory (permission denied or path component is a file)',
                        {'hint': '检查 WORK_DIR 写权限, 以及目标路径中是否存在同名文件。'})
    tmp = dest + '.upload_tmp.' + os.urandom(4).hex()
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
        os.chmod(tmp, upload_mode)
        os.replace(tmp, dest)
        if upload_mtime is not None:
            try:
                os.utime(dest, (upload_mtime, upload_mtime))
            except OSError:
                pass
    except Exception as e:
        try: os.remove(tmp)
        except OSError: pass
        audit_log('upload', req, E['UPLOAD_FAILED'])
        return fail(E['UPLOAD_FAILED'], 'write_fail', 'Cannot write to destination')
    _metrics_inc(cfg, 'upload', 0)
    audit_log('upload', req, 0, {'size': len(data)})
    lint = None
    ext = os.path.splitext(dest)[1].lower().lstrip('.')
    if ext == 'php':
        php = _find_php_cli()
        if php:
            try:
                out = subprocess.run([php, '-l', dest], capture_output=True, text=True, timeout=30)
                lint = {'passed': out.returncode == 0, 'exit_code': out.returncode, 'output': (out.stdout + out.stderr).strip()}
            except Exception as e:
                lint = {'passed': False, 'exit_code': -1, 'output': str(e)}
    return ok({'size': len(data), 'md5': hashlib.md5(data).hexdigest(), 'mtime': int(os.path.getmtime(dest)),
               'mode': '%04o' % (upload_mode & 0o7777), 'backed_up': backed_up, 'lint': lint})


def action_upload(req, cfg):
    """带 expect_md5 的上传串行化: CAS 是 check-then-act, 并发下必须加锁才成立.

    不加锁时 12 路并发同一文件可全部通过旧 md5 校验, 后写覆盖先写 (静默丢更新).
    锁只覆盖同一目标路径的 CAS 上传; 普通上传/其它 action 不受影响.
    """
    need_cas = (req.get('expect_md5') if isinstance(req.get('expect_md5'), str) else '') \
        or (req.get('base_md5') if isinstance(req.get('base_md5'), str) else '')
    if not need_cas:
        return _action_upload_impl(req, cfg)
    rel = req.get('path', '')
    dest = safe_path_nofollow(rel, cfg['WORK_DIR']) if isinstance(rel, str) else None
    if not dest:
        return _action_upload_impl(req, cfg)  # 让实现层返回统一的 invalid_path
    with _cas_file_lock(dest, cfg):
        return _action_upload_impl(req, cfg)


def action_upload_batch(req, cfg):
    # FIX-v4.34 (P1 静默降级): 旧版缺 items 时视为空数组 → 循环 0 次 → 返回
    #   ok:true + ok_idx=[] + total:0。AI 本意是"批量上传这些文件", 却读到 ok=true,
    #   一个字节都没传也不知道。与 upload 缺 content_b64 的严格性保持一致。
    raw_items = None
    if 'items' in req:
        raw_items = req.get('items')
    elif 'files' in req:
        raw_items = req.get('files')
    if raw_items is None:
        return fail(E['INVALID_PARAM'], 'missing_items', 'upload_batch 缺少必填参数',
                    {'hint': '必须传 items 数组 (每项 {path, content_b64}); files 为历史别名仍兼容。'
                             '单个文件请直接用 upload action。',
                     'received_fields': _brief_keys(req)})
    if isinstance(raw_items, dict):
        # 对齐 PHP: {} -> [] (empty_items), 非空对象按值展开
        raw_items = list(raw_items.values())
    if not isinstance(raw_items, list):
        return fail(E['INVALID_PARAM'], 'bad_items', 'items must be array')
    if not raw_items:
        return fail(E['INVALID_PARAM'], 'empty_items', 'items 数组不能为空 (没有要上传的文件)',
                    {'hint': 'items 至少包含 1 项; 若只想上传单个文件请用 upload action。'})
    items = raw_items
    ok_idx, fail_idx, errors, results = [], [], {}, []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            fail_idx.append(i); errors[i] = 'bad_item'; continue
        rel = item.get('path', '')
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            reject_path(rel, 'upload_batch', {**req, '_idx': i})
            fail_idx.append(i); errors[i] = 'invalid_path'; continue
        if isinstance(rel, str) and re.search(r'[/\\]+$', rel):
            fail_idx.append(i); errors[i] = 'bad_path'; continue
        if os.path.basename(dest) == '.':
            fail_idx.append(i); errors[i] = 'bad_dest_path'; continue
        if os.path.isdir(dest) and not os.path.islink(dest):
            # 与单文件 upload 一致: 目标是目录时明确报 dest_is_dir, 而不是备份目录后 write_fail
            fail_idx.append(i); errors[i] = 'dest_is_dir'; continue
        if 'content_b64' not in item or item.get('content_b64') is None:
            fail_idx.append(i); errors[i] = 'missing_content_b64'; continue
        if not isinstance(item.get('content_b64'), str):
            fail_idx.append(i); errors[i] = 'content_b64_not_string'; continue
        data = _b64_decode_php(item.get('content_b64', ''))
        if data is None:
            fail_idx.append(i); errors[i] = 'bad_base64'; continue
        if len(data) > cfg.get('MAX_SIZE', 64 * 1024 * 1024):
            fail_idx.append(i); errors[i] = 'too_large'; continue
        if not os.path.islink(dest) and _special_kind(dest) is not None:
            fail_idx.append(i); errors[i] = 'dest_is_special'; continue
        backed_up = ''
        if (os.path.islink(dest) or os.path.exists(dest)) and cfg.get('BACKUP_KEEP', 0) > 0:
            backed_up = _create_link_backup(dest, rel, cfg) if os.path.islink(dest) \
                else _create_backup(dest, rel, cfg)
        item_mode_req = item.get('mode')
        if item_mode_req in (None, ''):
            item_mode = (os.stat(dest).st_mode & 0o777) \
                if (not os.path.islink(dest) and os.path.exists(dest)) else 0o644
        else:
            if not valid_mode_input(item_mode_req):
                fail_idx.append(i); errors[i] = 'bad_mode'; continue
            item_mode = safe_mode(item_mode_req)
        if item_mode <= 0:
            item_mode = 0o644
        item_mtime = None
        item_mtime_req = item.get('mtime')
        if item_mtime_req not in (None, ''):
            if not valid_mtime_input(item_mtime_req):
                fail_idx.append(i); errors[i] = 'bad_mtime'; continue
            item_mtime = int(item_mtime_req)
        parent = os.path.dirname(dest)
        if not os.path.isdir(parent):
            if os.path.exists(parent):
                fail_idx.append(i); errors[i] = 'parent_not_dir'; continue
            try:
                os.makedirs(parent, 0o755, exist_ok=True)
            except OSError:
                pass
            if not os.path.isdir(parent):
                fail_idx.append(i); errors[i] = 'parent_not_dir'; continue
        tmp = dest + '.upload_tmp.' + os.urandom(4).hex()
        try:
            with open(tmp, 'wb') as f:
                f.write(data)
            os.chmod(tmp, item_mode)
            os.replace(tmp, dest)
            if item_mtime is not None:
                try:
                    os.utime(dest, (item_mtime, item_mtime))
                except OSError:
                    pass
        except Exception:
            try: os.remove(tmp)
            except OSError: pass
            fail_idx.append(i); errors[i] = 'write_fail'; continue
        ok_idx.append(i)
        results.append({'size': len(data), 'md5': hashlib.md5(data).hexdigest(),
                        'mtime': int(os.path.getmtime(dest)), 'mode': '%04o' % (item_mode & 0o7777),
                        'backed_up': backed_up})
        audit_log('upload_batch', {**req, 'path': rel}, 0, {'size': len(data), 'md5': hashlib.md5(data).hexdigest(), 'backup': backed_up or None})
    _metrics_inc(cfg, 'upload_batch', 1008 if fail_idx else 0)
    err_list = [errors.get(i) for i in range(len(items))] if errors else []
    return ok({'ok_idx': ok_idx, 'fail_idx': fail_idx, 'errors': err_list, 'results': results,
               'total': len(items), 'ok_count': len(ok_idx), 'failed_count': len(fail_idx),
               'partial': bool(ok_idx) and bool(fail_idx)})


def action_download(req, cfg):
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "File '%s' not found" % rel)
    if os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'is_dir', 'Cannot download a directory')
    sk = _special_kind(dest)
    if sk is not None:
        return fail(E['INVALID_PARAM'], 'not_regular_file',
                    "Not a regular file (%s); 文件协议只能传输普通文件" % sk,
                    {'special_type': sk,
                     'hint': '特殊节点请用 exec 在服务器端处理, 或先用 delete 删除'})
    sz = os.path.getsize(dest)
    # 大文件走流式响应, 避免整包 base64/JSON 常驻内存 (峰值 O(块)); 阈值 <=0 表示禁用流式
    _stream_kb = cfg.get('STREAM_THRESHOLD_KB', 2048)
    if _stream_kb and sz > int(_stream_kb) * 1024:
        return ok({'_stream_file': dest})
    with open(dest, 'rb') as f:
        data = f.read()
    return ok({'content_b64': base64.b64encode(data).decode('ascii'),
               'size': len(data), 'md5': hashlib.md5(data).hexdigest(),
               'mode': '%04o' % (os.stat(dest).st_mode & 0o7777),
               'mtime': int(os.stat(dest).st_mtime)})


def action_stat(req, cfg):
    rel = req.get('path', '')
    # 不跟随最后一级: stat 一个软链接时返回链接本身的信息.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.islink(dest) and not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    try:
        st = os.lstat(dest)
    except OSError:
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    data = {
        'path': rel,
        'type': 'file',
        'size': st.st_size,
        'mtime': int(st.st_mtime),
        'ctime': int(st.st_ctime),
        'atime': int(st.st_atime),
        'mode': '%04o' % (st.st_mode & 0o7777),
        # 属主/硬链接/稀疏诊断 (传输层不保留, 但 AI 需要能观测到真实状态)
        'uid': st.st_uid,
        'gid': st.st_gid,
        'nlink': st.st_nlink,
        'ino': st.st_ino,
        'blocks': getattr(st, 'st_blocks', None),
    }
    # 对齐 PHP stat: 恒定返回 path_b64 (非 UTF-8 路径的无损寻址)
    data['path_b64'] = base64.b64encode(
        rel.encode('utf-8', 'surrogateescape')).decode('ascii')
    try:
        rel.encode('utf-8')
    except UnicodeEncodeError:
        data['encoding'] = 'binary'
    if os.path.islink(dest):
        lm = _link_meta(dest, cfg['WORK_DIR'])
        data['type'] = 'symlink'
        data['size'] = 0
        data['safe'] = lm['safe']
        data['broken'] = lm['broken']
        data['cycle'] = bool(lm.get('cycle'))
        data['resolvable'] = bool(lm.get('resolvable'))
        data['link_target'] = lm['target'] if lm['safe'] else None
        data['link_target_rel'] = lm['target_rel'] if lm['safe'] else None
        data['link_target_rel_b64'] = (base64.b64encode(lm['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm['safe'] else None)
        if lm['safe'] and lm['target'] is not None:
            data['link_target_b64'] = base64.b64encode(
                lm['target'].encode('utf-8', 'surrogateescape')).decode('ascii')
        if not lm['safe'] and lm.get('reason'):
            data['reason'] = lm['reason']
        if lm['safe'] and lm.get('target_rel') is not None:
            data['md5'] = hashlib.md5(lm['target_norm'].encode('utf-8', 'surrogateescape')).hexdigest()
        return ok(data)
    if os.path.isdir(dest):
        data['type'] = 'dir'
        data['size'] = 0
        try:
            data['child_count'] = len([e for e in os.scandir(dest) if e.name != '.' and e.name != '..'])
        except OSError:
            data['child_count'] = 0
    else:
        sk = _special_kind(dest)
        if sk is not None:
            # 特殊文件: 只返回元数据, 绝不 open (FIFO 无写端时 read 会永久阻塞).
            data['type'] = 'special'
            data['special_type'] = sk
            data['readable'] = False
            data['size'] = 0
            data['hint'] = '文件协议只能传输普通文件; 特殊节点请用 exec 在服务器端处理'
            return ok(data)
        with open(dest, 'rb') as fh:
            blob = fh.read()
        data['md5'] = hashlib.md5(blob).hexdigest()
        data['sha1'] = hashlib.sha1(blob).hexdigest()
    return ok(data)


def _walk_tree_sorted(root, max_depth=10, include_hidden=False, allow_internal=False):
    """确定性目录遍历 (list/checksum/diff 共用).

    每层先输出子目录 (按名字), 再输出普通文件和软链接 (按名字), 然后递归真实子目录.
    软链接目录绝不递归 (防环/防越界/防重复枚举目标).
    """
    def walk(d, depth):
        if depth >= max_depth:
            return
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        dirs, files = [], []
        for n in names:
            if not include_hidden and n.startswith('.'):
                continue
            if not allow_internal and depth == 0 and n == 'ai-sync':
                continue
            full = os.path.join(d, n)
            if os.path.islink(full):
                files.append(n)      # 软链接按"文件"排序输出, 不进入递归
            elif os.path.isdir(full):
                dirs.append(n)
            else:
                files.append(n)
        for n in dirs:
            yield {'full': os.path.join(d, n), 'name': n, 'type': 'dir'}
        for n in files:
            full = os.path.join(d, n)
            yield {'full': full, 'name': n, 'type': 'symlink' if os.path.islink(full) else 'file'}
        for n in dirs:
            yield from walk(os.path.join(d, n), depth + 1)
    yield from walk(root, 0)


def action_list(req, cfg):
    rel = req.get('path')
    if rel is None or rel == '':
        rel = '.'   # 与 PHP 一致: path 可选, 默认根目录
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    if not os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'not_dir', 'Path is not a directory')
    dest = os.path.normpath(dest)
    recursive = _bool_flag(req.get('recursive'))
    if recursive is None:
        return bad_bool('recursive', req.get('recursive'))
    pattern = req.get('pattern')
    if pattern is not None and not isinstance(pattern, str):
        return fail(E['INVALID_PARAM'], 'bad_pattern', 'pattern 必须是字符串 (如 "*.php")',
                    {'hint': 'pattern 用于按文件名过滤, 不能传 true/数字/数组/对象'})
    include_hidden = _bool_flag(req.get('include_hidden'))
    if include_hidden is None:
        return bad_bool('include_hidden', req.get('include_hidden'))
    # max_depth=0 表示不限深度 (客户端全量拉取目录状态时使用); 默认仍为 10 层.
    max_depth = int(req.get('max_depth', 10))
    if max_depth <= 0:
        max_depth = 1 << 62
    offset = int(req.get('offset', 0))
    limit = int(req.get('limit', 200))
    if limit > 1000:
        limit = 1000
    since_mtime = int(req.get('since_mtime', 0))
    base = os.path.realpath(cfg['WORK_DIR']).rstrip('/\\') + os.sep
    items = []
    total = 0
    # 服务端簿记目录 ai-sync/ (audit.log / backups / chunks / rate 计数 / metrics) 默认不返回:
    # 两端内部文件数天然不同, 混进 items 会让分页窗口错位、客户端把内部文件当业务文件。
    # 显式请求 ai-sync 或其中子路径时才照常返回。
    _req_norm = (rel or '').replace('\\', '/').strip('/')
    allow_internal = (_req_norm == 'ai-sync' or _req_norm.startswith('ai-sync/'))

    def _is_internal_rel(p):
        p = p.replace('\\', '/')
        return p == 'ai-sync' or p.startswith('ai-sync/')

    walk_depth = max_depth if recursive else 1
    for e in _walk_tree_sorted(dest, walk_depth, include_hidden, allow_internal):
        full = e['full']
        try:
            lst = os.lstat(full)
        except OSError:
            continue
        mtime = int(lst.st_mtime)
        if since_mtime > 0 and mtime < since_mtime:
            continue
        rel_path = full[len(base):]
        if not allow_internal and _is_internal_rel(rel_path):
            continue
        if pattern and not fnmatch.fnmatch(e['name'], pattern):
            continue
        item = {'name': e['name'], 'path': rel_path, 'type': e['type'],
                'size': 0, 'mtime': mtime, 'mode': '%04o' % (lst.st_mode & 0o7777)}
        if e['type'] == 'symlink':
            lm = _link_meta(full, cfg['WORK_DIR'])
            item['safe'] = lm['safe']
            item['link_target'] = lm['target'] if lm['safe'] else None
            item['link_target_rel'] = lm['target_rel'] if lm['safe'] else None
            item['link_target_rel_b64'] = (base64.b64encode(lm['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm['safe'] else None)
            item['broken'] = lm['broken']
            item['cycle'] = bool(lm.get('cycle'))
            item['resolvable'] = bool(lm.get('resolvable'))
            if lm['safe'] and lm['target'] is not None:
                item['link_target_b64'] = base64.b64encode(
                    lm['target'].encode('utf-8', 'surrogateescape')).decode('ascii')
            if not lm['safe']:
                item['reason'] = lm['reason']
        elif e['type'] == 'dir':
            try:
                item['child_count'] = len([x for x in os.scandir(full) if x.name not in ('.', '..')])
            except OSError:
                item['child_count'] = 0
        else:
            sk = _special_kind(full)
            if sk is not None:
                # FIFO/socket/设备节点: 不能当普通文件读取 (open 会阻塞), 显式分类.
                item['type'] = 'special'
                item['special_type'] = sk
                item['readable'] = False
                item['size'] = 0
            else:
                item['size'] = int(lst.st_size)
        try:
            rel_path.encode('utf-8')
        except UnicodeEncodeError:
            # 非 UTF-8 文件名: JSON 字符串只能给 replace 后的近似值, 精确寻址用 path_b64.
            item['encoding'] = 'binary'
            item['path_b64'] = base64.b64encode(
                rel_path.encode('utf-8', 'surrogateescape')).decode('ascii')
            item['name_b64'] = base64.b64encode(
                e['name'].encode('utf-8', 'surrogateescape')).decode('ascii')
        items.append(item)
        total += 1
    has_more = (offset + limit) < total
    page = items[offset:offset + limit]
    return ok({'path': rel, 'total': total, 'offset': offset, 'limit': limit,
               'has_more': has_more, 'items': page, 'since_mtime': since_mtime})


def action_diff(req, cfg):
    local_items = req.get('local_items', [])
    if isinstance(local_items, dict):
        local_items = list(local_items.values())  # 对齐 PHP: 对象按值展开为数组
    if not isinstance(local_items, list):
        # PHP: 非数组 local_items 传入 foreach 会被跳过 → 视为空清单
        local_items = []
    remote_path = req.get('path', '')
    algorithm = req.get('algorithm', 'md5')
    dest = safe_path(remote_path, cfg['WORK_DIR'])
    if not dest:
        return bad_path(remote_path)  # v4.29 修复: 原误写未定义的 rel, path 非法时 NameError→1099
    if not os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'not_dir', 'Remote path must be a directory')
    base = os.path.realpath(cfg['WORK_DIR']).rstrip('/\\') + os.sep
    remote_index = {}
    skipped_special = 0
    skipped = []
    want_hash = _bool_flag(req.get('compute_hash'))
    if want_hash is None:
        return bad_bool('compute_hash', req.get('compute_hash'))
    if want_hash:
        # 对齐 checksum: 白名单外的算法返回 1011, 而不是静默退化成 md5 摘要
        if not isinstance(algorithm, str):
            return fail(E['INVALID_PARAM'], 'bad_algorithm',
                        'algorithm 必须是字符串: ' + '|'.join(_HASH_SUPPORTED_ALGS),
                        {'supported': list(_HASH_SUPPORTED_ALGS),
                         'hint': 'algorithm 不能传 true/数字/数组/对象'})
        algorithm = algorithm.strip().lower()
        if algorithm not in _HASH_SUPPORTED_ALGS:
            return fail(E['INVALID_PARAM'], 'bad_algorithm',
                        "不支持的 algorithm '%s' (支持: %s)" % (algorithm, '|'.join(_HASH_SUPPORTED_ALGS)),
                        {'supported': list(_HASH_SUPPORTED_ALGS)})
    # 同 list/checksum: 默认不把 ai-sync/ 簿记文件算进业务差异
    _dreq = (remote_path or '').replace('\\', '/').strip('/')
    _d_allow = (_dreq == 'ai-sync' or _dreq.startswith('ai-sync/'))
    for e in _walk_tree_sorted(dest, 1 << 62, True, _d_allow):
        if e['type'] == 'dir':
            continue
        full = e['full']
        relp = full[len(base):]
        if not _d_allow and (relp.replace('\\', '/') == 'ai-sync'
                             or relp.replace('\\', '/').startswith('ai-sync/')):
            continue
        if e['type'] == 'symlink':
            lm = _link_meta(full, cfg['WORK_DIR'])
            try:
                lst = os.lstat(full)
                mtime = int(lst.st_mtime)
            except OSError:
                mtime = 0
            entry = {'size': 0, 'mtime': mtime, 'type': 'symlink',
                     'safe': lm['safe'],
                     'link_target': lm['target'] if lm['safe'] else None,
                     'link_target_rel': lm['target_rel'] if lm['safe'] else None,
                     'link_target_rel_b64': (base64.b64encode(lm['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm['safe'] else None),
                     'broken': lm['broken'],
                     'cycle': bool(lm.get('cycle')),
                     'resolvable': bool(lm.get('resolvable'))}
            if lm['safe'] and lm['target'] is not None:
                entry['link_target_b64'] = base64.b64encode(
                    lm['target'].encode('utf-8', 'surrogateescape')).decode('ascii')
            if want_hash:
                entry[algorithm] = _hash_str_alg(lm['target_norm'], algorithm) \
                    if lm['safe'] and lm.get('target_norm') is not None else ''
            remote_index[relp] = entry
            continue
        try:
            st = os.lstat(full)
        except OSError:
            continue
        sk = _special_kind(full)
        if sk is not None:
            # 特殊文件不能 hash (会阻塞), 也不参与同步比较; 显式计数避免"静默少文件".
            skipped_special += 1
            if len(skipped) < 100:
                skipped.append({'path': relp, 'special_type': sk})
            continue
        entry = {'size': int(st.st_size), 'mtime': int(st.st_mtime)}
        if want_hash:
            entry[algorithm] = _hash_file_alg(full, algorithm)
        remote_index[relp] = entry
    local_index = {it['path']: it for it in local_items if isinstance(it, dict) and 'path' in it}
    only_local, only_remote, modified, identical = [], [], [], 0
    for p, l in local_index.items():
        if p not in remote_index:
            only_local.append(p); continue
        r = remote_index[p]
        eq_size = l.get('size') is not None and _php_intval(l.get('size')) == int(r['size'])
        eq_mtime = l.get('mtime') is not None and _php_intval(l.get('mtime')) == int(r['mtime'])
        eq_hash = False
        if l.get(algorithm) is not None and r.get(algorithm) is not None:
            eq_hash = l[algorithm] == r[algorithm]
        if eq_size and (eq_mtime or eq_hash):
            identical += 1
        else:
            reason = 'mtime_diff' if eq_hash else ('hash_diff' if eq_size else 'size_diff')
            modified.append({'path': p, 'local': l, 'remote': r, 'reason': reason})
    for p in remote_index:
        if p not in local_index:
            only_remote.append(p)
    return ok({'summary': {'only_local': len(only_local), 'only_remote': len(only_remote),
                           'modified': len(modified), 'identical': identical},
               'skipped_special': skipped_special, 'skipped': skipped,
               'only_local': only_local, 'only_remote': only_remote, 'modified': modified})


def action_delete(req, cfg):
    rel = req.get('path', '')
    # 不跟随最后一级: 删除软链接必须 unlink 链接本身, 绝不能删掉它指向的目标.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.islink(dest) and not os.path.exists(dest):
        return ok({'already_missing': True})
    typ = 'symlink' if os.path.islink(dest) else ('dir' if os.path.isdir(dest) else 'file')
    # P0 安全边界: path="." / "/" 经 safe_path_nofollow 会解析为 work_dir 根.
    # 旧实现允许 recursive=true 清空整个 scope 工作区, 且返回 ok=true.
    root_real = os.path.realpath(cfg['WORK_DIR'].rstrip('/\\'))
    if typ == 'dir' and os.path.realpath(dest) == root_real:
        audit_log('delete', req, E['INVALID_PARAM'], {'reason': 'root_delete_blocked', 'path': str(rel)})
        return fail(E['INVALID_PARAM'], 'root_delete_not_allowed',
                    '拒绝删除 scope 工作目录根: 该操作会清空整个工作区',
                    {'path': rel,
                     'hint': 'path="." / "/" 指向 work_dir 根, 不允许删除。要清理目录请传具体子目录名; '
                             '确实要清空工作区请在服务器端操作。'})
    backed_up = ''
    recursive = _bool_flag(req.get('recursive'))
    if recursive is None:
        return bad_bool('recursive', req.get('recursive'))
    if typ == 'file' and cfg.get('BACKUP_KEEP', 0) > 0:
        backed_up = _create_backup(dest, rel, cfg)
    elif typ == 'symlink' and cfg.get('BACKUP_KEEP', 0) > 0:
        backed_up = _create_link_backup(dest, rel, cfg)
    if typ == 'symlink':
        try:
            os.unlink(dest)
        except OSError as e:
            return fail(E['INTERNAL'], 'unlink_fail', 'Permission denied: %s' % e)
    elif typ == 'dir':
        if recursive:
            res = _rmtree(dest, cfg['WORK_DIR'])
            if not res['ok']:
                return fail(E['INTERNAL'], 'rmdir_fail', 'Recursive delete failed (partial)',
                            {'reason': res.get('reason', 'partial'), 'failed_count': len(res.get('failed', [])),
                             'failed_sample': res.get('failed', [])[:10]})
        else:
            try:
                os.rmdir(dest)
            except OSError as e:
                return fail(E['INTERNAL'], 'rmdir_fail', 'Directory not empty or permission denied (use recursive=true for non-empty dirs)')
    else:
        try:
            os.unlink(dest)
        except OSError as e:
            return fail(E['INTERNAL'], 'unlink_fail', 'Permission denied: %s' % e)
    audit_log('delete', req, 0, {'type': typ, 'backed_up': backed_up, 'recursive': recursive})
    return ok({'deleted': rel, 'type': typ, 'backed_up': backed_up, 'recursive': recursive})


def action_rename(req, cfg):
    rel = req.get('from')
    if rel is None:
        rel = req.get('path', '')
    new_rel = req.get('to')
    if new_rel is None:
        new_rel = req.get('new_path', '')
    if rel == '' or new_rel == '':
        return fail(E['INVALID_PARAM'], 'missing_param', 'from/to are required',
                    {'hint': '需要 from 与 to 两个路径 (也可用 path / new_path 别名); 两者都必须相对 work_dir'})
    # 不跟随最后一级软链接: mv 链接时移动的是链接本身, 而不是它指向的目标.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    new_dest = safe_path_nofollow(new_rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel, 'from')
    if not new_dest:
        return bad_path(new_rel, 'to')
    # 根目录不能被重命名: from="." 解析为 work_dir 本身, os.rename 会 OSError 并兜成 1099.
    _src_root = os.path.realpath(cfg['WORK_DIR'].rstrip('/\\'))
    if os.path.realpath(dest) == _src_root:
        audit_log('rename', req, E['INVALID_PARAM'], {'reason': 'root_rename_blocked'})
        return fail(E['INVALID_PARAM'], 'root_rename_not_allowed',
                    '拒绝重命名 scope 工作目录根: from="." 指向 work_dir 本身',
                    {'hint': 'from 必须是 work_dir 内的具体文件/子目录; 根目录不能移动。'})
    # 断链 os.path.exists()=False, 必须先用 islink 判断, 否则断链既不能移动, 也不会被识别为"已存在".
    if not os.path.islink(dest) and not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "from '%s' not found" % rel)
    if os.path.islink(new_dest) or os.path.exists(new_dest):
        return fail(E['INVALID_PARAM'], 'dst_exists', "to '%s' already exists" % new_rel)
    mv_backed_up = 0
    if os.path.islink(dest):
        # 链接备份只记录 raw target, 不能跟随目标 (目标可能在 work_dir 外)
        try:
            if _create_link_backup(dest, rel, cfg):
                mv_backed_up = 1
        except Exception:
            mv_backed_up = 0
    elif os.path.isfile(dest):
        try:
            if _create_backup(dest, rel, cfg):
                mv_backed_up = 1
        except Exception:
            mv_backed_up = 0
    parent = os.path.dirname(new_dest)
    if not os.path.isdir(parent):
        if os.path.exists(parent):
            return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                        'Cannot create parent directory: a path component is a file, not a directory',
                        {'hint': 'to 路径的父级已存在同名文件; 请换路径或先删除/重命名该文件。'})
        try:
            os.makedirs(parent, 0o755, exist_ok=True)
        except OSError:
            pass
        if not os.path.isdir(parent):
            return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                        'Cannot create parent directory (permission denied or path component is a file)',
                        {'hint': '检查 WORK_DIR 写权限, 以及 to 路径中是否存在同名文件。'})
    try:
        os.replace(dest, new_dest)
    except OSError:
        return fail(E['INTERNAL'], 'rename_fail', 'rename failed')
    # isdir 会跟随链接, 必须先判 islink, 否则"链接 -> 目录"会被误报成 dir.
    mv_type = 'symlink' if os.path.islink(new_dest) else ('dir' if os.path.isdir(new_dest) else 'file')
    out = {'from': rel, 'to': new_rel, 'type': mv_type, 'backed_up': mv_backed_up}
    if mv_type == 'symlink':
        lm = _link_meta(new_dest, cfg['WORK_DIR'])
        out['link_target'] = lm['target'] if lm['safe'] else None
        out['link_target_rel'] = lm['target_rel'] if lm['safe'] else None
        out['link_target_rel_b64'] = (base64.b64encode(lm['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm['safe'] else None)
        out['broken'] = lm['broken']
        out['cycle'] = bool(lm.get('cycle'))
        out['resolvable'] = bool(lm.get('resolvable'))
        if lm['safe'] and lm['target'] is not None:
            out['link_target_b64'] = base64.b64encode(
                lm['target'].encode('utf-8', 'surrogateescape')).decode('ascii')
        if not lm['safe']:
            out['reason'] = lm.get('reason')
    audit_log('rename', req, 0, {'from': rel, 'to': new_rel, 'type': mv_type})
    return ok(out)


def action_copy(req, cfg):
    rel = req.get('from')
    if rel is None:
        rel = req.get('path', '')
    new_rel = req.get('to')
    if new_rel is None:
        new_rel = req.get('new_path', '')
    if rel == '' or new_rel == '':
        return fail(E['INVALID_PARAM'], 'missing_param', 'from/to are required',
                    {'hint': '需要 from 与 to 两个路径 (也可用 path / new_path 别名); 两者都必须相对 work_dir'})
    # 不跟随最后一级软链接:
    #   源是链接 -> 在目标处重建链接 (保留 raw target), 不复制它指向的内容;
    #   目标是链接 -> 替换链接本身, 不写穿到它指向的文件.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    new_dest = safe_path_nofollow(new_rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel, 'from')
    if not new_dest:
        return bad_path(new_rel, 'to')
    if not os.path.islink(dest) and not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "from '%s' not found" % rel)
    src_is_link = os.path.islink(dest)
    if not src_is_link and os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'is_dir', 'Source is a directory, use chunked or batch')
    if not src_is_link:
        sk = _special_kind(dest)
        if sk is not None:
            return fail(E['INVALID_PARAM'], 'src_is_special',
                        "from '%s' 是特殊文件 (%s), 无法复制内容" % (rel, sk),
                        {'path': rel, 'special_type': sk,
                         'hint': 'FIFO/socket/设备节点只能用 exec 在服务器端处理'})
    overwrite = _bool_flag(req.get('overwrite'))
    if overwrite is None:
        return bad_bool('overwrite', req.get('overwrite'))
    # 断链 exists()=False, 用 islink 一并判断, 否则 overwrite 时会把断链当"不存在"而静默覆盖.
    dst_exists = os.path.islink(new_dest) or os.path.exists(new_dest)
    if dst_exists and not overwrite:
        return fail(E['INVALID_PARAM'], 'dst_exists',
                    "to '%s' already exists (传 overwrite=true 覆盖)" % new_rel,
                    {'hint': '覆盖前会自动备份; 不传 overwrite 时拒绝静默覆盖'})
    if os.path.isdir(new_dest) and not os.path.islink(new_dest):
        return fail(E['INVALID_PARAM'], 'dst_is_dir',
                    "to '%s' 已存在且是目录, 不能复制覆盖" % new_rel,
                    {'hint': '先删除或重命名该目录, 或换一个目标路径。'})
    if not os.path.islink(new_dest):
        sk = _special_kind(new_dest)
        if sk is not None:
            return fail(E['INVALID_PARAM'], 'dst_is_special',
                        "to '%s' 已存在且是特殊文件 (%s), 拒绝覆盖" % (new_rel, sk),
                        {'path': new_rel, 'special_type': sk,
                         'hint': '先 delete 删除该节点, 再复制'})
    backed_up = ''
    if dst_exists and cfg.get('BACKUP_KEEP', 0) > 0:
        # 目标原为链接时按链接备份 (目标可能在 work_dir 外, 不能跟随)
        try:
            backed_up = (_create_link_backup(new_dest, new_rel, cfg) if os.path.islink(new_dest)
                         else _create_backup(new_dest, new_rel, cfg)) or ''
        except Exception:
            backed_up = ''
    # 父目录可能尚不存在 (safe_path_nofollow 已确保最近存在祖先在 work_dir 内, 此处 mkdir 不会逃逸)
    parent = os.path.dirname(new_dest)
    if not os.path.isdir(parent):
        if os.path.exists(parent):
            return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                        'Cannot create parent directory: a path component is a file, not a directory',
                        {'hint': 'to 路径的父级已存在同名文件; 请换路径或先删除/重命名该文件。'})
        try:
            os.makedirs(parent, 0o755, exist_ok=True)
        except OSError:
            pass
        if not os.path.isdir(parent):
            return fail(E['UPLOAD_FAILED'], 'parent_not_dir',
                        'Cannot create parent directory (permission denied or path component is a file)',
                        {'hint': '检查 WORK_DIR 写权限, 以及 to 路径中是否存在同名文件。'})
    if src_is_link:
        try:
            link_target = os.readlink(dest)
        except OSError:
            return fail(E['INTERNAL'], 'readlink_fail', 'Cannot read source symlink')
        # 链接文本原样复制, 但按目标位置的父目录重新校验落点必须仍在 work_dir 内
        lm = _link_meta_from_target(link_target, new_dest, cfg['WORK_DIR'])
        if not lm['safe']:
            return fail(E['INVALID_PATH'], 'invalid_link_target',
                        '软链接目标被拒绝: %s' % (lm.get('reason') or 'invalid'),
                        {'hint': '源链接的 raw target 复制到目标位置后会越出 work_dir (绝对路径/../ 越界)。',
                         'link_target': link_target})
        tmp_link = new_dest + '.copy_tmp.' + os.urandom(4).hex()
        try:
            os.symlink(link_target, tmp_link)
        except OSError:
            return fail(E['UPLOAD_FAILED'], 'symlink_fail', 'Cannot create symlink (permission denied?)',
                        {'hint': '目标文件系统可能不支持符号链接 (FAT/exFAT), 或父目录不可写。'})
        try:
            os.replace(tmp_link, new_dest)
        except OSError:
            try:
                os.unlink(tmp_link)
            except OSError:
                pass
            return fail(E['INTERNAL'], 'copy_fail', 'copy failed')
        lm_after = _link_meta(new_dest, cfg['WORK_DIR'])
        link_md5 = hashlib.md5(lm_after['target_norm'].encode('utf-8', 'surrogateescape')).hexdigest() \
            if lm_after['safe'] and lm_after.get('target_norm') is not None else ''
        try:
            _lst_after = os.lstat(new_dest)
            _mt = int(_lst_after.st_mtime)
            _mode = '%04o' % stat.S_IMODE(_lst_after.st_mode)
        except OSError:
            _mt = int(time.time()); _mode = '0777'
        audit_log('copy', req, 0, {'type': 'symlink', 'link_target': link_target,
                                   'backed_up': backed_up or None})
        return ok({'from': rel, 'to': new_rel, 'type': 'symlink', 'size': 0, 'md5': link_md5,
                   'mtime': _mt, 'mode': _mode,
                   'link_target': lm_after['target'] if lm_after['safe'] else None,
                   'link_target_b64': (base64.b64encode(
                       lm_after['target'].encode('utf-8', 'surrogateescape')).decode('ascii')
                       if lm_after['safe'] and lm_after['target'] is not None else None),
                   'link_target_rel': lm_after['target_rel'] if lm_after['safe'] else None,
                   'link_target_rel_b64': (base64.b64encode(lm_after['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm_after['safe'] else None),
                   'broken': lm_after['broken'],
                   'cycle': bool(lm_after.get('cycle')),
                   'resolvable': bool(lm_after.get('resolvable')),
                   'backed_up': backed_up})
    # 普通文件: 先写同目录临时文件再 replace — 若目标处是链接, 直接写会写穿到它指向的文件.
    tmp_copy = new_dest + '.copy_tmp.' + os.urandom(4).hex()
    try:
        shutil.copy2(dest, tmp_copy)
        os.replace(tmp_copy, new_dest)
    except OSError:
        try:
            os.unlink(tmp_copy)
        except OSError:
            pass
        return fail(E['INTERNAL'], 'copy_fail', 'copy failed')
    audit_log('copy', req, 0, {'from': rel, 'to': new_rel, 'backed_up': backed_up or None})
    try:
        with open(new_dest, 'rb') as _fh:
            _md5 = hashlib.md5(_fh.read()).hexdigest()
    except OSError:
        _md5 = ''
    try:
        _fmode = '%04o' % stat.S_IMODE(os.stat(new_dest).st_mode)
    except OSError:
        _fmode = ''
    return ok({'from': rel, 'to': new_rel, 'type': 'file', 'size': os.path.getsize(new_dest),
               'md5': _md5, 'mode': _fmode, 'backed_up': backed_up})


def action_mkdir(req, cfg):
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    mode_req = req.get('mode')
    has_mode = mode_req not in (None, '')
    if has_mode and not valid_mode_input(mode_req):
        return bad_mode(mode_req)
    mode = safe_mode(mode_req, 0o755) if has_mode else 0o755
    if os.path.isdir(dest):
        # 已存在也给 mode: 目录被 upload 隐式创建后需要回填正确权限
        if has_mode:
            try:
                os.chmod(dest, mode)
            except OSError:
                pass
        return ok({'path': rel, 'already_exists': True, 'mode': mode})
    # 同名文件已存在: mkdir 只会抛 FileExistsError, 旧实现报 1099 / 1008,
    # 调用方无法判断是"路径被文件占用"还是"权限/磁盘问题"。
    if os.path.exists(dest):
        return fail(E['INVALID_PARAM'], 'path_is_file',
                    "Path '%s' 已存在且是文件, 不能创建同名目录" % rel,
                    {'hint': '换一个目录名, 或先删除/重命名该文件 (delete/rename)。'})
    try:
        os.makedirs(dest, mode=mode, exist_ok=True)
    except OSError:
        pass
    if os.path.isdir(dest):
        try:
            os.chmod(dest, mode)
        except OSError:
            pass
        audit_log('mkdir', req, 0, {'mode': mode})
        return ok({'path': rel, 'mode': mode, 'recursive': True})
    return fail(E['UPLOAD_FAILED'], 'mkdir_fail',
                'Cannot create directory (a parent component is a file, or permission denied)',
                {'hint': '检查路径中间是否已有同名文件, 以及 WORK_DIR 下是否有写权限。'})


# MD5 缓存 (用于 checksum)
_MD5_CACHE = {}
_MD5_CACHE_LOCK = threading.Lock()

# 两端 (PHP/Python) 共同的算法白名单。刻意不含 PHP 的 'crc32' (BZIP2 变体):
# Python zlib 只能算标准 CRC-32 (crc32b), 纯 Python 实现 BZIP2 对几十 MB 文件过慢,
# 保留会得到"同名不同值"的摘要。crc32b/adler32 用 zlib 实现, 与 PHP hash() 输出逐字节一致。
_HASH_SUPPORTED_ALGS = ('md5', 'sha1', 'sha256', 'sha224', 'sha384', 'sha512', 'crc32b', 'adler32')


def _hash_file_alg(path, algorithm):
    """按算法分块计算文件摘要 (1MB/块), 避免大文件整包读入内存。"""
    if algorithm == 'crc32b':
        crc = 0
        with open(path, 'rb') as fh:
            for blk in iter(lambda: fh.read(1 << 20), b''):
                crc = zlib.crc32(blk, crc)
        return format(crc & 0xffffffff, '08x')
    if algorithm == 'adler32':
        ad = 1
        with open(path, 'rb') as fh:
            for blk in iter(lambda: fh.read(1 << 20), b''):
                ad = zlib.adler32(blk, ad)
        return format(ad & 0xffffffff, '08x')
    h = hashlib.new(algorithm)
    with open(path, 'rb') as fh:
        for blk in iter(lambda: fh.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()


def _hash_str_alg(text, algorithm):
    """按算法计算字符串摘要, 与 PHP hash($algorithm, $text) 逐字节一致."""
    blob = text.encode('utf-8', 'surrogateescape')
    if algorithm == 'crc32b':
        return format(zlib.crc32(blob) & 0xffffffff, '08x')
    if algorithm == 'adler32':
        return format(zlib.adler32(blob) & 0xffffffff, '08x')
    return hashlib.new(algorithm, blob).hexdigest()


def action_checksum(req, cfg):
    _t0 = time.time()
    rel = req.get('path', '')
    # 不跟随最后一级: checksum 一个软链接时返回链接本身 (type=symlink), 不返回目标文件摘要.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.islink(dest) and not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    algorithm = req.get('algorithm', 'md5')
    _supported_algs = _HASH_SUPPORTED_ALGS
    if not isinstance(algorithm, str):
        return fail(E['INVALID_PARAM'], 'bad_algorithm',
                    'algorithm 必须是字符串: ' + '|'.join(_supported_algs),
                    {'supported': list(_supported_algs),
                     'hint': 'algorithm 不能传 true/数字/数组/对象'})
    algorithm = algorithm.strip().lower()
    if algorithm not in _supported_algs:
        return fail(E['INVALID_PARAM'], 'bad_algorithm',
                    "不支持的 algorithm '%s' (支持: %s)" % (algorithm, '|'.join(_supported_algs)),
                    {'supported': list(_supported_algs)})
    pattern = req.get('pattern')
    if pattern is not None and not isinstance(pattern, str):
        return fail(E['INVALID_PARAM'], 'bad_pattern', 'pattern 必须是字符串 (如 "*.php")',
                    {'hint': 'pattern 用于按文件名过滤, 不能传 true/数字/数组/对象'})
    import fnmatch as _fnmatch
    base = os.path.realpath(cfg['WORK_DIR']).rstrip('/\\') + os.sep
    items = []
    total = 0
    skipped_special = 0
    skipped = []
    # 同 list: ai-sync/ 簿记目录默认不参与业务校验和 (两端内部文件数天然不同)
    _req_norm = (rel or '').replace('\\', '/').strip('/')
    _allow_internal = (_req_norm == 'ai-sync' or _req_norm.startswith('ai-sync/'))

    def emit_link(full, rel_p):
        nonlocal total
        lm = _link_meta(full, cfg['WORK_DIR'])
        try:
            lst = os.lstat(full)
            mtime = int(lst.st_mtime)
        except OSError:
            mtime = 0
        entry = {
            'path': rel_p,
            'size': 0,
            'mtime': mtime,
            'mode': '0777',
            'type': 'symlink',
            'safe': lm['safe'],
            'link_target': lm['target'] if lm['safe'] else None,
            'link_target_rel': lm['target_rel'] if lm['safe'] else None,
            'link_target_rel_b64': (base64.b64encode(lm['target_rel'].encode('utf-8', 'surrogateescape')).decode('ascii') if lm['safe'] else None),
            'broken': lm['broken'],
            'cycle': bool(lm.get('cycle')),
            'resolvable': bool(lm.get('resolvable')),
        }
        if lm['safe'] and lm['target'] is not None:
            entry['link_target_b64'] = base64.b64encode(
                lm['target'].encode('utf-8', 'surrogateescape')).decode('ascii')
        if not lm['safe']:
            entry['reason'] = lm['reason']
        entry[algorithm] = _hash_str_alg(lm['target_norm'], algorithm) \
            if lm['safe'] and lm.get('target_norm') is not None else ''
        entry.update(_path_b64_fields(rel_p))
        items.append(entry)
        total += 1

    if os.path.islink(dest):
        emit_link(dest, rel.replace('\\', '/').strip('/'))
        return ok({'algorithm': algorithm, 'items': items, 'total': total,
                   'skipped_large': 0,
                   'elapsed_ms': int((time.time() - _t0) * 1000)})

    if os.path.isdir(dest):
        for e in _walk_tree_sorted(dest, 1 << 62, True, _allow_internal):
            if e['type'] == 'dir':
                continue
            full = e['full']
            rel_p = full[len(base):].lstrip('/\\')
            rel_norm = rel_p.replace('\\', '/')
            if not _allow_internal and (rel_norm == 'ai-sync' or rel_norm.startswith('ai-sync/')):
                continue
            if pattern and not _fnmatch.fnmatch(e['name'], pattern):
                continue
            if e['type'] == 'symlink':
                emit_link(full, rel_p)
                continue
            sk = _special_kind(full)
            if sk is not None:
                # FIFO/socket/设备节点内容无限, 不能 hash (会阻塞); 显式计入 skipped.
                skipped_special += 1
                if len(skipped) < 100:
                    skipped.append({'path': rel_p, 'special_type': sk})
                continue
            try:
                st = os.lstat(full)
                h = _hash_file_alg(full, algorithm)
            except OSError:
                continue
            entry = {'path': rel_p, 'size': int(st.st_size), 'mtime': int(st.st_mtime),
                     'mode': '%04o' % (st.st_mode & 0o7777)}
            entry[algorithm] = h
            entry.update(_path_b64_fields(rel_p))
            items.append(entry)
            total += 1
    else:
        rel_p = rel.replace('\\', '/').strip('/')
        if not pattern or _fnmatch.fnmatch(os.path.basename(rel_p), pattern):
            sk = _special_kind(dest)
            if sk is not None:
                return fail(E['INVALID_PARAM'], 'not_regular_file',
                            "Cannot checksum special file '%s' (%s): only regular files are hashable" % (rel, sk),
                            {'path': rel, 'special_type': sk,
                             'hint': 'FIFO/socket/设备节点没有有限内容; checksum 只适用于普通文件和软链接。'})
            try:
                st = os.lstat(dest)
                h = _hash_file_alg(dest, algorithm)
            except OSError as e:
                return fail(E['INTERNAL'], 'checksum_fail', str(e))
            entry = {'path': rel_p, 'size': int(st.st_size), 'mtime': int(st.st_mtime),
                     'mode': '%04o' % (st.st_mode & 0o7777)}
            entry[algorithm] = h
            entry.update(_path_b64_fields(rel_p))
            items.append(entry)
            total += 1
    return ok({'algorithm': algorithm, 'items': items, 'total': total,
               'skipped_special': skipped_special, 'skipped_large': 0, 'skipped': skipped,
               'elapsed_ms': int((time.time() - _t0) * 1000)})


def _find_php_cli():
    for c in ['/usr/bin/php', '/usr/local/bin/php', '/usr/sbin/php', '/opt/php/bin/php',
              '/www/server/php/74/bin/php', 'php']:
        if c == 'php':
            import shutil as _s
            p = _s.which('php')
            if p and os.path.isfile(p) and 'fpm' not in p:
                return p
            continue
        if os.path.isfile(c) and os.access(c, os.X_OK) and 'fpm' not in c:
            return c
    return ''


def action_php_run(req, cfg):
    """
    安全 PHP 执行: 仅允许工作目录内 .php 文件, 强制超时, 禁用危险函数.
    用于 AI 在服务器端验证 PHP 代码 (替代危险的通用 exec).
    """
    if not cfg.get('EXEC_ENABLED'):
        audit_log('php_run', req, E['FORBIDDEN'], {'reason': 'exec_disabled'})
        return fail(E['FORBIDDEN'], 'exec_disabled', 'php_run is disabled (EXEC_ENABLED=false)')
    if cfg.get('EXEC_ALLOW_IPS'):
        ip = _ctx_ip()
        allowed = any(ip == rule or ('/' in rule and ip_in_cidr(ip, rule)) for rule in cfg['EXEC_ALLOW_IPS'])
        if not allowed:
            return fail(E['FORBIDDEN'], 'ip_blocked', "IP %s not in EXEC_ALLOW_IPS" % ip)
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "File '%s' not found" % rel)
    if not os.path.isfile(dest):
        return fail(E['INVALID_PARAM'], 'not_file', 'Target must be a file')
    if os.path.splitext(dest)[1].lower() != '.php':
        return fail(E['INVALID_PARAM'], 'not_php', 'Only .php files can be executed')
    php = _find_php_cli()
    if not php:
        return fail(E['INTERNAL'], 'no_php_cli', 'Cannot find PHP CLI binary')
    # 语法检查
    pre = subprocess.run([php, '-l', dest], capture_output=True, text=True, timeout=30)
    if pre.returncode != 0:
        return fail(E['INVALID_PARAM'], 'syntax_error', (pre.stdout + pre.stderr).strip())
    timeout = max(1, min(_php_intval(req.get('timeout', 15)), int(cfg.get('EXEC_TIMEOUT_MAX', 30))))
    work_dir = cfg['WORK_DIR'].rstrip('/\\')
    disable = ('exec,passthru,shell_exec,system,proc_open,popen,curl_exec,curl_multi_exec,putenv,mail,'
               'ini_restore,ini_alter,dl,pcntl_exec,pcntl_fork,pcntl_signal,socket_create,socket_connect,'
               'stream_socket_client,set_time_limit,symlink,link,readlink,phpinfo,posix_kill,posix_getpwuid,'
               'posix_getgrgid,posix_uname,getmypid,getmyuid,getmygid,get_current_user,php_uname,'
               'php_ini_loaded_file,unlink,rmdir,chmod,chown,chgrp,move_uploaded_file,ini_set,error_log')
    args = ''
    _args = req.get('args')
    if not isinstance(_args, list):
        _args = []   # 对齐 PHP: 非数组 args 静默忽略, 不把字符串按字符拆成多个参数
    for a in _args:
        args += ' ' + "'" + str(a).replace("'", "'\\''") + "'"
    stdin_data = b''
    if 'stdin_b64' in req:
        try:
            d = base64.b64decode(req['stdin_b64'])
            if len(d) < 1024 * 1024:
                stdin_data = d
        except Exception:
            pass
    cmd = "%s -d open_basedir=%s -d disable_functions=%s -d max_execution_time=%d -d memory_limit=128M -d allow_url_fopen=0 -d allow_url_include=0 %s%s" % (
        php, work_dir + os.pathsep + tempfile.gettempdir(), disable, timeout, dest, args)
    t_start = time.time()
    try:
        proc = subprocess.Popen(cmd, shell=True, cwd=work_dir, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': os.environ.get('LANG', 'C')})
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout + 5)
        if len(stdout) + len(stderr) > 1024 * 1024:
            stdout = stdout[:1024 * 1024]
            stderr = stderr[:1024 * 1024]
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        rc = 124
    except Exception as e:
        return fail(E['INTERNAL'], 'proc_open_fail', str(e))
    duration_ms = int(round((time.time() - t_start) * 1000))
    timed_out = (rc == 124)
    so = stdout.decode('utf-8', 'replace')
    se = stderr.decode('utf-8', 'replace')
    max_out = 256 * 1024
    if len(so.encode('utf-8')) > max_out:
        so = so[:max_out] + "\n...[truncated]"
    if len(se.encode('utf-8')) > max_out:
        se = se[:max_out] + "\n...[truncated]"
    exit_reason = 'normal'
    if timed_out:
        exit_reason = 'timeout'
    elif rc == 139:
        exit_reason = 'segfault'
    elif rc == 137:
        exit_reason = 'killed'
    elif rc > 128:
        exit_reason = 'signal_%d' % (rc - 128)
    elif rc != 0:
        exit_reason = 'error'
    auto_lint = None
    runtime_error = None
    if rc != 0 and not timed_out:
        try:
            lo = subprocess.run([php, '-l', dest], capture_output=True, text=True, timeout=30)
            lint_msg = (lo.stdout + lo.stderr).strip().replace(work_dir + '/', '')
            auto_lint = {'syntax_ok': lo.returncode == 0, 'message': lint_msg}
            m = re.search(r'(?:on line|第\s*(\d+)\s*行|line\s+(\d+))', lint_msg, re.I)
            if lo.returncode != 0 and m:
                line = int(m.group(1) or m.group(2) or 0)
                auto_lint['error_line'] = line
                try:
                    lines = open(dest, encoding='utf-8', errors='replace').read().splitlines()
                    if 1 <= line <= len(lines):
                        auto_lint['error_line_content'] = lines[line - 1]
                except OSError:
                    pass
        except Exception:
            auto_lint = {'syntax_ok': True, 'message': ''}
        if 'error_line' not in (auto_lint or {}):
            rm = re.search(r'(?:in\s+\S+(?::(\d+))|on line (\d+))', se, re.I)
            if rm:
                el = int(rm.group(1) or rm.group(2) or 0)
                if el > 0:
                    runtime_error = {'line': el}
                    try:
                        lines = open(dest, encoding='utf-8', errors='replace').read().splitlines()
                        if 1 <= el <= len(lines):
                            runtime_error['line_content'] = lines[el - 1]
                            cb = [{'line': i + 1, 'text': lines[i]} for i in range(max(0, el - 4), el - 1)]
                            ca = [{'line': i + 1, 'text': lines[i]} for i in range(el, min(len(lines), el + 2))]
                            if cb:
                                runtime_error['context_before'] = cb
                            if ca:
                                runtime_error['context_after'] = ca
                    except OSError:
                        pass
    crashed = (not timed_out) and rc != 0 and (
        bool(runtime_error) or bool(re.search(r'(Fatal error|Parse error|Uncaught|segfault)', se, re.I)))
    try:
        v = subprocess.run([php, '-v'], capture_output=True, text=True, timeout=10)
        vm = re.search(r'PHP\s+(\d+\.\d+\.\d+)', v.stdout + v.stderr)
        php_version = vm.group(1) if vm else ''
    except Exception:
        php_version = ''
    return ok({
        'return_code': rc, 'stdout': so, 'stderr': se,
        'php_cli': os.path.basename(php), 'php_version': php_version,
        'auto_lint': auto_lint, 'runtime_error': runtime_error,
        'duration_ms': duration_ms, 'timed_out': timed_out,
        'exit_reason': exit_reason, 'crashed': crashed,
    })


# ==================== 备份类 action ====================
def action_backup_ls(req, cfg):
    # FIX-v4.33 (P0): 备份列表此前无分页无上限, 实测一次返回 2004 条 / 179KB ——
    #   AI 一句 --backup-list 就吃掉几万 token, 而它真正想要的往往只是"某文件最近几个备份"。
    #   现在默认返回最近 50 条并明确告知 total/truncated, AI 可用 path 收窄或 limit 翻页。
    limit = _parse_limit(req.get('limit'), default=50, maximum=500)
    if isinstance(limit, tuple):   # (错误响应,)
        return limit[0]
    rel = req.get('path')
    if rel is None:
        rel = ''   # null/None 视为缺省 (列出全部备份), 与 PHP ?? 一致; 其它非字符串交给 safe_path 判 1004
    if rel in ('', '.'):
        backup_root = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['BACKUP_DIR']).rstrip('/')
        if not os.path.isdir(backup_root):
            return ok({'backups': [], 'total': 0})
        items = []
        for root, dirs, files in os.walk(backup_root):
            for fn in files:
                if '.bak.' not in fn:
                    continue
                full = os.path.join(root, fn)
                if os.path.islink(full):
                    continue
                rel_in = full[len(backup_root) + 1:]
                orig = os.path.dirname(rel_in)
                if orig == '.':
                    orig = _utf8_basename(rel_in)
                items.append({'file': fn, 'path': orig, 'size': os.path.getsize(full), 'mtime': int(os.path.getmtime(full))})
        items.sort(key=lambda x: x['mtime'], reverse=True)
        return _backup_ls_result(items, limit, hint_when_truncated=
                                 '备份总数较多, 已按时间倒序返回最近的若干条。'
                                 '要找某个文件的备份请用 path=<文件路径>; 要更多请 limit=<N>(上限500)')
    # 只解析父目录: 链接的备份挂在链接名下, 不是目标名下.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    if not dest:
        return reject_path(rel, 'backup_ls', req)
    scope_rel = _backup_scope_rel(dest, cfg['WORK_DIR'])
    backup_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['BACKUP_DIR'],
                              _utf8_dirname(scope_rel), _utf8_basename(scope_rel))
    if not os.path.isdir(backup_dir):
        return ok({'backups': [], 'total': 0})
    items = []
    for f in sorted(glob.glob(os.path.join(backup_dir, _utf8_basename(scope_rel) + '.bak.*')), key=os.path.getmtime, reverse=True):
        items.append({'file': _utf8_basename(f), 'path': scope_rel, 'size': os.path.getsize(f),
                      'mtime': int(os.path.getmtime(f)), 'type': _backup_entry_type(f)})
    return _backup_ls_result(items, limit, hint_when_truncated='该文件备份较多, 已返回最近若干条; 要更多请 limit=<N>')


def _parse_limit(v, default=50, maximum=500):
    """统一的 limit 解析: None/空 → default; 非整数或 <1 → (fail响应,); 超上限 → 钳制。"""
    if v is None or v == '':
        return default
    # 与 PHP _parse_limit 对齐: PHP is_numeric(true/[])=false → 按 0 报 bad_limit;
    # 旧实现 int(True)=1 → 布尔 true 被当成 limit=1 悄悄接受, 双端行为相反。
    if isinstance(v, bool):
        n = 0
    elif isinstance(v, (int, float)):
        n = int(v)
    elif isinstance(v, str) and _php_is_numeric(v):
        n = int(float(v))
    else:
        n = 0
    if n < 1:
        return (fail(E['INVALID_PARAM'], 'bad_limit',
                     "limit 必须 >= 1 (收到 %d)。注意: 参数按弱类型规则转换, 非数字字符串会变成 0"
                     " (如 \"abc\"→0), 请传正整数" % n,
                     {'hint': 'limit 用法: limit=20 (上限 500); 不传默认返回最近 %d 条; '
                              '要找某个文件的备份用 path=<文件路径> 更省 token' % default}),)
    return min(n, maximum)


def _backup_ls_result(items, limit, hint_when_truncated=''):
    """组装备份列表响应: 恒返回真实 total, 截断时明确告知 AI 如何继续。"""
    total = len(items)
    shown = items[:limit]
    res = {'backups': shown, 'total': total, 'returned': len(shown),
           'truncated': total > len(shown)}
    if total > len(shown):
        res['tip'] = hint_when_truncated
    return ok(res)


def action_backup_rb(req, cfg):
    rel = req.get('path', '')
    backup_file = req.get('backup_file', '')
    # 回滚目标不跟随最后一级: 恢复链接时替换链接本身, 不写到目标文件.
    dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
    if not dest:
        return reject_path(rel, 'backup_rb', req)
    if not _backup_validate_filename(backup_file):
        audit_log('backup_rb', {**req, '_blocked': 'bad_backup_file'}, E['INVALID_PARAM'])
        return fail(E['INVALID_PARAM'], 'bad_backup_file',
                    "backup_file 格式非法 (需 {name}.bak.{YYYYMMDD_HHMMss}_{6hex})",
                    {'hint': '用 backup_ls 查询该文件可用的备份列表'})
    backup_file = _utf8_basename(backup_file)
    scope_rel = _backup_scope_rel(dest, cfg['WORK_DIR'])
    backup_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['BACKUP_DIR'],
                              _utf8_dirname(scope_rel), _utf8_basename(scope_rel))
    backup_path = os.path.join(backup_dir, backup_file)
    real_dir = os.path.realpath(backup_dir)
    real_path = os.path.realpath(backup_path)
    if (real_dir is False or real_path is False or not (real_path + os.sep).startswith(real_dir + os.sep)
            or os.path.islink(backup_path) or not os.path.isfile(real_path)):
        audit_log('backup_rb', req, E['BACKUP_NOT_FOUND'], {'backup_file': backup_file})
        return fail(E['BACKUP_NOT_FOUND'], 'backup_not_found', "Backup '%s' not found" % backup_file,
                    {'hint': '用 backup_ls path=%s 查询该文件可用的备份列表' % rel})
    restored_type = _restore_backup_to_dest(real_path, dest, cfg)
    if restored_type is None:
        return fail(E['INTERNAL'], 'restore_fail', 'Restore failed',
                    {'hint': '检查目录权限/磁盘空间; 用 backup_ls 确认备份存在'})
    audit_log('backup_rb', req, 0, {'path': scope_rel, 'restored_from': backup_file,
                                     'type': restored_type})
    return ok({'path': scope_rel, 'restored_from': backup_file, 'type': restored_type})


# ==================== exec ====================
_EXEC_CAP = {}


def _exec_sh_bin():
    """sh 的真实路径: Android/BusyBox 上 /bin/sh 不一定存在, PATH 里也未必有。
    Windows 上优先用 PATH 里的 sh(Git 自带 MSYS2 sh); 找不到时回退常见 Git 安装位置,
    使 Windows 也能跑通 POSIX exec 协议(无需用户手动配置 PATH)。"""
    for p in ('/bin/sh', '/system/bin/sh', '/usr/bin/sh'):
        if os.access(p, os.X_OK):
            return p
    s = shutil.which('sh')
    if s:
        return s
    if os.name == 'nt':
        # Git for Windows / 便携 Git 常见位置
        candidates = []
        gi = os.environ.get('GIT_INSTALL_ROOT')
        if gi:
            candidates.append(gi)
        candidates += [r'D:\WorkBuddyAI\resources\vendor\PortableGit',
                       r'C:\Program Files\Git', r'C:\Program Files (x86)\Git',
                       os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Git'),
                       os.path.join(os.environ.get('USERPROFILE', ''), 'scoop', 'apps', 'git', 'current')]
        for c in candidates:
            if not c:
                continue
            for sub in ('usr\\bin\\sh.exe', 'bin\\sh.exe', 'usr\\bin\\bash.exe'):
                cand = os.path.join(c, sub)
                if os.path.exists(cand):
                    return cand
    return 'sh'


def _exec_timeout_prefix(timeout):
    """按 timeout 实现能力降级: GNU(-k) → POSIX(-s TERM) → 最简 → 无。
    BusyBox/ToyBox/macOS 不认 -k, 旧硬编码会让整条 exec 直接 rc=1 且命令根本没跑。"""
    timeout = int(timeout)
    if timeout <= 0:
        return '', False
    if 'cap' not in _EXEC_CAP:
        cap = ''
        bin_t = shutil.which('timeout')
        if bin_t:
            try:
                out = subprocess.run([bin_t, '--version'], capture_output=True, text=True, timeout=5)
                if 'GNU' in (out.stdout or '') + (out.stderr or ''):
                    cap = 'gnu'
            except Exception:
                cap = ''
            if not cap:
                try:
                    if subprocess.run([bin_t, '-s', 'TERM', '1', 'true'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=5).returncode == 0:
                        cap = 'posix'
                except Exception:
                    cap = ''
            if not cap:
                try:
                    if subprocess.run([bin_t, '1', 'true'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=5).returncode == 0:
                        cap = 'plain'
                except Exception:
                    cap = ''
        _EXEC_CAP['cap'] = cap
    cap = _EXEC_CAP['cap']
    if cap == 'gnu':
        return 'timeout -k 5 -s TERM %d' % timeout, True
    if cap == 'posix':
        return 'timeout -s TERM %d' % timeout, True
    if cap == 'plain':
        return 'timeout %d' % timeout, True
    return '', False


# ============ 跨平台 exec/shell 兼容层 ============
# Windows 与 POSIX 在「进程/信号/shell」三处不兼容:
#   1) subprocess shell=True 在 Windows 上调用 cmd.exe, 而 exec/shell 协议是 POSIX 语法
#      (; $? 2>/dev/null sh -s 管道), cmd.exe 无法解释 -> 直接失败("拒绝访问"/"系统找不到指定的路径");
#   2) os.killpg / os.getpgid 在 Windows 不存在; 3) signal.SIGKILL 在 Windows 未定义。
# 以下封装统一抹平, 使同一套 POSIX 协议在 Windows(Git 自带 MSYS2 sh)与 Linux 都能跑。
_SIGTERM = getattr(signal, 'SIGTERM', 15)
_SIGKILL = getattr(signal, 'SIGKILL', 9)


def _posix_shell_spawn(cmd, **kwargs):
    """以 POSIX shell 解释 cmd 并启动子进程(对应旧 subprocess.Popen(cmd, shell=True))。
    Windows 显式用 _exec_sh_bin() 作解释器(shell=False); POSIX 上 _exec_sh_bin() 即 /bin/sh, 等价。"""
    if os.name == 'nt':
        sh = _exec_sh_bin()
        return subprocess.Popen([sh, '-c', cmd], shell=False, **kwargs)
    return subprocess.Popen(cmd, shell=True, **kwargs)


def _posix_shell_run(cmd, **kwargs):
    """对应旧 subprocess.run(cmd, shell=True), 跨平台以 POSIX shell 解释。"""
    if os.name == 'nt':
        sh = _exec_sh_bin()
        return subprocess.run([sh, '-c', cmd], shell=False, **kwargs)
    return subprocess.run(cmd, shell=True, **kwargs)


def _get_pgid(pid):
    """POSIX 返回进程组 id; Windows 无进程组概念, 直接返回 pid。"""
    if os.name == 'nt':
        return pid
    try:
        return os.getpgid(pid)
    except Exception:
        return pid


def _exec_kill_tree(pid, sig):
    """跨平台整树强杀: POSIX 用 killpg; Windows 用 taskkill /T /F(进程组概念不存在)。"""
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            try:
                os.kill(pid, _SIGTERM if sig is None else sig)
            except Exception:
                pass
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except Exception:
        pass
    try:
        os.kill(pid, sig)
    except Exception:
        pass


def action_exec(req, cfg):
    if not cfg.get('EXEC_ENABLED'):
        return fail(E['EXEC_DISABLED'], 'exec_disabled', 'Remote exec is disabled')
    ip = _ctx_ip()
    open_warning = ''
    if not cfg.get('EXEC_ALLOW_IPS'):
        audit_log('exec', req, 0, {'reason': 'open_exec_no_allowlist', 'ip': ip})
    else:
        allowed = any(ip == rule or ('/' in rule and ip_in_cidr(ip, rule)) for rule in cfg['EXEC_ALLOW_IPS'])
        if not allowed:
            return fail(E['FORBIDDEN'], 'ip_blocked', "IP %s not in EXEC_ALLOW_IPS" % ip)
    cmd = req.get('cmd', '')
    # 类型/内容校验必须在拼接命令前: 旧实现把 123 当 "123" 执行 (rc=127),
    # 把数组/对象写进 stdin 时直接 TypeError → 1099; NUL 更危险 —— shell 会静默截断,
    # "echo A\0echo B" 实际只执行 "echo Aecho B", 调用方却以为执行了完整命令。
    if not isinstance(cmd, str):
        return fail(E['INVALID_PARAM'], 'cmd_must_be_string',
                    'cmd 必须是字符串, 收到 %s' % _php_type_name(cmd),
                    {'hint': '例: {"action":"exec","cmd":"ls -la"}'})
    if '\0' in cmd:
        return fail(E['INVALID_PARAM'], 'cmd_contains_null',
                    'cmd 不能包含 NUL 字节(\\0): shell 会静默截断其后的内容, 请改用换行分隔多条命令')
    # 不能用 `not cmd`: 与 PHP empty('0') 一样会误拒命令 "0"
    if cmd == '':
        return fail(E['INVALID_PARAM'], 'empty_cmd', 'cmd is required')
    # 严格校验 timeout: 0/负数会被 GNU timeout 解释为"永不超时"; "10s"/true 之类
    # 旧实现分别被 int() 静默解析或 TypeError 兜成 1099, 都不可操作。
    t_raw = req.get('timeout', 30)
    if isinstance(t_raw, bool) or not _php_is_numeric(t_raw):
        return fail(E['INVALID_PARAM'], 'invalid_timeout', 'timeout must be a positive number of seconds')
    try:
        _tf = float(t_raw)
    except (TypeError, ValueError):
        _tf = 0.0
    if _tf != _tf or _tf in (float('inf'), float('-inf')) or int(_tf) <= 0:
        return fail(E['INVALID_PARAM'], 'invalid_timeout', 'timeout must be a positive number of seconds')
    timeout = int(_tf)
    timeout = min(timeout, int(cfg.get('EXEC_TIMEOUT_MAX', 120)))
    # cwd 支持 (help 早已声明, 但旧实现直接忽略 → AI 传 cwd 后命令仍在 WORK_DIR 执行).
    # 相对路径以 WORK_DIR 为基准, 绝对路径直接用; 必须真实存在且是目录。
    exec_cwd = cfg['WORK_DIR'].rstrip('/\\')
    cwd_req = req.get('cwd')
    if cwd_req is not None and cwd_req != '':
        if not isinstance(cwd_req, str) or '\0' in cwd_req:
            return fail(E['INVALID_PARAM'], 'cwd_not_found',
                        'cwd 必须是字符串且不能含 NUL 字节',
                        {'hint': 'cwd 指向一个已存在的目录; 相对路径以 WORK_DIR 为基准。'})
        cwd_abs = cwd_req if cwd_req.startswith('/') else exec_cwd + '/' + cwd_req
        cwd_real = os.path.realpath(cwd_abs)
        if not os.path.isdir(cwd_real):
            return fail(E['INVALID_PARAM'], 'cwd_not_found',
                        "cwd '%s' 不存在或不是目录" % cwd_req,
                        {'hint': '相对路径以 WORK_DIR 为基准; 请先创建目录或改用已存在的路径。'})
        exec_cwd = cwd_real
    max_output = int(cfg.get('EXEC_MAX_OUTPUT', 8 * 1024 * 1024))
    if max_output < 65536:
        max_output = 65536
    rc_file = tempfile.NamedTemporaryFile(prefix='sync_exec_rc_', delete=False)
    rc_file.close()
    rc_path = rc_file.name
    # 可移植: timeout 能力探测 + sh 真实路径; 没有 timeout 时由 Python 侧兜底强杀
    to_prefix, timeout_enforced = _exec_timeout_prefix(timeout)
    wrapped = ((to_prefix + ' ') if to_prefix else '') + sh_quote(_exec_sh_bin()) + ' -s' \
              + ' ; echo $? > ' + sh_quote(rc_path) + ' 2>/dev/null'
    env = {'PATH': _exec_safe_path(),
           'LANG': os.environ.get('LANG', 'C.UTF-8'), 'HOME': os.environ.get('HOME', '/tmp')}
    t0 = time.time()
    try:
        # start_new_session: 超时时可整组强杀, 否则只杀 sh, 子孙进程继续跑并占着输出管道
        proc = _posix_shell_spawn(wrapped, cwd=exec_cwd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                start_new_session=True)
    except Exception as e:
        return fail(E['INTERNAL'], 'proc_open_fail', str(e))
    stdout, stderr = b'', b''
    truncated = False
    # 有 timeout 时给它 6s 收尾; 没有 timeout 只能自己卡在 timeout+1s
    end_ts = t0 + timeout + (6 if timeout_enforced else 1)
    timed_out = False
    hit_deadline = False
    try:
        proc.stdin.write(cmd.encode('utf-8', 'replace'))
    except Exception:
        pass
    finally:
        # 必须关闭 stdin: 否则 `sh -s` 会一直等待输入, 挂到超时才返回
        try:
            proc.stdin.close()
        except Exception:
            pass
    # 跨平台读取: POSIX 用 select+非阻塞; Windows 用线程读(select 不支持管道)
    if _fcntl is not None:
        import select
        for s in (proc.stdout, proc.stderr):
            _set_nonblocking(s)
        while time.time() < end_ts:
            # 进程已结束: 立即 drain 并退出 (避免 EOF 管道持续被判为"可读"导致死等 end_ts)
            if proc.poll() is not None:
                try:
                    so, se = proc.communicate(timeout=2)
                    stdout += so or b''
                    stderr += se or b''
                except Exception:
                    pass
                break
            try:
                rlist, _, _ = select.select([proc.stdout, proc.stderr], [], [], 0.2)
            except (ValueError, OSError):
                break
            if not rlist:
                continue
            for s in rlist:
                try:
                    chunk = s.read(65536)
                except (BlockingIOError, OSError):
                    chunk = b''
                if not chunk:
                    continue
                if s is proc.stdout:
                    stdout += chunk
                else:
                    stderr += chunk
                if len(stdout) + len(stderr) >= max_output:
                    truncated = True
                    break
            if truncated:
                break
    else:
        _so, _se, truncated = _drain_pipes_threaded(proc, end_ts, max_output)
        stdout += _so
        stderr += _se
    # 截止时间兜底: 先 TERM 进程组, 再 KILL; 记录 hit_deadline 以便如实报 124
    if proc.poll() is None:
        hit_deadline = True
        _exec_kill_tree(proc.pid, _SIGTERM)
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        if proc.poll() is None:
            _exec_kill_tree(proc.pid, _SIGKILL)
        timed_out = True
    try:
        so, se = proc.communicate(timeout=5)
        stdout += so or b''
        stderr += se or b''
    except Exception:
        pass
    rc = proc.returncode
    # 从 rc 文件读取真实退出码 (最可靠)
    try:
        with open(rc_path) as f:
            content = f.read().strip()
        if content != '':
            fr = int(content)
            if fr >= 0:
                rc = fr
    except Exception:
        pass
    if hit_deadline:
        rc = 124
        timed_out = True
    try: os.remove(rc_path)
    except OSError: pass
    if rc == 124:
        timed_out = True
    audit_log('exec', req, (0 if (rc == 0 and not timed_out) else E['INTERNAL']), {
        'cmd_preview': cmd[:300], 'cmd_len': len(cmd), 'timeout': timeout,
        'timed_out': 1 if timed_out else 0, 'output_truncated': 1 if truncated else 0,
        'stdout_len': len(stdout), 'stderr_len': len(stderr), 'return_code': rc,
        'elapsed_ms': int((time.time() - t0) * 1000),
        'stdout_preview': stdout[:500].decode('utf-8', 'replace'),
        'stderr_preview': stderr[:500].decode('utf-8', 'replace'),
    })
    return ok({'return_code': rc, 'stdout': stdout.decode('utf-8', 'replace'),
               'stderr': stderr.decode('utf-8', 'replace'), 'timeout': timeout,
               'timed_out': timed_out, 'timeout_enforced': timeout_enforced,
               'output_truncated': truncated,
               'stdout_len': len(stdout), 'stderr_len': len(stderr),
               'elapsed_ms': int((time.time() - t0) * 1000), 'cwd': exec_cwd,
               'tip': ('输出超过上限已截断, 如需完整输出请重定向到文件后 download' if truncated else None),
               'warning': (open_warning or None)})


def sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


# ==================== dev_tools ====================
def action_search(req, cfg):
    _t0 = time.time()
    rel = req.get('path')
    if rel is None or rel == '':
        rel = '.'   # 与 PHP 一致: path 可选, 默认根目录
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    pattern = req.get('pattern', '')
    if not isinstance(pattern, str):
        return fail(E['INVALID_PARAM'], 'bad_pattern', 'pattern 必须是字符串 (关键词或正则)',
                    {'hint': 'pattern 不能传 true/数字/数组/对象; 例: {"pattern": "TODO"}'})
    if pattern == '':
        return fail(E['INVALID_PARAM'], 'empty_pattern', 'pattern is required')
    is_regex = _bool_flag(req.get('regex'))
    if is_regex is None:
        return bad_bool('regex', req.get('regex'))
    case_insensitive = _bool_flag(req.get('case_insensitive'))
    if case_insensitive is None:
        return bad_bool('case_insensitive', req.get('case_insensitive'))
    whole_word = _bool_flag(req.get('whole_word'))
    if whole_word is None:
        return bad_bool('whole_word', req.get('whole_word'))
    file_pattern = req.get('file_pattern')
    if file_pattern is not None and not isinstance(file_pattern, str):
        return fail(E['INVALID_PARAM'], 'bad_file_pattern', 'file_pattern 必须是字符串 (如 "*.php")',
                    {'hint': '多个模式用空格或逗号分隔; 不能传 true/数字/数组/对象'})
    context_before = int(req.get('context_before') or req.get('context') or 0)
    context_after = int(req.get('context_after') or 0)
    max_results = int(req.get('max_results', 200))
    max_results = min(max_results, 5000)
    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE
    try:
        if is_regex:
            cre = re.compile(pattern, flags)
        elif whole_word:
            cre = re.compile(r'\b' + re.escape(pattern) + r'\b', flags)
        else:
            cre = re.compile(re.escape(pattern), flags)
    except re.error as e:
        return fail(E['INVALID_PARAM'], 'bad_regex', str(e))
    base = cfg['WORK_DIR'].rstrip('/\\') + os.sep
    files_scanned = 0
    total_matches = 0
    hits = []
    # 先收集 + 全局路径排序, 再顺序搜索。
    # 原因: max_results 截断时, 结果集取决于遍历顺序。旧实现 os.walk 的目录序与 PHP
    # RecursiveDirectoryIterator 不同, 会让"命中被截断在哪批文件"两端不一致
    # (实测: 同一个 pattern, PHP 前 200 条全落在 .sync_oplog 日志, PY 落在业务文件)。
    _root_rel = (rel or '').strip('/\\')
    _allow_internal = _root_rel not in ('', '.')
    _targets = []
    if os.path.isdir(dest):
        for _root, _dirs, _files in os.walk(dest):
            _dirs[:] = sorted(d for d in _dirs if d not in ('.', '..'))
            if not _allow_internal:
                # 服务端簿记目录不参与业务搜索 (否则日志会挤占 max_results 配额)
                _dirs[:] = [d for d in _dirs if not _is_internal_dir(d)]
            for fn in _files:
                if not _allow_internal and _is_internal_dir(fn):
                    # v4.28: 根级簿记"文件"同样剪枝 (.sync_oplog.jsonl / .sync.log 等),
                    # 旧实现只剪目录 → 簿记日志挤占 max_results 配额 (生产实测 11/200 条)
                    continue
                if file_pattern:
                    # 支持空格/逗号分隔多模式 (如 "*.php *.py *.js"), 任一匹配即通过 (对齐 PHP)
                    _fp_ok = False
                    for _fp in re.split(r'[\s,]+', str(file_pattern).strip()):
                        if _fp and fnmatch.fnmatch(fn, _fp):
                            _fp_ok = True
                            break
                    if not _fp_ok:
                        continue
                full = os.path.join(_root, fn)
                if not full.startswith(base):
                    continue
                _targets.append(full)
        _targets.sort()
    else:
        _targets.append(dest)
    for full in _targets:
        sk = _special_kind(full)
        if sk is not None:
            continue          # FIFO/socket/设备节点不能当文本读取 (会阻塞)
        try:
            st = os.stat(full)
        except OSError:
            continue
        files_scanned += 1
        if st.st_size > 4 * 1024 * 1024:
            continue          # 与 PHP 一致: 跳过超大文件, 避免整文件进内存
        try:
            with open(full, 'rb') as _bf:
                if b'\0' in _bf.read(8192):
                    continue  # AI 优化 v4.28: 二进制文件(NUL 头探测)不进文本搜索, 乱码行纯烧调用方 token
        except OSError:
            continue
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        # 逐行 search (与 PHP preg_match 一致): 每行最多 1 条命中。
        # 旧实现 finditer(整文) 会把同一行的多次出现重复计数, 与 PHP 的行级语义不一致。
        for _li, line_text in enumerate(lines):
            if not cre.search(line_text):
                continue
            hit = {'path': full[len(base):], 'line': _li + 1, 'match': line_text[:500]}
            if context_before or context_after:
                ctx = []
                # 先夹到文件行范围再迭代: 旧实现 range(_li - 1e12, ...) 会逐亿次空转
                # (context_before=1e12 时单请求挂死数分钟), PHP 端天然只遍历文件内行.
                _lo = max(0, _li - context_before)
                _hi = min(len(lines) - 1, _li + context_after)
                for ci in range(_lo, _hi + 1):
                    if ci != _li:
                        ctx.append({'line': ci + 1, 'text': lines[ci], 'kind': 'before' if ci < _li else 'after'})
                hit['context'] = ctx
            hits.append(hit)
            total_matches += 1
            if total_matches >= max_results:
                break
        # 每个文件处理完立即检查; total_matches > 0 保证 max_results<=0 时也只在命中后截断
        # (与 PHP 的 break 2 语义一致: 无命中不会提前结束扫描)
        if total_matches > 0 and total_matches >= max_results:
            break
    _elapsed = int((time.time() - _t0) * 1000)
    _res = {
        'path': rel,
        'pattern': pattern, 'regex': is_regex, 'case_insensitive': case_insensitive,
        'file_pattern': file_pattern, 'files_scanned': files_scanned,
        'total_matches': total_matches, 'truncated': total_matches >= max_results, 'hits': hits,
        'elapsed_ms': _elapsed,
    }
    # AI 易用性 v4.29: 大扫描成本自暴露 — 让调用方无需猜测就学会收窄搜索
    if files_scanned >= 300 and not file_pattern:
        _res['tip'] = ('扫描 %d 个文件耗时 %dms; 加 file_pattern(如 *.php) '
                       '或缩小 path 到子目录通常可提速 3 倍以上' % (files_scanned, _elapsed))
    return ok(_res)


def action_todo(req, cfg):
    rel = req.get('path')
    if rel is None or rel == '':
        rel = '.'   # 与 PHP 一致: path 可选, 默认根目录
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.isdir(dest):
        # 对齐 PHP: todo 只扫描目录, path 指向文件/不存在时明确报 not_dir
        return fail(E['INVALID_PARAM'], 'not_dir', 'Path must be a directory')
    default_tags = ['TODO', 'FIXME', 'XXX', 'HACK', 'NOTE', 'BUG', 'OPTIMIZE', 'REVIEW']
    tags = req.get('tags')
    if tags is None:
        tags = default_tags
    if isinstance(tags, dict):
        tags = list(tags.values())  # 对齐 PHP: JSON 对象即数组
    if not isinstance(tags, list) or not tags:
        return fail(E['INVALID_PARAM'], 'bad_tags',
                    'tags 必须是字符串数组 (如 ["TODO","FIXME"]), 且不能为空')
    for _t in tags:
        if not isinstance(_t, str) or _t == '':
            return fail(E['INVALID_PARAM'], 'bad_tags', 'tags 的每一项都必须是非空字符串')
    file_pattern = req.get('file_pattern')
    code_exts = ['php', 'js', 'ts', 'jsx', 'tsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h',
                 'css', 'html', 'vue', 'svelte', 'rb', 'swift', 'kt']
    base = cfg['WORK_DIR'].rstrip('/\\') + os.sep
    max_results = req.get('max_results', 500)
    if max_results is None:
        max_results = 500
    max_results = int(max_results)
    if max_results < 1:
        return fail(E['INVALID_PARAM'], 'bad_limit', 'max_results 必须 >= 1 (收到 %d)' % max_results)
    items = []
    by_tag = {}
    files_scanned = 0
    _done = False
    tag_pat = re.compile(r'(?<![\w/@])(' + '|'.join(re.escape(t) for t in tags) + r')(?:[\s(:]?\s*([^\n\r]*))?', re.I)
    for root, dirs, files in os.walk(dest):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fn in files:
            if _done:
                break
            fext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
            if file_pattern:
                if not fnmatch.fnmatch(fn, file_pattern):
                    continue
            else:
                if fext not in code_exts:
                    continue
            full = os.path.join(root, fn)
            if not full.startswith(base):
                continue
            if _special_kind(full) is not None:
                continue      # FIFO/socket/设备节点不能 readlines (会阻塞)
            try:
                with open(full, 'r', encoding='utf-8', errors='replace') as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            files_scanned += 1
            for i, line in enumerate(lines, 1):
                m = tag_pat.search(line)
                if m:
                    tag = m.group(1).upper()
                    note = (m.group(2) or '').strip()
                    items.append({'path': full[len(base):], 'line': i, 'tag': tag,
                                  'note': note, 'text': line.strip()[:300]})
                    by_tag[tag] = by_tag.get(tag, 0) + 1
                    if len(items) >= max_results:
                        _done = True
                        break
            # 每个文件处理完立即检查, 否则会越过 max_results 直到当前目录扫完
            if _done:
                break
        if _done:
            break
    # 与 PHP 对齐: by_tag 按 key 排序 (PHP 用 ksort), tags 随之稳定; 否则聚合顺序取决于
    # 遍历到文件的先后, 两端会给出不同的 tag 顺序
    _sorted_tags = {k: by_tag[k] for k in sorted(by_tag.keys())}
    return ok({'path': rel, 'tags': list(_sorted_tags.keys()), 'by_tag': _sorted_tags if _sorted_tags else [],
               'files_scanned': files_scanned, 'total': len(items), 'items': items})


def action_stats(req, cfg):
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    group_by = req.get('group_by')
    if group_by is None:
        group_by = 'ext'
    if group_by not in ('ext', 'dir'):
        return fail(E['INVALID_PARAM'], 'bad_group_by', "group_by 只支持 'ext' 或 'dir'")
    max_files = req.get('max_files', 50000)
    if max_files is None:
        max_files = 50000
    max_files = int(max_files)
    if max_files < 1:
        return fail(E['INVALID_PARAM'], 'bad_limit', 'max_files 必须 >= 1 (收到 %d)' % max_files)
    max_size = req.get('max_size', 8 * 1024 * 1024)
    if max_size is None:
        max_size = 8 * 1024 * 1024
    max_size = int(max_size)
    if max_size < 1:
        return fail(E['INVALID_PARAM'], 'bad_limit', 'max_size 必须 >= 1 (收到 %d)' % max_size)
    file_pattern = req.get('file_pattern')
    base = cfg['WORK_DIR'].rstrip('/\\') + os.sep
    code_exts = {'php', 'js', 'ts', 'jsx', 'tsx', 'java', 'go', 'c', 'cpp', 'rs', 'swift', 'kt'}

    files = []
    if os.path.isdir(dest):
        for root, dirs, fns in os.walk(dest):
            dirs[:] = [d for d in dirs if d not in ('.', '..')]
            for fn in fns:
                real = os.path.join(root, fn)
                if not real.startswith(base):
                    continue
                if file_pattern:
                    ok_fp = False
                    for fp in re.split(r'[\s,]+', file_pattern.strip()):
                        if fp and fnmatch.fnmatch(fn, fp):
                            ok_fp = True
                            break
                    if not ok_fp:
                        continue
                files.append(real)
                if len(files) >= max_files:
                    break
            if len(files) >= max_files:
                break
    else:
        files.append(dest)

    total_files = total_bytes = total_lines = total_blank = total_comment = 0
    groups = {}
    largest = []
    for real in files:
        if _special_kind(real) is not None:
            continue          # 特殊节点不计入统计, 也绝不能 open
        try:
            size = os.path.getsize(real)
        except OSError:
            continue
        if size == 0 or size > max_size:
            continue
        ext = os.path.splitext(real)[1].lower().lstrip('.')
        try:
            with open(real, 'r', encoding='utf-8', errors='replace') as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        line_count = len(lines)
        blank = comment = 0
        for ln in lines:
            t = ln.strip()
            if t == '':
                blank += 1
            elif ext in code_exts and (t.startswith('//') or t.startswith('/*') or t.startswith('*')):
                comment += 1
            elif ext == 'py' and t.startswith('#'):
                comment += 1
        total_files += 1
        total_bytes += size
        total_lines += line_count
        total_blank += blank
        total_comment += comment
        key = ext if group_by == 'ext' else os.path.dirname(real)
        g = groups.setdefault(key, {'files': 0, 'bytes': 0, 'lines': 0, 'blank': 0, 'comment': 0})
        g['files'] += 1
        g['bytes'] += size
        g['lines'] += line_count
        g['blank'] += blank
        g['comment'] += comment
        largest.append({'path': real[len(base):], 'bytes': size, 'lines': line_count})
    largest.sort(key=lambda x: x['bytes'], reverse=True)
    largest = largest[:10]
    return ok({
        'group_by': group_by, 'total_files': total_files, 'total_bytes': total_bytes,
        'total_lines': total_lines, 'total_blank': total_blank, 'total_comment': total_comment,
        'total_code': max(0, total_lines - total_blank - total_comment),
        'groups': groups, 'largest': largest,
    })


def action_read_range(req, cfg):
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    if os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'is_dir', 'Path is a directory')
    sk = _special_kind(dest)
    if sk is not None:
        return fail(E['INVALID_PARAM'], 'not_regular_file',
                    "Not a regular file (%s); 无法按文本读取" % sk,
                    {'special_type': sk})
    # 二进制文件 (前 8KB 含 NUL): 按行读只会返回乱码, 避免烧掉调用方上下文.
    try:
        with open(dest, 'rb') as _pf:
            _head = _pf.read(8192)
        if b'\0' in _head:
            return fail(E['INVALID_PARAM'], 'binary_file',
                        "File '%s' 看起来是二进制 (前 8KB 含 NUL), 不能按行读取" % rel,
                        {'path': rel, 'type': 'binary',
                         'hint': '需要原始字节请用 download; 文本文件请确认编码。'})
    except OSError:
        pass
    fmt = _php_str(req.get('format')).strip().lower()
    if fmt == '':
        fmt = 'json'
    if fmt == 'lines':
        fmt = 'json'  # 旧帮助里出现过 format=lines, 作为 json 的别名兼容
    if fmt not in ('json', 'text'):
        return fail(E['INVALID_PARAM'], 'bad_format',
                    "format 只支持 json 或 text, 收到 '%s'" % fmt,
                    {'hint': '要结构化数组用 format="json" (默认), 要省 token 的纯文本块用 format="text"。'})
    start = max(1, _php_intval(req.get('start_line', 1)))
    # 对齐 PHP: end_line 优先; 否则 count 默认 200 且上限 5000。
    # 旧实现缺省返回"全部剩余行", 800 行文件一次吐 800 行, 对大文件极易打爆调用方上下文。
    _end = _php_intval(req.get('end_line', 0))
    if _end > 0 and _end < start:
        return fail(E['INVALID_PARAM'], 'bad_range',
                    'end_line (%d) 不能小于 start_line (%d)' % (_end, start),
                    {'hint': '行号从 1 开始; 读取第 10-20 行用 start_line=10&end_line=20, '
                             '或 start_line=10&count=11。'})
    if _end > 0:
        count = _end - start + 1
    else:
        # 不能写 `count or 200`: PHP 对 count=0 的语义是"clamp 到 1", 不是回退默认值
        count = 200 if req.get('count') is None else _php_intval(req.get('count'))
    if count > 5000:
        count = 5000
    if count < 1:
        count = 1
    max_line_len = _php_intval(req.get('max_line_len', 2000))
    if max_line_len < 1:
        return fail(E['INVALID_PARAM'], 'bad_max_line_len',
                    'max_line_len 必须是 >= 1 的整数, 收到 %r' % (req.get('max_line_len'),),
                    {'hint': '默认 2000 字节; 传 0/负数/非数字会被拒绝, 避免每行都被截成 "..."。'})
    # 逐行流式读 (与 PHP 一致): 不再 readlines() 整包进内存, 大文件只占 O(1) 额外空间
    buf = []
    line_no = 0
    truncated_line = False
    try:
        with open(dest, 'r', encoding='utf-8', errors='replace') as f:
            for raw in f:
                line_no += 1
                if line_no < start:
                    continue
                if len(buf) >= count:
                    continue          # 仍需读完以得到准确 total_lines
                text = raw.rstrip('\r\n')
                # 截断按字节计长 (与 PHP strlen 一致), 切点遵守 UTF-8 字符边界
                _b = text.encode('utf-8')
                if len(_b) > max_line_len:
                    text = _b[:max_line_len].decode('utf-8', 'ignore') + '...'
                    truncated_line = True
                buf.append({'line': line_no, 'text': text})
    except OSError as e:
        return fail(E['INTERNAL'], 'read_fail', str(e))
    last = buf[-1]['line'] if buf else (start - 1)
    has_more = line_no > last
    base = {
        'path': rel, 'start_line': start,
        # end_line 是本次实际返回的最后一行, 不是文件总行数
        'end_line': last, 'returned': len(buf),
        'total_lines': line_no,
        # truncated 表示因 count/end_line 限制还有更多行未返回, 与 has_more 等价
        'truncated': has_more,
        'truncated_line': truncated_line, 'has_more': has_more,
    }
    if fmt == 'text':
        # 纯文本块: 每行 "行号<TAB>内容"。比 content[{line,text}] 数组省约 29% token(实测)
        base['text'] = '\n'.join('%d\t%s' % (it['line'], it['text']) for it in buf)
    else:
        base['content'] = buf
    return ok(base)


def action_format(req, cfg):
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "Path '%s' not found" % rel)
    if os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'is_dir', 'Path is a directory')
    sk = _special_kind(dest)
    if sk is not None:
        return fail(E['INVALID_PARAM'], 'not_regular_file',
                    "Not a regular file (%s); 无法格式化" % sk,
                    {'special_type': sk})
    typ = req.get('type', 'auto')
    if not isinstance(typ, str):
        return fail(E['INVALID_PARAM'], 'bad_type', 'type 必须是字符串 (auto/json/xml/raw...)，不能传 true/数字/数组/对象',
                    {'hint': '不传则按扩展名自动识别; 例: {"type": "json"}'})
    ext = os.path.splitext(dest)[1].lower().lstrip('.')
    if typ == 'auto':
        typ = 'json' if ext == 'json' else ('xml' if ext == 'xml' else 'raw')
    try:
        with open(dest, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        return fail(E['INTERNAL'], 'read_fail', 'Cannot read file')
    formatted = None
    valid = True
    error = ''
    if typ == 'json':
        try:
            obj = json.loads(content)
            formatted = json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception as e:
            valid = False
            error = str(e)
    elif typ == 'xml':
        try:
            import xml.dom.minidom as _md
            dom = _md.parseString(content)
            formatted = dom.toprettyxml(indent='  ')
        except Exception:
            valid = False
            error = 'Invalid XML'
    if not valid:
        return fail(E['INVALID_PARAM'], 'parse_error', error, {'type': typ})
    changed = (formatted is not None) and (formatted != content)
    backed_up = ''
    write = _bool_flag(req.get('write'))
    if write is None:
        return bad_bool('write', req.get('write'))
    if write and changed and cfg.get('BACKUP_KEEP', 0) > 0:
        try:
            backed_up = _create_backup(dest, rel, cfg) or ''
        except Exception:
            backed_up = ''
    if write and changed:
        try:
            tmp = dest + '.fmt_tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(formatted)
            os.replace(tmp, dest)
        except OSError:
            return fail(E['UPLOAD_FAILED'], 'write_fail', 'Cannot write formatted file')
    final = formatted if formatted is not None else content
    return ok({
        'path': rel, 'type': typ, 'valid': valid, 'changed': changed,
        'written': write and changed,
        'before_bytes': len(content.encode('utf-8')),
        'after_bytes': len(final.encode('utf-8')),
        'backed_up': backed_up, 'content': final,
    })


def _lint_response(rel, data):
    """与 PHP 一致的 lint 契约: 有 error 时顶层 ok=false/code=1014, 并带 valid=false."""
    has_error = any((i.get('severity') or '') == 'error' for i in (data.get('issues') or []))
    valid = data.get('syntax_ok')
    if has_error:
        valid = False
    data['valid'] = valid
    if valid is False:
        return fail(E['VALIDATION_FAILED'], 'syntax_error',
                    'Syntax check failed: %s' % rel, data)
    return ok(data)


def action_lint(req, cfg):
    rel = req.get('path', '')
    dest = safe_path(rel, cfg['WORK_DIR'])
    if not dest:
        return bad_path(rel)
    if not os.path.exists(dest):
        return fail(E['NOT_FOUND'], 'not_found', "File '%s' not found" % rel)
    if os.path.isdir(dest):
        return fail(E['INVALID_PARAM'], 'is_dir', 'Path is a directory')
    sk = _special_kind(dest)
    if sk is not None:
        return fail(E['INVALID_PARAM'], 'not_regular_file',
                    "Not a regular file (%s); 无法 lint" % sk,
                    {'special_type': sk})
    try:
        with open(dest, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        return fail(E['INTERNAL'], 'read_fail', 'Cannot read')

    ext = os.path.splitext(dest)[1].lower().lstrip('.')
    issues = []

    if ext == 'php':
        lines = content.splitlines()
        in_php = False
        outside_php_lines = 0
        for ln in lines:
            t = ln.strip()
            if t.startswith('<?php') or t.startswith('<?='):
                in_php = True
            if ln.endswith('?>'):
                in_php = False
            if not in_php and t != '' and t != '?>' and not t.startswith('<?'):
                outside_php_lines += 1
        php = _find_php_cli() or 'php'
        tmp = tempfile.NamedTemporaryFile('w', suffix='.php', delete=False, encoding='utf-8')
        tmp.write(content); tmp.close()
        try:
            out = subprocess.run([php, '-l', tmp.name], capture_output=True, text=True, timeout=30)
            rc = out.returncode
        finally:
            os.unlink(tmp.name)
        syntax_ok = (rc == 0)
        if not syntax_ok:
            for msg in (out.stdout + out.stderr).splitlines():
                m = re.search(r'on line (\d+)', msg)
                if m:
                    issues.append({'line': int(m.group(1)), 'severity': 'error',
                                   'rule': 'php_syntax', 'message': msg.strip()})
        return _lint_response(rel, {'language': 'php', 'syntax_ok': syntax_ok, 'issues': issues,
                   'stats': {'template_lines': outside_php_lines},
                   'summary': _lint_summary(issues)})

    if ext in ('js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs'):
        open_braces = open_brackets = open_parens = 0
        in_str = None
        i = 0
        n = len(content)
        while i < n:
            c = content[i]
            if in_str:
                if c == '\\':
                    i += 2; continue
                if c == in_str:
                    in_str = None
                i += 1; continue
            if c == '/' and i + 1 < n:
                nxt = content[i + 1]
                if nxt == '/':
                    while i < n and content[i] != '\n':
                        i += 1
                    continue
                if nxt == '*':
                    i += 2
                    while i + 1 < n and not (content[i] == '*' and content[i + 1] == '/'):
                        i += 1
                    i += 2; continue
            if c in ('"', "'", '`'):
                in_str = c; i += 1; continue
            if c == '{':
                open_braces += 1
            elif c == '}':
                open_braces -= 1
            elif c == '[':
                open_brackets += 1
            elif c == ']':
                open_brackets -= 1
            elif c == '(':
                open_parens += 1
            elif c == ')':
                open_parens -= 1
            i += 1
        for idx, line in enumerate(content.splitlines()):
            if re.search(r'\s+$', line):
                issues.append({'line': idx + 1, 'severity': 'info', 'rule': 'trailing_whitespace',
                               'message': 'Trailing whitespace'})
        if open_braces != 0:
            issues.append({'line': 0, 'severity': 'error', 'rule': 'brace_balance',
                           'message': 'Unbalanced braces: %d' % open_braces})
        if open_brackets != 0:
            issues.append({'line': 0, 'severity': 'error', 'rule': 'bracket_balance',
                           'message': 'Unbalanced brackets: %d' % open_brackets})
        if open_parens != 0:
            issues.append({'line': 0, 'severity': 'error', 'rule': 'paren_balance',
                           'message': 'Unbalanced parens: %d' % open_parens})
        if 'console.log' in content:
            issues.append({'line': 0, 'severity': 'warning', 'rule': 'console_log',
                           'message': 'console.log found, may need cleanup'})
        return _lint_response(rel, {'language': 'js', 'syntax_ok': open_braces == 0 and open_brackets == 0 and open_parens == 0,
                   'issues': issues, 'summary': _lint_summary(issues)})

    if ext == 'json':
        try:
            json.loads(content)
            ok_json = True
            err_msg = ''
            err_line = 0
        except Exception as e:
            ok_json = False
            err_msg = str(e)
            err_line = 0
            m = re.search(r'line\s+(\d+)', err_msg, re.I)
            if m:
                err_line = int(m.group(1))
        if not ok_json:
            issues.append({'line': err_line, 'severity': 'error', 'rule': 'json_syntax',
                           'message': err_msg + (' (near line %d)' % err_line if err_line else '')})
        return _lint_response(rel, {'language': 'json', 'syntax_ok': ok_json, 'issues': issues, 'summary': _lint_summary(issues)})

    if ext in ('py', 'python'):
        py = _find_python_cli() or sys.executable
        tmp = tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8')
        tmp.write(content); tmp.close()
        try:
            out = subprocess.run([py, '-m', 'py_compile', tmp.name], capture_output=True, text=True, timeout=30)
            rc = out.returncode
        finally:
            os.unlink(tmp.name)
        syntax_ok = (rc == 0)
        py_msg = (out.stdout + out.stderr).strip()
        if not syntax_ok:
            m = re.search(r'line\s+(\d+)', py_msg, re.I)
            err_line = int(m.group(1)) if m else 0
            issues.append({'line': err_line, 'severity': 'error', 'rule': 'py_syntax', 'message': py_msg})
        return _lint_response(rel, {'language': 'py', 'syntax_ok': syntax_ok, 'issues': issues, 'summary': _lint_summary(issues)})

    return _lint_response(rel, {'language': ext or 'unknown', 'syntax_ok': None, 'issues': [],
               'summary': {'error': 0, 'warning': 0, 'info': 0},
               'message': 'No linter available for .%s' % ext})


def _lint_summary(issues):
    s = {'error': 0, 'warning': 0, 'info': 0}
    for it in issues:
        k = it.get('severity', 'info')
        if k in s:
            s[k] += 1
    return s


def _find_python_cli():
    for cand in ('python3', 'python'):
        try:
            subprocess.run([cand, '--version'], capture_output=True, timeout=5)
            return cand
        except (OSError, subprocess.SubprocessError):
            continue
    return None



def action_doc(req, cfg):
    # 对齐 PHP: 接受 format(json/markdown), 返回全部 action 目录
    fmt = (req.get('format') or 'json')
    src = ''
    try:
        with open(os.path.abspath(__file__), 'r', encoding='utf-8') as _fh:
            src = _fh.read()
    except Exception:
        src = ''
    funcs = {}
    if src:
        blocks = re.split(r'\ndef (action_\w+)\(req, cfg\):', src)
        for i in range(1, len(blocks), 2):
            name = blocks[i]
            body = blocks[i + 1] if i + 1 < len(blocks) else ''
            ds = ''
            dm = re.search(r'^\s*("""|\'\'\')(.*?)\1', body, re.S)
            if dm:
                ds = dm.group(2)
            # 对齐 PHP _doc_clean: 去 docstring 公共缩进(首行不参与计算), 再 strip
            _lines = ds.split('\n')
            _rest = [l for l in _lines[1:] if l.strip()]
            if _rest:
                _ind = min(len(l) - len(l.lstrip(' ')) for l in _rest)
                _lines = [_lines[0].strip()] + [(l[_ind:] if l.strip() else '') for l in _lines[1:]]
            ds = '\n'.join(_lines).strip()
            funcs[name] = ds
    routes = []
    for act, fn in ROUTES.items():
        fn_name = fn.__name__
        routes.append({'action': act, 'function': fn_name, 'doc': funcs.get(fn_name, '')})
    routes.sort(key=lambda r: r['action'])
    total = len(routes)
    if fmt == 'markdown':
        md = '# ai-sync API v%s\n\n总计 **%d** 个 action\n\n' % (cfg.get('API_VERSION', ''), total)
        md += '| Action | 实现 | 说明 |\n|---|---|---|\n'
        for r in routes:
            first = (r['doc'].splitlines()[0] if r['doc'] else '')
            md += '| `%s` | `%s` | %s |\n' % (r['action'], r['function'], first.replace('|', '\\|'))
        md += '\n## 详细说明\n\n'
        for r in routes:
            md += '### `%s`\n\n%s\n\n' % (r['action'], r['doc'] or '_无说明_')
        return ok({'format': 'markdown', 'total': total, 'content': md})
    return ok({'format': 'json', 'total': total, 'actions': routes})


# ==================== snippet ====================
def _snippet_dir(cfg):
    return os.path.join(cfg['WORK_DIR'].rstrip('/'), 'ai-sync', 'snippets')


def _snippet_sanitize_name(name):
    return re.sub(r'[^a-zA-Z0-9_.\-]', '_', name or '')


def action_snippet_set(req, cfg):
    name = req.get('name', '')
    if name == '':
        return fail(E['INVALID_PARAM'], 'empty_name', 'name is required')
    safe = _snippet_sanitize_name(name)
    content = req.get('content', '')
    if content == '':
        return fail(E['INVALID_PARAM'], 'empty_content', 'content is required')
    tags = req.get('tags')
    if tags is None:
        tags = []
    if isinstance(tags, dict):
        tags = list(tags.values())  # 对齐 PHP: JSON 对象即数组
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        return fail(E['INVALID_PARAM'], 'bad_tags', 'tags 必须是字符串数组')
    record = {
        'name': name, 'safe': safe, 'content': content,
        'language': req.get('language', 'plain'),
        'tags': tags, 'description': req.get('description', ''),
        'created_at': int(time.time()), 'updated_at': int(time.time()),
    }
    d = _snippet_dir(cfg)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, safe + '.json')
    if os.path.exists(path):
        try:
            old = json.load(open(path, 'r', encoding='utf-8'))
            record['created_at'] = old.get('created_at', record['created_at'])
        except Exception:
            pass
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False)
    return ok({'name': name, 'safe': safe, 'language': record['language'], 'tags': record['tags']})


def action_snippet_get(req, cfg):
    name = req.get('name', '')
    if name == '':
        return fail(E['INVALID_PARAM'], 'empty_name', 'name is required')
    path = os.path.join(_snippet_dir(cfg), _snippet_sanitize_name(name) + '.json')
    if not os.path.exists(path):
        return fail(E['NOT_FOUND'], 'not_found', "Snippet '%s' not found" % name)
    with open(path, 'r', encoding='utf-8') as f:
        return ok(json.load(f))


def action_snippet_list(req, cfg):
    d = _snippet_dir(cfg)
    if not os.path.isdir(d):
        return ok({'snippets': [], 'total': 0})
    tag_filter = req.get('tag')
    lang_filter = req.get('language')
    keyword = (req.get('keyword') or '').lower()
    items = []
    for fn in os.listdir(d):
        if not fn.endswith('.json'):
            continue
        try:
            rec = json.load(open(os.path.join(d, fn), 'r', encoding='utf-8'))
        except Exception:
            continue
        if tag_filter and tag_filter not in (rec.get('tags') or []):
            continue
        if lang_filter and (rec.get('language') or '') != lang_filter:
            continue
        if keyword:
            hay = ((rec.get('name') or '') + ' ' + (rec.get('description') or '') + ' ' + (rec.get('content') or '')).lower()
            if keyword not in hay:
                continue
        items.append({
            'name': rec.get('name', ''), 'language': rec.get('language', 'plain'),
            'tags': rec.get('tags', []), 'description': rec.get('description', ''),
            'size': len(rec.get('content', '')), 'updated_at': rec.get('updated_at', 0),
        })
    items.sort(key=lambda x: x['updated_at'], reverse=True)
    return ok({'snippets': items, 'total': len(items)})


def action_snippet_delete(req, cfg):
    name = req.get('name', '')
    if name == '':
        return fail(E['INVALID_PARAM'], 'empty_name', 'name is required')
    path = os.path.join(_snippet_dir(cfg), _snippet_sanitize_name(name) + '.json')
    if not os.path.exists(path):
        return fail(E['NOT_FOUND'], 'not_found', "Snippet '%s' not found" % name)
    os.remove(path)
    return ok({'name': name, 'deleted': True})


# ==================== error_log ====================
def _is_safe_log_path(custom, cfg):
    work = os.path.realpath(cfg['WORK_DIR'].rstrip('/\\'))
    rp = os.path.realpath(custom)
    if rp == work or rp.startswith(work + os.sep):
        return True
    for d in ('/var/log', '/tmp', '/usr/local', '/www/server'):
        rd = os.path.realpath(d)
        if rp == rd or rp.startswith(rd + os.sep):
            return True
    return custom.endswith('.log') or custom.endswith('.txt')


def _try_parse_jsonl_ts(line):
    line = line.strip()
    if not line.startswith('{'):
        return 0
    try:
        j = json.loads(line)
        ts = j.get('ts') or j.get('time') or j.get('timestamp')
        if isinstance(ts, (int, float)):
            return int(ts)
        if isinstance(ts, str) and ts.isdigit():
            return int(ts)
    except Exception:
        pass
    return 0


def action_error_log(req, cfg):
    # 对齐 PHP: name 发现候选日志 / path 自定义 / grep+regex 过滤 / since_ts
    name = req.get('name', 'php_error')
    if name is None:
        name = 'php_error'
    if not isinstance(name, str):
        return fail(E['INVALID_PARAM'], 'bad_name', 'name 必须是字符串 (日志源名, 先调用 error_log_list 查看)',
                    {'hint': 'name 不能传 true/数字/数组/对象; 例: {"name": "php_error"}'})
    # 与 PHP 一致: path 仅在 name=custom 时生效; 其它 name 下忽略, 避免无关 path 触发校验
    custom = ''
    if name == 'custom':
        _cp = req.get('path')
        if _cp is not None and not isinstance(_cp, str):
            return fail(E['INVALID_PARAM'], 'bad_path', 'path must be a string when name=custom')
        custom = str(_cp or '').strip()
    lines_n = max(1, min(_php_intval(req.get('lines', 100)) or 100, 5000))
    grep = req.get('grep', '')
    if grep is None:
        grep = ''
    if not isinstance(grep, str):
        return fail(E['INVALID_PARAM'], 'bad_grep', 'grep 必须是字符串关键词',
                    {'hint': 'grep 不能传 true/数字/数组/对象; 正则过滤请用 regex=<pattern> + regex=true'})
    use_regex = _bool_flag(req.get('regex', False))
    if use_regex is None:
        return bad_bool('regex', req.get('regex'))
    since = int(req.get('since_ts', 0) or 0)
    before = int(req.get('before_ts', 0) or 0)
    # relate_to: recent=从审计提取最近写操作涉及文件并 grep; 其它=直接 grep 指定路径
    relate_to = req.get('relate_to', '')
    if relate_to is None:
        relate_to = ''
    if not isinstance(relate_to, str):
        return fail(E['INVALID_PARAM'], 'bad_relate_to', 'relate_to 必须是字符串 ("recent" 或文件路径)',
                    {'hint': 'relate_to 不能传 true/数字/数组/对象'})
    relate_to = relate_to.strip()
    related_files = []
    if relate_to:
        if relate_to == 'recent':
            audit_file = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg.get('AUDIT_LOG', 'ai-sync/audit.log'))
            if os.path.isfile(audit_file):
                write_acts = {'upload', 'upload_batch', 'delete', 'rename', 'copy', 'chunked_upload', 'php_run'}
                for al in _read_tail_lines(audit_file, 500):
                    try:
                        j = json.loads(al)
                    except Exception:
                        continue
                    if not isinstance(j, dict) or j.get('action') not in write_acts:
                        continue
                    p = j.get('path', '')
                    if p and p not in related_files:
                        related_files.append(p)
            if related_files:
                grep = '|'.join(re.escape(p) for p in related_files)
                use_regex = True
        else:
            related_files = [relate_to]
            if grep == '':
                grep = re.escape(relate_to)
                use_regex = True
    candidates = _error_log_candidates(cfg)
    target = ''
    if custom:
        if not _is_safe_log_path(custom, cfg):
            return fail(E['FORBIDDEN'], 'unsafe_path', 'custom 日志路径不在白名单目录或后缀非 log/txt')
        target = custom
    else:
        for c in candidates:
            if c['name'] == name and c.get('readable'):
                target = c['path']
                break
        if not target:
            return fail(E['NOT_FOUND'], 'log_not_found',
                        "未找到 name='%s' 的可读日志，先调用 error_log_list 查看可用列表" % name,
                        {'available': ['%s:%s' % (c['name'], c['path']) for c in candidates]})
    if not os.path.isfile(target) or not os.access(target, os.R_OK):
        return fail(E['NOT_FOUND'], 'not_readable', '日志路径不可读: %s' % target)
    all_lines = _read_tail_lines(target, lines_n * (5 if grep else 1))
    matched = []
    for ln in all_lines:
        if grep:
            if use_regex:
                try:
                    if re.search(grep, ln) is None:
                        continue
                except re.error:
                    return fail(E['INVALID_PARAM'], 'bad_regex', 'regex 参数无效')
            else:
                if grep.lower() not in ln.lower():
                    continue
        if since or before:
            ts = _try_parse_jsonl_ts(ln)
            if ts:
                if since and ts < since:
                    continue
                if before and ts > before:
                    continue
        matched.append(ln)
    if len(matched) > lines_n:
        matched = matched[-lines_n:]
    parsed = None
    if matched and matched[0].lstrip().startswith('{'):
        parsed = []
        for ln in matched:
            try:
                parsed.append(json.loads(ln))
            except Exception:
                parsed.append({'raw': ln})
    return ok({
        'name': name, 'path': target, 'size': os.path.getsize(target),
        'mtime': int(os.path.getmtime(target)), 'format': _guess_log_format(target),
        'lines': matched, 'line_count': len(matched), 'parsed': parsed, 'related_files': related_files,
    })


def _error_log_candidates(cfg):
    work_dir = cfg['WORK_DIR'].rstrip('/\\')
    candidates = []

    def add(name, path, fmt='text', scope=None, note=None, from_=None):
        if os.path.isfile(path) and os.access(path, os.R_OK):
            c = {'name': name, 'path': path, 'format': fmt}
            if scope:
                c['scope'] = scope
            if note:
                c['note'] = note
            if from_:
                c['from'] = from_
            c['readable'] = True
            try:
                c['size'] = os.path.getsize(path)
                c['mtime'] = int(os.path.getmtime(path))
            except OSError:
                c['size'] = 0
                c['mtime'] = 0
            candidates.append(c)

    # 1. scope 自带 audit
    add('audit', os.path.join(work_dir, cfg['AUDIT_LOG'].lstrip('/\\')), 'jsonl', scope='ai-sync')
    # 2. php error_log (ini)
    php_err = ''
    try:
        import subprocess as _sp
        r = _sp.run([_find_php_cli() or 'php', '-r', "echo ini_get('error_log');"],
                    capture_output=True, text=True, timeout=10)
        php_err = r.stdout.strip()
    except Exception:
        php_err = ''
    if php_err and os.path.isfile(php_err) and os.access(php_err, os.R_OK):
        add('php_error', php_err, 'text', note='from ini_get(error_log)')
    for p in ['/var/log/php-fpm.log', '/var/log/php-fpm/error.log',
              '/var/log/nginx/php_errors.log', '/tmp/php_errors.log',
              '/usr/local/php/var/log/php-fpm.log']:
        if os.path.isfile(p) and os.access(p, os.R_OK):
            add('php_error', p, 'text')
            break
    # 3. nginx
    for p, n in [('/var/log/nginx/error.log', 'nginx_err'),
                 ('/var/log/nginx/access.log', 'nginx_acc'),
                 ('/www/server/nginx/logs/error.log', 'nginx_err'),
                 ('/www/server/nginx/logs/access.log', 'nginx_acc'),
                 ('/usr/local/nginx/logs/error.log', 'nginx_err'),
                 ('/usr/local/nginx/logs/access.log', 'nginx_acc')]:
        if os.path.isfile(p) and os.access(p, os.R_OK):
            add(n, p, 'nginx_access' if n == 'nginx_acc' else 'text')
    # 4. workdir 常见 *.log
    scan_dirs = [work_dir]
    if os.path.isdir(os.path.join(work_dir, 'logs')):
        scan_dirs.append(os.path.join(work_dir, 'logs'))
    if os.path.isdir(os.path.join(work_dir, 'runtime')):
        scan_dirs.append(os.path.join(work_dir, 'runtime'))
    seen = set()
    for sd in scan_dirs:
        try:
            logs = [os.path.join(sd, f) for f in os.listdir(sd) if f.endswith('.log')][:20]
        except OSError:
            continue
        for f in logs:
            bn = os.path.basename(f).lower()
            is_err = ('error' in bn or 'err' in bn)
            name = 'custom_error' if is_err else 'custom_log'
            if f in seen:
                continue
            seen.add(f)
            if os.path.isfile(f) and os.access(f, os.R_OK):
                add(name, f, _guess_log_format(f), from_='workdir_scan')
    # 去重
    out, seen2 = [], set()
    for c in candidates:
        if c['path'] in seen2:
            continue
        seen2.add(c['path'])
        out.append(c)
    return out


def action_error_log_list(req, cfg):
    out = _error_log_candidates(cfg)
    return ok({'logs': out, 'total': len(out)})


def _guess_log_format(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(2000)
    except OSError:
        return 'text'
    if head.lstrip().startswith('{'):
        return 'json'
    if 'nginx' in head.lower() or head.count(' - - ['):
        return 'nginx_access'
    return 'text'


# ==================== 管理类 action ====================
def _deployment_warnings(cfg):
    """部署自检: 严格对齐 PHP sync_api.php 的 _deployment_warnings()。
    只做静态判断, 不发网络请求, 不阻塞请求。"""
    app_dir = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = STATE.get('config_path') or ''
    cfg_real = os.path.realpath(cfg_path)
    _prefix = app_dir.rstrip(os.sep) + os.sep
    cfg_in_app = cfg_real.startswith(_prefix)
    w = []
    if cfg_in_app:
        w.append({
            'code': 'config_in_webroot', 'severity': 'critical',
            'message': 'sync_config.json 位于 Web 根目录内, 可被 GET /sync_config.json 直接下载 (含 token)',
            'hint': '把配置移到 Web 根之外并设置 AI_SYNC_CONFIG_FILE; 或在 nginx 加 exact deny. 移动后轮换全部 token.',
        })
    pkg_dir = str(cfg.get('PACKAGE_DIR') or '').rstrip('/\\')
    pkg_real = os.path.realpath(pkg_dir) if pkg_dir else ''
    if pkg_real and pkg_real.startswith(_prefix):
        w.append({
            'code': 'packages_in_webroot', 'severity': 'high',
            'message': '客户端安装包目录位于 Web 根目录内 (安装包含 token 明文)',
            'hint': '在 sync_config.json global.package_dir 指定 Web 根之外的目录; 或使用 nginx deny.',
        })
    if STATE.get('MULTI_DIR') and (STATE.get('RAW_CONFIG') or {}).get('admin_token') and cfg_in_app:
        w.append({
            'code': 'admin_token_exposed', 'severity': 'critical',
            'message': 'admin_token 与可静态下载的配置文件同处 Web 根, 等于管理员凭据公开',
            'hint': '立即将配置移出 Web 根并轮换 admin_token 与所有 scope token.',
        })
    if cfg.get('EXEC_ENABLED') and hasattr(os, 'geteuid') and os.geteuid() == 0:
        w.append({
            'code': 'exec_as_root', 'severity': 'high',
            'message': 'exec/shell 已启用且 PHP 进程以 root 运行, 任何 exec token 等价于 root 远程执行',
            'hint': '改用非 root 用户运行 php-fpm, 或关闭 exec_enabled, 并为 exec 配置 IP 白名单.',
        })
    return {'config_file': cfg_path, 'config_in_app_dir': cfg_in_app,
            'package_dir': pkg_dir, 'warnings': w}


# ---- 服务端参考客户端 (与 sync_api.py 同目录的 sync.py) 元数据 ----
# 对齐 PHP _client_meta(): md5 按 mtime 缓存, whoami 高频调用不重算。
_CLIENT_META_CACHE = {'mtime': None, 'data': None}


def _client_sync_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sync.py')


def _client_meta():
    path = _client_sync_path()
    try:
        st = os.stat(path)
    except OSError:
        return {'md5': None, 'size': 0, 'mtime': None}
    mt, sz = int(st.st_mtime), int(st.st_size)
    c = _CLIENT_META_CACHE
    if c['mtime'] == mt and c['data'] is not None:
        return c['data']
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    c['data'] = {'md5': h.hexdigest(), 'size': sz, 'mtime': mt}
    c['mtime'] = mt
    return c['data']


def action_client_download(req, cfg):
    """对齐 PHP action_client_download: 返回参考 sync.py 原始字节流。
    dispatch 返回 {'_client_stream': path, '_client_md5': md5}, 由 Handler 直接写字节
    (Content-Type: application/octet-stream + X-Client-Md5 响应头)。"""
    meta = _client_meta()
    if meta['md5'] is None:
        return fail(E['NOT_FOUND'], 'not_found', 'client sync.py not found')
    return {'_client_stream': _client_sync_path(), '_client_md5': meta['md5']}


def action_ping(req, cfg):
    resp = {
        'version': cfg.get('API_VERSION'), 'time': int(time.time()), 'work_dir': cfg['WORK_DIR'],
        'backup_dir': cfg['BACKUP_DIR'], 'probe_dir': cfg['PROBE_DIR'], 'chunk_dir': cfg['CHUNK_DIR'],
        'exec_enabled': cfg.get('EXEC_ENABLED', False),
        # actions 按 HELP_SPEC 顺序输出 (两端同序), 只列本机已注册的
        'actions': [a for a in HELP_SPEC if a in ROUTES],
        'help': 'help (无参) 列出全部 action 及其必填参数; help name=<action> 看参数契约',
    }
    _dw = _deployment_warnings(cfg)
    resp['deployment_warning_count'] = len(_dw['warnings'])
    if _dw['warnings']:
        resp['deployment_warnings'] = _dw['warnings']
    if 'SCOPE' in cfg:
        resp.update({
            'scope': cfg['SCOPE'], 'token_name': cfg.get('TOKEN_NAME', ''),
            'is_admin': cfg.get('IS_ADMIN', False), 'permissions': cfg.get('PERMISSIONS', []),
            'available_scopes': cfg.get('AVAILABLE_SCOPES', [cfg['SCOPE']]),
        })
    return ok(resp)


def action_help(req, cfg):
    """自描述: 让调用方(尤其 AI)无需外部文档即可知道每个 action 该传什么。
    无参 → 全部 action 索引(名 + 一句话 + 必填参数名);
    name=<action> → 单个 action 的完整契约(必填/可选/返回/示例/常见错误/相关 action)。"""
    # FIX-v4.34 (P2 静默降级): AI 查契约时最常用的字段名是 **action** ——
    #   因为这个协议里 action 就是"指定要什么"的通用字段名(所有请求都靠它指明目标),
    #   所以 {"action":"help","args":{"action":"upload"}} 是极自然的写法。
    #   旧实现只认顶层的 name/about/topic/target, 遇到 action 就**静默忽略**,
    #   照样返回全部 59 条索引且 ok:true —— AI 以为拿到了详情, 实际什么都没拿到,
    #   而且没有任何错误可供察觉。现在显式接管这个写法, 并在响应里回注说明。
    want = ''
    alias_used = ''
    _args = req.get('args')
    if not isinstance(_args, dict):
        _args = {}
    # ① 契约字段优先: 顶层 name/about/topic/target, 再 args 内同名
    for src in (req, _args):
        for k in ('name', 'about', 'topic', 'target'):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                want = v.strip()
                break
        if want:
            break
    # ② 别名: args.action (AI 最易猜错的写法)
    if not want:
        v = _args.get('action')
        if isinstance(v, str) and v.strip():
            want = v.strip()
            alias_used = 'action'
    if not want:
        items = [{'n': n, 'd': HELP_SPEC[n]['d'],
                  'r': [x.split(':', 1)[0] for x in (HELP_SPEC[n].get('r') or [])]}
                 for n in HELP_SPEC if n in ROUTES]
        return ok({
            'total': len(items),
            'usage': 'help name=<action> 查看单个 action 的参数契约; ping 查看权限与 scope',
            'envelope': {
                'request': {'token': 'str 必填', 'scope': 'str 多目录模式下必填',
                            'action': 'str 必填', '...': '各 action 专属参数, 见下方 r(必填)/详情'},
                'response': '恒含 {ok, code}; 失败另含 error, message, fix_hint(可直接照做的修复建议)',
                'http_status': '1001→401, 1002→403, 1003→429, 其余(含 token 之外的业务错误)→200',
            },
            'error_codes': {str(k): v for k, v in ERROR_LABELS.items()},
            'actions': items,
        })

    spec = HELP_SPEC.get(want)
    if spec is None:
        # 与 PHP 端同一套建议算法 (levenshtein + 相对阈值), 保证两端 suggestions 逐项一致
        cand = sorted(((_lev(want, n), n) for n in HELP_SPEC if n in ROUTES))
        lim = max(3, len(want) // 2)
        sug = [n for d, n in cand[:5] if d <= lim]
        return fail(E['INVALID_PARAM'], 'unknown_action', 'No such action: %r' % want,
                    {'hint': '不带参数调用 help 可列出全部 action%s' %
                             ('; 是否想找: ' + ', '.join(sug) if sug else ''),
                     'suggestions': sug})
    if want not in ROUTES:
        return fail(E['INVALID_PARAM'], 'unknown_action',
                    'Action %r is documented but not enabled on this server' % want,
                    {'hint': '该 action 在契约表中但本机未注册, 可能被服务端版本裁剪'})
    _ret = {
        'action': want,
        'desc': spec['d'],
        'required': spec.get('r') or [],
        'optional': spec.get('o') or [],
        'returns': spec['ret'],
        'example': spec.get('eg') or {},
        # FIX-v4.35: 60 个 action 里有 16 个没写 errors 声明, AI 查契约时看不到
        #   "这个调用可能因为什么失败"。逐条手写容易过时(实现改了文档没改),
        #   所以没写具体错误码时自动补**通用错误码** —— 它们对任何 action 都成立。
        'errors': spec.get('err') or COMMON_ERRORS_HINT,
        'see_also': spec.get('see') or [],
    }
    if alias_used:
        _ret['alias_note'] = (
            "你用的是 %r 字段查询本契约; 契约字段名是 'name' (例: {\"action\":\"help\",\"name\":\"%s\"})。"
            "已按你的意图返回详情。" % (alias_used, want))
    return ok(_ret)


def action_health(req, cfg):
    wd = cfg['WORK_DIR'].rstrip('/\\')
    try:
        st = os.statvfs(wd)
        free = st.f_bavail * st.f_frsize
        total = st.f_blocks * st.f_frsize
    except OSError:
        free = total = None
    return ok({
        'version': cfg.get('API_VERSION'), 'uptime_s': int(time.time() - STATE['start_time']),
        'php_version': sys.version.split()[0], 'work_dir': wd,
        'work_dir_writable': os.access(wd, os.W_OK),
        'disk_free_mb': round(free / 1048576, 1) if free else None,
        'disk_total_mb': round(total / 1048576, 1) if total else None,
        'extensions': {'zlib': True, 'json': True, 'curl': False, 'mbstring': True,
                       'zip': True},
        'deployment': _deployment_warnings(cfg),
    })


def action_metrics(req, cfg):
    # 先把内存累计批量落盘, 保证返回最新
    try:
        with STATE['rate_lock']:
            _metrics_flush(cfg)
    except Exception:
        pass
    f = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['METRICS_FILE'])
    if not os.path.isfile(f):
        return ok({'actions': [], 'errors': [], 'total': 0, 'since': int(time.time())})
    try:
        # 与 PHP 对齐: 原样返回落盘结构 (actions 为 {action: 次数} 映射, 不做 list 转换)
        data = json.load(open(f, 'r', encoding='utf-8')) or {}
        data.setdefault('actions', [])
        data.setdefault('errors', [])
        return ok(data)
    except Exception:
        return ok({'actions': [], 'errors': [], 'total': 0, 'since': int(time.time())})


def action_config_get(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not STATE['MULTI_DIR']:
        return ok({'mode': 'single', 'version': cfg.get('API_VERSION'),
                   'config': {'work_dir': cfg['WORK_DIR'], 'token': mask_token(cfg['TOKEN'])}})
    tokens_out = []
    for tk, info in (raw.get('tokens') or {}).items():
        tokens_out.append({'token': mask_token(tk), 'name': info.get('name', ''),
                           'scopes': info.get('scopes', []), 'permissions': info.get('permissions', [])})
    return ok({'mode': 'multi', 'version': raw.get('version', ''), 'global': raw.get('global', {}),
               'admin_token_present': bool(raw.get('admin_token')), 'tokens': tokens_out,
               'scopes': raw.get('scopes', {})})


def action_config_reload(req, cfg):
    new = load_sync_config(STATE['config_path'])
    if new is None:
        return fail(E['INTERNAL'], 'config_load_failed', 'Cannot load sync_config.json')
    with STATE['lock']:
        STATE['RAW_CONFIG'] = new
        STATE['CONFIG'] = build_boot_config(new)
    return ok({'reloaded': True, 'version': new.get('version', ''),
               'tokens': len(new.get('tokens', {})), 'scopes': len(new.get('scopes', {})),
               'note': 'current request still uses old config; new config takes effect on next request'})


def action_scopes_list(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not STATE['MULTI_DIR']:
        return ok({'mode': 'single', 'scopes': [{'name': 'default', 'work_dir': cfg['WORK_DIR'],
                                                'writable': os.access(cfg['WORK_DIR'], os.W_OK)}]})
    out = []
    for name, sc in (raw.get('scopes') or {}).items():
        wd = sc.get('work_dir', '')
        stats = _scope_disk_stats(wd)
        out.append({'name': name, 'work_dir': wd, 'writable': os.access(wd, os.W_OK),
                    'file_count': stats['file_count'], 'size_mb': round(stats['size_bytes'] / 1048576, 2),
                    'backup_count': _scope_backup_count(wd, sc.get('backup_dir', 'ai-sync/backups/')),
                    'backup_keep': sc.get('backup_keep', 0), 'max_size_mb': round((sc.get('max_size', 0) / 1048576), 1),
                    'max_files_per_deploy': sc.get('max_files_per_deploy', cfg.get('MAX_FILES_PER_DEPLOY', 50)),
                    'exec_enabled': sc.get('exec_enabled', False)})
    return ok({'mode': 'multi', 'scopes': out})


def action_tokens_list(req, cfg):
    raw = STATE['RAW_CONFIG']
    show_full = bool(cfg.get('IS_ADMIN'))
    if not STATE['MULTI_DIR']:
        return ok({'mode': 'single', 'tokens': [{'token': cfg['TOKEN'] if show_full else mask_token(cfg['TOKEN']),
                                                 'name': 'default', 'scopes': ['default'],
                                                 'permissions': ['read', 'write', 'delete', 'exec', 'lint', 'backup']}]})
    out = []
    for tk, info in (raw.get('tokens') or {}).items():
        out.append({'token': tk if show_full else mask_token(tk), 'name': info.get('name', ''),
                    'scopes': info.get('scopes', []), 'permissions': info.get('permissions', [])})
    return ok({'mode': 'multi', 'admin_token_present': bool(raw.get('admin_token')), 'tokens': out})


def action_token_add(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not raw:
        return fail(E['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support token management')
    token = str(req.get('new_token') or '').strip()
    name = str(req.get('name') or '').strip()
    scopes = req.get('scopes', []) or []
    permissions = req.get('permissions', []) or []
    if name == '':
        return fail(E['INVALID_PARAM'], 'invalid_param', 'name required',
                    {'hint': 'name 是 token 的便于识别的名字, 必填; new_token 可不填(由服务端生成)'})
    # FIX-v4.35 (帮助文档与实现不符): 契约一直写着 new_token 是可选、"否则自动生成",
    #   但实现强制必填 —— AI 照契约不传就失败, 且被迫自己编一个随机串(安全性还更差)。
    #   现在补上自动生成分支: 不传 new_token 时生成高强度随机串, 并在本次响应里返回**明文**
    #   (手动指定的才掩码, 因为调用方本来就知道; 自动生成的若不返回明文, AI 等于白创建)。
    generated = False
    if token == '':
        token = 'tk-' + secrets.token_urlsafe(24)
        generated = True
    if isinstance(scopes, dict):
        scopes = list(scopes.values())  # 对齐 PHP: JSON 对象即数组
    if isinstance(permissions, dict):
        permissions = list(permissions.values())
    if not isinstance(scopes, list) or not isinstance(permissions, list):
        return fail(E['INVALID_PARAM'], 'invalid_param', 'scopes and permissions must be arrays')
    valid = ['read', 'write', 'delete', 'exec', 'lint', 'backup', 'admin']
    for p in permissions:
        if p not in valid:
            return fail(E['INVALID_PARAM'], 'invalid_permission', "permission '%s' invalid" % p)
    ok_flag, data, extra = update_sync_config_atomic(lambda d: _token_add_mut(d, token, name, scopes, permissions))
    if not ok_flag:
        if data in ('exists', 'invalid_scope'):
            return fail(E['INVALID_PARAM'], data, extra or data)
        return fail(E['INTERNAL'], data, extra or 'failed to write config file',
                    {'hint': '检查配置文件写权限; 并发 admin 操作可能冲突, 可重试'})
    audit_log('token_add', req, 0, {'name': name, 'token': mask_token(token)})
    # 自动生成时必须回明文(否则 AI 拿不到凭据 = 白创建); 手动指定的回掩码(调用方本就知道)
    return ok({'added': True, 'generated': generated,
               'token': token if generated else mask_token(token),
               'name': name, 'scopes': scopes, 'permissions': permissions})


def _token_add_mut(d, token, name, scopes, permissions):
    d.setdefault('tokens', {})
    if token in d['tokens']:
        return [False, 'exists', 'token already exists']
    all_scopes = list((d.get('scopes') or {}).keys())
    for s in scopes:
        if s not in all_scopes:
            return [False, 'invalid_scope', "scope '%s' not found" % s]
    d['tokens'][token] = {'name': name, 'scopes': list(scopes), 'permissions': list(permissions)}
    return [True, d]


def action_token_delete(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not raw:
        return fail(E['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support token management')
    token = str(req.get('target_token') or '').strip()
    if token == '':
        return fail(E['INVALID_PARAM'], 'invalid_param', 'target_token required')
    ok_flag, data, extra = update_sync_config_atomic(lambda d: _token_del_mut(d, token))
    if not ok_flag:
        msg = extra or 'failed to write config file'
        code = E['NOT_FOUND'] if data == 'not_found' else (E['INVALID_PARAM'] if data == 'last_token' else E['INTERNAL'])
        return fail(code, data, msg)
    audit_log('token_delete', req, 0, {'target': mask_token(token)})
    return ok({'deleted': True, 'token': mask_token(token)})


def _token_del_mut(d, token):
    if token not in d.get('tokens', {}):
        return [False, 'not_found', 'token not found']
    if len(d['tokens']) <= 1:
        return [False, 'last_token', '拒绝删除最后一个 token, 否则所有非 admin 客户端将立即失效.']
    del d['tokens'][token]
    return [True, d]


def action_scope_add(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not raw:
        return fail(E['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support scope management')
    name = req.get('name', '')
    if not isinstance(name, str):
        return fail(E['INVALID_PARAM'], 'bad_name', 'name 必须是字符串')
    name = name.strip()
    work_dir = req.get('work_dir', '')
    if not isinstance(work_dir, str):
        return fail(E['INVALID_PARAM'], 'bad_work_dir', 'work_dir 必须是字符串路径')
    work_dir = work_dir.rstrip('/\\')
    if name == '' or work_dir == '':
        return fail(E['INVALID_PARAM'], 'invalid_param', 'name and work_dir required')
    real_wd = os.path.realpath(work_dir)
    if real_wd is False or not os.path.isdir(real_wd):
        return fail(E['INVALID_PARAM'], 'bad_work_dir', "work_dir 不存在或不可访问: %s" % work_dir)
    dangerous = ['/', '/etc', '/root', '/var', '/usr', '/bin', '/sbin', '/boot', '/sys', '/proc', '/dev']
    if real_wd in dangerous:
        return fail(E['INVALID_PARAM'], 'unsafe_work_dir', "拒绝将系统目录 '%s' 配为 work_dir" % real_wd)
    d = default_scope_config()
    for f in ['backup_dir', 'probe_dir', 'chunk_dir', 'webhook_dir', 'audit_log', 'metrics_file']:
        val = req.get(f, d[f])
        if not isinstance(val, str):
            return fail(E['INVALID_PARAM'], 'bad_%s' % f,
                        '%s 必须是字符串相对路径 (如 "ai-sync/audit.log")' % f)
        if re.match(r'^([/\\]|[a-z]:[\\/])', val, re.I) or '..' in val:
            return fail(E['INVALID_PARAM'], 'bad_%s' % f, "%s 必须是相对路径且不含 .. 段, 收到: %s" % (f, val))
    exec_allow_ips = d['exec_allow_ips']
    if req.get('exec_allow_ips') is not None:
        _ips = req.get('exec_allow_ips')
        if isinstance(_ips, dict):
            # 对齐 PHP: json_decode('{}', true) -> [], 对象按值展开后仍按数组校验
            _ips = list(_ips.values())
        if not isinstance(_ips, list):
            return fail(E['INVALID_PARAM'], 'bad_exec_allow_ips', 'exec_allow_ips 必须是字符串数组 (CIDR 或 IP)')
        for _ip in _ips:
            if not isinstance(_ip, str):
                return fail(E['INVALID_PARAM'], 'bad_exec_allow_ips', 'exec_allow_ips 的每一项都必须是字符串 (CIDR 或 IP)')
        exec_allow_ips = list(_ips)
    exec_enabled = _bool_flag(req.get('exec_enabled', d['exec_enabled']))
    if exec_enabled is None:
        return bad_bool('exec_enabled', req.get('exec_enabled'))
    max_files_per_deploy = d['max_files_per_deploy']
    if req.get('max_files_per_deploy') is not None:
        _mfd = req.get('max_files_per_deploy')
        if isinstance(_mfd, bool) or not _php_is_numeric(_mfd):
            return fail(E['INVALID_PARAM'], 'bad_max_files_per_deploy',
                        'max_files_per_deploy 必须是数字, 收到 %s' % _php_type_name(_mfd),
                        {'hint': '传整数, 如 "max_files_per_deploy": 50'})
        max_files_per_deploy = _php_intval(_mfd)
    if max_files_per_deploy < 1:
        return fail(E['INVALID_PARAM'], 'bad_max_files_per_deploy',
                    'max_files_per_deploy 必须是 >= 1 的整数, 收到 %s' % req.get('max_files_per_deploy'),
                    {'hint': '它限制单次 batch_deploy 的文件数, 默认 50; '
                             '调大前确认 PHP memory_limit 与超时能承受。'})
    scope = {
        'work_dir': real_wd + '/',
        'backup_keep': _php_intval(req['backup_keep']) if 'backup_keep' in req else d['backup_keep'],
        'max_size': _php_intval(req['max_size']) if 'max_size' in req else d['max_size'],
        'max_chunked_size': _php_intval(req['max_chunked_size']) if 'max_chunked_size' in req else d['max_chunked_size'],
        'chunk_ttl_days': _php_intval(req['chunk_ttl_days']) if 'chunk_ttl_days' in req else d['chunk_ttl_days'],
        'chunk_size_default': _php_intval(req['chunk_size_default']) if 'chunk_size_default' in req else d['chunk_size_default'],
        'max_files_per_deploy': max_files_per_deploy,
        'exec_enabled': exec_enabled,
        'exec_allow_ips': exec_allow_ips,
        'exec_timeout_max': _php_intval(req['exec_timeout_max']) if 'exec_timeout_max' in req else d['exec_timeout_max'],
        'backup_dir': req.get('backup_dir', d['backup_dir']),
        'probe_dir': req.get('probe_dir', d['probe_dir']),
        'chunk_dir': req.get('chunk_dir', d['chunk_dir']),
        'webhook_dir': req.get('webhook_dir', d['webhook_dir']),
        'audit_log': req.get('audit_log', d['audit_log']),
        'metrics_file': req.get('metrics_file', d['metrics_file']),
    }
    ok_flag, data, extra = update_sync_config_atomic(lambda dd: _scope_add_mut(dd, name, scope))
    if not ok_flag:
        code = E['INVALID_PARAM'] if data == 'exists' else E['INTERNAL']
        return fail(code, data, extra or 'failed to write config file')
    audit_log('scope_add', req, 0, {'name': name, 'work_dir': real_wd + '/'})
    return ok({'added': True, 'name': name, 'work_dir': real_wd + '/'})


def _scope_add_mut(d, name, scope):
    d.setdefault('scopes', {})
    if name in d['scopes']:
        return [False, 'exists', 'scope already exists']
    d['scopes'][name] = scope
    return [True, d]


def action_scope_delete(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not raw:
        return fail(E['INTERNAL'], 'not_multi_mode', 'Single-dir mode does not support scope management')
    name = str(req.get('name') or '').strip()
    if name == '':
        return fail(E['INVALID_PARAM'], 'invalid_param', 'name required')
    referenced = []
    ok_flag, data, extra = update_sync_config_atomic(lambda d: _scope_del_mut(d, name, referenced))
    if not ok_flag:
        code = E['NOT_FOUND'] if data == 'not_found' else E['INTERNAL']
        return fail(code, data, extra or 'failed to write config file')
    audit_log('scope_delete', req, 0, {'name': name, 'referenced_by': referenced})
    return ok({'deleted': True, 'name': name, 'referenced_by': referenced})


def _scope_del_mut(d, name, referenced):
    if name not in d.get('scopes', {}):
        return [False, 'not_found', 'scope not found']
    for tk, info in (d.get('tokens') or {}).items():
        if name in (info.get('scopes') or []):
            referenced.append(mask_token(tk))
    del d['scopes'][name]
    return [True, d]


def action_audit_log_view(req, cfg):
    raw = STATE['RAW_CONFIG']
    lines = max(1, min(1000, _php_intval(req.get('lines', 100))))
    scope_name = req.get('scope') or cfg.get('SCOPE', '')
    if scope_name != cfg.get('SCOPE', ''):
        if not cfg.get('IS_ADMIN'):
            return fail(E['FORBIDDEN'], 'forbidden', "无权访问其他 scope 的审计日志")
    if STATE['MULTI_DIR'] and scope_name in (raw.get('scopes') or {}):
        sc = raw['scopes'][scope_name]
        log_file = os.path.join(sc['work_dir'].rstrip('/'), sc.get('audit_log', 'ai-sync/audit.log'))
    else:
        log_file = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['AUDIT_LOG'])
    if not os.path.isfile(log_file):
        return ok({'lines': [], 'count': 0, 'file': log_file})
    entries = []
    for line in _read_tail_lines(log_file, lines):
        try:
            entries.append(json.loads(line))
        except Exception:
            entries.append({'raw': line})
    return ok({'lines': entries, 'count': len(entries), 'file': log_file})


def action_admin_stats(req, cfg):
    raw = STATE['RAW_CONFIG']
    if not STATE['MULTI_DIR']:
        wd = cfg['WORK_DIR']
        s = _scope_disk_stats(wd)
        df = _disk_free(wd)
        return ok({'mode': 'single', 'work_dir': wd, 'file_count': s['file_count'],
                   'size_mb': round(s['size_bytes'] / 1048576, 2),
                   'backup_count': _scope_backup_count(wd, cfg['BACKUP_DIR']),
                   'disk_free_mb': round(df / 1048576, 1) if df else None})
    scopes_out = []
    total_files = 0
    total_size = 0
    for name, sc in (raw.get('scopes') or {}).items():
        wd = sc.get('work_dir', '')
        s = _scope_disk_stats(wd)
        df = _disk_free(wd)
        scopes_out.append({'name': name, 'work_dir': wd, 'file_count': s['file_count'],
                           'size_mb': round(s['size_bytes'] / 1048576, 2),
                           'backup_count': _scope_backup_count(wd, sc.get('backup_dir', 'ai-sync/backups/')),
                           'disk_free_mb': round(df / 1048576, 1) if df else None})
        total_files += s['file_count']
        total_size += s['size_bytes']
    return ok({'mode': 'multi', 'scopes': scopes_out, 'cached_at': int(time.time()), 'totals': {
        'file_count': total_files, 'size_mb': round(total_size / 1048576, 2),
        'token_count': len(raw.get('tokens', {})), 'scope_count': len(raw.get('scopes', {}))}})


def _disk_free(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


def action_whoami(req, cfg):
    """
    whoami: 查询当前 token 的身份/权限/配额 (AI 启动时自查边界)
    返回: token_name, scope, permissions, available_scopes, is_admin, exec_enabled, rate_limit
    """
    scope_cfg = cfg.get('_scope_cfg', {}) or {}
    # 对齐 PHP: available_actions 只列当前 token 权限位真正可调的 action, 不能把 admin 专属
    # 也列给普通 token —— AI 照单去调只会收到 1003, 白白浪费往返。
    my_perms = cfg.get('PERMISSIONS', []) or []
    available_actions = sorted(act for act, perm in ACTION_PERMS.items() if perm in my_perms)
    caps = {
        'batch_deploy': 'batch_deploy' in available_actions,
        'chunked_upload': 'chunked_upload' in available_actions,
        'php_run': ('php_run' in available_actions) and bool(cfg.get('EXEC_ENABLED', False)),
        'webhook_test': 'webhook_test' in available_actions,
        'sync_check': 'sync_check' in available_actions,
        'recent_changes': 'recent_changes' in available_actions,
        'error_log': 'error_log' in available_actions,
        'backup': 'backup_rb' in available_actions,
        # 链接协议: type=symlink + link_target/link_target_b64, 见 help name=upload/stat/list
        'symlink': True,
        'symlink_b64': True,
    }
    return ok({
        'token_name': cfg.get('TOKEN_NAME', ''),
        'server_time': int(time.time()),
        'scope': cfg.get('SCOPE', ''),
        'is_admin': cfg.get('IS_ADMIN', False),
        'permissions': cfg.get('PERMISSIONS', []),
        'available_scopes': cfg.get('AVAILABLE_SCOPES', [cfg.get('SCOPE', '')]),
        'work_dir': cfg['WORK_DIR'],
        'exec_enabled': cfg.get('EXEC_ENABLED', False),
        'api_version': cfg.get('API_VERSION', '4.3'),
        'max_size': cfg.get('MAX_SIZE', 64 * 1024 * 1024),
        'chunk_size_default': cfg.get('CHUNK_SIZE_DEFAULT', 4 * 1024 * 1024),
        'max_chunked_size': cfg.get('MAX_CHUNKED_SIZE', 1024 * 1024 * 1024),
        'chunk_ttl_days': cfg.get('CHUNK_TTL_DAYS', 7),
        'backup_keep': cfg.get('BACKUP_KEEP', 5),
        'server_timezone': 'UTC',
        'max_files_per_deploy': cfg.get('MAX_FILES_PER_DEPLOY', 50),
        'rate_limit': cfg.get('RATE_LIMIT', {'enabled': True, 'per_token_per_minute': 60,
                                             'per_ip_per_second': 10, 'upload_per_minute': 30}),
        'capabilities': caps,
        'available_actions': available_actions,
        # 客户端自动升级: 服务端参考 sync.py 的 md5/size/mtime (对齐 PHP whoami)
        'client_md5': _client_meta()['md5'],
        'client_size': _client_meta()['size'],
        'client_updated_at': _client_meta()['mtime'],
        'workflow_hint': '改代码 → sync_check 判断需不需要推 → upload/batch_deploy → php_run 验证 → error_log 排查',
    })


def action_sync_check(req, cfg):
    """
    sync_check: 一体化同步决策 (AI 一次调用判断该不该推)
    参数: files [{path, md5, size, mtime}], 可选 base_md5 (乐观锁, 期望远程当前 md5)
    返回: results [{path, remote_exists, remote_md5, should_push, reason, conflict}]
      reason: same/remote_missing/local_newer/remote_changed/conflict/size_diff
      conflict=true 表示远程已变更且本地也变 (需 backup_both)
    """
    # 对齐 PHP: files 数组 -> 本地 vs 远程 差异比对
    files = req.get('files') or []
    if isinstance(files, dict):
        files = list(files.values())  # 对齐 PHP: JSON 对象即数组
    if not isinstance(files, list) or not files:
        return fail(E['INVALID_PARAM'], 'missing_files', 'files 数组必填')
    base = cfg['WORK_DIR'].rstrip('/\\') + os.sep

    def md5_file(p):
        h = hashlib.md5()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()

    results = []
    for f in files:
        if not isinstance(f, dict):
            return fail(E['INVALID_PARAM'], 'bad_files_item',
                        'files 的每一项都必须是对象 ({path,size,md5,...})')
        rel = f.get('path', '')
        local_md5 = f.get('md5', '')
        if local_md5 is None:
            local_md5 = ''
        elif isinstance(local_md5, dict) and not local_md5:
            # PHP json_decode(..., true) 把 {} 解成 [], 回显时 JSON 为 []
            local_md5 = []
        local_size = _php_intval(f.get('size', 0))
        local_mtime = _php_intval(f.get('mtime', 0))
        expect_md5 = f.get('base_md5', '')
        item = {'path': rel, 'local_md5': local_md5, 'local_size': local_size}
        # 不跟随最后一级: 链接本身参与 sync_check (md5(规范化目标)).
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            item.update({'remote_exists': False, 'should_push': True, 'reason': 'invalid_path', 'conflict': False})
            results.append(item)
            continue
        if not os.path.islink(dest) and not os.path.exists(dest):
            # 与 PHP 对齐: 远程缺失时不输出 remote_mtime 字段 (PHP 该分支只有 5 个键)
            item.update({'remote_exists': False, 'remote_md5': '',
                         'should_push': True, 'reason': 'remote_missing', 'conflict': False})
            results.append(item)
            continue
        remote_md5 = _node_md5(dest, cfg['WORK_DIR'])
        try:
            remote_mtime = int(os.lstat(dest).st_mtime)
        except OSError:
            remote_mtime = 0
        item.update({'remote_exists': True, 'remote_md5': remote_md5,
                     'remote_mtime': remote_mtime,
                     'remote_type': ('symlink' if os.path.islink(dest)
                                     else ('dir' if os.path.isdir(dest) else 'file'))})
        if expect_md5 and expect_md5 != remote_md5:
            item.update({'should_push': False, 'reason': 'conflict', 'conflict': True,
                         'hint': '远程已被其他人修改, 建议 pull 后合并再 push, 或强制 push (放弃 base_md5)'})
            results.append(item)
            continue
        if local_md5 and remote_md5 == local_md5:
            item.update({'should_push': False, 'reason': 'same', 'conflict': False})
            results.append(item)
            continue
        if local_mtime > remote_mtime + 1:
            item.update({'should_push': True, 'reason': 'local_newer', 'conflict': False})
            results.append(item)
            continue
        if remote_mtime > local_mtime + 1:
            item.update({'should_push': False, 'reason': 'remote_changed', 'conflict': True,
                         'hint': '远程较新, 建议 pull 拉取后再决策'})
            results.append(item)
            continue
        item.update({'should_push': True, 'reason': 'size_diff', 'conflict': False})
        results.append(item)
    push_count = sum(1 for r in results if r.get('should_push'))
    conflict_count = sum(1 for r in results if r.get('conflict'))
    return ok({'results': results, 'total': len(results),
               'should_push_count': push_count, 'conflict_count': conflict_count,
               'summary': '%d 个需推送, %d 个冲突' % (push_count, conflict_count)})


def action_recent_changes(req, cfg):
    # 基于审计日志推断近期变更 (对齐 PHP 结构)
    log_file = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['AUDIT_LOG'])
    if not os.path.isfile(log_file):
        return ok({'changes': [], 'total': 0, 'success': 0, 'failed': 0,
                   'by_path': {}, 'by_action': {}, 'summary': '无审计日志'})
    lines = max(1, min(2000, _php_intval(req.get('lines', 200))))
    scope_name = req.get('scope', cfg.get('SCOPE', ''))
    if scope_name != cfg.get('SCOPE', ''):
        if not cfg.get('IS_ADMIN'):
            audit_log('recent_changes', req, E['FORBIDDEN'],
                      {'reason': 'cross_scope_denied', 'requested_scope': scope_name})
            return fail(E['FORBIDDEN'], 'scope_forbidden', "Token has no access to scope '%s'" % scope_name)
    write_actions = ['upload', 'upload_batch', 'delete', 'rename', 'copy', 'mkdir',
                    'chunked_upload', 'backup_rb', 'php_run']
    changes = []
    by_path = {}
    by_action = {}
    for line in _read_tail_lines(log_file, lines):
        try:
            j = json.loads(line)
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        action = j.get('action', '')
        if action not in write_actions:
            continue
        path = j.get('path', '')
        # 历史日志里可能存在非字符串 path (客户端传 int/list), 读侧兜底成字符串, 否则 dict key 报 unhashable
        if not isinstance(path, str):
            path = str(path) if isinstance(path, (int, float)) and not isinstance(path, bool) \
                else json.dumps(path, ensure_ascii=False, separators=(',', ':'))
        if path == '' and j.get('from'):
            path = '%s → %s' % (j.get('from', ''), j.get('to', ''))
        code = int(j.get('code', 0) or 0)
        ch = {
            'ts': j.get('ts', ''),
            'action': action,
            'path': path,
            'code': code,
            'ok': code == 0,
            'ip': j.get('ip', ''),
            'size': j.get('size'),
            'md5': j.get('md5'),
            'error_line': j.get('error_line'),
        }
        changes.append(ch)
        by_path[path] = by_path.get(path, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1
    changes.sort(key=lambda c: c['ts'], reverse=True)
    ok_count = sum(1 for c in changes if c['ok'])
    return ok({
        'changes': changes,
        'total': len(changes),
        'success': ok_count,
        'failed': len(changes) - ok_count,
        'by_path': by_path,
        'by_action': by_action,
        'summary': '%d 次变更 (成功 %d 失败 %d)' % (len(changes), ok_count, len(changes) - ok_count),
    })


# ==================== chunked_upload (简化但协议兼容) ====================
def _cleanup_expired_chunks(chunk_dir, ttl_days):
    """惰性回收过期分片。
    分片上传若中途断开 (网络断/进程被杀), 已 init 的 chunk 目录与 meta 会永久残留,
    磁盘占用只增不减。这里按 TTL 回收 (默认 7 天), 由 chunked_upload 触发并节流 (1h 一次)。"""
    ttl = max(1, int(ttl_days or 7)) * 86400
    now = time.time()
    removed = 0
    try:
        names = os.listdir(chunk_dir)
    except OSError:
        return 0
    for name in names:
        if not name.startswith('cu_'):
            continue
        p = os.path.join(chunk_dir, name)
        try:
            if name.endswith('.meta.json'):
                uid = name[:-len('.meta.json')]
                d = os.path.join(chunk_dir, uid)
                # 孤立 meta (目录已不存在) 且过期 -> 删除
                if not os.path.isdir(d) and (now - os.path.getmtime(p)) > ttl:
                    os.remove(p); removed += 1
            elif os.path.isdir(p) and (now - os.path.getmtime(p)) > ttl:
                shutil.rmtree(p, ignore_errors=True)
                meta = p + '.meta.json'
                if os.path.isfile(meta):
                    try:
                        os.remove(meta)
                    except OSError:
                        pass
                removed += 1
        except OSError:
            pass
    return removed


def action_chunked_upload(req, cfg):
    # 与 PHP 对齐: op 无默认值, 空/缺失/未知都给 1011 bad_op (原实现默认 init, 漏传 op 会静默建会话)
    op = req.get('op', '')
    if not isinstance(op, str):
        # 对齐 PHP (string) 强转语义: true->'1', []->'Array'
        op = _php_str(op)
    if op not in ('init', 'put', 'assemble', 'status', 'abort'):
        return fail(E['INVALID_PARAM'], 'bad_op',
                    "op must be init/put/assemble/status/abort, got '%s'" % op)
    chunk_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['CHUNK_DIR'])
    if not chunk_dir.endswith('/'):
        chunk_dir += '/'
    os.makedirs(chunk_dir, exist_ok=True)

    def _owner_hash(token):
        return hashlib.sha256((cfg.get('TOKEN', '') + (token or '')).encode()).hexdigest()[:12]

    if op == 'init':
        rel = req.get('path', '')
        # 不跟随最后一级: 目标是链接时, assemble 要替换链接本身.
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            return bad_path(rel)
        # 与 upload 一致: 分片上传的目标是文件, path 不能以 / 结尾;
        # 目标已是目录时也应在 init 阶段拒绝, 而不是等 assemble 到最后一步才报错。
        if isinstance(rel, str) and re.search(r'[/\\]+$', rel):
            return fail(E['INVALID_PARAM'], 'bad_path',
                        "chunked_upload 的 path 不能以 / 结尾 (它是文件路径): '%s'" % rel,
                        {'hint': '要写入目录请给出完整文件名, 如 "dir/big.bin"; 要创建目录请用 mkdir。'})
        if os.path.isdir(dest) and not os.path.islink(dest):
            return fail(E['INVALID_PARAM'], 'is_dir',
                        "Path '%s' 是目录, 不能作为分片上传目标" % rel,
                        {'hint': 'chunked_upload 的目标必须是文件路径; 目录请换一个不冲突的文件名。'})
        if not os.path.islink(dest):
            sk = _special_kind(dest)
            if sk is not None:
                return fail(E['INVALID_PARAM'], 'dest_is_special',
                            "目标 '%s' 已存在且是特殊文件 (%s), 拒绝覆盖" % (rel, sk),
                            {'path': rel, 'special_type': sk,
                             'hint': '先 delete 删除该节点, 再重新 init/assemble 上传。'})
        # mode 省略/null 时保留目标原有权限 (与 upload/upload_batch 一致);
        # 真正落盘模式在 assemble 时决定 (目标可能在 init 后被修改).
        mode_req = req.get('mode')
        has_mode = mode_req not in (None, '')
        if has_mode and not valid_mode_input(mode_req):
            return bad_mode(mode_req)
        mtime_req = req.get('mtime')
        if mtime_req not in (None, '') and not valid_mtime_input(mtime_req):
            return bad_mtime(mtime_req)
        mtime_val = None if mtime_req in (None, '') else int(mtime_req)
        # 与 PHP `??` 语义一致: size=0 时不能悄悄回退到 total_size
        total = req.get('size')
        if total is None:
            total = req.get('total_size')
        if total is None:
            total = req.get('total')
        if total is None:
            total = 0
        total = _php_intval(total)
        if total <= 0:
            return fail(E['INVALID_PARAM'], 'invalid_size',
                        'total_size/size 必须是正整数 (字节数), 收到 %d' % total,
                        {'hint': 'init 时必须给出文件总字节数, 如 "total_size": 10485760; 空文件请直接用 upload, '
                                 '路径以 / 结尾或目标为目录也会被拒绝。'})
        if total > cfg.get('MAX_CHUNKED_SIZE', 1024 ** 3):
            return fail(E['FILE_TOO_LARGE'], 'too_large',
                        'Size %d exceeds MAX_CHUNKED_SIZE (%d)' % (total, cfg.get('MAX_CHUNKED_SIZE', 1024 ** 3)),
                        {'total_size': total,
                         'max_chunked_size': cfg.get('MAX_CHUNKED_SIZE', 1024 ** 3),
                         'hint': '超过上限的文件无法分片上传; 联系管理员调整 scope 的 max_chunked_size, '
                                 '或先压缩/拆分后分别上传。'})
        chunk_size_raw = req.get('chunk_size')
        chunk_size = _php_intval(chunk_size_raw) if chunk_size_raw not in (None, '') else cfg.get('CHUNK_SIZE_DEFAULT', 4 * 1024 * 1024)
        if chunk_size <= 0:
            return fail(E['INVALID_PARAM'], 'bad_chunk_size', 'chunk_size must be > 0')
        chunk_count = int(ceil(total / chunk_size))
        if chunk_count > 100000:
            return fail(E['INVALID_PARAM'], 'too_many_chunks',
                        'chunk_count %d 超过上限 100000, 请增大 chunk_size' % chunk_count,
                        {'chunk_count': chunk_count, 'limit': 100000})
        import secrets
        uid = 'cu_' + secrets.token_hex(6)
        meta = {
            'upload_id': uid, 'path': rel, 'dest': dest, 'total_size': total,
            'chunk_size': chunk_size, 'sha256': req.get('sha256', ''),
            'mode': mode_req if has_mode else None, 'mtime': mtime_val,
            'created_at': int(time.time()),
            'token_hash': _owner_hash(req.get('token')), 'chunk_count': chunk_count,
        }
        with open(os.path.join(chunk_dir, uid + '.meta.json'), 'w') as f:
            json.dump(meta, f)
        os.makedirs(os.path.join(chunk_dir, uid), exist_ok=True)
        return ok({'upload_id': uid, 'total_chunks': chunk_count, 'chunk_count': chunk_count,
                   'chunk_size': chunk_size})

    uid = req.get('upload_id', '')
    if not isinstance(uid, str) or not re.match(r'^cu_[0-9a-f]{12}$', uid):
        return fail(E['INVALID_PARAM'], 'bad_upload_id', "upload_id 格式非法 (需 cu_+12 hex)")
    meta_path = os.path.join(chunk_dir, uid + '.meta.json')
    if not os.path.isfile(meta_path):
        return fail(E['NOT_FOUND'], 'upload_not_found', "upload_id '%s' not found" % uid)
    try:
        with open(meta_path) as _mf:
            meta = json.load(_mf)
    except Exception:
        return fail(E['INTERNAL'], 'meta_corrupted', "meta 文件损坏或字段缺失: %s" % uid)
    if not isinstance(meta, dict) or any(_k not in meta for _k in ('dest', 'path', 'total_size', 'chunk_size')):
        return fail(E['INTERNAL'], 'meta_corrupted', "meta 文件损坏或字段缺失: %s" % uid)
    if _owner_hash(req.get('token')) != meta.get('token_hash'):
        return fail(E['FORBIDDEN'], 'token_mismatch', 'Token does not match the upload owner')
    chunk_count = meta.get('chunk_count', int(ceil(meta['total_size'] / meta['chunk_size'])))

    if op == 'put':
        # 缺失/null 与类型错误分开报: 旧实现 intval("abc")=0, 会把内容静默写到第 0 片,
        # 最终 assemble 只报 size_mismatch, AI 完全看不出根因.
        _missing = object()
        idx_raw = req.get('chunk_index', _missing)
        if idx_raw is _missing or idx_raw is None:
            return fail(E['INVALID_PARAM'], 'missing_chunk_index',
                        "Required field 'chunk_index' is missing (received fields: %s)" % _brief_keys(req),
                        {'hint': '分片从 0 开始, 第 i 片传 "chunk_index": i; 可用 status 查看 missing 列表。'})
        _idx_ok = (isinstance(idx_raw, int) and not isinstance(idx_raw, bool)) \
            or (isinstance(idx_raw, float) and idx_raw.is_integer()) \
            or (isinstance(idx_raw, str) and re.match(r'^\s*[+-]?[0-9]+\s*$', idx_raw) is not None)
        if not _idx_ok:
            return fail(E['INVALID_PARAM'], 'bad_chunk_index_type',
                        'chunk_index 必须是整数 (0 起的片序号), 收到 %s' % _php_type_name(idx_raw),
                        {'received': idx_raw if isinstance(idx_raw, (int, float, str)) else _php_type_name(idx_raw),
                         'hint': '不要传 true/false、小数 (1.9) 或非数字字符串; 请传 "chunk_index": 0, 1, 2 ...'})
        idx = int(idx_raw)
        if idx < 0 or idx >= chunk_count:
            return fail(E['INVALID_PARAM'], 'bad_chunk_index', 'chunk_index %d 超出范围 [0, %d)' % (idx, chunk_count))
        if 'content_b64' not in req or req.get('content_b64') is None:
            return fail(E['INVALID_PARAM'], 'missing_content_b64',
                        "Required field 'content_b64' is missing (received fields: %s)" % _brief_keys(req),
                        {'hint': 'put 必须带该分片的 base64 内容; 空分片不可能出现在合法上传中。'})
        if not isinstance(req.get('content_b64'), str):
            return fail(E['INVALID_PARAM'], 'content_b64_not_string',
                        'content_b64 must be a base64 string, got %s' % _php_type_name(req.get('content_b64')),
                        {'hint': 'content_b64 必须是字符串; 二进制先做 base64 编码。'})
        data = _b64_decode_php(req.get('content_b64', ''))
        if data is None:
            return fail(E['INVALID_PARAM'], 'bad_base64', 'chunk decode failed')
        is_last = (idx == chunk_count - 1)
        if len(data) > meta['chunk_size']:
            return fail(E['INVALID_PARAM'], 'chunk_too_large', 'chunk %d size %d > chunk_size %d' % (idx, len(data), meta['chunk_size']))
        if not is_last and len(data) != meta['chunk_size']:
            return fail(E['INVALID_PARAM'], 'chunk_size_mismatch', '非末片 chunk %d size %d != chunk_size %d' % (idx, len(data), meta['chunk_size']))
        chunk_md5 = hashlib.md5(data).hexdigest()
        req_md5 = req.get('md5') or req.get('chunk_md5') or ''
        if req_md5 and req_md5 != chunk_md5:
            return fail(E['CHECKSUM_MISMATCH'], 'chunk_md5_mismatch', 'Chunk %d md5 mismatch' % idx)
        chk_path = os.path.join(chunk_dir, uid, '%05d' % idx)
        os.makedirs(os.path.dirname(chk_path), exist_ok=True)
        with open(chk_path, 'wb') as f:
            f.write(data)
        return ok({'upload_id': uid, 'chunk_index': idx, 'md5': chunk_md5, 'size': len(data)})

    if op == 'status':
        # 对齐 PHP: received/missing 为索引列表, 另有 progress/state/created_at/ttl_seconds
        received, missing = [], []
        for i in range(chunk_count):
            if os.path.isfile(os.path.join(chunk_dir, uid, '%05d' % i)):
                received.append(i)
            else:
                missing.append(i)
        created = int(meta.get('created_at', 0) or 0)
        ttl_days = int(cfg.get('CHUNK_TTL_DAYS', 7) or 7)
        return ok({
            'upload_id': uid,
            'path': meta.get('path', ''),
            'total_size': meta['total_size'],
            'received': received,
            'missing': missing,
            # PHP round(x,1) 对整数结果 json_encode 为 0 而非 0.0; 对齐输出类型.
            'progress': (lambda _p: int(_p) if _p == int(_p) else _p)(
                round(len(received) / chunk_count * 100, 1) if chunk_count else 0),
            'state': 'uploading',
            'created_at': created,
            'ttl_seconds': max(0, ttl_days * 86400 - (int(time.time()) - created)),
        })

    if op == 'assemble':
        missing = [i for i in range(chunk_count) if not os.path.isfile(os.path.join(chunk_dir, uid, '%05d' % i))]
        if missing:
            return fail(E['CHUNK_MISSING'], 'chunks_missing', 'Missing %d chunks' % len(missing), {'missing': missing})
        rel = meta['path']
        # 重新按 nofollow 解析: init 时保存的绝对路径可能因父目录改名/链接替换而失效,
        # 且必须以"链接本身"为落点 (旧实现复用 safe_path 的结果会写穿目标).
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            return bad_path(rel)
        # init 之后目标可能被其他请求改成了目录 (TOCTOU): 这里再确认一次,
        # 否则 os.replace(tmp, dir) 只会报笼统的 assemble_fail。
        if os.path.isdir(dest) and not os.path.islink(dest):
            return fail(E['INVALID_PARAM'], 'is_dir',
                        "Path '%s' 现在是目录, 无法写入文件" % rel,
                        {'hint': '目标路径已被改成目录; 换一个文件名重新 init 上传。'})
        if not os.path.islink(dest):
            sk = _special_kind(dest)
            if sk is not None:
                return fail(E['INVALID_PARAM'], 'dest_is_special',
                            "目标 '%s' 现在是特殊文件 (%s), 拒绝覆盖" % (rel, sk),
                            {'path': rel, 'special_type': sk,
                             'hint': '先 delete 删除该节点, 再重新 init/assemble 上传。'})
        backed_up = ''
        if (os.path.islink(dest) or os.path.exists(dest)) and cfg.get('BACKUP_KEEP', 0) > 0:
            backed_up = _create_link_backup(dest, rel, cfg) if os.path.islink(dest) \
                else _create_backup(dest, rel, cfg)
        parent = os.path.dirname(dest)
        try:
            os.makedirs(parent, 0o755, exist_ok=True)
        except OSError:
            pass
        if not os.path.isdir(parent):
            # 父级被同名文件占用/无权限: 与 PHP fopen(tmp_dest) 失败一致, 报 1008 open_fail
            return fail(E['UPLOAD_FAILED'], 'open_fail', 'Cannot open temp destination')
        tmp_dest = dest + '.chunk_tmp.' + os.urandom(4).hex()
        try:
            with open(tmp_dest, 'wb') as out:
                for i in range(chunk_count):
                    with open(os.path.join(chunk_dir, uid, '%05d' % i), 'rb') as inp:
                        shutil.copyfileobj(inp, out)
            # mode 省略时保留目标现有权限; 新文件默认 0644
            mode_meta = meta.get('mode')
            if mode_meta in (None, ''):
                try:
                    final_mode = ((os.stat(dest).st_mode & 0o777)
                                  if os.path.exists(dest) and not os.path.islink(dest) else 0o644)
                except OSError:
                    final_mode = 0o644
            else:
                final_mode = safe_mode(mode_meta)
            if final_mode <= 0:
                final_mode = 0o644
            os.chmod(tmp_dest, final_mode)
            actual_size = os.path.getsize(tmp_dest)
            # 流式摘要: 拼接后可能上 GB, 不能用 read() 一次性读进内存 (设备上会 OOM)
            _m5, _s256 = hashlib.md5(), hashlib.sha256()
            with open(tmp_dest, 'rb') as _fh:
                while True:
                    _blk = _fh.read(1024 * 1024)
                    if not _blk:
                        break
                    _m5.update(_blk)
                    _s256.update(_blk)
            actual_md5 = _m5.hexdigest()
            final_md5 = req.get('md5') or req.get('final_md5') or ''
            if final_md5 and final_md5 != actual_md5:
                os.remove(tmp_dest)
                return fail(E['CHECKSUM_MISMATCH'], 'md5_mismatch',
                            'Final MD5 mismatch. 原文件未受影响 (%s)' % ('kept_original' if backed_up else 'no_original'),
                            {'expected_md5': final_md5, 'actual_md5': actual_md5})
            total = int(meta.get('total_size', 0) or 0)
            if actual_size != total:
                os.remove(tmp_dest)
                return fail(E['CHECKSUM_MISMATCH'], 'size_mismatch',
                            'Size %d != %d. 原文件未受影响 (%s)'
                            % (actual_size, total, 'kept_original' if backed_up else 'no_original'))
            sha256_expected = meta.get('sha256') or ''
            if sha256_expected and sha256_expected != _s256.hexdigest():
                os.remove(tmp_dest)
                return fail(E['CHECKSUM_MISMATCH'], 'sha256_mismatch',
                            'SHA256 mismatch. 原文件未受影响',
                            {'expected_sha256': sha256_expected, 'actual_sha256': _s256.hexdigest()})
            os.replace(tmp_dest, dest)
            if meta.get('mtime') is not None:
                try:
                    os.utime(dest, (int(meta['mtime']), int(meta['mtime'])))
                except (OSError, TypeError, ValueError):
                    pass
        except OSError as e:
            try: os.remove(tmp_dest)
            except OSError: pass
            return fail(E['INTERNAL'], 'assemble_fail', str(e))
        shutil.rmtree(os.path.join(chunk_dir, uid), ignore_errors=True)
        try: os.remove(meta_path)
        except OSError: pass
        return ok({'upload_id': uid, 'path': rel, 'size': actual_size, 'md5': actual_md5,
                   'mode': '%04o' % final_mode,
                   'mtime': int(os.path.getmtime(dest)) if os.path.exists(dest) else 0,
                   'backed_up': backed_up})

    if op == 'abort':
        shutil.rmtree(os.path.join(chunk_dir, uid), ignore_errors=True)
        try: os.remove(meta_path)
        except OSError: pass
        return ok({'aborted': True, 'upload_id': uid})
    return fail(E['INVALID_PARAM'], 'bad_op', 'unknown op %s' % op)


def _bd_rollback_one(rel, dest, backup_root):
    """对齐 PHP _rollback_from_backup: 新建文件无备份时删除, 返回 {ok, restored_from|error}。"""
    fbd = os.path.join(backup_root, os.path.dirname(rel), os.path.basename(rel))
    cands = glob.glob(os.path.join(fbd, os.path.basename(rel) + '.bak.*')) if os.path.isdir(fbd) else []
    if not cands:
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        return {'ok': True, 'restored_from': 'deleted_new_file'}
    try:
        best = max(cands, key=lambda p: os.path.getmtime(p))
    except OSError:
        best = cands[-1]
    rtype = _restore_backup_to_dest(best, dest)
    if rtype is None:
        return {'ok': False, 'error': 'restore_fail'}
    return {'ok': True, 'restored_from': os.path.basename(best), 'type': rtype}


# ==================== batch_deploy (简化但兼容) ====================
def action_batch_deploy(req, cfg):
    # FIX-v4.34 (P0 双端不一致): 契约(HELP_SPEC)与示例声明的参数名是 **items**,
    #   而 PHP 端历史上只认 files → 同一份照契约写的请求双端结果不同。
    #   统一以契约名 items 为主, files 保留为历史别名(客户端旧版本在用)。
    #   空/缺失一律显式报错 —— 旧版返回 ok:true+total:0, AI 会误以为批量上传成功了。
    raw_items = None
    if 'items' in req:
        raw_items = req.get('items')
    elif 'files' in req:
        raw_items = req.get('files')
    if raw_items is None:
        return fail(E['INVALID_PARAM'], 'missing_items', 'batch_deploy 缺少必填参数',
                    {'hint': '必须传 items 数组 (每项 {path, content_b64}); files 为历史别名仍兼容。'
                             '契约名与示例请以 help name=batch_deploy 为准。',
                     'received_fields': _brief_keys(req)})
    if isinstance(raw_items, dict):
        # 对齐 PHP: json_decode(..., true) 下 {} 与 [] 等价, 非空对象按键值展开
        raw_items = list(raw_items.values())
    if not isinstance(raw_items, list):
        return fail(E['INVALID_PARAM'], 'bad_items', 'items 必须是数组 ({path, content_b64} 的列表)',
                    {'hint': '正确形态: {"items":[{"path":"a.php","content_b64":"PD9waHA="}]}'})
    if not raw_items:
        return fail(E['INVALID_PARAM'], 'empty_items', 'items 数组不能为空 (没有要部署的文件)',
                    {'hint': 'items 至少包含 1 项; 若只部署单个文件可用 upload action。'})
    items = raw_items
    max_files = cfg.get('MAX_FILES_PER_DEPLOY', 50)
    max_files = max(1, _php_intval(max_files))
    if len(items) > max_files:
        return fail(E['INVALID_PARAM'], 'too_many_files',
                    '单次部署最多 %d 个文件, 收到 %d 个' % (max_files, len(items)),
                    {'limit': max_files, 'received': len(items),
                     'hint': '请拆分成多次 batch_deploy (每次 <= %d 个); '
                             '上限由 scope 配置 max_files_per_deploy 决定, 可用 whoami 查看。' % max_files})
    # --- Phase 0: 目标路径冲突预检 (零写入) ---
    # 与 PHP 对齐: 重复 path / 别名 (./a、/a)、目录或 work_dir 根本身、
    #   以及同一批次中 "a" 与 "a/b" 这类父子冲突, 一律在写临时文件前返回 1011.
    #   旧实现重复项 Python file_count=2 / PHP=1 (last-wins), 目录目标拖到 Phase3
    #   rename 失败才报 1099, AI 无法从错误里知道该怎么改.
    real_work_root = os.path.realpath(cfg['WORK_DIR'].rstrip('/\\'))
    norm_work_root = _lexical_norm_abs(real_work_root) if real_work_root else None
    seen_dest = {}
    dup_pairs = []
    dir_targets = []
    root_targets = []
    dot_targets = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rel = it.get('path', '')
        if not isinstance(rel, str) or rel == '':
            continue
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            continue  # 具体原因 (含审计) 由 Phase1 统一报告
        if os.path.basename(dest) == '.':
            dot_targets.append(rel)
            continue
        dest_norm = _lexical_norm_abs(dest)
        if dest_norm is None:
            continue
        if norm_work_root is not None and dest_norm == norm_work_root:
            root_targets.append(rel)
            continue
        if os.path.isdir(dest) and not os.path.islink(dest):
            dir_targets.append(rel)
            continue
        if dest_norm in seen_dest:
            dup_pairs.append({'path': seen_dest[dest_norm], 'duplicate': rel})
            continue
        seen_dest[dest_norm] = rel
    if root_targets:
        return fail(E['INVALID_PARAM'], 'root_deploy_not_allowed',
                    '部署目标是 scope 工作目录根: ' + ', '.join(root_targets),
                    {'paths': root_targets,
                     'hint': 'batch_deploy 的 path 必须指向 work_dir 内的文件; "." "/" "./" 都指向工作目录本身, 不能作为部署目标。'})
    if dot_targets:
        return fail(E['INVALID_PARAM'], 'bad_dest_path',
                    '部署目标必须以文件名结尾, 不能是目录路径: ' + ', '.join(dot_targets),
                    {'paths': dot_targets,
                     'hint': '形如 "dir/." 的 path 等价于目录 dir 本身; 请给出具体文件名, 如 "dir/app.php"。'})
    if dir_targets:
        return fail(E['INVALID_PARAM'], 'dest_is_dir',
                    '部署目标是已存在的目录, 不能作为文件覆盖: ' + ', '.join(dir_targets),
                    {'paths': dir_targets,
                     'hint': 'path 必须指向文件; 要写入目录内请带上文件名 (如 "conf/app.json"); 删除/重命名目录请用 delete/rename。'})
    if dup_pairs:
        return fail(E['INVALID_PARAM'], 'duplicate_path',
                    '同一批次中多个条目指向同一目标路径',
                    {'duplicates': dup_pairs,
                     'hint': '每个目标路径在单次 batch_deploy 中只能出现一次; "./a"、"a"、"/a" 等别名视为同一路径。请合并成一条后重试。'})
    conflict_pairs = []
    dest_keys = list(seen_dest.keys())
    for ai in range(len(dest_keys)):
        for bi in range(ai + 1, len(dest_keys)):
            a, b = dest_keys[ai], dest_keys[bi]
            if (b + '/').startswith(a + '/') or (a + '/').startswith(b + '/'):
                conflict_pairs.append({'path': seen_dest[a], 'conflicts_with': seen_dest[b]})
    if conflict_pairs:
        return fail(E['INVALID_PARAM'], 'conflicting_paths',
                    '同一批次中的路径互相冲突: 一个条目是文件, 另一个条目又需要它是目录',
                    {'conflicts': conflict_pairs,
                     'hint': '例如同时部署 "a" 和 "a/b.php" 无法同时成立; 请改用不冲突的路径, 或拆成两次部署并先建好目录结构。'})
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + os.urandom(4).hex()
    deploy_id = ts
    tmp_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), '.ai-deploy-tmp', deploy_id)
    os.makedirs(tmp_dir, exist_ok=True)
    files, sizes, backed_up = [], {}, {}
    staged_map = {}
    staged_mode = {}
    staged_mtime = {}
    phase1_errors = []
    pre_check = req.get('pre_check') or ''
    post_check = req.get('post_check') or ''
    for i, item in enumerate(items):
        # 与 PHP 一致: 单条非法只记录该条, 最后统一报 phase1_write_fail,
        # 不再因 items[0] 类型错误直接 1099/断连 (旧实现 item.get on int -> AttributeError)。
        if not isinstance(item, (dict, list)):
            phase1_errors.append('文件 #%d: 每项必须是对象 {path, content_b64}' % i)
            continue
        if not isinstance(item, dict):
            # PHP 里 JSON 数组也是 array, 其 ['path'] 不存在 → 报 path 为空
            phase1_errors.append('文件 #%d: path 为空或不是字符串' % i)
            continue
        rel = item.get('path', '')
        if not isinstance(rel, str) or rel == '':
            phase1_errors.append('文件 #%d: path 为空或不是字符串' % i)
            continue
        # 不跟随最后一级: 目标是链接时部署要替换链接本身.
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            reject_path(rel, 'batch_deploy_stage', {**req, 'deploy_id': deploy_id})
            phase1_errors.append('%s: 路径安全策略拦截 (path_traversal)' % rel)
            continue
        if 'content_b64' not in item or item.get('content_b64') is None:
            phase1_errors.append('%s: 缺少 content_b64 (收到字段: %s)' % (rel, _brief_keys(item)))
            continue
        if not isinstance(item.get('content_b64'), str):
            phase1_errors.append('%s: content_b64 必须是 base64 字符串' % rel)
            continue
        data = _b64_decode_php(item.get('content_b64', ''))
        if data is None:
            phase1_errors.append('%s: base64 解码失败' % rel)
            continue
        if len(data) > cfg.get('MAX_SIZE', 64 * 1024 * 1024):
            phase1_errors.append('%s: 文件过大 (%d > MAX_SIZE)' % (rel, len(data)))
            continue
        # mode 省略/null 时保留目标现有权限 (Phase 3 决定); 新文件默认 0644.
        mode_req = item.get('mode')
        has_mode = mode_req not in (None, '')
        if has_mode and not valid_mode_input(mode_req):
            phase1_errors.append('%s: mode 非法 (需 1-4 位八进制字符串, 如 0644)' % rel)
            continue
        mode_val = safe_mode(mode_req) if has_mode else None
        mtime_req = item.get('mtime')
        if mtime_req not in (None, ''):
            if not valid_mtime_input(mtime_req):
                phase1_errors.append('%s: mtime 非法 (需秒级 unix 整数时间戳)' % rel)
                continue
            mtime_val = int(mtime_req)
        else:
            mtime_val = None
        ext = os.path.splitext(rel)[1].lstrip('.') or 'bin'
        tmpf = os.path.join(tmp_dir, '%03d.%s' % (i, ext))
        try:
            with open(tmpf, 'wb') as f:
                f.write(data)
        except OSError:
            phase1_errors.append('%s: 临时文件写入失败' % rel)
            continue
        files.append(rel)
        sizes[rel] = len(data)
        staged_map[rel] = tmpf
        staged_mode[rel] = mode_val
        staged_mtime[rel] = mtime_val
    if phase1_errors:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        audit_log('batch_deploy', req, E['UPLOAD_FAILED'],
                  {'phase': 'phase1', 'deploy_id': deploy_id, 'errors': phase1_errors})
        return fail(E['UPLOAD_FAILED'], 'phase1_write_fail', '部分文件写入临时目录失败',
                    {'errors': phase1_errors, 'deploy_id': deploy_id, 'staged': len(files)})
    # Phase2: pre_check (仅当参数给定 + 仅接受枚举, 对齐 PHP)
    pre_results = {}
    if pre_check:
        check_type = 'php_lint' if pre_check == 'php -l {file}' else pre_check
        if check_type != 'php_lint':
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return fail(E['INVALID_PARAM'], 'bad_pre_check',
                        "pre_check 仅支持 'php_lint' 或空 (旧 'php -l {file}' 兼容). 拒绝执行任意命令, 防止注入.",
                        {'received': pre_check})
        php_bin = _find_php_cli() or 'php'
        for rel, tmpf in staged_map.items():
            if os.path.splitext(rel)[1].lstrip('.').lower() != 'php':
                pre_results[rel] = {'exit_code': 0, 'output': '(非 PHP 文件, 跳过)', 'passed': True}
                continue
            lo = subprocess.run([php_bin, '-l', tmpf], capture_output=True, text=True, timeout=30)
            outtxt = (lo.stdout + lo.stderr).strip()
            pre_results[rel] = {'cmd': 'php -l {file}', 'exit_code': lo.returncode,
                                'output': outtxt, 'passed': lo.returncode == 0}
        failed_checks = [r for r, v in pre_results.items() if not v['passed']]
        if failed_checks:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            audit_log('batch_deploy', req, E['INVALID_PARAM'],
                      {'phase': 'pre_check', 'deploy_id': deploy_id, 'failed': failed_checks})
            return fail(E['INVALID_PARAM'], 'pre_check_failed',
                        'pre_check 未通过, 已中止部署 (文件未变更)',
                        {'deploy_id': deploy_id, 'pre_check': pre_check,
                         'results': pre_results, 'failed': failed_checks})
    # Phase3: realpath 父目录校验(对齐 PHP) + 备份 + 原子替换 + 失败回滚
    rollback = _bool_flag(req.get('rollback_on_fail', True))
    if rollback is None:
        return bad_bool('rollback_on_fail', req.get('rollback_on_fail'))
    replace_errors = []
    replaced = {}
    real_work = os.path.realpath(cfg['WORK_DIR'].rstrip('/\\'))
    backup_root = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg['BACKUP_DIR']).rstrip('/')
    for i, item in enumerate(items):
        rel = item.get('path', '')
        # Phase 3 重新按 nofollow 解析, 防止 Phase 1 之后父目录/最后一级被替换.
        dest = safe_path_nofollow(rel, cfg['WORK_DIR'])
        if not dest:
            replace_errors.append('%s: 路径安全策略拦截 (path_traversal)' % rel)
            continue
        # Phase1 已预检; 这里防 Phase1→Phase3 期间目标被并发改成目录/根 (TOCTOU).
        dest_norm = _lexical_norm_abs(dest)
        if dest_norm is not None and norm_work_root is not None and dest_norm == norm_work_root:
            replace_errors.append('%s: 目标是 work_dir 根, 拒绝作为文件写入' % rel)
            continue
        if os.path.isdir(dest) and not os.path.islink(dest):
            replace_errors.append('%s: 目标是目录, 不能作为文件覆盖' % rel)
            continue
        parent = os.path.dirname(dest) or ''
        tmpf = os.path.join(tmp_dir, '%03d.%s' % (i, os.path.splitext(rel)[1].lstrip('.') or 'bin'))
        # FIX-v4.31 (P0): 目标父目录可能尚不存在 —— AI 首次部署新目录/新项目 (brand_new 场景) 是常态.
        #   旧逻辑要求 os.path.exists(parent), 否则一律按"路径逃逸"拦截 → 新目录部署 100% 失败且错误不可自愈.
        #   改为与 safe_path 一致的三级校验: ① 祖先前缀校验(零写入) ② 创建缺失中间目录 ③ 落盘后终校.
        anc = parent
        real_anc = ''
        while True:
            if anc and os.path.exists(anc):
                real_anc = os.path.realpath(anc)
                break
            up = os.path.dirname(anc)
            if up == anc or len(up) < len(real_work.rstrip(os.sep)):
                break
            anc = up
        if not anc or not (real_anc.rstrip(os.sep) + os.sep).startswith(real_work.rstrip(os.sep) + os.sep):
            replace_errors.append('%s: 路径安全策略拦截 (目标父目录 %s 解析后落在 work_dir 之外)' % (rel, parent))
            continue
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, 0o755)
            except OSError:
                replace_errors.append('%s: 目标目录创建失败 (权限不足或同名文件冲突): %s' % (rel, parent))
                continue
        real_parent = os.path.realpath(parent) if os.path.exists(parent) else ''
        if not (real_parent.rstrip(os.sep) + os.sep).startswith(real_work.rstrip(os.sep) + os.sep):
            replace_errors.append('%s: 路径安全策略拦截 (父目录创建后落在 work_dir 之外, 疑似符号链接逃逸)' % rel)
            continue
        if not os.path.islink(dest):
            _sk = _special_kind(dest)
            if _sk is not None:
                # FIFO/socket/设备节点无法备份且写入会阻塞; 明确拒绝并让回滚逻辑处理其他文件.
                replace_errors.append('%s: 目标是特殊文件 (%s), 拒绝覆盖' % (rel, _sk))
                continue
        old_dest_mode = None
        if os.path.exists(dest) and not os.path.islink(dest):
            try:
                old_dest_mode = os.stat(dest).st_mode & 0o777
            except OSError:
                old_dest_mode = None
        if (os.path.islink(dest) or os.path.exists(dest)) and cfg.get('BACKUP_KEEP', 0) > 0:
            bk = _create_link_backup(dest, rel, cfg) if os.path.islink(dest) \
                else _create_backup(dest, rel, cfg)
            if bk:
                backed_up[rel] = bk
        try:
            # mode 省略: 保留被替换文件的原权限; 新文件 0644
            _sm = staged_mode.get(rel)
            final_mode = _sm if _sm is not None else (old_dest_mode if old_dest_mode is not None else 0o644)
            if final_mode <= 0:
                final_mode = 0o644
            os.chmod(tmpf, final_mode)
            os.replace(tmpf, dest)
            _mt = staged_mtime.get(rel)
            if _mt is not None:
                try:
                    os.utime(dest, (_mt, _mt))
                except OSError:
                    pass
            replaced[rel] = dest
        except OSError:
            replace_errors.append('%s: 文件替换失败' % rel)
    if replace_errors:
        rolled = {}
        if rollback:
            for rel, dest in replaced.items():
                rolled[rel] = _bd_rollback_one(rel, dest, backup_root)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return fail(E['INTERNAL'], 'phase3_replace_fail',
                    '部分文件替换失败' + (', 已回滚已替换文件' if rollback and replaced else ''),
                    {'deploy_id': deploy_id, 'errors': replace_errors,
                     'replaced_count': len(replaced), 'rolled_back': rolled})
    # Phase4: post_check (HTTP 状态检查 或 shell 命令), 对齐 PHP
    post_result = None
    if post_check:
        post_expect = req.get('post_check_expect') or ''
        is_shell = re.match(r'^https?://', post_check.strip(), re.I) is None
        if is_shell:
            if not cfg.get('EXEC_ENABLED'):
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return fail(E['INVALID_PARAM'], 'bad_post_check',
                            'post_check shell 命令需要 exec 权限且 scope 开启 exec_enabled',
                            {'hint': 'HTTP(S) URL 检查无需 exec 权限; shell 命令需 exec 权限'})
            try:
                pr = _posix_shell_run("timeout -k 3 -s TERM 15 sh -c " + "'" + post_check.replace("'", "'\\''") + "' 2>&1",
                                    cwd=cfg['WORK_DIR'], capture_output=True, text=True, timeout=25)
                post_out = (pr.stdout + pr.stderr).strip()
                post_rc = pr.returncode
            except Exception:
                post_out = ''
                post_rc = -1
            post_passed = (post_expect in post_out) if post_expect else (post_rc == 0)
            post_result = {'url': 'shell', 'rc': post_rc, 'output': post_out, 'passed': post_passed}
        else:
            vok, verr = _webhook_validate_url(post_check, cfg)
            if not vok:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return fail(E['INVALID_PARAM'], 'bad_post_check',
                            "post_check URL 被安全策略拒绝: %s" % verr,
                            {'received': post_check, 'hint': 'post_check 仅允许公网 http/https URL, 防 SSRF'})
            http_code = 0
            curl_err = None
            try:
                rq = _urlreq.Request(post_check, method='HEAD')
                with _urlreq.urlopen(rq, timeout=10) as resp:
                    http_code = resp.status
            except Exception as e:
                curl_err = str(e)
            post_output = ("curl_error: %s" % curl_err) if curl_err else ("HTTP %d" % http_code)
            post_passed = (post_expect in str(http_code)) if post_expect else (200 <= http_code < 400)
            post_result = {'url': post_check, 'http_code': http_code, 'output': post_output, 'passed': post_passed}
        if not post_passed and rollback:
            rolled2 = {}
            for rel, dest in replaced.items():
                rolled2[rel] = _bd_rollback_one(rel, dest, backup_root)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            audit_log('batch_deploy', req, E['INTERNAL'],
                      {'phase': 'post_check_failed', 'deploy_id': deploy_id, 'post_check': post_result})
            return fail(E['INTERNAL'], 'post_check_failed', 'post_check 未通过, 已回滚全部文件',
                        {'deploy_id': deploy_id, 'post_check': post_result, 'rolled_back': rolled2})
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return ok({'deploy_id': deploy_id, 'files': files, 'sizes': sizes, 'backed_up': backed_up if backed_up else [],
               'pre_check': pre_results if pre_results else None, 'post_check': post_result,
               'rolled_back': False, 'file_count': len(files)})


# ==================== 补齐 PHP 独有 action (webhook/oplog/package) ====================
import hmac as _hmac
import ipaddress as _ipaddress
import zipfile as _zipfile
import urllib.request as _urlreq

def _human_size(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n < 1024:
        return '%d B' % n
    # 与 PHP round($x,1) 一致: ROUND_HALF_UP, 整数结果不带 .0 (如 15 KB 而非 15.0 KB)
    from decimal import Decimal, ROUND_HALF_UP
    if n < 1048576:
        d = (Decimal(n) / Decimal(1024)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        s = format(d, 'f').rstrip('0').rstrip('.')
        return s + ' KB'
    if n < 1073741824:
        d = (Decimal(n) / Decimal(1048576)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        s = format(d, 'f').rstrip('0').rstrip('.')
        return s + ' MB'
    d = (Decimal(n) / Decimal(1073741824)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    s = format(d, 'f').rstrip('0').rstrip('.')
    return s + ' GB'

def _webhook_dir(cfg):
    d = os.path.join(cfg['WORK_DIR'].rstrip('/'), (cfg.get('WEBHOOK_DIR') or 'ai-sync/webhooks/'))
    os.makedirs(d, exist_ok=True)
    return d

def _webhook_is_public_ip(ip):
    """对齐 PHP _webhook_is_public_ip(): 排除回环/私网/链路本地/未指定;
    不使用 is_reserved (PHP 8.3 会把不少公网段误判为保留)。"""
    try:
        a = _ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    # IPv4-mapped IPv6 (::ffff:x.x.x.x) 先提取再按 IPv4 判定
    if isinstance(a, _ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        return _webhook_is_public_ip(str(a.ipv4_mapped))
    if a.is_loopback or a.is_link_local or a.is_private or a.is_unspecified:
        return False
    return True


def _webhook_resolve_ips(host):
    """对齐 PHP gethostbynamel + dns_get_record(DNS_AAAA): 返回 A/AAAA 全部地址。"""
    ips = []
    try:
        for _fam, _typ, _proto, _canon, _sa in socket.getaddrinfo(
                host, None, proto=socket.IPPROTO_TCP):
            _ip = _sa[0]
            if _ip not in ips:
                ips.append(_ip)
    except Exception:
        pass
    return ips


def _webhook_resolve_public_ips(host):
    """对齐 PHP _webhook_resolve_public_ips(): 只保留公网地址。"""
    return [ip for ip in _webhook_resolve_ips(host) if _webhook_is_public_ip(ip)]


def _webhook_validate_url(url, cfg):
    if not isinstance(url, str) or not url:
        return False, 'Invalid URL: %s' % (url or '')
    try:
        p = urlparse(url)
    except Exception:
        return False, 'Invalid URL structure'
    if not p.scheme or not p.hostname:
        return False, 'Invalid URL: %s' % url
    if p.scheme.lower() not in ('http', 'https'):
        return False, "URL scheme '%s' not allowed (only: http,https)" % p.scheme
    if not cfg.get('WEBHOOK_ALLOW_INTERNAL'):
        try:
            ip = _ipaddress.ip_address(p.hostname)
            if not _webhook_is_public_ip(str(ip)):
                return False, "Private/loopback/link-local IP '%s' not allowed. Set WEBHOOK_ALLOW_INTERNAL=true to override." % p.hostname
        except ValueError:
            # 域名: 对齐 PHP 3b —— DNS 解析后逐 IP 检查, 任一内网记录即拒绝 (DNS 无记录时保守放行,
            # 由 webhook_test 的 dns_rebinding_blocked 兜底)
            for _rip in _webhook_resolve_ips(p.hostname):
                if not _webhook_is_public_ip(_rip):
                    return False, ("Resolved IP '%s' of host '%s' is internal/private/loopback; "
                                   "not allowed. Set WEBHOOK_ALLOW_INTERNAL=true if this is an internal host."
                                   % (_rip, p.hostname))
    return True, None

def _webhook_valid_id(wid):
    return isinstance(wid, str) and re.match(r'^wh_[0-9a-f]{10}$', wid) is not None

def action_webhook_set(req, cfg):
    url = req.get('url', '')
    vok, verr = _webhook_validate_url(url, cfg)
    if not vok:
        return fail(E['INVALID_PARAM'], 'bad_url', verr)
    events = req.get('events') or ['upload', 'delete']
    wid = 'wh_' + hashlib.md5((url + str(time.time()) + str(os.urandom(4))).encode()).hexdigest()[:10]
    rec = {'id': wid, 'url': url, 'events': events, 'filter': req.get('filter') or {},
           'secret': req.get('secret', ''), 'fail_count': 0, 'paused': False,
           'created_at': int(time.time())}
    with open(os.path.join(_webhook_dir(cfg), wid + '.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False)
    audit_log('webhook_set', req, 0, {'id': wid, 'url': url, 'events': events})
    return ok({'id': wid, 'url': url, 'events': events})

def action_webhook_list(req, cfg):
    d = _webhook_dir(cfg)
    items = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(d, fn), encoding='utf-8') as f:
                rec = json.load(f)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        rec.pop('secret', None)
        items.append(rec)
    return ok({'hooks': items, 'total': len(items)})

def action_webhook_delete(req, cfg):
    wid = req.get('id', '')
    if not _webhook_valid_id(wid):
        return fail(E['INVALID_PARAM'], 'bad_id', 'webhook id 格式非法 (需 wh_+10 hex)')
    d = _webhook_dir(cfg)
    f = os.path.join(d, wid + '.json')
    rd = os.path.realpath(d)
    rf = os.path.realpath(f)
    if not (rf == rd or rf.startswith(rd + os.sep)):
        return fail(E['INVALID_PATH'], 'path_escape', '文件路径逃逸拦截')
    if not os.path.exists(rf):
        return fail(E['NOT_FOUND'], 'not_found', "Hook %s not found" % wid)
    os.remove(rf)
    audit_log('webhook_delete', req, 0, {'id': wid})
    return ok({'id': wid, 'deleted': True})

def action_webhook_test(req, cfg):
    """
    AI 视角优化: webhook_test - 注册前先测试 webhook 是否可达 + 返回诊断信息.
    AI 可据此判断 URL 是否可达、签名是否正确, 避免注册一个永远失败的 webhook.
    """
    url = req.get('url', '')
    vok, verr = _webhook_validate_url(url, cfg)
    if not vok:
        return fail(E['INVALID_PARAM'], 'bad_url', verr)
    # DNS rebinding 防护 (对齐 PHP action_webhook_test): 解析不到公网 IP 就拒绝测试,
    # 否则攻击者可用域名先解析公网、再 rebind 到 127.0.0.1 打内网。
    _hk = urlparse(url)
    _host = _hk.hostname or ''
    _public_ips = _webhook_resolve_public_ips(_host) if _host else []
    if not _public_ips and not cfg.get('WEBHOOK_ALLOW_INTERNAL'):
        audit_log('webhook_test', req, E['WEBHOOK_FAILED'],
                  {'url': url, 'reason': 'dns_rebinding_blocked'})
        return fail(E['WEBHOOK_FAILED'], 'dns_rebinding_blocked',
                    '无法解析到公网 IP (可能 DNS rebind 到内网). 如目标是内网服务, 设置 WEBHOOK_ALLOW_INTERNAL=true',
                    {'url': url, 'hint': 'webhook 目标必须解析到公网 IP, 防 SSRF'})
    secret = req.get('secret', '')
    payload = {'event': 'webhook_test', 'ts': int(time.time()), 'test': True, 'path': req.get('path', '')}
    if secret:
        sig = _hmac.new(secret.encode(), json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
                        hashlib.sha256).hexdigest()
        payload['signature'] = 'sha256=' + sig
    body = json.dumps(payload, ensure_ascii=False).encode()
    t0 = time.time()
    http_code = 0
    curl_err = None
    try:
        rq = _urlreq.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
        with _urlreq.urlopen(rq, timeout=8) as resp:
            http_code = resp.status
    except Exception as e:
        curl_err = str(e)
    passed = 200 <= http_code < 300
    audit_log('webhook_test', req, 0 if passed else E.get('WEBHOOK_FAILED', 1099),
              {'url': url, 'http_code': http_code, 'curl_error': curl_err})
    return ok({'url': url, 'http_code': http_code, 'passed': passed, 'curl_error': curl_err,
               'total_time': round(time.time() - t0, 3),
               'tip': 'webhook 可达, 可安全注册' if passed else 'webhook 不可达或返回非 2xx, 请检查 URL/签名/防火墙'})

def _oplog_dir(cfg):
    d = os.path.join(cfg['WORK_DIR'].rstrip('/'), '.sync_oplog')
    os.makedirs(d, exist_ok=True)
    return d

def _all_work_dirs(cfg):
    raw = STATE.get('RAW_CONFIG') or {}
    wds = {}
    for name, sc in (raw.get('scopes') or {}).items():
        wd = sc.get('work_dir') if isinstance(sc, dict) else None
        if wd:
            wds[name] = wd.rstrip('/')
    if not wds and cfg.get('WORK_DIR'):
        wds['default'] = cfg['WORK_DIR'].rstrip('/')
    return wds

def _php_str(v):
    """PHP (string) 强制转换语义: true->'1', false->'', null->'', 数组->'Array'。"""
    if v is None:
        return ''
    if isinstance(v, bool):
        return '1' if v else ''
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, dict)):
        return 'Array'
    return str(v)


def _oplog_clip(v, max_bytes):
    """操作日志字段清洗: 剔除控制字符 + 按字节安全截断 (与 PHP _oplog_clip 对齐)。"""
    s = _php_str(v)
    s = re.sub(r'[\x00-\x1F\x7F]+', '', s)
    b = s.encode('utf-8', 'ignore')
    if len(b) > max_bytes:
        b = b[:max_bytes]
    return b.decode('utf-8', 'ignore')

def _oplog_args(v):
    """args 白名单校验 + 8KB 上限; 空 dict 转 [] 与 PHP json_decode/json_encode 对齐。"""
    if not isinstance(v, (dict, list)):
        return {}
    if isinstance(v, dict) and not v:
        return []
    try:
        enc = json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        return {}
    n = len(enc.encode('utf-8'))
    if n > 8192:
        return {'_truncated': True, '_bytes': n}
    return v

def _oplog_ts_bound(v, is_until=False):
    """时间筛选: epoch 秒或日期时间字符串; 纯日期 until 补到当天 23:59:59。"""
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s == '':
        return None
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    t = None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            t = int(time.mktime(datetime.strptime(s, fmt).timetuple()))
            break
        except ValueError:
            continue
    if t is not None and is_until and re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        t += 86399
    return t

def _oplog_last_entry(path):
    """只读文件尾部一行 (op_id 去重)。"""
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size <= 0:
                return None
            back = min(size, 8192)
            f.seek(size - back)
            buf = f.read().decode('utf-8', 'replace')
        lines = [ln for ln in buf.splitlines() if ln.strip()]
        if not lines:
            return None
        val = json.loads(lines[-1])
        return val if isinstance(val, dict) else None
    except Exception:
        return None

def action_oplog_append(req, cfg):
    """
    oplog_append: 客户端操作日志上传 (本地 .sync_oplog.jsonl 的每条记录自动同步到服务器)
    参数: entry (JSON object: ts/ts_epoch/op_id/command/args/summary/rc/duration_ms/version/host/user/cwd)
    存储: {WORK_DIR}/.sync_oplog/{scope}_{token_hash8}.jsonl
    服务端补充: ip/ua/token_name/server_ts/server_ts_epoch (客户端不可伪造)
    权限: 任何已认证 token (write 权限即可, 日志是客户端自己的操作记录)
    """
    entry = req.get('entry')
    if isinstance(entry, list):
        # 对齐 PHP: json_decode(..., true) 下 [] 与 {} 都是空数组, 按键取值全为默认值
        entry = {}
    if not isinstance(entry, dict):
        return fail(E['INVALID_PARAM'], 'missing_entry', 'entry (JSON object) 必填')
    try:
        _h = getattr(_REQUEST_CTX, 'headers', None)
        _ua = str(_h.get('User-Agent', '') if _h is not None else '')
    except Exception:
        _ua = ''
    safe = {
        'ts': _oplog_clip(entry.get('ts'), 32),
        'ts_epoch': _php_intval(entry.get('ts_epoch', 0)),
        'op_id': _oplog_clip(entry.get('op_id'), 64),
        'command': _oplog_clip(entry.get('command'), 32),
        'args': _oplog_args(entry.get('args')),
        'summary': _oplog_clip(entry.get('summary'), 240),
        'rc': _php_intval(entry.get('rc', 0)),
        'duration_ms': max(0, _php_intval(entry.get('duration_ms', 0))),
        'version': _oplog_clip(entry.get('version'), 32),
        'host': _oplog_clip(entry.get('host'), 64),
        'user': _oplog_clip(entry.get('user'), 64),
        'cwd': _oplog_clip(entry.get('cwd'), 300),
        'ip': _oplog_clip(_ctx_ip(), 45),
        'ua': _oplog_clip(_ua, 200),
        'token_name': _oplog_clip(cfg.get('TOKEN_NAME', ''), 64),
        'server_ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'server_ts_epoch': int(time.time()),
    }
    scope = re.sub(r'[^a-zA-Z0-9_-]', '_', _php_str(req.get('scope', 'default')))
    th = hashlib.md5(str(req.get('token', '')).encode()).hexdigest()[:8]
    client_id = scope + '_' + th
    log_file = os.path.join(_oplog_dir(cfg), client_id + '.jsonl')
    # 幂等: 客户端超时重传时按 op_id 去重 (只比对最后一条)
    if safe['op_id'] != '' and os.path.isfile(log_file):
        last = _oplog_last_entry(log_file)
        if last is not None and str(last.get('op_id', '')) == safe['op_id']:
            return ok({'client_id': client_id, 'log_file': log_file, 'bytes': 0, 'duplicate': True})
    # 对齐 PHP: JSON_UNESCAPED_UNICODE (中文原样) + 默认转义 '/'
    line = json.dumps(safe, ensure_ascii=False, separators=(',', ':')).replace('/', '\\/') + '\n'
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError:
        return fail(E['INTERNAL'], 'write_failed', '操作日志写入失败 (检查目录权限)')
    return ok({'client_id': client_id, 'log_file': log_file, 'bytes': len(line.encode('utf-8'))})

def action_oplog_list(req, cfg):
    """
    oplog_list: 列出所有客户端的操作日志文件 (admin)
    存储: {WORK_DIR}/.sync_oplog/{client_id}.jsonl
    返回: clients [{client_id, size_bytes, mtime, line_count, ok_count, error_count,
                   first_ts, last_ts, last_epoch, last_command, last_summary, last_ip, last_rc}]
    """
    clients = []
    wds = _all_work_dirs(cfg)
    for scope_name, wd in wds.items():
        d = os.path.join(wd, '.sync_oplog')
        if not os.path.isdir(d):
            continue
        try:
            names = [f for f in os.listdir(d) if f.endswith('.jsonl')]
        except OSError:
            continue
        for fn in names:
            f = os.path.join(d, fn)
            try:
                st = os.stat(f)
                with open(f, encoding='utf-8', errors='replace') as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            line_count = ok_count = error_count = 0
            first_ts = last_ts = last_cmd = last_summary = last_ip = ''
            last_epoch = 0
            last_rc = None
            by_cmd = {}
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                line_count += 1
                try:
                    dd = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(dd, dict):
                    continue
                rc = _php_intval(dd.get('rc', 0))
                if rc == 0:
                    ok_count += 1
                else:
                    error_count += 1
                if first_ts == '' and dd.get('ts'):
                    first_ts = str(dd.get('ts'))
                last_ts = str(dd.get('ts', last_ts))
                last_epoch = _php_intval(dd.get('ts_epoch', last_epoch))
                last_cmd = str(dd.get('command', ''))
                last_summary = str(dd.get('summary', ''))
                last_ip = str(dd.get('ip', ''))
                last_rc = rc
                ck = str(dd.get('command') or '')
                if ck:
                    by_cmd[ck] = by_cmd.get(ck, 0) + 1
            by_cmd_top = dict(sorted(by_cmd.items(), key=lambda kv: -kv[1])[:30])
            clients.append({
                'client_id': fn[:-6], 'scope': scope_name, 'log_file': f,
                'size_bytes': st.st_size, 'size_human': _human_size(st.st_size),
                'mtime': int(st.st_mtime),
                'mtime_str': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'line_count': line_count, 'ok_count': ok_count, 'error_count': error_count,
                'first_ts': first_ts, 'last_ts': last_ts, 'last_epoch': last_epoch,
                'last_command': last_cmd, 'last_summary': last_summary,
                'last_ip': last_ip, 'last_rc': last_rc, 'commands': by_cmd_top,
            })
    clients.sort(key=lambda c: c['mtime'], reverse=True)
    return ok({'clients': clients, 'total': len(clients), 'scopes': list(wds.keys())})

def _oplog_norm_json(v):
    """PHP json_decode(assoc)+json_encode 会把空对象输出成 []，Python 侧同步转换，保证响应字节一致。"""
    if isinstance(v, dict):
        if not v:
            return []
        return {k: _oplog_norm_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_oplog_norm_json(x) for x in v]
    return v


def action_oplog_view(req, cfg):
    """
    oplog_view: 查看某个客户端的操作日志内容 (admin)
    参数: client_id (必填), limit (默认100, 最大1000), offset (从最新往前跳过 N 条),
          grep (全文关键词), command (命令名, 逗号分隔多个), result (ok/error),
          since/until (epoch 秒或日期时间字符串)
    返回: entries [{ts, ts_epoch, op_id, command, args, summary, rc, duration_ms,
                    version, host, user, cwd, ip, ua, token_name, server_ts}],
          total (文件总条数), matched (筛选后条数), returned, has_more, stats
    """
    cid = str(req.get('client_id') or '').strip()
    if cid == '':
        return fail(E['INVALID_PARAM'], 'missing_client_id', 'client_id 必填')
    if not re.match(r'^[a-zA-Z0-9_-]+$', cid):
        return fail(E['INVALID_PARAM'], 'invalid_client_id', 'client_id 只允许字母数字下划线短横线')
    limit = max(1, min(1000, int(req.get('limit', 100) or 100)))
    offset = max(0, int(req.get('offset', 0) or 0))
    grep = str(req.get('grep') or '').strip()
    command = str(req.get('command') or '').strip().lower()
    cmd_set = [c.strip().lower() for c in command.split(',') if c.strip()] if command else []
    result = str(req.get('result') or '').strip().lower()
    since = _oplog_ts_bound(req.get('since'))
    until = _oplog_ts_bound(req.get('until'), True)
    log_file = None
    found = ''
    for wd in _all_work_dirs(cfg).values():
        c = os.path.join(wd, '.sync_oplog', cid + '.jsonl')
        if os.path.isfile(c):
            log_file = c
            found = os.path.basename(wd)
            break
    if not log_file:
        return fail(E['NOT_FOUND'], 'log_not_found', "操作日志文件不存在: %s" % cid)
    entries = []
    total = ok_count = error_count = sum_ms = max_ms = 0
    by_cmd = {}
    first_ts = last_ts = ''
    try:
        with open(log_file, encoding='utf-8', errors='replace') as fh:
            for ln in fh.read().splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    dd = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(dd, dict):
                    continue
                if 'args' in dd:
                    dd['args'] = _oplog_norm_json(dd['args'])
                total += 1
                cmd = str(dd.get('command') or '').lower()
                rc = _php_intval(dd.get('rc', 0))
                epoch = _php_intval(dd.get('ts_epoch', 0))
                if epoch == 0 and dd.get('ts'):
                    try:
                        epoch = _oplog_ts_bound(dd.get('ts')) or 0
                    except Exception:
                        epoch = 0
                # --- 筛选 ---
                if grep and grep.lower() not in json.dumps(dd, ensure_ascii=False).lower():
                    continue
                if cmd_set and cmd not in cmd_set:
                    continue
                if result in ('ok', 'success') and rc != 0:
                    continue
                if result in ('error', 'fail') and rc == 0:
                    continue
                if since is not None and epoch < since:
                    continue
                if until is not None and epoch > until:
                    continue
                # --- 统计 (基于筛选结果) ---
                if rc == 0:
                    ok_count += 1
                else:
                    error_count += 1
                ms = max(0, _php_intval(dd.get('duration_ms', 0)))
                sum_ms += ms
                if ms > max_ms:
                    max_ms = ms
                ck = str(dd.get('command') or '')
                if ck:
                    by_cmd[ck] = by_cmd.get(ck, 0) + 1
                if not first_ts and dd.get('ts'):
                    first_ts = str(dd.get('ts'))
                if dd.get('ts'):
                    last_ts = str(dd.get('ts'))
                entries.append(dd)
    except OSError:
        return fail(E['NOT_FOUND'], 'log_not_found', "操作日志文件不存在: %s" % cid)
    matched = len(entries)
    # 从最新往前 offset 条, 再取 limit 条 (offset=0 即最新一页)
    end = max(0, matched - offset)
    start = max(0, end - limit)
    page = entries[start:end] if end > start else []
    by_cmd_top = dict(sorted(by_cmd.items(), key=lambda kv: -kv[1])[:30])
    stats = {
        'total': total, 'matched': matched, 'ok': ok_count, 'error': error_count,
        'avg_ms': ((sum_ms + matched // 2) // matched) if matched > 0 else 0,
        'max_ms': max_ms, 'by_command': by_cmd_top,
        'first_ts': first_ts, 'last_ts': last_ts,
    }
    return ok({'client_id': cid, 'scope': found, 'entries': page,
               'total': total, 'matched': matched, 'returned': len(page),
               'limit': limit, 'offset': offset, 'has_more': start > 0,
               'filters': {'grep': grep, 'command': command, 'result': result,
                           'since': since, 'until': until},
               'stats': stats})

def _server_base_dir(cfg):
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return cfg['WORK_DIR']

def _package_dir(cfg):
    d = str(cfg.get('PACKAGE_DIR') or '').rstrip('/\\')
    if not d:
        d = os.path.join(tempfile.gettempdir(), 'ai-sync-packages')
    return d


def _api_base_url(cfg=None):
    """当前请求的 API 地址去掉 query, 用于拼 pkg_key 能力 URL (与 PHP 一致)."""
    try:
        host = getattr(_REQUEST_CTX, 'headers', None)
        h = host.get('Host') if host else ''
        if h:
            _xf = (host.get('X-Forwarded-Proto') or '').lower() == 'https'
            scheme = 'https' if (STATE.get('tls_enabled') or _xf) else 'http'
            return '%s://%s/sync_api.php' % (scheme, h)
    except Exception:
        pass
    return '/sync_api.php'


def action_package_list(req, cfg):
    """
    package_list: 列出已生成的客户端下载包 (供清理含 token 的过期包)
    返回: packages [{file, size, mtime, url, expires_at}]
    """
    pkg_dir = _package_dir(cfg)
    if not os.path.isdir(pkg_dir):
        return ok({'packages': [], 'total': 0})
    api_base = _api_base_url(cfg)
    items = []
    for fn in sorted(os.listdir(pkg_dir)):
        if not (fn.startswith('ai-sync-client-') and fn.endswith('.zip')):
            continue
        f = os.path.join(pkg_dir, fn)
        try:
            st = os.stat(f)
        except OSError:
            continue
        meta = None
        mf = f + '.meta.json'
        if os.path.isfile(mf):
            try:
                meta = json.load(open(mf, encoding='utf-8'))
            except Exception:
                meta = None
        url = None
        expires_at = None
        if isinstance(meta, dict) and meta.get('key'):
            url = api_base + ('&' if '?' in api_base else '?') + 'pkg_key=' + str(meta['key'])
            expires_at = int(meta.get('created') or st.st_mtime) + int(meta.get('ttl') or cfg.get('PACKAGE_TTL', 86400))
        items.append({'file': fn, 'size': st.st_size, 'mtime': int(st.st_mtime),
                      'url': url, 'expires_at': expires_at})
    items.sort(key=lambda x: x['mtime'], reverse=True)
    return ok({'packages': items, 'total': len(items)})

def action_package_delete(req, cfg):
    """
    package_delete: 删除指定客户端下载包 (含 token, 用完即删)
    参数: file (包文件名)
    """
    fn = req.get('file', '')
    if not isinstance(fn, str) or fn == '' or not re.match(r'^ai-sync-client-[a-z0-9._-]+\.zip$', fn, re.I):
        return fail(E['INVALID_PARAM'], 'bad_filename', '非法文件名')
    pkg_dir = _package_dir(cfg)
    path = os.path.join(pkg_dir, fn)
    if not os.path.isfile(path):
        return fail(E['NOT_FOUND'], 'not_found', "包 %s 不存在" % fn)
    try:
        os.remove(path)
        try: os.remove(path + '.meta.json')
        except OSError: pass
    except OSError:
        return fail(E['INTERNAL'], 'delete_fail', '删除失败')
    audit_log('package_delete', req, 0, {'file': fn})
    return ok({'deleted': fn})

def action_client_package(req, cfg):
    raw = STATE.get('RAW_CONFIG') or {}
    if not STATE.get('MULTI_DIR'):
        return fail(E['INVALID_PARAM'], 'not_multi_mode', 'client_package 仅在多目录模式下可用')
    target_token = req.get('target_token', '')
    if target_token == '':
        return fail(E['INVALID_PARAM'], 'missing_token', '请指定 target_token')
    token_info = (raw.get('tokens') or {}).get(target_token)
    if not token_info:
        return fail(E['NOT_FOUND'], 'token_not_found', 'Token 不存在')
    tokens_scopes = token_info.get('scopes') or []
    scope_name = req.get('scope') or (tokens_scopes[0] if tokens_scopes else '')
    if scope_name not in (raw.get('scopes') or {}):
        return fail(E['INVALID_PARAM'], 'invalid_scope', "Scope '%s' 不存在" % scope_name)
    base = _server_base_dir(cfg)
    sync_py = None
    for cand in (os.path.join(base, 'sync.py'), os.path.join(base, '..', 'sync.py')):
        if os.path.isfile(cand):
            sync_py = cand
            break
    if not sync_py:
        return fail(E['INTERNAL'], 'sync_py_missing', '找不到 sync.py 源文件, 无法打包')
    api_url = req.get('api_url') or ''
    if not api_url:
        api_url = _api_base_url(cfg)
    perms = token_info.get('permissions') or []
    doc_mode = req.get('doc_mode') or 'two_way'
    _mode_titles = {'two_way': '双向同步', 'pull_edit_push': '拉取-编辑-推送', 'brand_new': '全新接入'}
    if doc_mode not in _mode_titles:
        doc_mode = 'two_way'
    local_dir = str(req.get('local_dir') or '').strip().replace('\\', '/')
    local_dir_display = local_dir if local_dir else ('/workspace/%s (自动识别)' % scope_name)
    remote_subdir = str(req.get('remote_subdir') or '').strip().strip('/\\').replace('\\', '/')
    remote_full = ('scope:%s/%s' % (scope_name, remote_subdir)) if remote_subdir else ('scope:%s (根)' % scope_name)
    # FIX-v4.31: 版本必须与打进 zip 的 sync.py 实际版本一致 (旧版注释硬编码 v4.18, 会让 AI 误判能力边界)
    client_ver = ''
    try:
        _head = open(sync_py, 'r', encoding='utf-8', errors='replace').read(65536)
        _m = re.search(r'^APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', _head, re.M)
        if _m:
            client_ver = _m.group(1).strip()
    except OSError:
        client_ver = ''
    _ver_disp = client_ver or '(版本未知, 见 sync.py --version)'
    remote_rel = (remote_subdir + '/') if remote_subdir else ''
    perms_str = ', '.join(perms) if perms else '(无)'

    conf = ("# ai-sync %s 客户端配置 - 自动生成\n"
            "# 由管理面板打包, 生成时间: %s\n"
            "# Token 名称: %s  Scope: %s\n"
            "# WATCH_DIR/LOG_FILE 留空是故意的: 首次运行需用 --workdir 指定稳定工作目录, 客户端会自动写入\n\n"
            "API_URL    = \"%s\"\n"
            "API_TOKEN  = \"%s\"\n"
            "SCOPE      = \"%s\"\n\n"
            "WATCH_DIR  = \"%s\"\n"
            "REMOTE_BASE = \"%s\"\n"
            "LOG_FILE   = \"%s\"\n\n"
            "# 同步行为\n"
            "TWO_WAY        = %s\n"
            "DELETE_SYNC    = false\n"
            "IGNORE         = [\".sync_*\", \"._*\", \"*.log\", \"*.bak\", \"ai-sync/*\", \".git/*\", \"node_modules/*\"]\n"
            "ALLOW_EXT      = []\n\n"
            "# 备份\n"
            "LOCAL_BACKUP_DIR  = \".sync_backup\"\n"
            "LOCAL_BACKUP_KEEP = 10\n\n"
            "# 上传\n"
            "UPLOAD_TIMEOUT  = 120\n"
            "VERIFY_SSL      = auto\n"
            "LOG_LEVEL       = \"info\"\n"
            ) % (_ver_disp, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), token_info.get('name', ''), scope_name,
                 api_url, target_token, scope_name, local_dir, remote_subdir,
                 (local_dir + '/.sync.log') if local_dir else '',
                 'true' if doc_mode == 'two_way' else 'false')

    # start.sh 与 PHP 端同款 (支持 pull|save|fix|status 意图), 版本由 {{CLIENT_VERSION}} 注入
    start_sh = ("#!/bin/bash\n"
                "# ai-sync {{CLIENT_VERSION}} 快速启动 — 无参=自举; 也可: bash start.sh pull|save|fix\n"
                "set -e\n"
                "CONF=\"sync.conf\"\n"
                "INTENT=\"\"\n"
                "case \"${1:-}\" in\n"
                "  pull|save|fix|status) INTENT=\"$1\"; shift ;;\n"
                "  \"\") ;;\n"
                "  *) CONF=\"$1\"; shift ;;\n"
                "esac\n"
                "if [ ! -f \"$CONF\" ]; then\n"
                "  echo \"缺少配置文件 $CONF (应与 start.sh 同目录)\"\n"
                "  exit 1\n"
                "fi\n"
                "if [ -z \"$INTENT\" ]; then\n"
                "  exec python3 sync.py --conf \"$CONF\"\n"
                "fi\n"
                "exec python3 sync.py --conf \"$CONF\" --auto \"$INTENT\" \"$@\"\n")

    # README: 与 PHP 端一致, 优先读同目录 README.template.txt 模板, 无则简化兜底
    _tmpl = os.path.join(base, 'README.template.txt')
    if os.path.isfile(_tmpl):
        try:
            readme = open(_tmpl, 'r', encoding='utf-8').read()
        except OSError:
            readme = ''
    else:
        readme = ''
    if not readme:
        readme = ("你好 AI。这是 ai-sync 客户端（scope: {{SCOPE}}，客户端版本 {{CLIENT_VERSION}}）。\n\n"
                  "三步接入（照抄即可）:\n"
                  "1) 下载解压: {{ONELINE_CURL_CMD}}\n"
                  "2) 自举:     python3 sync.py --workdir /你的/稳定/工作目录\n"
                  "3) 拉代码:   python3 sync.py --auto pull\n\n"
                  "日常三条: --auto pull | --auto save [文件...] --yes | --auto fix\n"
                  "--json 任意命令可加, stdout 单行 JSON; --help-ai full 看全命令; --version 查版本;\n"
                  "--batch 一次跑多条只读命令省冷启动 (stdin 传 JSON 数组); --api-help 看服务端 action 契约。\n"
                  "写类命令多数需 --yes 防呆; 被策略阻断退出码=99; 部署到新目录会自动创建, 无需先 mkdir。\n")
    pkg_dir = _package_dir(cfg)
    os.makedirs(pkg_dir, exist_ok=True)
    ttl = max(60, int(cfg.get('PACKAGE_TTL', 86400) or 86400))
    try:
        _now = time.time()
        for _old in glob.glob(os.path.join(pkg_dir, 'ai-sync-client-*.zip')):
            if os.path.isfile(_old) and (_now - os.path.getmtime(_old)) > ttl:
                try: os.remove(_old)
                except OSError: pass
                try: os.remove(_old + '.meta.json')
                except OSError: pass
        for _m in glob.glob(os.path.join(pkg_dir, 'ai-sync-client-*.zip.meta.json')):
            if not os.path.isfile(_m[:-len('.meta.json')]):
                try: os.remove(_m)
                except OSError: pass
    except Exception:
        pass
    filename = 'ai-sync-client-%s-%s.zip' % (scope_name, secrets.token_hex(8))
    # 能力 URL: 64 位随机 key, 只能下载这一个包; 不暴露目录, 不能枚举其他包.
    pkg_key = secrets.token_hex(32)
    api_base = re.sub(r'\?.*$', '', api_url)
    saved_url = api_base + ('&' if '?' in api_base else '?') + 'pkg_key=' + pkg_key
    # 与 PHP 端一致的占位符替换 + 彻底清理残留占位符 (防止把未替换的 {{...}} 交给 AI)
    one_line_curl = (
        "curl -sSL " + _shq(saved_url) + " -o /tmp/ai-sync-client.zip \\\n"
        "  && mkdir -p ~/ai-sync-client \\\n"
        "  && (unzip -o /tmp/ai-sync-client.zip -d ~/ai-sync-client 2>/dev/null \\\n"
        "      || python3 -m zipfile -e /tmp/ai-sync-client.zip ~/ai-sync-client) \\\n"
        "  && rm /tmp/ai-sync-client.zip \\\n"
        "  && cd ~/ai-sync-client")
    ph = {'{{DOWNLOAD_URL}}': saved_url, '{{SHA256}}': '', '{{ONELINE_CURL_CMD}}': one_line_curl,
          '{{API_URL}}': api_url, '{{SCOPE}}': scope_name, '{{PERMS}}': perms_str,
          '{{LOCAL_DIR}}': local_dir, '{{REMOTE_SUBDIR}}': (remote_subdir or '.'),
          '{{REMOTE_REL}}': remote_rel, '{{CLIENT_VERSION}}': (client_ver or 'latest')}
    for k, v in ph.items():
        readme = readme.replace(k, v)
        start_sh = start_sh.replace(k, v)
    readme = re.sub(r'\{\{[A-Z0-9_]+\}\}', '', readme)
    out_path = os.path.join(pkg_dir, filename)
    meta_path = out_path + '.meta.json'
    meta = {
        'file': filename, 'key': pkg_key, 'created': int(time.time()),
        'ttl': ttl, 'scope': scope_name, 'token_name': token_info.get('name', ''),
    }
    try:
        with open(meta_path, 'w', encoding='utf-8') as _mf:
            json.dump(meta, _mf, ensure_ascii=False, separators=(',', ':'))
        with _zipfile.ZipFile(out_path, 'w', _zipfile.ZIP_DEFLATED) as zf:
            zf.write(sync_py, 'sync.py')
            zf.writestr('sync.conf', conf)
            zf.writestr('README.txt', readme)
            zf.writestr('start.sh', start_sh)
    except Exception as e:
        try: os.remove(out_path)
        except OSError: pass
        try: os.remove(meta_path)
        except OSError: pass
        return fail(E['INTERNAL'], 'package_fail', str(e))
    data = open(out_path, 'rb').read()
    sha = hashlib.sha256(data).hexdigest()
    save_url = saved_url
    return ok({
        'filename': filename, 'sha256': sha, 'size': len(data), 'scope': scope_name,
        'token_name': token_info.get('name', ''), 'permissions': perms, 'api_url': api_url,
        'watch_dir': '', 'download_url': save_url, 'package_file': save_url,
        'package_ttl': ttl, 'zip_b64': base64.b64encode(data).decode(),
        'entries': [{'name': 'sync.py', 'size': os.path.getsize(sync_py)},
                    {'name': 'sync.conf', 'size': len(conf)},
                    {'name': 'README.txt', 'size': len(readme)},
                    {'name': 'start.sh', 'size': len(start_sh)}],
        'sync_py_candidates': [sync_py],
        'sync_py_loaded_bytes': os.path.getsize(sync_py),
        'doc': {'mode': doc_mode, 'mode_title': _mode_titles[doc_mode],
                'local_dir': local_dir, 'local_dir_display': local_dir_display,
                'remote_subdir': remote_subdir, 'remote_full': remote_full,
                'readme_md': readme},
    })

def action_lint_batch(req, cfg):
    """
    lint_batch: 批量语法检查 (一次请求验证多个文件)
    参数: files [path1, path2, ...]
    返回: results [{path, ok, syntax_ok, issue_count, first_error}]
    """
    files = req.get('files') or []
    if not isinstance(files, list) or not files:
        return fail(E['INVALID_PARAM'], 'missing_files', 'files 数组必填')
    results = []
    total_issues = 0
    failed = 0
    syntax_failed = 0
    for rel in files:
        r = action_lint({'action': 'lint', 'path': rel, 'token': req.get('token', ''),
                         'scope': req.get('scope', '')}, cfg)
        is_syntax_failure = (not r.get('ok')) and r.get('error') == 'syntax_error'
        item = {'path': rel, 'ok': bool(r.get('ok')), 'valid': r.get('valid')}
        if r.get('ok') or is_syntax_failure:
            issues = r.get('issues') or []
            item['syntax_ok'] = r.get('syntax_ok')
            item['issue_count'] = len(issues)
            item['language'] = r.get('language', '')
            total_issues += len(issues)
            if is_syntax_failure:
                syntax_failed += 1
            for i in issues:
                if i.get('severity') == 'error':
                    item['first_error'] = {'line': i.get('line', 0), 'message': i.get('message', '')}
                    break
        else:
            item['error'] = r.get('error', '')
            item['message'] = r.get('message', '')
            failed += 1
        results.append(item)
    payload = {'results': results, 'total': len(results), 'failed': failed,
               'syntax_failed': syntax_failed, 'total_issues': total_issues,
               'all_ok': failed == 0 and syntax_failed == 0 and total_issues == 0}
    if syntax_failed > 0 or total_issues > 0:
        return fail(E['VALIDATION_FAILED'], 'syntax_error',
                    '%d file(s) failed syntax check (%d issue(s))' % (syntax_failed, total_issues),
                    payload)
    if failed > 0:
        return fail(E['VALIDATION_FAILED'], 'lint_failed',
                    '%d file(s) could not be linted' % failed, payload)
    return ok(payload)

def action_batch_rollback(req, cfg):
    """
    batch_rollback: 按时间点批量回滚 (恢复某时间点之前的所有文件)
    参数: before_ts (unix 时间戳, 回滚到此时间之前的备份), paths (可选, 指定文件列表, 默认全部有备份的)
    返回: results [{path, restored_from, ok, error}], restored_count, failed_count
    """
    before_ts = _php_intval(req.get('before_ts', 0))
    if before_ts <= 0:
        return fail(E['INVALID_PARAM'], 'missing_before_ts', 'before_ts (unix 时间戳) 必填')
    dry = _bool_flag(req.get('dry_run', False))
    if dry is None:
        return bad_bool('dry_run', req.get('dry_run'))
    # 对齐 PHP: paths 不是数组时, 非空值(如 true/"x")不会触发全量扫描, foreach 也跳过 → 空结果
    raw_paths = req.get('paths')
    if isinstance(raw_paths, dict):
        raw_paths = list(raw_paths.values())
    if raw_paths is None or raw_paths == '' or raw_paths is False or raw_paths == 0:
        raw_paths = []
    _skip_scan = False
    if not isinstance(raw_paths, list):
        # PHP: 非空标量(如 true/"x")让 empty() 为 false, 跳过全量扫描且 foreach 不产出结果
        _skip_scan = bool(raw_paths)
        raw_paths = []
    paths = raw_paths
    backup_dir = os.path.join(cfg['WORK_DIR'].rstrip('/'), (cfg.get('BACKUP_DIR') or 'ai-sync/backups/'))
    if not os.path.isdir(backup_dir):
        return ok({'results': [], 'restored_count': 0, 'failed_count': 0,
                   'before_ts': before_ts, 'dry_run': dry, 'summary': '无备份目录'})
    if not paths and not _skip_scan:
        found = set()
        for root, _dirs, fns in os.walk(backup_dir):
            for fn in fns:
                if '.bak.' not in fn:
                    continue
                rel_dir = os.path.relpath(root, backup_dir)
                if rel_dir == '.':
                    continue
                found.add(rel_dir.replace('\\', '/'))
        paths = sorted(found)
    results = []
    restored = 0
    failed = 0
    for rel in paths:
        if not isinstance(rel, str):
            # PHP: safe_path() 对非字符串返回 null → 该条 no_backup, path 原样回显
            results.append({'path': rel, 'ok': False, 'error': 'no_backup'})
            failed += 1
            continue
        dest = safe_path(rel, cfg['WORK_DIR'])
        fbd = os.path.join(backup_dir, os.path.dirname(rel), os.path.basename(rel))
        if not dest or not os.path.isdir(fbd):
            results.append({'path': rel, 'ok': False, 'error': 'no_backup'})
            failed += 1
            continue
        cands = glob.glob(os.path.join(fbd, os.path.basename(rel) + '.bak.*'))
        best = None
        best_mt = 0
        for bf in cands:
            try:
                mt = int(os.path.getmtime(bf))
            except OSError:
                continue
            if mt <= before_ts and mt > best_mt:
                best = bf
                best_mt = mt
        if not best:
            results.append({'path': rel, 'ok': False, 'error': 'no_backup_before_ts'})
            failed += 1
            continue
        if dry:
            results.append({'path': rel, 'ok': True, 'restored_from': os.path.basename(best),
                            'backup_mtime': best_mt})
            restored += 1
        else:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(best, dest)
                results.append({'path': rel, 'ok': True, 'restored_from': os.path.basename(best),
                                'backup_mtime': best_mt})
                restored += 1
            except OSError:
                results.append({'path': rel, 'ok': False, 'error': 'copy_fail'})
                failed += 1
    if not dry:
        audit_log('batch_rollback', req, E['INTERNAL'] if failed > 0 else 0,
                  {'before_ts': before_ts, 'restored': restored, 'failed': failed})
    return ok({'results': results, 'restored_count': restored, 'failed_count': failed,
               'before_ts': before_ts, 'dry_run': dry,
               'summary': ('预览: 将回滚 %d 个文件, %d 个无备份' % (restored, failed)) if dry
                          else ('回滚 %d 个文件, 失败 %d 个' % (restored, failed))})


# ==================== 路由表 ====================
def action_pipeline(req, cfg):
    """FIX-v4.34 (性能): 一次 HTTP 往返执行多个 action。

    为什么需要它
    ------------
    AI 的典型会话是"一串连续动作"(查 README → 看目录 → 读文件 → 确认 → 改 → 验证),
    每步都是一次独立 HTTP。实测生产单次往返 ~105ms:
        10 条独立命令 : 10 × (进程启动 170 + 网络 105) ≈ 2.8s
        --batch       : 1 × 170 + 10 × 105        ≈ 1.2s   (省了进程启动, 往返照付)
        pipeline      : 1 × 170 + 1 × 105         ≈ 0.3s   ← 本 action
    关键在于 --batch 只是"同进程"而非"同请求", N 次往返一次都少不了;
    pipeline 把 N 次往返压成 1 次, 这是 N×RTT 与 1×RTT 的区别。

    安全边界
    --------
    1. 每个 step **重新走 dispatch** —— 完整继承鉴权、scope 校验、权限位、IP 白名单。
       不做"外层验过就放行"的信任假设。
    2. step 自带的 token/scope **一律被顶层覆盖**, 防止用低权限凭据申请、
       再在管道里塞高权限 token 提权。
    3. 黑名单拦截不可逆/高危 action (exec/删除/凭据增删/嵌套 pipeline 等)。
    4. 只读 step 不重复计限流配额, 写类 step 照常计费 (否则管道会成为限流后门)。
    """
    steps = req.get('steps')
    if steps is None:
        return fail(E['INVALID_PARAM'], 'missing_steps',
                    'pipeline 缺少必填参数 steps',
                    {'hint': '形态: {"action":"pipeline","steps":[{"action":"stat","path":"a.txt"},'
                             '{"action":"read_range","path":"a.txt"}]}; 上限 %d 步。'
                             % PIPELINE_MAX_STEPS})
    if isinstance(steps, dict):
        # 对齐 PHP: JSON 对象经 json_decode(..., true) 即数组, {} 等价 [] (empty_steps),
        # 非空对象按值展开。Python 若直接拒绝 {} 会与 PHP 行为分叉。
        steps = list(steps.values())
    if not isinstance(steps, list):
        return fail(E['INVALID_PARAM'], 'bad_steps', 'steps must be array',
                    {'hint': 'steps 是对象数组, 每项是一个完整 action 请求'})
    if not steps:
        return fail(E['INVALID_PARAM'], 'empty_steps', 'steps 数组不能为空 (没有要执行的动作)',
                    {'hint': '至少 1 步; 只想执行单个 action 请直接调用它'})
    if len(steps) > PIPELINE_MAX_STEPS:
        return fail(E['INVALID_PARAM'], 'too_many_steps',
                    'steps 超过上限 %d (收到 %d)' % (PIPELINE_MAX_STEPS, len(steps)),
                    {'hint': '拆成多个 pipeline 分批执行'})

    stop_on_error = _bool_flag(req.get('stop_on_error', False))
    if stop_on_error is None:
        return bad_bool('stop_on_error', req.get('stop_on_error'))
    results = []
    n_ok = n_fail = 0
    stopped_at = -1

    # 先做一次准入检查: 阻塞名单内的 action 不允许进管道 (避免"执行到一半才失败")
    blocked = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            blocked.append('[%d] step 必须是对象' % i)
            continue
        a = str(st.get('action', '') or '')
        if not a:
            blocked.append('[%d] 缺 action 字段' % i)
        elif a in PIPELINE_FORBIDDEN:
            blocked.append('[%d] action %r 不允许在 pipeline 中执行' % (i, a))
    if blocked:
        return fail(E['INVALID_PARAM'], 'pipeline_blocked_action',
                    'pipeline 拒绝了 %d 个 step' % len(blocked),
                    {'blocked': blocked[:10],
                     'hint': '以下 action 因不可逆或高危不支持管道执行: '
                             + ', '.join(sorted(PIPELINE_FORBIDDEN))
                             + '。请单独调用它们。'})

    top_token = req.get('token', '')
    top_scope = req.get('scope', '')
    for i, st in enumerate(steps):
        step = dict(st)
        # 强制继承外层凭据 —— 安全第一语义:
        #   ① step 没带 token/scope 的正常情况 → 注入外层的(否则 dispatch 鉴权失败);
        #   ② step **自带** token/scope → 被外层覆盖, 防止用低权限凭据申请管道、
        #      再在 step 里塞高权限 token 提权。
        if top_token:
            step['token'] = top_token
        if top_scope:
            step['scope'] = top_scope
        act = str(step.get('action', '') or '')
        try:
            _REQUEST_CTX.pipeline_readonly_step = (act not in PIPELINE_MUTATING)
            res = dispatch(step)
        except Exception as e:  # 单步异常不应炸掉整条管道
            res = fail(E['INTERNAL'], 'pipeline_step_error', str(e)[:200])
        finally:
            _REQUEST_CTX.pipeline_readonly_step = False
        if not isinstance(res, dict):
            res = fail(E['INTERNAL'], 'pipeline_bad_step_result', 'step returned non-dict')
        item = {'index': i, 'action': act}
        item.update(res)
        results.append(item)
        if res.get('ok'):
            n_ok += 1
        else:
            n_fail += 1
            if stop_on_error:
                stopped_at = i
                break

    return ok({
        'results': results,
        'total': len(results),
        'succeeded': n_ok,
        'failed': n_fail,
        'stopped_at': stopped_at,
        'stop_on_error': stop_on_error,
        'max_steps': PIPELINE_MAX_STEPS,
        'mutating_actions_declared': sorted(set(
            str(s.get('action', '')) for s in steps
            if isinstance(s, dict) and str(s.get('action', '')) in PIPELINE_MUTATING)),
    })


# ==================== shell (对齐原生 ssh 的持久会话工具, 面向 AI 易用性) ====================
# 与 exec 并存: exec 是单发命令; shell 是带持久 cwd/env 的会话, SSH 般可连续操作。
import uuid as _uuid
try:
    import fcntl as _fcntl
except Exception:
    _fcntl = None  # Windows: 无 fcntl 模块, 非阻塞套接字/文件锁走降级路径
import select as _select


def _set_nonblocking(stream):
    """POSIX: 把管道/套接字设为非阻塞; Windows: 无操作(改用线程读)。"""
    if _fcntl is None:
        return
    try:
        _fcntl.fcntl(stream, _fcntl.F_SETFL, _fcntl.fcntl(stream, _fcntl.F_GETFL) | os.O_NONBLOCK)
    except OSError:
        pass


def _drain_pipes_threaded(proc, end_ts, max_output):
    """Windows 无 select(管道)/fcntl: 用线程并发读 stdout/stderr, 到 end_ts 或进程退出为止。"""
    import threading
    buckets = {'o': bytearray(), 'e': bytearray()}
    stop = threading.Event()

    def _rd(src, key):
        try:
            while not stop.is_set():
                chunk = src.read(65536)
                if not chunk:
                    break
                buckets[key].extend(chunk)
                if len(buckets['o']) + len(buckets['e']) >= max_output:
                    stop.set()
                    break
        except Exception:
            pass

    t1 = threading.Thread(target=_rd, args=(proc.stdout, 'o'))
    t2 = threading.Thread(target=_rd, args=(proc.stderr, 'e'))
    t1.daemon = True
    t2.daemon = True
    t1.start()
    t2.start()
    while time.time() < end_ts and proc.poll() is None:
        time.sleep(0.05)
    stop.set()
    t1.join(timeout=1)
    t2.join(timeout=1)
    truncated = (len(buckets['o']) + len(buckets['e']) >= max_output)
    return bytes(buckets['o']), bytes(buckets['e']), truncated


SHELL_SESSIONS = {}      # sid -> {'cwd','env','ts','name'}
SHELL_JOBS = {}          # jid -> {...}
SHELL_SESSIONS_MAX = 32
SHELL_IDLE_TTL = 3600    # 空闲 1h 自动清理
SHELL_JOBS_MAX = 32
SHELL_JOB_TTL = 600      # 已完成的后台任务保留 10 分钟后回收
SHELL_OUTPUT_CAP = 8 * 1024 * 1024

def _shell_auth(req, cfg):
    """复用 exec 授权: exec_enabled + EXEC_ALLOW_IPS。通过返回 None, 否则返回 fail 响应。"""
    if not cfg.get('EXEC_ENABLED'):
        return fail(E['EXEC_DISABLED'], 'exec_disabled', 'Shell 需要 exec_enabled=true')
    if cfg.get('EXEC_ALLOW_IPS'):
        ip = STATE.get('_client_ip', '')
        allowed = any(ip == rule or ('/' in rule and ip_in_cidr(ip, rule)) for rule in cfg['EXEC_ALLOW_IPS'])
        if not allowed:
            return fail(E['FORBIDDEN'], 'ip_blocked', "IP %s not in EXEC_ALLOW_IPS" % ip)
    return None

def _shell_sweep():
    now = time.time()
    if len(SHELL_SESSIONS) > SHELL_SESSIONS_MAX:
        for k, _ in sorted(SHELL_SESSIONS.items(), key=lambda kv: kv[1].get('ts', 0))[:len(SHELL_SESSIONS) - SHELL_SESSIONS_MAX]:
            SHELL_SESSIONS.pop(k, None)
    for k, v in list(SHELL_SESSIONS.items()):
        if now - v.get('ts', 0) > SHELL_IDLE_TTL:
            SHELL_SESSIONS.pop(k, None)

def _shell_job_finished(job):
    """后台任务是否已结束: 以 runner 的 EXIT trap 是否写出 rc 文件为准(比 kill -0 可靠, 不受 pid 复用影响)。"""
    if job.get('done'):
        return True
    rp = job.get('rc')
    if rp and os.path.exists(rp):
        try:
            return os.path.getsize(rp) > 0
        except OSError:
            return False
    return False

def _shell_sweep_jobs():
    """回收已完成的后台任务。

    没有这个回收的话, 提交满 SHELL_JOBS_MAX 个任务后 SHELL_JOBS 永远不减,
    后台功能会永久不可用(内存态下要等进程重启, 落盘态下则是永久) —— 这是真实缺陷。
    策略: 先标记已完成, 再回收超期任务; 仍达上限时淘汰最旧的已完成任务。
    """
    now = time.time()
    for jid, j in SHELL_JOBS.items():
        if not j.get('done') and _shell_job_finished(j):
            j['done'] = True
            j['ts'] = j.get('ts', now)
    for jid, j in list(SHELL_JOBS.items()):
        if j.get('done') and now - j.get('ts', 0) > SHELL_JOB_TTL:
            _shell_job_cleanup(j, True)
            SHELL_JOBS.pop(jid, None)
    if len(SHELL_JOBS) >= SHELL_JOBS_MAX:
        dones = [(k, v) for k, v in SHELL_JOBS.items() if v.get('done')]
        dones.sort(key=lambda kv: kv[1].get('ts', 0))
        need = len(SHELL_JOBS) - SHELL_JOBS_MAX + 1
        for jid, j in dones[:max(0, need)]:
            _shell_job_cleanup(j, True)
            SHELL_JOBS.pop(jid, None)

def _shell_parse_timeout(v):
    """timeout 容错解析: 接受 int/float/数字字符串('30' 也常见), 其它一律给明确的参数错误。

    直接 int(v) 的话, 'abc' 会抛 ValueError -> 500(INTERNAL)。对 AI 来说
    "服务器内部错误"毫无可操作性, 远不如"timeout 必须是数字(秒)"。
    """
    if v is None or isinstance(v, bool):
        return 30
    if isinstance(v, (int, float)):
        try:
            iv = int(v)
        except (ValueError, OverflowError):
            return None
        # <=0 一律拒绝: GNU timeout 的 0 表示"不限制", 静默把超时变成永不超时,
        # 比报错危险得多 (AI 传错类型时 PHP intval("abc")/本文件上游 _php_intval 都会得到 0)
        return iv if iv > 0 else None
    if isinstance(v, str):
        s = v.strip()
        if s == '':
            return 30
        try:
            f = float(s)
        except ValueError:
            return None
        if f != f or f <= 0:    # NaN / 非正数
            return None
        return int(f)
    return None

# 统一的错误文案 (与 PHP 端 _shell_err_msg 逐字一致, 否则双端响应比对必然"漂移")
_SHELL_ERR_MSG = {
    'invalid_session_id': 'session_id 必须是字符串, 例: {"session_id": "sh_abc123"}',
    'invalid_job_id':     'job_id 必须是字符串, 例: {"job_id": "jb_abc123"}',
    'invalid_env':        'env 必须是对象(键值对), 例: {"env": {"FOO": "bar"}}',
    'invalid_env_key':    'env 的键名必须是合法环境变量名(字母或下划线开头, 仅含字母/数字/下划线), 例: {"env": {"FOO": "bar"}}',
    'invalid_env_value':  'env 的值必须是字符串或数字, 例: {"env": {"FOO": "bar"}}',
    'invalid_cwd':        'cwd 必须是字符串(绝对路径或相对会话 cwd 的路径)',
    'cwd_not_found':      'cwd 指向的目录不存在或不是目录, 请先创建它再执行',
    'invalid_background': 'background 必须是布尔值, 例: {"background": true}',
    'invalid_lines':      'lines 必须是正整数, 例: {"lines": 50}',
    'cmd_contains_null':  'cmd 不能包含 NUL 字节(\\0): shell 会静默截断其后的内容, 请改用换行分隔多条命令',
}

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _shell_err(key, extra=None):
    return fail(E['INVALID_PARAM'], key, _SHELL_ERR_MSG.get(key, '参数不合法'), extra)

_SHELL_GUARD_RULES = [
    # ---- T1: 毁灭性且不可逆 -> 拒绝执行, 并在响应里给出替代方案 ----
    {'kind': 'wipe_root', 'level': 'block',
     're': r'^\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(?:/|/\*|//)\s*$',
     'msg': '拒绝执行: 该命令会递归强制删除根目录 /, 会瞬间毁掉整个系统且不可恢复',
     'alts': ['把 / 换成真实目标目录, 例: rm -rf /var/log/old',
              '删之前先 ls -la <目标> 看一眼里面到底是什么']},
    {'kind': 'wipe_device', 'level': 'block',
     're': r'^\s*(?:sudo\s+)?dd\s+.*\bof\s*=\s*/dev/(?:sd[a-z]+\d*|nvme\d+n\d+p?\d*|vd[a-z]+|hd[a-z]+|xvd[a-z]+)\s*$',
     'msg': '拒绝执行: 该命令会把数据直接写入裸磁盘/分区, 会不可逆地覆盖整块盘',
     'alts': ['要生成镜像文件就写到普通文件: dd if=xx of=/path/to/file.img',
              '确实要整盘写入, 先用 lsblk 确认设备名, 再单独走运维流程']},
    {'kind': 'mkfs_device', 'level': 'block',
     're': r'^\s*(?:sudo\s+)?(?:mkfs(?:\.[a-z0-9]+)?|mke2fs|mkswap)\s+.*/dev/(?:sd|nvme|vd|hd|xvd)',
     'msg': '拒绝执行: 该命令会格式化整个磁盘/分区, 盘上数据全部丢失且不可恢复',
     'alts': ['只是要清空目录就用 rm -rf <目录>, 不要用 mkfs',
              '确实要格式化, 先用 lsblk 确认是数据盘而不是系统盘']},
    {'kind': 'overwrite_device', 'level': 'block',
     're': r'^\s*(?:sudo\s+)?[^>]*>\s*/dev/(?:sd|nvme|vd|hd|xvd)',
     'msg': '拒绝执行: 该命令会用输出重定向覆盖裸磁盘/分区',
     'alts': ['重定向请指向普通文件: echo "内容" > /path/to/file']},
    {'kind': 'fork_bomb', 'level': 'block',
     're': r'^\s*:\s*\(\s*\)\s*\{[^}]*\|[^}]*\}',
     'msg': '拒绝执行: 这是 fork 炸弹, 会瞬间耗尽进程/内存, 只能重启机器才能恢复',
     'alts': ['要并发就用限定数量: xargs -P 4',
              '要压测就先 ulimit -u 限制进程数再跑']},
    {'kind': 'power_off', 'level': 'block',
     're': r'^\s*(?:sudo\s+)?(?:shutdown|reboot|halt|poweroff|init\s+0|systemctl\s+(?:poweroff|reboot|halt))\b',
     'msg': '拒绝执行: 该命令会关机/重启, 会让本工具和你的连接一起掉线, 之后就无法再操作了',
     'alts': ['要重启某个服务就用 systemctl restart <服务名>',
              '确实要重启机器, 请走你自己的运维渠道, 不要用本工具']},

    # ---- T2: 破坏性但可能是 AI 的真意 -> 照常执行, 只提醒 ----
    {'kind': 'rm_rf', 'level': 'warn',
     're': r'^\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+\S',
     'msg': '这条命令会递归强制删除, 删除不可恢复',
     'hint': '确认目标路径是你真正想删的; 建议先 ls -la <目标> 看一眼内容'},
    {'kind': 'rm_rf_var', 'level': 'warn',
     're': r'^\s*(?:sudo\s+)?rm\s+(?:--?[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+[^;\s]*\$',
     'msg': '删除目标里含 shell 变量; 一旦变量为空或未设置, 目标会退化成 /*, 实际等于删掉整个系统',
     'hint': '先 echo "<变量>" 打印出实际值再删; 或用 set -u 让未设置变量直接报错'},
    {'kind': 'chmod_recursive', 'level': 'warn',
     're': r'^\s*(?:sudo\s+)?chmod\s+.*-R.*(?:777|a=rwx).*\s/',
     'msg': '递归把权限改成 777 会影响整棵目录树, 作用到 / 或系统目录时风险很高',
     'hint': '限定到具体业务目录, 并优先考虑 755/750 而不是 777'},
    {'kind': 'wipe_file', 'level': 'warn',
     're': r'^\s*(?:sudo\s+)?(?:truncate|shred)\s+.*/dev/(?:sd|nvme|vd|hd|xvd)',
     'msg': 'truncate/shred 会不可逆销毁数据',
     'hint': '确认目标设备不是系统盘; 只是要清空文件就用 truncate -s 0 <普通文件>'},
]

def _shell_split_cmds(cmd):
    """按 ; && || | 换行 把命令切成独立的段, 逐段判断。

    引号内的分隔符会被一并切开 —— 这只影响检测精度(可能漏检),
    不会误伤(每段仍按规则独立判断)。防呆场景下漏检远好于误伤:
    误伤会直接拦下 AI 的正常命令, 那才是真正损害可用性的事。
    """
    return re.split(r';|\|\||&&|\||\n', cmd)

def _shell_guard_scan(cmd):
    """破坏性命令扫描。返回 {'block': rule|None, 'warn': [rule...]}。

    定位是防呆, 不是安全边界: shell 只给 AI 用, 而能调用 shell 的调用方本来就能
    执行任意命令, 想绕过这些字面量规则易如反掌(用变量 / 编码 / 先写脚本再执行)。
    这里要解决的是 AI 的误操作:
      1) 路径变量为空 -> `rm -rf $DIR/*` 退化成 `rm -rf /*`;
      2) 复制粘贴来的清盘 / 格式化命令被原样下发;
      3) 关机 / 重启会让本工具和 AI 的连接一起掉线。
    规则刻意保守, 只认最常见、最典型的字面量形态: 宁可漏检, 也不误伤。
    """
    blocks = [r for r in _SHELL_GUARD_RULES if r['level'] == 'block']
    warns = [r for r in _SHELL_GUARD_RULES if r['level'] == 'warn']
    out = {'block': None, 'warn': {}}

    # ---- 整串预检(必须先于分段) ----
    # 血的教训: fork 炸弹的形态是 :(){:|:&};:, 它的函数体依赖 | 和 ; —— 而这两个字符
    # 正是 _shell_split_cmds 的分隔符。切完之后段是 [':(){:', ':&}', ':'], 没有任何一段
    # 含完整形态, 这条规则 100% 漏检(实测: 炸弹真的被执行了, 把机器进程表打满,
    # 双端同时 EAGAIN 掉线)。所以任何依赖"被分段切走的字符"的规则, 都只能在整串上判。
    # 这些规则都以 ^ 锚定, 整串预检是分段的超集, 不会引入新的误伤。
    for r in blocks:
        if re.search(r['re'], cmd):
            out['block'] = r
            return out

    for seg in _shell_split_cmds(cmd):
        seg = seg.strip()
        if not seg:
            continue
        blocked = False
        for r in blocks:
            if re.search(r['re'], seg):
                if out['block'] is None:
                    out['block'] = r        # 只报第一条, 避免刷屏
                blocked = True
                break
        if blocked:
            continue                        # 已判死的段不再重复提醒
        for r in warns:
            if re.search(r['re'], seg):
                out['warn'].setdefault(r['kind'], r)
    out['warn'] = list(out['warn'].values())
    return out

def _shell_warnings_field(warns):
    return [{'level': 'warn', 'kind': r['kind'], 'message': r['msg'], 'hint': r.get('hint', '')}
            for r in warns]

def _shell_check_id(v):
    """id 参数类型校验 (session_id / job_id)。返回 (字符串, 是否非法)。

    不校验的后果: Python 拿 list 查 dict -> "unhashable type" TypeError,
    PHP 拿数组当数组键 -> "Illegal offset type" Error —— 双端都是 1099(INTERNAL),
    而真实问题只是"参数类型写错了"。数字宽容转字符串, 保留 unknown_* 的语义。
    """
    if v is None or v == '':
        return '', False
    if isinstance(v, bool):
        return None, True
    if isinstance(v, (int, float)):
        return str(v), False
    if isinstance(v, str):
        return v, False
    return None, True

def _shell_check_env(extra):
    """env 入参校验。返回 (map, 错误码 key)。

    定位是防呆, 不是防恶意: shell 只给 AI 用, 而能调用 shell 的调用方本来就能直接
    执行任意命令(exec 同权限), 这里不需要、也不该做"恶意破坏拦截"。
    要解决的是 AI 侧的静默失败:
      1) 键名非法: `export 1BAD=x` 的报错被 source 的 2>/dev/null 吞掉,
         AI 以为变量设上了, 后续命令却读不到;
      2) 值不是标量 / env 不是对象: 整包 env 被静默忽略, AI 同样无从察觉。
    """
    if extra is None:
        return {}, None
    if isinstance(extra, dict):
        items = list(extra.items())
        if not items:
            return {}, None
    elif isinstance(extra, (list, tuple)):
        if len(extra) == 0:
            return {}, None
        return None, 'invalid_env'
    else:
        return None, 'invalid_env'
    out = {}
    for k, v in items:
        if not isinstance(k, str) or not _ENV_KEY_RE.match(k):
            return None, 'invalid_env_key'
        if isinstance(v, (dict, list, tuple)):
            return None, 'invalid_env_value'
        if isinstance(v, bool):
            v = '1' if v else ''
        out[k] = str(v)
    return out, None

def _shell_check_cwd(param, base_abs):
    """cwd 入参校验: 目录必须真实存在。返回 (绝对路径, 错误码 key)。

    原先目录不存在时 Popen 拿不到 cwd 就退化成"继承服务端进程的工作目录",
    runner 里的 cd 失败后命令继续在那个目录跑 —— AI 指定 /data 却在
    /workspace/ai_sync_server 下执行了命令, 会话 cwd 还被回写成服务端目录。
    静默在错误目录执行比报错危险得多。
    """
    if param is None or param == '' or param == [] or param == {}:
        return base_abs, None
    if isinstance(param, bool) or isinstance(param, (dict, list, tuple)):
        return None, 'invalid_cwd'
    if isinstance(param, (int, float)):
        param = str(param)
    if not isinstance(param, str):
        return None, 'invalid_cwd'
    abs_p = param if os.path.isabs(param) else os.path.join(base_abs, param)
    if not os.path.isdir(abs_p):
        return None, 'cwd_not_found'
    return os.path.normpath(abs_p), None

def _shell_parse_boolish(v):
    """background 归一化。返回 True/False; 无法识别返回 None(调用方报 1011)。

    Python 的 bool("false") 与 PHP 的 !empty("false") 都是 True —— AI 按直觉传
    {"background": "false"} 会把前台命令扔进后台(拿不到输出); 而 "0" 上双端恰好相反
    (Python bool("0") 为真 -> 后台, PHP empty("0") 为真 -> 前台), 是实打实的双端漂移。
    """
    if v is None or v == '' or v == [] or v == {}:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ('1', 'true', 'yes', 'on', 'y'):
            return True
        if s in ('0', 'false', 'no', 'off', 'n'):
            return False
        return None
    return None

def _shell_parse_lines(v):
    """lines 解析。<=0 视为"全部"(这里 0 含义明确且无害), 非数字返回 None 报错。"""
    if v is None or v == '' or v == [] or v == {}:
        return 0
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        iv = int(v)
        return iv if iv > 0 else 0
    if isinstance(v, str):
        s = v.strip()
        if s == '':
            return 0
        try:
            f = float(s)
        except ValueError:
            return None
        if f != f:      # NaN
            return None
        iv = int(f)
        return iv if iv > 0 else 0
    return None

def _shell_envfile_content(env):
    """生成 envfile 内容 —— 再兜一道键名过滤, 保证每行都是 `export NAME='value'` 这一种形状。"""
    out = []
    for k, v in (env or {}).items():
        ks = str(k)
        if not _ENV_KEY_RE.match(ks):
            continue
        if isinstance(v, (dict, list, tuple)):
            v = ''
        if isinstance(v, bool):
            v = '1' if v else ''
        out.append("export %s=%s\n" % (ks, sh_quote(str(v))))
    return ''.join(out)

def _shell_popen_cwd(start_abs):
    """Popen 的 cwd 只在目录确实存在时传。

    目录不存在时若直接传给 Popen, 子进程根本起不来 -> 抛异常 -> 500(INTERNAL),
    而真实意图只是"切到一个不存在的目录"。交给 runner 里的 cd 失败去体现即可(与 PHP 端一致)。
    """
    return start_abs if os.path.isdir(start_abs) else None

def _shell_resolve_cwd(cwd_param, base_abs):
    """per-call cwd: 绝对直接用, 相对相对 base_abs。不 clamp(不过度保护)。"""
    if not cwd_param:
        return base_abs
    cwd_param = str(cwd_param)
    if os.path.isabs(cwd_param):
        return os.path.normpath(cwd_param)
    return os.path.normpath(os.path.join(base_abs, cwd_param))

# ---- exec PATH 自适应 ----------------------------------------------------
# 旧实现按字符串写死剔除 '/data/data/'，只适用于 Termux 这一种环境，且在普通 Linux
# 上会强行把标准路径前置、改变用户自定义 PATH 顺序。改为按「当前系统能否真正 execve」
# 探测，不维护任何平台黑名单。
_EXEC_PATH_CACHE = None
_EXEC_PATH_LOCK = threading.Lock()


# 在「与 exec 子进程完全相同」的 shell 上下文里探测：stdin 逐行给 PATH 段，
# stdout 每行 "<bad> <core> <dir>"。
#   bad=1  该目录下的程序 execve 被拒（shell 退出码 126/127：权限 / 加载器 / 无 shebang）
#   core=1 该目录下的 sh 或 setsid 确实能跑起来（可作为 command -v 的首选段）
# 关键：不能在本 Python 进程里探测 —— Termux 自身允许 execve 私有目录 ELF，
# 但从系统 sh 派生的子进程会被 SELinux 拒绝，两者结论相反（线上实测踩过）。
_EXEC_PATH_PROBE_SH = r'''
while IFS= read -r d; do
  [ -n "$d" ] || continue
  bad=0; core=0
  for n in sh setsid; do
    [ -x "$d/$n" ] || continue
    if [ "$n" = sh ]; then "$d/sh" -c : >/dev/null 2>&1; else "$d/$n" --version >/dev/null 2>&1; fi
    rc=$?
    if [ "$rc" -eq 126 ] || [ "$rc" -eq 127 ]; then bad=1; else core=1; fi
  done
  echo "$bad $core $d"
done
'''


def _exec_safe_path():
    """构造 exec 子进程的 PATH —— 按当前系统真实可执行性自适应，无平台黑名单。

    背景：Android/Termux 的 SELinux untrusted_app 允许 Termux 自身进程 execve 应用
    私有目录下的 ELF，却禁止「从系统 sh 派生的子进程」这么做；php-fpm 靠 clear_env
    恰好绕开，而 Python 端继承 Termux PATH 时 `command -v setsid` 会命中一个跑不了
    的 setsid（rc=126）。所以在服务端进程内探测必然误判，必须放到真实 exec 上下文。

    策略：
      1) 用 _exec_sh_bin()（运行时实测可执行的 sh）在最小环境里对每段 PATH 真跑一次
         sh/setsid，按 shell 退出码 126/127 判定该段在当前系统是否根本加载不了；
      2) 没有受限段 → PATH 原样返回，顺序与内容零改动（普通 Linux / 本地测试）；
      3) 有受限段 → 剔除它们，把「sh/setsid 已验证可执行」的段按原相对顺序提到最前，
         并把 _exec_sh_bin() 所在目录强制补入并置顶（保证 command -v 命中系统实现）；
      4) 探测异常（无 sh / 超时 / 沙箱）→ 保守降级：仅置顶 sh 目录，其余原样，
         绝不因探测失败而阻塞 exec；
      5) 按原始 PATH 缓存，进程内只探测一次（ThreadingHTTPServer 多线程安全）。

    /data/data、/data/app、noexec 挂载点、受限容器等都能自动适配，无需任何平台黑名单。
    """
    global _EXEC_PATH_CACHE
    raw = os.environ.get('PATH', '') or os.defpath
    with _EXEC_PATH_LOCK:
        if _EXEC_PATH_CACHE is not None and _EXEC_PATH_CACHE[0] == raw:
            return _EXEC_PATH_CACHE[1]
        parts = [p for p in dict.fromkeys(raw.split(os.pathsep)) if p]
        sh_bin = _exec_sh_bin()
        sh_dir = os.path.dirname(sh_bin) or '/bin'
        try:
            r = subprocess.run([sh_bin, '-c', _EXEC_PATH_PROBE_SH],
                               input=('\n'.join(parts) + '\n').encode(),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=15,
                               env={'PATH': os.pathsep.join([sh_dir, '/bin', '/system/bin', '/usr/bin']),
                                    'LANG': 'C'})
            bad, core_map = set(), {}
            for line in (r.stdout or b'').decode('utf-8', 'replace').splitlines():
                bits = line.split(' ', 2)
                if len(bits) != 3:
                    continue
                b, c, d = bits
                if b == '1':
                    bad.add(d)
                core_map[d] = (c == '1')
            if bad:
                usable = [p for p in parts if p not in bad]
                ordered = ([p for p in usable if core_map.get(p)] +
                           [p for p in usable if not core_map.get(p)])
                ordered = [sh_dir] + [p for p in ordered if p != sh_dir]
                result = os.pathsep.join(ordered) or os.defpath
            else:
                result = os.pathsep.join(parts)       # 普通系统：完全原样
        except Exception:
            result = os.pathsep.join([sh_dir] + [p for p in parts if p != sh_dir])
        _EXEC_PATH_CACHE = (raw, result)
        return result


def _shell_base_env():
    return {'PATH': _exec_safe_path(),
            'LANG': os.environ.get('LANG', 'C.UTF-8'), 'HOME': os.environ.get('HOME', '/tmp')}

def _shell_merge_env(session, req):
    base = _shell_base_env()
    for k, v in (session.get('env') or {}).items():
        base[str(k)] = str(v)
    extra = req.get('env') or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            base[str(k)] = str(v)
    return base

def _parse_export_p(s):
    """解析 `export -p` 输出。

    不同 shell 格式不同, 两种都必须认:
      bash:        declare -x K="v"
      dash/ash/mksh/BusyBox: export K='v'  或  export K=v
    旧实现只认 bash 的 declare -x, 在 /bin/sh=dash 或 BusyBox 的系统上
    shell 会话的 export 永远不生效 (实测导出后下一轮 $VAR 为空)。
    """
    env = {}
    for line in s.splitlines():
        line = line.strip()
        if line.startswith('declare -x '):
            body = line[len('declare -x '):]
        elif line.startswith('export '):
            body = line[len('export '):]
        else:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", body)
        if not m:
            continue
        k = m.group(1); v = m.group(2)
        if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0]:
            q = v[0]
            v = v[1:-1]
            if q == '"':
                v = v.replace('\\"', '"').replace('\\\\', '\\').replace('\\$', '$').replace('\\`', '`')
            else:
                # POSIX shell 两种自嵌套单引号写法都要还原:
                #   ash/BusyBox: 'it'\''s'     dash: 'it'"'"'s'
                v = v.replace("'\\''", "'").replace("'\"'\"'", "'")
        env[k] = v
    return env

def _shell_env_clean(env):
    """剔除 shell 自身簿记变量, 避免把临时文件路径/PWD 等噪声写进会话 env。

    与 PHP 端 _shell_env_clean 保持一致: PWD/OLDPWD 若写回会话, 下一轮
    runner 先 cd 再 source envfile 时会把 PWD 覆盖成旧值, `pwd` 与真实工作
    目录可能不一致; SHLVL/_ 每层都变, 没有保存价值。
    """
    drop = {'RC_PATH', 'CWD_PATH', 'ENV_PATH', 'SHLVL', 'OLDPWD', 'PWD', '_'}
    return {k: v for k, v in (env or {}).items() if k not in drop}


def _shell_write_cmdfile(cmd, rc_path, cwd_path, envout_path):
    """用户命令写入脚本文件, 末尾 EXIT trap 回写 rc/cwd/env(支持 exit, 不受大括号干扰)。"""
    # FIX-win-shell: 原实现把整条 trap 命令包在单引号里, 又对每个路径 sh_quote(再套单引号),
    #   引号互相打断 —— Windows 临时路径 'C:\...\Temp\shell_rc_x' 实际被当成未加引号,
    #   反斜杠被 shell 当转义吃掉, 写到当前目录的怪文件名, 且 _shell_capture 读不到真实 rc/cwd/env
    #   (POSIX 的 /tmp 无反斜杠, 侥幸正常)。改为先拼命令体(路径各自 sh_quote), 再对整条命令体 sh_quote 一次。
    _body = ("printf \"AI_SYNC_RC=%%d\\n\" \"$?\" > %s; printf \"%%s\" \"$(pwd)\" > %s; export -p > %s"
             % (sh_quote(rc_path), sh_quote(cwd_path), sh_quote(envout_path)))
    content = "trap %s EXIT\n" % sh_quote(_body)
    content += cmd + "\n"
    f = tempfile.NamedTemporaryFile(prefix='shell_cmd_', delete=False, mode='w', encoding='utf-8')
    f.write(content); f.close()
    return f.name

def _shell_write_runner(start_abs, envfile, cmdfile, timeout, timeout_flag, done_flag, rc_path, cwd_path, envout_path):
    # POSIX 写法: `.` 代替 bash 的 source, sh 代替硬编码 bash ——
    # Android/BusyBox/精简容器没有 bash 时旧写法直接 'bash: not found', shell 工具全不可用。
    # 超时不用外部 timeout(各系统 rc/信号分发不一致, 且 runner 被杀后 cmdfile 的
    # EXIT trap 常把 rc 记成 0) —— 改为 runner 内纯 POSIX sh 看门狗, 超时强制写 rc=124。
    sh = sh_quote(_exec_sh_bin())
    lines = ["cd " + sh_quote(start_abs), ". " + sh_quote(envfile) + " 2>/dev/null"]
    if timeout > 0:
        # 子进程清单优先 /proc/<pid>/task/<pid>/children (Linux/Android), 其次 pgrep -P
        # (macOS/BusyBox), 不依赖 setsid/进程组。先杀父再杀子: 避免子进程被 TERM 后
        # 父 shell 继续执行后续命令(如 `sleep 30; echo done` 会把 done 也跑出来)。
        kids_code = [
            "  __aik_kids=\"\"",
            "  if [ -r \"/proc/$__aik_p/task/$__aik_p/children\" ]; then",
            "    __aik_kids=$(cat \"/proc/$__aik_p/task/$__aik_p/children\" 2>/dev/null)",
            "  elif command -v pgrep >/dev/null 2>&1; then",
            "    __aik_kids=$(pgrep -P \"$__aik_p\" 2>/dev/null)",
            "  fi",
        ]
        lines += ["__AISYNC_KILL_TREE() {", "  __aik_p=$1; __aik_s=$2"] + kids_code + [
            "  kill -$__aik_s \"$__aik_p\" 2>/dev/null",
            "  for __aik_c in $__aik_kids; do __AISYNC_KILL_TREE \"$__aik_c\" \"$__aik_s\"; done",
            "}",
            "__AISYNC_KILL_KIDS() {", "  __aik_p=$1; __aik_s=$2",
        ] + kids_code + [
            "  for __aik_c in $__aik_kids; do __AISYNC_KILL_TREE \"$__aik_c\" \"$__aik_s\"; done",
            "}",
            "rm -f " + sh_quote(timeout_flag) + " " + sh_quote(done_flag),
            sh + " " + sh_quote(cmdfile) + " & __AISYNC_P=$!",
            # 看门狗每秒查一次 done 标记: 正常结束由 runner 写 done 并回收看门狗,
            # 不用 kill 硬杀(sh 会往任务 stderr 打 'Terminated', 污染命令输出)。
            "( __ai_i=0; while [ \"$__ai_i\" -lt %d ] && [ ! -f %s ]; do sleep 1; __ai_i=$((__ai_i+1)); done; "
            "if [ ! -f %s ]; then : > %s; __AISYNC_KILL_TREE \"$__AISYNC_P\" TERM; sleep 2; __AISYNC_KILL_TREE \"$__AISYNC_P\" KILL; fi )"
            " >/dev/null 2>&1 </dev/null & __AISYNC_W=$!"
            % (timeout, sh_quote(done_flag), sh_quote(done_flag), sh_quote(timeout_flag)),
            # wait 的 stderr 重定向: 被 TERM 的后台任务会触发 sh 打印 'Terminated'
            "wait \"$__AISYNC_P\" 2>/dev/null; __AISYNC_RC=$?",
            ": > " + sh_quote(done_flag),
            "__AISYNC_KILL_KIDS \"$__AISYNC_W\" TERM; wait \"$__AISYNC_W\" 2>/dev/null",
            "if [ -f " + sh_quote(timeout_flag) + " ]; then "
            + "printf 'AI_SYNC_RC=%d\\n' 124 > " + sh_quote(rc_path) + "; "
            + "printf '%s' \"$(pwd)\" > " + sh_quote(cwd_path) + "; "
            + "export -p > " + sh_quote(envout_path) + "; exit 124; fi",
            "exit \"$__AISYNC_RC\"",
        ]
    else:
        lines.append(sh + " " + sh_quote(cmdfile))
    f = tempfile.NamedTemporaryFile(prefix='shell_run_', delete=False, mode='w', encoding='utf-8')
    f.write("\n".join(lines) + "\n"); f.close()
    return f.name

def _shell_write_envfile(env):
    f = tempfile.NamedTemporaryFile(prefix='shell_env_', delete=False, mode='w', encoding='utf-8')
    f.write(_shell_envfile_content(env)); f.close()
    return f.name

def _posix_to_win_path(p):
    """Git-bash/MSYS2 的 pwd 返回 /c/Users/... 形式; Windows 上转成 C:\\Users\\... 。
    非 Windows 或非该形式原样返回。"""
    if os.name != 'nt' or not p:
        return p
    if len(p) >= 3 and p[0] == '/' and p[2] == '/' and p[1].isalpha():
        return p[1].upper() + ':' + p[2:].replace('/', '\\')
    return p


def _shell_capture(rc_path, cwd_path, envout_path, fallback_cwd):
    rc = 0; new_cwd = fallback_cwd; env = {}
    try:
        with open(rc_path) as f:
            m = re.search(r"AI_SYNC_RC=(\d+)", f.read())
            if m: rc = int(m.group(1))
    except Exception:
        pass
    try:
        with open(cwd_path) as f:
            p = f.read().strip()
            if p: new_cwd = os.path.normpath(_posix_to_win_path(p))
    except Exception:
        pass
    try:
        with open(envout_path) as f:
            env = _parse_export_p(f.read())
    except Exception:
        pass
    return rc, new_cwd, env

def _shell_stream(proc, timeout, max_output, grace=6):
    stdout, stderr = b'', b''
    truncated = False
    hit_deadline = False
    t0 = time.time()
    end_ts = t0 + timeout + grace
    if _fcntl is not None:
        for s in (proc.stdout, proc.stderr):
            _set_nonblocking(s)
        while time.time() < end_ts:
            if proc.poll() is not None:
                break
            try:
                rlist, _, _ = _select.select([proc.stdout, proc.stderr], [], [], 0.2)
            except (ValueError, OSError):
                break
            if not rlist:
                continue
            for s in rlist:
                try: chunk = s.read(65536)
                except (BlockingIOError, OSError): chunk = b''
                if not chunk: continue
                if s is proc.stdout: stdout += chunk
                else: stderr += chunk
                if len(stdout) + len(stderr) >= max_output:
                    truncated = True; break
            if truncated: break
    else:
        _so, _se, truncated = _drain_pipes_threaded(proc, end_ts, max_output)
        stdout += _so
        stderr += _se
    if proc.poll() is None:
        hit_deadline = True
        _exec_kill_tree(proc.pid, _SIGTERM)
        try: proc.wait(timeout=2)
        except Exception: pass
        if proc.poll() is None:
            _exec_kill_tree(proc.pid, _SIGKILL)
    try:
        so, se = proc.communicate(timeout=5)
        stdout += so or b''; stderr += se or b''
    except Exception: pass
    return stdout, stderr, truncated, hit_deadline

def action_shell_open(req, cfg):
    err = _shell_auth(req, cfg)
    if err: return err
    _shell_sweep()
    sid = req.get('session_id')
    if sid is None:
        sid = req.get('name')
    if sid is None:
        sid = ''
    sid, bad = _shell_check_id(sid)
    if bad:
        return _shell_err('invalid_session_id')
    if not sid:
        sid = 'sh_' + _uuid.uuid4().hex[:12]
    if sid in SHELL_SESSIONS:
        s = SHELL_SESSIONS[sid]; s['ts'] = time.time()
        note = None
        # 复用会话时若请求里还带了 cwd/env, 它们不会生效(会话已有自己的状态) —— 说清楚,
        # 否则 AI 会以为设置成功了。cwd/env 只在新开会话时生效。
        if req.get('cwd') or req.get('env'):
            note = '会话已存在, 本次的 cwd/env 未生效(会话沿用原有状态); 需要新的 cwd/env 请先 shell_close 再 shell_open'
        return ok({'session_id': sid, 'cwd': s['cwd'], 'name': s.get('name', ''), 'reused': True, 'note': note})
    base_abs = os.path.realpath(cfg['WORK_DIR'].rstrip('/'))
    start_abs, ekey = _shell_check_cwd(req.get('cwd'), base_abs)
    if ekey:
        return _shell_err(ekey)
    init_env, ekey = _shell_check_env(req.get('env'))
    if ekey:
        return _shell_err(ekey)
    name = req.get('name') if isinstance(req.get('name'), str) else ''
    SHELL_SESSIONS[sid] = {'cwd': start_abs, 'env': init_env, 'ts': time.time(), 'name': name}
    return ok({'session_id': sid, 'cwd': start_abs, 'name': name, 'reused': False,
               'tip': 'action=shell_run 在会话内执行; cwd/export 跨调用保持'})

def _shell_proc_cmd(runner):
    """可移植的 shell 执行命令: 超时由 runner 内看门狗负责, 不依赖 timeout 命令。"""
    return sh_quote(_exec_sh_bin()) + ' ' + sh_quote(runner)


def action_shell_run(req, cfg):
    err = _shell_auth(req, cfg)
    if err: return err
    sid = req.get('session_id')
    if sid is None:
        sid = ''
    sid, bad = _shell_check_id(sid)
    if bad:
        return _shell_err('invalid_session_id')
    if not sid or sid not in SHELL_SESSIONS:
        return fail(E['INVALID_PARAM'], 'unknown_session', "session_id '%s' 不存在, 请先 shell_open" % sid,
                    {'available': list(SHELL_SESSIONS.keys())[:20]})
    s = SHELL_SESSIONS[sid]; s['ts'] = time.time()
    cmd = req.get('cmd', '')
    if not isinstance(cmd, str):
        # 直接把数字喂给命令文件写入会抛 TypeError -> 500, 而真实问题只是参数类型不对
        # 不写具体类型名: PHP 说 integer / Python 说 int, 会让双端响应逐字不一致
        return fail(E['INVALID_PARAM'], 'cmd_must_be_string',
                    'cmd 必须是字符串, 例: {"cmd": "ls -la"}')
    if not cmd:
        return fail(E['INVALID_PARAM'], 'empty_cmd', 'cmd is required')
    if '\0' in cmd:
        # bash 会把 "echo A<NUL>echo B" 静默拼成 "echo Aecho B" —— 命令语义被改写却不报错
        return _shell_err('cmd_contains_null')
    # 破坏性命令防呆: T1(毁灭性) 直接拒绝并给替代方案; T2(破坏性但可能是真意) 放行但提醒
    _g = _shell_guard_scan(cmd)
    if _g['block']:
        return fail(E['FORBIDDEN'], 'destructive_blocked', _g['block']['msg'],
                    {'guard': {'level': 'block', 'kind': _g['block']['kind']},
                     'alternatives': _g['block']['alts'],
                     'fix_hint': '; '.join(_g['block']['alts'])})
    _tmo = _shell_parse_timeout(req.get('timeout', 30))
    if _tmo is None:
        return fail(E['INVALID_PARAM'], 'invalid_timeout',
                    'timeout 必须是数字(秒), 例: {"timeout": 30}')
    timeout = min(_tmo, int(cfg.get('EXEC_TIMEOUT_MAX', 120)))
    max_output = max(65536, int(cfg.get('EXEC_MAX_OUTPUT', 8 * 1024 * 1024)))
    start_abs, ekey = _shell_check_cwd(req.get('cwd'), s['cwd'])
    if ekey:
        return _shell_err(ekey)
    extra_env, ekey = _shell_check_env(req.get('env'))
    if ekey:
        return _shell_err(ekey)
    _bg = _shell_parse_boolish(req.get('background'))
    if _bg is None:
        return _shell_err('invalid_background')
    env = _shell_merge_env(s, {'env': extra_env})
    envfile = _shell_write_envfile(env)
    rc_path = tempfile.NamedTemporaryFile(prefix='shell_rc_', delete=False).name
    cwd_path = tempfile.NamedTemporaryFile(prefix='shell_cwd_', delete=False).name
    envout_path = tempfile.NamedTemporaryFile(prefix='shell_envout_', delete=False).name
    cmdfile = _shell_write_cmdfile(cmd, rc_path, cwd_path, envout_path)
    timeout_flag = tempfile.NamedTemporaryFile(prefix='shell_to_', delete=False).name
    done_flag = tempfile.NamedTemporaryFile(prefix='shell_ok_', delete=False).name
    runner = _shell_write_runner(start_abs, envfile, cmdfile, timeout, timeout_flag, done_flag, rc_path, cwd_path, envout_path)
    proc_cmd = _shell_proc_cmd(runner)
    has_timeout = timeout > 0
    t0 = time.time()
    if _bg:
        if len(SHELL_JOBS) >= SHELL_JOBS_MAX:
            _shell_sweep_jobs()
        if len(SHELL_JOBS) >= SHELL_JOBS_MAX:
            return fail(E['RATE_LIMITED'], 'too_many_jobs',
                        '后台任务数已达上限(%d), 且均无已完成可回收的任务; 先 shell_job 取走结果或 shell_kill 终止后重试' % SHELL_JOBS_MAX)
        jid = 'jb_' + _uuid.uuid4().hex[:12]
        d = os.path.join(cfg['WORK_DIR'].rstrip('/'), 'ai-sync', '.shell')
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, jid + '.out'); errf = os.path.join(d, jid + '.err')
        try:
            fout = open(out, 'wb'); ferr = open(errf, 'wb')
            proc = _posix_shell_spawn(proc_cmd,
                                    cwd=_shell_popen_cwd(start_abs), stdout=fout, stderr=ferr,
                                    stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        except Exception as e:
            return fail(E['INTERNAL'], 'proc_open_fail', str(e))
        SHELL_JOBS[jid] = {'pid': proc.pid, 'proc': proc, 'out': out, 'err': errf,
                           'rc': rc_path, 'cwd': cwd_path, 'envout': envout_path,
                           'temps': [envfile, cmdfile, runner, timeout_flag, done_flag],
                           'cmd': cmd, 'timeout': timeout, 'session_id': sid, 'ts': time.time(),
                           'done': False, 'cleaned': False, 'start_cwd': start_abs}
        # 注意: 临时脚本不能立刻删(分离进程尚未读取), 待任务收割时再清理
        tip = 'action=shell_job 轮询(job_id); action=shell_kill 终止'
        if _g['warn']:
            tip = '[提醒] %s; %s' % (_g['warn'][0]['msg'], _g['warn'][0].get('hint', ''))
        return ok({'job_id': jid, 'background': True, 'session_id': sid, 'cwd': start_abs, 'timeout': timeout,
                   'timeout_enforced': has_timeout,
                   'warnings': _shell_warnings_field(_g['warn']), 'tip': tip})
    try:
        proc = _posix_shell_spawn(proc_cmd,
                                cwd=_shell_popen_cwd(start_abs), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    except Exception as e:
        return fail(E['INTERNAL'], 'proc_open_fail', str(e))
    stdout, stderr, truncated, hit_deadline = _shell_stream(proc, timeout, max_output, 6 if has_timeout else 1)
    rc = proc.returncode
    real_rc, new_cwd, new_env = _shell_capture(rc_path, cwd_path, envout_path, start_abs)
    if real_rc != 0:
        rc = real_rc
    if hit_deadline:
        rc = 124
    timed_out = (rc == 124)
    s['cwd'] = new_cwd; s['env'] = _shell_env_clean(new_env); s['ts'] = time.time()
    for p in (envfile, cmdfile, runner, timeout_flag, done_flag, rc_path, cwd_path, envout_path):
        try: os.remove(p)
        except OSError: pass
    audit_log('shell_run', req, (0 if (rc == 0 and not timed_out) else E['INTERNAL']), {
        'session_id': sid, 'cmd_preview': cmd[:300], 'return_code': rc, 'timed_out': 1 if timed_out else 0,
        'cwd': new_cwd, 'elapsed_ms': int((time.time() - t0) * 1000)})
    # 提醒放在 tip 位: 它是响应里最醒目的提示位, AI 扫一眼就能看到;
    # 输出截断的提示优先级更高, 两者冲突时保留截断提示(提醒仍完整留在 warnings 里)
    tip = '输出超过上限已截断, 重定向到文件后 download' if truncated else None
    if tip is None and _g['warn']:
        tip = '[提醒] %s; %s' % (_g['warn'][0]['msg'], _g['warn'][0].get('hint', ''))
    return ok({'session_id': sid, 'return_code': rc, 'stdout': stdout.decode('utf-8', 'replace'),
               'stderr': stderr.decode('utf-8', 'replace'), 'cwd': new_cwd, 'pid': proc.pid,
               'timed_out': timed_out, 'timeout_enforced': has_timeout,
               'output_truncated': truncated,
               'stdout_len': len(stdout), 'stderr_len': len(stderr),
               'elapsed_ms': int((time.time() - t0) * 1000),
               'warnings': _shell_warnings_field(_g['warn']),
               'tip': tip})

def _shell_job_cleanup(job, purge=False):
    """清理任务临时文件。默认只删启动脚本(temps), 保留 rc/cwd/envout 供反复轮询;
    purge=True 用于 TTL/淘汰, 连输出与采集文件一起删, 防止磁盘泄漏。
    旧实现第一次 shell_job 就把 rc 删了 → 所有任务 return_code 恒 0、cwd/env 丢失。"""
    for p in (job.get('temps') or []):
        try: os.remove(p)
        except OSError: pass
    job['temps'] = []
    job['cleaned'] = True
    if purge:
        for p in (job.get('rc'), job.get('cwd'), job.get('envout'), job.get('out'), job.get('err')):
            try: os.remove(p)
            except OSError: pass
        job['purged'] = True

def action_shell_job(req, cfg):
    err = _shell_auth(req, cfg)
    if err: return err
    jid = req.get('job_id', '')
    jid, bad = _shell_check_id(jid)
    if bad:
        return _shell_err('invalid_job_id')
    lines_n = _shell_parse_lines(req.get('lines'))
    if lines_n is None:
        return _shell_err('invalid_lines')
    if not jid or jid not in SHELL_JOBS:
        return fail(E['INVALID_PARAM'], 'unknown_job', "job_id '%s' 不存在" % jid)
    job = SHELL_JOBS[jid]
    # rc 文件优先(EXIT trap 落盘); 僵尸进程 / pid 复用都会让 poll()/kill -0 误判仍在运行
    finished = _shell_job_finished(job)
    if not finished and job['proc'].poll() is not None:
        try: job['proc'].communicate(timeout=1)
        except Exception: pass
        finished = True
        if job.get('rc') and not os.path.exists(job['rc']):
            job['died_without_rc'] = True
    if finished and not job['done']:
        job['done'] = True
    rc, new_cwd, new_env = _shell_capture(job['rc'], job['cwd'], job['envout'], job['start_cwd'])
    if finished:
        _shell_job_cleanup(job)   # 只删 temps, rc/cwd/envout 留给后续轮询
    def _rd(p):
        try:
            with open(p, 'rb') as f: return f.read()
        except Exception: return b''
    raw_out = _rd(job['out'])
    raw_err = _rd(job['err'])
    truncated = False
    if len(raw_out) + len(raw_err) > SHELL_OUTPUT_CAP:
        half = SHELL_OUTPUT_CAP // 2
        raw_out = raw_out[-half:]; raw_err = raw_err[-half:]
        truncated = True
    combined = raw_out + (b'\n' + raw_err if raw_err else b'')
    if lines_n > 0:
        # 与 PHP 端对齐: 先去掉尾随换行再取末尾 N 行
        text = '\n'.join(combined.decode('utf-8', 'replace').rstrip('\r\n').split('\n')[-lines_n:])
    else:
        text = combined.decode('utf-8', 'replace')
    rc_known = False
    try:
        with open(job['rc']) as f:
            m = re.search(r"AI_SYNC_RC=(\d+)", f.read())
            if m:
                rc_known = True; rc = int(m.group(1))
    except Exception:
        pass
    if finished:
        if job.get('killed'):
            # 主动终止一律 128+sig: 杀子进程后 runner 可能继续走完并以 0 退出, rc=0 会被误读为成功
            final_rc = 128 + int(job.get('kill_signal', 15))
        elif rc_known:
            final_rc = rc
        elif job.get('died_without_rc'):
            final_rc = None
        else:
            final_rc = rc
    else:
        final_rc = None
    return ok({'job_id': jid, 'status': 'done' if finished else 'running',
               'return_code': final_rc,
               'timed_out': (final_rc == 124),
               'killed': bool(job.get('killed')),
               'output_truncated': truncated, 'output': text,
               'stdout': raw_out.decode('utf-8', 'replace'),
               'stderr': raw_err.decode('utf-8', 'replace'),
               'pid': job['pid'], 'cwd': new_cwd, 'timeout': job['timeout'],
               'elapsed_ms': int((time.time() - job['ts']) * 1000)})

def action_shell_kill(req, cfg):
    err = _shell_auth(req, cfg)
    if err: return err
    jid = req.get('job_id', '')
    jid, bad = _shell_check_id(jid)
    if bad:
        return _shell_err('invalid_job_id')
    if not jid or jid not in SHELL_JOBS:
        return fail(E['INVALID_PARAM'], 'unknown_job', "job_id '%s' 不存在" % jid)
    job = SHELL_JOBS[jid]
    was_done = bool(job.get('done')) or _shell_job_finished(job)
    if not was_done:
        try:
            _exec_kill_tree(job['pid'], 15)
        except Exception:
            try: os.kill(job['pid'], 15)
            except Exception: pass
        for _ in range(10):
            if job['proc'].poll() is not None:
                break
            time.sleep(0.2)
        else:
            job['kill_signal'] = 9
            try: os.kill(job['pid'], 9)
            except Exception: pass
        if 'kill_signal' not in job:
            job['kill_signal'] = 15
        job['killed'] = True
    job['done'] = True
    # 只删 temps: 保留 rc/cwd/envout, 下一次 shell_job 才能拿到 143 和最终 cwd
    _shell_job_cleanup(job)
    # 幂等: 已是完成态的任务再 kill 不报错, 但要如实说明"它早就结束了",
    # 否则 AI 会把 killed=true 当成"是我终止的", 从而误判任务耗时/结果
    now_killed = not was_done
    return ok({'job_id': jid, 'killed': now_killed, 'pid': job['pid'],
               'return_code': (128 + int(job.get('kill_signal', 15))) if now_killed else None,
               'already_finished': was_done,
               'note': ('该任务在本次 kill 之前就已结束, 本次未产生实际终止动作' if was_done else None)})

def action_shell_close(req, cfg):
    err = _shell_auth(req, cfg)
    if err: return err
    sid = req.get('session_id', '')
    sid, bad = _shell_check_id(sid)
    if bad:
        return _shell_err('invalid_session_id')
    if not sid:
        return fail(E['INVALID_PARAM'], 'missing_session_id', 'session_id is required')
    if sid not in SHELL_SESSIONS:
        # 幂等: "关闭"的目标状态(不存在)已经达成, 再关一次不该报错 —— AI 不必为此写重试/纠错分支
        return ok({'session_id': sid, 'closed': False, 'already_closed': True,
                   'note': '会话本就不存在(已关闭或已被超时回收), 无需再次关闭'})
    SHELL_SESSIONS.pop(sid, None)
    return ok({'session_id': sid, 'closed': True, 'already_closed': False,
               'note': '会话状态已清除; 该会话的后台任务仍可在 shell_job 查询'})

def action_shell_list(req, cfg):
    err = _shell_auth(req, cfg)
    if err: return err
    sessions = []
    for k, v in SHELL_SESSIONS.items():
        sessions.append({'session_id': k, 'cwd': v.get('cwd', ''),
                         'name': v.get('name', '') if isinstance(v.get('name'), str) else '',
                         'idle_s': int(time.time() - v.get('ts', 0))})
    jobs = []
    for k, v in SHELL_JOBS.items():
        alive = v['proc'].poll() is None
        cmd = v.get('cmd') if isinstance(v.get('cmd'), str) else ''
        jobs.append({'job_id': k, 'status': 'running' if alive else 'done',
                     'session_id': v.get('session_id', ''), 'cmd_preview': cmd[:120], 'pid': v.get('pid', 0)})
    return ok({'sessions': sessions, 'jobs': jobs, 'session_count': len(sessions), 'job_count': len(jobs)})


# ==================== 路由表 ====================
ROUTES = {
    'ping': action_ping, 'help': action_help, 'health': action_health, 'metrics': action_metrics,
    'client_download': action_client_download,
    'upload': action_upload, 'upload_batch': action_upload_batch, 'download': action_download,
    'stat': action_stat, 'list': action_list, 'diff': action_diff, 'delete': action_delete,
    'rename': action_rename, 'copy': action_copy, 'mkdir': action_mkdir, 'checksum': action_checksum,
    'php_run': action_php_run,
    'search': action_search, 'todo': action_todo, 'stats': action_stats, 'read_range': action_read_range,
    'format': action_format, 'lint': action_lint, 'doc': action_doc,
    'snippet_set': action_snippet_set, 'snippet_get': action_snippet_get,
    'snippet_list': action_snippet_list, 'snippet_delete': action_snippet_delete,
    'backup_ls': action_backup_ls, 'backup_rb': action_backup_rb,
    'exec': action_exec,
    # 持久 shell 工具(对齐原生 ssh, 面向 AI 易用性)
    'shell_open': action_shell_open, 'shell_run': action_shell_run, 'shell_job': action_shell_job,
    'shell_kill': action_shell_kill, 'shell_close': action_shell_close, 'shell_list': action_shell_list,
    'error_log': action_error_log, 'error_log_list': action_error_log_list,
    'config_get': action_config_get, 'config_reload': action_config_reload,
    'scopes_list': action_scopes_list, 'tokens_list': action_tokens_list,
    'token_add': action_token_add, 'token_delete': action_token_delete,
    'scope_add': action_scope_add, 'scope_delete': action_scope_delete,
    'audit_log': action_audit_log_view, 'admin_stats': action_admin_stats,
    'whoami': action_whoami, 'sync_check': action_sync_check, 'recent_changes': action_recent_changes,
    'chunked_upload': action_chunked_upload, 'batch_deploy': action_batch_deploy,
    # 补齐 PHP 独有 action
    'webhook_set': action_webhook_set, 'webhook_list': action_webhook_list,
    'webhook_delete': action_webhook_delete, 'webhook_test': action_webhook_test,
    'oplog_append': action_oplog_append, 'oplog_list': action_oplog_list, 'oplog_view': action_oplog_view,
    'client_package': action_client_package, 'package_list': action_package_list,
    'package_delete': action_package_delete,
    'lint_batch': action_lint_batch, 'batch_rollback': action_batch_rollback,
    # FIX-v4.34 (性能): 单次往返执行多步, 把 N×RTT 压成 1×RTT
    'pipeline': action_pipeline,
}


# ==================== HTTP 处理 ====================
def _serve_package_by_key_http(handler, key):
    """GET /sync_api.php?pkg_key=<64hex>: 能力 URL 下载安装包 (无 token)."""
    pkg_dir = _package_dir(STATE.get('CONFIG') or {})
    ttl_default = int((STATE.get('CONFIG') or {}).get('PACKAGE_TTL', 86400) or 86400)
    if not re.match(r'^[a-f0-9]{64}$', key or '', re.I) or not os.path.isdir(pkg_dir):
        handler._send_json(fail(E['NOT_FOUND'], 'package_not_found',
                                'Package not found or link expired'), 404)
        return
    meta = None
    for mf in glob.glob(os.path.join(pkg_dir, 'ai-sync-client-*.zip.meta.json')):
        try:
            m = json.load(open(mf, encoding='utf-8'))
        except Exception:
            continue
        if isinstance(m, dict) and isinstance(m.get('key'), str):
            if _hmac.compare_digest(m['key'], key):
                meta = m
                break
    if meta is None or not meta.get('file'):
        handler._send_json(fail(E['NOT_FOUND'], 'package_not_found',
                                'Package not found or link expired'), 404)
        return
    zip_path = os.path.join(pkg_dir, os.path.basename(str(meta['file'])))
    ttl = max(60, int(meta.get('ttl') or ttl_default))
    created = int(meta.get('created') or (os.path.getmtime(zip_path) if os.path.isfile(zip_path) else 0))
    if not os.path.isfile(zip_path) or (time.time() - created) > ttl:
        try: os.remove(zip_path)
        except OSError: pass
        try: os.remove(zip_path + '.meta.json')
        except OSError: pass
        handler._send_json(fail(E['NOT_FOUND'], 'package_expired', 'Package expired',
                                {'hint': '请管理员重新执行 client_package 生成新包'}), 410)
        return
    try:
        with open(zip_path, 'rb') as f:
            blob = f.read()
    except OSError:
        handler._send_json(fail(E['NOT_FOUND'], 'package_not_found', 'Package not found'), 404)
        return
    audit_log('package_download', {}, 0, {
        'file': os.path.basename(zip_path), 'scope': str(meta.get('scope', '')),
        'ip': handler.client_address[0] if handler.client_address else '',
    })
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/zip')
    handler.send_header('Content-Length', str(len(blob)))
    handler.send_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(zip_path))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(blob)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    timeout = 30  # 空闲 keep-alive 连接超时回收, 防长期占线程
    disable_nagle_algorithm = True  # 设 TCP_NODELAY, 消除 Nagle+延迟ACK 造成的 ~40ms 卡顿

    def log_message(self, fmt, *args):
        pass  # 静默

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        # 每请求审计上下文 (rid/耗时/字节数): 必须在 JSON 解析前初始化, bad_json 也要留痕
        _REQUEST_CTX.rid = secrets.token_hex(4)
        _REQUEST_CTX.t0 = time.time()
        _REQUEST_CTX.req_bytes = length
        _REQUEST_CTX.detail = 0
        _REQUEST_CTX.action = ''
        _REQUEST_CTX.body = {}
        _REQUEST_CTX.cfg = None
        _REQUEST_CTX.ip = self.client_address[0]
        _REQUEST_CTX.remote_addr = self.client_address[0]
        _REQUEST_CTX.headers = self.headers
        raw = self.rfile.read(length) if length else b''
        # 空 body 不是合法 JSON (旧实现当 {} 处理 → 401 Token required, 与 PHP 的 bad_json 不一致)
        if not raw.strip():
            audit_log('', {}, E['INVALID_PARAM'], {'reason': 'bad_json'})
            self._send_json(fail(E['INVALID_PARAM'], 'bad_json', 'Request body must be a JSON object'), 200)
            return
        try:
            body = json.loads(raw.decode('utf-8'))
        except Exception:
            audit_log('', {}, E['INVALID_PARAM'], {'reason': 'bad_json'})
            self._send_json(fail(E['INVALID_PARAM'], 'bad_json', 'Request body must be a JSON object'), 200)
            return
        if not isinstance(body, dict):
            audit_log('', {}, E['INVALID_PARAM'], {'reason': 'bad_json'})
            self._send_json(fail(E['INVALID_PARAM'], 'bad_json', 'Request body must be a JSON object'), 200)
            return
        # pipeline 的 step 会递归调用 dispatch 并覆盖 _REQUEST_CTX.action/body,
        # 顶层审计必须固定用最外层请求的 action/body
        _top_action = body.get('action', '')
        _REQUEST_CTX.top_action = _top_action if isinstance(_top_action, str) else ''
        _REQUEST_CTX.top_body = body
        try:
            result = dispatch(body)
        except Exception as ex:
            # 兜底: dispatch 里任何未预料的异常都不能让连接直接断开 (调用方只会看到
            # RemoteDisconnected, 完全无从排查). 回 1099 + 服务端日志留 traceback.
            import traceback
            traceback.print_exc()
            try:
                audit_log(str(body.get('action', ''))[:40], body, E['INTERNAL'],
                          {'exception': str(ex)[:300], 'exception_class': type(ex).__name__})
            except Exception:
                pass
            result = fail(E['INTERNAL'], 'exception', 'Internal server error (see server log)',
                          {'exception_class': type(ex).__name__})
        # client_download: 原始字节流 (对齐 PHP 的 application/octet-stream + X-Client-Md5)
        if isinstance(result, dict) and result.get('_client_stream'):
            audit_request(getattr(_REQUEST_CTX, 'top_action', ''),
                          getattr(_REQUEST_CTX, 'top_body', {}) or {},
                          result, _ctx_cfg())
            self._send_client_download(result['_client_stream'], result['_client_md5'])
            return
        # 大文件下载: 流式直写 (避免整包 base64 常驻内存)
        if isinstance(result, dict) and result.get('_stream_file'):
            audit_request(getattr(_REQUEST_CTX, 'top_action', ''),
                          getattr(_REQUEST_CTX, 'top_body', {}) or {},
                          result, _ctx_cfg())
            self._send_stream_download(result['_stream_file'])
            return
        # 与 PHP 对齐: 错误码映射 HTTP 状态 (1001→401, 1002→403, 1003→429, 其余 200)
        _code = result.get('code', 0) if isinstance(result, dict) else 0
        _status = 401 if _code == E['UNAUTHORIZED'] else (
            403 if _code == E['FORBIDDEN'] else (
                429 if _code == E['RATE_LIMITED'] else 200))
        # _send_json 在序列化后统一补请求级审计 (resp_bytes 用未压缩 JSON 长度)
        self._send_json(result, _status, _audit=True)

    # 与 PHP(任意 method 读取 php://input) 对齐: 非 GET 的常见 method 同样按 JSON 请求处理,
    # 而不是落到 BaseHTTPRequestHandler 默认 501 + HTML —— 后者破坏 --json 调用方的解析契约.
    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def do_OPTIONS(self):
        # OPT-R12: CORS 预检。admin.html 若与服务端不同域/不同端口部署(本地调试、独立托管
        #   管理面板), 浏览器会先发 OPTIONS; 没有这个分支会 501, fetch 直接失败。
        self._send_cors(204, b'')

    def _cors_headers(self):
        """按配置的允许来源返回 CORS 头; 未配置时回显请求的 Origin(便于本地调试)。"""
        origin = self.headers.get('Origin', '')
        _c = _ctx_cfg() or {}
        allow = _c.get('CORS_ALLOW_ORIGIN', '*')
        if allow == '*':
            # 回显 Origin 而非写死 '*', 这样带凭证的请求也能过
            hdr_origin = origin if origin else '*'
        else:
            allowed = [x.strip() for x in str(allow).split(',') if x.strip()]
            hdr_origin = origin if (origin and origin in allowed) else (allowed[0] if allowed else '*')
        return [
            ('Access-Control-Allow-Origin', hdr_origin),
            ('Access-Control-Allow-Methods', 'POST, GET, OPTIONS'),
            ('Access-Control-Allow-Headers', 'Content-Type, X-Token, Authorization'),
            ('Access-Control-Max-Age', '86400'),
            # 对齐 PHP sync_api.php 的全局 header(): 每个响应都带 API 版本号
            ('X-AI-Sync-Version', str(_c.get('API_VERSION', '4.3'))),
            ('Vary', 'Origin'),
        ]

    def _send_cors(self, status, body):
        self.send_response(status)
        for k, v in self._cors_headers():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        # 带 body 的 GET: PHP 同样读取 php://input 并执行 action; 这里保持一致,
        # 否则同一请求 PHP 成功 / PY bad_json. 纯 GET (无 body) 仍走下面各分支.
        try:
            _clen = int(self.headers.get('Content-Length', 0) or 0)
        except Exception:
            _clen = 0
        if _clen > 0:
            return self.do_POST()
        # 健康检查端点 (PY 附加能力)
        if self.path in ('/', '/healthz', '/ping'):
            self._send_json({'ok': True, 'service': 'ai-sync-python', 'version': STATE['server_version']}, 200)
            return
        # 安装包能力下载 (与 PHP 一致): GET /sync_api.php?pkg_key=<64hex>, 无需 token
        _pu = urlparse(self.path)
        if _pu.path.rstrip('/').endswith('/sync_api.php') or _pu.path.rstrip('/') == '/sync_api.php':
            from urllib.parse import parse_qs
            _qs = parse_qs(_pu.query or '')
            if 'pkg_key' in _qs:
                _serve_package_by_key_http(self, (_qs.get('pkg_key') or [''])[0])
                return
        # 与 PHP 对齐: GET 到接口路径按空 body -> bad_json (HTTP 200)
        self._send_json(fail(E['INVALID_PARAM'], 'bad_json', 'Request body must be a JSON object'), 200)

    def do_HEAD(self):
        # 与 PHP 内置服务器一致: HEAD 不做业务处理, 回 200 + JSON Content-Type, 无正文。
        # 旧实现走 BaseHTTPRequestHandler 默认分支 → 501, 健康检查/探活用 HEAD 会误判服务不可用。
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('X-AI-Sync-Version', str((_ctx_cfg() or {}).get('API_VERSION', '4.3')))
        self.end_headers()

    def _send_json(self, obj, status, _audit=False):
        obj = _json_safe_links(obj)
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        if _audit:
            # 请求级兜底审计: 明细优先; resp_bytes 用未压缩 JSON 字节数 (与 PHP 一致)
            audit_request(getattr(_REQUEST_CTX, 'top_action', ''),
                          getattr(_REQUEST_CTX, 'top_body', {}) or {},
                          obj, _ctx_cfg(), len(body))
        accept_enc = self.headers.get('Accept-Encoding', '')
        can_gzip = ('gzip' in accept_enc.lower())
        _c = _ctx_cfg()
        threshold = _c.get('GZIP_THRESHOLD_KB', 16) * 1024
        force = _c.get('GZIP_FORCE', False)
        if can_gzip and (force or len(body) >= threshold):
            import gzip
            gz = gzip.compress(body, 6)
            self.send_response(status)
            for _k, _v in self._cors_headers():
                self.send_header(_k, _v)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('X-Original-Size', str(len(body)))
            self.send_header('Vary', 'Accept-Encoding')
            self.send_header('Content-Length', str(len(gz)))
            if status == 429:
                self.send_header('Retry-After', str(obj.get('retry_after', 60)))
            self.end_headers()
            self.wfile.write(gz)
        else:
            self.send_response(status)
            for _k, _v in self._cors_headers():
                self.send_header(_k, _v)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            if not self.headers.get('Origin'):
                self.send_header('Vary', 'Accept-Encoding')
            self.send_header('Content-Length', str(len(body)))
            if status == 429:
                self.send_header('Retry-After', str(obj.get('retry_after', 60)))
            self.end_headers()
            self.wfile.write(body)

    def _send_client_download(self, path, md5):
        """对齐 PHP action_client_download: 直接写参考 sync.py 原始字节, 不做 JSON/gzip 包装。"""
        try:
            with open(path, 'rb') as f:
                blob = f.read()
        except OSError:
            try:
                self._send_json(fail(E['NOT_FOUND'], 'not_found', 'client sync.py not found'), 200)
            except Exception:
                pass
            return
        self.send_response(200)
        for _k, _v in self._cors_headers():
            self.send_header(_k, _v)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(blob)))
        self.send_header('X-Client-Md5', md5)
        self.end_headers()
        try:
            self.wfile.write(blob)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_stream_download(self, path):
        # 流式下载: 单次顺序读, 边算 md5 边分块 base64 直写; 峰值内存 O(块)
        #   一遍读完即可定 Content-Length: md5 十六进制恒 32 字符, size 已知
        #   → 总长 = 前缀 + ceil(size/3)*4 + 中缀 + 32 + 后缀 (无需头一遍全盘扫描算 md5)
        import base64 as _b64
        try:
            f = open(path, 'rb')
        except OSError:
            try:
                self._send_json(fail(E['INTERNAL'], 'internal', 'Cannot open file for streaming'), 200)
            except Exception:
                pass
            return
        try:
            _st = os.fstat(f.fileno())
            size = _st.st_size
            b64len = ((size + 2) // 3) * 4
            # mode/mtime 一并回传, 客户端落盘后恢复 (与 PHP 端流式响应一致)
            _mode_s = '%04o' % (_st.st_mode & 0o777)
            _mtime_i = int(_st.st_mtime)
            prefix = b'{"content_b64": "'
            mid = ('", "size": %d, "mode": "%s", "mtime": %d, "md5": "'
                   % (size, _mode_s, _mtime_i)).encode()
            tail = b'", "ok": true, "code": 0}'
            total = len(prefix) + b64len + len(mid) + 32 + len(tail)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Vary', 'Accept-Encoding')
            self.send_header('Content-Length', str(total))
            self.end_headers()
            self.wfile.write(prefix)
            h = hashlib.md5()
            CHUNK = 3 * 32768  # 3 的整数倍: 保证各块 base64 无需中途 padding
            while True:
                c = f.read(CHUNK)
                if not c:
                    break
                h.update(c)
                self.wfile.write(_b64.b64encode(c))
            self.wfile.write(mid + h.hexdigest().encode('ascii') + tail)
        except Exception:
            # 已发头后无法再改状态; 直接结束该响应
            pass
        finally:
            try:
                f.close()
            except Exception:
                pass


# ==================== 参数类型规范化 ====================
# PHP 是弱类型 (int 会被隐式转 string, "12" 会被转 int)。Python 严格,
# 非字符串直接调 .lower()/.strip() 会抛 AttributeError, 数值字段收 "abc" 会 ValueError,
# 进而被兜底成 1099 "内部错误" —— 这是错误分类缺陷 (用户输入错误却报服务器故障)。
# 因此在入口统一规范化已知字段, 使行为与 PHP 一致, 并让真正的类型错误返回 1011。
_STR_FIELDS = {
    # 注意: 刻意不含 'cmd' —— 早期版本会把 {"cmd": 12345} 静默转成 "12345" 再执行,
    # AI 传错类型时只会看到 "12345: command not found", 看不出真正的问题是参数类型。
    # 不做转换后, exec / shell_run 各自的 cmd_must_be_string 才会生效(返回 1011), 与 PHP 端一致。
    'name', 'pattern', 'file_pattern', 'algorithm',
    # 刻意不含 content_b64/mode: 二者都要求精确类型 (字符串), 静默把 123 转成 "123"
    # 会让 Python 接受而 PHP 拒绝, 或把整数 mode 解释成另一套权限。由各 action 显式校验。
    'op', 'content', 'symbol', 'format', 'lint_type', 'relate_to',
    'target_token', 'new_token', 'scope', 'language', 'description',
    'url', 'id', 'file', 'keyword', 'tag', 'client_id', 'api_url',
    # 刻意不含 scope 的目录/日志文件字段 (work_dir/backup_dir/...): scope_add 要求严格字符串
    # (数组传给 re.match 会抛 TypeError), 若在此被 int->str 提前转换, Python 会接受而 PHP 拒绝.
    'secret',
    'group_by', 'type', 'local_dir', 'remote_subdir', 'doc_mode', 'post_check',
    'post_check_expect', 'pre_check', 'upload_id', 'base_md5', 'md5', 'sha256',
    'chunk_md5', 'final_md5', 'stdin_b64',
}
# 路径类字段: PHP 对它们严格要求字符串 (非字符串直接 1004 invalid_path),
# 不做 int->str 的宽松转换, 以保持与 PHP 一致。
_PATH_FIELDS = {'path', 'from', 'to', 'new_path', 'backup_file'}
_NUM_FIELDS = {
    'start_line', 'end_line', 'count', 'limit', 'offset', 'size', 'total_size', 'total',
    'chunk_size', 'since_mtime', 'before_ts', 'since_ts',
    'max_results', 'max_depth', 'max_files', 'max_size', 'max_line_len',
    'context', 'context_before', 'context_after', 'page', 'backup_keep',
    'chunk_size_default', 'chunk_ttl_days', 'exec_timeout_max', 'max_chunked_size',
}
# 注意: chunk_index 刻意不做 _php_intval 预转换 —— intval("abc")=0 会把分片内容
# 静默写到第 0 片, assemble 时才以 size_mismatch 暴露, AI 无法定位. 由 action 内严格校验.


class _PathTypeError(Exception):
    """路径类字段类型错误 (对齐 PHP: 非字符串 -> 1004 invalid_path)。"""


_NUM_PREFIX_RE = re.compile(r'^\s*([+-]?\d+)')


def _php_intval(v):
    """PHP intval() 语义: 取字符串前导整数, 解析不出数字则 0。

    对齐原因: PHP 各 action 用 intval($req['x'] ?? d), 传 "abc" 得到 0 而不是报错;
    早期 Python 版直接 int() 抛 ValueError → 变成 1011/1099, 与 PHP 行为不一致。
    """
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = _NUM_PREFIX_RE.match(v)
        return int(m.group(1)) if m else 0
    if isinstance(v, (list, dict)):
        return 1 if v else 0
    return 0


def _normalize_body(body):
    """把已知字段规范化为 action 期望的类型。
    注意: 路径类字段 (path/from/to/...) 不在此处理 —— safe_path() 已对非字符串返回 None
    并由各 action 转 1004, 这样不使用 path 的 action (exec/upload_batch) 不会被误拒。"""
    if not isinstance(body, dict):
        return body
    out = dict(body)
    # 记录原始字段名 (含 null 值字段), 供 _brief_keys 回显; 不参与业务逻辑
    out['_orig_keys'] = list(body.keys())
    for k, v in list(out.items()):
        if v is None:
            # PHP 全用 $req['x'] ?? default, null 等价于"未传"; 这里同样剔除, 让下游 .get(k, default) 生效
            out.pop(k, None)
            continue
        if isinstance(v, bool):
            continue
        if k in _STR_FIELDS:
            if isinstance(v, (int, float)):
                out[k] = str(v)
            # list/dict 保持原样: 无关 action 会忽略它 (PHP 亦如此); 真正用到时由路由层分类
        elif k == 'timeout':
            # PHP exec 用 is_numeric 严格校验, 而 shell_* 接受数字字符串; 这里不能把
            # "10s" 这类非法值静默截成 10 —— 保留原值, 由各 action 给出精确错误。
            if isinstance(v, str) and not _php_is_numeric(v):
                continue
            out[k] = _php_intval(v)
        elif k in _NUM_FIELDS:
            out[k] = _php_intval(v)
    return out


def dispatch(body):
    # 浅拷贝: merge_scope_into_cfg 是原地修改, 若直接引用 STATE['CONFIG']
    # 则所有并发请求共享同一个 dict, 互相覆盖 WORK_DIR/SCOPE → 跨 scope 串写
    cfg = dict(STATE['CONFIG'])
    # 鉴权
    # token 只接受字符串/数字: 数组/对象型 token 直接参与比较会抛 TypeError -> 1099,
    # 非标量按"未提供 token"处理 (401), 与 PHP 一致, 不泄露类型细节。
    _token_raw = body.get('token', '')
    req_token = str(_token_raw) if type(_token_raw) in (str, int, float) else ''
    if not STATE['MULTI_DIR']:
        if cfg['TOKEN'] == '' or not hmac_compare(cfg['TOKEN'], str(req_token)):
            audit_log('*', body, E['UNAUTHORIZED'])
            return fail(E['UNAUTHORIZED'], 'unauthorized',
                        'Server TOKEN not configured (fail-closed)' if cfg['TOKEN'] == '' else 'Invalid or missing token')
    else:
        raw = STATE['RAW_CONFIG']
        token_info = None
        is_admin = False
        if req_token == '':
            audit_log('*', body, E['UNAUTHORIZED'])
            return fail(E['UNAUTHORIZED'], 'unauthorized', 'Token required')
        if raw.get('admin_token') and hmac_compare(raw['admin_token'], str(req_token)):
            is_admin = True
            token_info = {'name': 'admin', 'scopes': list((raw.get('scopes') or {}).keys()), 'permissions': list(ALL_PERMS)}
        elif str(req_token) in (raw.get('tokens') or {}):
            token_info = dict(raw['tokens'][str(req_token)])
            token_info.setdefault('name', '')
            token_info.setdefault('scopes', [])
            token_info.setdefault('permissions', [])
        else:
            audit_log('*', body, E['UNAUTHORIZED'])
            return fail(E['UNAUTHORIZED'], 'unauthorized', 'Invalid or missing token')
        req_scope = body.get('scope', '')
        # 数组/对象型 scope 直接当 dict 键用会抛 TypeError -> 1099; 数字按字符串处理
        if type(req_scope) not in (str, int, float):
            audit_log('', body, E['INVALID_PARAM'], {'reason': 'bad_scope_type'})
            return fail(E['INVALID_PARAM'], 'bad_scope_type',
                        'scope must be a string, got %s' % _php_type_name(req_scope),
                        {'hint': 'scope 只接受字符串 (scope 名), 例: {"scope":"work"}'})
        req_scope = str(req_scope)
        scope_name = req_scope if req_scope != '' else (token_info['scopes'][0] if token_info['scopes'] else '')
        if scope_name == '' or scope_name not in (raw.get('scopes') or {}):
            audit_log('', body, E['INVALID_PARAM'], {'reason': 'invalid_scope', 'scope': scope_name})
            return fail(E['INVALID_PARAM'], 'invalid_scope', "Scope '%s' not found" % scope_name,
                        {'available': token_info['scopes']})
        if scope_name not in token_info['scopes']:
            audit_log(body.get('action', ''), body, E['FORBIDDEN'], {'reason': 'scope_forbidden', 'scope': scope_name})
            return fail(E['FORBIDDEN'], 'scope_forbidden', "Token has no access to scope '%s'" % scope_name)
        cfg = merge_scope_into_cfg(cfg, raw['scopes'][scope_name], scope_name, token_info, is_admin, str(req_token))
        # 注意: 不回写 STATE['CONFIG'] —— 那是进程级全局, 多线程并发写会互相覆盖;
        # 本请求的配置通过 _REQUEST_CTX.cfg 传递 (见下)。

    # 绑定本请求的线程上下文 (cfg 与 IP), 供 audit_log / 白名单 / rate_limit 使用。
    # 必须在 cfg 解析之后: XFF 解析依赖该 scope 的 TRUSTED_PROXY_IPS。
    _REQUEST_CTX.cfg = cfg
    _REQUEST_CTX.ip = client_ip(getattr(_REQUEST_CTX, 'remote_addr', ''),
                                getattr(_REQUEST_CTX, 'headers', {}))

    # IP 白名单
    if cfg.get('ALLOW_IPS'):
        ip = _ctx_ip()
        allowed = any(ip == rule or ('/' in rule and ip_in_cidr(ip, rule)) for rule in cfg['ALLOW_IPS'])
        if not allowed:
            audit_log('*', body, E['FORBIDDEN'])
            return fail(E['FORBIDDEN'], 'ip_blocked', "IP %s not in whitelist" % ip)

    # 参数严格类型 gate 在 action 校验之后执行 (见下), 这里先保留原始请求体:
    # _normalize_body 会把 int 转成 str, gate 必须看原始 JSON 类型才能拦住 true/123.
    _raw_req = body

    # 参数类型规范化 (对齐 PHP 弱类型; 参数类错误 -> 1011, 不再误报 1099 内部错误)
    try:
        body = _normalize_body(body)
    except _PathTypeError as ex:
        audit_log('', body, E['INVALID_PATH'], {'reason': 'bad_param', 'exception': str(ex)[:160]})
        return fail(E['INVALID_PATH'], 'invalid_path', str(ex)[:160])
    except ValueError as ex:
        audit_log('', body, E['INVALID_PARAM'], {'reason': 'bad_param', 'exception': str(ex)[:160]})
        return fail(E['INVALID_PARAM'], 'bad_param', str(ex)[:160])

    action = body.get('action', '')
    # 非字符串 action: 旧实现会让 int 落到 `action not in ROUTES` 后 len(action) TypeError
    # (或 list 不可哈希) -> 1099/断连; 参数错误必须分类为 1011。
    if not isinstance(action, str):
        _atype = _php_type_name(action)
        audit_log('', body, E['INVALID_PARAM'], {'reason': 'bad_action_type'})
        return fail(E['INVALID_PARAM'], 'bad_action_type',
                    'action must be a string, got %s' % _atype,
                    {'hint': 'action 只接受字符串, 例: {"action":"ping"}'})

    # 参数严格类型: 这些字段必须是字符串 (与 PHP 入口的 STRICT_STR_FIELDS 逐字对齐).
    # PHP 的弱转换会把 true/123 变成 "1"/"123" (Python 的 _normalize_body 也会), 于是
    # {"pattern": true} 被当成关键词 "1"; 传错类型时应给明确 1011, 不猜调用方意图.
    for _sf in ('pattern', 'file_pattern', 'algorithm', 'type', 'name', 'grep', 'relate_to',
                'url', 'content', 'language', 'description', 'keyword',
                'new_token', 'target_token',
                'pre_check', 'post_check', 'post_check_expect',
                'expect_md5', 'base_md5', 'md5', 'final_md5', 'chunk_md5', 'stdin_b64'):
        if _sf in _raw_req and _raw_req[_sf] is not None and not isinstance(_raw_req[_sf], str):
            audit_log(action, body, E['INVALID_PARAM'],
                      {'reason': 'bad_param_type', 'field': _sf})
            return fail(E['INVALID_PARAM'], 'bad_param',
                        '%s 必须是字符串, 收到 %s' % (_sf, _php_type_name(_raw_req[_sf])),
                        {'field': _sf,
                         'hint': '不要传 true/数字/数组/对象; 例: {"%s": "value"}' % _sf})
    # 非 UTF-8 路径的无损寻址: JSON 字符串无法承载任意字节, 调用方可传 path_b64/from_b64/to_b64.
    # 与 PHP 一致: 分发前覆盖同名字段, 所有 action 无需单独处理即可操作非 UTF-8 文件名.
    for _pf, _bf in (('path', 'path_b64'), ('from', 'from_b64'), ('to', 'to_b64')):
        if _bf not in body:
            continue
        _bv = body.get(_bf)
        if not isinstance(_bv, str) or _bv == '':
            audit_log(action, body, E['INVALID_PARAM'], {'reason': 'bad_' + _bf})
            return fail(E['INVALID_PARAM'], 'bad_path_b64',
                        '%s 必须是非空 base64 字符串' % _bf,
                        {'hint': '把原始路径字节 base64 编码后放入 %s; 与 %s 同时出现时以 %s 为准'
                                 % (_bf, _pf, _bf)})
        _raw = _b64_decode_php(_bv)
        if _raw is None:
            audit_log(action, body, E['INVALID_PARAM'], {'reason': 'bad_' + _bf})
            return fail(E['INVALID_PARAM'], 'bad_path_b64',
                        '%s base64 解码失败' % _bf,
                        {'hint': '%s 必须是原始路径字节的 base64' % _bf})
        _dec = _raw.decode('utf-8', 'surrogateescape')
        if _dec == '':
            audit_log(action, body, E['INVALID_PARAM'], {'reason': 'bad_' + _bf})
            return fail(E['INVALID_PARAM'], 'bad_path_b64',
                        '%s 解码结果为空' % _bf, {'hint': '%s 必须包含路径字节' % _bf})
        body[_pf] = _dec

    # 供 bad_path / reject_path 回显字段名与记审计 (路径类错误发生在 action 函数内部)
    _REQUEST_CTX.action = action
    _REQUEST_CTX.body = body
    if action not in ROUTES:
        audit_log(action, body, E['INVALID_PARAM'])
        cand = sorted(((_lev(action, n), n) for n in HELP_SPEC if n in ROUTES))
        lim = max(3, len(action) // 2)
        sug = [n for d, n in cand[:5] if d <= lim]
        return fail(E['INVALID_PARAM'], 'unknown_action', "Action '%s' not found" % action,
                    {'available': [a for a in HELP_SPEC if a in ROUTES],
                     'suggestions': sug,
                     'hint': '不带参数调用 help 可列出全部 action%s' %
                             ('; 是否想找: ' + ', '.join(sug) if sug else '')})

    # 权限校验
    if STATE['MULTI_DIR']:
        need = ACTION_PERMS.get(action, 'admin')
        if need not in cfg.get('PERMISSIONS', []):
            audit_log(action, body, E['FORBIDDEN'], {'required': need})
            return fail(E['FORBIDDEN'], 'forbidden', "Permission '%s' required for action '%s'" % (need, action),
                        {'required': need, 'your_permissions': cfg.get('PERMISSIONS', [])})

    # 惰性清理过期分片 (与 PHP 一致: 每请求触发, 600s 节流; 否则中断的上传会永久占盘)
    _now = time.time()
    if _now - STATE.get('_chunk_cleanup_ts', 0.0) > 600:
        STATE['_chunk_cleanup_ts'] = _now
        try:
            _cdir = os.path.join(cfg['WORK_DIR'].rstrip('/'), cfg.get('CHUNK_DIR', 'ai-sync/chunks'))
            if os.path.isdir(_cdir):
                _cleanup_expired_chunks(_cdir, cfg.get('CHUNK_TTL_DAYS', 7))
        except Exception:
            pass

    # 限流
    rl = rate_limit_check(body, cfg)
    if rl['limited']:
        audit_log(action, body, E['RATE_LIMITED'], {
            'reason': 'rate_limited', 'limit': rl.get('limit'),
            'window': rl.get('window'), 'retry_after': rl.get('retry_after')})
        return fail(E['RATE_LIMITED'], 'rate_limited', rl['message'],
                    {'limit': rl.get('limit'), 'window': rl.get('window'), 'retry_after': rl.get('retry_after')})

    try:
        return ROUTES[action](body, cfg)
    except (TypeError, ValueError, binascii.Error, UnicodeError) as ex:
        # 参数类型/编码错误: 属于调用方输入问题, 返回 1011 而非 1099
        audit_log(action, body, E['INVALID_PARAM'], {'exception': str(ex), 'exception_class': type(ex).__name__})
        return fail(E['INVALID_PARAM'], 'bad_param', 'Invalid parameter: %s' % str(ex)[:160],
                    {'exception_class': type(ex).__name__})
    except Exception as ex:
        audit_log(action, body, E['INTERNAL'], {'exception': str(ex), 'exception_class': type(ex).__name__})
        return fail(E['INTERNAL'], 'exception', 'Internal server error (see audit log)', {'exception_class': type(ex).__name__})


def hmac_compare(a, b):
    # 恒定时间比较
    a = str(a).encode('utf-8'); b = str(b).encode('utf-8')
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0


def main():
    parser = argparse.ArgumentParser(description='ai-sync Python server')
    parser.add_argument('--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sync_config.json'))
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5443)
    parser.add_argument('--tls-cert', default=None,
                        help='PEM 证书链文件; 与 --tls-key 同时给出即启用原生 HTTPS 直连(无需 nginx 反代)')
    parser.add_argument('--tls-key', default=None, help='PEM 私钥文件')
    args = parser.parse_args()

    STATE['config_path'] = args.config
    raw = load_sync_config(args.config)
    if raw is not None:
        STATE['RAW_CONFIG'] = raw
        STATE['MULTI_DIR'] = True
        STATE['CONFIG'] = build_boot_config(raw)
    else:
        STATE['MULTI_DIR'] = False
        STATE['CONFIG'] = dict(DEFAULT_CONFIG)
        # 用单目录默认配置时强制 TOKEN 空 -> fail-closed
    print('[ai-sync] multi_dir=%s config=%s' % (STATE['MULTI_DIR'], args.config))

    class _SyncHTTPServer(ThreadingHTTPServer):
        request_queue_size = 256          # listen 积压队列(默认5→256), 防并发突发掉连接
        daemon_threads = True
        allow_reuse_address = True
        _sem = threading.BoundedSemaphore(128)  # 并发工作线程上限, 超出由内核 backlog 背压

        def process_request(self, request, client_address):
            self._sem.acquire()
            try:
                return super().process_request(request, client_address)
            except Exception:
                self._sem.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._sem.release()

    server = _SyncHTTPServer((args.host, args.port), Handler)
    # 原生 TLS: 直接把监听 socket 包装成 TLS server socket, 不依赖 nginx 反代。
    # 不传 --tls-cert/--tls-key 时行为与原来完全一致 (纯 HTTP)。
    _tls = False
    if bool(args.tls_cert) != bool(args.tls_key):
        raise SystemExit('--tls-cert 与 --tls-key 必须同时提供')
    if args.tls_cert:
        import ssl
        _ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        _ctx.load_cert_chain(certfile=args.tls_cert, keyfile=args.tls_key)
        server.socket = _ctx.wrap_socket(server.socket, server_side=True)
        _tls = True
    STATE['tls_enabled'] = _tls
    print('[ai-sync] listening on %s://%s:%d (tls=%s)' %
          ('https' if _tls else 'http', args.host, args.port, _tls))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == '__main__':
    main()
