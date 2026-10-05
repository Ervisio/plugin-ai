# Changelog

All notable changes to the Ervisio AI plugin will be documented in this file.

## 0.1.0

* First release.
* **Two ways to use AI on a machine.** Chat on the Ervisio page with your own API key, or turn the machine into a headless MCP server that Claude Code, Codex, Cursor, VS Code, Kilo Code, Roo Code, Cline, Windsurf, Antigravity, Gemini CLI, Zed, OpenCode and Claude Desktop use from your own computer.
* **Chat.** A terminal-style chat in the Ervisio page. The browser talks to the provider directly (Anthropic with prompt caching, OpenAI, OpenRouter, Google Gemini, Groq, DeepSeek, Mistral, xAI, or any OpenAI-compatible address such as Ollama or LM Studio); your key never leaves your account folder on the server. Streaming replies, Markdown, live tool output, three approval modes (ask for everything, read freely and ask to change, never ask), "always allow this tool", per-conversation history, `/` commands and `!` to run a command yourself.
* **42 tools** in ten groups: shell and background jobs, live terminals (tmux), files, system and processes, services and cron, packages, network / SSH / firewall, Docker, Ervisio itself (every method of the Ervisio bridge, plugin commands, the audit log) and a persistent memory per server. Plus four MCP resources and ten prompts.
* **Headless MCP server.** Streamable HTTP with bearer tokens (full or read-only, optional expiry, stored hashed), or MCP over SSH (`ervisio-ai stdio`, no open port). Plain HTTP, self-signed HTTPS, Origin and Host checks, progress and cancellation. Runs as a systemd service, or as a plain background process where there is no systemd.
* **Connector for your computer.** A single Python file, downloaded from the Connect page with the server's settings built in, finds the AI tools installed on your computer, tests the connection, backs up each settings file and adds the server. `--dry-run`, `--remove`, `--print`. Everything it does can be done by hand: the Connect page shows the exact settings for each tool.
* **Safety.** Full access by default, because that is the point; a policy narrows it (read-only mode, tool groups, protected paths, no root, Ervisio methods that stay off). A guard stops the few commands that cannot be undone or lock everybody out until the AI confirms. Every call is written to an activity log with secrets masked.
* English and Italian interface.
