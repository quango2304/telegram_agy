# Replies: triggers, batching, acknowledgement

## When the bot answers

Trigger detection is pure (`src/delivery/telegram/triggers.py`). The bot answers
when:

- the chat is **private**, or
- the message **@-mentions** the bot (text mention counts), or
- the message **replies to one of the bot's own messages**.

It never answers another bot (two bots in a group must not lock into an infinite
exchange), an edited message, or a service message.

> A **sticker cannot carry a mention**, so in a group a sticker-only message only
> triggers when it replies to one of the bot's messages (in a private chat it
> always does). When it does trigger, the bot actually looks at the sticker —
> see [media.md](media.md).

> **Group setup:** make the bot an admin, or disable privacy mode in BotFather.
> A non-admin bot with default privacy only *receives* messages that mention or
> reply to it, so stored history would be full of holes.

Every message the bot can see is stored regardless of whether it triggers a reply.

## How a reply happens

The worker does **not** send anything to Telegram. It takes the per-thread lock,
builds the prompt, and runs `agy`; `agy` then calls the **`send_chat_message`**
MCP tool — once, or several times for a "chờ tí… xong rồi, đây" flow — and the
`mcp` service (which holds the bot token) does the actual sending, replies the
first message to the trigger, and stores each one as `is_bot_self`.

A runaway guard caps a single run at **12 send/edit actions**.

### Silence is deliberate

If `agy` fails, times out, or never calls a send tool, **the thread stays silent**.
There is no worker-side fallback message. Look for `agy called no send tool` or
`agy failed` in the worker log. This is a chosen trade-off: no fake reassurance.

## Batching rapid triggers

Several people can @-mention the bot while one reply is already running. Rather
than queueing a separate `agy` run per message:

1. Each trigger is flagged (`messages.is_trigger`) before its Celery task is
   enqueued.
2. The task that wins the Redis lock gathers **every unanswered trigger** for the
   thread — newest `_MAX_PENDING_PER_RUN` (**10**) — and answers them all in one
   `agy` run, one reply each, with the right `reply_to_tg_message_id` per message.
3. It then advances the thread's high-water mark (`chat_threads.last_answered_message_id`)
   past those messages, so the sibling tasks queued for them become no-ops
   (`trigger already covered`).

Sibling tasks that arrive while the lock is held retry every 5s, up to 120 times —
a budget that outlasts one full run plus the lock TTL.

## Acknowledgement: the 👀 reaction

When a run starts, the bot reacts with `REACTION_ACK` (default 👀) on the newest
pending trigger, and **clears it once the reply lands**.

If the run fails, the reaction **stays** — it is then the only trace that the bot
saw the message at all, given that a failed run leaves the thread silent.

Setting the reaction is best-effort: a group can restrict `available_reactions`,
and that must never fail a reply. Set `REACTION_ACK=` (empty) to disable.

A `send_chat_action(TYPING)` also fires, but Telegram expires it after ~5s, which
is why the reaction exists for long runs.

## Editing — for corrections only

`send_chat_message` returns the Telegram message ids it sent, so `agy` can call
**`edit_chat_message`** on one of its own messages afterwards.

This is **only** for setting the record straight: it said something wrong — a bad
number, the wrong name, the wrong person — and wants to fix it in place rather than
leave the mistake sitting in the chat.

It is deliberately **not** used to collapse the "chờ tí" placeholder into the
answer. A long-running turn sends the placeholder and then a **separate** message
with the result, so the chat keeps the natural shape of someone saying "hang on"
and then coming back. The prompt says this explicitly; if you loosen that wording,
expect the model to start editing placeholders again.

Only the bot's own messages, in the current thread, can be edited. Edits share the
same 12-action runaway budget as sends.
