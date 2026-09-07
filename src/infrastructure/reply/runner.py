"""Entry point for the ``generate_reply`` task body.

Implemented in step 6 (Redis lock, prompt builder, ``agy`` subprocess, send).
"""

from __future__ import annotations

from typing import Any


def run_generate_reply(task: Any, message_id: int) -> None:
    raise NotImplementedError("step 6")
