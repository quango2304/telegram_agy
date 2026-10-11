"""Extra MCP servers for ``agy``, from ``config/mcp_servers.json``.

Run once at worker start (``src.entrypoints.agy_mcp_setup``): validate the file,
health-check every enabled server, and write agy's own ``mcp_config.json`` with
our ``memory`` server plus the servers that answered. Each server's optional
``prompt`` (when to use it) goes to :data:`PROMPTS_PATH` for the prompt builder,
again only for servers that answered — never tell the model about a tool it
doesn't have.

Facts this relies on (agy 1.3.3, tested):

- agy reads ``~/.gemini/config/mcp_config.json`` on every run. The shape below
  (``command``/``args``/``env`` or ``serverUrl``/``headers``, plus ``disabled``)
  is exactly what ``agy mcp add`` writes.
- A server that fails fast (connection refused, command exits) is skipped
  silently and costs nothing. A server that **hangs** — accepts the connection
  and never answers, or a command that never speaks — blocks the run until the
  print timeout, so every reply fails. That is why we check before registering.
- Every enabled server's tool list goes into every prompt.

The file is rebuilt from scratch each start, so a server removed from
``mcp_servers.json`` does not survive a ``docker compose restart worker``.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import anyio
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from src.infrastructure.agy.agy_client_impl import _GOSU, SCRUBBED_ENV
from src.shared.logger import get_logger

logger = get_logger(__name__)

# Our own server (src/delivery/mcp). Name-coupled to the prompt and docs.
RESERVED_NAME = "memory"
# A first `npx -y` / `uvx` on a fresh container downloads the package.
LOCAL_CHECK_SECONDS = 60
REMOTE_CHECK_SECONDS = 10
# Root-only (not under /home/agy): the agy user must not be able to rewrite its
# own instructions. Written by the worker entrypoint, read by the reply runner.
PROMPTS_PATH = "/var/lib/telegram_agy/mcp_prompts.json"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCAL_KEYS = frozenset({"command", "args", "env", "disabled", "type", "prompt"})
_REMOTE_KEYS = frozenset({"url", "headers", "disabled", "type", "prompt"})
# READMEs paste one of these as "type"; all mean the same transport to agy.
_LOCAL_TYPES = frozenset({"stdio"})
_REMOTE_TYPES = frozenset({"http", "streamable-http", "streamableHttp"})
_STDERR_TAIL_CHARS = 300


class ConfigError(Exception):
    """``mcp_servers.json`` is malformed. Carries every problem found."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    # When/how to use this server, added to the run prompt's tool rules.
    prompt: str = ""

    @property
    def is_remote(self) -> bool:
        return bool(self.url)

    @property
    def target(self) -> str:
        """What to show in a log line: the host for a remote (never the full
        URL — a token may ride in the query), the command for a local."""
        return urlsplit(self.url).netloc if self.is_remote else self.command

    def to_agy(self) -> dict[str, Any]:
        if self.is_remote:
            return {"disabled": False, "headers": dict(self.headers), "serverUrl": self.url}
        return {
            "args": list(self.args),
            "command": self.command,
            "disabled": False,
            "env": dict(self.env),
        }


def _str_map(value: Any, where: str, errors: list[str]) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        errors.append(f"{where} must be an object of string values")
        return {}
    return dict(value)


def _parse_entry(name: str, entry: Any, errors: list[str]) -> ServerSpec | None:
    where = f"server '{name}'"
    if not isinstance(entry, dict):
        errors.append(f"{where} must be an object")
        return None
    has_cmd, has_url = "command" in entry, "url" in entry
    if has_cmd == has_url:
        errors.append(f"{where} needs exactly one of 'command' (local) or 'url' (remote)")
        return None
    allowed = _REMOTE_KEYS if has_url else _LOCAL_KEYS
    unknown = sorted(set(entry) - allowed)
    if unknown:
        errors.append(f"{where} has unknown field(s) {unknown}; allowed: {sorted(allowed)}")
    n_errors = len(errors)

    kind = entry.get("type")
    if kind is not None and kind not in (_REMOTE_TYPES if has_url else _LOCAL_TYPES):
        hint = " (SSE-only servers are not supported)" if kind == "sse" else ""
        errors.append(f"{where} has type {kind!r}, which doesn't match its fields{hint}")
    disabled = entry.get("disabled", False)
    if not isinstance(disabled, bool):
        errors.append(f"{where}: 'disabled' must be true or false")
    prompt = entry.get("prompt", "")
    if not isinstance(prompt, str):
        errors.append(f"{where}: 'prompt' must be a string")
        prompt = ""
    common: dict[str, Any] = {"name": name, "disabled": disabled is True, "prompt": prompt.strip()}

    if has_url:
        url = entry["url"]
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
            errors.append(f"{where}: 'url' must be an http(s) URL")
            url = ""
        headers = _str_map(entry.get("headers", {}), f"{where}: 'headers'", errors)
        spec = ServerSpec(url=url, headers=headers, **common)
    else:
        command = entry["command"]
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{where}: 'command' must be a non-empty string")
            command = ""
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            errors.append(f"{where}: 'args' must be a list of strings")
            args = []
        env = _str_map(entry.get("env", {}), f"{where}: 'env'", errors)
        spec = ServerSpec(command=command, args=tuple(args), env=env, **common)
    return spec if len(errors) == n_errors and not unknown else None


