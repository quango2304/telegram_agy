"""Composition root (KB ``06-dependency-injection.md``).

Both the Celery worker and the Telegram poller build this at process start; the
MCP entrypoint wires its own providers. Providers are added by the step that
introduces the collaborator they wrap — this file grows as the system does.
"""

from __future__ import annotations

from dependency_injector import containers, providers

from src.infrastructure.config import get_settings
from src.infrastructure.db.engine import build_engine, build_session_factory
from src.infrastructure.db.uow import SqlAlchemyUnitOfWork


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(get_settings)

    # NOTE: an async engine binds to the event loop that first touches it. The
    # Celery worker runs each task in its own ``asyncio.run`` loop, so it builds
    # a fresh engine per task (see src/infrastructure/tasks.py) instead of this
    # singleton. The poller and MCP server, which have one long-lived loop, use
    # this provider.
    engine = providers.Singleton(build_engine, settings.provided.database_url)
    session_factory = providers.Singleton(build_session_factory, engine)

    uow = providers.Factory(SqlAlchemyUnitOfWork, session_factory)
