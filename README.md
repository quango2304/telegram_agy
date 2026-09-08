# Gen Đần — a Telegram bot powered by the `agy` CLI

Gen Đần ("gen đần" ≈ "the dim generation") listens in Telegram groups, forum topics
and DMs, stores what it sees into PostgreSQL, and answers when it is **mentioned**,
**replied to**, or **DM'd**. Replies come from the locally-authed **Antigravity CLI
(`agy`)**, which sends them itself through an MCP server this project hosts.

It reads images people post, remembers things long-term per chat, searches its own
older history, and can schedule reminders — all as MCP tools `agy` chooses to call.

One `make dev` brings up the whole stack.

## Documentation

| Doc | What's in it |
|---|---|
| [architecture.md](docs/architecture.md) | Services, the chat-thread unit, layering, stack |
| [replies.md](docs/replies.md) | When it answers, batching, the 👀 ack, editing, why failures are silent |
| [media.md](docs/media.md) | Reading images (and why voice notes don't work) |
| [memory-and-history.md](docs/memory-and-history.md) | Prompt window vs retention vs chat memory, full-text search |
| [scheduled-tasks.md](docs/scheduled-tasks.md) | Reminders, one-off and cron |
| [mcp-tools.md](docs/mcp-tools.md) | Every tool `agy` can call, and the session-key model |
| [composio-and-files.md](docs/composio-and-files.md) | Sending files, optional Composio integration |
| [configuration.md](docs/configuration.md) | Every environment variable |
| [security.md](docs/security.md) | What contains `agy`, what doesn't, and the accepted risks |
| [operations.md](docs/operations.md) | Commands, migrations, troubleshooting, log lines |

Contributing or working on this with an agent? Start with [CLAUDE.md](CLAUDE.md).

## Prerequisites

- **Docker** running.
- **`agy` installed and signed in on the host.** Install it, run `agy` once, sign
  in, and confirm `agy -p "hi"` prints a reply. That writes an OAuth token to
  `~/.gemini/antigravity-cli/antigravity-oauth-token`.
- The stack **bind-mounts that token file** into the `worker` container
  **read-write** — `agy` rewrites it in place on refresh, so a read-only mount
  eventually breaks auth. The token file alone is enough; `agy` bootstraps the rest
  inside the container.

`make dev` / `make prod` refuse to start with a clear message if the token is
missing.

## Setup

```bash
cp .env.example .env
```

Fill in `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather) → `/newbot`)
and `TELEGRAM_BOT_USERNAME` (without the `@`). Everything else has a working local
default — see [configuration.md](docs/configuration.md).

```bash
make dev          # build + start db, redis, mcp, bot, worker, beat
```

Secrets never enter git: `.env` is gitignored and the `agy` token stays on the host.

**Make Gen Đần an admin in every group you add it to**, or disable privacy mode in
BotFather — otherwise it only receives messages that already mention it and its
history is full of holes. Details and the re-add caveat: [replies.md](docs/replies.md).

## Deploy to a VPS

`docker-compose.dev.yml` bind-mounts the source and hot-reloads — great locally,
wrong for a server. `Dockerfile` + `docker-compose.yml` are the production pair: a
multi-stage image with the code baked in, no dev tooling, no watcher,
`restart: always`, and **nothing published on the host**.

On the box (Docker + a signed-in `agy`, so the OAuth token exists):

```bash
git clone https://github.com/quango2304/telegram_agy && cd telegram_agy
cp .env.example .env         # fill TELEGRAM_BOT_TOKEN + TELEGRAM_BOT_USERNAME
make prod                    # builds there, runs detached
make prod-logs               # tail
```

Update = `git pull && make prod` (rebuilds, recreates, re-runs migrations from the
`bot` entrypoint). Inspect with
`docker compose -f docker-compose.yml exec …` — `db`, `redis` and `mcp` are only
reachable inside the compose network.

The entrypoint `chown`s `/home/agy` on start so the root-owned bind-mounted token is
readable by the unprivileged `agy` user on native Linux (Docker Desktop remaps this
automatically; a plain Linux host does not).

`worker` scales freely (`--scale worker=3`); **`bot` and `beat` must stay at
exactly 1**. Costs, per-reply token numbers and the reasoning behind them are in
[architecture.md](docs/architecture.md) and [media.md](docs/media.md).

## Before you expose this

The bot is **open to anyone** and runs `agy` with `--dangerously-skip-permissions`,
so anyone who can message it can cause commands to run inside the `worker`
container. **The container is not a security boundary.** Read
[security.md](docs/security.md) first.
