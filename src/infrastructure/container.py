"""Composition root (KB ``06-dependency-injection.md``).

Both the Celery worker and the Telegram poller build this at process start; the
MCP entrypoint wires its own providers. Providers are added by the step that
introduces the collaborator they wrap — this file grows as the system does.
"""

from __future__ import annotations

from dependency_injector import containers, providers

from src.infrastructure.config import get_settings


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(get_settings)
