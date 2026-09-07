"""SQLAlchemy Unit of Work (KB ``05-database-uow.md``).

One ``AsyncSession`` per ``async with`` block; the four repositories share it.
``__aexit__`` always rolls back what was not committed, then closes.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.db.repositories.memory_repository_impl import MemoryRepositoryImpl
from src.infrastructure.db.repositories.message_repository_impl import MessageRepositoryImpl
from src.infrastructure.db.repositories.schedule_repository_impl import ScheduleRepositoryImpl
from src.infrastructure.db.repositories.session_repository_impl import SessionRepositoryImpl
from src.infrastructure.db.repositories.thread_repository_impl import ThreadRepositoryImpl


class SqlAlchemyUnitOfWork(IUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.threads = ThreadRepositoryImpl(self._session)
        self.messages = MessageRepositoryImpl(self._session)
        self.memories = MemoryRepositoryImpl(self._session)
        self.sessions = SessionRepositoryImpl(self._session)
        self.schedules = ScheduleRepositoryImpl(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        assert self._session is not None
        await self._session.commit()

    async def rollback(self) -> None:
        assert self._session is not None
        await self._session.rollback()
