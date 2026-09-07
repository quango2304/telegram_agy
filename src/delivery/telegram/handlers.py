"""PTB message handler — an entry point only.

Parse the update into a command, hand it to the ingest use case, then let step 5
decide whether to enqueue a reply. A failure storing one message is logged and
swallowed so the poll loop keeps running.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from src.application.ingest.commands import IncomingMessage, IngestResult
from src.application.ingest.ingest_handler import IngestHandler
from src.delivery.telegram.parsers import to_incoming_message
from src.shared.logger import get_logger

logger = get_logger(__name__)


class TelegramMessageHandler:
    def __init__(self, ingest_handler: IngestHandler, bot_id: int, bot_username: str) -> None:
        self._ingest = ingest_handler
        self._bot_id = bot_id
        self._bot_username = bot_username

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cmd = to_incoming_message(update)
        if cmd is None:
            return

        try:
            result = await self._ingest.ingest(cmd)
        except Exception:
            chat = update.effective_chat
            msg = update.effective_message
            logger.exception(
                "ingest failed",
                extra={
                    "chat_id": chat.id if chat else None,
                    "message_id": msg.message_id if msg else None,
                },
            )
            return

        await self._after_ingest(update, cmd, result)

    async def _after_ingest(
        self, update: Update, cmd: IncomingMessage, result: IngestResult
    ) -> None:
        """Step 5 fills this in (trigger detection + Celery dispatch)."""
        return
