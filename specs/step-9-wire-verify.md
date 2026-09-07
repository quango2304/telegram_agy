# Step 9 — Wire up, document, verify end to end

## Goal
`make dev` starts everything and the bot actually answers. Nothing left dangling.

## README.md

Must cover, in this order:

1. **Prerequisites** — Docker Desktop running; `agy` installed and signed in on the
   host (`agy -p "hi"` works). Note that the token at
   `~/.gemini/antigravity-cli/antigravity-oauth-token` is bind-mounted **read-write**
   into the worker, because `agy` rewrites it when refreshing.
2. **Setup** — `cp .env.example .env`, paste `TELEGRAM_BOT_TOKEN`, `make dev`.
3. **The Telegram step people get wrong** — *make A Khôn an admin in every group.*
   A non-admin bot with default privacy mode only sees messages that mention or reply
   to it, so stored history would be full of holes. The alternative
   (BotFather → `/setprivacy` → Disable) **also requires removing and re-adding the
   bot to every existing group**.
4. **Scaling** — `docker compose -f docker-compose.dev.yml up --scale worker=3`.
   Explain that `bot` and `beat` must stay at 1 (one long-poller per token; one
   scheduler or the hourly job multiplies), while `worker` scales freely because
   per-thread ordering is enforced by a Redis lock, not by worker count.
5. **What it costs** — ~27k input tokens per reply (that is `agy`'s own system
   prompt), ~2s latency on `gemini-3.8-flash-low`. Change the model with `AGY_MODEL`;
   `agy models` lists the options.
6. **Retention** — only the last 20 messages per thread survive the hourly cleanup.
   The per-thread memory row is the only long-term recall.
7. **Security, stated plainly** — the bot is open to anyone and runs `agy` with
   `--dangerously-skip-permissions`. Anyone who can message it can run commands
   inside the worker container as the unprivileged `agy` user. The environment is
   scrubbed so the DB password and bot token are out of reach, but the container is
   not a security boundary. Do not expose this beyond trusted groups.
8. **Troubleshooting** — the three failures that will actually happen:
   - `"status":"CANCELED"` + `denied_actions` → `--dangerously-skip-permissions` is
     missing from the `agy` invocation.
   - `421 Misdirected Request` from the MCP server → `TransportSecuritySettings.allowed_hosts`
     doesn't list `mcp` / `mcp:*`.
   - `RuntimeError: Task group is not initialized` → the Starlette lifespan isn't
     running `mcp.session_manager.run()`.

## Verification checklist

Run all of it before declaring done.

```bash
make verify                       # ruff check, ruff format --check, ty, compileall
make dev                          # all six services healthy
```

1. `docker compose ... run --rm worker gosu agy agy mcp list`
   → `memory  http  enabled  http://mcp:8000/mcp`
2. `curl localhost:8010/health` → 200
3. **DM the bot** → in-character Vietnamese reply within a few seconds.
4. **Group, bot as admin** → non-mention chatter appears in `messages` but gets no
   reply; a mention gets one; a reply to the bot gets one.
5. **Forum topic** → lands on its own `chat_threads` row, separate from the group's
   `topic_id = 0` row.
6. **Memory** → tell it something worth remembering ("gọi tao là sếp"), confirm a
   `thread_memories` row appears, then confirm a later message reflects it.
7. **Ordering** → fire three mentions at once in one thread: three replies, in order,
   each with `reply_to_message_id` pointing at its own question. Simultaneously
   mention it from a different chat and confirm that one is answered concurrently.
8. **Bot replies stored** → A Khôn's own messages present with `is_bot_self = true`.
9. **Cleanup** → `celery ... call tasks.cleanup_old_messages`, exactly 20 rows remain
   per thread, memory untouched.
10. **Scale** → `--scale worker=3`, repeat step 7, ordering still holds.
11. **Restart** → `make down && make dev`; the bot still authenticates (proving the
    token mount and refresh survive) and does not replay old updates.

## Definition of done
Every checklist item passes, `make verify` is clean, and the README tells a stranger
how to get from a fresh clone to a working bot.
