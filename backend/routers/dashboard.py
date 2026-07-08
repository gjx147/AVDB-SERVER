"""Dashboard 路由 —— 聚合统计（任务/观看/下载/磁盘）。"""

from __future__ import annotations

import logging
import shutil
from fastapi import APIRouter, Query
from sqlalchemy import func, select

from config import get_settings
from deps import CurrentUser, DbSession
from models import Download, Task

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def dashboard_stats(db: DbSession, _user: CurrentUser):
    """总览统计。"""
    total = db.execute(select(func.count(Task.id))).scalar_one()
    by_status = {
        r[0]: r[1]
        for r in db.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
    }
    by_view = {
        r[0]: r[1]
        for r in db.execute(
            select(Task.view_status, func.count(Task.id))
            .where(Task.view_status.isnot(None))
            .group_by(Task.view_status)
        ).all()
    }
    favorite = db.execute(select(func.count(Task.id)).where(Task.is_favorite == True)).scalar_one()  # noqa: E712
    downloads_total = db.execute(select(func.count(Download.id))).scalar_one()
    downloads_active = db.execute(
        select(func.count(Download.id)).where(Download.status.in_(["pushed", "downloading"]))
    ).scalar_one()

    # 磁盘
    data_dir = get_settings().DATA_DIR
    disk = {"total": 0, "used": 0, "free": 0}
    try:
        usage = shutil.disk_usage(data_dir)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except Exception:
        logger.warning("磁盘用量查询失败", exc_info=True)

    return {
        "tasks": {"total": total, "by_status": by_status},
        "view": by_view,
        "favorite": favorite,
        "downloads": {"total": downloads_total, "active": downloads_active},
        "disk": disk,
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
