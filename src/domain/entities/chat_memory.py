"""One long-lived memory note per **chat** (not per topic).

A private chat has one; a group/supergroup has one shared across all its forum
topics. Keyed by the raw Telegram ``chat_id`` (no FK — ``chat_threads`` rows are
per-topic). After the hourly message cleanup this is the only durable state.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base, updated_at_col


class ChatMemory(Base):
    __tablename__ = "chat_memories"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = updated_at_col()
