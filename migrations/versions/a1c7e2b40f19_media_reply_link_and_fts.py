"""media attachments, reply linkage, and full-text search

Hand-written on purpose: --autogenerate cannot express a Postgres GENERATED
column or a GIN index, and would try to drop them on the next run.

Revision ID: a1c7e2b40f19
Revises: d89ebf83c1f6
Create Date: 2026-09-08 07:40:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c7e2b40f19'
down_revision: str | Sequence[str] | None = 'd89ebf83c1f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("reply_to_tg_message_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("messages", sa.Column("media_kind", sa.String(32), nullable=True))
    op.add_column("messages", sa.Column("media_file_id", sa.String(512), nullable=True))
    op.add_column("messages", sa.Column("media_mime", sa.String(128), nullable=True))
    op.add_column("messages", sa.Column("media_file_name", sa.String(256), nullable=True))

    # Retention now sweeps by age, so `sent_at` needs its own index (the existing
    # composite one leads with thread_id and can't serve a global range scan).
    op.create_index("ix_messages_sent_at", "messages", ["sent_at"])

    # Two-arg to_tsvector is the IMMUTABLE form a generated column requires.
    # 'simple' = no stemming, no accent folding — the right call for chat that
    # mixes Vietnamese and English.
    op.execute(
        "ALTER TABLE messages ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED"
    )
    op.execute("CREATE INDEX ix_messages_tsv ON messages USING GIN (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_tsv")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS tsv")
    op.drop_index("ix_messages_sent_at", table_name="messages")
    op.drop_column("messages", "media_file_name")
    op.drop_column("messages", "media_mime")
    op.drop_column("messages", "media_file_id")
    op.drop_column("messages", "media_kind")
    op.drop_column("messages", "reply_to_tg_message_id")
