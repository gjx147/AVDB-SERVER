"""ListSource（列表源）模型 —— 爬取源配置。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ListSource(Base):
    __tablename__ = "list_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)  # 如 SDMM
    list_path: Mapped[str] = mapped_column(String(200), nullable=False)  # 如 /video_codes/SDMM
    list_params: Mapped[str] = mapped_column(String(100), nullable=False, default="f=download")
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    last_scanned_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    tasks = relationship("Task", back_populates="list_source")

    def __repr__(self) -> str:
        return f"<ListSource id={self.id} code={self.list_code!r}>"
