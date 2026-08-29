"""Top250 独立模块——JavDB TOP250 榜单条目（来源：jinjier.art 数据包 / 手动导入）。"""
from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Top250Entry(Base):
    """TOP250 条目。kind：类型榜（6 总榜/7 有码/8 无码/9 欧美/10 FC2）与年份榜（2008~2025）。"""
    __tablename__ = "top250_entries"
    __table_args__ = (UniqueConstraint("kind", "number", name="uq_top250_kind_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(500), default="")
    date: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(200))
    icon_url: Mapped[str | None] = mapped_column(String(500))
    magnet: Mapped[str | None] = mapped_column(Text)
    magnet_version: Mapped[str | None] = mapped_column(String(20))
    task_id: Mapped[int | None] = mapped_column(Integer)
