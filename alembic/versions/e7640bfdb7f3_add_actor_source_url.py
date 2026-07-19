"""add actor source_url

Revision ID: e7640bfdb7f3
Revises: ed5cf9e9b057
Create Date: 2026-07-19 06:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7640bfdb7f3'
down_revision: Union[str, None] = 'ed5cf9e9b057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_url', sa.String(length=500), nullable=True))
    # 从 note 字段回填已有数据（note 格式 "source_url: https://..."）
    op.execute("UPDATE actors SET source_url = substr(note, 13) WHERE note LIKE 'source_url: %'")


def downgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.drop_column('source_url')
