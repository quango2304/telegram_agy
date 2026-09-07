# Gen Đần — a Telegram bot powered by the `agy` CLI

Gen Đần ("gen đần" ≈ "the dim generation") listens in Telegram groups, forum
topics and DMs, stores every message it can see into PostgreSQL, and answers when
it is **mentioned**, **replied to**, or **DM'd**. Answers are produced by shelling
out to the locally-authed **Antigravity CLI (`agy`)**, which sends its replies
itself through an MCP tool this project hosts. `agy` also keeps one long-lived
*memory* note per chat (shared across a group's forum topics) and can schedule
per-thread tasks — same MCP server.

One `make dev` brings up the whole stack.

---

## 1. Prerequisites

- **Docker Desktop** running.
- **`agy` installed and signed in on the host.** Install it, run `agy` once, sign
  in, and confirm `agy -p "hi"` prints a reply. That writes an OAuth token to
  `~/.gemini/antigravity-cli/antigravity-oauth-token`.
- The stack **bind-mounts that token file** into the `worker` container
  **read-write** — `agy` rewrites it in place when the token refreshes, so a
  read-only mount eventually breaks auth. Nothing else from `~/.gemini` is copied;
  the token file alone is enough (`agy` bootstraps the rest inside the container).
- The container's `agy` user is uid `1001`; the entrypoint `chown`s `/home/agy`
  on start so the bind-mounted token is readable/writable regardless of the host
  file's owner. (Docker Desktop for macOS remaps mount ownership anyway; a plain
  Linux host does not, which is why the `chown` exists.)

`make dev` / `make prod` refuse to start with a clear message if the token file
is missing.

## 2. Setup

```bash
cp .env.example .env
```

Then edit `.env` and set:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_BOT_USERNAME` | the bot's username **without `@`** (e.g. `gendan_agy_bot`). Used to strip the bot's handle out of the question text. If left blank the bot still works; the handle just isn't stripped. |

Everything else in `.env` has a working default for local Docker.

```bash
make dev          # build + start db, redis, mcp, bot, worker, beat
```

Secrets never enter git: `.env` is gitignored and the `agy` token stays on the
host, mounted at runtime.

## 3. The Telegram step people get wrong

**Make Gen Đần an admin in every group you add it to.** A non-admin bot with the
default *privacy mode* only receives messages that mention or reply to it, so the
stored history would be full of holes and replies would lack context. Admins
always receive everything.

The alternative is BotFather → `/setprivacy` → **Disable** — but that **also
requires removing and re-adding the bot to every existing group** before it takes
effect. Disabling privacy *before* creating a group avoids the re-add.

## 4. Deploy to a VPS (production)

`docker-compose.dev.yml` bind-mounts the source and hot-reloads with `watchfiles`
— great locally, wrong for a server. `Dockerfile` + `docker-compose.yml` are the
production pair: a multi-stage image with the code baked in, no dev tooling, no
watcher, `restart: always`, and **nothing published on the host**.

On the box (Docker + a signed-in `agy` so the OAuth token exists at
`~/.gemini/antigravity-cli/antigravity-oauth-token`):

```bash
git clone https://github.com/quango2304/telegram_agy && cd telegram_agy
cp .env.example .env         # then fill TELEGRAM_BOT_TOKEN + TELEGRAM_BOT_USERNAME
make prod                    # docker compose -f docker-compose.yml up -d --build
make prod-logs               # tail
```

Update = `git pull && make prod` (rebuilds, recreates, re-runs migrations from the
`bot` entrypoint). Inspect with `docker compose -f docker-compose.yml exec …` —
`db`, `redis` and `mcp` are only reachable inside the compose network.

The entrypoint `chown`s `/home/agy` on start, so the root-owned bind-mounted token
is readable by the unprivileged `agy` user on native Linux (Docker Desktop remaps
this automatically; a plain Linux host does not).

## 5. Scaling

```bash
docker compose -f docker-compose.yml up -d --scale worker=3
```

- **`worker` scales freely.** Per-thread ordering is enforced by a Redis lock
  (`lock:thread:{id}`), not by worker count: replies for one chat thread run
  strictly one at a time and in order, while different threads run fully in
  parallel across all workers.
- **`bot` must stay at 1** — only one process may long-poll a token.
- **`beat` must stay at 1** — a second scheduler doubles every scheduled job.

## 6. What it costs

- **~27k input tokens per reply.** That is `agy`'s own coding-agent system prompt,
  inflated from ~5k once an MCP server is registered, and paid on every reply. It
  is inherent to driving `agy`, not a bug — but it is the number to watch if quota
  runs out.
- **~2s latency** on `gemini-3.8-flash-low` (3–8s on the thinking models).
- Change the model with `AGY_MODEL` in `.env`; `agy models` lists the options.

## 7. Retention

An hourly job keeps only the **newest 20 messages per chat thread**
(`CONTEXT_MESSAGE_LIMIT`). `chat_threads` rows and `chat_memories` are **never**
deleted. Memory is **per chat** — one row for a private chat, one shared across
every forum topic of a group — and is the only long-term recall: anything Gen
Đần should remember beyond ~20 messages must have been written via the
`update_memory` MCP tool before cleanup runs. This is by design.

## 8. Security — stated plainly

The bot is **open to anyone** (no allowlist, no rate limit) and runs `agy` with
`--dangerously-skip-permissions`. **Anyone who can message it can cause commands
to run inside the `worker` container**, as the unprivileged `agy` user.

Mitigations, and their limits:

- `agy` runs as a non-root user (`agy`) in a **built-from-scratch environment** —
  `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `CELERY_BROKER_URL`, `REDIS_URL` and every
  `POSTGRES_*` are unreachable from that process.
