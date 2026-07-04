"""Subscription（多维订阅）模型 —— Immortal 式设计。

订阅类型：
- ranking: 榜单订阅（按 rank_type + 日期偏移同步）
- actor: 演员订阅（关联 actor_id，检测新作）
- composite: 综合订阅（按 maker/label/series/genre 等过滤条件）

过滤器（JSON）：
{
  "makers": ["厂牌A"],           # 制作商白名单
  "labels": ["厂牌B"],            # 发行商白名单
  "series": ["系列X"],            # 系列白名单
  "genres": ["标签1"],            # 类型白名单
  "exclude_codes": ["VR-"],       # 番号前缀黑名单
  "min_rating": 7.0,              # 最低评分
  "date_from": "2026-01-01"       # 起始日期
}
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 订阅名称
    sub_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ranking/actor/composite

    # 榜单订阅：rank_type（hot/weekly/monthly/daily）
    rank_type: Mapped[str | None] = mapped_column(String(20))
    # 演员订阅：关联演员
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id", ondelete="CASCADE"))
    # 综合订阅/通用：过滤条件 JSON
    filters_json: Mapped[str | None] = mapped_column(Text)

    # 入库配置
    auto_add: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    # 入库到哪个 list_source（空则用 RANKING 默认源）
    target_list_source_id: Mapped[int | None] = mapped_column(ForeignKey("list_sources.id", ondelete="SET NULL"))

    # 调度
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    check_interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_result: Mapped[str | None] = mapped_column(Text)  # 上次检查结果摘要 JSON

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_subscriptions_type", "sub_type"),
        Index("idx_subscriptions_enabled", "enabled"),
        Index("idx_subscriptions_actor", "actor_id"),
    )

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} name={self.name!r} type={self.sub_type!r}>"
