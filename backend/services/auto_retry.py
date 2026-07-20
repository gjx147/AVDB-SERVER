"""自动重试调度服务 —— 定时重试失败任务。

读 DB settings:
- auto_retry_enabled: 是否启用
- auto_retry_interval: 重试间隔（秒）
- auto_retry_max_count: 最大重试次数

被 APScheduler 按间隔调用，把 status=failed 且 retry_count < max_count 的任务
重置为 pending，然后触发 scraper extract。
"""

from __future__ import annotations

import logging
import os

from database import SessionLocal
from models import Setting, Task

logger = logging.getLogger("avdb.auto_retry")


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


async def run_retry_cycle() -> dict:
    """检查失败任务并重试。"""
    db = SessionLocal()
    try:
        enabled = _get_setting(db, "auto_retry_enabled", "false").lower() == "true"
        if not enabled:
            return {"ok": False, "message": "自动重试未启用"}

        max_count = int(_get_setting(db, "auto_retry_max_count", "3"))
        interval = int(_get_setting(db, "auto_retry_interval", "300"))

        # 查找需要重试的失败任务
        from sqlalchemy import select, and_
        tasks = db.execute(
            select(Task).where(
                and_(
                    Task.status == "failed",
                    Task.retry_count < max_count,
                )
            )
        ).scalars().all()

        if not tasks:
            logger.info("自动重试: 无失败任务需要重试")
            return {"ok": True, "retried": 0}

        # 重置为 pending
        task_ids = []
        for t in tasks:
            t.status = "pending"
            task_ids.append(t.id)
        db.commit()

        logger.info("自动重试: 重置 %d 个失败任务为 pending (max_count=%d)", len(task_ids), max_count)

        # 触发 scraper extract（非阻塞，不等完成）
        try:
            from services import scraper_lock
            if scraper_lock.is_running():
                logger.warning("自动重试: 已有爬取在运行，跳过 extract 触发")
            else:
                from services.auto_crawl import _run_scraper
                import asyncio
                asyncio.create_task(_run_scraper(["extract", "--failed-only"]))
        except Exception as e:
            logger.warning("自动重试: 触发 extract 失败: %s", e)

        return {"ok": True, "retried": len(task_ids)}
    finally:
        db.close()


def register_job(interval: int = 300) -> None:
    """注册到调度中心。"""
    from services.scheduler import add_interval_job
    add_interval_job(run_retry_cycle, "auto-retry", seconds=interval)
    logger.info("auto_retry 已注册: 每 %ds 检查失败任务", interval)
