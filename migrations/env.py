"""Async-aware Alembic environment (KB ``07-migrations.md``).

Migrations run through the **sync** ``ALEMBIC_DATABASE_URL`` (psycopg2); the app
itself uses asyncpg. Every entity is imported so ``--autogenerate`` sees the
tables.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import every entity for autogenerate metadata.
from src.domain.entities.agy_session import AgySession  # noqa: F401
from src.domain.entities.base import Base
from src.domain.entities.chat_thread import ChatThread  # noqa: F401
from src.domain.entities.message import Message  # noqa: F401
from src.domain.entities.thread_memory import ThreadMemory  # noqa: F401

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True and would silence the app's
    # loggers for the rest of the process (bot.py runs this in-process).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

_url = os.environ.get("ALEMBIC_DATABASE_URL")
if _url:
    config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