- Each call runs in a fresh empty temp dir that is deleted afterwards.
- The MCP server is only reachable on the compose network and is guarded by an
  opaque, single-thread-scoped `session_key` that expires in 10 minutes.

**The container is not a security boundary.** Do not expose this beyond trusted
groups, and do not publish the MCP port (`8010`) anywhere but local dev.

## 9. Troubleshooting

| Symptom | Cause |
|---|---|
| `agy` result has `"status":"CANCELED"` and a non-empty `denied_actions` | `--dangerously-skip-permissions` is missing from the `agy` invocation. |
| MCP calls fail with `421 Misdirected Request` | `TransportSecuritySettings.allowed_hosts` doesn't list `mcp` / `mcp:*`. |
| MCP server raises `RuntimeError: Task group is not initialized` | The Starlette app isn't running `mcp.session_manager.run()` in its lifespan. Use the app returned by `streamable_http_app()` directly (it wires its own lifespan). |
| `bot` container exits immediately | `TELEGRAM_BOT_TOKEN` is empty or wrong in `.env`. |
| Trigger handled (worker logs `reply sent` / `agy_ok`) but nothing appears in the chat | `agy` answered in plain text instead of calling `send_chat_message`. Look for `agy called no send tool` in the worker log. By design there is no fallback; tighten the persona/prompt if the model does this often. |
| `send_chat_message` fails with a Telegram error | the `mcp` service is missing `TELEGRAM_BOT_TOKEN`, or the bot was removed from the chat. |
| DB errors on first boot | Run `make migrate` once; migrations normally auto-apply from the `bot` entrypoint, but a hand-run is the fix if that service didn't come up. |

Useful commands:

```bash
make logs          # tail everything
make ps            # service status
make shell         # bash in the worker container
make migrate       # alembic upgrade head
make down          # stop the stack
```

---

## Architecture

```
   Telegram ──long poll──▶  bot (delivery, 1 replica)
                            stores every message, enqueues triggers
                                   │ celery task (message row id only)
                                   ▼
                            redis  ◀────────▶  worker (N replicas) + agy
                            broker + lock            fetches last 20 at run time
                                   ▲                     │ subprocess (user: agy)
                                   │                     │ HTTP tool calls
                            beat (1 replica)        mcp (delivery, holds the bot token)
                            hourly cleanup          send_chat_message  ──▶ Telegram
                            + scheduled dispatch    update_memory (per chat)
                                   │                schedule_* tools
                                   ▼                     │
                            ┌─────────────────────────────────┐
                            │            postgres              │
                            └─────────────────────────────────┘
```

The unit of everything is a **chat thread** = `(chat_id, topic_id)`. A DM or plain
group is `topic_id = 0`; a forum topic is the topic id.

### How a reply happens

The worker does **not** send anything to Telegram. It takes the per-thread lock,
builds the prompt, and runs `agy`; `agy` then calls the **`send_chat_message`**
MCP tool — once, or several times for a "chờ tí… xong rồi, đây" flow — and the
`mcp` service (which holds the bot token) does the actual sending, replies the
first message to the trigger, and stores each one as `is_bot_self`. A runaway
guard caps a single run at 12 messages.

