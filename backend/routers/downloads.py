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


@router.get("/torrent-health")
def torrent_health(db: DbSession, _user: CurrentUser):
    """N19: qB 种子健康度——实时做种数 <5 标记低健康。"""
    from models import Download, Setting

    def _get(k: str) -> str:
        row = db.get(Setting, k)
        return row.value if row else ""

    qb_url, qb_user, qb_pass = _get("qb_url"), _get("qb_username"), _get("qb_password")
    if not qb_url:
        return {"ok": False, "message": "未配置 qBittorrent"}
    try:
        import qbittorrentapi
        qbc = qbittorrentapi.Client(host=qb_url, username=qb_user, password=qb_pass, REQUESTS_ARGS={"timeout": 8})
        qbc.auth_log_in()
        torrents = {t.infohash_v1.lower(): t for t in qbc.torrents_info() if t.infohash_v1}
        qbc.auth_log_out()
    except Exception as e:
        return {"ok": False, "message": f"qB 连接失败: {str(e)[:120]}"}

    rows = db.execute(
        select(Download).where(Download.status.in_(["pushed", "downloading"]))
    ).scalars().all()
    items = []
    for dl in rows:
        t = torrents.get((dl.info_hash or "").lower())
        if not t:
            continue
        seeder = int(getattr(t, "num_seeds", 0) or 0)
        items.append({
            "dl_id": dl.id, "video_code": dl.video_code,
            "seeder": seeder, "leecher": int(getattr(t, "num_leechs", 0) or 0),
            "progress": round(float(getattr(t, "progress", 0) or 0) * 100, 1),
            "healthy": seeder >= 5,
        })
    items.sort(key=lambda x: x["seeder"])
    return {"ok": True, "total": len(items), "items": items}
