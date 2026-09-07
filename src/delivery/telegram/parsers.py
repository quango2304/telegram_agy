"""Pure functions that turn a PTB ``Update`` into an ingest command.

No I/O, no DB — trivially testable.
"""

from __future__ import annotations

from telegram import Chat, Message, Update

from src.application.ingest.commands import IncomingMessage

_UNKNOWN_AUTHOR = "ai đó"


def resolve_text(message: Message) -> str:
    """``messages.text`` is not-null. Resolve text, then caption, then a
    Vietnamese placeholder by media kind. Media itself is not fetched in v1."""
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.photo:
        return "[ảnh]"
    if message.video or message.video_note:
        return "[video]"
    if message.animation:
        return "[gif]"
    if message.sticker:
        return f"[sticker: {message.sticker.emoji or '❓'}]"
    if message.voice:
        return "[voice]"
    if message.audio:
        return "[audio]"
    if message.document:
        return f"[file: {message.document.file_name or 'tệp'}]"
    if message.location:
        return "[vị trí]"
    if message.venue:
        return "[địa điểm]"
    if message.contact:
        return "[danh thiếp]"
    if message.poll:
        return f"[bình chọn: {message.poll.question}]"
    if message.dice:
        return f"[dice: {message.dice.value}]"
    return "[…]"


def resolve_author_name(message: Message) -> str | None:
    user = message.from_user
    if user is None:
        return None
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return _UNKNOWN_AUTHOR


def resolve_topic_id(chat: Chat, message: Message) -> int:
    """Only forums have topics. ``message_thread_id`` is also set on ordinary
    replies in some clients, which would otherwise shatter one group into many
    threads. The forum "General" topic omits the id and correctly lands on 0."""
    if getattr(chat, "is_forum", False) and message.message_thread_id:
        return int(message.message_thread_id)
    return 0


def resolve_chat_title(chat: Chat) -> str | None:
    if chat.title:
        return chat.title
    if chat.full_name:
        return chat.full_name
    if chat.username:
        return f"@{chat.username}"
    return None


def to_incoming_message(update: Update) -> IncomingMessage | None:
    """Return the ingest command for a message/edited_message update, or ``None``
    if this update carries nothing we store."""
    message = update.edited_message or update.message
    if message is None or update.effective_chat is None:
        return None

    chat = update.effective_chat
    return IncomingMessage(
        chat_id=chat.id,
        topic_id=resolve_topic_id(chat, message),
        chat_type=str(chat.type),
        chat_title=resolve_chat_title(chat),
        tg_message_id=message.message_id,
        from_user_id=message.from_user.id if message.from_user else None,
        from_username=message.from_user.username if message.from_user else None,
        from_name=resolve_author_name(message),
        text=resolve_text(message),
        sent_at=message.date,
        is_edit=update.edited_message is not None,
    )
