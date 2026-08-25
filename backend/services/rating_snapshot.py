"""评分快照（N15）：每日记录 visited 任务评分快照，供趋势分析。"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

logger = logging.getLogger("avdb.rating_snapshot")


async def run_snapshot() -> dict:
    """全量快照今日评分（幂等：当日已快照则跳过）。"""
    from database import SessionLocal
    from models import RatingHistory, Task

    db = SessionLocal()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        exist = db.execute(
            select(RatingHistory.id).where(RatingHistory.snapshot_date == today).limit(1)
        ).scalar_one_or_none()
        if exist:
            return {"ok": True, "skipped": True, "snapshotted": 0}
        rows = db.execute(
            select(Task.id, Task.rating).where(Task.rating.isnot(None))
        ).all()
        for tid, rating in rows:
            db.add(RatingHistory(task_id=tid, rating=float(rating), snapshot_date=today))
        db.commit()
        logger.info("评分快照完成: %d 条", len(rows))
        return {"ok": True, "skipped": False, "snapshotted": len(rows)}
    finally:
        db.close()
