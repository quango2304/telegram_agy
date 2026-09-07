"""The reply worker: per-thread Redis lock, prompt, ``agy``, send, store.

Two entry points share one body:
- ``run_generate_reply`` — a real Telegram trigger (mention / reply / DM).
- ``run_scheduled_reply`` — a due ``scheduled_tasks`` row; the saved instruction
  plays the part of the trigger message, and the reply is not a Telegram reply
  to anything (``trigger_tg_id`` is ``None``).

Sync/async bridge: the Celery task body is sync, everything it needs is async.
We ``asyncio.run`` once per task and build the engine *inside that loop* — an
async engine binds to the first loop that touches it, and each task gets a new
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
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
from src.infrastructure.schedule.cron import to_local_str
from src.infrastructure.telegram.sender import TelegramSender
from src.shared.chunking import split_message
from src.shared.logger import get_logger
from src.shared.persona import FALLBACK_REPLY

logger = get_logger(__name__)

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class TriggerContext:
    thread_id: int
    chat_id: int
    topic_id: int
    trigger_name: str
    trigger_text: str
    trigger_tg_id: int | None  # None for a scheduled run — reply is not a Telegram reply


def run_generate_reply(task: Any, message_id: int) -> None:
    asyncio.run(_dispatch(task, message_kind="message", ref_id=message_id))


def run_scheduled_reply(task: Any, scheduled_task_id: int) -> None:
    asyncio.run(_dispatch(task, message_kind="scheduled", ref_id=scheduled_task_id))


async def _dispatch(task: Any, *, message_kind: str, ref_id: int) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)

    def uow_factory() -> IUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    redis_client = redis.Redis.from_url(settings.redis_url)
    try:
        ctx = await _resolve_context(uow_factory, message_kind, ref_id)
        if ctx is None:
            logger.warning("trigger gone", extra={"kind": message_kind, "ref_id": ref_id})
            return

        lock = redis_client.lock(
            f"lock:thread:{ctx.thread_id}",
            timeout=settings.agy_timeout_seconds + 60,
            blocking=False,
        )
        if not lock.acquire(blocking=False):
            # Another reply for this thread is in flight — normal path, not an error.
            raise task.retry(countdown=3)

        try:
            await _run_reply(settings, uow_factory, ctx)
        finally:
            # lock may have already expired — don't mask the real error
            with contextlib.suppress(LockError):
                lock.release()
    finally:
        await engine.dispose()
        redis_client.close()


async def _resolve_context(
    uow_factory: UowFactory, message_kind: str, ref_id: int
) -> TriggerContext | None:
    async with uow_factory() as uow:
        if message_kind == "scheduled":
            sched = await uow.schedules.get_by_id(ref_id)
            if sched is None:
                return None
            instruction = sched.instruction
            created_by = sched.created_by
            thread = await uow.threads.get_by_id(sched.thread_id)
            if thread is None:
                return None
            return TriggerContext(
                thread_id=thread.id,
                chat_id=thread.chat_id,
                topic_id=thread.topic_id,
                trigger_name=created_by or "lịch hẹn",
                trigger_text=instruction,
                trigger_tg_id=None,
            )

        msg = await uow.messages.get_by_id(ref_id)
        if msg is None:
            return None
        thread = await uow.threads.get_by_id(msg.thread_id)
        if thread is None:
            return None
        return TriggerContext(
            thread_id=thread.id,
            chat_id=thread.chat_id,
            topic_id=thread.topic_id,
            trigger_name=msg.from_name or "ai đó",
            trigger_text=msg.text,
            trigger_tg_id=msg.tg_message_id,
        )


async def _run_reply(settings: Settings, uow_factory: UowFactory, ctx: TriggerContext) -> None:
    sender = TelegramSender(settings.telegram_bot_token)
    agy = AgyClient(settings)
    session_key: str | None = None
    try:
        async with uow_factory() as uow:
            rows = await uow.messages.last_n(ctx.thread_id, settings.context_message_limit)
            history = [HistoryLine(r.from_name or "ai đó", r.text, r.is_bot_self) for r in rows]
            memory = await uow.memories.get(ctx.thread_id)
            memory_text = memory.content if memory else None
            session_key = await uow.sessions.mint(ctx.thread_id, settings.session_ttl_seconds)
            await uow.commit()

        await sender.send_typing(ctx.chat_id, ctx.topic_id)

        prompt = build_prompt(
            session_key=session_key,
            memory=memory_text,
            history=history,
            trigger_name=ctx.trigger_name,
            trigger_text=ctx.trigger_text,
            bot_username=settings.telegram_bot_username,
            now_local=to_local_str(datetime.now(UTC)),
            max_chars=settings.agy_prompt_max_chars,
            truncate_chars=settings.agy_message_truncate_chars,
        )

        result = await agy.run(prompt)
        reply_text = result.text if (result.ok and result.text) else FALLBACK_REPLY

        chunks = split_message(reply_text, settings.telegram_max_chars) or [FALLBACK_REPLY]
        async with uow_factory() as uow:
            for i, chunk in enumerate(chunks):
                reply_to = ctx.trigger_tg_id if (i == 0 and ctx.trigger_tg_id) else None
                sent_id = await sender.send_reply(ctx.chat_id, chunk, ctx.topic_id, reply_to)
                await uow.messages.add(
                    Message(
                        thread_id=ctx.thread_id,
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
            extra={"thread_id": ctx.thread_id, "chunks": len(chunks), "agy_ok": result.ok},
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
