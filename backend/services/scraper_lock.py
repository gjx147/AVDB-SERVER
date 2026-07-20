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

import subprocess
from threading import Lock

_proc: subprocess.Popen | None = None
_info: dict = {}
_lock = Lock()


def try_acquire() -> bool:
    """尝试获取锁（非阻塞）。

    返回 True = 获取成功（调用方应随后启动子进程并调 set_proc 注册）；
    返回 False = 已有 scraper 在跑，调用方应跳过。
    """
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return False
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
        return _proc is not None and _proc.poll() is None


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
