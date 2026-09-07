"""Every minute, turn due ``scheduled_tasks`` rows into reply work.

Roll-forward (or disable, for one-offs) is committed *before* the reply task is
enqueued: a crash in between drops one occurrence, which for a reminder bot beats
firing it twice.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from src.infrastructure.config import get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork
from src.infrastructure.schedule.cron import GRACE_SECONDS, next_cron_run
from src.shared.logger import get_logger

logger = get_logger(__name__)

_MAX_PER_TICK = 100


def run_dispatch_scheduled() -> None:
    asyncio.run(_run())


async def _run() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    now = datetime.now(UTC)
    to_enqueue: list[int] = []
    skipped = 0
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            due = await uow.schedules.due(now, _MAX_PER_TICK)
            for task in due:
                next_run = next_cron_run(task.cron, after=now) if task.cron else None
                late = (now - task.run_at).total_seconds()
                await uow.schedules.mark_ran(task.id, now, next_run)
                if late > GRACE_SECONDS:
                    skipped += 1
                    continue
                to_enqueue.append(task.id)
            await uow.commit()
    finally:
        await engine.dispose()

    for task_id in to_enqueue:
        from src.infrastructure.tasks import run_scheduled

        run_scheduled.apply_async(args=[task_id], queue="replies")

    if to_enqueue or skipped:
        logger.info(
            "scheduled dispatch",
            extra={"enqueued": len(to_enqueue), "skipped_late": skipped},
        )
