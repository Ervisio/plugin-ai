# Tool reference

42 tools in ten groups.

Generated from the engine (`python3 scripts/gen-tools-doc.py`). Every tool is available in the chat and to every MCP client; 
the policy can switch whole groups or single tools off. **R** = only reads, **W** = changes something, **!** = can destroy data or lock you out, 
**root** = accepts `sudo: true` to run as root.

## Shell and background jobs

| Tool | | What it does |
|---|---|---|
| `job_kill` | W! | Stop a background job (its whole process group). SIGTERM first; use signal KILL if it ignores it. |
| `job_list` | R | List background jobs started with job_start, newest first, with their state. |
| `job_output` | R | Read the output (stdout and stderr together) of a background job. By default the last 200 lines; pass offset (the next_offset of the previous call) to read only what is new. |
| `job_start` | W! root | Start a long-running command in the background and return at once with a job id. The job keeps running if this connection drops or the server restarts. Read its output with job_output, stop it with job_kill. |
| `shell_exec` | W! root | Run a shell command on this machine and wait for it to finish (bash -c, no terminal, no stdin). Returns the exit code, stdout and stderr. For anything that can take more than a few minutes use job_start; for interactive programs use terminal_open. |

## Live terminals

| Tool | | What it does |
|---|---|---|
| `terminal_close` | W! | Close a terminal and everything running in it. |
| `terminal_list` | R | List the open terminals with what runs in them and where. |
| `terminal_open` | W! root | Open a persistent interactive terminal (a real shell on a pseudo-terminal, kept in tmux). Use it for programs that ask questions or take over the screen: editors, installers, REPLs, ssh sessions, long watch commands. Then drive it with terminal_send and read it with terminal_read. |
| `terminal_read` | R | Read a terminal's screen (and scrollback) without typing. Use wait_ms to wait for a running program to produce more. |
| `terminal_resize` | W | Change the size of a terminal (some programs lay themselves out by it). |
| `terminal_send` | W! | Type into a terminal and return what the screen shows afterwards. text is typed literally; keys sends special keys (Enter, Tab, Escape, C-c, C-d, Up, Down, BSpace, Space, F1...). Set enter=true to press Enter after the text. Waits until the screen stops changing, or until wait_for (a regular expression) appears. |

## Files

| Tool | | What it does |
|---|---|---|
| `fs_archive` | W! root | Create, extract or list an archive: .tar, .tar.gz/.tgz, .tar.bz2, .tar.xz or .zip (chosen by the file name). Extraction refuses paths that escape the destination folder. |
| `fs_diff` | R root | Unified diff between two files, or between a file and some text. |
| `fs_edit` | W! root | Change a text file by replacing exact text: old_string must match the file exactly (whitespace included) and be unique unless replace_all is set. Give several edits at once with edits; they apply in order and all-or-nothing. Returns the diff. Read the file first. |
| `fs_find` | R root | Find files and folders by name, type, size or age, anywhere on the machine. |
| `fs_list` | R root | List a folder: permissions, owner, size, modification time. Optionally recursive, filtered by a glob on the name. |
| `fs_manage` | W! root | File operations: mkdir, move, copy, delete, chmod, chown, symlink, touch. Deleting or changing a system folder is refused by the safety guard unless confirm_dangerous=true. |
| `fs_read` | R root | Read a text file with line numbers. Long files come in pages: pass offset (first line, 1-based) and limit. For a binary file use encoding=base64 (offset and max_bytes are then in bytes). A directory is listed instead. |
| `fs_search` | R root | Search file contents (like grep -rn, using ripgrep when installed). Returns path:line:text, with optional context lines. Skips .git, node_modules and binary files unless include_ignored is set. |
| `fs_stat` | R root | Details of one path: type, size, permissions, owner, times, link target, and optionally its SHA-256. |
| `fs_write` | W! root | Create or overwrite a file with the given content (atomic: the old file is replaced only when the new one is complete; permissions and owner of an existing file are kept). For a small change to an existing file use fs_edit. |

## System and processes

