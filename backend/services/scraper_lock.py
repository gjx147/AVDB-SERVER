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

# ── 双通道（订阅上新 / Top250 分离）──
# main = 订阅/常规爬取（crawl-actor / extract-single / scan / ranking / extract）
# top250 = Top250 入库与提取（search-movie / TOP250 源 extract）
# 两通道各自持锁 + 各自浏览器配置目录（SCRAPER_PROFILE env 区分），互不阻塞。
# is_running() 不传 channel 时表示"任一通道在跑"（保守语义：登录互斥等场景用）。
CHANNEL_MAIN = "main"
CHANNEL_TOP250 = "top250"
_channels: tuple[str, ...] = (CHANNEL_MAIN, CHANNEL_TOP250)
_procs: dict[str, subprocess.Popen | None] = {c: None for c in _channels}
_infos: dict[str, dict] = {c: {} for c in _channels}
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
        if is_proc_alive(_procs[CHANNEL_MAIN]):
            return False
        return True


def try_acquire_and_set(proc: subprocess.Popen, info: dict, channel: str = CHANNEL_MAIN) -> bool:
    """原子获取锁并登记进程（检查 + 注册在同一把锁内完成）。

    返回 True = 获取成功且已登记 proc+info；返回 False = 已有 scraper 在跑。
    解决原 try_acquire() → ... → set_proc() 之间的 TOCTOU 窗口：
    两个并发请求可能同时通过 try_acquire，各自启动一个 Chromium 互踩。
    调用方拿到 False 时应回收自己刚启动的进程。
    """
    with _lock:
        global _procs, _infos
        if channel not in _procs:
            channel = CHANNEL_MAIN
        if is_proc_alive(_procs[channel]):
            return False
        _procs[channel] = proc
        _infos[channel] = info
        return True


def set_proc(proc: subprocess.Popen, info: dict, channel: str = CHANNEL_MAIN) -> None:
    """注册已启动的 scraper 子进程 + 元信息。"""
    with _lock:
        global _procs, _infos
        if channel not in _procs:
            channel = CHANNEL_MAIN
        _procs[channel] = proc
        _infos[channel] = info


def is_running(channel: str | None = None) -> bool:
    """当前是否有 scraper 在跑。channel=None 表示任一通道。"""
    with _lock:
        if channel is None:
            return any(is_proc_alive(p) for p in _procs.values())
        if channel not in _procs:
            channel = CHANNEL_MAIN
        return is_proc_alive(_procs[channel])


def get_proc(channel: str = CHANNEL_MAIN) -> subprocess.Popen | None:
    with _lock:
        if channel not in _procs:
            channel = CHANNEL_MAIN
        return _procs[channel]


def get_info(channel: str = CHANNEL_MAIN) -> dict:
    with _lock:
        if channel not in _procs:
            channel = CHANNEL_MAIN
        return dict(_infos[channel])


def find_channel_by_pid(pid) -> str | None:
    """按子进程 pid 找登记通道（register 回调用）。找不到返回 None。"""
    if pid is None:
        return None
    try:
        pid = int(pid)
    except Exception:
        return None
    with _lock:
        for c in _channels:
            p = _procs.get(c)
            if p is not None and p.pid == pid:
                return c
    return None


def clear(channel: str = CHANNEL_MAIN) -> None:
    """清理通道锁（进程结束/被杀后调用）。"""
    with _lock:
        if channel not in _procs:
            channel = CHANNEL_MAIN
        _procs[channel] = None
        _infos[channel] = {}


def clear_if_current(proc: subprocess.Popen | None, channel: str = CHANNEL_MAIN) -> None:
    """按身份释放：proc 仍是该通道登记的进程才清（防 ABA）。"""
    with _lock:
        if channel not in _procs:
            channel = CHANNEL_MAIN
        if proc is not None and _procs[channel] is proc:
            _procs[channel] = None
            _infos[channel] = {}