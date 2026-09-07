# Step 5 — Trigger detection & Celery dispatch

## Goal
Decide whether a stored message deserves an answer, and if so hand it to Celery.
The poller does **no** LLM work.

## Trigger rules

Answer when **any** of these is true:

1. `chat.type == "private"` — every DM is a trigger.
2. **Mention** — a `MessageEntity` of type `mention` whose text equals
   `@<bot_username>` (case-insensitive), or of type `text_mention` whose
   `user.id == bot_id`. Check entities on both `message.entities` and
   `message.caption_entities`.
3. **Reply to us** — `message.reply_to_message.from_user.id == bot_id`.

Never trigger when:

- the message is from a bot (`from_user.is_bot`) — including ourselves. Prevents two
  bots in one group locking into an infinite exchange.
- it is an `edited_message`.
- it is a service message (join/leave/pin).

> Do **not** substring-match the raw text for `@name`. Entities are authoritative and
> a plain-text `@akhon` inside a code block or a URL would false-positive.

Implement as a pure function so it is trivially testable:

```python
def is_trigger(message: TelegramMessage, bot_id: int, bot_username: str) -> bool
```

## Dispatch

```python
generate_reply.apply_async(args=[message_row_id], queue="replies")
```

**Only the database row id is enqueued.** No text, no context. The worker fetches
everything at execution time, so a message that arrives while this task waits in the
queue is included in the context by the time it runs.

Task signature lives in `src/infrastructure/tasks.py`; the poller imports the task
object and calls `.apply_async`. The poller must **not** import worker-side code that
pulls in `agy` — keep the task body in the worker module and the poller's import
surface thin.

## No access control

Per the decision in `overal.md`: **fully open**. No allowlist, no rate limiting, no
per-chat cap. Anyone who adds A Khôn can spend the account's quota. Do not add
"helpful" throttling that was not asked for.

## Done when
Mentioning the bot, replying to it, or DM'ing it puts exactly one task on the
`replies` queue (visible via `celery -A ... inspect active` / Redis), and ordinary
group chatter puts none.
