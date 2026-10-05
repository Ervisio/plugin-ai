"""server_context and the server's memory notes."""
import os
import socket
import time
from typing import Dict

from .. import __version__, paths, policy, proc
from ..registry import Ctx, S, ToolError, ok, register
from ..util import clip, now_iso

NOTES_LIMIT = 200000


def notes_path() -> str:
    return os.path.join(paths.data_dir(), "notes.md")


def read_notes() -> str:
    try:
        with open(notes_path(), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def detect_tools() -> Dict[str, bool]:
    names = ["systemctl", "docker", "tmux", "git", "python3", "node", "caddy", "nginx", "ufw", "firewall-cmd", "nft", "rg", "curl",
             "apt-get", "dnf", "pacman", "apk", "zypper", "ervisiod", "ervisio-bridge"]
    return {n: proc.which(n) is not None for n in names}


@register(
    "server_context", "meta",
    "START HERE. Says which machine this is, who you run as, the policy that applies, which tools work here (systemd, "
    "Docker, tmux, Ervisio...) and what earlier sessions wrote in the server memory.",
    {}, title="Server context", read_only=True, idempotent=True,
)
def server_context(ctx: Ctx):
    from . import system
    from ..bridge import find_bridge

    present = detect_tools()
    notes = read_notes().strip()
    lines = [
        "Ervisio AI %s on %s" % (__version__, socket.gethostname()),
        "time: %s (%s)" % (now_iso(), time.strftime("%Z")),
        "running as: %s%s" % (system.current_user(), " (root)" if os.geteuid() == 0 else ""),
        "client: %s via %s (%s scope)" % (ctx.client, ctx.transport, ctx.scope),
        "policy: " + policy.describe(ctx.policy),
        "",
        system.overview_text(),
        "",
        "available: " + ", ".join(sorted(k for k, v in present.items() if v)),
        "missing: " + (", ".join(sorted(k for k, v in present.items() if not v)) or "-"),
        "ervisio bridge: " + (find_bridge() or "not found (the ervisio_* tools need Ervisio on this machine)"),
    ]
    if present.get("tmux") is False:
        lines.append("note: install tmux (package_manage) to use the terminal_* tools.")
    lines.append("")
    lines.append("server memory (%d characters)%s" % (len(notes), ":" if notes else ": empty"))
    if notes:
        lines.append(clip(notes, 6000))
    return ok("\n".join(lines), {"hostname": socket.gethostname(), "root": os.geteuid() == 0, "tools": present, "version": __version__})


@register("memory_read", "meta", "Read the server memory: notes that earlier AI sessions left about this machine.",
          {}, title="Read server memory", read_only=True, idempotent=True)
def memory_read(ctx: Ctx):
    text = read_notes()
    return ok(text if text.strip() else "(the server memory is empty)")


@register(
    "memory_write", "meta",
    "Write to the server memory, shared by every AI client of this server. Record what a future session needs: how "
    "apps are deployed, where data lives, decisions, quirks. Keep it factual and short. mode=append adds a dated "
    "entry; mode=replace rewrites everything (read it first).",
    {
        "text": S("string", "Markdown text to store."),
        "mode": S("string", "append (default) or replace.", enum=["append", "replace"]),
    },
    ["text"], title="Write server memory", idempotent=False,
)
def memory_write(ctx: Ctx, text: str, mode: str = "append"):
    if len(text) > NOTES_LIMIT:
        raise ToolError("The note is too long (limit %d characters)." % NOTES_LIMIT)
    path = notes_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if mode == "replace":
        body = text.rstrip() + "\n"
    else:
        body = read_notes().rstrip() + "\n\n## %s (%s)\n%s\n" % (now_iso(), ctx.client, text.strip())
        body = body.lstrip("\n")
    if len(body) > NOTES_LIMIT:
        raise ToolError("The memory would grow past %d characters: summarise it with mode=replace first." % NOTES_LIMIT)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, 0o600)
    return ok("Saved (%d characters in the server memory)." % len(body))
