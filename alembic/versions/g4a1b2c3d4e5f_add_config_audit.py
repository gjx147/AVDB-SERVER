"""G4: config_audit 配置修改审计表

Revision ID: g4a1b2c3d4e5f
Revises: head
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "g4a1b2c3d4e5f"
down_revision = "a4b5c6d7e8f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(length=100), nullable=False, server_default="ai"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="agent"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_config_audit_key", "config_audit", ["key"])
    op.create_index("idx_config_audit_created", "config_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_config_audit_created", table_name="config_audit")
    op.drop_index("idx_config_audit_key", table_name="config_audit")
    op.drop_table("config_audit")
