# External MCP servers

Besides our own `memory` server ([mcp-tools.md](mcp-tools.md)), `agy` can use any
other MCP server you list in **`config/mcp_servers.json`**. That file is gitignored
because it holds API keys. `config/mcp_servers.example.json` is the committed
template.

```bash
cp config/mcp_servers.example.json config/mcp_servers.json   # then edit
docker compose -f docker-compose.yml restart worker          # picks up edits
```

A restart is enough (no `up -d`): `config/` is bind-mounted into the worker, and
the entrypoint rebuilds `agy`'s config from scratch on every start, so a server
you delete from the file is gone after the restart too.

## Format

The shape most MCP READMEs use, so an entry can usually be pasted straight in:

```json
{
  "mcpServers": {
    "ebook": {
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer …"},
      "prompt": "tìm sách TIẾNG ANH thì gọi `search_ebooks`, một lần thôi."
    },
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone=Asia/Ho_Chi_Minh"],
      "env": {}
    }
  }
}
```

| Field | Kind | Notes |
|---|---|---|
| `url` | remote | http(s). Current Streamable HTTP transport only. Old SSE-only servers fail the check. |
| `headers` | remote | String map sent verbatim on every request. **The only remote auth supported**: no OAuth, no `${VAR}` lookup. |
| `command`, `args` | local | Started by `agy` itself, **as the `agy` user inside the worker**, once per reply. |
| `env` | local | Added on top of `SCRUBBED_ENV`. Nothing else from the container's env reaches the server. |
| `prompt` | both | Optional. When and how to use this server, added to the run prompt's tool rules as `CÔNG CỤ MCP \`<name>\`: …`. Only sent when the server passed its check. |
| `disabled` | both | `true` keeps the entry but doesn't register it. |
| `type` | both | Optional and ignored, accepted so pasted entries work: `stdio` for local; `http`, `streamable-http` or `streamableHttp` for remote. `sse` is rejected. |

`memory` is reserved. Names are 1–64 of `A-Z a-z 0-9 _ -`.

**Local servers** need their runtime in the image. The image ships Node 22
(`npx`) and `uv` (`uvx`) in `/usr/local/bin`, because `SCRUBBED_ENV`'s PATH is only
`/usr/local/bin:/usr/bin:/bin`. Tools on your Mac don't carry over. The first
`npx -y` / `uvx` in a fresh container downloads the package. The startup check
does that download, so the first reply doesn't pay for it.

## What happens at worker start

`docker/entrypoint.sh` runs `python -m src.entrypoints.agy_mcp_setup`. It runs only
in the worker, because compose sets `AGY_MCP_SERVERS_FILE` only there. The step:

1. **Validates the file strictly.** Any mistake, such as broken JSON, an unknown
   field (`"header"`), both `url` and `command`, a non-string header or the name
   `memory`, logs every problem and **exits 1, so the worker refuses to start**.
   This is deliberate: a typo must never be half-applied silently. The bot keeps
   polling and storing messages, but nothing gets answered until the file is
   fixed. A missing file is fine: you get just `memory`.
2. **Health-checks every enabled server in parallel**: connect, handshake,
   `tools/list`. The limit is 10s for remote and 60s for local (to allow for the
   first package download). Local servers are launched exactly as `agy` will
   launch them (`gosu agy env -i <SCRUBBED_ENV + env> …`).
3. **Registers only the servers that answered.** It writes
   `/home/agy/.gemini/config/mcp_config.json` (mode 0600, owned by `agy`) in the
   format `agy mcp add` produces. The `prompt` texts go to
   `/var/lib/telegram_agy/mcp_prompts.json`, which only root can read, so `agy`
   cannot rewrite its own instructions. The reply runner reads that file for every
   prompt.

### Why the health check exists

Tested on agy 1.3.3:

- A server that **fails fast** (connection refused, command exits) is skipped
  silently by `agy` and costs nothing.
- A server that **hangs** blocks the whole run until the print timeout. That
  means a URL that accepts the connection and never answers, or a command that
  never speaks MCP. Every reply then fails, and since failed runs are silent
  ([replies.md](replies.md)), the chat just sees 👀 forever.

The check only runs at start. **A server that starts hanging later still breaks
every reply until the worker restarts.** If replies suddenly all time out, look
at `stuck=` in the `agy run finished` line ([operations.md](operations.md)).

## Cost and behaviour notes

- **Every registered server's tool list goes into every prompt.** A server with
  a dozen tools adds thousands of tokens to each reply. That is why Composio is
  a lazily-called CLI and not an MCP server
  ([composio-and-files.md](composio-and-files.md)). Use `disabled` for anything
  you don't need right now.
- **Write a `prompt`.** Without one, Flash discovers the tool but uses it badly.
  In testing, a slow tool (~45–90s per call) was called four times, the model
  wandered off probing `/proc`, and the run timed out. With a one-paragraph
  `prompt` ("call it ONCE with a short query, it's slow, don't probe the system,
  answer with the top 3–5"), the same ask finished in 116s with one call.
- Tool-heavy asks should run on the complex model. Book and file searches are in
  Jev's complex criteria for this reason ([model-routing.md](model-routing.md)).
  If you add a server for a new kind of job, check the `model routed` line for
  it, and extend `_COMPLEX_CRITERIA` in `model_router.py` if Flash gets it.
- Large tool outputs are spilled by `agy` into a file under
  `/home/agy/.gemini/antigravity-cli/brain/…`, which it then reads with
  `view_file`. Those extra steps in the log are normal.

## Deploying

The file is not in git, so `git pull` doesn't bring it. Copy it to the box once,
and again after every edit, then restart the worker:

```bash
scp config/mcp_servers.json <host>:~/telegram_agy/config/mcp_servers.json
ssh <host> 'cd ~/telegram_agy && docker compose -f docker-compose.yml restart worker'
```

## Security

Every header and `env` value in this file can be read by the `agy` process, and
anyone who can message the bot can ask it to print them. As with the Composio key
and the OAuth token, the only protection is `SECRET_GUARD`. Only put keys here
that you'd tolerate leaking to the people in your chats. See
[security.md](security.md).
