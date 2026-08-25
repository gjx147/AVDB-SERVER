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



# F8: 个性化推荐（收藏/已看偏好 → 未看作品打分；5 分钟缓存）
_recommend_cache: dict = {"ts": 0.0, "data": None}
_RECOMMEND_TTL = 300.0


@router.get("/recommendations")
def recommendations(db: DbSession, _user: CurrentUser, limit: int = Query(6, le=20)):
    """个性化推荐：从收藏与已看作品聚合演员/标签/厂商偏好，
    对未看作品打分排序（偏好权重 + 评分加成）。"""
    import time
    from collections import Counter
    from models import Task, task_collections

    now = time.monotonic()
    if _recommend_cache["data"] is not None and now - _recommend_cache["ts"] < _RECOMMEND_TTL:
        return _recommend_cache["data"]

    # 已收藏 + 已看/想看的 task id
    fav_ids = {r[0] for r in db.execute(select(task_collections.c.task_id)).all()}
    viewed_ids = {r[0] for r in db.execute(
        select(Task.id).where(Task.view_status.in_(["viewed", "want"]))
    ).all()}
    known = fav_ids | viewed_ids

    # 偏好画像
    pref_rows = db.execute(
        select(Task.actors, Task.tags, Task.maker).where(Task.id.in_(known))
    ).all() if known else []
    actor_pref: Counter = Counter()
    tag_pref: Counter = Counter()
    maker_pref: Counter = Counter()
    for actors, tags, maker in pref_rows:
        for a in (actors or "").split(","):
            if a.strip():
                actor_pref[a.strip()] += 1
        for t in (tags or "").split(","):
            if t.strip():
                tag_pref[t.strip()] += 1
        if maker:
            maker_pref[maker] += 1

    if not (actor_pref or tag_pref or maker_pref):
        # 无偏好：返回高分未看
        rows = db.execute(
            select(Task).where(
                Task.rating.isnot(None), Task.video_code.isnot(None),
                ~Task.id.in_(known) if known else Task.id.isnot(None),
            ).order_by(Task.rating.desc()).limit(limit)
        ).scalars().all()
        result = {"items": [
            {"task_id": t.id, "video_code": t.video_code, "title": t.title,
             "rating": t.rating, "poster_url": t.poster_url, "score": None, "match": []}
            for t in rows
        ], "reason": "no_pref"}
        _recommend_cache["ts"] = now
        _recommend_cache["data"] = result
        return result

    # 候选：未收藏未看的任务（限定有元数据的）
    candidates = db.execute(
        select(Task).where(
            ~Task.id.in_(known),
            Task.video_code.isnot(None),
            (Task.actors.isnot(None) | Task.tags.isnot(None) | Task.rating.isnot(None)),
        )
    ).scalars().all()

    scored = []
    for t in candidates:
        t_actors = set(a.strip() for a in (t.actors or "").split(",") if a.strip())
        t_tags = set(a.strip() for a in (t.tags or "").split(",") if a.strip())
        score = 0.0
        score += sum(actor_pref[a] for a in t_actors) * 2.0
        score += sum(tag_pref[a] for a in t_tags) * 1.0
        if t.maker and maker_pref.get(t.maker):
            score += maker_pref[t.maker] * 1.5
        if t.rating:
            score += (t.rating - 5) * 0.5
        scored.append((score, t))

    scored.sort(key=lambda x: x[0], reverse=True)
    items = []
    for score, t in scored[:limit]:
        t_actors = set(a.strip() for a in (t.actors or "").split(",") if a.strip())
        t_tags = set(a.strip() for a in (t.tags or "").split(",") if a.strip())
        match = [a for a in t_actors if actor_pref[a]][:3] + [a for a in t_tags if tag_pref[a]][:3]
        items.append({
            "task_id": t.id, "video_code": t.video_code, "title": t.title,
            "rating": t.rating, "poster_url": t.poster_url, "score": round(score, 1),
            "match": match[:4],
        })
    result = {"items": items, "reason": "pref"}
    _recommend_cache["ts"] = now
    _recommend_cache["data"] = result
    return result


