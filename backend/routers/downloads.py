"""下载历史路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from deps import CurrentUser, DbSession, Pagination
from models import Download

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.get("")
def list_downloads(
    db: DbSession,
    _user: CurrentUser,
    pagination: Pagination,
    status: str | None = Query(None),
    downloader: str | None = Query(None),
):
    """下载历史列表，支持按状态/下载器筛选 + 分页。"""
    stmt = select(Download)
    count_stmt = select(func.count(Download.id))
    if status:
        stmt = stmt.where(Download.status == status)
        count_stmt = count_stmt.where(Download.status == status)
    if downloader:
        stmt = stmt.where(Download.downloader == downloader)
        count_stmt = count_stmt.where(Download.downloader == downloader)
    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = db.execute(stmt.order_by(Download.pushed_at.desc()).offset(offset).limit(limit)).scalars().all()
    return {"total": total, "page": offset // limit + 1, "page_size": limit, "items": items}


@router.get("/stats")
def download_stats(db: DbSession, _user: CurrentUser):
    """下载统计。"""
    rows = db.execute(
        select(Download.status, func.count(Download.id)).group_by(Download.status)
    ).all()
    by_status = {r[0]: r[1] for r in rows}
    by_downloader = {
        r[0]: r[1] for r in db.execute(
            select(Download.downloader, func.count(Download.id)).group_by(Download.downloader)
        ).all()
    }
    return {"by_status": by_status, "by_downloader": by_downloader}
