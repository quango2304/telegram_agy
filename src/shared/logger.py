"""Single logging entry point (KB ``12-logging.md``).

Never ``print()``, never bare ``logging.getLogger()`` elsewhere — call
:func:`get_logger`. Format is driven by ``LOG_FORMAT`` (``plain`` for dev,
``json`` for prod).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_CONFIGURED = False

# A Bot API URL carries the token in its path (``/bot<id>:<secret>/getUpdates``,
# ``/file/bot<id>%3A<secret>/photos/...``). httpx logs every request URL at
# INFO, and on prod that put the token into journald ~170k times before
# 2026-10-16. ``_configure`` silences those request lines; this filter is the
# backstop for anything else that quotes the URL, an exception message included.
_BOT_TOKEN = re.compile(r"bot(\d+)(?::|%3A)[A-Za-z0-9_-]{20,}")


def _redact(text: str) -> str:
    return _BOT_TOKEN.sub(r"bot\1:<redacted>", text)


class RedactTokenFilter(logging.Filter):
    """Strips bot tokens from a record before any formatter sees it. Attached
    to handlers, not loggers, so records propagated from library loggers are
    covered too."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _redact(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        if record.exc_info and not record.exc_text:
            record.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
        return True


def protect_handlers(logger: logging.Logger) -> None:
    """Silence httpx's per-request INFO lines and redact tokens on every handler
    of ``logger``. Celery replaces the root handlers in the worker and beat, so
    ``celery_app`` calls this again from its ``after_setup_logger`` signal."""
    # httpx2 is the MCP SDK's fork of httpx, with the same request logging.
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for handler in logger.handlers:
        if not any(isinstance(f, RedactTokenFilter) for f in handler.filters):
            handler.addFilter(RedactTokenFilter())


_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _PlainFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")
        }
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


def _configure(force: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    # Imported lazily to avoid a circular import at module load.
    from src.infrastructure.config import get_settings

    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if settings.log_format == "json" else _PlainFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    protect_handlers(root)
    _CONFIGURED = True


def reconfigure() -> None:
    """Re-assert our handlers after something else (e.g. Alembic's ``fileConfig``)
    has stomped the root logger."""
    _configure(force=True)


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(name)
