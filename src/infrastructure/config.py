"""Typed application settings.

Dynaconf loads ``.env`` + the process environment; a plain frozen ``@dataclass``
exposes it typed. Everything downstream reads :func:`get_settings`, never
``os.environ`` (KB ``04-config-secrets.md`` — deliberately not pydantic-settings).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from dynaconf import Dynaconf

_dc = Dynaconf(load_dotenv=True, envvar_prefix=False)


def _str(key: str, default: str = "") -> str:
    val = _dc.get(key, default)
    return str(val) if val is not None else default


def _int(key: str, default: int) -> int:
    val = _dc.get(key, default)
    return int(val) if val is not None else default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_bot_username: str  # without the leading @, used to strip mentions
    database_url: str
    celery_broker_url: str
    celery_result_backend: str
    redis_url: str

    agy_model: str = "gemini-3.8-flash-low"
    agy_timeout_seconds: int = 180
    agy_binary: str = "/usr/local/bin/agy"
    agy_user: str = "agy"
    agy_mcp_url: str = "http://mcp:8000/mcp"

    mcp_port: int = 8000
    context_message_limit: int = 20
    session_ttl_seconds: int = 600
    telegram_max_chars: int = 4096
    agy_prompt_max_chars: int = 60_000
    agy_message_truncate_chars: int = 2_000

    log_level: str = "INFO"
    log_format: str = "plain"  # plain | json


@lru_cache
def get_settings() -> Settings:
    token = _str("TELEGRAM_BOT_TOKEN")
    database_url = _str("DATABASE_URL")
    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("DATABASE_URL", database_url))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Required settings are empty: {', '.join(missing)}. Check your .env.")

    return Settings(
        telegram_bot_token=token,
        telegram_bot_username=_str("TELEGRAM_BOT_USERNAME").lstrip("@"),
        database_url=database_url,
        celery_broker_url=_str("CELERY_BROKER_URL", "redis://redis:6379/0"),
        celery_result_backend=_str("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
        redis_url=_str("REDIS_URL", "redis://redis:6379/2"),
        agy_model=_str("AGY_MODEL", "gemini-3.8-flash-low"),
        agy_timeout_seconds=_int("AGY_TIMEOUT_SECONDS", 180),
        agy_binary=_str("AGY_BINARY", "/usr/local/bin/agy"),
        agy_user=_str("AGY_USER", "agy"),
        agy_mcp_url=_str("AGY_MCP_URL", "http://mcp:8000/mcp"),
        mcp_port=_int("MCP_PORT", 8000),
        context_message_limit=_int("CONTEXT_MESSAGE_LIMIT", 20),
        session_ttl_seconds=_int("SESSION_TTL_SECONDS", 600),
        telegram_max_chars=_int("TELEGRAM_MAX_CHARS", 4096),
        agy_prompt_max_chars=_int("AGY_PROMPT_MAX_CHARS", 60_000),
        agy_message_truncate_chars=_int("AGY_MESSAGE_TRUNCATE_CHARS", 2_000),
        log_level=_str("LOG_LEVEL", "INFO"),
        log_format=_str("LOG_FORMAT", "plain"),
    )
