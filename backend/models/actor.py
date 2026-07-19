"""Actor（演员）与 ActorMovie（演员-作品关联）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# 演员-作品多对多关联表
actor_movies = Table(
    "actor_movies",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_id", Integer, ForeignKey("actors.id", ondelete="CASCADE"), nullable=False),
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()),
    Index("idx_actor_movies_actor", "actor_id"),
    Index("idx_actor_movies_task", "task_id"),
)


class Actor(Base):
    __tablename__ = "actors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # index via idx_actors_name
    name_en: Mapped[str | None] = mapped_column(String(100))

    # 头像（远程 URL 与本地缓存路径）
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    avatar_local: Mapped[str | None] = mapped_column(String(500))

    gender: Mapped[str | None] = mapped_column(String(10))  # female/trans/...
    birth_date: Mapped[str | None] = mapped_column(String(20))
    height: Mapped[str | None] = mapped_column(String(20))
    cup: Mapped[str | None] = mapped_column(String(10))
    measurements: Mapped[str | None] = mapped_column(String(50))
    debut_date: Mapped[str | None] = mapped_column(String(20))
    movie_count: Mapped[int | None] = mapped_column(Integer)

    # 状态标记（JavdBviewed 移植）
    is_followed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")  # 收藏
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")  # 拉黑

    source_url: Mapped[str | None] = mapped_column(String(500))  # JavDB 演员页 URL（用于一键补齐作品）
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    tasks = relationship("Task", secondary=actor_movies, backref="actor_refs")

    __table_args__ = (
        Index("idx_actors_name", "name"),
        Index("idx_actors_followed", "is_followed"),
        Index("idx_actors_blacklisted", "is_blacklisted"),
    )

    def __repr__(self) -> str:
        return f"<Actor id={self.id} name={self.name!r}>"


# 便于直接 from models import ActorMovie
ActorMovie = actor_movies
