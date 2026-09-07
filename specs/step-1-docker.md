# Step 1 — Docker image (with `agy`) + Compose stack

## Goal
One dev image containing the app **and** a working, authed `agy`. Four services run
from it. `make dev` brings everything up.

> Do not use the distroless prod image from the KB here. `agy` needs a shell (we
> `su` to another user and shell out), so the dev single-stage image is the pattern.

---

## `Dockerfile.dev`

```dockerfile
FROM python:3.13-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.8.19 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends curl ca-certificates git gosu \
 && rm -rf /var/lib/apt/lists/*

# --- agy CLI -------------------------------------------------------------
# VERIFIED: this installer supports linux_arm64 and linux_amd64.
# The ONLY supported flag is -d <dir>. --skip-aliases / --skip-path do NOT exist.
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- -d /usr/local/bin \
 && agy --help > /dev/null

# Unprivileged user that actually runs agy. Its HOME holds the OAuth token
# (bind-mounted by compose) and the MCP registration.
RUN useradd -m -u 1001 -s /bin/bash agy \
 && mkdir -p /home/agy/.gemini/antigravity-cli /home/agy/.gemini/config \
 && chown -R agy:agy /home/agy

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

Source is **not** `COPY`'d — compose bind-mounts it.

## `docker/entrypoint.sh`

Runs on every service. Registers the MCP server into `agy`'s config as the `agy`
user, then execs the service command.

```bash
#!/usr/bin/env bash
set -euo pipefail

if [ -n "${AGY_MCP_URL:-}" ]; then
  # Writes /home/agy/.gemini/config/mcp_config.json (VERIFIED path).
  # Server name MUST be "memory" — permission rules and docs are name-coupled.
  gosu agy agy mcp add --type http memory "${AGY_MCP_URL}" >/dev/null 2>&1 || true
fi

exec "$@"
```

> `|| true` because re-running `mcp add` on an existing entry is a no-op update, and
> a transient failure must not stop the bot from booting.

---

## `docker-compose.dev.yml`

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports: ["5434:5432"]
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports: ["6381:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  mcp:
    build: {context: ., dockerfile: Dockerfile.dev}
    restart: unless-stopped
    env_file: [.env]
    command: uv run watchfiles "python -m src.entrypoints.mcp" src
    volumes:
      - .:/app
      - /app/.venv
    ports: ["8010:8000"]
    depends_on:
      db: {condition: service_healthy}

  bot:
    build: {context: ., dockerfile: Dockerfile.dev}
    restart: unless-stopped
    env_file: [.env]
    command: uv run watchfiles "python -m src.entrypoints.bot" src
    volumes:
      - .:/app
      - /app/.venv
    depends_on:
      db: {condition: service_healthy}
      redis: {condition: service_healthy}
    # EXACTLY ONE. Two processes long-polling the same token conflict.

  worker:
    build: {context: ., dockerfile: Dockerfile.dev}
    restart: unless-stopped
    env_file: [.env]
    command: >
      uv run celery -A src.infrastructure.celery_app:celery_app worker
      -Q replies,maintenance -c 4 -l INFO
    volumes:
      - .:/app
      - /app/.venv
      # VERIFIED: the token file ALONE is enough; agy bootstraps the rest.
      # MUST be read-write — agy rewrites this file when it refreshes.
      - ${HOME}/.gemini/antigravity-cli/antigravity-oauth-token:/home/agy/.gemini/antigravity-cli/antigravity-oauth-token:rw
    depends_on:
      db: {condition: service_healthy}
      redis: {condition: service_healthy}
      mcp: {condition: service_started}
    # Scale freely: docker compose up --scale worker=3

  beat:
    build: {context: ., dockerfile: Dockerfile.dev}
    restart: unless-stopped
    env_file: [.env]
    command: uv run celery -A src.infrastructure.celery_app:celery_app beat -l INFO
    volumes:
      - .:/app
      - /app/.venv
    depends_on:
      redis: {condition: service_healthy}
    # EXACTLY ONE. N beats = N× the hourly cleanup.

volumes:
  postgres_data:
```

## Notes for the implementer

- Host ports 5434 / 6381 / 8010 are deliberately non-default to avoid clashing with
  anything already running on the machine.
- Only `worker` mounts the token — it is the only service that runs `agy`.
- If the container's `agy` user cannot write the mounted token, `agy` will fail on
  refresh. `useradd -u 1001` plus a `:rw` mount is correct for Docker Desktop on
  macOS (which maps ownership automatically). On native Linux the uid must match the
  host file owner — note this in the README.

## Done when

```bash
docker compose -f docker-compose.dev.yml run --rm worker \
  gosu agy agy -p "say STACK_OK" --model gemini-3.8-flash-low --output-format json
```
returns `"status":"SUCCESS"`, and `gosu agy agy mcp list` shows `memory  http  enabled`.
