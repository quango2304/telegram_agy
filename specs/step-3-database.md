# Step 3 — Entities, Unit of Work, repositories, migrations

## Goal
The four tables from `overal.md`, async SQLAlchemy access behind repository
Protocols, and an Alembic chain that auto-applies on startup.

## Entities — `src/domain/entities/`

`chat_thread.py`, `message.py`, `thread_memory.py`, `agy_session.py`.
SQLAlchemy 2.0 declarative with `Mapped[...]` / `mapped_column`. These ORM classes
*are* the domain models (KB `01`).

Exact columns, keys and indexes: see the Data model section of `overal.md`. Points
that matter:

- `chat_threads`: `unique (chat_id, topic_id)`; `topic_id` **not null default 0** —
  private chats and non-forum groups use `0`, never `NULL`, so the unique index works.
- `messages`: `unique (thread_id, tg_message_id)` so a redelivered Telegram update is
  a no-op; `index (thread_id, sent_at desc)` is what the 20-message fetch and the
  cleanup job both ride on.
- `messages.text` is **not null** — non-text messages are stored as a placeholder
  (step 4), never as NULL.
- `agy_sessions.session_key` is the PK, opaque, `secrets.token_urlsafe(24)`.
- All FKs `ON DELETE CASCADE` from `chat_threads`.

## Interfaces — `src/domain/interfaces/`

`I` prefix, no `Protocol` suffix (KB `rules.md` §3):

```python
class IThreadRepository(Protocol):
    async def get_or_create(self, chat_id: int, topic_id: int,
                            chat_type: str, title: str | None) -> ChatThread: ...

class IMessageRepository(Protocol):
    async def add(self, msg: Message) -> Message: ...              # idempotent upsert
    async def last_n(self, thread_id: int, n: int) -> list[Message]: ...  # chronological
    async def prune_to_last_n(self, n: int) -> int: ...            # returns rows deleted

class IMemoryRepository(Protocol):
    async def get(self, thread_id: int) -> ThreadMemory | None: ...
    async def upsert(self, thread_id: int, content: str) -> ThreadMemory: ...

class ISessionRepository(Protocol):
    async def mint(self, thread_id: int, ttl_seconds: int) -> str: ...
    async def resolve(self, session_key: str) -> int | None: ...   # None if expired/unknown
    async def delete(self, session_key: str) -> None: ...
    async def purge_expired(self) -> int: ...
```

## UoW & repos — `src/infrastructure/db/`

`engine.py` (async engine + `async_sessionmaker`), `uow.py`, and
`repositories/*_repository_impl.py` per KB `05-database-uow.md`. `Impl` classes
explicitly inherit their Protocol.

Implementation notes:

- `add` → `insert(...).on_conflict_do_nothing(index_elements=["thread_id","tg_message_id"])`.
- `last_n` → `ORDER BY sent_at DESC, id DESC LIMIT n`, then **reverse in Python** so
  callers always get oldest→newest.
- `get_or_create` → `on_conflict_do_update` on `(chat_id, topic_id)` refreshing
  `title` and `updated_at`, `RETURNING *`. Do not do select-then-insert: two workers
  can race.
- `prune_to_last_n` → one statement:

  ```sql
  DELETE FROM messages m USING (
    SELECT id, row_number() OVER (
      PARTITION BY thread_id ORDER BY sent_at DESC, id DESC) AS rn
    FROM messages
  ) r
  WHERE m.id = r.id AND r.rn > :n
  ```

## Migrations — `migrations/`

Async-aware Alembic per KB `07-migrations.md`. `env.py` imports every entity with
`# noqa: F401` so autogenerate sees the tables. Migrations use the **sync**
`ALEMBIC_DATABASE_URL` (psycopg2); the app uses asyncpg.

One initial revision creating all four tables. Auto-upgrade to `head` at startup of
the `bot` entrypoint only (one place, so services don't race on the migration lock).

## Done when
`make migrate` creates the four tables, and `make verify` passes.
