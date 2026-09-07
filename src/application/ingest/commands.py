"""Plain command/result dataclasses for the ingest use case.

The PTB handler parses an ``Update`` into :class:`IncomingMessage` and never
leaks a telegram object past the delivery layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: int
    topic_id: int
    chat_type: str
    chat_title: str | None
    tg_message_id: int
    from_user_id: int | None
    from_username: str | None
    from_name: str | None
    text: str
    sent_at: datetime
    is_edit: bool = False


@dataclass(frozen=True)
class IngestResult:
    thread_id: int
    message_id: int | None  # None for an edit (existing row updated in place)
    is_edit: bool
