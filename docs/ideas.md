# Ideas and roadmap

What 0.1.0 does is in the [README](../README.md). This is what could come next, roughly in the order I would build it.
Nothing here exists yet.

## Next

* **Watch the AI work.** An xterm terminal in the page attached to the AI's tmux session (the Ervisio SDK has `api.pty`),
  so you see `vim` and installers live instead of reading a transcript, and can type into the same terminal.
* **Fleet mode.** Ervisio can pair several servers (`sdk.envs`). Let one conversation address *all* of them: "update every
  server and tell me which ones need a reboot", with `ssh_exec` and the per-server engine doing the work.
* **Per-token scopes.** Today a token is *full* or *read-only*. Add allow-lists ("this token may only use `docker_cli` and
  `service_manage`"), per-token protected paths and rate limits, so a CI pipeline or a teammate gets exactly what they need.
* **Background agents.** Use Ervisio's jobs (`capabilities.jobs`) to run a prompt on a schedule or on a trigger: a nightly
  "check disk, failed services and pending security updates", a webhook that starts an investigation when monitoring fires,
  with the result pushed through Ervisio's notifications.
* **Session recording and replay.** Save a conversation's tool calls as a runbook: replay it on another server, or turn it
  into a shell script / Ansible playbook after the fact ("what exactly did the AI do to fix this?").
* **Approval on your phone.** When an MCP client does something the policy marks as *ask*, send the request to Ervisio's
  notification channel and let the administrator allow or deny it from anywhere.

## Further out

* **Sign in with OAuth** for the HTTP server (the MCP authorization spec), so remote clients that expect it can connect without
  a pasted token, and put the endpoint behind Ervisio's Caddy with one click (automatic HTTPS and a hostname).
* **Dry-run mode.** The AI proposes a plan and the page shows the diff of every file and the exact commands before anything runs.
* **Snapshots before changes.** Optional automatic `btrfs`/LVM snapshot, or a tar of the touched files, before a destructive
  tool call, with one-click rollback in *Activity*.
* **Knowledge packs.** Drop-in markdown "skills" (how this company deploys, naming rules, runbooks) the engine adds to the
  server context, shared across servers.
* **Local models first.** Ollama and LM Studio already work as custom providers; add model discovery on the LAN and a "run
  the model on the same server" installer.
* **More clients** for the connector as MCP spreads, and `--update` to refresh a tool's entry when a token is rotated.

## Registry entry

When the first release is tagged, add this to [`registry.json`](https://github.com/Ervisio/plugins/blob/main/registry.json)
of the plugin registry (and the category, since none fits):

```json
{ "id": "AI", "name": "AI", "icon": "zap", "color": "ov" }
```

```json
{ "id": "ai", "repo": "Ervisio/plugin-ai", "trust": "team", "category": "AI", "featured": true }
```

The sync workflow then opens the review pull request. Expect the maintainers to look hard at the permissions: the plugin
declares administrator commands and a writable system folder (`/var/lib/ervisio-ai/spool`), which is what lets the AI act as root.
