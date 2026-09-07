"""Programmatic ``alembic upgrade head``.

Called once, from the ``bot`` entrypoint only (step 3), so the services do not
race on the migration lock.
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from src.shared.logger import get_logger, reconfigure

logger = get_logger(__name__)

_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def upgrade_to_head() -> None:
    cfg = Config(str(_ALEMBIC_INI))
    url = os.environ.get("ALEMBIC_DATABASE_URL")
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    logger.info("running alembic upgrade head")
    try:
        command.upgrade(cfg, "head")
    finally:
        # Alembic's env.py runs fileConfig(), which clears the root logger.
        reconfigure()
    logger.info("alembic upgrade complete")