@router.get("/yearly-report")
def yearly_report(db: DbSession, _user: CurrentUser, year: int | None = None):
    """F14: 年度回顾——入库/下载/收藏统计 + Top 演员/标签/厂商 + 月度分布。"""
    from collections import Counter
    from datetime import datetime
    from models import Download, Task, task_collections

    y = year or datetime.utcnow().year
    start = datetime(y, 1, 1)
    end = datetime(y + 1, 1, 1)

    # 年度入库
    year_tasks = db.execute(
        select(Task).where(Task.created_at >= start, Task.created_at < end)
    ).scalars().all()
    # 年度下载完成
    dl_count = db.execute(
        select(Download.id).where(
            Download.completed_at >= start, Download.completed_at < end,
            Download.status == "completed",
        )
    ).all()
    # 年度收藏
    fav_count = db.execute(
        select(task_collections.c.task_id).where(
            task_collections.c.created_at >= start, task_collections.c.created_at < end
        )
    ).all()

    actor_c: Counter = Counter()
    tag_c: Counter = Counter()
    maker_c: Counter = Counter()
    monthly = [0] * 12
    for t in year_tasks:
        if t.created_at:
            monthly[t.created_at.month - 1] += 1
        for a in (t.actors or "").split(","):
            if a.strip():
                actor_c[a.strip()] += 1
        for g in (t.tags or "").split(","):
            if g.strip():
                tag_c[g.strip()] += 1
        if t.maker:
            maker_c[t.maker] += 1

    return {
        "year": y,
        "stats": {
            "added": len(year_tasks),
            "downloads": len(dl_count),
            "favorites": len(fav_count),
        },
        "top_actors": [{"name": k, "count": v} for k, v in actor_c.most_common(5)],
        "top_tags": [{"name": k, "count": v} for k, v in tag_c.most_common(5)],
        "top_makers": [{"name": k, "count": v} for k, v in maker_c.most_common(5)],
        "monthly": monthly,
    }


@router.get("/library-health")
def library_health(db: DbSession, _user: CurrentUser):
    """N1 库健康度：字段覆盖率加权评分 + 最该补的 Top20（高评分缺磁力）。"""
    from models import Download, Task

    total = db.execute(select(Task.id).where(Task.video_code.isnot(None))).all()
    total_n = len(total)
    if total_n == 0:
        return {"ok": True, "score": 100, "total": 0, "items": []}
    counts = {}
    for col, label in ((Task.best_magnet, "magnet"), (Task.title, "title"), (Task.maker, "maker"),
                       (Task.release_date, "date"), (Task.rating, "rating"), (Task.media_in_library, "in_library")):
        n = db.execute(select(Task.id).where(col.isnot(None))).all()
        counts[label] = len(n)
    org_n = db.execute(select(Download.id).where(Download.organized == True, Download.status == "completed")).all()  # noqa: E712
    dl_n = db.execute(select(Download.id).where(Download.status == "completed")).all()
    organized_rate = len(org_n) / len(dl_n) if dl_n else 1.0
    magnet_rate = counts["magnet"] / total_n
    meta_rate = (counts["title"] + counts["maker"] + counts["date"]) / (3 * total_n)
    rating_rate = counts["rating"] / total_n
    inlib_rate = counts["in_library"] / total_n
    score = round(
        100 * (0.30 * magnet_rate + 0.25 * meta_rate + 0.20 * rating_rate + 0.15 * inlib_rate + 0.10 * organized_rate),
        1,
    )
    # 最该补的：评分高但无磁力
    fix_rows = db.execute(
        select(Task).where(Task.rating.isnot(None), Task.best_magnet.is_(None))
        .order_by(Task.rating.desc()).limit(20)
    ).scalars().all()
    return {
        "ok": True, "score": score, "total": total_n,
        "rates": {"magnet": round(magnet_rate * 100, 1), "meta": round(meta_rate * 100, 1),
                  "rating": round(rating_rate * 100, 1), "in_library": round(inlib_rate * 100, 1),
                  "organized": round(organized_rate * 100, 1)},
        "fix_top": [{"task_id": t.id, "video_code": t.video_code, "title": t.title, "rating": t.rating} for t in fix_rows],
    }


