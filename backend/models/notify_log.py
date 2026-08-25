"""通知发送历史（F2）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, func

from database import Base


class NotifyLog(Base):
    __tablename__ = "notify_logs"
    __table_args__ = (Index("idx_notify_logs_created", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    event = Column(String(50), nullable=False, default="")
    title = Column(String(200), default="")
    body = Column(Text, default="")
    channel = Column(String(30), nullable=False, default="")
    ok = Column(Boolean, default=False)
    message = Column(String(500), default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
