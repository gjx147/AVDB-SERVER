"""AgentAction（AI 写操作审计，工程底座）——所有写工具执行留痕，支持撤销。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String(50), nullable=False)
    args_json: Mapped[str | None] = mapped_column(Text)          # 参数快照（撤销/恢复用）
    operator: Mapped[str] = mapped_column(String(100), nullable=False, default="ai")
    result: Mapped[str | None] = mapped_column(String(500))      # 执行结果摘要
    ok: Mapped[bool] = mapped_column(nullable=False, default=True)  # 是否执行成功
    undone: Mapped[bool] = mapped_column(nullable=False, default=False)  # 是否已撤销
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_agent_actions_created", "created_at"),
        Index("idx_agent_actions_tool", "tool"),
    )

    def __repr__(self) -> str:
        return f"<AgentAction id={self.id} tool={self.tool!r}>"
