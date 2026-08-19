"""add actor photos

Revision ID: a91b2c4d8e77
Revises: c4a1f9e02b8d
Create Date: 2026-08-19 18:00:00.000000

（回退兼容：xslist 功能已移除，但 NAS 数据库已应用过本迁移——保留文件
让 alembic 能识别数据库版本。photos 列留在库中无害。）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a91b2c4d8e77'
down_revision: Union[str, None] = 'c4a1f9e02b8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('photos', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.drop_column('photos')
