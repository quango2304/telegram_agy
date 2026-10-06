# External tools and sending files

## Sending a file to the chat

To hand a file back to the user, `agy` writes it into the shared **`/outbox`**
volume (mounted in both `worker` and `mcp`) and calls the **`send_chat_file`** MCP
tool.

- Paths outside `/outbox` are rejected.
- Files over `TELEGRAM_MAX_FILE_MB` (default 50, the Bot API `send_document`
  ceiling) are rejected.
- The file is deleted after sending.

## Free ebooks

`search_ebooks` → `download_ebook` → `send_chat_file`. Code:
`src/infrastructure/ebooks/catalog.py`. Only **legal, public-domain** sources —
the persona tells the agent to say "not available" rather than look elsewhere,
and pirate sites (OceanofPDF etc.) are deliberately not supported.

| Source | Search | Download |
|---|---|---|
| Standard Ebooks | scrapes `schema:Book` microdata on `/ebooks?query=` (the OPDS feed is patrons-only → 401) | link read off the book page, plus `?source=download` — without it you get a "thank you" HTML page with a meta refresh, not the file |
| Project Gutenberg | its own OPDS search (`gutenberg.org/ebooks/search.opds/?query=`, `l.<lang>` in the query filters language) | `gutenberg.org/ebooks/<id>.epub3.images` / `.kf8.images` / `.txt.utf-8` |
| Internet Archive | `advancedsearch.php`, limited to library-scan collections `americana`, `toronto`, `europeanlibraries`, `cdl`, `gutenberg`, not `access-restricted-item` | `/metadata/<id>` → smallest EPUB / Text PDF, re-checking the collection |

- **The agent never passes a URL**, only a `book_id` (`se:<slug>`,
  `gutenberg:<n>`, `ia:<identifier>`), validated by regex. An httpx request hook
  rejects any request — redirects included — that isn't https to
  `standardebooks.org`, `gutenberg.org` or `archive.org`. Without
  that the tool is a generic fetcher a prompt injection could aim anywhere.
- **IA user uploads are excluded on purpose.** Collections like `opensource`
  carry plenty of pirated books; restricting to library scans keeps it to public
  domain. The collection is re-checked on download, since an `ia:` id could come
  from anywhere.
- Downloads stream to `<name>.part` with a hard `TELEGRAM_MAX_FILE_MB` cap and are
  renamed only when complete. HTML responses are rejected (that's what a
  wrong/expired link returns). Gutenberg's own EPUBs with images can be ~25MB.
- **Don't use Gutendex** (gutendex.com, the popular Gutenberg JSON API). It took
  26–40s+ per search and timed out mid-run, which made every search wait for it.
  Gutenberg's own OPDS answers in ~1s. Search sources run concurrently; one being
  down is reported in the result, not raised.
- **Gutenberg search is accent-sensitive**: `les miserables l.fr` finds nothing
  relevant, `les misérables l.fr` finds Hugo. The tool docstring tells the agent
  to keep the original spelling.
- Only old, out-of-copyright books exist here. Titles in the original language
  (usually English) match far better than Vietnamese translations.

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

- **Use a `-medium` or `-high` model** (the default `claude-sonnet-5-5-medium`
  is fine) when Composio is on. `-low` is fast but drops multi-step tool tasks
  too often. Expect 40–180s for a
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
