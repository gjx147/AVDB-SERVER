"""每周新作榜单推送（F4）：最近 7 天发布的作品按评分取 Top10，notify 推送。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

logger = logging.getLogger("avdb.weekly_report")


async def run_weekly_report() -> dict:
    """生成并推送本周新作 Top10（事件 weekly_report，需在通知设置中启用）。"""
    from database import SessionLocal
    from models import Task

    db = SessionLocal()
    try:
        since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        rows = db.execute(
            select(Task)
            .where(Task.release_date.isnot(None), Task.release_date >= since)
            .order_by(Task.rating.desc().nullslast())
            .limit(10)
        ).scalars().all()
        if not rows:
            logger.info("每周榜单：本周无新作")
            return {"ok": True, "pushed": 0, "reason": "本周无新作"}
        lines = []
        for i, t in enumerate(rows, 1):
            title = (t.title or "").strip() or t.video_code or ""
            rating = f" {t.rating}" if t.rating else ""
            lines.append(f"{i}. {t.video_code} [{rating}] {title}")
        body = "\n".join(lines)
        # A4: AI 周报解读（趋势/亮点，缓存周级）
        try:
            from services.ai_p2 import _ai_text
            ai = await _ai_text(
                f"本周新作 Top：\n{body[:400]}\n用 2 句话总结本周看点（趋势/亮点/评分水平）。",
                f"weekly_ai:{body[:200]}", "weekly_ai")
            if ai:
                body += f"\n\n📝 AI 解读：{ai}"
        except Exception:
            pass
        from services.notifier import notify
        await notify("weekly_report", f"本周新作 Top{len(rows)}", body)
        logger.info("每周榜单已推送 %d 部", len(rows))
        return {"ok": True, "pushed": len(rows)}
    finally:
        db.close()
