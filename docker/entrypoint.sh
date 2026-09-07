#!/usr/bin/env bash
set -euo pipefail

# The OAuth token is bind-mounted from the host. On Docker Desktop (macOS) its
# ownership is remapped to the container user automatically; on native Linux it
# keeps the host uid (often root), so the unprivileged `agy` user can't read it
# and every `agy` call fails with "authentication required". We run as root here,
# so hand the whole HOME (token included) back to `agy`. `agy` rewrites the token
# in place on refresh, which is why the mount must stay read-write.
chown -R agy:agy /home/agy 2>/dev/null || true

# Shared outbox volume (mounted in worker + mcp): agy drops files here, the mcp
# service reads them back to send. agy runs as uid 1001, so it must own it.
mkdir -p /outbox && chown agy:agy /outbox 2>/dev/null || true

if [ -n "${AGY_MCP_URL:-}" ]; then
  # Writes /home/agy/.gemini/config/mcp_config.json (VERIFIED path).
  # Server name MUST be "memory" — permission rules and docs are name-coupled.
  gosu agy agy mcp add --type http memory "${AGY_MCP_URL}" >/dev/null 2>&1 || true
fi

# Optional: log the Composio CLI in so agy can shell out to `composio search |
# execute` on demand (lazy — no per-prompt token cost). Enabled by the presence
# of COMPOSIO_API_KEY, which must be a `uak_...` user API key (run `composio
# login` once and copy ~/.composio/user_data.json's api_key). Best-effort.
if [ -n "${COMPOSIO_API_KEY:-}" ]; then
  if gosu agy composio login --user-api-key "${COMPOSIO_API_KEY}" \
       --no-skill-install -y >/dev/null 2>&1; then
    echo "[entrypoint] Composio CLI logged in"
  else
    echo "[entrypoint] WARN: composio login failed — check COMPOSIO_API_KEY is a uak_ key"
  fi
fi

exec "$@"
