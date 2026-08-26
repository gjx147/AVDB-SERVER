"""ConfigAudit（配置修改审计，G4）——每次 AI/管理员改配置留痕，支持回滚。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ConfigAudit(Base):
    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(100), nullable=False, default="ai")  # ai / admin / rollback
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")  # agent / settings / rollback
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_config_audit_key", "key"),
        Index("idx_config_audit_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ConfigAudit id={self.id} key={self.key!r}>"
