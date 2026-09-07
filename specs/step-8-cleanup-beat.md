# Step 8 — Hourly cleanup (Celery Beat)

## Goal
Keep only the newest 20 messages per chat thread. Runs hourly on the `maintenance`
queue, scheduled by the single `beat` service.

## Schedule — `src/infrastructure/celery_app.py`

```python
celery_app.conf.beat_schedule = {
    "cleanup-hourly": {
        "task": "tasks.cleanup_old_messages",
        "schedule": crontab(minute=0),       # top of every hour, UTC
        "options": {"queue": "maintenance"},
    },
}
```

`beat` is pinned to **one replica** in compose. Scaling it would run the job N times
an hour.

## Task

```python
@celery_app.task(name="tasks.cleanup_old_messages")
def cleanup_old_messages():
    deleted   = messages.prune_to_last_n(settings.context_message_limit)
    expired   = sessions.purge_expired()
    logger.info("cleanup done", extra={"messages_deleted": deleted,
                                       "sessions_purged": expired})
```

One SQL statement for the prune (see step 3) — do **not** loop over threads in
Python. It is a single `DELETE ... USING` with a window function, and it covers every
thread at once.

## Ordering & safety

- The prune orders by `sent_at DESC, id DESC`. `id` is the tiebreaker because several
  messages can share a second.
- It can run while a reply is in flight. Worst case the in-flight reply used a message
  that is deleted a moment later — harmless, the reply is already generated.
- `chat_threads` rows are **never** deleted, even when empty. They are cheap, and the
  memory row hangs off them.
- `thread_memories` is **never** touched by cleanup. It is the only durable state.

## Consequence worth stating in the README
Retaining 20 messages means the memory row is the whole long-term recall. Anything
A Khôn should remember past ~20 messages must have been written to memory before
cleanup runs. That is the intended design, not an oversight.

## Done when
Insert 50 messages in one thread, run
`celery -A src.infrastructure.celery_app:celery_app call tasks.cleanup_old_messages`,
and exactly the newest 20 remain — with the thread's memory row untouched.
