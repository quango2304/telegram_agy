"""The Celery application.

Broker/backend come from Settings. Task behaviour config, routes and the beat
schedule are added in steps 6 and 8.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from src.infrastructure.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "telegram_agy",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=["src.infrastructure.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,  # a killed worker re-delivers the task
    worker_prefetch_multiplier=1,  # don't hoard tasks behind a lock wait
    task_track_started=True,
    timezone="UTC",
    # Backstop: if agy (or a wedged composio child) escapes the in-process
    # timeout, the soft limit raises inside the task so its finally-blocks run,
    # and the hard limit SIGKILLs the pool child.
    task_soft_time_limit=_settings.agy_timeout_seconds + 120,
    task_time_limit=_settings.agy_timeout_seconds + 180,
    task_routes={
        "tasks.generate_reply": {"queue": "replies"},
        "tasks.run_scheduled": {"queue": "replies"},
        "tasks.dispatch_scheduled": {"queue": "maintenance"},
        "tasks.cleanup_old_messages": {"queue": "maintenance"},
    },
    beat_schedule={
        "cleanup-hourly": {
            "task": "tasks.cleanup_old_messages",
            "schedule": crontab(minute=0),  # top of every hour, UTC
            "options": {"queue": "maintenance"},
        },
        "dispatch-scheduled-every-minute": {
            "task": "tasks.dispatch_scheduled",
            "schedule": crontab(),  # every minute
            "options": {"queue": "maintenance"},
        },
    },
)
