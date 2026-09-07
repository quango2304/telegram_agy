"""The ``bot`` service: long-poll Telegram, store every message it can see.

Exactly one replica — two processes long-polling the same token conflict.
"""

from __future__ import annotations

from collections.abc import Callable

from telegram.ext import Application, MessageHandler, filters

from src.application.ingest.ingest_handler import IngestHandler
from src.delivery.telegram.handlers import TelegramMessageHandler
from src.domain.interfaces.unit_of_work import IUnitOfWork
from src.infrastructure.config import get_settings
from src.infrastructure.container import Container
from src.infrastructure.db.migrate import upgrade_to_head
from src.shared.logger import get_logger

logger = get_logger(__name__)


def main() -> None:
    settings = get_settings()

    # The one place migrations are applied (step 3).
    upgrade_to_head()

    container = Container()
    ingest = IngestHandler(container.uow)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)  # a slow handler must not stall the poll loop
        .post_init(_register_handlers(ingest, container.uow))
        .build()
    )

    logger.info("starting long-polling")
    app.run_polling(
        allowed_updates=["message", "edited_message"],
        drop_pending_updates=True,  # a restart must not replay a backlog
    )


def _register_handlers(ingest: IngestHandler, uow_factory: Callable[[], IUnitOfWork]):
    async def _post_init(app: Application) -> None:
        me = await app.bot.get_me()
        logger.info("authenticated", extra={"bot_id": me.id, "username": me.username})
        handler = TelegramMessageHandler(ingest, uow_factory, me.id, me.username or "")
        app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, handler.on_message))

    return _post_init


if __name__ == "__main__":
    main()
