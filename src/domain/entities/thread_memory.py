"""One long-lived memory note per chat thread. After the hourly cleanup this is
the only durable state — see step 8."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base, updated_at_col


class ThreadMemory(Base):
    __tablename__ = "thread_memories"

    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_threads.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = updated_at_col()
