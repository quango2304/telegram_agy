"""Pick the ``agy`` model for a run: plain chat -> ``agy_model``, complex asks
(market outlook, evaluation, multi-step reasoning) -> ``agy_model_complex``.

The pick is made by **Jev** (TypeSafe's decision model) through OpenRouter's
Decisions API, ``POST /api/alpha/decisions``. Jev is not a chat model — it
generates nothing, it answers questions about a ``state`` with probabilities.
We ask one ``choice`` question whose options *are* the two model names, so its
answer is the model to run. Established by testing (jev-1.13):

- ``~typesafe/jev-latest`` / ``typesafe/jev-1.13`` on ``/chat/completions``
  returns 400 ("a decisions model"). ``typesafe/jev-router`` *does* work there,
  but it is a different product: it forwards the prompt to some upstream chat
  model (gpt / claude / deepseek), took 1.3-6s, and DeepSeek once ignored the
  JSON schema and answered the user's question instead.
- The Decisions call takes ~0.65s, costs ~$0.00002 (input tokens only), and got
  9/9 test messages right, including follow-ups ("thế còn HPG?" after a market
  question -> complex; "thêm cái nữa" after a joke -> simple).
- ``ROUTER_MODEL`` defaults to the ``~typesafe/jev-latest`` alias (resolved to
  jev-1.13 when tested), so a new Jev release is picked up without a deploy.
  The response's ``model`` names the concrete version; it is logged per run
  in case a release shifts which way borderline messages go.

Never raises: anything going wrong falls back to ``agy_model`` with a reason,
so a broken router can only make a reply cheaper, never make it disappear.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from src.infrastructure.agy.prompt_builder import HistoryLine, PendingTrigger
from src.infrastructure.config import Settings
from src.shared.logger import get_logger
from src.shared.persona import BOT_LABEL

logger = get_logger(__name__)

_URL = "https://openrouter.ai/api/alpha/decisions"
# Recent history lines shown to Jev, so a follow-up like "thế còn HPG?" is read
# as part of the market question before it.
_HISTORY_LINES = 6
_LINE_CHARS = 300
_ERROR_CHARS = 200
_ASKS_LOG_CHARS = 1000

_INSTRUCTIONS = (
    "Which model should answer the pending message(s) of this Vietnamese Telegram "
    "chat bot? Judge the pending message(s); use the recent chat only to understand "
    "a follow-up."
)
_SIMPLE_CRITERIA = (
    "Casual chat, greetings, jokes, simple Q&A, quick factual lookups (a price, the "
    "time, a definition), translation, short rewrites, reminders and scheduling."
)
_COMPLEX_CRITERIA = (
    "Market outlook or prediction (stocks, VN-Index, gold, FX, crypto), evaluating "
    "or valuing something, comparing options, financial statement analysis, "
    "investment questions, planning, debugging, or anything needing careful "
    "multi-step reasoning."
)


@dataclass(frozen=True)
class ModelChoice:
    model: str
    timeout_s: int
    complex: bool
    # "jev" when Jev picked it, else why we fell back: off, timeout,
    # http_<status>, http_error, bad_answer.
    by: str
    confidence: float | None = None
    # Jev's probability that the complex model is the right one — the number
    # to look at when judging a borderline pick.
    p_complex: float | None = None
    elapsed_ms: int = 0
    # Concrete Jev version that answered (e.g. typesafe/jev-1.13-20260917) and
    # the generation id (gen-dec-…), to look the call up on OpenRouter.
    jev_version: str = "-"
    jev_id: str = "-"


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _LINE_CHARS else text[:_LINE_CHARS] + "…"


def _state(
    history: Sequence[HistoryLine],
    pending: Sequence[PendingTrigger],
    trigger_name: str,
    trigger_text: str,
) -> dict[str, list[str]]:
    pending_ids = {p.tg_message_id for p in pending}
    recent = [h for h in history if h.tg_message_id not in pending_ids][-_HISTORY_LINES:]
    if pending:
        asks = [f"[{p.name}]: {_clip(p.text) or '[ảnh]'}" for p in pending]
    else:  # scheduled run: the saved instruction plays the trigger
        asks = [f"[{trigger_name}]: {_clip(trigger_text) or '[ảnh]'}"]
    return {
        "recent_chat": [
            f"[{BOT_LABEL if h.is_bot_self else h.name}]: {_clip(h.text) or '[ảnh]'}"
            for h in recent
        ],
        "pending": asks,
    }


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


async def choose_model(
    settings: Settings,
    *,
    history: Sequence[HistoryLine],
    pending: Sequence[PendingTrigger] | None,
    trigger_name: str,
    trigger_text: str,
    label: str = "",
) -> ModelChoice:
    """Pick the model and log one ``model routed`` line per run — everything
    needed to judge the pick later: what was asked, what Jev chose, how sure it
    was, and the generation id."""
    state = _state(history, pending or [], trigger_name, trigger_text)
    choice = await _decide(settings, state, label)
    logger.info(
        "model routed %s model=%s by=%s p_complex=%s confidence=%s timeout=%ss "
        "router_ms=%s jev=%s jev_id=%s context_lines=%s asks=%s",
        label,
        choice.model,
        choice.by,
        _fmt(choice.p_complex),
        _fmt(choice.confidence),
        choice.timeout_s,
        choice.elapsed_ms,
        choice.jev_version,
        choice.jev_id,
        len(state["recent_chat"]),
        json.dumps(state["pending"], ensure_ascii=False)[:_ASKS_LOG_CHARS],
    )
    return choice


async def _decide(settings: Settings, state: dict[str, list[str]], label: str) -> ModelChoice:
    simple, complex_ = settings.agy_model, settings.agy_model_complex
    started = time.monotonic()

    def ms() -> int:
        return int((time.monotonic() - started) * 1000)

    def fallback(by: str) -> ModelChoice:
        return ModelChoice(simple, settings.agy_timeout_seconds, False, by, elapsed_ms=ms())

    if not settings.openrouter_api_key or simple == complex_:
        return fallback("off")

    body = {
        "model": settings.router_model,
        "state": state,
        "questions": {
            "model": {
                "type": "choice",
                "instructions": _INSTRUCTIONS,
                "criteria": {simple: _SIMPLE_CRITERIA, complex_: _COMPLEX_CRITERIA},
            }
        },
    }
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
    try:
        async with httpx.AsyncClient() as client:
            # httpx timeouts are per phase (connect/read/...), not end to end —
            # wait_for is what actually bounds the call.
            resp = await asyncio.wait_for(
                client.post(_URL, json=body, headers=headers),
                timeout=settings.router_timeout_seconds,
            )
    except TimeoutError:
        return fallback("timeout")
    except httpx.HTTPError as exc:
        logger.warning("model router request failed %s error=%r", label, exc)
        return fallback("http_error")

    if resp.status_code != 200:
        logger.warning(
            "model router http error %s status=%s body=%s",
            label,
            resp.status_code,
            resp.text[:_ERROR_CHARS].replace("\n", " "),
        )
        return fallback(f"http_{resp.status_code}")
    try:
        data = resp.json()
        answer = data["answers"]["model"]
        picked = answer["choice"]
        probabilities = answer.get("probabilities") or {}
        confidence = _num(answer.get("confidence"))
        p_complex = _num(probabilities.get(complex_))
        version = str(data.get("model") or "-")
        jev_id = str(data.get("id") or "-")
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.warning(
            "model router bad answer %s body=%s", label, resp.text[:_ERROR_CHARS].replace("\n", " ")
        )
        return fallback("bad_answer")

    if picked in (simple, complex_):
        is_complex = picked == complex_
        timeout = (
            settings.agy_timeout_seconds_complex if is_complex else settings.agy_timeout_seconds
        )
        return ModelChoice(
            picked, timeout, is_complex, "jev", confidence, p_complex, ms(), version, jev_id
        )
    logger.warning("model router bad answer %s picked=%r", label, picked)
    return fallback("bad_answer")
