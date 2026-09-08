"""Run ``agy`` as an unprivileged subprocess with a scrubbed environment.

Every fact here was established by testing:
the prompt is an argv arg (no stdin); ``--print-timeout`` wants a duration
string; ``status != "SUCCESS"`` or a non-empty ``denied_actions`` is a failure;
``--dangerously-skip-permissions`` is mandatory in headless mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pwd
import shutil
import signal
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.infrastructure.config import Settings
from src.shared.logger import get_logger
from src.shared.persona import SECRET_GUARD

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
    # Shared dir for files agy wants sent to chat (see send_chat_file).
    "AGY_OUTBOX": "/outbox",
}


@dataclass(frozen=True)
class AgyResult:
    ok: bool
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the whole process group. agy is started as a session leader, so
    its group id is its pid; this also reaps whatever composio / curl left
    running (which would otherwise hold the stdout pipe open and hang
    communicate() forever)."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        proc.kill()


class AgyClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def run(self, prompt: str, attachments: Sequence[Path] = ()) -> AgyResult:
        """``attachments`` are copied into the run's workdir under their own
        basename, so the prompt can refer to them as plain relative paths — agy
        opens them with its own Read tool. They die with the workdir."""
        try:
            pw = pwd.getpwnam(self._s.agy_user)
        except KeyError:
            logger.error("agy user missing", extra={"user": self._s.agy_user})
            return AgyResult(ok=False, text="", raw={"error": "no_agy_user"})

        workdir = tempfile.mkdtemp(prefix="agy-")
        proc: asyncio.subprocess.Process | None = None
        try:
            os.chown(workdir, pw.pw_uid, pw.pw_gid)
            for src in attachments:
                # Root copies the file in, so hand each one to the agy uid or the
                # unprivileged process can't open what we just told it to read.
                dst = os.path.join(workdir, os.path.basename(src))
                shutil.copyfile(src, dst)
                os.chown(dst, pw.pw_uid, pw.pw_gid)
                os.chmod(dst, 0o644)
            # agy walks up from cwd loading AGENTS.md as a hard workspace rule.
            # Drop the secret-guard here too — third copy, and the strongest
            # framing (a rule, not a request in the prompt body).
            agents_md = os.path.join(workdir, "AGENTS.md")
            # tiny one-shot write to a fresh tmpfile; same blocking-fs style as
            # the mkdtemp/chown calls around it.
            with open(agents_md, "w", encoding="utf-8") as fh:  # noqa: ASYNC230
                fh.write(f"# Quy tắc bắt buộc\n\n{SECRET_GUARD}\n")
            os.chown(agents_md, pw.pw_uid, pw.pw_gid)
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
                start_new_session=True,  # own process group -> _kill_group reaps children
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=self._s.agy_timeout_seconds + 30
                )
            except TimeoutError:
                _kill_group(proc)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=5)
                logger.error("agy timed out")
                return AgyResult(ok=False, text="", raw={"error": "timeout"})

            if proc.returncode != 0:
                tail = err.decode(errors="replace")[-1500:].strip()
                logger.error("agy exited non-zero code=%s stderr=%s", proc.returncode, tail)
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
            # Always nuke the group: even on the happy path composio can leave a
            # tooling server / subagent alive.
            if proc is not None:
                _kill_group(proc)
            shutil.rmtree(workdir, ignore_errors=True)
