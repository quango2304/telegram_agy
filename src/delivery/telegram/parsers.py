"""Pure functions that turn a PTB ``Update`` into an ingest command.

No I/O, no DB — trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from telegram import Chat, Message, Update

from src.application.ingest.commands import IncomingMessage

_UNKNOWN_AUTHOR = "ai đó"


@dataclass(frozen=True)
class MediaRef:
    """What is attached to a message, as ids only — no bytes are fetched here."""

    kind: str
    file_id: str
    mime: str | None = None
    file_name: str | None = None


def resolve_media(message: Message) -> MediaRef | None:
    """The one attachment worth remembering, or ``None``.

    ``message.photo`` is a tuple of sizes, smallest → largest; the last entry is
    the full-resolution one. Only kinds a model can actually open are worth a
    ``file_id``: stickers (Lottie/webm), video and audio are recorded by kind so
    the history line stays honest, but are never downloaded (see
    ``is_image_media``)."""
    if message.photo:
        return MediaRef(kind="photo", file_id=message.photo[-1].file_id, mime="image/jpeg")
    if message.document is not None:
        doc = message.document
        return MediaRef(
            kind="document",
            file_id=doc.file_id,
            mime=doc.mime_type,
            file_name=doc.file_name,
        )
    if message.voice is not None:
        return MediaRef(kind="voice", file_id=message.voice.file_id, mime=message.voice.mime_type)
    if message.video is not None:
        return MediaRef(kind="video", file_id=message.video.file_id, mime=message.video.mime_type)
    if message.audio is not None:
        return MediaRef(kind="audio", file_id=message.audio.file_id, mime=message.audio.mime_type)
    if message.animation is not None:
        return MediaRef(kind="animation", file_id=message.animation.file_id)
    if message.video_note is not None:
        return MediaRef(kind="video_note", file_id=message.video_note.file_id)
    if message.sticker is not None:
        return MediaRef(kind="sticker", file_id=message.sticker.file_id)
    return None


def is_image_media(kind: str | None, mime: str | None) -> bool:
    """Only these are handed to ``agy``. A Telegram photo is always a JPEG; a
    document counts when it declares an ``image/*`` mime. Audio is excluded
    deliberately — verified that ``agy`` cannot transcribe ogg/opus or mp3."""
    if kind == "photo":
        return True
    return kind == "document" and bool(mime) and str(mime).startswith("image/")


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
    media = resolve_media(message)
    reply_to = message.reply_to_message
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
        reply_to_tg_message_id=(reply_to.message_id if reply_to is not None else None),
        media_kind=(media.kind if media else None),
        media_file_id=(media.file_id if media else None),
        media_mime=(media.mime if media else None),
        media_file_name=(media.file_name if media else None),
    )
