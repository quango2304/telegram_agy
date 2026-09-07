"""Unit-of-Work Protocol (KB ``05-database-uow.md``)."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from src.domain.interfaces.repositories import (
    IMemoryRepository,
    IMessageRepository,
    ISessionRepository,
    IThreadRepository,
)


class IUnitOfWork(Protocol):
    threads: IThreadRepository
    messages: IMessageRepository
    memories: IMemoryRepository
    sessions: ISessionRepository

    async def __aenter__(self) -> IUnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
