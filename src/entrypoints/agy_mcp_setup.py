"""One-shot, worker start only: build agy's MCP config from mcp_servers.json.

Called by ``docker/entrypoint.sh`` when ``AGY_MCP_SERVERS_FILE`` is set (compose
sets it on the worker alone). Exit 1 on a malformed file — the worker refusing
to start is deliberate, so a typo is never silently half-applied. A server that
fails its health check is only skipped. See docs/external-mcp-servers.md.
"""

from __future__ import annotations

import sys

import anyio

from src.infrastructure.agy.mcp_servers import (
    RESERVED_NAME,
    ConfigError,
    check_all,
    load_servers,
    write_agy_config,
    write_prompts,
)
from src.infrastructure.config import get_settings
from src.shared.logger import get_logger

logger = get_logger(__name__)


def main() -> int:
    settings = get_settings()
    path = settings.agy_mcp_servers_file
    try:
        specs = load_servers(path)
    except ConfigError as exc:
        for error in exc.errors:
            logger.error("mcp_servers.json invalid path=%s: %s", path, error)
        logger.error("mcp_servers.json invalid — worker will not start until it is fixed")
        return 1

    servers: dict[str, dict[str, object]] = {}
    prompts: dict[str, str] = {}
    if settings.agy_mcp_url:
        servers[RESERVED_NAME] = {"disabled": False, "serverUrl": settings.agy_mcp_url}

    for spec in specs:
        if spec.disabled:
            logger.info("mcp server disabled name=%s target=%s", spec.name, spec.target)
    enabled = [s for s in specs if not s.disabled]
    results = anyio.run(check_all, enabled, settings.agy_user)
    for spec in enabled:
        result = results[spec.name]
        kind = "remote" if spec.is_remote else "local"
        if result.ok:
            servers[spec.name] = spec.to_agy()
            if spec.prompt:
                prompts[spec.name] = spec.prompt
            logger.info(
                "mcp server ok name=%s kind=%s target=%s tools=%s took=%.1fs",
                spec.name,
                kind,
                spec.target,
                result.tools,
                result.took_s,
            )
        else:
            logger.warning(
                "mcp server SKIPPED name=%s kind=%s target=%s took=%.1fs reason=%s",
                spec.name,
                kind,
                spec.target,
                result.took_s,
                result.reason,
            )

    written = write_agy_config(servers, settings.agy_user)
    write_prompts(prompts)
    logger.info(
        "mcp servers registered: %s (%s) prompts=%s",
        ", ".join(servers) or "-",
        written,
        ",".join(prompts) or "-",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
