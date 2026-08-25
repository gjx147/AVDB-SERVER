"""公开分享令牌（N21）：收藏夹/榜单只读分享链接。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from database import Base


class ShareToken(Base):
    __tablename__ = "share_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(32), unique=True, nullable=False, index=True)
    kind = Column(String(20), nullable=False)  # collection / actor / ranking
    ref_id = Column(Integer, nullable=False)
    note = Column(String(200), default="")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
