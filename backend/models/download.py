"""Download（下载记录）模型 —— 磁力推送 + 进度追踪。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Download(Base):
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    video_code: Mapped[str | None] = mapped_column(String(50))
    magnet: Mapped[str] = mapped_column(Text, nullable=False)
    info_hash: Mapped[str | None] = mapped_column(String(40), index=True)

    # 下载器：qbittorrent / clouddrive2 / aria2 / transmission
    downloader: Mapped[str] = mapped_column(String(20), nullable=False)

    # 状态：pushed / downloading / completed / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pushed", server_default="pushed")
    progress: Mapped[float | None] = mapped_column(Float)  # 0-100
    error_message: Mapped[str | None] = mapped_column(Text)

    pushed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_downloads_status", "status"),
        Index("idx_downloads_task", "task_id"),
        Index("idx_downloads_downloader", "downloader"),
    )

    def __repr__(self) -> str:
        return f"<Download id={self.id} downloader={self.downloader!r} status={self.status!r}>"
