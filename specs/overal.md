# A Khôn — Telegram bot powered by the `agy` CLI

## Goal

A Telegram bot ("A Khôn") that listens in groups, forum topics and DMs, stores every
message it can see into PostgreSQL, and answers when it is mentioned / replied to /
DM'd. Answers are produced by shelling out to the locally-authed **Antigravity CLI
(`agy`)**. The bot keeps one long-lived *memory* note per chat thread, which `agy`
updates itself through an MCP tool we host.

One `make dev` brings up the whole stack.

---

## Verified facts (tested on this machine, 2026-09-07 — do not re-litigate)

These were proven empirically before this spec was written. Build on them.

| # | Fact |
|---|---|
| 1 | `agy` is **Google's Antigravity CLI**. Config root is `~/.gemini/antigravity-cli/`. |
| 2 | `install.sh` ships **linux_arm64** and linux_amd64 builds. Correct flag is `-d <dir>`; `--skip-aliases` / `--skip-path` do **not** exist. |
| 3 | **Auth in a Linux container works.** Mounting *only* `~/.gemini/antigravity-cli/antigravity-oauth-token` into the container is enough — `agy` bootstraps every other file itself. (Upstream issue #479 claims otherwise; it does not reproduce on this version.) |
| 4 | `agy` **rewrites** that token file on refresh, so the mount must be **read-write** and owned by the user running `agy`. |
| 5 | Print mode: `agy -p "<prompt>" --output-format json`. The prompt must be an **argv argument** — stdin is not read, and `-p` followed by another flag is rejected. |
| 6 | JSON shape: `{"conversation_id","status","response","duration_seconds","num_turns","usage":{...},"denied_actions":[...]}`. `status` is `SUCCESS` or `CANCELED`. |
| 7 | 3 concurrent `agy -p` processes all succeeded. Concurrency is safe. |
| 8 | MCP config is written to **`~/.gemini/config/mcp_config.json`** — *not* under `antigravity-cli/`. Registered with `agy mcp add --type http <name> <url>`. |
| 9 | In headless mode tools are **auto-denied** unless allowlisted or `--dangerously-skip-permissions` is passed. Allow-rule syntax is `mcp(<server>/<tool>)`, wildcards allowed (`mcp(memory/*)`); `mcp(memory)` silently fails to match. We ship `--dangerously-skip-permissions` (see Decisions). |
| 10 | End-to-end proven: containerised `agy` called an MCP HTTP tool with a `session_key` argument and the server received it. |
| 11 | **Token cost per call ≈ 27k input tokens** once an MCP server is registered (~5.3k without). That is `agy`'s own coding-agent system prompt, paid on every reply. |
| 12 | Latency: **~2.0s** on `gemini-3.8-flash-low`, 3–8s on `claude-opus-4-6-thinking`. |
| 13 | The A Khôn persona (Vietnamese, sarcastic, youth slang, no markdown) holds correctly on `gemini-3.8-flash-low` via prompt-prepend. Sample reply: *"Nó bận chạy deadline với người yêu trong tưởng tượng đấy, mày rủ làm gì cho mệt xác."* |
| 14 | MCP Python SDK **v2** uses `MCPServer` (not `FastMCP`) and **rejects non-localhost requests by default** — `TransportSecuritySettings(allowed_hosts=[...])` is mandatory for container-to-container calls, or you get `421 Misdirected Request`. |
| 15 | Telegram privacy mode: a bot in a group sees only mentions/commands/replies **unless** it is a group **admin** (admins always receive everything) or privacy is disabled in BotFather *and the bot re-added to every group*. |

---

## Decisions (chosen by the user — do not change)

| Decision | Choice | Consequence |
|---|---|---|
| Bot powers | **Full agent**, `--dangerously-skip-permissions` | Any group member's text becomes commands `agy` executes. Accepted risk. Mitigated only by running `agy` as an unprivileged user with a scrubbed environment. |
| Model | `gemini-3.8-flash-low` | ~2s replies, cheapest. |
| Persona | **A Khôn** — Vietnamese, humorous/sarcastic, friend tone, youth slang | Prompt-prepend, no markdown, chat-length replies. |
| Access control | **Fully open, no allowlist, no rate limit** | Anyone who adds the bot can spend the account's quota. Accepted. |
| Workspace | **Fresh empty temp dir per call**, deleted after | Nothing `agy` writes to disk survives. The memory row is the **only** durable thing it can affect. Do **not** build per-thread volumes or workspace cleanup — dead code. |
| `agy` placement | **Inside the worker image**, invoked as a subprocess | One image. Runs as OS user `agy`, own `HOME`, env scrubbed. |
| Queue | **Celery + Redis**; reply work in the worker, not the poller | Workers scale freely; per-thread ordering via a Redis lock. |
| History fetch | **At task execution time**, not enqueue time | A reply sees messages that arrived while it queued. |

---

## Architecture

One image, four commands (per the `backend_python` KB: `delivery → application → domain ← infrastructure`).

```
                    ┌──────────────┐
   Telegram  ──────▶│  bot         │  long-polling poller, 1 replica only
   (long poll)      │  (delivery)  │  stores every message, enqueues triggers
                    └──────┬───────┘
                           │ celery task (message_id only)
                    ┌──────▼───────┐        ┌──────────┐
                    │  redis       │◀──────▶│  worker  │  N replicas, scalable
                    │  broker+lock │        │  + agy   │  fetches last 20 at run time
                    └──────▲───────┘        └────┬─────┘
                           │                     │ subprocess (user: agy)
                    ┌──────┴───────┐             │ HTTP
                    │  beat        │        ┌────▼─────┐
                    │  1 replica   │        │  mcp     │  MCPServer, /mcp
                    │  hourly job  │        │(delivery)│  update_thread_memory
                    └──────────────┘        └────┬─────┘
                                                 │
                    ┌────────────────────────────▼───────┐
                    │           postgres                  │
                    └─────────────────────────────────────┘
```

### Services (all from the same image except `db` / `redis`)

| Service | Command | Replicas |
|---|---|---|
| `db` | `postgres:16-alpine` | 1 |
| `redis` | `redis:7-alpine` | 1 |
| `bot` | `python -m src.entrypoints.bot` | **exactly 1** (only one process may long-poll a token) |
| `worker` | `celery -A src.infrastructure.celery_app:celery_app worker -Q replies,maintenance -c 4` | N (scalable) |
| `beat` | `celery -A src.infrastructure.celery_app:celery_app beat` | **exactly 1** (more = duplicate schedules) |
| `mcp` | `python -m src.entrypoints.mcp` (uvicorn) | 1 |

---

## Data model

A **chat thread** is the unit of everything: `(chat_id, message_thread_id)`. Private
chat and plain group → `message_thread_id = 0`. Forum topic → the topic id.

```
chat_threads
  id            bigserial pk
  chat_id       bigint      not null
  topic_id      bigint      not null default 0
  chat_type     text        not null      -- private | group | supergroup | channel
  title         text        null
  created_at, updated_at
  unique (chat_id, topic_id)

messages
  id            bigserial pk
  thread_id     bigint      fk -> chat_threads.id  on delete cascade
  tg_message_id bigint      not null
  from_user_id  bigint      null
  from_username text        null
  from_name     text        null
  is_bot_self   boolean     not null default false   -- A Khôn's own replies
  text          text        not null                 -- caption, or "[ảnh]" placeholder
  sent_at       timestamptz not null
  created_at    timestamptz not null
  unique (thread_id, tg_message_id)
  index (thread_id, sent_at desc)

thread_memories
  thread_id     bigint      pk fk -> chat_threads.id on delete cascade
  content       text        not null
  updated_at    timestamptz not null

agy_sessions                              -- binds an agy run to one thread
  session_key   text        pk            -- opaque, secrets.token_urlsafe(24)
  thread_id     bigint      fk -> chat_threads.id on delete cascade
  expires_at    timestamptz not null
  index (expires_at)
```

**Why `agy_sessions` exists.** MCP servers are registered globally, and
`agy mcp add --header` is static — there is no way to tell the MCP server which
chat thread a given `agy` run belongs to via transport. And we must not trust the
model to echo a raw `chat_id` back correctly (or honestly). So each run gets a
short-lived opaque key, embedded in the prompt, required by the tool, and mapped to
a thread server-side.

---

## Reply flow

1. `bot` receives an update. Upserts the thread, inserts the message.
2. Is it a trigger? — private chat, **or** a `mention` / `text_mention` entity matching
   the bot's own username, **or** `reply_to_message.from.id == bot.id`.
3. If yes → `generate_reply.delay(message_id)` on queue `replies`. Nothing else.
4. `worker` picks it up. Acquires Redis lock `lock:thread:{thread_id}`
   (`blocking_timeout=30`); on failure `self.retry(countdown=3, max_retries=20)`.
5. Under the lock: `send_chat_action(typing)`, fetch **last 20 messages** for the
   thread (chronological, includes A Khôn's own replies) + the memory row.
6. Mint an `agy_sessions` row (TTL 10 min). Build the prompt. Run `agy` as user `agy`
   in a fresh `mkdtemp()` cwd.
7. Split the response on 4096 chars, `send_message(..., reply_to_message_id=<trigger>)`.
8. Store each sent chunk back into `messages` with `is_bot_self = true`.
9. Delete the session row. `shutil.rmtree()` the temp dir. Release the lock.

### On duplicate triggers

Three mentions in one thread produce **three replies**, each answered in order, each
tied to its own question by `reply_to_message_id`. We deliberately do **not** coalesce
or drop: two different people asking two different things both deserve answers.
Because the 20-message window is fetched at execution time and includes A Khôn's own
replies, the 2nd and 3rd runs *see* that the earlier question was already answered and
can respond accordingly. This is the self-correcting cheap option; no extra machinery.

---

## Prompt sent to `agy`

Single argv string. Roughly:

```
Bạn là A Khôn — một con bot trong nhóm chat Telegram.
Luôn trả lời bằng tiếng Việt. Giọng hài hước, châm biếm, xưng hô như bạn bè
(tao/mày, ông/tui tuỳ ngữ cảnh), rành slang giới trẻ. Trả lời NGẮN như đang chat —
không markdown, không bullet, không tiêu đề. Nếu người ta viết tiếng khác thì
trả lời bằng tiếng đó.

session_key = <opaque>
Nếu bạn học được điều gì đáng nhớ lâu dài về nhóm này hoặc về người trong nhóm,
hãy gọi tool `update_thread_memory` với session_key ở trên và TOÀN BỘ nội dung
memory mới (nó ghi đè, không nối thêm).

Ghi nhớ hiện tại về nhóm này:
<memory content, or "(chưa có gì)">

Lịch sử chat gần đây (cũ → mới):
[tên]: nội dung
...

Trả lời tin nhắn cuối cùng.
```

Persona text lives in one constant so it can be tuned without hunting.

---

## MCP server

`MCPServer("memory")`, streamable HTTP, mounted at `/mcp` on a Starlette/FastAPI app.

- **`update_thread_memory(session_key: str, memory: str) -> str`** — looks up an
  unexpired session, **replaces** `thread_memories.content`, returns a short ack.
  Unknown/expired key → `ToolError`.
- `GET /health` via `@mcp.custom_route` for the compose healthcheck.
- `TransportSecuritySettings(allowed_hosts=["mcp", "mcp:*", "localhost", "localhost:*"])`
  — **mandatory**, otherwise every call from the worker container returns 421.
- Host app owns the lifespan and must `async with mcp.session_manager.run():`.

Registered into `agy` at container start:
`agy mcp add --type http memory http://mcp:8000/mcp`

---

## Cleanup (beat, hourly)

Task `cleanup_old_messages` on queue `maintenance`:

- Per thread, delete every message outside the newest 20 (one `DELETE ... USING`
  with `row_number() over (partition by thread_id order by sent_at desc, id desc)`).
- Delete `agy_sessions` where `expires_at < now()`.
- Log counts.

**Consequence to be aware of:** with only 20 messages retained, `thread_memories` is
the *only* long-term state. Anything the bot should remember beyond ~20 messages must
have been written to memory before cleanup runs. This is by design.

---

## Auth mechanism (the part the user asked about)

`make dev` bind-mounts the host token straight in:

```yaml
worker:
  volumes:
    - ${HOME}/.gemini/antigravity-cli/antigravity-oauth-token:/home/agy/.gemini/antigravity-cli/antigravity-oauth-token:rw
```

- **Read-write, not `:ro`** — `agy` rewrites the file on refresh (fact #4).
- The image `chown`s `/home/agy` to the `agy` user; the mounted file must be readable
  and writable by that uid. `make dev` prints a clear error if the host file is missing.
- Nothing else from `~/.gemini` is copied — the token alone is sufficient (fact #3).

---

## Operational prerequisite (must be in the README)

**Make A Khôn an admin in every group you add it to.** Admin bots receive all
messages; non-admin bots with default privacy see only mentions and replies, so the
message history would be full of holes. The alternative — `/setprivacy → Disable` in
BotFather — additionally requires **removing and re-adding the bot to every existing
group** before it takes effect.

---

## Stack

Python 3.13 · uv · SQLAlchemy 2.0 async + asyncpg · Alembic · Celery 5 + Redis ·
python-telegram-bot v22 (long polling) · `mcp` SDK v2 · Starlette/FastAPI + uvicorn ·
`dependency-injector` · Dynaconf + `@dataclass` Settings · ruff + ty · Docker Compose.

## Steps

| Step | File |
|---|---|
| 0 | `step-0-scaffold.md` — repo skeleton, uv, tooling, Makefile |
| 1 | `step-1-docker.md` — Dockerfile with `agy`, compose, entrypoints, auth mount |
| 2 | `step-2-config-logging.md` — Settings, logger, DI container |
| 3 | `step-3-database.md` — entities, UoW, repositories, Alembic |
| 4 | `step-4-telegram-ingest.md` — poller, thread resolution, message storage |
| 5 | `step-5-trigger-enqueue.md` — trigger detection, Celery task dispatch |
| 6 | `step-6-agy-worker.md` — Redis lock, prompt builder, `agy` subprocess, reply send |
| 7 | `step-7-mcp-memory.md` — MCP server + `update_thread_memory` |
| 8 | `step-8-cleanup-beat.md` — hourly retention job |
| 9 | `step-9-wire-verify.md` — `make dev`, README, end-to-end smoke test |
