"""Repository Protocols (KB rules §3: ``I`` prefix, no ``Protocol`` suffix)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.entities.agy_session import AgySession
from src.domain.entities.chat_thread import ChatThread
from src.domain.entities.message import Message
from src.domain.entities.scheduled_task import ScheduledTask
from src.domain.entities.thread_memory import ThreadMemory


class IThreadRepository(Protocol):
    async def get_or_create(
        self, chat_id: int, topic_id: int, chat_type: str, title: str | None
    ) -> ChatThread: ...

    async def get_by_id(self, thread_id: int) -> ChatThread | None: ...


class IMessageRepository(Protocol):
    async def add(self, msg: Message) -> Message:
        """Idempotent upsert on ``(thread_id, tg_message_id)``."""
        ...

    async def get_by_id(self, message_id: int) -> Message | None: ...

    async def update_text(self, thread_id: int, tg_message_id: int, new_text: str) -> None:
        """Edited-message path: refresh ``text`` for an existing row."""
        ...

    async def last_n(self, thread_id: int, n: int) -> list[Message]:
        """The newest ``n`` messages, returned oldest→newest."""
        ...

    async def prune_to_last_n(self, n: int) -> int:
        """Delete every message outside the newest ``n`` per thread. Returns rows deleted."""
        ...


class IMemoryRepository(Protocol):
    async def get(self, thread_id: int) -> ThreadMemory | None: ...

    async def upsert(self, thread_id: int, content: str) -> ThreadMemory: ...


class ISessionRepository(Protocol):
    async def mint(
        self,
        thread_id: int,
        ttl_seconds: int,
        trigger_tg_message_id: int | None = None,
    ) -> str: ...

    async def resolve(self, session_key: str) -> int | None:
        """Thread id, or ``None`` if the key is unknown or expired."""
        ...

    async def get_active(self, session_key: str) -> AgySession | None:
        """The full unexpired row, or ``None``."""
        ...

    async def bump_sent_count(self, session_key: str, by: int) -> int:
        """Add ``by`` to ``sent_count``; return the new total."""
        ...

    async def delete(self, session_key: str) -> None: ...

    async def purge_expired(self) -> int: ...


class IScheduleRepository(Protocol):
    async def create(
        self,
        thread_id: int,
        instruction: str,
        run_at: datetime,
        cron: str | None,
        created_by: str | None,
    ) -> ScheduledTask: ...

    async def list_for_thread(self, thread_id: int) -> list[ScheduledTask]: ...

    async def get_by_id(self, task_id: int) -> ScheduledTask | None: ...

    async def get_for_thread(self, thread_id: int, task_id: int) -> ScheduledTask | None: ...

    async def update_fields(
        self, thread_id: int, task_id: int, **fields: object
    ) -> ScheduledTask | None: ...

    async def delete(self, thread_id: int, task_id: int) -> bool: ...

    async def due(self, now: datetime, limit: int) -> list[ScheduledTask]:
        """Enabled tasks whose ``run_at`` has passed, oldest first."""
        ...

    async def mark_ran(self, task_id: int, ran_at: datetime, next_run_at: datetime | None) -> None:
        """Record a fire. ``next_run_at`` None disables the task (one-off done)."""
        ...
