# MCP tools

`agy` cannot talk to the user directly. Text it writes outside a tool call is seen
by nobody — the only way to reach the chat is the MCP server this project hosts
(`src/delivery/mcp/server.py`, server name **`memory`**).

## The session key

Every tool takes an opaque **`session_key`**, minted per run and injected into the
prompt. It resolves to exactly one chat thread and expires after
`SESSION_TTL_SECONDS` (default 600). This is what stops a prompt-injected
instruction from making the bot post into a different chat: the key is the only
addressing mechanism, and it only ever points at the thread being handled.

The server is reachable only on the compose network. Never publish port 8010
anywhere but local dev.

## Tools

| Tool | What it does |
|---|---|
| `send_chat_message(session_key, text, reply_to_tg_message_id=0)` | Send a message to the current thread. Callable several times per run. Long text is auto-chunked. Returns the Telegram message ids it sent. |
| `edit_chat_message(session_key, tg_message_id, text)` | Correct a message the bot itself already sent in this thread (wrong number, wrong name). **Not** for turning a "chờ tí" placeholder into the answer — that is a new message. |
| `send_chat_file(session_key, file_path, caption="")` | Send a file. Paths must be under `/outbox`; files over `TELEGRAM_MAX_FILE_MB` (50) are rejected; the file is deleted after sending. |
| `update_memory(session_key, memory)` | Overwrite the per-chat long-term note. Send the full merged text — it replaces, never appends. Soft budget 3000 chars (prompt), hard cap 8000 (tool). |
| `search_history(session_key, query, limit=0)` | Keyword-search older messages of this chat, past the prompt window. |
| `schedule_task(session_key, instruction, when="", cron="", requested_by="")` | Schedule a one-off or recurring task for this thread. |
| `list_scheduled_tasks(session_key)` | List this thread's scheduled tasks with ids. |
| `update_scheduled_task(session_key, task_id, …)` | Edit instruction, timing, or enabled state. |
| `cancel_scheduled_task(session_key, task_id)` | Delete a scheduled task. |

## The post-send rule

**Anything that runs after a tool has already had an external effect must not
raise.** MCP reports a raised exception as a failed tool call, and `agy` retries —
re-sending a message or a file that already went out.

This bit us once for real: a `logger.info(..., extra={"name": ...})` used `name`,
a reserved `LogRecord` field, so the *success* log threw after the file was sent
and `agy` sent it up to four times. Post-effect code is wrapped in
`contextlib.suppress(Exception)` for this reason. Keep it that way.
