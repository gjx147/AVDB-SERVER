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
