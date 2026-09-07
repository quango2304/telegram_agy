"""Repository Protocols (KB rules §3: ``I`` prefix, no ``Protocol`` suffix)."""

from __future__ import annotations

from typing import Protocol

from src.domain.entities.chat_thread import ChatThread
from src.domain.entities.message import Message
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
    async def mint(self, thread_id: int, ttl_seconds: int) -> str: ...

    async def resolve(self, session_key: str) -> int | None:
        """Thread id, or ``None`` if the key is unknown or expired."""
        ...

    async def delete(self, session_key: str) -> None: ...

    async def purge_expired(self) -> int: ...
