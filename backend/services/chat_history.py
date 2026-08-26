"""聊天会话历史服务：多会话管理 + 消息存档。"""
from __future__ import annotations

import logging

from sqlalchemy import select

logger = logging.getLogger("avdb.chat_history")


def _user_key(user) -> str:
    return getattr(user, "username", None) or (user if isinstance(user, str) else None) or "default"


def create_session(db, user, title: str = "新对话") -> dict:
    from models import ChatSession
    s = ChatSession(user=_user_key(user), title=(title or "新对话")[:200])
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "title": s.title}


def list_sessions(db, user, limit: int = 50) -> list[dict]:
    from models import ChatSession
    rows = db.execute(
        select(ChatSession).where(ChatSession.user == _user_key(user))
        .order_by(ChatSession.updated_at.desc()).limit(min(limit, 100))
    ).scalars().all()
    return [{"id": s.id, "title": s.title,
             "created_at": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else None} for s in rows]


def delete_session(db, user, session_id: int) -> bool:
    from models import ChatSession
    s = db.get(ChatSession, session_id)
    if not s or s.user != _user_key(user):
        return False
    db.delete(s)
    db.commit()
    return True


def rename_session(db, user, session_id: int, title: str) -> bool:
    from models import ChatSession
    s = db.get(ChatSession, session_id)
    if not s or s.user != _user_key(user):
        return False
    s.title = (title or "新对话")[:200]
    db.commit()
    return True


def session_messages(db, user, session_id: int, limit: int = 100) -> list[dict] | None:
    from models import ChatSession, ChatMessage
    s = db.get(ChatSession, session_id)
    if not s or s.user != _user_key(user):
        return None
    rows = db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc()).limit(min(limit, 200))
    ).scalars().all()
    msgs = [{"role": m.role, "content": m.content[:2000]} for m in reversed(rows)]
    return msgs


def save_messages(db, user, session_id: int, messages: list[dict]) -> None:
    """把对话消息存档（user/assistant 轮次）。"""
    from models import ChatSession, ChatMessage
    s = db.get(ChatSession, session_id)
    if not s or s.user != _user_key(user):
        return
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")[:2000]
        if role in ("user", "assistant") and content:
            db.add(ChatMessage(session_id=session_id, role=role, content=content))
    db.commit()


def auto_title(db, session_id: int, first_user_msg: str) -> None:
    """用第一条用户消息生成会话标题。"""
    from models import ChatSession
    s = db.get(ChatSession, session_id)
    if s and s.title in ("新对话", "New chat"):
        s.title = (first_user_msg or "新对话")[:20]
        db.commit()
