"""Run ``agy`` as an unprivileged subprocess with a scrubbed environment.

Every fact here was established by testing (see ``specs/overal.md``):
the prompt is an argv arg (no stdin); ``--print-timeout`` wants a duration
string; ``status != "SUCCESS"`` or a non-empty ``denied_actions`` is a failure;
``--dangerously-skip-permissions`` is mandatory in headless mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any

from src.infrastructure.config import Settings
from src.shared.logger import get_logger

logger = get_logger(__name__)

# Resolved from the parent's PATH (SCRUBBED_ENV's PATH is intentionally narrow
# and does not include /usr/sbin where the gosu package lands).
_GOSU = shutil.which("gosu") or "/usr/sbin/gosu"

# Built from scratch — NOT os.environ minus keys. agy runs with
# --dangerously-skip-permissions, so any group member's text can become a shell
# command; DATABASE_URL / TELEGRAM_BOT_TOKEN / REDIS_URL / POSTGRES_* must be
# unreachable from that process.
SCRUBBED_ENV = {
    "HOME": "/home/agy",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "USER": "agy",
    "LANG": "C.UTF-8",
    "TERM": "dumb",
}


@dataclass(frozen=True)
class AgyResult:
    ok: bool
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


class AgyClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def run(self, prompt: str) -> AgyResult:
        try:
            pw = pwd.getpwnam(self._s.agy_user)
        except KeyError:
            logger.error("agy user missing", extra={"user": self._s.agy_user})
            return AgyResult(ok=False, text="", raw={"error": "no_agy_user"})

        workdir = tempfile.mkdtemp(prefix="agy-")
        try:
            os.chown(workdir, pw.pw_uid, pw.pw_gid)
            cmd = [
                _GOSU,
                self._s.agy_user,
                self._s.agy_binary,
                "-p",
                prompt,
                "--model",
                self._s.agy_model,
                "--output-format",
                "json",
                "--print-timeout",
                f"{self._s.agy_timeout_seconds}s",
                "--dangerously-skip-permissions",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workdir,
                env=SCRUBBED_ENV,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=self._s.agy_timeout_seconds + 30
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error("agy timed out")
                return AgyResult(ok=False, text="", raw={"error": "timeout"})

            if proc.returncode != 0:
                logger.error(
                    "agy exited non-zero",
                    extra={"code": proc.returncode, "stderr": err.decode(errors="replace")[:2000]},
                )
                return AgyResult(ok=False, text="", raw={"error": "nonzero"})

            try:
                data = json.loads(out.decode())
            except json.JSONDecodeError:
                logger.error(
                    "agy output not JSON",
                    extra={"stdout": out.decode(errors="replace")[:2000]},
                )
                return AgyResult(ok=False, text="", raw={"error": "bad_json"})

            denied = data.get("denied_actions") or []
            ok = data.get("status") == "SUCCESS" and not denied
            if not ok:
                logger.error("agy call unsuccessful", extra={"agy": json.dumps(data)[:2000]})
            return AgyResult(ok=ok, text=(data.get("response") or "").strip(), raw=data)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
