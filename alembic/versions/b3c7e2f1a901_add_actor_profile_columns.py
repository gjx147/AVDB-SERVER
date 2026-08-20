"""add actor profile columns (三源资料聚合)

Revision ID: b3c7e2f1a901
Revises: a91b2c4d8e77
Create Date: 2026-08-19 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3c7e2f1a901'
down_revision: Union[str, None] = 'a91b2c4d8e77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('blood_type', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('zodiac', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('birthplace', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('nationality', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('active_years', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('bio', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('timeline', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('alias', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('profile_fetched', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('profile_fetch_failed', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('actors', schema=None) as batch_op:
        for col in ('blood_type', 'zodiac', 'birthplace', 'nationality', 'active_years',
                    'bio', 'timeline', 'alias', 'profile_fetched', 'profile_fetch_failed'):
            batch_op.drop_column(col)
