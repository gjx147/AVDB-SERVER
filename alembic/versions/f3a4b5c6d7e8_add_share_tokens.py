"""add share_tokens table (N21 公开分享)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-25 12:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'share_tokens',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('token', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('ref_id', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
    )
    op.create_index('ix_share_tokens_token', 'share_tokens', ['token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_share_tokens_token', table_name='share_tokens')
    op.drop_table('share_tokens')
