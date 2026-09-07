"""Binds one ``agy`` run to one chat thread.

MCP servers are registered globally and ``agy mcp add --header`` is static, so
there is no transport-level way to tell the MCP server which thread a run belongs
to — and the model must not be trusted to echo a raw ``chat_id`` back. Each run
gets a short-lived opaque key instead, embedded in the prompt and resolved
server-side.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base


class AgySession(Base):
    __tablename__ = "agy_sessions"
    __table_args__ = (Index("ix_agy_sessions_expires_at", "expires_at"),)

    session_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
