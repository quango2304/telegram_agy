# Configuration

Dynaconf loads `.env` plus the process environment; a frozen `@dataclass`
(`src/infrastructure/config.py`) exposes it typed. Everything downstream reads
`get_settings()`, never `os.environ`.

`.env` is gitignored. `.env.example` is the template and carries inline notes.

> **Changing a value requires recreating containers**, not restarting them:
> `docker compose ... up -d`. `docker restart` does **not** re-read `env_file`.

## Required

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_BOT_USERNAME` | the bot's username **without `@`**. Used to strip the handle out of question text. Blank still works; the handle just isn't stripped. |
| `DATABASE_URL` | asyncpg URL. `ALEMBIC_DATABASE_URL` is the psycopg2 equivalent for migrations. |

Everything else has a working default for local Docker.

## Model and runtime

| Key | Default | Notes |
|---|---|---|
| `AGY_MODEL` | `gemini-3.8-flash-high` | The **simple** model: what every run uses unless the router picks `AGY_MODEL_COMPLEX` (see [model-routing.md](model-routing.md)). `agy models` lists options (Claude Sonnet/Opus 5.5, Gemini 3.x Flash, Gemini 3.1 Pro; effort suffix `-low`/`-medium`/`-high`). Avoid `-low` if Composio is enabled. Run it as the `agy` user inside the container — as root it says "Please sign in". |
| `AGY_TIMEOUT_SECONDS` | 300 | Budget for an `AGY_MODEL` run. |
| `AGY_MODEL_COMPLEX` | `gemini-3.1-pro-high` | The **complex** model: market outlook, evaluation, multi-step reasoning. |
| `AGY_TIMEOUT_SECONDS_COMPLEX` | 480 | Budget for an `AGY_MODEL_COMPLEX` run. Celery soft/hard limits (+120 / +180) and the thread lock TTL (+240) are derived from the larger of the two budgets. |
| `OPENROUTER_API_KEY` | *(empty)* | Turns model routing on. Empty means every run uses `AGY_MODEL`. |
| `ROUTER_MODEL` | `~typesafe/jev-latest` | Jev model on OpenRouter's Decisions API. Pin e.g. `typesafe/jev-1.13` if a release changes routing. |
| `ROUTER_TIMEOUT_SECONDS` | 5 | End-to-end cap on the Jev call; past it the run falls back to `AGY_MODEL`. |
| `AGY_PROMPT_MAX_CHARS` | 60000 | Prompt cap; oldest history is dropped first, then memory truncated. |
| `AGY_MESSAGE_TRUNCATE_CHARS` | 2000 | Per-message cut in the history block. |
| `SESSION_TTL_SECONDS` | 600 | Lifetime of a run's `session_key`. |

## History, retention and search

| Key | Default | Notes |
|---|---|---|
| `CONTEXT_MESSAGE_LIMIT` | 30 | Messages per prompt. **Not** retention. Also the per-thread floor the cleanup job never deletes below. |
| `MESSAGE_RETENTION_DAYS` | 10 | Age after which messages are pruned hourly. |
| `HISTORY_SEARCH_LIMIT` | 20 | Max rows one `search_history` call returns. |

See [memory-and-history.md](memory-and-history.md).

## Images

| Key | Default | Notes |
|---|---|---|
| `MEDIA_MAX_PER_RUN` | 5 | Max images attached per reply. `0` disables image reading. Each image costs tokens and latency. |
| `MEDIA_MAX_DOWNLOAD_MB` | 20 | Telegram's `getFile` ceiling anyway. |
| `MEDIA_CONTEXT_SECONDS` | 60 | How close to the trigger an otherwise-unlinked photo must be to get attached. Raising this makes unrelated replies download old photos. |

See [media.md](media.md).

## Chat behaviour

| Key | Default | Notes |
|---|---|---|
| `REACTION_ACK` | 👀 | Reaction while a run is in flight. Empty disables. Must be an emoji Telegram allows bots to use. |
| `TELEGRAM_MAX_CHARS` | 4096 | Chunking threshold for long replies. |
| `TELEGRAM_MAX_FILE_MB` | 50 | `send_chat_file` ceiling. |

## Optional integrations

| Key | Notes |
|---|---|
| `COMPOSIO_API_KEY` | A `uak_…` **user** key. See [composio-and-files.md](composio-and-files.md). |

## Logging

`LOG_LEVEL` (default `INFO`), `LOG_FORMAT` (`plain` or `json`).

`LOG_DRIVER` is read by **`docker-compose.yml`**, not by the app: the Docker log
driver for the app services. Default `json-file`; set **`journald` on the VPS**.
json-file logs are deleted with the container, so every deploy used to wipe the
only record of failed runs. Docker Desktop has no journald, hence the default.
See [operations.md](operations.md) for reading old logs.
