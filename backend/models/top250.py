"""Top250 独立模块——数据源为 jinjier.sqlite3（ranks 表直读，不转存）。

- 榜单数据：jinjier_ranks.db（数据包文件，动态更新替换）
- 手动导入的磁力：主库 top250_magnets 表（kind+number 唯一）
- 入库状态：实时联查主库 tasks.video_code（番号匹配），无映射表
"""
from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Top250Magnet(Base):
    """手动导入的磁力（按 kind+番号 与榜单数据 join）。"""
    __tablename__ = "top250_magnets"
    __table_args__ = (UniqueConstraint("kind", "number", name="uq_top250_magnet_kind_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    magnet: Mapped[str] = mapped_column(Text, nullable=False)
    magnet_version: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[str | None] = mapped_column(String(20))


class Top250Entry(Base):
    """[已弃用] 旧转存表——保留结构以兼容历史数据迁移（磁力已迁至 Top250Magnet）。"""
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
    updated_at: Mapped[str | None] = mapped_column(String(20))
    prev_rank: Mapped[int | None] = mapped_column(Integer)
    prev_date: Mapped[str | None] = mapped_column(String(20))
