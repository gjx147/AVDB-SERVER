"""add rating_history table (N15 评分快照)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-25 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rating_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('rating', sa.Float(), nullable=False),
        sa.Column('snapshot_date', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.UniqueConstraint('task_id', 'snapshot_date', name='uq_rating_task_date'),
    )
    op.create_index('idx_rating_history_date', 'rating_history', ['snapshot_date'])


def downgrade() -> None:
    op.drop_constraint('uq_rating_task_date', 'rating_history', type_='unique')
    op.drop_index('idx_rating_history_date', table_name='rating_history')
    op.drop_table('rating_history')
