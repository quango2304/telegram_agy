"""The ``mcp`` service: HTTP MCP server exposing ``update_thread_memory``."""

from __future__ import annotations

import uvicorn

from src.delivery.mcp.server import app
from src.infrastructure.config import get_settings
from src.shared.logger import get_logger

logger = get_logger(__name__)


def main() -> None:
    settings = get_settings()
    logger.info("starting MCP server", extra={"port": settings.mcp_port})
    uvicorn.run(app, host="0.0.0.0", port=settings.mcp_port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
