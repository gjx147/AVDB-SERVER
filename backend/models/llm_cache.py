"""LLMCache（LLM 调用缓存）+ ContentFilterRule（内容过滤规则）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LLMCache(Base):
    """LLM 调用缓存：prompt_hash -> response，避免重复调用花钱。"""
    __tablename__ = "llm_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(20), nullable=False)  # translate/tags/summary/chat
    model: Mapped[str | None] = mapped_column(String(50))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())


class ContentFilterRule(Base):
    """内容过滤规则：hide/highlight/blur/mark 动作 + 正则/关键字匹配。"""
    __tablename__ = "content_filter_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    is_regex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    case_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="hide", server_default="hide")  # hide/highlight/blur/mark
    fields_json: Mapped[str | None] = mapped_column(Text)  # ["title","actor","studio","video-id"]
    message: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (Index("idx_filter_rules_enabled", "enabled"),)
