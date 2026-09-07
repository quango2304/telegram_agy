from __future__ import annotations

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.message import Message
from src.domain.interfaces.repositories import IMessageRepository

_PRUNE_SQL = text(
    """
    DELETE FROM messages m USING (
        SELECT id, row_number() OVER (
            PARTITION BY thread_id ORDER BY sent_at DESC, id DESC) AS rn
        FROM messages
    ) r
    WHERE m.id = r.id AND r.rn > :n
    """
)


class MessageRepositoryImpl(IMessageRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, msg: Message) -> Message:
        values = {
            "thread_id": msg.thread_id,
            "tg_message_id": msg.tg_message_id,
            "from_user_id": msg.from_user_id,
            "from_username": msg.from_username,
            "from_name": msg.from_name,
            "is_bot_self": msg.is_bot_self,
            "text": msg.text,
            "sent_at": msg.sent_at,
        }
        stmt = (
            pg_insert(Message)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["thread_id", "tg_message_id"])
            .returning(Message)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        # Redelivered update — the row already exists.
        return (
            await self._session.execute(
                select(Message).where(
                    Message.thread_id == msg.thread_id,
                    Message.tg_message_id == msg.tg_message_id,
                )
            )
        ).scalar_one()

    async def update_text(self, thread_id: int, tg_message_id: int, new_text: str) -> None:
        await self._session.execute(
            update(Message)
            .where(Message.thread_id == thread_id, Message.tg_message_id == tg_message_id)
            .values(text=new_text)
        )

    async def last_n(self, thread_id: int, n: int) -> list[Message]:
        rows = (
            (
                await self._session.execute(
                    select(Message)
                    .where(Message.thread_id == thread_id)
                    .order_by(Message.sent_at.desc(), Message.id.desc())
                    .limit(n)
                )
            )
            .scalars()
            .all()
        )
        return list(reversed(rows))

    async def prune_to_last_n(self, n: int) -> int:
        result = await self._session.execute(_PRUNE_SQL, {"n": n})
        return int(getattr(result, "rowcount", 0) or 0)
