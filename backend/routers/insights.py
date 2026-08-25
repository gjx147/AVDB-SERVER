"""数据洞察路由 —— 聚合统计 + 月报。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from deps import CurrentUser, DbSession
from services.report_generator import aggregate, generate_report, get_report

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/stats")
def insights_stats(db: DbSession, _user: CurrentUser, month: str | None = Query(None, description="YYYY-MM，空则全部")):
    """实时聚合统计（不入库）。"""
    return aggregate(db, month=month)


@router.post("/reports/{month}")
def create_report(month: str, db: DbSession, _user: CurrentUser):
    """生成/刷新某月月报（存入 insight_reports 表）。"""
    if not month or len(month) != 7:
        raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
    return generate_report(db, month=month)


@router.get("/reports/{month}")
def read_report(month: str, db: DbSession, _user: CurrentUser):
    """读取已存的月报。"""
    report = get_report(db, month)
    if not report:
        raise HTTPException(status_code=404, detail="该月报告未生成")
    return report



@router.get("/activity-heatmap")
def activity_heatmap(db: DbSession, _user: CurrentUser, days: int = Query(180, le=365)):
    """活动热力（F5）：收藏 + 下载行为按天聚合（GitHub 风格热力图数据源）。"""
    from datetime import datetime, timedelta
    from models import Download, task_collections

    since = datetime.utcnow() - timedelta(days=days)
    fav_rows = db.execute(
        select(task_collections.c.created_at).where(task_collections.c.created_at >= since)
    ).all()
    dl_rows = db.execute(
        select(Download.created_at).where(Download.created_at >= since)
    ).all()

    days_map: dict[str, dict] = {}
    for (ts,) in fav_rows:
        if ts:
            d = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            days_map.setdefault(d, {"favorites": 0, "downloads": 0})["favorites"] += 1
    for (ts,) in dl_rows:
        if ts:
            d = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            days_map.setdefault(d, {"favorites": 0, "downloads": 0})["downloads"] += 1
    return {"days": days_map, "total_days": days}
