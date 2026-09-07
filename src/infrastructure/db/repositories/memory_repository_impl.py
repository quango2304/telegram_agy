from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.thread_memory import ThreadMemory
from src.domain.interfaces.repositories import IMemoryRepository


class MemoryRepositoryImpl(IMemoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, thread_id: int) -> ThreadMemory | None:
        return await self._session.get(ThreadMemory, thread_id)

    async def upsert(self, thread_id: int, content: str) -> ThreadMemory:
        stmt = (
            pg_insert(ThreadMemory)
            .values(thread_id=thread_id, content=content)
            .on_conflict_do_update(
                index_elements=["thread_id"],
                set_={"content": content, "updated_at": func.now()},
            )
            .returning(ThreadMemory)
        )
        return (await self._session.execute(stmt)).scalar_one()
