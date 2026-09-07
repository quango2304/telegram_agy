"""Assemble the single argv string handed to ``agy -p``.

Order: persona, session-key + memory-tool instruction, current memory, recent
chat history (old -> new), then the closing instruction. The whole thing is one
argv entry, so it is capped: oldest history lines are dropped first, then the
memory block is truncated.
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
    return text.replace(f"@{bot_username}", "").replace(f"@{bot_username.lower()}", "").strip()


def _fmt_line(line: HistoryLine, truncate: int) -> str:
    name = BOT_LABEL if line.is_bot_self else (line.name or "ai đó")
    body = line.text.strip()
    if len(body) > truncate:
        body = body[:truncate] + "…"
    return f"[{name}]: {body}"


def build_prompt(
    *,
    session_key: str,
    memory: str | None,
    history: Sequence[HistoryLine],
    bot_username: str,
    max_chars: int,
    truncate_chars: int,
) -> str:
    mem_text = (memory or "").strip() or "(chưa có gì)"
    rendered = [_fmt_line(h, truncate_chars) for h in history]
    # Strip the bot handle from the final (trigger) line so it doesn't read as
    # part of the question.
    if rendered:
        last = history[-1]
        rendered[-1] = _fmt_line(
            HistoryLine(last.name, _strip_handle(last.text, bot_username), last.is_bot_self),
            truncate_chars,
        )

    def assemble(lines: list[str], mem: str) -> str:
        history_block = "\n".join(lines) if lines else "(chưa có tin nào)"
        return (
            f"{PERSONA_PROMPT}\n\n"
            f"session_key = {session_key}\n"
            "Nếu bạn học được điều gì đáng nhớ lâu dài về nhóm này hoặc người trong nhóm,\n"
            "hãy gọi tool `update_thread_memory` với session_key ở trên và TOÀN BỘ nội dung\n"
            "memory mới (tool này GHI ĐÈ, không nối thêm).\n\n"
            f"Ghi nhớ hiện tại về nhóm này:\n{mem}\n\n"
            f"Lịch sử chat gần đây (cũ → mới):\n{history_block}\n\n"
            "Trả lời tin nhắn cuối cùng."
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
