from __future__ import annotations

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.chat_thread import ChatThread
from src.domain.interfaces.repositories import IThreadRepository


class ThreadRepositoryImpl(IThreadRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self, chat_id: int, topic_id: int, chat_type: str, title: str | None
    ) -> ChatThread:
        # One statement — two pollers/workers can race on the same new thread.
        stmt = (
            pg_insert(ChatThread)
            .values(chat_id=chat_id, topic_id=topic_id, chat_type=chat_type, title=title)
            .on_conflict_do_update(
                index_elements=["chat_id", "topic_id"],
                set_={"title": title, "chat_type": chat_type, "updated_at": func.now()},
            )
            .returning(ChatThread)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def get_by_id(self, thread_id: int) -> ChatThread | None:
        return await self._session.get(ChatThread, thread_id)

    async def bump_last_answered(self, thread_id: int, message_id: int) -> None:
        # GREATEST so an out-of-order completion can never move the mark back.
        await self._session.execute(
            update(ChatThread)
            .where(ChatThread.id == thread_id)
            .values(
                last_answered_message_id=func.greatest(
                    func.coalesce(ChatThread.last_answered_message_id, 0), message_id
                )
            )
        )