def parse_servers(raw: Any) -> list[ServerSpec]:
    """Validate the decoded JSON. Pure. Raises :class:`ConfigError` listing
    every problem, so one restart shows them all."""
    errors: list[str] = []
    if not isinstance(raw, dict) or set(raw) != {"mcpServers"}:
        raise ConfigError(['top level must be {"mcpServers": {...}} and nothing else'])
    servers = raw["mcpServers"]
    if not isinstance(servers, dict):
        raise ConfigError(["'mcpServers' must be an object of name -> server"])
    specs: list[ServerSpec] = []
    for name, entry in servers.items():
        if name == RESERVED_NAME:
            errors.append(f"server name '{RESERVED_NAME}' is reserved for the bot's own server")
            continue
        if not _NAME_RE.match(name):
            errors.append(f"server name {name!r} must be 1-64 of A-Z a-z 0-9 _ -")
            continue
        spec = _parse_entry(name, entry, errors)
        if spec is not None:
            specs.append(spec)
    if errors:
        raise ConfigError(errors)
    return specs


def load_servers(path: str) -> list[ServerSpec]:
    """``[]`` when the file doesn't exist — external servers are optional."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError([f"not valid JSON: {exc}"]) from exc
    return parse_servers(raw)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    took_s: float
    tools: int = 0
    reason: str = ""


def _leaf(exc: BaseException) -> BaseException:
    """anyio task groups wrap the real error in (nested) ExceptionGroups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _local_params(spec: ServerSpec, agy_user: str) -> StdioServerParameters:
    """Launch exactly as agy will see it: as the agy user, with SCRUBBED_ENV
    plus the entry's env and nothing inherited from this (root, secret-laden)
    process. The SDK merges our PATH/HOME into ``env``; ``env -i`` drops them."""
    pairs = [f"{k}={v}" for k, v in (SCRUBBED_ENV | spec.env).items()]
    return StdioServerParameters(
        command=_GOSU,
        args=[agy_user, "env", "-i", *pairs, spec.command, *spec.args],
        cwd="/tmp",
    )


async def check_server(spec: ServerSpec, agy_user: str) -> CheckResult:
    """Connect, handshake, list tools — all inside a hard deadline."""
    started = time.monotonic()
    limit = REMOTE_CHECK_SECONDS if spec.is_remote else LOCAL_CHECK_SECONDS
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errlog:
        try:
            with anyio.fail_after(limit):
                if spec.is_remote:
                    async with (
                        create_mcp_http_client(headers=spec.headers) as http,
                        Client(streamable_http_client(spec.url, http_client=http)) as client,
                    ):
                        tools = await client.list_tools()
                else:
                    # Client(StdioServerParameters) can't take errlog; wrap it ourselves.
                    transport = stdio_client(_local_params(spec, agy_user), errlog=errlog)
                    async with Client(transport) as client:
                        tools = await client.list_tools()
            return CheckResult(ok=True, took_s=time.monotonic() - started, tools=len(tools.tools))
        except Exception as exc:
            leaf = _leaf(exc)
            if isinstance(leaf, TimeoutError):
                reason = f"no answer within {limit}s"
            else:
                reason = f"{type(leaf).__name__}: {leaf}"
                if spec.url:
                    reason = reason.replace(spec.url, spec.target)
            if not spec.is_remote:
                errlog.seek(0)
                tail = errlog.read()[-_STDERR_TAIL_CHARS:].strip().replace("\n", " | ")
                if tail:
                    reason += f" stderr={tail}"
            return CheckResult(ok=False, took_s=time.monotonic() - started, reason=reason[:600])


async def check_all(specs: list[ServerSpec], agy_user: str) -> dict[str, CheckResult]:
    results: dict[str, CheckResult] = {}

    async def one(spec: ServerSpec) -> None:
        results[spec.name] = await check_server(spec, agy_user)

    async with anyio.create_task_group() as tg:
        for spec in specs:
            tg.start_soon(one, spec)
    return results


def write_agy_config(servers: dict[str, dict[str, Any]], agy_user: str) -> str:
    """Atomically replace agy's MCP config, readable only by the agy user."""
    pw = pwd.getpwnam(agy_user)
    config_dir = os.path.join(pw.pw_dir, ".gemini", "config")
    os.makedirs(config_dir, exist_ok=True)
    path = os.path.join(config_dir, "mcp_config.json")
    fd, tmp = tempfile.mkstemp(dir=config_dir, prefix=".mcp_config.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": servers}, fh, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.chown(tmp, pw.pw_uid, pw.pw_gid)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    return path


def write_prompts(prompts: Mapping[str, str], path: str = PROMPTS_PATH) -> None:
    """Always rewritten (``{}`` included) so a removed server's text can't linger."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".mcp_prompts.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(prompts), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_prompts(path: str = PROMPTS_PATH) -> dict[str, str]:
    """``{}`` when absent (no extra servers, or not the worker) or unreadable —
    a missing hint must never fail a reply."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("mcp prompts unreadable path=%s error=%s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v}
