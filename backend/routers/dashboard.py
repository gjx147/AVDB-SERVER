"""Dashboard 路由 —— 聚合统计（任务/观看/下载/磁盘）。"""

from __future__ import annotations

import logging
import shutil
from fastapi import APIRouter, Query

logger = logging.getLogger("avdb.dashboard")
from sqlalchemy import func, select

from config import get_settings
from deps import CurrentUser, DbSession
from models import Download, Task

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats(db: DbSession, _user: CurrentUser):
    """总览统计。返回前端 DashboardStats 期望的扁平结构。"""
    from models import Actor
    total = db.execute(select(func.count(Task.id))).scalar_one()
    by_status = {
        r[0]: r[1]
        for r in db.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
    }
    favorite = db.execute(select(func.count(Task.id)).where(Task.is_favorite == True)).scalar_one()  # noqa: E712
    actor_count = db.execute(select(func.count(Actor.id))).scalar_one()
    total_magnets = db.execute(
        select(func.count(Task.id)).where(Task.best_magnet.isnot(None))
    ).scalar_one()

    # 磁盘
    data_dir = get_settings().DATA_DIR
    disk = {"total": 0, "used": 0, "free": 0}
    try:
        usage = shutil.disk_usage(data_dir)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except Exception:
        logger.warning("磁盘用量查询失败", exc_info=True)

    # 返回前端期望的扁平结构（兼容 AVDB admin-new DashboardStats 类型）
    return {
        "total_tasks": total,
        "visited_tasks": by_status.get("visited", 0),
        "pending_tasks": by_status.get("pending", 0),
        "failed_tasks": by_status.get("failed", 0),
        "favorite_count": favorite,
        "actor_count": actor_count,
        "total_magnets": total_magnets,
        "db_size_mb": round(disk["used"] / 1024 / 1024, 1),
    }


@router.get("/recent")
def recent_tasks(db: DbSession, _user: CurrentUser, limit: int = Query(12, le=200)):
    """最近入库任务。"""
    tasks = db.execute(
        select(Task).where(Task.status == "visited").order_by(Task.created_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {"id": t.id, "video_code": t.video_code, "title": t.title, "poster_url": t.poster_url, "created_at": str(t.created_at)}
        for t in tasks
    ]


@router.get("/monthly")
def monthly_stats(db: DbSession, _user: CurrentUser):
    """月度统计（兼容 AVDB 前端 Dashboard 月视图）。按 created_at 年月聚合。"""
    rows = db.execute(
        select(func.substr(Task.created_at, 1, 7).label("month"), func.count(Task.id))
        .where(Task.status == "visited")
        .group_by("month")
        .order_by("month")
    ).all()
    return [{"month": r[0], "count": r[1]} for r in rows]
