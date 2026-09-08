# Working on this repo

Gen Đần — a Telegram bot that answers by shelling out to the `agy` CLI. Read
[docs/architecture.md](docs/architecture.md) first; it explains the service split
and the one non-obvious rule about the Unit of Work.

## Keep the docs current — this is the important one

`docs/` describes **how the project behaves today**, not how it was built. It is
the primary reference for both humans and agents, and a stale doc is worse than no
doc.

**When you change behaviour, update the matching doc in the same change.** Not
afterwards, not "if there's time".

| If you touch… | Update |
|---|---|
| trigger rules, batching, the lock, reactions, edits | [docs/replies.md](docs/replies.md) |
| image selection/fetching, media columns | [docs/media.md](docs/media.md) |
| prompt window, retention, search, chat memory | [docs/memory-and-history.md](docs/memory-and-history.md) |
| any MCP tool signature or behaviour | [docs/mcp-tools.md](docs/mcp-tools.md) |
| scheduling | [docs/scheduled-tasks.md](docs/scheduled-tasks.md) |
| any setting in `config.py` | [docs/configuration.md](docs/configuration.md) **and** `.env.example` |
| the sandbox, `SCRUBBED_ENV`, `SECRET_GUARD` | [docs/security.md](docs/security.md) |
| `/outbox`, `send_chat_file`, Composio | [docs/composio-and-files.md](docs/composio-and-files.md) |
| services, layering, the stack | [docs/architecture.md](docs/architecture.md) |
| new failure modes or log lines | [docs/operations.md](docs/operations.md) |

Add a new `docs/*.md` for a genuinely new feature area and link it from the root
README's index. Keep the root README short — intro, docs index, deploy. Details
belong in `docs/`.

When you learn something the hard way (a failure mode, a library gotcha, a
non-obvious constraint), write it into the relevant doc with the *reason*, not just
the fix. Several sections here exist because a bug shipped twice.

## Before you say it's done

```bash
make verify     # ruff check + format check + ty + compileall
```

Migrations: `--autogenerate` cannot express the `messages.tsv` generated column or
its GIN index, and will try to drop them. Hand-write those, then run autogenerate
once to confirm it produces an empty migration. See
[docs/operations.md](docs/operations.md).

Config changes need `docker compose ... up -d`, not `docker restart`.

## House rules

- **Never propagate an exception after a tool has already had an external effect.**
  MCP treats a raised error as a failed call and `agy` retries — re-sending a
  message or file that already went out. Wrap post-effect code in
  `contextlib.suppress(Exception)`. This bug shipped once (a reserved `LogRecord`
  field name in a success log sent a file four times).
- **Copy primitives out of a UoW block before it closes.** `__aexit__` always rolls
  back, which expires every ORM instance; reading an attribute afterwards raises
  `DetachedInstanceError`.
- **A failed run leaves the thread silent, on purpose.** Don't add a fallback
  message without asking — it's an explicit product decision.
- **The persona and `SECRET_GUARD` live in one file** (`src/shared/persona.py`).
  `SECRET_GUARD` is repeated verbatim in three places; keep the wording identical
  and re-test after editing.
- Secrets stay in `.env` (gitignored) and the `agy` OAuth token stays on the host.
  **The repo is public — never commit either.**
- Prompt changes are behavioural changes. Re-test a plain reply and a multi-step
  reply, not just the new path.

## Testing

There is no automated test suite yet. Verify live against the **test** bot
(`.env` → `TELEGRAM_BOT_USERNAME`), never the production one — two long-pollers on
the same token make the deployed bot go intermittently deaf.

`src/delivery/telegram/parsers.py`, `triggers.py`, `infrastructure/agy/prompt_builder.py`,
`infrastructure/schedule/cron.py` and `shared/chunking.py` are pure and would be
the highest-value place to start a real test suite.

## Deploying

Only when explicitly asked. See the root README.
