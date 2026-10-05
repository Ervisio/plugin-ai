# Security

This plugin hands an AI the keys to a machine, on purpose. This page says what protects you, and what does not.

## What to trust

| Layer | What it gives | What it does not give |
|---|---|---|
| **The account the engine runs as** | The real limit. Over SSH it is the SSH user; the HTTP service runs as root. | — |
| **Tokens** (HTTP) | Only holders of a token can use the HTTP server. Random 256-bit `eai_…` values, only a hash is stored, optional expiry, revocable, *read-only* scope. | A token is a password to the machine: treat it like an SSH key. |
| **SSH** | The recommended way. No port to open, no token to leak: your SSH key is the login, and the server's own SSH rules (users, keys, `from=`, `command=`) apply. | — |
| **Policy** | Read-only mode, whole tool groups off, protected paths, no root, denied Ervisio methods; applies to the chat and every client. | Not a sandbox: a shell tool with full access can do anything its account can. |
| **Safety guard** | Stops the commands that cannot be undone or lock everybody out until the call is repeated with `confirm_dangerous`. | Not a barrier against intent. Pattern-based, so a determined caller can phrase a command around it. |
| **Approval in the chat** | In *ask* modes you see and allow every call that changes something. | The MCP clients have their own approval prompts: check them in your tool. |
| **Activity log** | Every call, who made it, arguments and result, secrets masked. | Masking is best-effort: do not paste secrets you cannot rotate. |

For a hard limit, use a **read-only token**, the **read-only policy**, or connect as a **normal user** (the AI then has that
user's rights, and asks for `sudo` per call).

## Choices that matter

* **HTTP is `127.0.0.1` by default.** Reach it through an SSH tunnel, or choose *every network interface* with HTTPS. The
  Checks tab warns when tokens would cross the network unencrypted.
* **Origin and Host checks.** Requests with a browser `Origin` that is not allowed, or a `Host` that does not match, are refused
  (DNS-rebinding protection). Configure extra names in `server.json` (`allowed_origins`, `allowed_hosts`).
* **Self-signed HTTPS** is for tunnels and tests: tell the connector `--insecure` only for a certificate you made.
* **The chat holds your API key in your browser's account folder on the server** (`~/.config/ervisio/plugins/ai/settings.json`,
  in a folder only your account can enter). The key goes only to the provider, directly from the browser. A provider key with a spending
  limit is a good idea.
* **Root from the chat** needs Ervisio's administrator unlock. The page never builds a command line: it writes a request
  file and starts a command the manifest declares with a fixed argument list; the request id is validated by regex.
* **Files created by the engine** are private: tokens and the audit log are `0600`; the data folder is `0711` so the status
  page works for a normal user, and only `server.json`, the pid and the log are readable in it.
* **Prompt injection.** Anything the AI reads (a web page, a log line, a file) can contain instructions. Use *ask* mode, or
  a read-only token, when the AI works on content you do not control.
* **The model provider sees what the AI sees.** Command output and file contents are sent to the provider you chose (the chat) or
  to the one your MCP client uses.

## Reporting a problem

Open a private security advisory on this repository. Please do not post exploits in public issues.
