# Step 0 — Repo scaffold & tooling

## Goal
An empty-but-runnable Python project: `uv` env, lint/type tooling, folder skeleton,
Makefile. No app logic yet.

## Deliverables

```
.
├── Makefile
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── alembic.ini
├── README.md
├── migrations/            (empty, filled in step 3)
└── src/
    ├── __init__.py
    ├── domain/
    │   ├── entities/
    │   └── interfaces/
    ├── application/
    ├── infrastructure/
    ├── delivery/
    ├── entrypoints/
    └── shared/
```

Every package gets an `__init__.py`.

## `pyproject.toml`

```toml
[project]
name = "telegram-agy"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
  "python-telegram-bot>=22,<23",
  "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.30",
  "psycopg2-binary>=2.9",       # alembic (sync) only
  "alembic>=1.14",
  "celery[redis]>=5.4",
  "redis>=5.2",
  "mcp>=2.0",
  "uvicorn[standard]>=0.34",
  "starlette>=0.46",
  "dependency-injector>=4.42",
  "dynaconf>=3.2",
]

[dependency-groups]
dev = ["ruff>=0.9", "ty>=0.0.1"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC"]

[tool.ty.environment]
python-version = "3.13"
```

> If `mcp>=2.0` does not resolve, pin the newest 2.x available and **verify with
> context7 that `from mcp.server import MCPServer` is the correct import for that
> version** before writing step 7. v1 used `FastMCP`; the APIs differ.

## `.env.example`

```dotenv
TELEGRAM_BOT_TOKEN=
POSTGRES_USER=agy
POSTGRES_PASSWORD=agy_password
POSTGRES_DB=telegram_agy
DATABASE_URL=postgresql+asyncpg://agy:agy_password@db:5432/telegram_agy
ALEMBIC_DATABASE_URL=postgresql+psycopg2://agy:agy_password@db:5432/telegram_agy
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
REDIS_URL=redis://redis:6379/2
AGY_MODEL=gemini-3.8-flash-low
AGY_TIMEOUT_SECONDS=180
AGY_MCP_URL=http://mcp:8000/mcp
MCP_PORT=8000
CONTEXT_MESSAGE_LIMIT=20
LOG_LEVEL=INFO
LOG_FORMAT=plain
```

`.gitignore`: `.venv/`, `__pycache__/`, `.env`, `*.pyc`, `.ruff_cache/`, `.ty_cache/`.

## `Makefile` (root)

```makefile
.DEFAULT_GOAL := help
COMPOSE := docker compose -f docker-compose.dev.yml
AGY_TOKEN := $(HOME)/.gemini/antigravity-cli/antigravity-oauth-token

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'

check-auth:      ## Fail early if the agy token is missing
	@test -f "$(AGY_TOKEN)" || { \
	  echo "ERROR: $(AGY_TOKEN) not found."; \
	  echo "Run 'agy' once on this machine and sign in, then retry."; exit 1; }

dev: check-auth  ## Build and start the whole stack
	@test -f .env || cp .env.example .env
	$(COMPOSE) up --build

down:            ## Stop the stack
	$(COMPOSE) down

logs:            ## Tail all logs
	$(COMPOSE) logs -f

ps:              ## Show services
	$(COMPOSE) ps

shell:           ## Shell into the worker container
	$(COMPOSE) exec worker bash

gen-migration:   ## make gen-migration msg="describe_change"
	$(COMPOSE) run --rm worker alembic revision --autogenerate -m "$(msg)"

migrate:         ## Apply migrations
	$(COMPOSE) run --rm worker alembic upgrade head

verify:          ## Lint, format-check, typecheck, compile
	uv run ruff check . && uv run ruff format --check . && \
	uv run ty check && uv run python -m compileall -q src migrations

fmt:             ## Auto-fix and format
	uv run ruff check --fix . && uv run ruff format .

.PHONY: help check-auth dev down logs ps shell gen-migration migrate verify fmt
```

## Done when
`uv sync` succeeds and `make verify` passes on the empty skeleton.
