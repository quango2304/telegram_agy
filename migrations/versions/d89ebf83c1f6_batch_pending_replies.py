"""batch pending replies

Revision ID: d89ebf83c1f6
Revises: 4614938e5fc5
Create Date: 2026-09-07 12:20:40.162467
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd89ebf83c1f6'
down_revision: str | Sequence[str] | None = '4614938e5fc5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "is_trigger",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_messages_thread_trigger",
        "messages",
        ["thread_id", "is_trigger", "id"],
    )
    op.add_column(
        "chat_threads",
        sa.Column("last_answered_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_threads", "last_answered_message_id")
    op.drop_index("ix_messages_thread_trigger", table_name="messages")
    op.drop_column("messages", "is_trigger")
