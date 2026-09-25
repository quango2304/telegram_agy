"""Run ``agy`` as an unprivileged subprocess with a scrubbed environment.

Every fact here was established by testing (agy 1.2.9):

- The prompt is an argv arg (stdin is ``/dev/null``); ``--print-timeout`` wants
  a duration string; ``--dangerously-skip-permissions`` is mandatory headless.
- ``status != "SUCCESS"`` or a non-empty ``denied_actions`` is a failure.
- **A print timeout is reported as success.** When ``--print-timeout`` fires
  mid-turn agy exits 0 with ``status: "SUCCESS"`` and an empty response; the
  only trace is a ``[agy] print timeout after …`` line on stderr. We look for
  that marker (backstop: wall time) and turn it into ``print_timeout``.
- A long shell command may be put in the background — even when the prompt
  says "foreground". If it is still running when the turn ends, agy waits for it
  up to the print timeout, kills it, and still reports SUCCESS with whatever the
  agent said before ("started it, will report back"). We call that
  ``background_timeout``: the work the user asked for was cut off.
- ``--output-format stream-json`` prints one NDJSON event per line: ``init``,
  then ``step_update`` (``ACTIVE`` / ``DONE`` per step, with ``tool_name``,
  ``tool_info.parameters``, ``duration_seconds``, ``usage``), then one ``result``
  whose payload is exactly what ``--output-format json`` prints. Steps are
  logged as they finish, so a run that times out shows what it was stuck on.
- ``--log-file`` is language-server noise with no tool trace — not useful.
- ``duration_seconds`` in the result is not wall time (6s reported for a 19s
  run), so elapsed time is measured here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pwd
import re
import shutil
import signal
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.infrastructure.config import Settings
from src.shared.logger import get_logger
from src.shared.persona import SECRET_GUARD

logger = get_logger(__name__)

# NOTE: in the worker, Celery owns the root logger and its formatter drops
# `extra=` — put every value you want to see into the message itself.

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

_PRINT_TIMEOUT_MARKER = "print timeout"
# stderr when the turn ended with a backgrounded command still running; agy
# waited for it up to the print timeout, then killed it.
_BACKGROUND_KILLED_RE = re.compile(r"terminating \d+ background task")
# Upstream blips worth one retry. Seen in prod: "Eligibility check failed:
# UNAVAILABLE (code 503)". Only matched against the stderr of a non-zero exit.
_TRANSIENT_RE = re.compile(r"UNAVAILABLE|RESOURCE_EXHAUSTED|\(code (?:429|500|502|503|504)\)")
# One stream-json line can carry a whole tool output (a file read, a web page).
_LINE_LIMIT = 32 * 1024 * 1024
_PARAM_LOG_CHARS = 200
# step_update keys that are bookkeeping, not content.
_STEP_ENVELOPE = frozenset(
    {"conversation_id", "step_index", "state", "step_type", "duration_seconds", "usage"}
)
_STDERR_LOG_CHARS = 1500
# After agy exits, how long a straggler child may keep the pipes open.
_DRAIN_GRACE_SECONDS = 5


@dataclass(frozen=True)
class AgyResult:
    ok: bool
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    # "" when ok, else one of: no_agy_user, timeout (our backstop fired),
    # print_timeout (agy's own limit, mid-turn), background_timeout (a
    # backgrounded command was killed at the limit), transient (upstream
    # 5xx/429 — safe to retry if nothing was sent), nonzero, no_result,
    # unsuccessful.
    error: str = ""
    elapsed_s: float = 0.0


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the whole process group. agy is started as a session leader, so
    its group id is its pid; this also reaps whatever composio / curl left
    running (which would otherwise hold the stdout pipe open)."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        proc.kill()


def _redact(value: Any) -> Any:
    """Drop session keys from tool parameters before they reach a log line."""
    if isinstance(value, dict):
        return {k: ("…" if "session_key" in k else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _fmt_params(params: Any) -> str:
    if not params:
        return ""
    text = json.dumps(_redact(params), ensure_ascii=False, default=str)
    return text if len(text) <= _PARAM_LOG_CHARS else text[:_PARAM_LOG_CHARS] + "…"


class _RunTrace:
    """Follows one run's stream-json events.

    Logs every model / tool step as it finishes (``agy step …``). Whatever is
    still ``ACTIVE`` when the run ends is what it was stuck on."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started = time.monotonic()
        self.conversation_id = ""
        self.result: dict[str, Any] | None = None
        self.active: dict[Any, str] = {}
        self.steps = 0
        self.tools: list[str] = []
        self.tokens = 0

    def feed(self, line: bytes) -> None:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("event")
        if kind == "init":
            self.conversation_id = str(event.get("conversation_id") or "")
        elif kind == "result":
            result = event.get("result")
            self.result = result if isinstance(result, dict) else {}
        elif kind == "step_update" and isinstance(event.get("step_update"), dict):
            self._step(event["step_update"])

    def _step(self, step: dict[str, Any]) -> None:
        step_type = step.get("step_type") or "?"
        if step_type == "user_input":
            return
        index = step.get("step_index")
        tool = step.get("tool_name") or ""
        desc = f"tool:{tool}" if tool else step_type
        if tool:
            params = (step.get("tool_info") or {}).get("parameters")
            desc = f"{desc} {_fmt_params(params)}".rstrip()
        elif step_type != "agent_response":
            # e.g. error_message — whatever it carries is the interesting part.
            extra = {k: v for k, v in step.items() if k not in _STEP_ENVELOPE}
            desc = f"{desc} {_fmt_params(extra)}".rstrip()
        at = time.monotonic() - self.started
        state = step.get("state") or "?"
        if state == "ACTIVE":
            self.active[index] = f"#{index} {desc} (since +{at:.0f}s)"
            return
        self.active.pop(index, None)
        self.steps += 1
        if tool:
            self.tools.append(tool)
        tokens = (step.get("usage") or {}).get("total_tokens") or 0
        self.tokens += tokens
        logger.info(
            "agy step %s #%s %s %s took=%.1fs%s at=+%.0fs",
            self.label,
            index,
            state,
            desc,
            float(step.get("duration_seconds") or 0),
            f" tokens={tokens}" if tokens else "",
            at,
        )

    def stuck(self) -> str:
        return "; ".join(self.active.values()) or "-"


class AgyClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def run(
        self,
        prompt: str,
        attachments: Sequence[Path] = (),
        *,
        timeout_s: int | None = None,
        label: str = "",
    ) -> AgyResult:
        """``attachments`` are copied into the run's workdir under their own
        basename, so the prompt can refer to them as plain relative paths — agy
        opens them with its own Read tool. They die with the workdir.

        ``timeout_s`` overrides ``agy_timeout_seconds`` (a retry passes what is
        left of the budget). ``label`` (e.g. ``thread_id=5``) prefixes every log
        line of this run so they can be grepped together."""
        limit_s = timeout_s or self._s.agy_timeout_seconds
        try:
            pw = pwd.getpwnam(self._s.agy_user)
        except KeyError:
            logger.error("agy user missing user=%s", self._s.agy_user)
            return AgyResult(ok=False, text="", raw={"error": "no_agy_user"}, error="no_agy_user")

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
                "stream-json",
                "--print-timeout",
                f"{limit_s}s",
                "--dangerously-skip-permissions",
            ]
            trace = _RunTrace(label)
            logger.info(
                "agy start %s timeout=%ss prompt_chars=%s attachments=%s",
                label,
                limit_s,
                len(prompt),
                len(attachments),
            )
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workdir,
                env=SCRUBBED_ENV,
                # Nothing may block on a prompt (npx "Ok to proceed?", git auth…).
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_LINE_LIMIT,
                start_new_session=True,  # own process group -> _kill_group reaps children
            )
            return await self._collect(proc, trace, limit_s)
        finally:
            # Always nuke the group: even on the happy path composio can leave a
            # tooling server / subagent alive.
            if proc is not None:
                _kill_group(proc)
            shutil.rmtree(workdir, ignore_errors=True)

    async def _collect(
        self, proc: asyncio.subprocess.Process, trace: _RunTrace, limit_s: int
    ) -> AgyResult:
        assert proc.stdout is not None and proc.stderr is not None
        stdout, stderr = proc.stdout, proc.stderr

        async def pump() -> None:
            while True:
                try:
                    line = await stdout.readline()
                except ValueError:  # a single line over _LINE_LIMIT — skip it
                    logger.warning("agy %s stream line over limit, skipped", trace.label)
                    continue
                if not line:
                    return
                trace.feed(line)

        pump_task = asyncio.ensure_future(pump())
        err_task = asyncio.ensure_future(stderr.read())
        backstop_hit = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=limit_s + 30)
        except TimeoutError:
            backstop_hit = True
            _kill_group(proc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
        # agy has exited. Drain what it wrote, but a straggler child still
        # holding the pipe must not hang the worker.
        _, pending = await asyncio.wait({pump_task, err_task}, timeout=_DRAIN_GRACE_SECONDS)
        if pending:
            _kill_group(proc)
            _, pending = await asyncio.wait(pending, timeout=2)
            for task in pending:
                task.cancel()
        err_text = ""
        if err_task.done() and not err_task.cancelled() and err_task.exception() is None:
            err_text = err_task.result().decode(errors="replace")
        elapsed = time.monotonic() - trace.started
        return self._classify(proc.returncode, err_text, trace, limit_s, elapsed, backstop_hit)

    def _classify(
        self,
        returncode: int | None,
        err_text: str,
        trace: _RunTrace,
        limit_s: int,
        elapsed: float,
        backstop_hit: bool,
    ) -> AgyResult:
        data = trace.result or {}
        denied = data.get("denied_actions") or []
        response = (data.get("response") or "").strip()
        if backstop_hit:
            error = "timeout"
        elif _PRINT_TIMEOUT_MARKER in err_text or (not response and elapsed >= limit_s):
            error = "print_timeout"
        elif returncode != 0:
            error = "transient" if _TRANSIENT_RE.search(err_text) else "nonzero"
        elif _BACKGROUND_KILLED_RE.search(err_text):
            error = "background_timeout"
        elif trace.result is None:
            error = "no_result"
        elif data.get("status") != "SUCCESS" or denied:
            error = "unsuccessful"
        else:
            error = ""
        ok = not error

        usage = data.get("usage") or {}
        summary = (
            "agy run finished %s outcome=%s elapsed=%.0fs limit=%ss exit=%s status=%s "
            "steps=%s tools=[%s] tokens=%s conversation_id=%s"
        )
        args: list[Any] = [
            trace.label,
            error or "ok",
            elapsed,
            limit_s,
            returncode,
            data.get("status") or "-",
            trace.steps,
            ",".join(trace.tools),
            usage.get("total_tokens") or trace.tokens,
            trace.conversation_id or data.get("conversation_id") or "-",
        ]
        if ok:
            logger.info(summary, *args)
        else:
            # What was still running is the answer to "why did it time out".
            summary += " stuck=%s denied=%s stderr=%s"
            tail = err_text[-_STDERR_LOG_CHARS:].strip().replace("\n", " | ") or "-"
            args += [trace.stuck(), json.dumps(denied, ensure_ascii=False)[:300], tail]
            logger.error(summary, *args)
        return AgyResult(
            ok=ok,
            text=response,
            raw=data if data else {"error": error},
            error=error,
            elapsed_s=elapsed,
        )
