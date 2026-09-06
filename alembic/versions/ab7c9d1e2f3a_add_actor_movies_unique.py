"""actor_movies unique (actor_id, task_id) + dedupe

Revision ID: ab7c9d1e2f3a
Revises: us1a2b3c4d5e6f
Create Date: 2026-09-06 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'ab7c9d1e2f3a'
down_revision: Union[str, None] = 'us1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先清理历史重复关联行（保留每组最小 id），否则唯一约束创建失败
    op.execute(
        "DELETE FROM actor_movies "
        "WHERE id NOT IN (SELECT MIN(id) FROM actor_movies GROUP BY actor_id, task_id)"
    )
    with op.batch_alter_table('actor_movies', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_actor_movies_actor_task', ['actor_id', 'task_id'])


def downgrade() -> None:
    with op.batch_alter_table('actor_movies', schema=None) as batch_op:
        batch_op.drop_constraint('uq_actor_movies_actor_task', type_='unique')