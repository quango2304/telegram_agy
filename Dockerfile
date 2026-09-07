# Production image. Multi-stage: resolve deps into /app/.venv, then a lean
# runtime with the app baked in (no source bind-mount, no dev tooling, no
# watchfiles). Same agy CLI + unprivileged agy user + entrypoint as the dev image.

# ---- build: resolve the locked prod dependencies ----
FROM python:3.13-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.8.19 /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime ----
FROM python:3.13-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends curl ca-certificates git gosu unzip \
 && rm -rf /var/lib/apt/lists/*

# --- agy CLI --- (linux_arm64 / linux_amd64; -d <dir> is the only supported flag)
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- -d /usr/local/bin \
 && agy --help > /dev/null

# --- Composio CLI (optional; only used when COMPOSIO_API_KEY is set) ---
RUN curl -fsSL https://composio.dev/install | \
    COMPOSIO_INSTALL_DIR=/opt/composio COMPOSIO_BIN_DIR=/usr/local/bin \
    COMPOSIO_INSTALL_SHELL=none COMPOSIO_QUIET=1 sh \
 && composio --help > /dev/null

# Unprivileged user that runs agy. Its HOME holds the bind-mounted OAuth token
# and the MCP registration; the entrypoint chowns it (native Linux keeps host uid).
RUN useradd -m -u 1001 -s /bin/bash agy \
 && mkdir -p /home/agy/.gemini/antigravity-cli /home/agy/.gemini/config \
 && chown -R agy:agy /home/agy

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini pyproject.toml ./
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# --- Layer 0 + 1: the app tree must be unreadable to the unprivileged `agy`
# user. Secrets never enter the image (.env is in .dockerignore; they arrive at
# runtime via compose `env_file`) — assert that, then lock /app to root only.
# Every long-running service runs as root and keeps full access; `agy` runs as
# uid 1001 with its own /tmp workdir and can read nothing here — not the source,
# not a stray .env, not the configs.
RUN test ! -e /app/.env || { echo "SECURITY: .env must not be baked into the image"; exit 1; }
RUN chown -R root:root /app && chmod -R go-rwx /app

# Root at runtime — the entrypoint gosu's down to `agy` per agy call.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
