"""Thin async wrapper over ``telegram.Bot`` for the worker.

A standalone Bot must be initialized before use (PTB v20+); we do it lazily and
keep the instance for the life of the task's event loop.
"""

from __future__ import annotations

from pathlib import Path

from telegram import Bot, ReplyParameters
from telegram.constants import ChatAction

from src.shared.logger import get_logger

logger = get_logger(__name__)


class TelegramSender:
    def __init__(self, token: str) -> None:
        self._bot = Bot(token)
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await self._bot.initialize()
            self._ready = True

    async def close(self) -> None:
        if self._ready:
            await self._bot.shutdown()
            self._ready = False

    async def send_typing(self, chat_id: int, topic_id: int) -> None:
        await self._ensure()
        try:
            await self._bot.send_chat_action(
                chat_id, ChatAction.TYPING, message_thread_id=topic_id or None
            )
        except Exception:
            logger.warning("send_chat_action failed", extra={"chat_id": chat_id})

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str | None) -> None:
        """React (or, with ``emoji=None``, clear our reaction). Best-effort: a
        group can restrict ``available_reactions``, and a message can be too old
        or already deleted — none of that should fail a reply."""
        await self._ensure()
        try:
            await self._bot.set_message_reaction(
                chat_id=chat_id, message_id=message_id, reaction=(emoji or None)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "set_message_reaction failed chat_id=%s message_id=%s err=%s",
                chat_id,
                message_id,
                exc,
            )

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        await self._ensure()
        await self._bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)

    async def download_media(self, file_id: str, dest: Path, max_bytes: int) -> bool:
        """Fetch one attachment to ``dest``. Returns False (and logs) rather than
        raising: a run must still answer when the file is gone or oversized.

        Bot API ``getFile`` refuses anything over 20 MB, so the size check is
        mostly about not wasting the round trip."""
        await self._ensure()
        try:
            tg_file = await self._bot.get_file(
                file_id, read_timeout=60, connect_timeout=30, pool_timeout=30
            )
            size = tg_file.file_size or 0
            if size > max_bytes:
                logger.info("media too large file_size=%s max=%s", size, max_bytes)
                return False
            await tg_file.download_to_drive(
                custom_path=dest, read_timeout=120, connect_timeout=30, pool_timeout=30
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("media download failed file_id=%s err=%s", file_id[:24], exc)
            return False

    async def send_reply(
        self, chat_id: int, text: str, topic_id: int, reply_to_message_id: int | None
    ) -> int:
        await self._ensure()
        reply_params = (
            ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
            if reply_to_message_id is not None
            else None
        )
        msg = await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=topic_id or None,
            reply_parameters=reply_params,
        )
        return msg.message_id

    async def send_file(
        self,
        chat_id: int,
        path: Path,
        caption: str | None,
        topic_id: int,
        reply_to_message_id: int | None,
    ) -> int:
        await self._ensure()
        reply_params = (
            ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
            if reply_to_message_id is not None
            else None
        )
        with path.open("rb") as fh:
            msg = await self._bot.send_document(
                chat_id=chat_id,
                document=fh,
                filename=path.name,
                caption=(caption or None),
                message_thread_id=topic_id or None,
                reply_parameters=reply_params,
                # Uploads over a slow container link routinely blow the 20s
                # default and make callers retry a file Telegram already got.
                write_timeout=120,
                read_timeout=120,
                connect_timeout=30,
            )
        return msg.message_id