@router.get("/profile")
def profile(db: DbSession, _user: CurrentUser):
    """N2 观看偏好画像：viewed/want 加权聚合口味 Top + 集中度。"""
    from collections import Counter
    from models import Task

    rows = db.execute(
        select(Task.actors, Task.tags, Task.maker, Task.rating, Task.view_status)
        .where(Task.view_status.in_(["viewed", "want"]))
    ).all()
    actor_c: Counter = Counter()
    tag_c: Counter = Counter()
    maker_c: Counter = Counter()
    ratings: list[float] = []
    weights = {"viewed": 2.0, "want": 1.5}
    for actors, tags, maker, rating, vs in rows:
        w = weights.get(vs, 1.0)
        for a in (actors or "").split(","):
            if a.strip():
                actor_c[a.strip()] += w
        for t in (tags or "").split(","):
            if t.strip():
                tag_c[t.strip()] += w
        if maker:
            maker_c[maker] += w
        if rating:
            ratings.append(float(rating))
    n = len(rows)
    avg = round(sum(ratings) / len(ratings), 2) if ratings else None
    top3 = sum(v for _, v in actor_c.most_common(3))
    hhi = round(sum((v / sum(actor_c.values())) ** 2 for v in actor_c.values()), 3) if actor_c else None
    return {
        "ok": True, "total": n, "avg_rating": avg, "hhi": hhi,
        "top_actors": [{"name": k, "score": round(v, 1)} for k, v in actor_c.most_common(8)],
        "top_tags": [{"name": k, "score": round(v, 1)} for k, v in tag_c.most_common(8)],
        "top_makers": [{"name": k, "score": round(v, 1)} for k, v in maker_c.most_common(8)],
    }


@router.get("/download-stats")
def download_stats(db: DbSession, _user: CurrentUser, days: int = Query(30, le=365)):
    """N3 下载成功率与失败归因（按下载器）。"""
    from collections import Counter, defaultdict
    from datetime import datetime, timedelta
    from models import Download

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(Download.downloader, Download.status, Download.error_message, Download.pushed_at, Download.completed_at)
        .where(Download.pushed_at >= since)
    ).all()
    per: dict[str, dict] = defaultdict(lambda: {"total": 0, "completed": 0, "failed": 0, "durations": [], "errors": Counter()})
    for dl, status, err, pushed, completed in rows:
        d = per[dl or "unknown"]
        d["total"] += 1
        if status == "completed":
            d["completed"] += 1
            if pushed and completed:
                d["durations"].append((completed - pushed).total_seconds() / 3600)
        elif status == "failed":
            d["failed"] += 1
            key = (err or "未知原因")[:40]
            d["errors"][key] += 1
    result = []
    for dl, d in per.items():
        rate = round(d["completed"] / d["total"] * 100, 1) if d["total"] else 0
        avg_h = round(sum(d["durations"]) / len(d["durations"]), 2) if d["durations"] else None
        result.append({
            "downloader": dl, "total": d["total"], "completed": d["completed"],
            "failed": d["failed"], "success_rate": rate, "avg_hours": avg_h,
            "top_errors": [{"msg": k, "count": v} for k, v in d["errors"].most_common(3)],
        })
    result.sort(key=lambda x: -x["total"])
    return {"ok": True, "days": days, "items": result}


