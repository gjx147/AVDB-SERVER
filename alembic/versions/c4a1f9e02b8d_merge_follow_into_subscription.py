"""merge follow into subscription

把 Actor.is_followed 并入 Subscription(sub_type='actor')，并删除该列。

注意：actors 被 subscriptions(actor_id) 以 ON DELETE CASCADE 引用。
SQLite 在 foreign_keys=ON 时，batch_alter_table 重建 actors 会触发隐式
DELETE → 级联清空 actor 订阅。因此这里改用原生 `ALTER TABLE ... DROP COLUMN`
（SQLite 3.35+，不重建表、不触发级联）来删除列。

Revision ID: c4a1f9e02b8d
Revises: e7640bfdb7f3
Create Date: 2026-08-13 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a1f9e02b8d'
down_revision: Union[str, None] = 'e7640bfdb7f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 去重既有 actor 订阅（每个演员只保留 id 最小的一条），为唯一约束铺路
    op.execute(
        "DELETE FROM subscriptions "
        "WHERE sub_type='actor' AND actor_id IS NOT NULL "
        "AND id NOT IN ("
        "  SELECT MIN(id) FROM subscriptions "
        "  WHERE sub_type='actor' AND actor_id IS NOT NULL "
        "  GROUP BY actor_id"
        ")"
    )

    # 2. 数据回填：is_followed=1 且尚无 actor 订阅的演员 → 插入 actor 订阅（auto_add=0，只通知不入库）
    op.execute(
        "INSERT INTO subscriptions "
        "  (name, sub_type, actor_id, auto_add, enabled, check_interval_hours, created_at, updated_at) "
        "SELECT a.name, 'actor', a.id, 0, 1, 6, datetime('now'), datetime('now') "
        "FROM actors a "
        "WHERE a.is_followed = 1 "
        "  AND a.id NOT IN ("
        "    SELECT actor_id FROM subscriptions "
        "    WHERE sub_type='actor' AND actor_id IS NOT NULL"
        "  )"
    )

    # 3. actor 订阅每演员唯一（ranking/composite 的 actor_id 为 NULL，SQLite 视 NULL 互异，不受约束）
    op.create_index(
        'uq_subscriptions_type_actor', 'subscriptions',
        ['sub_type', 'actor_id'], unique=True,
    )

    # 4. 删除 actors.is_followed：先删索引，再用原生 DROP COLUMN（不重建表，不触发 FK 级联）
    op.execute("DROP INDEX IF EXISTS idx_actors_followed")
    op.execute("ALTER TABLE actors DROP COLUMN is_followed")


def downgrade() -> None:
    # 恢复 is_followed 列（原生 ADD COLUMN，不触发级联）
    op.execute("ALTER TABLE actors ADD COLUMN is_followed BOOLEAN NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS idx_actors_followed ON actors (is_followed)")
    # 按订阅回写 is_followed=1
    op.execute(
        "UPDATE actors SET is_followed = 1 "
        "WHERE id IN ("
        "  SELECT actor_id FROM subscriptions "
        "  WHERE sub_type='actor' AND actor_id IS NOT NULL"
        ")"
    )
    op.drop_index('uq_subscriptions_type_actor', table_name='subscriptions')
