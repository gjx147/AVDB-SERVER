"""pref: user_prefs 用户偏好表

Revision ID: pf1a2b3c4d5e6f
Revises: ea1b2c3d4e5f6
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "pf1a2b3c4d5e6f"
down_revision = "ea1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_prefs",
        sa.Column("user", sa.String(length=100), primary_key=True),
        sa.Column("prefs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("user_prefs")
