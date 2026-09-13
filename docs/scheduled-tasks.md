# Scheduled tasks

Gen Đần can be told, in plain language, to do something later or on a schedule —
*"ê mỗi sáng 9h nhắc cả nhóm đi họp"*, *"5 phút nữa nhắc tao uống nước"*.

`agy` turns that into a **`schedule_task`** MCP call. `beat` checks for due rows
every minute and, when one fires, enqueues a normal reply turn for that thread
whose "message" is the saved instruction — so the bot acts on it exactly as if
someone had just sent it, with full history and memory in context.

A scheduled reply is a plain message in the thread, not a Telegram reply to
anything.

**No "chờ tí" placeholder.** On the message path the prompt tells `agy` to send a
quick "ok để tui lo" before a slow job so nobody is left staring at silence. A
scheduled run has no such audience — the timer fired, nobody just typed — so the
prompt *forbids* the placeholder there and asks for one message with the result.
It has to say so explicitly rather than just stay quiet about it: the
`send_chat_message` tool description itself advertises the "chờ tao xíu… xong
rồi, đây" flow on every run, and the model follows it unless told not to.

## Rules

- Times are interpreted in **Asia/Ho_Chi_Minh**. The prompt tells `agy` the current
  local time so it can resolve "tomorrow", "in 10 minutes", etc.
- One-off (`when`, an ISO-8601 datetime) **or** recurring (`cron`, a 5-field
  expression) — exactly one, never both.
- A run missed by more than ~10 minutes (the stack was down) is **skipped**, not
  replayed. Recurring rows roll forward to the next occurrence; one-offs disable.
- Scheduling is **per thread** (DM, group, or forum topic). The agent lists, edits
  and cancels only its own thread's tasks.

## Tools

`schedule_task`, `list_scheduled_tasks`, `update_scheduled_task`,
`cancel_scheduled_task` — all keyed by the same opaque `session_key` as every other
tool. See [mcp-tools.md](mcp-tools.md).
