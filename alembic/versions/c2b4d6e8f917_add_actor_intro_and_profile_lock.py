"""add actor intro and profile lock

Revision ID: c2b4d6e8f917
Revises: e9f1a3c5d724
Create Date: 2026-08-20 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2b4d6e8f917'
down_revision: Union[str, None] = 'e9f1a3c5d724'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('intro', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('profile_locked', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.drop_column('intro')
        batch_op.drop_column('profile_locked')
