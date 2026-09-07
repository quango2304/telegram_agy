"""MCP memory server — one tool so ``agy`` can persist a durable per-thread note.

SDK notes (mcp 2.1.1, verified against the installed package):
- ``FastMCP`` is now ``MCPServer`` (``from mcp.server import MCPServer``).
- ``streamable_http_app()`` already returns a Starlette app whose lifespan runs
  ``session_manager.run()``. We use that app directly instead of mounting it in
  an outer Starlette, which sidesteps the "Task group is not initialized" trap.
- ``TransportSecuritySettings`` is mandatory: DNS-rebinding protection is on by
  default and only accepts localhost, so a call from the ``worker`` container
  (Host: ``mcp:8000``) returns 421 unless ``mcp``/``mcp:*`` is allow-listed.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.config import get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork
from src.shared.logger import get_logger

logger = get_logger(__name__)

mcp = MCPServer("memory")

_uow_factory: Callable[[], IUnitOfWork] | None = None


def _uow() -> IUnitOfWork:
    # Built lazily so the async engine binds to uvicorn's running loop.
    global _uow_factory
    if _uow_factory is None:
        session_factory = build_session_factory(build_engine(get_settings().database_url))
        _uow_factory = lambda: SqlAlchemyUnitOfWork(session_factory)  # noqa: E731
    return _uow_factory()


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
async def update_thread_memory(session_key: str, memory: str) -> str:
    """Lưu ghi nhớ lâu dài về nhóm chat hiện tại.

    Gọi tool này khi bạn biết được điều gì đáng nhớ về nhóm hoặc về người trong
    nhóm (tên gọi, sở thích, cách xưng hô, quy ước riêng, việc đang làm...).
    Nội dung sẽ GHI ĐÈ toàn bộ ghi nhớ cũ, nên hãy gửi bản đầy đủ đã gộp.

    Args:
        session_key: khoá phiên được cung cấp trong prompt. Bắt buộc.
        memory: toàn bộ nội dung ghi nhớ mới.
    """
    async with _uow() as uow:
        thread_id = await uow.sessions.resolve(session_key)
        if thread_id is None:
            raise ToolError("session_key không hợp lệ hoặc đã hết hạn.")
        await uow.memories.upsert(thread_id, memory.strip()[:8000])
        await uow.commit()
    logger.info("memory updated", extra={"thread_id": thread_id})
    return "Đã lưu ghi nhớ."


_security = TransportSecuritySettings(
    allowed_hosts=[
        "mcp",
        "mcp:*",
        "mcp:8000",
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
    ],
    allowed_origins=["http://mcp:*", "http://localhost:*", "http://127.0.0.1:*"],
)

app = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=_security)
