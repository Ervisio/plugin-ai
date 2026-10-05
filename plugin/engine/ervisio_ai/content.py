"""The words of the server: instructions for the model, resources and ready-made prompts."""
import os
import socket
from typing import Any, Dict, List, Optional

from . import __version__, audit, policy as policy_mod

ABOUT_URI = "ervisio-ai://about"
MEMORY_URI = "ervisio-ai://memory"
SYSTEM_URI = "ervisio-ai://system"
AUDIT_URI = "ervisio-ai://audit"


def instructions() -> str:
    pol = policy_mod.load()
    who = "root" if os.geteuid() == 0 else "the user '%s'" % _user()
    return (
        "You are connected to Ervisio AI on the Linux machine '%s', running as %s. You have a real shell, files, "
        "services, packages, Docker and the Ervisio web console under your hands.\n\n"
        "Start with server_context: it says what this machine is, which tools work here and what earlier sessions "
        "noted in memory.\n"
        "How to work well:\n"
        "- Look before you change: read files, check service status and logs, then act, then verify the result.\n"
        "- Prefer fs_edit for small changes to a file and fs_write for new files; keep backups of config you replace.\n"
        "- shell_exec is for commands that finish. Anything slower than a few minutes: job_start, then job_output. "
        "Interactive programs (editors, installers that ask questions, REPLs): terminal_open + terminal_send + "
        "terminal_read.\n"
        "- Commands run without a terminal and with no stdin, so give them non-interactive flags (-y, --yes).\n"
        "- Say what you are about to change on a production machine and keep the change small. If a command is "
        "refused by the safety guard, explain why before you repeat it with confirm_dangerous=true.\n"
        "- Write what a future session must know (how an app is deployed, where its data lives, quirks) with "
        "memory_write.\n"
        "Policy on this server: %s." % (socket.gethostname(), who, policy_mod.describe(pol))
    )


def _user() -> str:
    import pwd
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


def resources() -> List[Dict[str, Any]]:
    return [
        {"uri": ABOUT_URI, "name": "about", "title": "About this server", "mimeType": "text/markdown",
         "description": "What Ervisio AI is, the tools it offers and the policy that applies."},
        {"uri": MEMORY_URI, "name": "memory", "title": "Server memory", "mimeType": "text/markdown",
         "description": "Notes earlier AI sessions left about this machine."},
        {"uri": SYSTEM_URI, "name": "system", "title": "System overview", "mimeType": "text/plain",
         "description": "Operating system, CPU, memory, disks and uptime right now."},
        {"uri": AUDIT_URI, "name": "audit", "title": "Recent AI activity", "mimeType": "application/json",
         "description": "The last 50 tool calls on this server."},
    ]


def read_resource(uri: str) -> Optional[Dict[str, Any]]:
    from .tools import meta, system

    if uri == ABOUT_URI:
        from .registry import load_tools
        names = ", ".join(sorted(load_tools()))
        text = "# Ervisio AI %s\n\nHost: %s\n\nPolicy: %s\n\nTools: %s\n" % (
            __version__, socket.gethostname(), policy_mod.describe(policy_mod.load()), names)
        return {"uri": uri, "mimeType": "text/markdown", "text": text}
    if uri == MEMORY_URI:
        return {"uri": uri, "mimeType": "text/markdown", "text": meta.read_notes() or "(no notes yet)"}
    if uri == SYSTEM_URI:
        return {"uri": uri, "mimeType": "text/plain", "text": system.overview_text()}
    if uri == AUDIT_URI:
        import json
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(audit.read(50), indent=2, ensure_ascii=False)}
    return None


