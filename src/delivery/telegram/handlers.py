"""PTB message handler — an entry point only.

Parse the update into a command, hand it to the ingest use case, then let step 5
decide whether to enqueue a reply. A failure storing one message is logged and
swallowed so the poll loop keeps running.
"""

from __future__ import annotations

from collections.abc import Callable

from telegram import Update
from telegram.ext import ContextTypes

from src.application.ingest.commands import IncomingMessage, IngestResult
from src.application.ingest.ingest_handler import IngestHandler
from src.delivery.telegram.parsers import to_incoming_message
from src.delivery.telegram.triggers import is_trigger
from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.tasks import generate_reply
from src.shared.logger import get_logger

logger = get_logger(__name__)


class TelegramMessageHandler:
    def __init__(
        self,
        ingest_handler: IngestHandler,
        uow_factory: Callable[[], IUnitOfWork],
        bot_id: int,
        bot_username: str,
    ) -> None:
        self._ingest = ingest_handler
        self._uow_factory = uow_factory
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
        """Trigger detection + Celery dispatch. Only the DB row id is enqueued —
        the worker fetches history at execution time."""
        if result.is_edit or result.message_id is None:
            return  # edits never trigger a reply

        message = update.effective_message
        if message is None or not is_trigger(message, self._bot_id, self._bot_username):
            return

        # Persist the trigger flag BEFORE enqueueing: the worker may dequeue and
        # read pending_triggers before this commit otherwise, and see nothing.
        try:
            async with self._uow_factory() as uow:
                await uow.messages.mark_trigger(result.message_id)
                await uow.commit()
        except Exception:
            logger.exception("mark_trigger failed", extra={"message_id": result.message_id})
            # The runner falls back to the ref message, so still enqueue.

        generate_reply.apply_async(args=[result.message_id], queue="replies")
        logger.info(
            "reply enqueued",
            extra={"message_id": result.message_id, "thread_id": result.thread_id},
        )
