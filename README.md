# AI plugin for Ervisio

Give an AI full control of a server, from [Ervisio](https://github.com/Ervisio/ervisio): its shell, live terminals,
files, services, packages, Docker, firewall and Ervisio itself. Choose where the AI lives, and you can use both:

* **Chat here.** Paste an API key (Claude, OpenAI, Gemini, OpenRouter, Groq, DeepSeek, Mistral, xAI, or any
  OpenAI-compatible server such as Ollama) and work from a terminal-style chat inside Ervisio.
* **MCP server.** Turn the machine into an MCP server. Claude Code, Codex, Cursor, VS Code, Kilo Code, Roo Code, Cline,
  Windsurf, Antigravity, Gemini CLI, Zed, OpenCode and Claude Desktop, running on **your own computer**, connect to it and
  manage the server from there. Add as many servers as you like and drive all your VPSes from one place.

English and Italian interface. Nothing to install on the server but the plugin (and Python 3, which every Ervisio
machine has).

## Install

In Ervisio, open **Plugins › Browse › AI › Install** (the marketplace package is signed by the Ervisio team). The dialog
lists the permissions below; updates appear in **Plugins › Updates**.

Then open **AI** in the sidebar and choose:

1. **Chat here with your API key.** Pick a provider, paste the key, press *Test the key*, choose a model. Your key is
   saved in `~/.config/ervisio/plugins/ai/settings.json`, readable only by your account, and is sent only to the
   provider. The chat runs in your browser; the server only runs the tools.
2. **MCP server for your tools.** Follow the three steps on the page:
   1. *Install the engine*, the small program that gives the AI its tools (one click, asks for administrator rights).
   2. *Choose how your computer connects*: **SSH** (recommended: nothing listens, your SSH key is the login) or **HTTP
      with a token** (a service on a port; plain, or HTTPS with a self-signed certificate).
   3. Open **Connect**: it shows the exact settings for each tool, and a one-file **connector** that adds the server to
      every AI tool it finds on your computer.

### Connect your computer (automatic)

The *Download the connector* button gives you `ervisio-ai-connect.py` with this server's settings built in. Run it **on
your own computer**, not on the server:

```sh
python3 ervisio-ai-connect.py             # finds your AI tools, tests the connection, asks, then adds the server
python3 ervisio-ai-connect.py --list      # what it found
python3 ervisio-ai-connect.py --dry-run   # show every change, make none
python3 ervisio-ai-connect.py --remove    # take the server out of the tools again
```

It tests the connection first, keeps a backup (`<file>.bak-ervisio-ai-<date>`) of every settings file before it touches it, and only
adds one entry named after the server. It knows the settings locations on Linux, macOS and Windows and needs Python 3.8+ and nothing else (the automated tests run on Linux; macOS and Windows paths come from each tool's documentation).

### Connect your computer (by hand)

Every setting the connector writes is shown on the Connect page, with a Copy button, and in
[docs/clients.md](docs/clients.md). The two shapes, for any tool that speaks MCP:

```sh
# SSH: the tool starts the engine on the server when it needs it. No open port, no token.
ssh -T root@your-server ervisio-ai stdio

# HTTP: Streamable HTTP with a bearer token (create one on the Tokens tab).
POST https://your-server:7420/mcp        Authorization: Bearer eai_…
```

Claude Code, for example:

```sh
claude mcp add-json ervisio-myvps '{"type":"stdio","command":"ssh","args":["-T","-o","BatchMode=yes","root@your-server","ervisio-ai","stdio"]}' --scope user
```

## What it does

* **Total control of the machine**, the main use:
  * **Shell** (`shell_exec`) and **background jobs** that survive a dropped connection (`job_start`, `job_output`).
  * **Live terminals** (`terminal_*`) backed by tmux: the AI can drive `vim`, a REPL, an installer asking questions, or a
    `docker compose up`. You can attach to the very same terminal and watch it work: `tmux -S /var/lib/ervisio-ai/tmux.sock attach`.
  * **Files**: read, write, edit by exact replacement, search (ripgrep when present), diff, find, archive, manage
    (copy, move, delete, chmod, chown, symlink).
  * **System**: info, processes, signals, listening ports, logs (journal and files), cron.
  * **Services, packages and firewall**: systemd (`service_manage`), apt, dnf, yum, pacman, apk or zypper (`package_manage`), ufw or firewalld
    (`firewall_manage`).
  * **Docker** (`docker_cli`, any `docker` or `docker compose` command), plus HTTP requests, port checks and **SSH to other
    machines** (`ssh_exec`).
* **Total control of Ervisio.** `ervisio_call` reaches every method of the Ervisio bridge (the same ones the Ervisio page
  uses for services, software, users, files, logs, plugins and more), `ervisio_methods` lists them,
  and `ervisio_plugin_exec` runs the commands of other plugins (Docker, Database…), so the AI manages Ervisio as well as the
  operating system under it.
* **Memory per server.** `memory_write` keeps notes ("the staging stack lives in /srv/stack, deploys with make up") that
  every later conversation starts from, in the chat and in every MCP client.
* **Ten ready prompts and four resources** for MCP clients: security audit, debug a service, deploy an app, harden SSH, free
  disk space, reverse proxy with HTTPS, backup plan, incident triage, a tour of Ervisio…
* **Everything is logged.** *Activity* shows every tool call (chat or MCP), with arguments, result, duration and who
  made it; secrets are masked.
* **Full access by default.** The AI can do anything you can do here, root included. That is the point. Narrow it when you
  want to: see [Safety](#safety).

All 42 tools: [docs/tools.md](docs/tools.md).

### The chat

* Streaming replies with Markdown, live output of running commands, a card for every tool call.
* **Approval modes**: *Read freely, ask to change* (default), *Ask for everything*, *Never ask*. "Always allow this
  tool" when you trust one.
* **As me / As root**: tools run as your account; the AI asks for root on single calls (`sudo`), or turn on *Run tools as
  root*. Root needs Ervisio's administrator unlock, the same as any other administrator action.
* `/` commands, `!command` to run a command yourself and let the AI see the result, `Esc` to stop, conversation history
  saved on the server.
* Anthropic prompt caching, retry with backoff on overload and rate limits, long outputs shortened in old turns so a
  long session keeps fitting in the model's context.

### The MCP server

* **Two transports**: MCP over SSH (`ervisio-ai stdio`) or Streamable HTTP (`ervisio-ai serve`) with progress for long
  commands and cancellation.
* **Tokens** per device, *full* or *read-only* (a read-only token can look at everything and change nothing), optional
  expiry; only a hash is stored on the server. Revoke in one click.
* **HTTP hardening**: bearer tokens, Origin and Host checks (DNS-rebinding), `127.0.0.1` by default, a warning in the
  checks when it is exposed over plain HTTP, optional self-signed HTTPS.
* Runs as a **systemd service** (start at boot), or as a plain background process on machines without systemd.
* **Checks** tab: Python, tmux, SSH, systemd, bridge, tokens, transport security; each with the fix.

## Safety

The AI has the same rights as the account the engine runs as (the SSH user, or root for the HTTP service). On top of that
the **Safety** tab lets you set a policy that applies to the chat and to every client:

* **Read-only mode**: the AI can look, not touch.
* **Tool groups**: untick *Docker*, *Packages*, *Ervisio itself*… to remove them from every client.
* **Protected paths** (e.g. `/etc/ssh`) and **Ervisio methods that stay off** (e.g. `users.*`).
* **Allow root** off, and an **activity log** on (recommended).
* A **safety guard**, on by default: a short list of commands that cannot be undone or lock everybody out (`rm -rf /`,
  `mkfs`, writing to a disk device, `reboot`, firewall changes that can cut your own SSH, `curl … | sh`…) is stopped until the AI repeats the call with
  `confirm_dangerous`. It is a speed bump against mistakes, not a sandbox: with full access the AI can always do harm if told
  to. For a hard limit use a read-only token, a read-only policy, or a normal user.

How it all fits, and what to trust: [docs/security.md](docs/security.md).

## Permissions

The manifest is [plugin/manifest.json](plugin/manifest.json). In short:

* **Commands** (fixed argument lists; the only values the page can pass are 32-character request ids):
  * `ai-call`, `ai-ctl`: run one tool call / read the state of the AI server, **as your own user**.
  * `ai-root-call`, `ai-root-ctl`, **`admin`**: the same as root (Ervisio asks for administrator rights).
    `ai-root-ctl` also installs, starts and stops the server and manages its tokens and policy.
  * `ai-info`: list the tools, or say who the engine runs as.
* **Folders**: `~/.config/ervisio/plugins/ai` (created on first use: settings, your API keys, conversations, request
  files) and, as administrator, `/var/lib/ervisio-ai/spool` (requests for root calls).
* **Network**: the model providers (`api.anthropic.com`, `api.openai.com`, `openrouter.ai`,
  `generativelanguage.googleapis.com`, `api.groq.com`, `api.deepseek.com`, `api.mistral.ai`, `api.x.ai`), and **one extra
  address you approve** (Ollama, LM Studio, a company gateway): an administrator confirms it once in a dialog.
* **Visible to** members of `sudo`, `wheel` and `admin` (administrators always see it).

The plugin never runs a command of its own making: the model's tool calls reach the machine only through the engine
(`/var/lib/ervisio/plugins/ai/engine`), and each one is checked by the policy and written to the log.

## Development

Requires Node.js 22 or newer and Python 3.8+ (the engine and the connector use only the standard library).

```sh
npm ci
npm run build       # typecheck, bundle to dist/ai/index.js, copy plugin/* and the manifest
npm run pack        # package dist/ai-<version>.tar.gz and .sha256
npm run typecheck   # TypeScript check
npm test            # page + engine + connector tests
npm run check       # version is the same everywhere; docs/tools.md matches the engine
npm run docs        # regenerate docs/tools.md from the engine
```

Turn on developer mode in Ervisio (`plugins.dev = true`, or a daemon started with `--dev`) and load `dist/ai` from
**Plugins › Developer**; after a rebuild use "Reload" there. A dev folder runs unsigned while developer mode is on.
The engine is looked up at `/var/lib/ervisio/plugins/ai/engine`: when developing, link that path to `dist/ai`.

Tests:

* `npm run test:ui`: the chat core (providers, agent loop, Markdown, commands) with `node --test`, no browser.
* `npm run test:engine`: the engine, over both MCP transports, with the official MCP SDK client as one of the clients. Set
  `ERVISIO_BRIDGE` to an `ervisio-bridge` binary (CI builds it from `Ervisio/ervisio`) to run the tests that talk to the real
  bridge.
* `npm run test:connect`: the connector against fake home folders of every supported tool.

The SDK types and Vite presets come from [@ervisio/plugin-sdk](https://github.com/Ervisio/plugin-sdk).

## How it fits together

React is not bundled. The preset in `vite.config.ts` aliases `react` and the JSX runtime to the SDK's shim
(`@ervisio/plugin-sdk/react`), which forwards to `sdk.react`. The SDK exists only inside `activate()`
(`src/index.ts`), so:

* Never call a React API at module top level (`memo`, `createContext`…): use `lazyMemo` (`src/ui/memo.ts`).
* Read the SDK with `getSdk()` (`src/sdk.ts`) inside functions, never at import time.
* Use the kit through `src/kit.ts` (`Button`, `Dialog`, `toast`, …), not `sdk.ui` directly.

```
plugin/
  manifest.json            commands, permissions, plugin metadata
  logo.svg                 icon for the sidebar and marketplace
  engine/                  the engine, Python standard library only (installed on the server)
    __main__.py            ervisio-ai: serve | stdio | call | ctl | install | token | policy | audit | doctor …
    ervisio_ai/            mcp.py (JSON-RPC), transport_http.py, transport_stdio.py, registry.py, policy.py, auth.py,
                           audit.py, bridge.py (Ervisio bridge client), ctl.py (install, service, status, doctor),
                           content.py (resources, prompts), tools/ (the 42 tools)
  connect/
    ervisio-ai-connect.py  the connector that runs on your computer
src/
  index.ts                 plugin entry: stores the SDK and React, registers the page
  engine.ts                how the page talks to the engine (request files + declared commands)
  chat/                    providers, agent loop, Markdown, commands, controller (no React: tested with node --test)
  state/                   settings, conversations, server status
  views/                   welcome, chat, MCP server, connect, activity, settings
  i18n/strings.ts          English and Italian
tests/                     engine and connector tests (unittest)
test/                      chat tests (node --test)
docs/                      architecture, security, clients, tools, ideas
```

More in [docs/architecture.md](docs/architecture.md).

## Releasing

On GitHub: **Actions › Release › Run workflow**, choose `patch`, `minor` or `major`, optionally type the release notes
(empty: the commit subjects since the last release), and run it. The workflow bumps the version, writes the
`CHANGELOG.md` section, tags, builds, validates and releases, then tells the Ervisio registry: an update that asks for
no new permissions is in the marketplace a few minutes later. The steps live in
[Ervisio/plugin-sdk](https://github.com/Ervisio/plugin-sdk/blob/main/docs/publishing.md).

## License

MIT, see [LICENSE](LICENSE).
