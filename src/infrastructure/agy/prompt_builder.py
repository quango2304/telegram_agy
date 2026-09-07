"""Assemble the single argv string handed to ``agy -p``.

Order: persona, session-key + memory-tool instruction, current memory, recent
chat history (old -> new), then the message this run must answer — named
explicitly, because rapid triggers mean the trigger is not always the last
history line. The whole thing is one argv entry, so it is capped: oldest
history lines are dropped first, then the memory block is truncated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.shared.persona import BOT_LABEL, PERSONA_PROMPT


@dataclass(frozen=True)
class HistoryLine:
    name: str
    text: str
    is_bot_self: bool


def _strip_handle(text: str, bot_username: str) -> str:
    if not bot_username:
        return text.strip()
    out = text
    for variant in (f"@{bot_username}", f"@{bot_username.lower()}", f"@{bot_username.upper()}"):
        out = out.replace(variant, "")
    return out.strip()


def _fmt_line(line: HistoryLine, bot_username: str, truncate: int) -> str:
    name = BOT_LABEL if line.is_bot_self else (line.name or "ai đó")
    body = _strip_handle(line.text, bot_username)
    if len(body) > truncate:
        body = body[:truncate] + "…"
    return f"[{name}]: {body}"


def build_prompt(
    *,
    session_key: str,
    memory: str | None,
    history: Sequence[HistoryLine],
    trigger_name: str,
    trigger_text: str,
    bot_username: str,
    now_local: str,
    max_chars: int,
    truncate_chars: int,
    extra_tools: bool = False,
) -> str:
    mem_text = (memory or "").strip() or "(chưa có gì)"
    extra_tools_line = (
        "CÔNG CỤ NGOÀI: bạn có CLI `composio` (đã đăng nhập sẵn) để thao tác Google "
        "Drive, Gmail, tìm kiếm web, v.v. Nếu người ta nhờ việc cần công cụ này thì "
        'PHẢI làm thật: chạy `composio search "<việc>"` tìm tool, '
        "`composio execute <TOOL_SLUG> --get-schema` xem input, rồi "
        "`composio execute <TOOL_SLUG> -d '{...}'` để chạy (kết quả download thường là "
        "một s3url — dùng `curl -sL '<url>' -o /outbox/<tên-file>` để lấy về). "
        "Gửi file cho người dùng: đặt file vào /outbox/ rồi gọi tool `send_chat_file` "
        "ĐÚNG MỘT LẦN cho mỗi file.\n"
        if extra_tools
        else ""
    )
    rendered = [_fmt_line(h, bot_username, truncate_chars) for h in history]
    trigger_body = _strip_handle(trigger_text, bot_username)
    if len(trigger_body) > truncate_chars:
        trigger_body = trigger_body[:truncate_chars] + "…"

    def assemble(lines: list[str], mem: str) -> str:
        history_block = "\n".join(lines) if lines else "(chưa có tin nào)"
        return (
            f"{PERSONA_PROMPT}\n\n"
            f"Bây giờ là {now_local} (giờ Việt Nam).\n"
            f"session_key = {session_key}\n\n"
            "CÁCH TRẢ LỜI: bạn KHÔNG nói chuyện trực tiếp với người dùng. Muốn nhắn gì\n"
            "vào nhóm thì phải gọi tool `send_chat_message` với session_key ở trên.\n"
            "Được gọi nhiều lần để nhắn thành nhiều tin. Chữ bạn viết ra ngoài tool sẽ\n"
            "KHÔNG ai thấy, nên nếu không gọi tool thì coi như bạn im lặng.\n"
            "QUAN TRỌNG: nếu việc cần nhiều bước hoặc mất thời gian (tra cứu, tải file,\n"
            "chạy `composio`, đặt nhiều lịch...), hãy gọi `send_chat_message` NGAY từ\n"
            "đầu để báo 'ok để tui lo' rồi mới bắt tay làm — đừng để người ta chờ im\n"
            "ru. Làm xong thì gọi lại để báo kết quả.\n\n"
            "Nếu bạn học được điều gì đáng nhớ lâu dài về nhóm này hoặc người trong nhóm,\n"
            "hãy gọi tool `update_memory` với session_key ở trên và TOÀN BỘ nội dung\n"
            "memory mới (tool này GHI ĐÈ, không nối thêm; group thì memory dùng chung\n"
            "cho cả nhóm, mọi topic).\n"
            "Nếu người ta nhờ làm gì đó vào lúc khác hoặc định kỳ (vd 'mai nhắc...',\n"
            "'mỗi sáng 9h...'), hãy gọi tool `schedule_task` với session_key ở trên.\n"
            f"{extra_tools_line}"
            "Người ta nhờ việc cụ thể (tìm file, tra cứu, gửi file, đặt lịch...) thì "
            "LÀM cho xong đã, xong rồi muốn cà khịa gì thì cà — đừng né việc để đi "
            "chọc ngoáy.\n\n"
            f"Ghi nhớ hiện tại về nhóm này:\n{mem}\n\n"
            f"Lịch sử chat gần đây (cũ → mới):\n{history_block}\n\n"
            f"Trả lời tin nhắn này của {trigger_name} (nhớ gọi `send_chat_message`): "
            f'"{trigger_body}"'
        )

    prompt = assemble(rendered, mem_text)
    # Trim oldest history lines first.
    while len(prompt) > max_chars and len(rendered) > 1:
        rendered.pop(0)
        prompt = assemble(rendered, mem_text)
    # Then truncate the memory block.
    if len(prompt) > max_chars:
        overflow = len(prompt) - max_chars
        mem_text = mem_text[: max(0, len(mem_text) - overflow - 1)] + "…"
        prompt = assemble(rendered, mem_text)
    return prompt
