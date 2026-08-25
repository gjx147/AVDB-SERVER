"""add downloads organized columns (F7 自动整理)

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2026-08-25 08:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('downloads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organized', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('organized_path', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('downloads', schema=None) as batch_op:
        batch_op.drop_column('organized_path')
        batch_op.drop_column('organized')
