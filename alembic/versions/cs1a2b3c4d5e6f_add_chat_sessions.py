"""chat: chat_sessions / chat_messages 会话持久化表

Revision ID: cs1a2b3c4d5e6f
Revises: pf1a2b3c4d5e6f
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "cs1a2b3c4d5e6f"
down_revision = "pf1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user", sa.String(length=100), nullable=False, server_default="default"),
        sa.Column("title", sa.String(length=200), nullable=False, server_default="new chat"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_chat_sessions_user", "chat_sessions", ["user"])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_chat_messages_session", "chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("idx_chat_messages_session", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("idx_chat_sessions_user", table_name="chat_sessions")
    op.drop_table("chat_sessions")
