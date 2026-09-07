"""MCP server for ``agy``: send chat messages, persist per-thread memory, and
schedule per-thread tasks — all keyed by the opaque per-run ``session_key``.

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

import contextlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from telegram.error import TimedOut as TelegramTimedOut

from src.domain.entities.message import Message
from src.domain.entities.scheduled_task import ScheduledTask
from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.config import get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork
from src.infrastructure.schedule.cron import (
    ScheduleError,
    next_cron_run,
    parse_when,
    to_local_str,
    validate_cron,
)
from src.infrastructure.telegram.sender import TelegramSender
from src.shared.chunking import split_message
from src.shared.logger import get_logger

logger = get_logger(__name__)

mcp = MCPServer("memory")

# One run may send at most this many chat messages (runaway-loop guard).
_MAX_MESSAGES_PER_RUN = 12

_uow_factory: Callable[[], IUnitOfWork] | None = None
_sender: TelegramSender | None = None


def _uow() -> IUnitOfWork:
    # Built lazily so the async engine binds to uvicorn's running loop.
    global _uow_factory
    if _uow_factory is None:
        session_factory = build_session_factory(build_engine(get_settings().database_url))
        _uow_factory = lambda: SqlAlchemyUnitOfWork(session_factory)  # noqa: E731
    return _uow_factory()


def _telegram() -> TelegramSender:
    global _sender
    if _sender is None:
        _sender = TelegramSender(get_settings().telegram_bot_token)
    return _sender


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
async def send_chat_message(session_key: str, text: str) -> str:
    """Gửi một tin nhắn vào đúng nhóm/kênh/DM đang xử lý.

    ĐÂY là cách duy nhất để nói với người dùng — chữ bạn viết ngoài tool không ai
    thấy. Gọi được nhiều lần trong một lượt để nhắn thành nhiều tin: ví dụ nhắn
    "chờ tao xíu" trước, làm xong rồi gọi lại để nhắn kết quả. Tin quá dài sẽ tự
    được cắt nhỏ.

    Args:
        session_key: khoá phiên được cung cấp trong prompt. Bắt buộc.
        text: nội dung tin nhắn.
    """
    text = text.strip()
    if not text:
        raise ToolError("Tin nhắn rỗng.")

    async with _uow() as uow:
        session = await uow.sessions.get_active(session_key)
        if session is None:
            raise ToolError("session_key không hợp lệ hoặc đã hết hạn.")
        thread = await uow.threads.get_by_id(session.thread_id)
        if thread is None:
            raise ToolError("Không tìm thấy nhóm cho phiên này.")
        thread_id = thread.id
        chat_id = thread.chat_id
        topic_id = thread.topic_id
        already_sent = session.sent_count
        reply_to = session.trigger_tg_message_id if already_sent == 0 else None

    if already_sent >= _MAX_MESSAGES_PER_RUN:
        raise ToolError("Đã gửi quá nhiều tin trong lượt này, dừng lại.")

    chunks = split_message(text, get_settings().telegram_max_chars)
    sender = _telegram()
    sent_ids: list[int] = []
    for i, chunk in enumerate(chunks):
        try:
            sent_ids.append(
                await sender.send_reply(chat_id, chunk, topic_id, reply_to if i == 0 else None)
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("telegram send failed", extra={"thread_id": thread_id, "err": str(exc)})
            raise ToolError(f"Gửi Telegram lỗi: {exc}") from exc

    async with _uow() as uow:
        for sent_id, chunk in zip(sent_ids, chunks, strict=True):
            await uow.messages.add(
                Message(
                    thread_id=thread_id,
                    tg_message_id=sent_id,
                    from_user_id=None,
                    from_username=None,
                    from_name=None,
                    is_bot_self=True,
                    text=chunk,
                    sent_at=datetime.now(UTC),
                )
            )
        await uow.sessions.bump_sent_count(session_key, len(sent_ids))
        await uow.commit()

    logger.info("chat message sent", extra={"thread_id": thread_id, "parts": len(sent_ids)})
    return "Đã gửi."


def _resolve_outbox_file(file_path: str) -> Path:
    """Sync fs checks (kept out of the async tool body for ASYNC240). Returns the
    validated real path, or raises ToolError."""
    settings = get_settings()
    outbox = os.path.realpath(settings.outbox_dir)
    real = os.path.realpath(file_path)
    if real == outbox or os.path.commonpath([real, outbox]) != outbox:
        raise ToolError(f"file_path phải nằm trong {settings.outbox_dir}/.")
    if not os.path.isfile(real):
        raise ToolError(f"Không thấy file: {file_path}")
    size_mb = os.path.getsize(real) / (1024 * 1024)
    if size_mb > settings.telegram_max_file_mb:
        raise ToolError(
            f"File {size_mb:.1f}MB, quá {settings.telegram_max_file_mb}MB Telegram cho phép."
        )
    return Path(real)


def _safe_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


@mcp.tool()
async def send_chat_file(session_key: str, file_path: str, caption: str = "") -> str:
    """Gửi một FILE vào đúng nhóm/kênh/DM đang xử lý.

    Trước đó hãy tải/ghi file cần gửi vào thư mục /outbox/ (ví dụ tải file từ
    Google Drive bằng `composio` rồi lưu vào /outbox/ten-file). Chỉ nhận đường
    dẫn tuyệt đối nằm trong /outbox/. File sẽ bị xoá sau khi gửi xong.

    Args:
        session_key: khoá phiên được cung cấp trong prompt. Bắt buộc.
        file_path: đường dẫn tuyệt đối tới file, phải nằm trong /outbox/.
        caption: (tuỳ chọn) chú thích gửi kèm file.
    """
    path = _resolve_outbox_file(file_path)

    async with _uow() as uow:
        session = await uow.sessions.get_active(session_key)
        if session is None:
            raise ToolError("session_key không hợp lệ hoặc đã hết hạn.")
        thread = await uow.threads.get_by_id(session.thread_id)
        if thread is None:
            raise ToolError("Không tìm thấy nhóm cho phiên này.")
        thread_id = thread.id
        chat_id = thread.chat_id
        topic_id = thread.topic_id
        already_sent = session.sent_count
        reply_to = session.trigger_tg_message_id if already_sent == 0 else None

    if already_sent >= _MAX_MESSAGES_PER_RUN:
        raise ToolError("Đã gửi quá nhiều tin trong lượt này, dừng lại.")

    try:
        sent_id: int | None = await _telegram().send_file(
            chat_id, path, caption.strip(), topic_id, reply_to
        )
    except TelegramTimedOut:
        # The upload almost always completed; Telegram just didn't ACK in time.
        # Treat as delivered so agy doesn't retry and double-send.
        logger.warning("file send timed out (assumed delivered)", extra={"thread_id": thread_id})
        sent_id = None
    except Exception as exc:  # noqa: BLE001
        logger.error("telegram file send failed", extra={"thread_id": thread_id, "err": str(exc)})
        raise ToolError(f"Gửi file lỗi: {exc}") from exc

    # The file HAS been handed to Telegram. From here on nothing may raise: a
    # post-send error that surfaced as a tool failure would make agy retry and
    # send the file again (this is exactly how the 4x-send bug happened).
    label = f"[file: {path.name}]" + (f" {caption.strip()}" if caption.strip() else "")
    try:
        async with _uow() as uow:
            if sent_id is not None:
                await uow.messages.add(
                    Message(
                        thread_id=thread_id,
                        tg_message_id=sent_id,
                        from_user_id=None,
                        from_username=None,
                        from_name=None,
                        is_bot_self=True,
                        text=label,
                        sent_at=datetime.now(UTC),
                    )
                )
            await uow.sessions.bump_sent_count(session_key, 1)
            await uow.commit()
    except Exception:  # noqa: BLE001
        logger.exception("post-send bookkeeping failed", extra={"thread_id": thread_id})

    _safe_unlink(path)
    with contextlib.suppress(Exception):
        logger.info("chat file sent", extra={"thread_id": thread_id, "file_name": path.name})
    return "Đã gửi file."


@mcp.tool()
async def update_memory(session_key: str, memory: str) -> str:
    """Lưu ghi nhớ lâu dài cho cuộc trò chuyện hiện tại.

    Với chat riêng thì là ghi nhớ về người đó; với group/supergroup thì là MỘT
    ghi nhớ chung cho cả nhóm (dùng chung cho mọi topic trong nhóm). Gọi khi bạn
    biết được điều gì đáng nhớ (tên gọi, sở thích, cách xưng hô, quy ước riêng,
    việc đang làm...). Nội dung sẽ GHI ĐÈ toàn bộ ghi nhớ cũ, nên hãy gửi bản
    đầy đủ đã gộp.

    Args:
        session_key: khoá phiên được cung cấp trong prompt. Bắt buộc.
        memory: toàn bộ nội dung ghi nhớ mới.
    """
    async with _uow() as uow:
        thread_id = await uow.sessions.resolve(session_key)
        if thread_id is None:
            raise ToolError("session_key không hợp lệ hoặc đã hết hạn.")
        thread = await uow.threads.get_by_id(thread_id)
        if thread is None:
            raise ToolError("Không tìm thấy cuộc trò chuyện cho phiên này.")
        await uow.memories.upsert(thread.chat_id, memory.strip()[:8000])
        await uow.commit()
    logger.info("memory updated", extra={"chat_id": thread.chat_id})
    return "Đã lưu ghi nhớ."


async def _resolve_thread(uow: IUnitOfWork, session_key: str) -> int:
    thread_id = await uow.sessions.resolve(session_key)
    if thread_id is None:
        raise ToolError("session_key không hợp lệ hoặc đã hết hạn.")
    return thread_id


def _plan_run_at(when: str, cron: str) -> tuple[datetime, str | None]:
    """Return (first run_at UTC, cron-or-None). Exactly one of when/cron."""
    when, cron = when.strip(), cron.strip()
    if bool(when) == bool(cron):
        raise ToolError("Cần đúng một trong hai: 'when' (một lần) hoặc 'cron' (lặp lại).")
    try:
        if cron:
            cron = validate_cron(cron)
            return next_cron_run(cron), cron
        run_at = parse_when(when)
    except ScheduleError as exc:
        raise ToolError(str(exc)) from exc
    if run_at <= datetime.now(UTC):
        raise ToolError("Thời điểm 'when' đã ở quá khứ.")
    return run_at, None


def _fmt_task(t: ScheduledTask) -> str:
    kind = f"lặp `{t.cron}`" if t.cron else "một lần"
    state = "" if t.enabled else " [đã tắt]"
    return f"#{t.id} • {kind}{state} • lần tới: {to_local_str(t.run_at)} • {t.instruction}"


@mcp.tool()
async def schedule_task(
    session_key: str,
    instruction: str,
    when: str = "",
    cron: str = "",
    requested_by: str = "",
) -> str:
    """Hẹn cho nhóm/kênh hiện tại làm một việc vào lúc khác, hoặc lặp lại.

    Đến giờ, bot sẽ xử lý `instruction` y như vừa có người nhắn nó vào nhóm này
    (có đầy đủ lịch sử chat và ghi nhớ). Dùng khi người ta nói kiểu "mỗi sáng
    9h nhắc cả nhóm họp", "5 phút nữa nhắc tao uống nước", "thứ 2 hàng tuần...".

    Args:
        session_key: khoá phiên trong prompt. Bắt buộc.
        instruction: việc cần làm, viết đầy đủ tự-chứa (vd "Nhắc cả nhóm họp
            standup lúc 9h").
        when: thời điểm MỘT LẦN, ISO-8601 giờ Việt Nam (vd "2026-09-08T09:00").
        cron: biểu thức cron 5 trường cho việc LẶP LẠI, giờ Việt Nam
            (vd "0 9 * * *" = 9h sáng mỗi ngày; "*/30 * * * *" = mỗi 30 phút).
        requested_by: (tuỳ chọn) tên người yêu cầu, để bot xưng hô đúng khi tới giờ.
    """
    instruction = instruction.strip()
    if not instruction:
        raise ToolError("Thiếu 'instruction'.")
    run_at, cron_norm = _plan_run_at(when, cron)
    async with _uow() as uow:
        thread_id = await _resolve_thread(uow, session_key)
        task = await uow.schedules.create(
            thread_id, instruction[:4000], run_at, cron_norm, (requested_by.strip() or None)
        )
        await uow.commit()
        summary = _fmt_task(task)
    logger.info("task scheduled", extra={"thread_id": thread_id, "run_at": run_at.isoformat()})
    return f"Đã đặt lịch. {summary}"


@mcp.tool()
async def list_scheduled_tasks(session_key: str) -> str:
    """Liệt kê các việc đã hẹn giờ của nhóm/kênh hiện tại (kèm id để sửa/xoá)."""
    async with _uow() as uow:
        thread_id = await _resolve_thread(uow, session_key)
        tasks = await uow.schedules.list_for_thread(thread_id)
        lines = [_fmt_task(t) for t in tasks]
    if not lines:
        return "Nhóm này chưa hẹn việc gì."
    return "\n".join(lines)


@mcp.tool()
async def update_scheduled_task(
    session_key: str,
    task_id: int,
    instruction: str = "",
    when: str = "",
    cron: str = "",
    enabled: bool | None = None,
) -> str:
    """Sửa một việc đã hẹn (theo id lấy từ list_scheduled_tasks).

    Chỉ truyền trường muốn đổi. Đổi `when` hoặc `cron` sẽ tính lại giờ chạy kế.
    `enabled=false` để tạm dừng, `true` để bật lại.
    """
    fields: dict[str, object] = {}
    if instruction.strip():
        fields["instruction"] = instruction.strip()[:4000]
    if when.strip() or cron.strip():
        run_at, cron_norm = _plan_run_at(when, cron)
        fields["run_at"] = run_at
        fields["cron"] = cron_norm  # None => switch to one-off
    if enabled is not None:
        fields["enabled"] = enabled
    if not fields:
        raise ToolError("Không có gì để sửa.")

    async with _uow() as uow:
        thread_id = await _resolve_thread(uow, session_key)
        existing = await uow.schedules.get_for_thread(thread_id, task_id)
        if existing is None:
            raise ToolError(f"Không tìm thấy việc #{task_id} trong nhóm này.")
        updated = await uow.schedules.update_fields(thread_id, task_id, **fields)
        await uow.commit()
        summary = _fmt_task(updated) if updated is not None else ""
    return f"Đã cập nhật. {summary}"


@mcp.tool()
async def cancel_scheduled_task(session_key: str, task_id: int) -> str:
    """Xoá hẳn một việc đã hẹn (theo id lấy từ list_scheduled_tasks)."""
    async with _uow() as uow:
        thread_id = await _resolve_thread(uow, session_key)
        ok = await uow.schedules.delete(thread_id, task_id)
        await uow.commit()
    if not ok:
        raise ToolError(f"Không tìm thấy việc #{task_id} trong nhóm này.")
    return f"Đã xoá việc #{task_id}."


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
