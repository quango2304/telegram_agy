# Memory, history and search

Three different mechanisms, often confused. They are deliberately decoupled.

| Mechanism | Scope | Lifetime |
|---|---|---|
| Prompt window (`CONTEXT_MESSAGE_LIMIT`, default 30) | per thread | the newest N messages, rebuilt every run |
| Retention (`MESSAGE_RETENTION_DAYS`, default 10) | per thread | how long a row survives in the database |
| Chat memory (`update_memory`) | per **chat** | until overwritten — never auto-deleted |

## The prompt window

Every run loads the newest `CONTEXT_MESSAGE_LIMIT` messages for the thread,
oldest → newest, and renders them as `[name]: text` lines. The whole prompt is one
argv string, so it is capped at `AGY_PROMPT_MAX_CHARS`: oldest history lines are
dropped first, then the memory block is truncated.

Individual messages longer than `AGY_MESSAGE_TRUNCATE_CHARS` are cut with an
ellipsis.

## Retention

An hourly job deletes messages older than `MESSAGE_RETENTION_DAYS` — **except**
each thread's newest `CONTEXT_MESSAGE_LIMIT` rows, which are kept regardless of
age. A thread nobody has posted in for weeks still has context to answer with.

It is one `DELETE … USING` with a window function covering every thread at once —
do not loop in Python. `chat_threads` and `chat_memories` are never touched.

## Searching older messages

Between the prompt window and the retention cutoff sit messages that have scrolled
out of context but are still in the database. The **`search_history`** MCP tool
reaches them: `agy` calls it with keywords when someone asks about something older
("hôm trước ai gửi cái link đó").

- Searches the whole **chat** — every forum topic of a group — matching how memory
  is scoped.
- Backed by a Postgres `tsvector` **generated column** with the `simple`
  configuration, plus a GIN index. The two-argument `to_tsvector` is the IMMUTABLE
  form a generated column requires.
- Falls back to `ILIKE` when the tokeniser finds nothing (a fragment inside a
  longer word, odd punctuation).
- Capped at `HISTORY_SEARCH_LIMIT` (default 20) rows per call, so a broad keyword
  cannot flood the agent's context.

**Known limit:** `simple` does no stemming and no accent folding, so `ca phe` will
not match `cà phê`.

## Chat memory

`update_memory` stores one long-lived note per **chat** — one row for a private
chat, one shared across every forum topic of a group. It is the only recall that
survives retention indefinitely.

The tool **overwrites**; it does not append. The prompt tells `agy` to send the
full merged note (current memory + the new fact), because sending only the new
part would wipe everything else.

The prompt also tells the model, using the real `CONTEXT_MESSAGE_LIMIT` value,
that it only ever sees the last N messages — that is *why* writing to memory in
the same turn matters, rather than "later".

### Keeping it small

The prompt sets a **soft budget of 3000 characters**
(`MEMORY_SOFT_LIMIT_CHARS` in `src/shared/persona.py`) and instructs the model to
curate: merge duplicates, summarise, drop what is stale or no longer true. A note
that grows unbounded is paid for in input tokens on *every single reply* and
eventually becomes an unreadable pile.

The MCP tool still hard-caps at **8000** characters as a backstop, but that is a
truncation, not a plan — if notes routinely hit it, the curation instruction is
not working and should be tightened.

> Changing the soft budget is a prompt change: re-test that the model still
> merges rather than replaces, since those two behaviours are easy to confuse.
