"""eng: agent_actions 写操作审计表

Revision ID: ea1b2c3d4e5f6
Revises: g4a1b2c3d4e5f
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "ea1b2c3d4e5f6"
down_revision = "g4a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tool", sa.String(length=50), nullable=False),
        sa.Column("args_json", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(length=100), nullable=False, server_default="ai"),
        sa.Column("result", sa.String(length=500), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("undone", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_agent_actions_created", "agent_actions", ["created_at"])
    op.create_index("idx_agent_actions_tool", "agent_actions", ["tool"])


def downgrade() -> None:
    op.drop_index("idx_agent_actions_tool", table_name="agent_actions")
    op.drop_index("idx_agent_actions_created", table_name="agent_actions")
    op.drop_table("agent_actions")
