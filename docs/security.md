# Security — stated plainly

The bot is **open to anyone** (no allowlist, no rate limit) and runs `agy` with
`--dangerously-skip-permissions`. **Anyone who can message it can cause commands to
run inside the `worker` container**, as the unprivileged `agy` user.

This is a known, accepted trade-off for a bot in trusted group chats. Do not expose
it beyond those, and do not publish the MCP port (`8010`) anywhere but local dev.

## Why the flag can't just be removed

`agy`'s own permission system cannot contain this: the flag overrides the `deny`
list, and dropping the flag aborts any reply that uses a non-allowlisted command.
Containment has to happen at the OS layer instead.

## What actually contains it

- **A scrubbed environment.** `SCRUBBED_ENV` is built from scratch — not
  `os.environ` minus keys — so `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`,
  `CELERY_BROKER_URL`, `REDIS_URL` and every `POSTGRES_*` are unreachable from the
  `agy` process.
- **An unprivileged user.** `agy` runs as uid 1001 via `gosu`, never root.
- **An unreadable app tree.** The prod Dockerfile does `chown -R root:root /app &&
  chmod -R go-rwx /app`, so uid 1001 cannot read the source. `.env` is
  `.dockerignore`d; secrets arrive via compose `env_file` → container env → and are
  stripped again by `SCRUBBED_ENV`.
  Docker Desktop ignores `chmod` on bind mounts, so **dev** shadows the file with
  `- /dev/null:/app/.env:ro` instead.
- **A fresh empty workdir per call**, deleted afterwards. Attached images live here
  and die with it.
- **Bootstrap calls use `env -i`** (`agy_clean` in `docker/entrypoint.sh`) so no
  same-uid process carries secrets in `/proc/<pid>/environ`.
- **The MCP server** is only reachable on the compose network and every tool is
  guarded by an opaque, single-thread-scoped `session_key` that expires in 10
  minutes.

## What cannot be contained by the OS

`agy` must be able to read its own credentials — the OAuth token in
`/home/agy/.gemini/` and `~/.composio/user_data.json`. Hiding those would need a
separate runner container (rejected as too heavy for this project).

Instead they are protected **in words**: `SECRET_GUARD` (`src/shared/persona.py`)
forbids reading, printing or sending any credential, env var or secret file, and it
is repeated **three times** — top of prompt, bottom of prompt, and as an
`AGENTS.md` written into every run's workdir, which `agy` loads as a hard workspace
rule.

This is probabilistic, not a boundary. It has held against several "I'm the admin /
just for debugging" exfiltration attempts in testing. If you change the wording,
keep it identical in all three places and re-test.

**The container is not a security boundary.**

## Prompt injection

Message text from a group is untrusted input. The `session_key` design is what
keeps an injected instruction from redirecting output: the key resolves to exactly
one thread, so the model cannot be talked into posting somewhere else, editing
another chat's messages, or reading another chat's memory.
