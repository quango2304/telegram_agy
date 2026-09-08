"""A single stored Telegram message. ``text`` is never NULL — non-text messages
are stored as a Vietnamese placeholder (step 4); the media itself is described by
the ``media_*`` columns and fetched lazily, only when a reply run needs it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base, created_at_col, pk


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "tg_message_id", name="uq_messages_thread_tg"),
        Index("ix_messages_thread_sent_at", "thread_id", "sent_at"),
        Index("ix_messages_thread_trigger", "thread_id", "is_trigger", "id"),
        Index("ix_messages_sent_at", "sent_at"),  # retention sweep
        Index("ix_messages_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = pk()
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    tg_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_bot_self: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Set by the delivery layer when this message is what triggered a reply run
    # (DM, @mention, or reply to the bot). The lock-holding worker answers every
    # unanswered trigger for the thread in one agy run; siblings then no-op.
    is_trigger: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Telegram id of the message this one replies to (not our row id) — lets a
    # reply run find the photo someone is pointing at with "cái này là gì".
    reply_to_tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Attached media, if any. Nothing is downloaded at ingest time: the file_id
    # stays valid indefinitely, so the worker fetches on demand (media_kind is
    # one of photo/voice/video/document/... — only images are used today).
    media_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_file_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    # Full-text index over `text`, maintained by Postgres. The two-arg
    # to_tsvector is the IMMUTABLE form a generated column requires; 'simple'
    # does no stemming or accent folding, which suits mixed Vietnamese/English
    # chat (see search_history). `persisted=True` keeps it out of INSERTs.
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', text)", persisted=True),
        nullable=True,
    )
