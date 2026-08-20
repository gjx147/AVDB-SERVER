"""add actor minnano extended profile columns

Revision ID: e9f1a3c5d724
Revises: d5e8f4c2b913
Create Date: 2026-08-20 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9f1a3c5d724'
down_revision: Union[str, None] = 'd5e8f4c2b913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agency', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('hobbies', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('debut_work', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('twitter', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('website', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('tags', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.drop_column('agency')
        batch_op.drop_column('hobbies')
        batch_op.drop_column('debut_work')
        batch_op.drop_column('twitter')
        batch_op.drop_column('website')
        batch_op.drop_column('tags')
