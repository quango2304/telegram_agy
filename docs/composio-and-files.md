# External tools and sending files

## Sending a file to the chat

To hand a file back to the user, `agy` writes it into the shared **`/outbox`**
volume (mounted in both `worker` and `mcp`) and calls the **`send_chat_file`** MCP
tool.

- Paths outside `/outbox` are rejected.
- Files over `TELEGRAM_MAX_FILE_MB` (default 50, the Bot API `send_document`
  ceiling) are rejected.
- The file is deleted after sending.

## Composio (optional)

Set **`COMPOSIO_API_KEY`** to a `uak_…` Composio *user* API key and the image logs
the `composio` CLI in at start. `agy` then shells out to `composio search|execute`
**only when a message needs it** — so ordinary replies still cost their usual
tokens, not the ~40k an always-on Composio MCP server would add to every prompt.

Whatever apps you connected in that Composio account (Google Drive, Gmail, …)
become available.

Get the key by running `composio login` once anywhere and copying the `api_key`
field from `~/.composio/user_data.json`. **`ak_` / `ck_` keys do not work** — it
must be a `uak_` user key.

### Notes

- **Use `AGY_MODEL=gemini-3.8-flash-medium` (or `-high`)** when Composio is on.
  `-low` is fast but drops multi-step tool tasks too often. Expect 40–180s for a
  search-download-send reply.
- Typical flow: *"find X in my Drive and send it"* → `composio search` →
  `composio execute GOOGLEDRIVE_DOWNLOAD_FILE` → `curl` the returned URL into
  `/outbox` → `send_chat_file`.
- `agy` runs `--dangerously-skip-permissions`, so it will invoke any connected
  Composio tool with no approval. **Anyone who can message the bot can act on those
  accounts** — only enable on a private / trusted deployment. See
  [security.md](security.md).
- A `composio` run that wedges is killed by process-group SIGKILL plus a Celery
  hard time limit; the reply fails, the worker slot frees, nothing hangs.
