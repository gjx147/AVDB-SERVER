"""CrawlLog（爬取日志）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_source_id: Mapped[int | None] = mapped_column(ForeignKey("list_sources.id", ondelete="SET NULL"))
    crawl_type: Mapped[str | None] = mapped_column(String(20))  # scan/extract/ranking/...
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="info", server_default="info")  # info/warn/error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        Index("idx_crawl_logs_source", "list_source_id"),
        Index("idx_crawl_logs_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<CrawlLog id={self.id} level={self.level!r}>"
