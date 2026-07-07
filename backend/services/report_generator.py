"""数据洞察 + 月报生成 —— SQL 聚合统计。

参考 JavdBviewed insights。SQL 聚合在服务端比浏览器对 IndexedDB 快几个数量级。
- top_from_column: 从逗号分隔字段统计 Top N（actors/tags/maker）
- aggregate_month: 聚合某月数据
- generate_report: 生成月报存入 insight_reports 表
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from database import SessionLocal
from models import InsightReport, Task

logger = logging.getLogger("avdb.insights")


def _top_from_column(values: list[str], top_n: int = 10) -> list[dict]:
    """从逗号分隔的字段列表统计 Top N。"""
    counter: Counter[str] = Counter()
    for v in values:
        if not v:
            continue
        for item in v.split(","):
            item = item.strip()
            if item:
                counter[item] += 1
    return [{"name": k, "count": c} for k, c in counter.most_common(top_n)]


def _hhi(distribution: list[int]) -> float:
    """赫芬达尔指数（集中度，0=完全分散，1=完全集中）。"""
    total = sum(distribution)
    if total == 0:
        return 0.0
    return sum((n / total) ** 2 for n in distribution)


def aggregate(db, *, month: str | None = None) -> dict:
    """聚合统计数据。month 格式 YYYY-MM，空则全部。"""
    stmt = select(Task).where(Task.status == "visited")
    if month:
        # 按 release_date 或 created_at 的年月过滤
        stmt = stmt.where(
            (func.substr(Task.release_date, 1, 7) == month)
            | (func.substr(Task.created_at, 1, 7) == month)
        )
    tasks = db.execute(stmt).scalars().all()

    total = len(tasks)
    # Top 演员/标签/厂牌
    actors_vals = [t.actors for t in tasks if t.actors]
    tags_vals = [t.tags for t in tasks if t.tags]
    makers_vals = [t.maker for t in tasks if t.maker]
    top_actors = _top_from_column(actors_vals, 10)
    top_tags = _top_from_column(tags_vals, 10)
    top_makers = _top_from_column(makers_vals, 10)

    # 评分分布
    rating_buckets = {"<6": 0, "6-7": 0, "7-8": 0, "8-9": 0, "9-10": 0}
    rated = [t.rating for t in tasks if t.rating is not None]
    for r in rated:
        if r < 6:
            rating_buckets["<6"] += 1
        elif r < 7:
            rating_buckets["6-7"] += 1
        elif r < 8:
            rating_buckets["7-8"] += 1
        elif r < 9:
            rating_buckets["8-9"] += 1
        else:
            rating_buckets["9-10"] += 1
    rating_dist = [{"bucket": k, "count": v} for k, v in rating_buckets.items()]

    # 观看状态分布
    status_dist = {"viewed": 0, "browsed": 0, "want": 0, "unmarked": 0}
    for t in tasks:
        s = t.view_status or "unmarked"
        status_dist[s] = status_dist.get(s, 0) + 1

    # 集中度指标
    top3_ratio = sum(a["count"] for a in top_actors[:3]) / total if total and top_actors else 0.0
    actor_hhi = _hhi([a["count"] for a in top_actors]) if top_actors else 0.0
    avg_rating = sum(rated) / len(rated) if rated else 0.0

    return {
        "month": month or "all",
        "total": total,
        "top_actors": top_actors,
        "top_tags": top_tags,
        "top_makers": top_makers,
        "rating_dist": rating_dist,
        "avg_rating": round(avg_rating, 2),
        "status_dist": status_dist,
        "concentration_top3": round(top3_ratio, 3),
        "actor_hhi": round(actor_hhi, 3),
    }


def generate_report(db, month: str | None = None) -> dict:
    """生成月报并存入 insight_reports 表。返回报告内容。"""
    if not month:
        month = datetime.utcnow().strftime("%Y-%m")
    stats = aggregate(db, month=month)
    # 检查是否已有该月报告
    existing = db.execute(
        select(InsightReport).where(InsightReport.month == month)
    ).scalar_one_or_none()
    stats_json = json.dumps(stats, ensure_ascii=False)
    if existing:
        existing.stats_json = stats_json
        report = existing
    else:
        report = InsightReport(month=month, stats_json=stats_json)
        db.add(report)
    db.commit()
    db.refresh(report)
    logger.info("生成月报: %s (total=%d)", month, stats["total"])
    return stats


def get_report(db, month: str) -> dict | None:
    """读取已存的月报。"""
    report = db.execute(
        select(InsightReport).where(InsightReport.month == month)
    ).scalar_one_or_none()
    if not report:
        return None
    data = json.loads(report.stats_json)
    if report.summary:
        data["summary"] = report.summary
    return data
