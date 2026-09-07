#!/usr/bin/env bash
set -euo pipefail

if [ -n "${AGY_MCP_URL:-}" ]; then
  # Writes /home/agy/.gemini/config/mcp_config.json (VERIFIED path).
  # Server name MUST be "memory" — permission rules and docs are name-coupled.
  gosu agy agy mcp add --type http memory "${AGY_MCP_URL}" >/dev/null 2>&1 || true
fi

exec "$@"
