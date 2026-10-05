# Architecture

```
 your computer                                     the server
┌──────────────────────┐                  ┌──────────────────────────────────────────────┐
│ Claude Code, Codex,  │  MCP over SSH    │  ervisio-ai stdio ─┐                         │
│ Cursor, Kilo Code…   │ ───────────────▶ │                    │                         │
│                      │  MCP over HTTP   │  ervisio-ai serve ─┤   policy · guard · audit │
│                      │ ───────────────▶ │  (token)           ├──▶ 42 tools ──▶ shell, terminals (tmux),
└──────────────────────┘                  │                    │               files, services, packages,
┌──────────────────────┐   declared       │  ervisio-ai call ──┘               Docker, SSH, Ervisio bridge
│ Ervisio page (chat)  │   commands       │  (one tool call)                                  │
│  browser ──▶ provider│ ───────────────▶ │                                                   ▼
│  tool calls ─────────┼─ request file ─▶ │                                         ervisio-bridge (the same
└──────────────────────┘                  └─────────────────────────────────────── methods Ervisio's page uses)
```

One **engine** (`plugin/engine`, Python standard library, 3.8+) holds everything that touches the machine. The chat and
the MCP clients are two front doors to the same tools, the same policy and the same activity log.

## The engine

| Piece | File | Notes |
|---|---|---|
| MCP protocol | `mcp.py` | JSON-RPC 2.0, version negotiation (2024-11-05, 2025-03-26, 2025-06-18), tools, resources, prompts, progress, cancellation. |
| Transports | `transport_stdio.py`, `transport_http.py` | stdio is what an IDE starts through `ssh`. HTTP is Streamable HTTP (`POST /mcp`, SSE for long calls, `Mcp-Session-Id`), threaded, with `/healthz`. |
| Tools | `registry.py`, `tools/*.py` | A tool is a function plus a JSON schema, a category and flags (read-only, destructive, elevatable). `docs/tools.md` is generated from the registry. |
| Policy | `policy.py` | Read-only mode, disabled tools and groups, protected paths, denied Ervisio methods, allow-root, the guard. |
| Tokens | `auth.py` | `eai_…` random tokens; only a SHA-256 hash is stored (`tokens.json`, 0600). Scopes `full` and `readonly`, optional expiry. |
| Audit | `audit.py` | JSON lines (`audit.jsonl`, 0600), arguments and errors with secrets masked, rotated at 20 MiB, five old files kept. |
| Ervisio bridge | `bridge.py` | Starts the real `ervisio-bridge` (newline-JSON on stdio) and keeps it; with `--admin` when the engine runs as root. |
| Terminals | `tools/terminal.py` | tmux on a private socket (`/var/lib/ervisio-ai/tmux.sock` as root), so terminals outlive any connection and a person can attach. |
| Jobs | `tools/shell.py` | Background commands in their own process group with output files, so they survive a dropped connection. |
| Service and setup | `ctl.py` | `install`, `start`/`stop`, systemd unit or a pidfile fallback, status, doctor, tokens, policy. |

### Who the engine runs as

* **Over SSH** the engine runs as the SSH user. Root login gives root; a normal user gives that user's rights, and a tool
  called with `sudo: true` re-runs itself through `sudo -n`.
* **HTTP service**: as root, from the systemd unit (or a background process without systemd).
* **From the Ervisio chat**: `ai-call` runs as the signed-in user; `ai-root-call` is an `admin` command and runs as root
  after Ervisio's unlock dialog.

State lives in `/var/lib/ervisio-ai` for root and `~/.local/share/ervisio-ai` for everybody else. `/var/lib/ervisio-ai` is
`0711`: other users can read `server.json`, the pid and the log (the Ervisio page shows the server's state to a normal
user) and nothing else.

## The page

* **Chat** (`src/chat`). No React, no SDK: it takes `fetch` and the tool runner as parameters, so it is unit-tested with
  `node --test`. `providers.ts` speaks Anthropic Messages (browser-direct, prompt caching) and the OpenAI-compatible
  Chat Completions API (streaming tool calls). `agent.ts` is the loop: ask the model, run the tool calls (asking first,
  according to the mode), give the results back, repeat; with retry/backoff, a step limit and old-output compaction.
* **Tool calls** go through the engine like this: the page writes a request file (`{tool, args}`, a random 32-hex name)
  into a folder the plugin declared, then starts the declared command `ai-call <id>` (or `ai-root-call <id>`) with
  `execStream`; the engine reads the file, runs the tool and answers with JSON lines (progress, then the result). The model
  therefore never chooses an argument list: the only free value is the request id, validated by the manifest's regex.
* **Classification**: in *Read freely, ask to change* mode the page asks the engine whether a call only reads
  (`classify`, which runs nothing), because the same tool can read or change depending on its arguments
  (`service_manage status` vs `restart`).
* **Server pages** (`src/views`) use `ai-ctl` / `ai-root-ctl` the same way.
* **Approved hosts**: the providers in the manifest work at once; a custom address (Ollama…) needs the administrator's
  approval, after which Ervisio reloads the plugin's frame. The page saves the provider first, so it is there after the reload.

## The connector

`plugin/connect/ervisio-ai-connect.py` is one file with a `Client` class per tool (where its settings live, which format,
how to detect it, how to add and remove the entry). The Ervisio page compiles the server's settings into it at download
time (a `PRESET` block), so running it needs no arguments. Edits are conservative: parse, change one key, write
atomically, keep a backup; JSON with comments (`.jsonc`) is refused unless `--force-jsonc`.

## Tests

| Suite | What it covers |
|---|---|
| `tests/engine` | Every tool; MCP over both transports with the official MCP SDK client as a cross-check; tokens, scopes, read-only, Origin/Host checks, cancel, progress; install/start/stop/upgrade/uninstall in pidfile mode; the real `ervisio-bridge` (when `ERVISIO_BRIDGE` is set). |
| `tests/connect` | Each client's detection, add, update, remove, backups, JSONC handling, SSH and HTTP modes, against fake home folders. |
| `test/` | Providers (canned SSE streams), agent loop, retries, approval modes, Markdown, `/` commands, summaries. |

Not covered by automated tests: systemd service mode (needs systemd), macOS and Windows paths in the connector, real
provider APIs, real SSH logins, and the page itself (it was exercised by hand in a development Ervisio).
