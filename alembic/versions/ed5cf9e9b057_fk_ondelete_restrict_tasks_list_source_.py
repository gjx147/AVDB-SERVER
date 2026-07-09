"""FK ondelete RESTRICT tasks.list_source_id

Revision ID: ed5cf9e9b057
Revises: 568e1c94c2c8
Create Date: 2026-07-09 21:32:57.448291

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ed5cf9e9b057'
down_revision: Union[str, None] = '568e1c94c2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_foreign_key(
            'fk_tasks_list_source_id', 'list_sources',
            ['list_source_id'], ['id'], ondelete='RESTRICT',
        )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tasks_list_source_id', type_='foreignkey')
