"""AI 报告服务（P1 批）：订阅周报 S4 / 演员动态解读 S5 / 库健康建议 A3 / 每日推荐 A7。

设计：
- 全部走 LLMCache（按内容哈希/按对象），高频任务防爆量
- 未配置 AI 时降级：返回规则生成的静态文案（不阻塞功能）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

logger = logging.getLogger("avdb.ai_reports")


async def _summarize_with_ai(prompt: str, cache_key: str, task_type: str) -> str | None:
    """LLM 生成文案（带缓存）。失败返回 None。"""
    from services.ai_service import _get_cached, _hash_prompt, _save_cache, chat
    key = _hash_prompt(cache_key)
    cached = _get_cached(key)
    if cached:
        return cached
    try:
        raw = await chat(
            [{"role": "system", "content": "你是 AVDB 影片库智能分析助手，用简洁自然的中文输出，不要 markdown 标题。"},
             {"role": "user", "content": prompt}],
            task_type=task_type,
        )
        text = str(raw or "").strip()
        if text:
            _save_cache(key, task_type, "", cache_key, text)
        return text or None
    except Exception as e:
        logger.warning("AI 生成失败(%s): %s", task_type, e)
        return None


# ---------- A3 库健康 AI 建议 ----------
async def library_health_advice() -> dict:
    """健康分 + 指标 + 3 条行动建议（AI 优先，降级规则文案）。"""
    from routers.insights import library_health
    from database import SessionLocal
    db = SessionLocal()
    try:
        data = library_health(db, "anonymous")
    finally:
        db.close()
    if not data.get("ok"):
        return {"ok": False, "message": "健康数据不可用"}

    score = data.get("score", 0)
    rates = data.get("rates", {})
    total = data.get("total", 0)
    fix_top = data.get("fix_top", [])[:5]

    prompt = (
        f"影片库健康分 {score}/100，共 {total} 部。指标：磁力完整率 {rates.get('magnet', 0)}%，"
        f"元数据完整率 {rates.get('meta', 0)}%，评分覆盖率 {rates.get('rating', 0)}%，"
        f"在库率 {rates.get('in_library', 0)}%，整理率 {rates.get('organized', 0)}%。\n"
        f"最该补磁力但评分高的作品：{', '.join(t['video_code'] for t in fix_top[:3]) or '无'}。\n"
        "请给出 3 条具体、可执行的改进建议（每条不超过 40 字，按优先级排序，用 1. 2. 3. 开头）。"
    )
    ai = await _summarize_with_ai(prompt, f"health_advice:{score}:{json.dumps(rates, ensure_ascii=False)}", "health")
    if ai:
        return {"ok": True, "score": score, "rates": rates, "advice": ai, "engine": "ai"}

    # 降级：规则建议
    tips = []
    if rates.get("magnet", 100) < 80:
        tips.append(f"1. 磁力完整率偏低（{rates.get('magnet')}%），建议补抓高分作品磁力")
    if rates.get("meta", 100) < 80:
        tips.append(f"2. 元数据完整率偏低（{rates.get('meta')}%），可批量补全标题/厂商/日期")
    if rates.get("rating", 100) < 60:
        tips.append(f"3. 评分覆盖率偏低（{rates.get('rating')}%），建议先为高热度作品补评分")
    if not tips:
        tips.append("1. 库健康良好，继续维护即可")
        tips.append("2. 可关注最新榜单新作入库")
        tips.append("3. 定期清理失败任务与重复项")
    return {"ok": True, "score": score, "rates": rates, "advice": "\n".join(tips[:3]), "engine": "rules"}


# ---------- S5 演员动态 AI 解读 ----------
async def actor_dynamics(actor_id: int) -> dict:
    """演员活跃/休止/趋势解读（按演员缓存）。"""
    from models import Actor, Task, actor_movies
    from database import SessionLocal
    db = SessionLocal()
    try:
        actor = db.get(Actor, actor_id)
        if not actor:
            return {"ok": False, "message": "演员不存在"}
        rows = db.execute(
            select(Task.release_date, Task.rating)
            .join(actor_movies, actor_movies.c.task_id == Task.id)
            .where(actor_movies.c.actor_id == actor_id, Task.release_date.isnot(None))
            .order_by(Task.release_date.desc())
        ).all()
        dates = [r[0] for r in rows if r[0]]
        ratings = [float(r[1]) for r in rows if r[1] is not None]
        total_works = db.execute(
            select(func.count()).select_from(actor_movies).where(actor_movies.c.actor_id == actor_id)
        ).scalar() or 0
    finally:
        db.close()

    if not dates:
        return {"ok": True, "actor_id": actor_id, "name": actor.name, "total_works": total_works,
                "summary": f"{actor.name} 暂无明确发行日期数据，难以判断动态。", "engine": "rules"}

    latest = max(dates)
    months_since = (datetime.utcnow() - datetime.strptime(latest, "%Y-%m-%d")).days / 30
    recent_1y = sum(1 for d in dates if d >= (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d"))
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None

    prompt = (
        f"演员 {actor.name}：共 {total_works} 部作品，最近发行 {latest}（距今约 {months_since:.1f} 个月），"
        f"近一年 {recent_1y} 部，平均评分 {avg_rating if avg_rating else '未知'}。\n"
        "用一句话解读她的动态状态（活跃期/休止期/稳定产出/下滑），50 字以内。"
    )
    ai = await _summarize_with_ai(prompt, f"actor_dyn:{actor_id}:{latest}:{recent_1y}", "actor_dyn")
    if ai:
        return {"ok": True, "actor_id": actor_id, "name": actor.name, "total_works": total_works,
                "latest_release": latest, "recent_1y": recent_1y, "avg_rating": avg_rating,
                "summary": ai, "engine": "ai"}
    if months_since > 12:
        s = f"{actor.name} 已约 {months_since:.0f} 个月无新作，处于休止期（最近 {latest}）。"
    elif months_since > 3:
        s = f"{actor.name} 最近作品距今约 {months_since:.0f} 个月，产出放缓（最近 {latest}）。"
    else:
        s = f"{actor.name} 近一年 {recent_1y} 部作品，仍在活跃产出（最近 {latest}）。"
    return {"ok": True, "actor_id": actor_id, "name": actor.name, "total_works": total_works,
            "latest_release": latest, "recent_1y": recent_1y, "avg_rating": avg_rating,
            "summary": s, "engine": "rules"}


# ---------- S4 AI 订阅周报 ----------
async def subscription_weekly_job() -> dict:
    """每周订阅动态周报：聚合各订阅最近结果 → AI 摘要 → 推送。"""
    from models import Subscription
    from database import SessionLocal
    db = SessionLocal()
    try:
        subs = db.execute(select(Subscription).where(Subscription.enabled == True)).scalars().all()  # noqa: E712
        if not subs:
            return {"ok": True, "message": "无订阅，跳过周报"}
        lines = []
        for s in subs[:10]:
            res = ""
            if s.last_result:
                try:
                    r = json.loads(s.last_result)
                    res = f"命中 {r.get('matched', '?')} 部"
                except Exception:
                    res = "已检查"
            lines.append(f"- {s.name}（{s.sub_type}）：{res}")
        digest = "\n".join(lines)
        last_checked = max((s.last_checked_at for s in subs if s.last_checked_at), default=None)
    finally:
        db.close()

    if last_checked is None:
        return {"ok": True, "message": "订阅尚未巡检过，跳过周报"}

    prompt = (
        "以下是本周订阅动态：\n" + digest + "\n"
        "用 3-4 句话总结订阅整体状态与值得关注的点（60-100 字）。"
    )
    ai = await _summarize_with_ai(prompt, f"sub_weekly:{digest[:300]}", "sub_weekly")
    body = f"本周订阅状态：\n{digest}\n" + (f"\n📝 AI 解读：{ai}" if ai else "")

    from services.notifier import notify
    await notify("subscription_weekly", "AI 订阅周报", body)
    return {"ok": True, "message": "订阅周报已推送", "ai": bool(ai)}


# ---------- A7 每日 AI 推荐推送 ----------
async def daily_recommend_job() -> dict:
    """每天 9 点推送 3 部推荐 + AI 理由。"""
    from routers.insights import recommendations
    from database import SessionLocal
    db = SessionLocal()
    try:
        data = recommendations(db, "anonymous", limit=3)
        items = data.get("items") or []
        if not items:
            return {"ok": True, "message": "暂无推荐内容"}
        lines = []
        for it in items[:3]:
            vc = it.get("video_code") or it.get("task_id")
            tags = (it.get("tags") or "") if isinstance(it.get("tags"), str) else ""
            tag_hint = tags.split(",")[0] if tags else ""
            reason = f"评分 {it.get('rating') or '-'}"
            if tag_hint:
                reason += f"、{tag_hint}向"
            lines.append(f"- {vc} {it.get('title') or ''}（{reason}）")
    finally:
        db.close()

    body = "今日 AI 推荐：\n" + "\n".join(lines)
    from services.notifier import notify
    await notify("daily_recommend", "每日 AI 推荐", body)
    return {"ok": True, "message": "每日推荐已推送", "count": len(lines)}
