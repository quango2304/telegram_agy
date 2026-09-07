# Step 6 — The reply worker: lock, prompt, `agy`, send

The core of the system.

## `src/infrastructure/celery_app.py`

```python
celery_app = Celery("telegram_agy",
                    broker=settings.celery_broker_url,
                    backend=settings.celery_result_backend)
celery_app.conf.update(
    task_acks_late=True,               # a killed worker re-delivers the task
    worker_prefetch_multiplier=1,      # don't hoard tasks behind a lock wait
    task_track_started=True,
    timezone="UTC",
    task_routes={
        "tasks.generate_reply":       {"queue": "replies"},
        "tasks.cleanup_old_messages": {"queue": "maintenance"},
    },
)
```

## The per-thread lock

This is how "N workers, but strictly one reply in flight per chat thread" is achieved.
Celery has no per-task concurrency cap; a Redis lock gives it to us for free.

```python
@celery_app.task(bind=True, name="tasks.generate_reply",
                 max_retries=20, default_retry_delay=3)
def generate_reply(self, message_id: int):
    thread_id = lookup_thread_id(message_id)
    lock = redis_client.lock(f"lock:thread:{thread_id}",
                             timeout=settings.agy_timeout_seconds + 60,
                             blocking=False)
    if not lock.acquire(blocking=False):
        raise self.retry(countdown=3)     # another reply for this thread is running
    try:
        _run_reply(message_id, thread_id)
    finally:
        try: lock.release()
        except LockError: pass            # lock expired; don't mask the real error
```

Different threads run fully in parallel across all workers. `docker compose up
--scale worker=5` just works.

## `_run_reply` — the sequence

1. Load the trigger message + its thread.
2. `send_chat_action(chat_id, ChatAction.TYPING, message_thread_id=topic_id or None)`.
3. `messages.last_n(thread_id, settings.context_message_limit)` — **fetched now**,
   not at enqueue time. Includes A Khôn's own earlier replies.
4. `memories.get(thread_id)`.
5. `session_key = sessions.mint(thread_id, settings.session_ttl_seconds)`.
6. Build the prompt (below).
7. Run `agy` (below).
8. Split the response into ≤4096-char chunks, on a newline where possible.
9. Send each chunk. First chunk carries
   `reply_to_message_id=<trigger tg_message_id>` and `message_thread_id=topic_id or None`.
10. Store every sent chunk into `messages` with `is_bot_self=True` — otherwise the
    20-message window is A Khôn talking into a void.
11. `sessions.delete(session_key)`; `shutil.rmtree(workdir)`.

## Prompt builder — `src/infrastructure/agy/prompt_builder.py`

Assemble in this order:

```
{PERSONA_PROMPT}

session_key = {session_key}
Nếu bạn học được điều gì đáng nhớ lâu dài về nhóm này hoặc người trong nhóm,
hãy gọi tool `update_thread_memory` với session_key ở trên và TOÀN BỘ nội dung
memory mới (tool này GHI ĐÈ, không nối thêm).

Ghi nhớ hiện tại về nhóm này:
{memory or "(chưa có gì)"}

Lịch sử chat gần đây (cũ → mới):
[{name}]: {text}
...

Trả lời tin nhắn cuối cùng.
```

A Khôn's own lines are labelled `[A Khôn]`. Truncate any single message to ~2000
chars. Strip the bot's `@username` from the trigger text so it doesn't read as part of
the question.

## `src/infrastructure/agy/agy_client_impl.py`

```python
# Derive the uid — do NOT hardcode 1001 and duplicate the Dockerfile's useradd.
_pw = pwd.getpwnam(settings.agy_user)

workdir = tempfile.mkdtemp(prefix="agy-")          # fresh & empty per call
os.chown(workdir, _pw.pw_uid, _pw.pw_gid)

cmd = ["gosu", settings.agy_user, settings.agy_binary,
       "-p", prompt,
       "--model", settings.agy_model,
       "--output-format", "json",
       "--print-timeout", f"{settings.agy_timeout_seconds}s",
       "--dangerously-skip-permissions"]

proc = await asyncio.create_subprocess_exec(
    *cmd, cwd=workdir, env=SCRUBBED_ENV,
    stdout=PIPE, stderr=PIPE)
```

**Hard requirements — these were established by testing, get them exactly right:**

- The prompt is an **argv argument**. `agy` does not read stdin, and `-p` immediately
  followed by another flag is rejected with a confusing error.
- `--print-timeout` takes a duration string (`"180s"`), not a bare number.
- Parse stdout as JSON: `{"conversation_id","status","response","duration_seconds",
  "num_turns","usage",...}`. Treat `status != "SUCCESS"` **or** a non-empty
  `denied_actions` as a failure and log the whole object.
- Also wrap in `asyncio.wait_for(..., timeout=agy_timeout_seconds + 30)` — belt and
  braces if the process wedges.
- Always `shutil.rmtree(workdir, ignore_errors=True)` in a `finally`.
- **The worker container must run as root** — it needs to `gosu` down to `agy`. Do not
  add a `USER` directive to `Dockerfile.dev`; the privilege drop happens per-call, in
  the subprocess.
- **Cap the total prompt at 60 000 chars.** It is a single argv entry: 20 messages ×
  2 000 + persona + memory can otherwise add up. Trim oldest messages first, then
  truncate the memory block. (`ARG_MAX` is far higher, so this is about keeping token
  cost predictable, not about failing.)
- `sessions.delete(session_key)` and `rmtree` both belong in the **`finally`**, not
  only on the happy path. A crashed reply that skips the delete is survivable — the
  key expires in 10 min and `purge_expired` mops it up — but leaking it is pointless.

### `SCRUBBED_ENV` — the isolation that was promised

`agy` runs with `--dangerously-skip-permissions`, so anything a group member types can
become a shell command. Pass a **built-from-scratch** environment, not
`os.environ.copy()` minus a few keys:

```python
SCRUBBED_ENV = {
    "HOME": "/home/agy",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "USER": "agy",
    "LANG": "C.UTF-8",
    "TERM": "dumb",
}
```

`DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `CELERY_BROKER_URL`, `REDIS_URL` and every
`POSTGRES_*` must not be reachable from that process.

## Failure handling

- `agy` fails / times out / returns empty → reply once, in character, e.g.
  *"Ê khoan, não tao đứng hình tí. Nhắn lại phát nữa đi."* Log the full JSON. Do not
  retry the LLM call — a retry costs another ~27k input tokens and usually fails the
  same way.
- Telegram send fails → let Celery retry (`acks_late` means the task redelivers).
- Lock contention is **not** an error; it is the normal path.

## Cost note
Every reply costs ~27k input tokens before the user's text (the MCP registration
inflates `agy`'s system prompt from ~5k). That is inherent to driving `agy`, not a
bug — but it is the number to watch if quota runs out.

## Done when
Mentioning the bot in a group yields a Vietnamese, in-character reply within a few
seconds; the reply is stored with `is_bot_self=True`; and two rapid mentions in the
same thread are answered in order while a mention in a *different* chat is answered
concurrently.
