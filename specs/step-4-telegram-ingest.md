# Step 4 — Telegram poller & message ingestion

## Goal
The `bot` service long-polls Telegram and stores every message it can see. No
answering yet.

## `src/entrypoints/bot.py`

```python
app = (Application.builder()
       .token(settings.telegram_bot_token)
       .concurrent_updates(True)      # a slow handler must not stall the poll loop
       .build())
app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, on_message))
app.run_polling(allowed_updates=["message", "edited_message"], drop_pending_updates=True)
```

Before `run_polling`, run Alembic `upgrade head` (this is the one place migrations
are applied — see step 3). Cache `bot.username` and `bot.id` from `get_me()` at
startup; step 5 needs both on every message.

`drop_pending_updates=True` so a restart doesn't replay and re-answer a backlog.

## Thread resolution — the key mapping

```python
chat_id  = update.effective_chat.id
topic_id = message.message_thread_id or 0
```

- private chat → `topic_id = 0`
- plain group / supergroup → `topic_id = 0`
- forum topic → the topic id
- forum "General" topic → Telegram omits `message_thread_id`, so it lands on `0`.
  That is correct and intended.

Only set `topic_id` from `message_thread_id` when `chat.is_forum` is true —
`message_thread_id` is *also* populated on ordinary replies in some clients, which
would otherwise shatter one group into many threads.

`title` = `chat.title` for groups, `chat.full_name`/username for private.

## Message text

`messages.text` is not-null. Resolve in this order:

1. `message.text`
2. `message.caption`
3. a Vietnamese placeholder by kind — `[ảnh]`, `[video]`, `[sticker: 😂]`,
   `[voice]`, `[file: report.pdf]`, `[vị trí]`, `[…]`

Media is **not** downloaded or sent to `agy` in v1 — text and captions only.

Author display name: `from_user.full_name`, falling back to `@username`, falling back
to `"ai đó"`. Store `from_user_id`, `from_username`, `from_name` separately so the
prompt builder can format however it likes later.

`sent_at` = `message.date` (already timezone-aware UTC from PTB).

## Handler flow

```
on_message:
  thread = threads.get_or_create(chat_id, topic_id, chat_type, title)
  messages.add(Message(thread_id=thread.id, tg_message_id=..., ..., is_bot_self=False))
  commit
  → step 5 decides whether to enqueue a reply
```

Wrap in `IngestHandler` under `src/application/ingest/` — one class, one method per
operation (KB `rules.md` §5). The PTB handler in `src/delivery/telegram/` is an entry
point only: parse the update into a command dataclass, call the handler, return.

Edited messages update `text` for the existing `(thread_id, tg_message_id)` row and do
**not** trigger a reply.

## Errors
A failure storing one message must not kill the poller. Log with `chat_id`/
`message_id` and continue.

## Done when
Sending messages in a DM and in a group (bot made admin) produces rows in
`messages`, with a forum topic landing on a distinct `chat_threads` row.
