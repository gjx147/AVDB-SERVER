"""订阅演员「全部补齐作品」后台串行任务。

前端只负责触发与查看进度——任务跑在 FastAPI 进程内的 daemon 线程里，
切走页面/刷新页面不中断（容器重启才会中止，重启后状态回到空闲）。

串行原因：scraper 全局锁同一时刻只允许一个爬取进程。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("avdb.actor_works_batch")

# 独立日志文件：data/actor_works_batch.log（与 app.log 分开，便于查看补齐过程）
_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
os.makedirs(_log_dir, exist_ok=True)
_log_path = os.path.join(_log_dir, "actor_works_batch.log")
_file_handler = logging.FileHandler(_log_path, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_file_handler)

_state = {
    "running": False,
    "total": 0,
    "idx": 0,
    "current_actor_id": None,
    "current_name": None,
    "done": 0,
    "skipped": 0,
    "failed": 0,
    "marked_skipped": 0,
    "wait_limit_min": 60,
    "max_co_star": 0,
    "last_summary": None,
}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def status() -> dict:
    """当前任务状态（供前端轮询）。"""
    with _lock:
        return dict(_state)


def start(wait_limit_min: int = 60, max_co_star: int = 0, force: bool = False) -> tuple[bool, str]:
    """启动后台任务。已在运行返回 (False, 原因)。

    force=False：增量——跳过已标记 works_fetched 的演员（默认）
    force=True：重补全部订阅演员（不排除已补齐的）
    """
    global _thread
    with _lock:
        if _state["running"]:
            return False, "补齐任务已在运行"
        _state.update({
            "running": True,
            "total": 0, "idx": 0,
            "current_actor_id": None, "current_name": None,
            "done": 0, "skipped": 0, "failed": 0, "marked_skipped": 0,
            "wait_limit_min": max(1, min(2880, int(wait_limit_min or 60))),
            "max_co_star": max(0, int(max_co_star or 0)),
            "force": bool(force),
            "last_summary": None,
        })
    logger.info("全部补齐作品启动: force=%s wait=%dmin", force, wait_limit_min)
    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()
    return True, "已启动"


def _wait_lock_idle(wait_min: int) -> bool:
    """等 scraper 全局锁空闲（3~30s 自适应轮询）。"""
    from services.scraper_lock import is_running
    interval = max(3, min(30, wait_min * 2))
    deadline = time.time() + wait_min * 60
    while time.time() < deadline:
        try:
            if not is_running():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _bump(key: str) -> None:
    with _lock:
        _state[key] += 1


def _run() -> None:
    from sqlalchemy import func, or_, select

    from database import SessionLocal
    from models import Actor, Subscription
    from routers.crawl import start_actor_crawl

    run_force = bool(_state.get("force"))
    db = SessionLocal()
    try:
        conds = [Subscription.sub_type == "actor", Subscription.actor_id.isnot(None)]
        if not run_force:
            conds.append(or_(Actor.works_fetched.is_(None), Actor.works_fetched == False))
        rows = db.execute(
            select(Subscription.actor_id, Subscription.name)
            .join(Actor, Actor.id == Subscription.actor_id)
            .where(*conds)
        ).all()
        # 已补齐标记的演员计数（跳过并计入总结）
        marked = db.execute(
            select(func.count(Actor.id))
            .join(Subscription, Subscription.actor_id == Actor.id)
            .where(Subscription.sub_type == "actor", Actor.works_fetched == True)  # noqa: E712
        ).scalar_one()
        sub_list = [(r[0], r[1]) for r in rows]
    finally:
        db.close()

    with _lock:
        _state["total"] = len(sub_list)
        _state["marked_skipped"] = int(marked)
    wait_min = _state["wait_limit_min"]
    max_co = _state["max_co_star"]
    logger.info("全部补齐作品开始: %d 位演员（已补齐跳过 %d 位；每演员等待上限 %d 分钟，最大共演 %s）",
                len(sub_list), marked, wait_min, f"{max_co} 人" if max_co > 0 else "不限")

    for i, (actor_id, name) in enumerate(sub_list, start=1):
        with _lock:
            _state["idx"] = i
            _state["current_actor_id"] = actor_id
            _state["current_name"] = name
        logger.info("补齐 %s（%d/%d）", name, i, len(sub_list))
        # ① 先等其它爬取任务结束
        if not _wait_lock_idle(wait_min):
            _bump("failed")
            logger.warning("补齐 %s: 等待其它爬取任务超过 %d 分钟，跳过", name, wait_min)
            continue
        # ② 读 source_url（note 兜底，与 crawl-works 端点一致）
        url = None
        db = SessionLocal()
        try:
            actor = db.get(Actor, actor_id)
            if actor:
                url = actor.source_url
                if not url and actor.note and actor.note.startswith("source_url: "):
                    url = actor.note[len("source_url: "):]
        finally:
            db.close()
        if not url:
            _bump("skipped")
            logger.info("补齐 %s: 无 JavDB URL，跳过", name)
            continue
        # ③ 触发爬取（409 锁竞争等空闲重试一次）
        started = False
        try:
            start_actor_crawl(url, actor_id=actor_id, max_co_star=max_co)
            started = True
        except Exception as e:
            if "已有爬取任务" in str(e) and _wait_lock_idle(wait_min):
                try:
                    start_actor_crawl(url, actor_id=actor_id, max_co_star=max_co)
                    started = True
                except Exception as e2:
                    logger.warning("补齐 %s 触发失败: %s", name, e2)
            else:
                logger.warning("补齐 %s 触发失败: %s", name, e)
        if not started:
            _bump("failed")
            continue
        # ④ 等这位演员爬完再继续下一位
        if _wait_lock_idle(wait_min):
            _bump("done")
            logger.info("补齐 %s 完成（%d/%d）", name, i, len(sub_list))
        else:
            _bump("done")
            logger.warning("补齐 %s: 超过 %d 分钟仍未爬完，继续下一位", name, wait_min)

    with _lock:
        _state["running"] = False
        _state["current_actor_id"] = None
        _state["current_name"] = None
        _state["last_summary"] = (
            f"全部补齐完成：成功 {_state['done']}，跳过 {_state['skipped']}（无 JavDB URL），"
            f"已补齐跳过 {_state['marked_skipped']}，失败 {_state['failed']}"
        )
    logger.info("全部补齐作品结束: %s", _state["last_summary"])
