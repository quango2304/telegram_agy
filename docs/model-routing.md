# Model routing

Every `agy` run picks one of two models first:

| | Model (default) | Budget | For |
|---|---|---|---|
| simple | `AGY_MODEL` = `gemini-3.8-flash-high` | `AGY_TIMEOUT_SECONDS` = 300s | casual chat, quick Q&A, lookups, translation, reminders |
| complex | `AGY_MODEL_COMPLEX` = `gemini-3.1-pro-high` | `AGY_TIMEOUT_SECONDS_COMPLEX` = 480s | market outlook, evaluation / valuation, comparisons, multi-step reasoning |

Code: `src/infrastructure/agy/model_router.py`, called from `_run_reply` in
`src/infrastructure/reply/runner.py`.

## Who decides: Jev

The pick is made by **Jev**, TypeSafe's *decision* model, through OpenRouter's
**Decisions API** (`POST https://openrouter.ai/api/alpha/decisions`). Jev
generates no text. It answers questions about a `state` with probabilities. We
send one `choice` question whose two options *are* the two model names (each with
a one-line description), and Jev's `choice` is the model to run.

- **`state`** = the pending trigger message(s) (or the saved instruction on a
  scheduled run) plus the **6 most recent history lines**, each clipped to 300
  chars. Images are shown as `[ảnh]` and are not sent. The history is there so
  follow-ups route correctly: "thế còn HPG?" after a VN-Index question goes to
  Pro, "thêm cái nữa" after a joke stays on Flash.
- One pick per run. A batch of several pending triggers goes in together and
  shares one model.
- Measured: **~0.65s**, **~$0.00002** per call (input tokens only), 9/9 on a
  Vietnamese test set.
- `ROUTER_MODEL` defaults to the **`~typesafe/jev-latest`** alias, so new Jev
  releases are picked up without a deploy. The concrete version that answered is
  logged (`jev=typesafe/jev-1.13-…`). If routing drifts after a release, pin
  `ROUTER_MODEL=typesafe/jev-1.13`.

### Not `jev-router`, not `/chat/completions`

Two things that look right and are not:

- **`~typesafe/jev-latest` on `/chat/completions`** returns 400 ("a decisions
  model and cannot be used with the chat/completions endpoint").
- **`typesafe/jev-router`** works on `/chat/completions`, but it is a different
  product. It *forwards the prompt to some upstream chat model* (seen: GPT,
  Claude, DeepSeek) and took 1.3–6s. Asked to classify, DeepSeek once ignored the
  JSON schema and wrote a full answer to the user's question instead.

## Order of a run

1. Lock, history, memory, media and session key, as before.
2. 👀 reaction and typing indicator go out.
3. **Jev picks the model** (bounded by `ROUTER_TIMEOUT_SECONDS`, 5s).
4. `agy --model <pick> --print-timeout <that model's budget>`.
5. A transient-error retry (see [replies.md](replies.md#one-retry-for-upstream-blips))
   reuses the same model and what is left of *its* budget.

## When Jev fails

Routing **never blocks a reply**. Any router failure (no key, timeout, HTTP
error, an answer that is not one of the two names) falls back to `AGY_MODEL` with
its normal budget. Worst case is a complex question answered by Flash, never
silence. The `by=` field of the `model routed` log line says which case it was.
See [operations.md](operations.md).

Turn routing off by leaving `OPENROUTER_API_KEY` empty (or setting
`AGY_MODEL_COMPLEX` equal to `AGY_MODEL`).

## Judging the picks

Every run logs one line with everything needed to grade the pick afterwards:

```
model routed thread_id=1 model=gemini-3.1-pro-high by=jev p_complex=0.99 confidence=0.98
  timeout=480s router_ms=1302 jev=typesafe/jev-1.13-20260917
  jev_id=gen-dec-1791530404-TK8BtVLvaEIOYbJcwr7L context_lines=2 asks=["[Quân]: thế còn HPG?"]
```

- `asks=` is the pending message text Jev judged (clipped). `context_lines=` is
  how many history lines it saw alongside.
- `p_complex` is Jev's probability for the complex model. Picks near 0.5 are
  the borderline ones worth reviewing. `confidence` is Jev's own certainty.
- `jev_id` finds the call in the OpenRouter activity log. `jev=` tells you
  whether a new Jev release changed things.
- The `agy run finished` line for the same `thread_id` right after says how it
  went on that model (elapsed, tools, outcome). Pro picks that finish in a few
  seconds with no tools, or Flash picks that time out, are misroutes.

On the VPS (journald, kept across deploys):

```bash
journalctl -t telegram_agy-worker-1 --since "7 days ago" --no-pager \
  | grep -E "model routed|agy run finished" | sed 's/ conversation_id=.*//'
# just the picks, sorted by p_complex (borderline ones sit around 0.5):
journalctl -t telegram_agy-worker-1 --since "7 days ago" --no-pager | grep "model routed" \
  | grep -oE "model=\S+ by=\S+ p_complex=\S+|asks=.*" | paste - - | sort -t= -k4 -n
```

## Timeouts

Pro is slower and complex asks are tool-heavy (web searches, several steps), so
it gets its own budget. The Celery soft/hard limits and the per-thread Redis lock
TTL are sized from the **larger** of the two budgets (`Settings.agy_timeout_max`),
otherwise a long Pro run would be SIGKILLed by Celery, or its lock would expire
mid-run and let a second reply race in.
