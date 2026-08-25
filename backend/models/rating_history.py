"""评分历史快照（N15）：每日记录任务评分，供趋势分析。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func

from database import Base


class RatingHistory(Base):
    __tablename__ = "rating_history"
    __table_args__ = (
        UniqueConstraint("task_id", "snapshot_date", name="uq_rating_task_date"),
        Index("idx_rating_history_date", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    rating = Column(Float, nullable=False)
    snapshot_date = Column(String(10), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
