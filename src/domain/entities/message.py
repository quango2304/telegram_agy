"""A single stored Telegram message. ``text`` is never NULL — non-text messages
are stored as a Vietnamese placeholder (step 4)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base, created_at_col, pk


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "tg_message_id", name="uq_messages_thread_tg"),
        Index("ix_messages_thread_sent_at", "thread_id", "sent_at"),
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
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
