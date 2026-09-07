"""Celery task signatures.

The poller imports :data:`generate_reply` and calls ``.apply_async`` — nothing
here may pull in ``agy`` at import time, so the heavy reply machinery is imported
lazily inside the task body (filled in step 6). ``cleanup_old_messages`` is
filled in step 8.
"""

from __future__ import annotations

from src.infrastructure.celery_app import celery_app
from src.shared.logger import get_logger

logger = get_logger(__name__)


# Retries here are the "another reply for this thread is already running" wait
# (Redis lock held). A single reply can legitimately run for minutes (agy +
# composio), so the budget must outlast one full run + the lock's TTL:
# 120 x 5s = 600s.
_LOCK_WAIT_RETRIES = 120


@celery_app.task(
    bind=True,
    name="tasks.generate_reply",
    max_retries=_LOCK_WAIT_RETRIES,
    default_retry_delay=5,
)
def generate_reply(self, message_id: int) -> None:  # noqa: ANN001
    from src.infrastructure.reply.runner import run_generate_reply

    run_generate_reply(self, message_id)


@celery_app.task(
    bind=True,
    name="tasks.run_scheduled",
    max_retries=_LOCK_WAIT_RETRIES,
    default_retry_delay=5,
)
def run_scheduled(self, scheduled_task_id: int) -> None:  # noqa: ANN001
    from src.infrastructure.reply.runner import run_scheduled_reply

    run_scheduled_reply(self, scheduled_task_id)


@celery_app.task(name="tasks.dispatch_scheduled")
def dispatch_scheduled() -> None:
    from src.infrastructure.reply.dispatch import run_dispatch_scheduled

    run_dispatch_scheduled()


@celery_app.task(name="tasks.cleanup_old_messages")
def cleanup_old_messages() -> None:
    from src.infrastructure.reply.cleanup import run_cleanup

    run_cleanup()
