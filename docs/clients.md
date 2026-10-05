# Connecting your tools

The **Connect** page of the plugin shows all of this with your server's address filled in, and the connector adds it for
you. This page is the same thing for reading, or for tools you set up by hand. Replace `ervisio-myvps` with any name, and
`root@your-server` with the SSH user you want the AI to act as.

Two ways, for every tool:

* **SSH** (recommended). The tool runs `ssh -T … root@your-server ervisio-ai stdio`. Nothing listens on the server; you need
  a key-based SSH login (no password prompt) from your computer to the server, and the engine installed (*MCP server › Setup*).
* **HTTP**. The tool talks to `https://your-server:7420/mcp` with `Authorization: Bearer <token>`. Start the HTTP server and
  create a token on the *MCP server* page. For a server that is not exposed, tunnel it:
  `ssh -N -L 7420:127.0.0.1:7420 root@your-server` and use `http://127.0.0.1:7420/mcp`.

The SSH command line used below:

```
ssh -T -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o BatchMode=yes root@your-server ervisio-ai stdio
```

## Automatic

```sh
python3 ervisio-ai-connect.py --list       # what is installed on this computer
python3 ervisio-ai-connect.py              # connect everything it found (asks first)
python3 ervisio-ai-connect.py --clients claude-code,codex --yes
python3 ervisio-ai-connect.py --remove     # undo
```

`--mode ssh|http`, `--host`, `--user`, `--port`, `--identity`, `--url`, `--token`, `--insecure`, `--name`, `--dry-run`,
`--print` (show the settings instead of writing them), `--no-check`, `--force-jsonc`. The downloaded copy already knows your
server's values.

## By hand

### Claude Code

```sh
# SSH
claude mcp add-json ervisio-myvps '{"type":"stdio","command":"ssh","args":["-T","-o","ServerAliveInterval=30","-o","ServerAliveCountMax=4","-o","BatchMode=yes","root@your-server","ervisio-ai","stdio"]}' --scope user
# HTTP
claude mcp add-json ervisio-myvps '{"type":"http","url":"https://your-server:7420/mcp","headers":{"Authorization":"Bearer eai_…"}}' --scope user
```

### OpenAI Codex (`~/.codex/config.toml`)

```toml
# SSH
[mcp_servers.ervisio-myvps]
command = "ssh"
args = ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes", "root@your-server", "ervisio-ai", "stdio"]
startup_timeout_sec = 30
tool_timeout_sec = 900

# HTTP
[mcp_servers.ervisio-myvps]
url = "https://your-server:7420/mcp"
http_headers = { Authorization = "Bearer eai_…" }
```

### Cursor (`~/.cursor/mcp.json`)

```json
{ "mcpServers": { "ervisio-myvps": {
    "command": "ssh",
    "args": ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes", "root@your-server", "ervisio-ai", "stdio"] } } }
```

HTTP: `{ "url": "https://your-server:7420/mcp", "headers": { "Authorization": "Bearer eai_…" } }` in place of `command`/`args`.

### VS Code (user `mcp.json`: command palette › *MCP: Open User Configuration*)

```json
{ "servers": { "ervisio-myvps": {
    "type": "stdio", "command": "ssh",
    "args": ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes", "root@your-server", "ervisio-ai", "stdio"] } } }
```

HTTP: `"type": "http", "url": "…", "headers": { "Authorization": "Bearer eai_…" }`.

### Kilo Code (`~/.config/kilo/kilo.jsonc`)

```jsonc
{ "mcp": { "ervisio-myvps": {
    "type": "local",
    "command": ["ssh", "-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes", "root@your-server", "ervisio-ai", "stdio"],
    "enabled": true, "timeout": 900000 } } }
```

HTTP: `"type": "remote", "url": "…", "headers": { "Authorization": "Bearer eai_…" }`.

### Roo Code and Cline

In the MCP panel › *Edit*:

```json
{ "mcpServers": { "ervisio-myvps": {
    "command": "ssh",
    "args": ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes", "root@your-server", "ervisio-ai", "stdio"],
    "timeout": 900 } } }
```

HTTP: Roo Code `"type": "streamable-http"`, Cline `"type": "streamableHttp"`, plus `url` and `headers`.

### Claude Desktop, Windsurf, Antigravity, Gemini CLI, Zed, OpenCode

Each has its own settings file and shape (Claude Desktop reaches HTTP servers through `npx mcp-remote`; Antigravity,
Windsurf and Gemini CLI use their own keys for the URL). Open **Connect** in the plugin: it prints the exact text for each,
or run `python3 ervisio-ai-connect.py --print`.

## Check it works

Ask the tool: *"Use the ervisio tools: what is this server's hostname and how much disk is free?"* The call appears in
**Activity** on the Ervisio page, tagged with the client's name.

## Troubleshooting

* **The tool says the server failed to start.** Run the SSH line yourself in a terminal: `ssh -T root@your-server ervisio-ai stdio`
  and type nothing; it should wait silently. Common causes: a password prompt (use a key), `ervisio-ai` missing (install the
  engine), Python missing.
* **401 / "a valid bearer token is required".** The token is wrong, revoked or expired; make a new one.
* **403 from a browser-based client.** Add its origin to `allowed_origins` in `/var/lib/ervisio-ai/server.json` and restart.
* **It connects but cannot change anything.** The token is read-only, or the policy is in read-only mode.
* **`ervisio_*` tools are missing.** Ervisio is not installed on that machine; the *Checks* tab says so.
