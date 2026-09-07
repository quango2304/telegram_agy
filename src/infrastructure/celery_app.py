"""The Celery application.

Broker/backend come from Settings. Task behaviour config, routes and the beat
schedule are added in steps 6 and 8.
"""

from __future__ import annotations

from celery import Celery

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
    task_routes={
        "tasks.generate_reply": {"queue": "replies"},
        "tasks.cleanup_old_messages": {"queue": "maintenance"},
    },
)
