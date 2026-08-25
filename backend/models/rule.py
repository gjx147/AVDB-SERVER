"""自动化规则（N22）：IF-THEN 规则引擎。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func

from database import Base


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    # 条件 JSON: {"actor": [], "tag": [], "maker": [], "rating_min": 8, "is_new": true}
    conditions_json = Column(Text, default="{}")
    # 动作 JSON: {"actions": ["notify", "favorite", "push"], "note": "文案"}
    actions_json = Column(Text, default="{}")
    enabled = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    hit_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
