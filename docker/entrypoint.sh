#!/usr/bin/env bash
set -euo pipefail

# Run a command as `agy` with a scrubbed environment. The container env carries
# every secret (compose env_file); a bootstrap process that keeps it would, for
# its lifetime, expose all of it via /proc/<pid>/environ to a same-uid agy run.
# Mirrors SCRUBBED_ENV in src/infrastructure/agy/agy_client_impl.py.
agy_clean() {
  gosu agy env -i \
    HOME=/home/agy PATH=/usr/local/bin:/usr/bin:/bin USER=agy \
    LANG=C.UTF-8 TERM=dumb AGY_OUTBOX=/outbox "$@"
}

# The OAuth token is bind-mounted from the host. On Docker Desktop (macOS) its
# ownership is remapped to the container user automatically; on native Linux it
# keeps the host uid (often root), so the unprivileged `agy` user can't read it
# and every `agy` call fails with "authentication required". We run as root here,
# so hand the whole HOME (token included) back to `agy`. `agy` rewrites the token
# in place on refresh, which is why the mount must stay read-write.
chown -R agy:agy /home/agy 2>/dev/null || true

# Keep the app tree unreadable to the unprivileged `agy` user: source, the
# alembic config, and any stray secret file. Root (this process and every
# long-running service) owns it and keeps full access. Enforced by the kernel on
# a native-Linux host (incl. the prod VPS, where the prod Dockerfile already
# baked this in); best-effort on Docker Desktop bind mounts, which don't honour
# POSIX perms — there the .env itself is shadowed by compose instead.
chmod -R go-rwx /app/src /app/migrations 2>/dev/null || true
chmod go-rwx /app/.env /app/alembic.ini /app/pyproject.toml 2>/dev/null || true

# Shared outbox volume (mounted in worker + mcp): agy drops files here, the mcp
# service reads them back to send. agy runs as uid 1001, so it must own it.
mkdir -p /outbox && chown agy:agy /outbox 2>/dev/null || true

if [ -n "${AGY_MCP_URL:-}" ]; then
  # Writes /home/agy/.gemini/config/mcp_config.json (VERIFIED path).
  # Server name MUST be "memory" — permission rules and docs are name-coupled.
  agy_clean agy mcp add --type http memory "${AGY_MCP_URL}" >/dev/null 2>&1 || true
fi

# Optional: log the Composio CLI in so agy can shell out to `composio search |
# execute` on demand (lazy — no per-prompt token cost). Enabled by the presence
# of COMPOSIO_API_KEY, which must be a `uak_...` user API key (run `composio
# login` once and copy ~/.composio/user_data.json's api_key). Best-effort.
# Key is passed as an arg (not the env) and scrubbed from the child's environ.
if [ -n "${COMPOSIO_API_KEY:-}" ]; then
  if agy_clean composio login --user-api-key "${COMPOSIO_API_KEY}" \
       --no-skill-install -y >/dev/null 2>&1; then
    echo "[entrypoint] Composio CLI logged in"
  else
    echo "[entrypoint] WARN: composio login failed — check COMPOSIO_API_KEY is a uak_ key"
  fi
fi

exec "$@"