| Tool | | What it does |
|---|---|---|
| `process_signal` | W! root | Send a signal to a process by pid, or to every process with an exact name (like killall). Default TERM; use KILL if it ignores it, HUP to make many daemons reload. |
| `system_info` | R | Operating system, kernel, CPU, memory, swap, disks, uptime, load, virtualization and addresses of this machine. |
| `system_logs` | R root | Read logs: the systemd journal (by unit, time, priority, text) or the end of any log file. |
| `system_network` | R root | Network state: listening ports with the program behind them, open connections, interfaces and addresses, routes, DNS settings or the firewall rules. |
| `system_processes` | R root | List running processes with CPU, memory and command line, busiest first. Filter by a regular expression on the command. |

## Services and cron

| Tool | | What it does |
|---|---|---|
| `cron_manage` | W! root | Scheduled jobs: list a user's crontab, /etc/cron.d files and systemd timers; add a crontab line; remove lines containing some text. |
| `service_manage` | W! root | Manage systemd units: list them, show status with recent log lines, start, stop, restart, reload, enable, disable, mask, read the unit file, reload systemd, or create a new unit file from text. |

## Packages

| Tool | | What it does |
|---|---|---|
| `package_manage` | W! root | Install, remove, search, update and list software with the machine's package manager (apt, dnf, yum, pacman, apk or zypper, detected automatically). Runs non-interactively; long installs stream progress. |

## Network, SSH and firewall

| Tool | | What it does |
|---|---|---|
| `firewall_manage` | W! root | Open or close ports with the machine's firewall (ufw or firewalld, detected automatically): show its state, allow or deny a port, delete a rule, reload, enable, disable. Enabling a firewall with no SSH rule is refused by the safety guard because it would lock everybody out. |
| `net_check` | R | Check how a host is reachable from this machine: DNS resolution, a TCP connection to a port, the TLS certificate (issuer, expiry) and ping. |
| `net_request` | W! | Make an HTTP(S) request from this machine (like curl). Returns the status, main headers and the body. Use it to test a service from the server's own point of view, call an API, or download a file with save_to. |
| `ssh_exec` | W! | Run a command on ANOTHER machine over SSH, using this server's SSH keys (non-interactive). Handy to manage a fleet from one place. First connection to a new host accepts and remembers its key. |

## Docker

| Tool | | What it does |
|---|---|---|
| `docker_cli` | W! root | Run the docker command line with the arguments you give (no shell). Examples: ['ps','-a'], ['logs','--tail','100','web'], ['compose','up','-d'] (with cwd = the project folder), ['exec','web','nginx','-t'], ['system','prune','-f']. |

## Ervisio itself

| Tool | | What it does |
|---|---|---|
| `ervisio_audit` | R root | Read Ervisio's own activity log (who signed in, which plugin commands ran, settings and plugin changes). Newest first. |
| `ervisio_call` | W! | Call any method of the Ervisio console (the same API its web UI uses) as the server's user, with full administrator rights when the server runs as root. Examples: services.list, services.restart {unit}, files.list {path}, logs.query, software.updates, users.list, terminal.list, config.get, plugins.list, system.metrics. Stream methods (logs.follow, system.metricsStream) are sampled for a few seconds. |
| `ervisio_methods` | R | List the Ervisio methods you can call with ervisio_call, grouped by area (services, files, logs, software, users, terminal, plugins, config, updates, system, prefs, overview). 'admin' ones need root; 'stream' ones deliver events. |
| `ervisio_plugin_exec` | W! | Run a command declared by an installed Ervisio plugin (for example the docker plugin's own commands) exactly as the plugin's page would, with the plugin's argument rules. Use ervisio_call plugins.list to see plugins and their commands. |
| `ervisio_status` | R | State of the Ervisio console on this machine: version, whether the daemon answers, the bridge, and the installed plugins. |

## Context and memory

| Tool | | What it does |
|---|---|---|
| `memory_read` | R | Read the server memory: notes that earlier AI sessions left about this machine. |
| `memory_write` | W | Write to the server memory, shared by every AI client of this server. Record what a future session needs: how apps are deployed, where data lives, decisions, quirks. Keep it factual and short. mode=append adds a dated entry; mode=replace rewrites everything (read it first). |
| `server_context` | R | START HERE. Says which machine this is, who you run as, the policy that applies, which tools work here (systemd, Docker, tmux, Ervisio...) and what earlier sessions wrote in the server memory. |

