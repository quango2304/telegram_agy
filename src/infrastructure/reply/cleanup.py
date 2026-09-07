"""Hourly retention job — keep only the newest N messages per thread.

One ``DELETE ... USING`` with a window function covers every thread at once (do
not loop in Python). ``chat_threads`` and ``chat_memories`` are never touched —
the per-chat memory row is the only long-term state.
"""

from __future__ import annotations

import asyncio

from src.infrastructure.config import get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork
from src.shared.logger import get_logger

logger = get_logger(__name__)


def run_cleanup() -> None:
    asyncio.run(_run())


async def _run() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            deleted = await uow.messages.prune_to_last_n(settings.context_message_limit)
            expired = await uow.sessions.purge_expired()
            await uow.commit()
        logger.info(
            "cleanup done",
            extra={"messages_deleted": deleted, "sessions_purged": expired},
        )
    finally:
        await engine.dispose()
