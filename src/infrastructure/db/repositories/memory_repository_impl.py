from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.chat_memory import ChatMemory
from src.domain.interfaces.repositories import IMemoryRepository


class MemoryRepositoryImpl(IMemoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, chat_id: int) -> ChatMemory | None:
        return await self._session.get(ChatMemory, chat_id)

    async def upsert(self, chat_id: int, content: str) -> ChatMemory:
        stmt = (
            pg_insert(ChatMemory)
            .values(chat_id=chat_id, content=content)
            .on_conflict_do_update(
                index_elements=["chat_id"],
                set_={"content": content, "updated_at": func.now()},
            )
            .returning(ChatMemory)
        )
        return (await self._session.execute(stmt)).scalar_one()
