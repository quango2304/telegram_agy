"""A chat thread — the unit of everything: ``(chat_id, topic_id)``.

Private chat and plain group → ``topic_id = 0``. Forum topic → the topic id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base, created_at_col, pk, updated_at_col


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (UniqueConstraint("chat_id", "topic_id", name="uq_chat_threads_chat_topic"),)

    id: Mapped[int] = pk()
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    # private | group | supergroup | channel
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # High-water mark: the largest ``messages.id`` already covered by a reply
    # run. A queued reply whose trigger id is <= this is a no-op (an earlier
    # run in the same thread already answered it in a batch).
    last_answered_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
