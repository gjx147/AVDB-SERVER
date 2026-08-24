"""全局爬取锁 —— 所有 scraper 触发路径（手动 HTTP + 定时任务）共用。

解决的问题（审计 H5）：
- 之前有 3 套互不感知的"运行中"状态：
  1. routers/crawl.py 的模块级 _running_proc（HTTP 触发用）
  2. services/auto_crawl.py 的 _state["running"]（定时 scan/extract 用）
  3. services/auto_retry.py / ranking_auto_crawl.py 完全不检查锁
- 后果：手动 + 定时可同时启动两个 Playwright Chromium，互踩 cookie/session。

本模块提供进程级单例锁，所有触发路径统一 acquire/release。
"""

from __future__ import annotations

import secrets
import subprocess
from threading import Lock

_proc: subprocess.Popen | None = None
_info: dict = {}
_lock = Lock()

# ── Phase 2 回调共享密钥 ──
# scraper 子进程 register/unregister 回调的共享密钥（原 crawl.py 模块级变量）。
# 移到本模块后，所有触发路径（HTTP 手动 crawl.py / 定时 auto_crawl /
# 单任务 tasks / 新作监控 new_works_monitor）启动子进程时都从同一处取密钥注入 env，
# 修复 auto_crawl / new_works_monitor / tasks 路径子进程回调被 401 拒绝的问题。
_callback_token: str = secrets.token_urlsafe(32)


def rotate_callback_token() -> str:
    """生成新回调密钥并返回（crawl.py 每次启动 scraper 时调用轮换）。"""
    global _callback_token
    _callback_token = secrets.token_urlsafe(32)
    return _callback_token


def get_callback_token() -> str:
    """返回当前回调密钥（所有启动路径注入子进程 env 用）。"""
    return _callback_token


def is_proc_alive(proc) -> bool:
    """判断子进程是否存活：兼容 subprocess.Popen（同步）与 asyncio.subprocess.Process（异步）。

    asyncio Process 没有 .poll() 方法，改用 returncode（await wait() 后更新；None = 存活）。
    修复生产报错: 'Process' object has no attribute 'poll'（new_works_monitor 等异步路径注册的进程）。
    """
    if proc is None:
        return False
    if hasattr(proc, "poll"):
        return proc.poll() is None
    return proc.returncode is None


def try_acquire() -> bool:
    """尝试获取锁（非阻塞）。

    返回 True = 获取成功（调用方应随后启动子进程并调 set_proc 注册）；
    返回 False = 已有 scraper 在跑，调用方应跳过。
    """
    with _lock:
        if is_proc_alive(_proc):
            return False
        return True


def try_acquire_and_set(proc: subprocess.Popen, info: dict) -> bool:
    """原子获取锁并登记进程（检查 + 注册在同一把锁内完成）。

    返回 True = 获取成功且已登记 proc+info；返回 False = 已有 scraper 在跑。
    解决原 try_acquire() → ... → set_proc() 之间的 TOCTOU 窗口：
    两个并发请求可能同时通过 try_acquire，各自启动一个 Chromium 互踩。
    调用方拿到 False 时应回收自己刚启动的进程。
    """
    with _lock:
        global _proc, _info
        if is_proc_alive(_proc):
            return False
        _proc = proc
        _info = info
        return True


def set_proc(proc: subprocess.Popen, info: dict) -> None:
    """注册已启动的 scraper 子进程 + 元信息。"""
    with _lock:
        global _proc, _info
        _proc = proc
        _info = info


def is_running() -> bool:
    """当前是否有 scraper 在跑。"""
    with _lock:
        return is_proc_alive(_proc)


def get_proc() -> subprocess.Popen | None:
    with _lock:
        return _proc


def get_info() -> dict:
    with _lock:
        return dict(_info)


def clear() -> None:
    """清理进程引用（停止/超时/退出后调用）。"""
    with _lock:
        global _proc
        _proc = None
