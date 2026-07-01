"""Ranking（排行榜）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Ranking(Base):
    __tablename__ = "rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rank_type: Mapped[str] = mapped_column(String(20), nullable=False)  # hot/weekly/monthly/daily
    rank_date: Mapped[str] = mapped_column(String(20), nullable=False)  # 排行榜日期
    rank_position: Mapped[int] = mapped_column(Integer, nullable=False)

    video_code: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(500))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    score: Mapped[float | None] = mapped_column(nullable=True)
    views: Mapped[int | None] = mapped_column(Integer)
    detail_url: Mapped[str | None] = mapped_column(String(500))

    # 与 tasks 表关联（入库后回填）
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    is_in_library: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_rankings_type_date", "rank_type", "rank_date"),
        Index("idx_rankings_video_code", "video_code"),
    )

    def __repr__(self) -> str:
        return f"<Ranking id={self.id} type={self.rank_type!r} pos={self.rank_position}>"
