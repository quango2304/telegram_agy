# Running and troubleshooting

## Commands

```bash
make dev        # build + start the whole dev stack (hot-reload)
make logs       # tail everything
make ps         # service status
make shell      # bash in the worker container
make migrate    # alembic upgrade head
make verify     # ruff check + format check + ty + compileall
make fmt        # auto-fix and format
make down       # stop the stack
```

Production equivalents: `make prod`, `make prod-logs`, `make prod-ps`,
`make prod-down`. See the deploy section of the root README.

## Migrations

Migrations auto-apply from the `bot` entrypoint on start. `make migrate` is the
hand-run fix if that service didn't come up.

```bash
make gen-migration msg="describe_change"
```

Two things `--autogenerate` cannot express and will try to **drop** if you let it:
the `messages.tsv` generated column and its GIN index. Those are hand-written in
`a1c7e2b40f19`. After any schema change, run autogenerate once and confirm it
produces an empty migration — then delete that file.

> If you generate a migration and delete the file, make sure it was never applied
> first. A container restart runs `alembic upgrade head` on its own, and a deleted
> but applied revision leaves the DB pointing at a revision that no longer exists
> (`Can't locate revision identified by …`). Fix by re-stamping:
> `UPDATE alembic_version SET version_num='<real head>';`

## Making a change take effect (dev)

Two traps, both of which look like "my change did nothing":

- **Only `bot` and `mcp` hot-reload.** They run under `watchfiles`; `worker` and
  `beat` do not. After editing anything the worker runs — `runner.py`,
  `prompt_builder.py`, `persona.py`, `agy_client_impl.py`, the repositories — run
  `docker compose -f docker-compose.dev.yml restart worker`.
- **Config changes need containers recreated**, not restarted:
  `docker compose ... up -d`. `docker restart` does **not** re-read `env_file`.

When a test result looks like the old behaviour, check these before debugging the
code.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `agy` result has `"status":"CANCELED"` and a non-empty `denied_actions` | `--dangerously-skip-permissions` is missing from the `agy` invocation. |
| MCP calls fail with `421 Misdirected Request` | `TransportSecuritySettings.allowed_hosts` doesn't list `mcp` / `mcp:*`. |
| MCP server raises `RuntimeError: Task group is not initialized` | The Starlette app isn't running `mcp.session_manager.run()` in its lifespan. Use the app returned by `streamable_http_app()` directly. |
| `bot` container exits immediately | `TELEGRAM_BOT_TOKEN` empty/wrong, or a migration error — check the log. |
| Trigger handled (`reply sent` / `agy_ok`) but nothing in the chat | `agy` answered in plain text instead of calling `send_chat_message`. Look for `agy called no send tool`. By design there is no fallback; tighten the persona/prompt. |
| `send_chat_message` fails with a Telegram error | The `mcp` service is missing `TELEGRAM_BOT_TOKEN`, or the bot was removed from the chat. |
| A file or message is sent 2–4 times | Something after the send raised, so MCP marked the tool failed and `agy` retried. Post-effect code must never raise — see [mcp-tools.md](mcp-tools.md). |
| `DetachedInstanceError` | An ORM attribute was read after its UoW block closed. Copy primitives out inside the block — see [architecture.md](architecture.md). |
| Bot ignores messages in a group | It only answers on @-mention or a reply to itself. Not a bug — see [replies.md](replies.md). |
| DB errors on first boot | Run `make migrate` once. |

## Log lines worth grepping

| Line | Meaning |
|---|---|
| `reply enqueued` | `bot` saw a trigger and queued it |
| `media attached … picked=N downloaded=M` | images selected / fetched for this run |
| `chat message sent … ids=[…]` | `mcp` actually sent to Telegram |
| `chat message edited` | an `edit_chat_message` landed |
| `reply sent … pending=N sent=M` | run finished; N triggers answered with M actions |
| `agy called no send tool` / `agy failed` | run finished but the thread stayed silent |
| `trigger already covered` | a sibling task correctly no-op'd after batching |
| `cleanup done … messages_deleted=N` | hourly retention sweep |
