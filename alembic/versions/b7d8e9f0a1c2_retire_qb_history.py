"""retire qbittorrent download history

Revision ID: b7d8e9f0a1c2
Revises: ab7c9d1e2f3a
Create Date: 2026-09-07 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b7d8e9f0a1c2'
down_revision: Union[str, None] = 'ab7c9d1e2f3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # qB 通道已退役：遗留未完成记录一次性置失败（历史 completed/failed 保留展示）
    op.execute(
        "UPDATE downloads SET status='failed', error_message='qB 通道已退役' "
        "WHERE downloader='qbittorrent' AND status IN ('pushed','downloading')"
    )
    # S3：默认下载器旧值归一（策略 JSON 内的旧值由代码层归一兜底）
    op.execute(
        "UPDATE settings SET value='xunlei' WHERE key='default_downloader' AND value='qbittorrent'"
    )


def downgrade() -> None:
    pass