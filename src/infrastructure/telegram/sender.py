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
