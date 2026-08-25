"""add notify_logs table (F2 通知历史中心)

Revision ID: c8d9e0f1a2b3
Revises: f7a2b9c3d418
Create Date: 2026-08-25 08:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'f7a2b9c3d418'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notify_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('channel', sa.String(length=30), nullable=False, server_default=''),
        sa.Column('ok', sa.Boolean(), nullable=True),
        sa.Column('message', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
    )
    op.create_index('idx_notify_logs_created', 'notify_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_notify_logs_created', table_name='notify_logs')
    op.drop_table('notify_logs')
