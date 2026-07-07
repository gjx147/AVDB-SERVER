"""Collection（收藏分组）模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Table, Column, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# 分组-任务多对多关联
task_collections = Table(
    "task_collections",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("collection_id", Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()),
    Index("idx_task_collections_collection", "collection_id"),
    Index("idx_task_collections_task", "task_id"),
)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())

    tasks = relationship("Task", secondary=task_collections, backref="collections")

    def __repr__(self) -> str:
        return f"<Collection id={self.id} name={self.name!r}>"
