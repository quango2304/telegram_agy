"""The reply worker: per-thread Redis lock, prompt, ``agy``, send, store.

Sync/async bridge: the Celery task body is sync, everything it needs is async.
We ``asyncio.run`` once per task and build the engine *inside that loop* — an
async engine binds to the first loop that touches it, and each task gets a new
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis
from redis.exceptions import LockError

from src.domain.entities.message import Message
from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.agy.agy_client_impl import AgyClient
from src.infrastructure.agy.prompt_builder import HistoryLine, build_prompt
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork
from src.infrastructure.telegram.sender import TelegramSender
from src.shared.chunking import split_message
from src.shared.logger import get_logger
from src.shared.persona import FALLBACK_REPLY

logger = get_logger(__name__)

UowFactory = Callable[[], IUnitOfWork]


def run_generate_reply(task: Any, message_id: int) -> None:
    asyncio.run(_run(task, message_id))


async def _run(task: Any, message_id: int) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)

    def uow_factory() -> IUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    redis_client = redis.Redis.from_url(settings.redis_url)
    try:
        async with uow_factory() as uow:
            trigger = await uow.messages.get_by_id(message_id)
            thread_id = trigger.thread_id if trigger is not None else None
        if thread_id is None:
            logger.warning("trigger message gone", extra={"message_id": message_id})
            return

        lock = redis_client.lock(
            f"lock:thread:{thread_id}",
            timeout=settings.agy_timeout_seconds + 60,
            blocking=False,
        )
        if not lock.acquire(blocking=False):
            # Another reply for this thread is in flight — normal path, not an error.
            raise task.retry(countdown=3)

        try:
            await _run_reply(settings, uow_factory, message_id, thread_id)
        finally:
            # lock may have already expired — don't mask the real error
            with contextlib.suppress(LockError):
                lock.release()
    finally:
        await engine.dispose()
        redis_client.close()


async def _run_reply(
    settings: Settings, uow_factory: UowFactory, message_id: int, thread_id: int
) -> None:
    sender = TelegramSender(settings.telegram_bot_token)
    agy = AgyClient(settings)
    session_key: str | None = None
    try:
        async with uow_factory() as uow:
            trigger = await uow.messages.get_by_id(message_id)
            thread = await uow.threads.get_by_id(thread_id)
            if trigger is None or thread is None:
                logger.warning("reply target gone", extra={"message_id": message_id})
                return

            # Extract every value we need to plain data BEFORE the session
            # closes — detached ORM instances raise on attribute access.
            chat_id = thread.chat_id
            topic_id = thread.topic_id
            trigger_tg_id = trigger.tg_message_id

            rows = await uow.messages.last_n(thread_id, settings.context_message_limit)
            history = [HistoryLine(r.from_name or "ai đó", r.text, r.is_bot_self) for r in rows]
            memory = await uow.memories.get(thread_id)
            memory_text = memory.content if memory else None
            session_key = await uow.sessions.mint(thread_id, settings.session_ttl_seconds)
            await uow.commit()

        await sender.send_typing(chat_id, topic_id)

        prompt = build_prompt(
            session_key=session_key,
            memory=memory_text,
            history=history,
            bot_username=settings.telegram_bot_username,
            max_chars=settings.agy_prompt_max_chars,
            truncate_chars=settings.agy_message_truncate_chars,
        )

        result = await agy.run(prompt)
        reply_text = result.text if (result.ok and result.text) else FALLBACK_REPLY

        chunks = split_message(reply_text, settings.telegram_max_chars) or [FALLBACK_REPLY]
        async with uow_factory() as uow:
            for i, chunk in enumerate(chunks):
                sent_id = await sender.send_reply(
                    chat_id,
                    chunk,
                    topic_id,
                    reply_to_message_id=trigger_tg_id if i == 0 else None,
                )
                await uow.messages.add(
                    Message(
                        thread_id=thread_id,
                        tg_message_id=sent_id,
                        from_user_id=None,
                        from_username=None,
                        from_name=None,
                        is_bot_self=True,
                        text=chunk,
                        sent_at=datetime.now(UTC),
                    )
                )
            await uow.commit()
        logger.info(
            "reply sent",
            extra={"thread_id": thread_id, "chunks": len(chunks), "agy_ok": result.ok},
        )
    finally:
        if session_key is not None:
            try:
                async with uow_factory() as uow:
                    await uow.sessions.delete(session_key)
                    await uow.commit()
            except Exception:
                logger.warning("session cleanup failed", extra={"session_key": session_key})
        await sender.close()
