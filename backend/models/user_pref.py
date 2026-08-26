"""UserPref（用户口味偏好记忆）——检索默认值自动套用。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class UserPref(Base):
    __tablename__ = "user_prefs"

    user: Mapped[str] = mapped_column(String(100), primary_key=True)
    prefs_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<UserPref user={self.user!r}>"
