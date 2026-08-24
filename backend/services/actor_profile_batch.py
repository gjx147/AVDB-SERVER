"""演员信息一键提取后台任务（演员库「一键提取演员信息」按钮）。

复用 actor_profile_sync.run_cycle 的抓取逻辑（含限速/男演员跳过/锁定保护），
在后台线程里循环跑轮次直到待抓队列清空。前端只负责触发与轮询进度，
切走页面/刷新不中断。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("avdb.actor_profile_batch")

_state = {
    "running": False,
    "total": 0,
    "idx": 0,
    "current_name": None,
    "done": 0,
    "skipped": 0,
    "failed": 0,
    "last_summary": None,
}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def status() -> dict:
    """当前任务状态（供前端轮询）。"""
    with _lock:
        return dict(_state)


def start() -> tuple[bool, str]:
    """启动后台任务。已在运行返回 (False, 原因)。"""
    global _thread
    with _lock:
        if _state["running"]:
            return False, "提取任务已在运行"
        _state.update({
            "running": True, "total": 0, "idx": 0, "current_name": None,
            "done": 0, "skipped": 0, "failed": 0, "last_summary": None,
        })
    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()
    return True, "已启动"


def _run() -> None:
    from sqlalchemy import func, or_, select

    from database import SessionLocal
    from models import Actor
    from services import actor_profile_sync

    # 互斥：定时同步正在运行时，手动一键提取不启动（非阻塞获取锁，失败则跳过并记日志）
    if not actor_profile_sync.acquire_cycle_lock():
        logger.warning("演员信息一键提取跳过：定时同步正在运行")
        with _lock:
            _state["running"] = False
            _state["last_summary"] = "提取已跳过：定时同步正在运行"
        return

    def _hook(name: str) -> None:
        with _lock:
            _state["current_name"] = name

    actor_profile_sync.set_progress_hook(_hook)
    try:
        db = SessionLocal()
        try:
            total = db.execute(
                select(func.count(Actor.id)).where(
                    Actor.profile_fetched.is_(False),
                    Actor.profile_fetch_failed.is_(False),
                    or_(Actor.gender.is_(None), Actor.gender != 'male'),
                )
            ).scalar_one()
        finally:
            db.close()
        with _lock:
            _state["total"] = int(total)
        logger.info("演员信息一键提取开始: 待抓 %d 位", total)

        while True:
            try:
                r = actor_profile_sync.run_cycle(_lock_held=True)
            except Exception as e:
                logger.warning("演员信息提取轮次异常: %s", e)
                with _lock:
                    _state["failed"] += 1
                break
            n = r.get("fetched", 0) + r.get("skipped", 0)
            with _lock:
                _state["done"] += r.get("fetched", 0)
                _state["skipped"] += r.get("skipped", 0)
                _state["idx"] += n
            if n == 0:
                break
            logger.info("演员信息提取进度: %d/%d（成功 %d，未命中 %d）",
                        _state["idx"], _state["total"], _state["done"], _state["skipped"])
            time.sleep(3)  # 轮间稍歇，避免长时间连续轰炸三源
    finally:
        actor_profile_sync.set_progress_hook(None)
        actor_profile_sync.release_cycle_lock()

    with _lock:
        _state["running"] = False
        _state["current_name"] = None
        _state["last_summary"] = (
            f"演员信息提取完成：成功 {_state['done']}，未命中 {_state['skipped']}，失败 {_state['failed']}"
        )
    logger.info("演员信息一键提取结束: %s", _state["last_summary"])