_PROMPTS = [
    {
        "name": "server-audit",
        "description": "Audit this machine for security and health problems and report them by priority.",
        "arguments": [],
        "text": (
            "Audit this server. Read-only first: do not change anything yet.\n"
            "1. server_context, system_info, then users with login shells, sudo rights and SSH keys.\n"
            "2. Exposed services: listening ports (system_network) against the firewall status, and which of them "
            "need to be public.\n"
            "3. SSH configuration (root login, password auth), failed logins in the last 24 hours, fail2ban.\n"
            "4. Pending security updates, reboot required, services in failed state, disk and inode usage, "
            "memory pressure, time sync.\n"
            "5. Backups: do they exist and are they recent?\n"
            "Report findings as a table with severity, evidence and the exact fix. Ask me before you apply any fix."
        ),
    },
    {
        "name": "debug-service",
        "description": "Find out why a service is failing and fix it.",
        "arguments": [{"name": "service", "description": "systemd unit or container name", "required": True}],
        "text": (
            "The service '{service}' is not working. Find the root cause: its status, the last 200 log lines, the "
            "unit file or container config, the ports and files it uses, resource limits and recent changes "
            "(package updates, config edits). Explain the cause, propose the smallest fix, apply it once I agree, "
            "then verify the service is healthy and stays so."
        ),
    },
    {
        "name": "deploy-app",
        "description": "Deploy an application with a service, a reverse proxy and HTTPS.",
        "arguments": [
            {"name": "app", "description": "repository URL or description of the app", "required": True},
            {"name": "domain", "description": "domain name to serve it on", "required": False},
        ],
        "text": (
            "Deploy this application on this server: {app}. Domain: {domain}.\n"
            "Inspect what is already installed first (Docker, Caddy/nginx, runtimes) and reuse it. Put the app "
            "under /opt or /srv with its own user where it makes sense, run it as a systemd service or a Docker "
            "Compose stack that restarts on boot, put the reverse proxy and HTTPS in front, open only the ports "
            "needed, and write down how it is deployed with memory_write. Show me the plan before you start."
        ),
    },
    {
        "name": "harden-ssh",
        "description": "Harden SSH without locking yourself out.",
        "arguments": [],
        "text": (
            "Harden SSH on this server safely. Before changing anything confirm there is a working key-based login "
            "for a non-root sudo user and keep this connection open. Then: disable password and root login, limit "
            "users, set sane timeouts, validate with 'sshd -t', reload (do not restart), and test a NEW connection "
            "path before ending. Keep a timestamped backup of every file you touch and tell me how to roll back."
        ),
    },
    {
        "name": "free-disk-space",
        "description": "Find what fills the disk and clean it up safely.",
        "arguments": [{"name": "target_gb", "description": "how much space to free", "required": False}],
        "text": (
            "Free disk space on this server (target: {target_gb} GB). Find the biggest folders and files, old logs "
            "and journal, package caches, unused Docker images/volumes/build cache, old kernels, core dumps. List "
            "what you would delete with sizes, ask me, then delete only what I approve and report the space freed."
        ),
    },
    {
        "name": "setup-reverse-proxy",
        "description": "Put a domain in front of a local service with automatic HTTPS.",
        "arguments": [
            {"name": "domain", "description": "public domain", "required": True},
            {"name": "upstream", "description": "local address, e.g. 127.0.0.1:3000", "required": True},
        ],
        "text": (
            "Serve {domain} from {upstream} with HTTPS. Use Caddy if it is installed (or install it), otherwise "
            "nginx with certbot. Check DNS points here first, validate the config before reloading, open ports "
            "80 and 443 in the firewall, and verify with a real request. Back up any config you change."
        ),
    },
    {
        "name": "backup-plan",
        "description": "Set up scheduled, verified backups.",
        "arguments": [
            {"name": "what", "description": "what to back up", "required": True},
            {"name": "where", "description": "destination (path, S3, another server)", "required": True},
        ],
        "text": (
            "Set up backups of {what} to {where}. Pick a tool that fits (restic or borg if available, otherwise "
            "rsync/tar), schedule it with a systemd timer, keep a retention policy, send failures to the Ervisio "
            "notifications if configured, run one backup now and prove a restore works on a test folder."
        ),
    },
    {
        "name": "explain-this-server",
        "description": "Inventory this machine and save a map of it in server memory.",
        "arguments": [],
        "text": (
            "Make a map of this server for future sessions: hardware, OS, users, installed runtimes, running "
            "services and containers with their ports, web sites and domains, cron jobs and timers, backups, "
            "where data lives. Be factual and short. Save it with memory_write (mode replace) and show it to me."
        ),
    },
    {
        "name": "ervisio-tour",
        "description": "Explore what the Ervisio console on this machine can do and what it manages.",
        "arguments": [],
        "text": (
            "Use ervisio_status and ervisio_methods to explore the Ervisio console on this machine: version, "
            "installed plugins, available methods per area (services, files, logs, software, users, terminal, "
            "config...). Summarise what is managed here and suggest three things worth automating."
        ),
    },
    {
        "name": "incident-triage",
        "description": "Something is wrong right now: find out what.",
        "arguments": [{"name": "symptom", "description": "what you see", "required": False}],
        "text": (
            "Incident: {symptom}. Triage fast and read-only: load, memory, disk, failed units, recent errors in the "
            "journal, recent logins and changes (package, config, deploy), network and DNS. Give me a timeline and "
            "the most likely cause with evidence, then the safest next step."
        ),
    },
]


def prompts() -> List[Dict[str, Any]]:
    return [{"name": p["name"], "description": p["description"], "arguments": p["arguments"]} for p in _PROMPTS]


def get_prompt(name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for p in _PROMPTS:
        if p["name"] == name:
            values = {a["name"]: str(args.get(a["name"], "") or "(not given)") for a in p["arguments"]}
            text = p["text"]
            for k, v in values.items():
                text = text.replace("{%s}" % k, v)
            return {"description": p["description"], "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
    return None