Consequence, chosen deliberately: **if `agy` fails or never calls the tool, the
thread stays silent.** There is no worker-side fallback message.

### Services (all from one image except `db` / `redis`)

| Service | Command | Replicas |
|---|---|---|
| `db` | `postgres:16-alpine` | 1 |
| `redis` | `redis:7-alpine` | 1 |
| `bot` | `python -m src.entrypoints.bot` | **exactly 1** |
| `worker` | `celery … worker -Q replies,maintenance -c 4` | N |
| `beat` | `celery … beat` (hourly cleanup + per-minute scheduled dispatch) | **exactly 1** |
| `mcp` | `python -m src.entrypoints.mcp` (uvicorn; sends chat messages, so it needs `TELEGRAM_BOT_TOKEN`) | 1 |

## Scheduled tasks

Gen Đần can be told, in plain language, to do something later or on a schedule —
*"ê mỗi sáng 9h nhắc cả nhóm đi họp"*, *"5 phút nữa nhắc tao uống nước"*. `agy`
turns that into a call to the **`schedule_task`** MCP tool; `beat` checks for due
rows every minute and, when one fires, enqueues a normal reply turn for that
thread whose "message" is the saved instruction — so the bot acts on it exactly
as if someone had just sent it, with full history and memory in context. A
scheduled reply is a plain message in the thread (not a Telegram reply to
anything).

- Times are interpreted in **Asia/Ho_Chi_Minh**.
- One-off (`when`, an ISO-8601 datetime) **or** recurring (`cron`, a 5-field
  expression) — exactly one. The prompt tells `agy` the current time so it can
  resolve "tomorrow", "in 10 minutes", etc.
- A run missed by more than ~10 minutes (the stack was down) is **skipped**, not
  replayed; recurring rows roll forward to the next occurrence, one-offs disable.
- Scheduling is **per thread** (DM, group, or forum topic). The agent lists,
  edits and cancels its own thread's tasks with **`list_scheduled_tasks`**,
  **`update_scheduled_task`** and **`cancel_scheduled_task`** (all keyed by the
  same opaque `session_key` as the memory tool).

## Composio tools + sending files (optional)

Set **`COMPOSIO_API_KEY`** to a `uak_…` Composio *user* API key (run `composio
login` once anywhere and copy `~/.composio/user_data.json`'s `api_key`; `ak_` /
`ck_` keys do **not** work) and the image logs the `composio` CLI in at start.
`agy` then shells out to `composio search|execute` **only when a message needs
it** — so ordinary replies still cost ~14k tokens, not the ~40k an always-on
Composio MCP would add. Whatever apps you connected in that Composio account
(Google Drive, Gmail, …) are available.

To hand a file back to the user, `agy` writes it into the shared **`/outbox`**
volume and calls the **`send_chat_file`** MCP tool (paths outside `/outbox` are
rejected; files over `TELEGRAM_MAX_FILE_MB` = 50 are rejected; the file is
deleted after sending). Typical flow: *"find X in my Drive and send it"* →
`composio search` → `composio execute GOOGLEDRIVE_DOWNLOAD_FILE` → `curl` the
returned URL into `/outbox` → `send_chat_file`.

Notes:

- **Use `AGY_MODEL=gemini-3.8-flash-medium` (or `-high`)** when Composio is on.
  `-low` is fast but drops multi-step tool tasks too often. Expect 40–180 s for a
  search-download-send reply.
- `agy` runs `--dangerously-skip-permissions`, so it will invoke any connected
  Composio tool with no approval. Anyone who can message the bot can act on those
  accounts — only enable on a private / trusted deployment.
- A `composio` run that wedges is killed by process-group SIGKILL plus a Celery
  hard time limit; the reply fails, the worker slot frees, nothing hangs.

## Stack

Python 3.13 · uv · SQLAlchemy 2.0 async + asyncpg · Alembic · Celery 5 + Redis ·
python-telegram-bot v22 (long polling) · `mcp` SDK v2 (`MCPServer`) ·
Starlette + uvicorn · `dependency-injector` · Dynaconf + frozen `@dataclass`
Settings · ruff + ty · Docker Compose.

See `specs/` for the full design rationale.
