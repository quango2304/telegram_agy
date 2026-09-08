"""The reply worker: per-thread Redis lock, prompt, run ``agy``.

``agy`` sends the actual chat messages itself, through the ``send_chat_message``
MCP tool — this module only takes the lock, builds the prompt, runs the CLI, and
cleans up. Two entry points share one body:
- ``run_generate_reply`` — a real Telegram trigger (mention / reply / DM).
- ``run_scheduled_reply`` — a due ``scheduled_tasks`` row; the saved instruction
  plays the part of the trigger message, and the first sent message is not a
  Telegram reply to anything (``trigger_tg_id`` is ``None``).

Sync/async bridge: the Celery task body is sync, everything it needs is async.
We ``asyncio.run`` once per task and build the engine *inside that loop* — an
async engine binds to the first loop that touches it, and each task gets a new
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import redis
from redis.exceptions import LockError

from src.delivery.telegram.parsers import is_image_media
from src.domain.entities.message import Message
from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.agy.agy_client_impl import AgyClient
from src.infrastructure.agy.prompt_builder import (
    HistoryLine,
    PendingTrigger,
    PromptAttachment,
    build_prompt,
)
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork
from src.infrastructure.schedule.cron import to_local_str
from src.infrastructure.telegram.sender import TelegramSender
from src.shared.logger import get_logger

logger = get_logger(__name__)

UowFactory = Callable[[], IUnitOfWork]


# Newest N unanswered triggers a single locked run will answer in one agy pass.
_MAX_PENDING_PER_RUN = 10


@dataclass(frozen=True)
class TriggerContext:
    thread_id: int
    chat_id: int
    topic_id: int
    trigger_name: str
    trigger_text: str
    trigger_tg_id: int | None  # None for a scheduled run — reply is not a Telegram reply
    ref_message_id: int | None = None  # DB messages.id of the trigger (None: scheduled)


@dataclass(frozen=True)
class MediaPick:
    """A chosen attachment, flattened to primitives.

    The UoW rolls back on exit, which expires every ORM instance it loaded — so
    anything needed after the session closes must be copied out inside the block
    (house rule; see SqlAlchemyUnitOfWork.__aexit__).
    """

    file_id: str
    tg_message_id: int
    from_name: str
    caption: str
    file_name: str


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

        # Pre-lock short-circuit: the mark only moves forward, so if an earlier
        # batched run already covered this message we can bail before even
        # queuing behind the lock (each sibling retry re-checks this).
        if ctx.ref_message_id is not None and await _already_covered(
            uow_factory, ctx.thread_id, ctx.ref_message_id
        ):
            logger.info(
                "trigger already covered by an earlier run thread_id=%s message_id=%s",
                ctx.thread_id,
                ctx.ref_message_id,
            )
            return

        lock = redis_client.lock(
            f"lock:thread:{ctx.thread_id}",
            # Outlast a full run incl. the Celery hard time limit
            # (agy_timeout + 180), so a SIGKILLed task can't leave the lock
            # expiring mid-run and let a second reply race in.
            timeout=settings.agy_timeout_seconds + 240,
            blocking=False,
        )
        if not lock.acquire(blocking=False):
            # Another reply for this thread is in flight — normal path, not an error.
            raise task.retry(countdown=5)

        try:
            if ctx.ref_message_id is not None:
                await _run_message_reply(settings, uow_factory, ctx)
            else:
                await _run_reply(settings, uow_factory, ctx, pending=None, mark_after=None)
        finally:
            # lock may have already expired — don't mask the real error
            with contextlib.suppress(LockError):
                lock.release()
    finally:
        await engine.dispose()
        redis_client.close()


async def _already_covered(uow_factory: UowFactory, thread_id: int, ref_message_id: int) -> bool:
    async with uow_factory() as uow:
        thread = await uow.threads.get_by_id(thread_id)
        mark = (thread.last_answered_message_id or 0) if thread is not None else 0
    return ref_message_id <= mark


async def _run_message_reply(
    settings: Settings, uow_factory: UowFactory, ctx: TriggerContext
) -> None:
    """Batch path for a real Telegram trigger: under the thread lock, gather
    every unanswered trigger (newest few) and answer them all in one agy run,
    then move the thread's high-water mark past them so the sibling tasks queued
    for those same messages become no-ops."""
    assert ctx.ref_message_id is not None
    async with uow_factory() as uow:
        thread = await uow.threads.get_by_id(ctx.thread_id)
        mark = (thread.last_answered_message_id or 0) if thread is not None else 0
        if ctx.ref_message_id <= mark:
            logger.info(
                "trigger already covered (post-lock) thread_id=%s message_id=%s",
                ctx.thread_id,
                ctx.ref_message_id,
            )
            return
        rows = await uow.messages.pending_triggers(ctx.thread_id, mark, _MAX_PENDING_PER_RUN)
        if not rows:
            # Flag never landed (mark_trigger raced or failed) — fall back to the
            # one message we were dispatched for.
            ref = await uow.messages.get_by_id(ctx.ref_message_id)
            if ref is None:
                logger.warning(
                    "trigger gone thread_id=%s message_id=%s", ctx.thread_id, ctx.ref_message_id
                )
                return
            rows = [ref]
        pending = [
            PendingTrigger(tg_message_id=r.tg_message_id, name=r.from_name or "ai đó", text=r.text)
            for r in rows
        ]
        mark_after = rows[-1].id

    await _run_reply(settings, uow_factory, ctx, pending=pending, mark_after=mark_after)


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
            ref_message_id=msg.id,
        )


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _attachment_name(msg: Message) -> str:
    """A predictable, shell-safe basename agy can reference in the workdir."""
    ext = ".jpg"
    if msg.media_file_name and "." in msg.media_file_name:
        ext = "." + _SAFE_NAME.sub("", msg.media_file_name.rsplit(".", 1)[1])[:8].lower()
    elif msg.media_mime and "/" in msg.media_mime:
        ext = "." + _SAFE_NAME.sub("", msg.media_mime.split("/", 1)[1])[:8].lower()
    return f"anh_{msg.tg_message_id}{ext or '.jpg'}"


async def _pick_media(
    uow: IUnitOfWork,
    thread_id: int,
    history: Sequence[Message],
    pending: Sequence[PendingTrigger] | None,
    limit: int,
    window_seconds: int,
) -> list[MediaPick]:
    """Which images this run should actually fetch, in priority order:

    1. an image on a trigger message itself ("@bot cái này là gì" + photo),
    2. an image on the message a trigger replies to — the dominant flow: someone
       posts a photo, chatter follows, then someone replies to *that* photo,
    3. otherwise an image posted *right around* the trigger — within
       ``window_seconds``. That is what picks up an album (Telegram sends it as
       one captioned message plus N bare image messages, all at the same instant)
       and the "posts a photo, then immediately asks" flow.

    Tier 3 is time-boxed on purpose: without it, every reply in a photo-heavy
    group would re-download the last N images even when the question is "2 + 2".

    Bounded by ``limit`` because every image costs real input tokens.
    """
    if limit <= 0:
        return []

    by_tg = {m.tg_message_id: m for m in history}
    trigger_ids = [p.tg_message_id for p in (pending or [])]
    picked: list[MediaPick] = []
    seen: set[int] = set()

    def take(msg: Message | None) -> None:
        if (
            msg is not None
            and msg.id not in seen
            and msg.media_file_id
            and is_image_media(msg.media_kind, msg.media_mime)
            and len(picked) < limit
        ):
            seen.add(msg.id)
            picked.append(
                MediaPick(
                    file_id=msg.media_file_id,
                    tg_message_id=msg.tg_message_id,
                    from_name=msg.from_name or "ai đó",
                    caption=msg.text,
                    file_name=_attachment_name(msg),
                )
            )

    for tg_id in reversed(trigger_ids):  # newest trigger first
        take(by_tg.get(tg_id))
    for tg_id in reversed(trigger_ids):
        trigger = by_tg.get(tg_id)
        if trigger is not None and trigger.reply_to_tg_message_id:
            target = by_tg.get(trigger.reply_to_tg_message_id)
            if target is None:
                # Older than the prompt window but still in retention.
                target = await uow.messages.get_by_tg_id(thread_id, trigger.reply_to_tg_message_id)
            take(target)
    # Tier 3, only near the trigger in time. No trigger at all (a scheduled run)
    # means no question to illustrate, so nothing is attached.
    trigger_times = [by_tg[t].sent_at for t in trigger_ids if t in by_tg]
    if trigger_times:
        newest = max(trigger_times)
        window = timedelta(seconds=window_seconds)
        for msg in reversed(history):
            if abs(newest - msg.sent_at) <= window:
                take(msg)
    return picked


async def _download_media(
    settings: Settings, sender: TelegramSender, picks: Sequence[MediaPick], dest_dir: Path
) -> list[tuple[Path, PromptAttachment]]:
    """Fetch each pick. A failed download is skipped, never fatal — the run still
    answers, just without that image."""
    out: list[tuple[Path, PromptAttachment]] = []
    max_bytes = settings.media_max_download_mb * 1024 * 1024
    for pick in picks:
        path = dest_dir / pick.file_name
        if not await sender.download_media(pick.file_id, path, max_bytes):
            continue
        out.append(
            (
                path,
                PromptAttachment(
                    file_name=pick.file_name,
                    sender=pick.from_name,
                    tg_message_id=pick.tg_message_id,
                    caption=pick.caption,
                ),
            )
        )
    return out


async def _run_reply(
    settings: Settings,
    uow_factory: UowFactory,
    ctx: TriggerContext,
    *,
    pending: list[PendingTrigger] | None,
    mark_after: int | None,
) -> None:
    """Run ``agy``; ``agy`` itself sends every message via the ``send_chat_message``
    MCP tool (see src/delivery/mcp/server.py). This function never sends to Telegram
    except the initial typing action — if ``agy`` fails or calls no tool, the thread
    stays silent by design.

    ``pending`` (message path) is the list of unanswered triggers this run must
    reply to, one reply each; ``mark_after`` is the ``messages.id`` to advance the
    thread's high-water mark to once agy has actually sent something. Both are
    ``None`` on the scheduled path (the instruction plays the trigger)."""
    sender = TelegramSender(settings.telegram_bot_token)
    agy = AgyClient(settings)
    session_key: str | None = None
    default_reply_to = pending[0].tg_message_id if pending else ctx.trigger_tg_id
    # Newest trigger — what we react to, and what the user is watching.
    ack_tg_id = pending[-1].tg_message_id if pending else ctx.trigger_tg_id
    media_dir: Path | None = None
    reacted = False
    try:
        async with uow_factory() as uow:
            rows = await uow.messages.last_n(ctx.thread_id, settings.context_message_limit)
            history = [HistoryLine(r.from_name or "ai đó", r.text, r.is_bot_self) for r in rows]
            memory = await uow.memories.get(ctx.chat_id)
            memory_text = memory.content if memory else None
            media_rows = await _pick_media(
                uow,
                ctx.thread_id,
                rows,
                pending,
                settings.media_max_per_run,
                settings.media_context_seconds,
            )
            session_key = await uow.sessions.mint(
                ctx.thread_id,
                settings.session_ttl_seconds,
                trigger_tg_message_id=default_reply_to,
            )
            await uow.commit()

        await sender.send_typing(ctx.chat_id, ctx.topic_id)
        if settings.reaction_ack and ack_tg_id is not None:
            await sender.set_reaction(ctx.chat_id, ack_tg_id, settings.reaction_ack)
            reacted = True

        attachments: list[PromptAttachment] = []
        attachment_paths: list[Path] = []
        if media_rows:
            media_dir = Path(tempfile.mkdtemp(prefix="media-"))
            downloaded = await _download_media(settings, sender, media_rows, media_dir)
            attachment_paths = [p for p, _ in downloaded]
            attachments = [a for _, a in downloaded]
            logger.info(
                "media attached thread_id=%s picked=%s downloaded=%s",
                ctx.thread_id,
                len(media_rows),
                len(attachments),
            )

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
            context_limit=settings.context_message_limit,
            extra_tools=bool(settings.composio_api_key),
            pending=pending,
            attachments=attachments,
        )

        result = await agy.run(prompt, attachments=attachment_paths)

        async with uow_factory() as uow:
            active = await uow.sessions.get_active(session_key)
            sent = active.sent_count if active is not None else 0
        n_pending = len(pending) if pending else 0
        if not result.ok:
            logger.error(
                "agy failed; thread left silent thread_id=%s pending=%s",
                ctx.thread_id,
                n_pending,
            )
        elif sent == 0:
            logger.warning(
                "agy called no send tool; thread left silent thread_id=%s pending=%s",
                ctx.thread_id,
                n_pending,
            )
        else:
            logger.info(
                "reply sent thread_id=%s pending=%s sent=%s",
                ctx.thread_id,
                n_pending,
                sent,
            )
            # The reply itself is now the acknowledgement, so drop the 👀. On a
            # failure it deliberately stays: it is the only trace that the bot
            # saw the message at all (the thread stays silent by design).
            if reacted and ack_tg_id is not None:
                await sender.set_reaction(ctx.chat_id, ack_tg_id, None)
            if mark_after is not None:
                async with uow_factory() as uow:
                    await uow.threads.bump_last_answered(ctx.thread_id, mark_after)
                    await uow.commit()
    finally:
        if media_dir is not None:
            shutil.rmtree(media_dir, ignore_errors=True)
        if session_key is not None:
            try:
                async with uow_factory() as uow:
                    await uow.sessions.delete(session_key)
                    await uow.commit()
            except Exception:
                logger.warning("session cleanup failed", extra={"session_key": session_key})
        await sender.close()
