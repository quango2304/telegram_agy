"""The ingest use case: upsert the thread, store (or update) the message.

One class, one method per operation (KB rules §5). No trigger logic here — that
is step 5, in the delivery layer.
"""

from __future__ import annotations

from collections.abc import Callable

from src.application.ingest.commands import IncomingMessage, IngestResult
from src.domain.entities.message import Message
from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.shared.logger import get_logger

logger = get_logger(__name__)


class IngestHandler:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def ingest(self, msg: IncomingMessage) -> IngestResult:
        async with self._uow_factory() as uow:
            thread = await uow.threads.get_or_create(
                chat_id=msg.chat_id,
                topic_id=msg.topic_id,
                chat_type=msg.chat_type,
                title=msg.chat_title,
            )

            if msg.is_edit:
                await uow.messages.update_text(thread.id, msg.tg_message_id, msg.text)
                await uow.commit()
                return IngestResult(thread_id=thread.id, message_id=None, is_edit=True)

            row = await uow.messages.add(
                Message(
                    thread_id=thread.id,
                    tg_message_id=msg.tg_message_id,
                    from_user_id=msg.from_user_id,
                    from_username=msg.from_username,
                    from_name=msg.from_name,
                    is_bot_self=False,
                    text=msg.text,
                    sent_at=msg.sent_at,
                )
            )
            await uow.commit()
            return IngestResult(thread_id=thread.id, message_id=row.id, is_edit=False)
