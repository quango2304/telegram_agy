# Step 2 — Settings, logging, DI container

## Goal
One typed `Settings` object, one `get_logger()`, one DI container. Everything after
this reads config from `Settings`, never `os.environ`.

## `src/infrastructure/config.py`

Dynaconf loads `.env` + environment; a plain `@dataclass` exposes it typed (KB
`04-config-secrets.md` — deliberately *not* pydantic-settings).

```python
@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
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

    log_level: str = "INFO"
    log_format: str = "plain"   # plain | json


@lru_cache
def get_settings() -> Settings: ...
```

Fail loudly at import if `TELEGRAM_BOT_TOKEN` or `DATABASE_URL` is empty.

## `src/shared/logger.py`

`get_logger(name)` per KB `12-logging.md`. Plain formatter for dev, JSON for prod,
driven by `log_format`. Never `print()`, never bare `logging.getLogger()`.

## `src/shared/persona.py`

The A Khôn persona in **one** constant so it can be tuned without hunting:

```python
PERSONA_PROMPT = """Bạn là A Khôn — một con bot trong nhóm chat Telegram.
Luôn trả lời bằng tiếng Việt (trừ khi người ta nhắn bằng tiếng khác thì trả lời bằng tiếng đó).
Giọng hài hước, châm biếm nhẹ, xưng hô thân mật như bạn bè, rành slang giới trẻ.
Trả lời NGẮN như đang chat — không markdown, không bullet, không tiêu đề, không emoji spam.
Đừng lặp lại câu hỏi, trả lời thẳng."""
```

> This exact style was verified against `gemini-3.8-flash-low` and produces the right
> register. If it is edited, re-test before shipping.

## `src/infrastructure/container.py`

`dependency-injector` `DeclarativeContainer` (KB `06-dependency-injection.md`):

- `settings` — `Singleton(get_settings)`
- `engine`, `session_factory` — `Singleton`
- `uow` — `Factory`
- repositories — `Factory`
- `agy_client` — `Singleton(AgyClientImpl, ...)`
- `telegram_sender` — `Singleton`

Both the Celery worker and the poller build the container at process start; there is
no FastAPI wiring to do except in the MCP entrypoint.

## Done when
`make verify` passes and `python -c "from src.infrastructure.config import get_settings; print(get_settings().agy_model)"` prints the model.