@router.get("/crawl-efficiency")
def crawl_efficiency(db: DbSession, _user: CurrentUser, days: int = Query(14, le=90)):
    """N5 爬虫效率：按天×类型统计成功率 + 失败原因 Top。"""
    from collections import Counter, defaultdict
    from datetime import datetime, timedelta
    from models import CrawlLog

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(CrawlLog.crawl_type, CrawlLog.level, CrawlLog.message, CrawlLog.created_at)
        .where(CrawlLog.created_at >= since)
    ).all()
    per_day: dict[str, dict] = defaultdict(lambda: {"total": 0, "errors": 0})
    type_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "errors": 0})
    error_c: Counter = Counter()
    for ctype, level, msg, ts in rows:
        day = ts.strftime("%m-%d") if hasattr(ts, "strftime") else str(ts)[5:10]
        d = per_day[day]
        d["total"] += 1
        if level in ("error", "warn"):
            d["errors"] += 1
        t = type_stats[ctype or "unknown"]
        t["total"] += 1
        if level in ("error", "warn"):
            t["errors"] += 1
            key = (msg or "未知")[:40]
            error_c[key] += 1
    trend = [{"date": k, **v} for k, v in sorted(per_day.items())]
    totals = [{"type": k, "total": v["total"], "errors": v["errors"],
               "success_rate": round((v["total"] - v["errors"]) / v["total"] * 100, 1) if v["total"] else 100}
              for k, v in type_stats.items()]
    return {"ok": True, "days": days, "trend": trend, "totals": totals,
            "top_errors": [{"msg": k, "count": v} for k, v in error_c.most_common(5)]}


@router.get("/notification-health")
def notification_health(db: DbSession, _user: CurrentUser, days: int = Query(30, le=365)):
    """N6 通知健康度：按通道成功率 + 最近失败。"""
    from collections import defaultdict
    from datetime import datetime, timedelta
    from models import NotifyLog

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(NotifyLog.channel, NotifyLog.ok, NotifyLog.message, NotifyLog.created_at)
        .where(NotifyLog.created_at >= since)
    ).all()
    per: dict[str, dict] = defaultdict(lambda: {"total": 0, "ok": 0})
    recent_fail: list[dict] = []
    for ch, ok, msg, ts in rows:
        d = per[ch or "unknown"]
        d["total"] += 1
        if ok:
            d["ok"] += 1
        elif len(recent_fail) < 10:
            recent_fail.append({"channel": ch, "message": (msg or "")[:80], "time": str(ts)[:19]})
    items = []
    for ch, d in per.items():
        items.append({"channel": ch, "total": d["total"], "ok": d["ok"],
                      "fail_rate": round((d["total"] - d["ok"]) / d["total"] * 100, 1) if d["total"] else 0})
    items.sort(key=lambda x: -x["fail_rate"])
    return {"ok": True, "days": days, "items": items, "recent_failures": recent_fail}


@router.get("/ranking-trends")
def ranking_trends(db: DbSession, _user: CurrentUser, days: int = Query(14, le=90)):
    """N8 榜单趋势：在榜天数/最佳排名/上升最快 Top10。"""
    from collections import defaultdict
    from datetime import datetime, timedelta
    from models import Ranking

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(Ranking.video_code, Ranking.rank_date, Ranking.rank_position)
        .where(Ranking.rank_date >= since, Ranking.video_code.isnot(None))
    ).all()
    per: dict[str, dict] = defaultdict(lambda: {"days": 0, "best": 9999, "first": None, "last": None, "positions": []})
    for code, rd, pos in rows:
        d = per[code]
        d["days"] += 1
        d["best"] = min(d["best"], pos or 9999)
        d["first"] = min(d["first"] or rd, rd)
        d["last"] = max(d["last"] or rd, rd)
        if pos:
            d["positions"].append(pos)
    risers = []
    for code, d in per.items():
        first_p = d["positions"][0] if d["positions"] else None
        last_p = d["positions"][-1] if d["positions"] else None
        if first_p and last_p and first_p != last_p:
            risers.append({"code": code, "days": d["days"], "best": d["best"],
                           "from": first_p, "to": last_p, "change": first_p - last_p})
    risers.sort(key=lambda x: -x["change"])
    top_days = sorted(per.items(), key=lambda kv: -kv[1]["days"])[:10]
    return {
        "ok": True, "days": days,
        "top_risers": risers[:10],
        "top_on_chart": [{"code": k, "days": v["days"], "best": v["best"]} for k, v in top_days],
    }
