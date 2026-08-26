"""数据清理任务：LLMCache / ai_usage / chat_messages 保留窗口。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("avdb.cleanup")


def cleanup_job() -> dict:
    """保留窗口：LLMCache 30 天 / ai_usage 90 天 / chat_messages 90 天 / agent_actions 180 天。"""
    from sqlalchemy import delete
    from database import SessionLocal
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        stats = {}
        for model, days, key in (
            ("LLMCache", 30, "created_at"),
            ("AiUsage", 90, "created_at"),
            ("ChatMessage", 90, "created_at"),
            ("AgentAction", 180, "created_at"),
        ):
            try:
                from models import LLMCache, AiUsage, ChatMessage, AgentAction
                cls = {"LLMCache": LLMCache, "AiUsage": AiUsage,
                       "ChatMessage": ChatMessage, "AgentAction": AgentAction}[model]
                cutoff = now - timedelta(days=days)
                r = db.execute(delete(cls).where(getattr(cls, key) < cutoff))
                db.commit()
                stats[model] = r.rowcount
            except Exception as e:
                logger.warning("清理 %s 失败: %s", model, e)
        return {"ok": True, "cleaned": stats}
    finally:
        db.close()
