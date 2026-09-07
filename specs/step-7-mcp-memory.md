# Step 7 — MCP memory server

## Goal
An HTTP MCP server exposing one tool so `agy` can persist a durable note per chat
thread. Same image, entrypoint `src.entrypoints.mcp`.

> **Verify the SDK first.** The MCP Python SDK v2 renamed `FastMCP` → `MCPServer`.
> Confirm with context7 (`/websites/py_sdk_modelcontextprotocol_io_v2`) against the
> version that actually resolved in `uv.lock` before writing this file.

## `src/delivery/mcp/server.py`

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

mcp = MCPServer("memory")


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
    thread_id = await sessions.resolve(session_key)
    if thread_id is None:
        raise ToolError("session_key không hợp lệ hoặc đã hết hạn.")
    await memories.upsert(thread_id, memory.strip()[:8000])
    return "Đã lưu ghi nhớ."
```

The docstring is what the model actually reads — it must explain *when* to call and
that the write is a full replacement.

## Wiring — mandatory details

```python
security = TransportSecuritySettings(
    allowed_hosts=["mcp", "mcp:*", "mcp:8000", "localhost", "localhost:*", "127.0.0.1:*"],
)

@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = Starlette(
    routes=[Mount("/", app=mcp.streamable_http_app(transport_security=security))],
    lifespan=lifespan,
)
```

Two failure modes that will otherwise cost hours:

1. **`TransportSecuritySettings` is not optional.** DNS-rebinding protection is on by
   default and only accepts localhost, so every call from the `worker` container
   returns **421 Misdirected Request**. `allowed_hosts` entries are exact strings —
   list both the bare host and the `host:*` form.
2. **The host app owns the lifespan.** Mounting disables the built-in one; without
   `mcp.session_manager.run()` every request fails with
   `RuntimeError: Task group is not initialized`.

Add `GET /health` via `@mcp.custom_route` (define custom routes *before* the `Mount`).

## `src/entrypoints/mcp.py`

`uvicorn.run(app, host="0.0.0.0", port=settings.mcp_port)`.

## Registration
Done by `docker/entrypoint.sh` in step 1:
`agy mcp add --type http memory ${AGY_MCP_URL}` → writes
`/home/agy/.gemini/config/mcp_config.json` (**verified path** — not under
`antigravity-cli/`). The server name **must** be `memory` to match the docs and the
prompt text.

## Security note
The tool is reachable by anything on the compose network and is unauthenticated. Its
only guard is `session_key`, which is opaque, single-thread-scoped, and expires in 10
minutes. That is sufficient here — it is not exposed outside the compose network. Do
not publish port 8010 in any deployment beyond local dev.

## Done when
From the worker container:
```bash
gosu agy agy -p "Gọi tool update_thread_memory với session_key=<a real key> và memory='test'" \
  --model gemini-3.8-flash-low --output-format json --dangerously-skip-permissions
```
returns `"status":"SUCCESS"` with no `denied_actions`, and the row appears in
`thread_memories`.
