"""Task（影片任务）模型 —— 核心表。

字段在 strivek 基础 11 字段之上，融入 AVDB 的富元数据扩展，
并新增来自 JavdBviewed 的观看状态字段与 Immortal 的媒体库在库缓存。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- 基础字段（strivek） ---
    list_source_id: Mapped[int] = mapped_column(Integer, ForeignKey("list_sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/visited/failed
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- 磁力（strivek + AVDB 结构升级：magnets_json 带 name） ---
    best_magnet: Mapped[str | None] = mapped_column(Text)
    magnets_json: Mapped[str | None] = mapped_column(Text)  # [{magnet, name}] 按优先级排序
    video_code: Mapped[str | None] = mapped_column(String(50))  # 番号

    error_message: Mapped[str | None] = mapped_column(Text)

    # --- 富媒体元数据（AVDB 扩展） ---
    title: Mapped[str | None] = mapped_column(String(500))
    poster_url: Mapped[str | None] = mapped_column(String(500))  # 本地路径或远程 URL
    thumbnail_urls: Mapped[str | None] = mapped_column(Text)  # JSON 数组
    synopsis: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    # --- 分类标签（AVDB 扩展） ---
    actors: Mapped[str | None] = mapped_column(Text)  # 逗号分隔
    tags: Mapped[str | None] = mapped_column(Text)  # 逗号分隔

    # --- 元数据面板（AVDB 扩展） ---
    release_date: Mapped[str | None] = mapped_column(String(20))
    duration: Mapped[str | None] = mapped_column(String(20))
    director: Mapped[str | None] = mapped_column(String(100))
    maker: Mapped[str | None] = mapped_column(String(100))
    label: Mapped[str | None] = mapped_column(String(100))
    series: Mapped[str | None] = mapped_column(String(100))
    rating: Mapped[float | None] = mapped_column(Float)
    file_size: Mapped[str | None] = mapped_column(String(20))

    # --- 收藏 / 笔记（AVDB 扩展） ---
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    favorite_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)

    # --- 观看状态（JavdBviewed 移植） ---
    # viewed=已观看 / browsed=已浏览 / want=想看 / NULL=未标记
    view_status: Mapped[str | None] = mapped_column(String(20))
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # --- AI 缓存（JavdBviewed 移植） ---
    ai_title_translated: Mapped[str | None] = mapped_column(String(500))
    ai_tags: Mapped[str | None] = mapped_column(Text)

    # --- 媒体库在库缓存（Immortal 参考） ---
    media_in_library: Mapped[bool | None] = mapped_column(Boolean)

    # --- 下载状态（AVDB patch 层并入） ---
    download_status: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # --- 关系 ---
    list_source = relationship("ListSource", back_populates="tasks")

    __table_args__ = (
        Index("idx_tasks_list_status", "list_source_id", "status"),
        Index("idx_tasks_video_code", "video_code"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_favorite", "is_favorite", "favorite_at"),
        Index("idx_tasks_view_status", "view_status"),
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} code={self.video_code!r} status={self.status!r}>"
