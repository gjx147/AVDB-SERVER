"""add actor works_fetched marker

Revision ID: f7a2b9c3d418
Revises: c2b4d6e8f917
Create Date: 2026-08-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a2b9c3d418'
down_revision: Union[str, None] = 'c2b4d6e8f917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('works_fetched', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.drop_column('works_fetched')
