"""NewRelease（检测到的新作品）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class NewRelease(Base):
    __tablename__ = "new_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id", ondelete="CASCADE"))
    video_code: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    detail_url: Mapped[str | None] = mapped_column(String(500))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    release_date: Mapped[str | None] = mapped_column(String(20))

    # 是否已处理（入库或忽略）
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # 是否已入库为 task
    added_to_library: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))

    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        Index("idx_new_releases_actor", "actor_id"),
        Index("idx_new_releases_code", "video_code"),
        Index("idx_new_releases_unread", "is_read"),
    )

    def __repr__(self) -> str:
        return f"<NewRelease code={self.video_code!r} actor={self.actor_id}>"
