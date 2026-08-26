"""usage: ai_usage AI 调用用量表

Revision ID: us1a2b3c4d5e6f
Revises: cs1a2b3c4d5e6f
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "us1a2b3c4d5e6f"
down_revision = "cs1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(length=50), nullable=False, server_default="chat"),
        sa.Column("model", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_ai_usage_created", "ai_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_ai_usage_created", table_name="ai_usage")
    op.drop_table("ai_usage")
