"""A per-thread scheduled instruction.

When ``run_at`` is reached, ``beat`` enqueues a normal reply turn for the thread
whose "message" is :attr:`instruction` — the bot answers it exactly as if
someone had just sent it. ``cron`` NULL means one-off; otherwise it is a 5-field
expression evaluated in Asia/Ho_Chi_Minh and ``run_at`` rolls forward after each
fire.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.entities.base import Base, created_at_col, pk, updated_at_col


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    __table_args__ = (Index("ix_scheduled_tasks_enabled_run_at", "enabled", "run_at"),)

    id: Mapped[int] = pk()
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
