#!/usr/bin/env bash
set -euo pipefail

# The OAuth token is bind-mounted from the host. On Docker Desktop (macOS) its
# ownership is remapped to the container user automatically; on native Linux it
# keeps the host uid (often root), so the unprivileged `agy` user can't read it
# and every `agy` call fails with "authentication required". We run as root here,
# so hand the whole HOME (token included) back to `agy`. `agy` rewrites the token
# in place on refresh, which is why the mount must stay read-write.
chown -R agy:agy /home/agy 2>/dev/null || true

if [ -n "${AGY_MCP_URL:-}" ]; then
  # Writes /home/agy/.gemini/config/mcp_config.json (VERIFIED path).
  # Server name MUST be "memory" — permission rules and docs are name-coupled.
  gosu agy agy mcp add --type http memory "${AGY_MCP_URL}" >/dev/null 2>&1 || true
fi

# Optional: hand agy the Composio tool catalogue. Enabled purely by the presence
# of COMPOSIO_API_KEY. Defaults target Composio Connect (consumer key `ck_...`,
# generic endpoint, `x-consumer-api-key` header); override COMPOSIO_MCP_URL /
# COMPOSIO_MCP_HEADER for a Platform project server (`ak_...`, `x-api-key`).
# Best-effort — a failure here must not stop the service from booting.
if [ -n "${COMPOSIO_API_KEY:-}" ]; then
  _composio_url="${COMPOSIO_MCP_URL:-https://connect.composio.dev/mcp}"
  _composio_hdr="${COMPOSIO_MCP_HEADER:-x-consumer-api-key}"
  if gosu agy agy mcp add --header "${_composio_hdr}: ${COMPOSIO_API_KEY}" \
       composio "${_composio_url}" >/dev/null 2>&1; then
    echo "[entrypoint] Composio MCP registered (${_composio_url})"
  else
    echo "[entrypoint] WARN: could not register Composio MCP (agy mcp add failed)"
  fi
fi

exec "$@"
