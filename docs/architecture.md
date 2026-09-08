# Architecture

Gen Đần listens in Telegram groups, forum topics and DMs, stores every message it
can see into PostgreSQL, and answers when it is **mentioned**, **replied to**, or
**DM'd**. Answers are produced by shelling out to the locally-authed **Antigravity
CLI (`agy`)**, which sends its replies itself through an MCP server this project
hosts.

```
   Telegram ──long poll──▶  bot (delivery, 1 replica)
                            stores every message, enqueues triggers
                                   │ celery task (message row id only)
                                   ▼
                            redis  ◀────────▶  worker (N replicas) + agy
                            broker + lock            fetches history at run time
                                   ▲                     │ subprocess (user: agy)
                                   │                     │ HTTP tool calls
                            beat (1 replica)        mcp (delivery, holds bot token)
                            hourly cleanup          send_chat_message ──▶ Telegram
                            + scheduled dispatch    edit_chat_message
                                   │                update_memory
                                   │                search_history
                                   ▼                schedule_* tools
                            ┌─────────────────────────────────┐
                            │            postgres              │
                            └─────────────────────────────────┘
```

## The chat thread

The unit of everything is a **chat thread** = `(chat_id, topic_id)`. A DM or plain
group is `topic_id = 0`; a forum topic is the topic id. Replies, scheduled tasks
and the per-thread lock are all keyed by it.

Chat **memory** and **history search** are scoped one level wider — per `chat_id`,
shared across every forum topic of a group.

## Services

All run from one image except `db` / `redis`.

| Service | Command | Replicas |
|---|---|---|
| `db` | `postgres:16-alpine` | 1 |
| `redis` | `redis:7-alpine` | 1 |
| `bot` | `python -m src.entrypoints.bot` | **exactly 1** |
| `worker` | `celery … worker -Q replies,maintenance -c 4` | N |
| `beat` | `celery … beat` (hourly cleanup + per-minute scheduled dispatch) | **exactly 1** |
| `mcp` | `python -m src.entrypoints.mcp` (uvicorn; holds `TELEGRAM_BOT_TOKEN` to send) | 1 |

- **`worker` scales freely.** Per-thread ordering comes from a Redis lock
  (`lock:thread:{id}`), not from worker count: replies for one thread run strictly
  one at a time and in order, while different threads run fully in parallel.
- **`bot` must stay at 1** — only one process may long-poll a token.
- **`beat` must stay at 1** — a second scheduler doubles every scheduled job.

## Layering

`src/` follows a domain / application / infrastructure / delivery split:

- `domain/` — entities and repository Protocols. No I/O.
- `application/` — use cases (currently message ingest).
- `infrastructure/` — SQLAlchemy repositories, the Unit of Work, Celery tasks, the
  `agy` subprocess client, the prompt builder, the Telegram sender.
- `delivery/` — the PTB handlers and the MCP server.

### One rule worth knowing

`SqlAlchemyUnitOfWork.__aexit__` always rolls back before closing, and a rollback
**expires every ORM instance the session loaded**. So anything needed after an
`async with uow_factory() as uow:` block must be copied out to primitives *inside*
the block. Reading a lazily-loaded attribute afterwards raises
`DetachedInstanceError`.

## Stack

Python 3.13 · uv · SQLAlchemy 2.0 async + asyncpg · Alembic · Celery 5 + Redis ·
python-telegram-bot v22 (long polling) · `mcp` SDK v2 (`MCPServer`) ·
Starlette + uvicorn · `dependency-injector` · Dynaconf + frozen `@dataclass`
Settings · ruff + ty · Docker Compose.
