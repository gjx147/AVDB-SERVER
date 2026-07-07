"""InsightReport（月度洞察报告）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class InsightReport(Base):
    __tablename__ = "insight_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    # 聚合统计 JSON（top_actors/top_tags/top_makers/rating_dist/metrics）
    stats_json: Mapped[str] = mapped_column(Text, nullable=False)
    # AI 生成的文案摘要（可选）
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (Index("idx_insight_reports_month", "month"),)

    def __repr__(self) -> str:
        return f"<InsightReport month={self.month!r}>"
