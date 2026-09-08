from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.chat_thread import ChatThread
from src.domain.entities.message import Message
from src.domain.interfaces.repositories import IMessageRepository

# Retention sweep: drop anything past the cutoff EXCEPT each thread's newest
# `keep` rows, so a thread nobody has touched in weeks still has context to
# answer with. One statement with a window function — never loop in Python.
_PRUNE_SQL = text(
    """
    DELETE FROM messages m USING (
        SELECT id, sent_at, row_number() OVER (
            PARTITION BY thread_id ORDER BY sent_at DESC, id DESC) AS rn
        FROM messages
    ) r
    WHERE m.id = r.id AND r.rn > :keep AND r.sent_at < :cutoff
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
            "reply_to_tg_message_id": msg.reply_to_tg_message_id,
            "media_kind": msg.media_kind,
            "media_file_id": msg.media_file_id,
            "media_mime": msg.media_mime,
            "media_file_name": msg.media_file_name,
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

    async def get_by_id(self, message_id: int) -> Message | None:
        return await self._session.get(Message, message_id)

    async def mark_trigger(self, message_id: int) -> None:
        await self._session.execute(
            update(Message).where(Message.id == message_id).values(is_trigger=True)
        )

    async def pending_triggers(self, thread_id: int, after_id: int, limit: int) -> list[Message]:
        rows = (
            (
                await self._session.execute(
                    select(Message)
                    .where(
                        Message.thread_id == thread_id,
                        Message.is_trigger.is_(True),
                        Message.id > after_id,
                    )
                    .order_by(Message.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return list(reversed(rows))

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

    async def get_by_tg_id(self, thread_id: int, tg_message_id: int) -> Message | None:
        return (
            await self._session.execute(
                select(Message).where(
                    Message.thread_id == thread_id,
                    Message.tg_message_id == tg_message_id,
                )
            )
        ).scalar_one_or_none()

    async def prune_older_than(self, cutoff: datetime, keep_per_thread: int) -> int:
        result = await self._session.execute(
            _PRUNE_SQL, {"cutoff": cutoff, "keep": keep_per_thread}
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def search(self, chat_id: int, query: str, limit: int) -> list[Message]:
        query = query.strip()
        if not query:
            return []

        tsq = func.websearch_to_tsquery("simple", query)
        stmt = (
            select(Message)
            .join(ChatThread, ChatThread.id == Message.thread_id)
            .where(ChatThread.chat_id == chat_id, Message.tsv.op("@@")(tsq))
            .order_by(func.ts_rank(Message.tsv, tsq).desc(), Message.sent_at.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        if rows:
            return rows

        # FTS found nothing: retry as a substring match. Catches a query the
        # tokeniser split differently, or a fragment inside a longer word.
        like_stmt = (
            select(Message)
            .join(ChatThread, ChatThread.id == Message.thread_id)
            .where(ChatThread.chat_id == chat_id, Message.text.ilike(f"%{query}%"))
            .order_by(Message.sent_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(like_stmt)).scalars().all())
