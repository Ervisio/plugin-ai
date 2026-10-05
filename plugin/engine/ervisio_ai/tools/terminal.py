"""Persistent interactive terminals, on top of tmux.

tmux renders the screen for us, so the model reads what a person would see (editors, installers, progress bars all
make sense) and the sessions survive a dropped connection or a restart of this server. A person can watch or take
over with ``tmux -S <socket> attach -t eai-<name>``, for example from Ervisio's Terminal page.
"""
import os
import re
import time
from typing import Any, Dict, List, Optional

from .. import paths, proc
from ..registry import Ctx, S, ToolError, ok, register
from .files import resolve

PREFIX = "eai-"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
KEY_RE = re.compile(r"^[\w+\-#]{1,24}$")


def socket_path() -> str:
    return os.path.join(paths.sub(create=True), "tmux.sock")


def tmux(*args: str, timeout: float = 15) -> Dict[str, Any]:
    exe = proc.which("tmux")
    if not exe:
        raise ToolError("tmux is not installed. Install it with package_manage (package 'tmux') to use the terminal tools.")
    env = {"TERM": "xterm-256color"}
    return proc.run([exe, "-S", socket_path()] + list(args), timeout=timeout, env=env)


def _target(session_id: str) -> str:
    name = session_id[len(PREFIX):] if session_id.startswith(PREFIX) else session_id
    if not NAME_RE.match(name):
        raise ToolError("%r is not a terminal id (use terminal_list)." % session_id)
    full = PREFIX + name
    r = tmux("has-session", "-t", "=" + full)
    if r["exit_code"] != 0:
        raise ToolError("There is no terminal %r. Use terminal_list, or terminal_open to start one." % name)
    return "=" + full + ":"


def _capture(target: str, lines: int) -> str:
    r = tmux("capture-pane", "-p", "-J", "-t", target, "-S", "-%d" % max(lines, 1))
    text = r["stdout"].rstrip()
    rows = text.split("\n")
    return "\n".join(rows[-lines:]) if text else "(blank screen)"


def _status(target: str) -> str:
    r = tmux("display-message", "-p", "-t", target, "#{pane_dead}|#{pane_current_command}|#{cursor_x}|#{cursor_y}|#{alternate_on}|#{pane_width}x#{pane_height}")
    parts = r["stdout"].strip().split("|")
    if len(parts) < 6:
        return ""
    dead, cmd, cx, cy, alt, size = parts[:6]
    bits = ["exited" if dead == "1" else "running: " + cmd, "cursor %s,%s" % (cx, cy), size]
    if alt == "1":
        bits.append("full-screen program")
    return ", ".join(bits)


def _settle(target: str, lines: int, wait_ms: int, settle_ms: int, wait_for: Optional[str], ctx: Ctx) -> str:
    """Wait until the screen stops changing (or matches wait_for), then return it."""
    rx = re.compile(wait_for, re.M) if wait_for else None
    deadline = time.time() + wait_ms / 1000.0
    last, stable_since = None, time.time()
    while True:
        screen = _capture(target, lines)
        if rx and rx.search(screen):
            return screen
        if screen != last:
            last, stable_since = screen, time.time()
        elif not rx and (time.time() - stable_since) * 1000 >= settle_ms:
            return screen
        if time.time() >= deadline or ctx.cancel.is_set():
            return screen
        time.sleep(0.1)


def _render(name: str, target: str, screen: str, note: str = "") -> str:
    return "[%s] %s%s\n%s" % (name, _status(target), note, screen)


@register(
    "terminal_open", "terminal",
    "Open a persistent interactive terminal (a real shell on a pseudo-terminal, kept in tmux). Use it for programs "
    "that ask questions or take over the screen: editors, installers, REPLs, ssh sessions, long watch commands. "
    "Then drive it with terminal_send and read it with terminal_read.",
    {
        "name": S("string", "Short name (letters, digits, . _ -). Default: t1, t2, ..."),
        "cwd": S("string", "Starting folder."),
        "command": S("string", "Program to run instead of the login shell, e.g. 'python3' or 'htop'."),
        "sudo": S("boolean", "Start a root shell (sudo -i) when the server is not root."),
        "cols": S("integer", "Width in characters (default 200).", minimum=20, maximum=500),
        "rows": S("integer", "Height in lines (default 50).", minimum=5, maximum=200),
    },
    title="Open a terminal", destructive=True, open_world=True,
)
def terminal_open(ctx: Ctx, name: Optional[str] = None, cwd: Optional[str] = None, command: Optional[str] = None,
                  sudo: bool = False, cols: int = 200, rows: int = 50):
    existing = _sessions()
    if name is None:
        n = 1
        while "t%d" % n in [s["name"] for s in existing]:
            n += 1
        name = "t%d" % n
    if not NAME_RE.match(name):
        raise ToolError("name may only contain letters, digits, . _ - (max 40).")
    if name in [s["name"] for s in existing]:
        raise ToolError("A terminal named %r already exists. Use it, close it, or pick another name." % name)
    argv = ["start-server", ";", "set-option", "-g", "remain-on-exit", "on", ";",
            "new-session", "-d", "-s", PREFIX + name, "-x", str(cols), "-y", str(rows)]
    if cwd:
        folder = resolve(cwd)
        if not os.path.isdir(folder):
            raise ToolError("cwd %s is not a directory." % folder)
        argv += ["-c", folder]
    start = proc.shell_argv(command) if command else [proc.shell_path()]
    if sudo and os.geteuid() != 0:
        start = ["sudo", "-n", "--"] + start if command else ["sudo", "-n", "-i"]
    argv += [proc.quote(start)]
    r = tmux(*argv)
    if r["exit_code"] != 0:
        raise ToolError("tmux could not start the terminal: %s" % (r["stderr"].strip() or r["stdout"].strip()))
    time.sleep(0.3)
    target = "=" + PREFIX + name + ":"
    screen = _capture(target, 20)
    hint = "Watch or take over: tmux -S %s attach -t %s" % (socket_path(), PREFIX + name)
    return ok("Opened terminal %r.\n%s\n%s" % (name, hint, _render(name, target, screen)), {"id": name, "socket": socket_path()})


@register(
    "terminal_send", "terminal",
    "Type into a terminal and return what the screen shows afterwards. text is typed literally; keys sends special keys "
    "(Enter, Tab, Escape, C-c, C-d, Up, Down, BSpace, Space, F1...). Set enter=true to press Enter after the text. "
    "Waits until the screen stops changing, or until wait_for (a regular expression) appears.",
    {
        "id": S("string", "Terminal name from terminal_open."),
        "text": S("string", "Text to type, exactly as given."),
        "keys": S("array", "tmux key names to press after the text, e.g. ['C-c'] or ['Down','Down','Enter'].", items={"type": "string"}),
        "enter": S("boolean", "Press Enter after the text."),
        "wait_for": S("string", "Regular expression to wait for on the screen, e.g. '\\$ $' or 'Proceed\\?'."),
        "wait_ms": S("integer", "Longest wait for the screen in ms (default 3000, max 120000).", minimum=0, maximum=120000),
        "settle_ms": S("integer", "Screen unchanged for this long counts as done (default 400).", minimum=50, maximum=10000),
        "lines": S("integer", "Lines of screen/scrollback to return (default 50).", minimum=1, maximum=2000),
    },
    ["id"], title="Type into a terminal", destructive=True, open_world=True,
)
def terminal_send(ctx: Ctx, id: str, text: Optional[str] = None, keys: Optional[List[str]] = None, enter: bool = False,
                  wait_for: Optional[str] = None, wait_ms: int = 3000, settle_ms: int = 400, lines: int = 50):
    target = _target(id)
    if text:
        r = tmux("send-keys", "-t", target, "-l", "--", text)
        if r["exit_code"] != 0:
            raise ToolError("could not type: %s" % r["stderr"].strip())
    for k in keys or []:
        if not KEY_RE.match(k):
            raise ToolError("%r is not a key name." % k)
    seq = list(keys or []) + (["Enter"] if enter else [])
    if seq:
        r = tmux("send-keys", "-t", target, *seq)
        if r["exit_code"] != 0:
            raise ToolError("could not press keys: %s" % r["stderr"].strip())
    try:
        screen = _settle(target, lines, wait_ms, settle_ms, wait_for, ctx)
    except re.error as e:
        raise ToolError("bad wait_for expression: %s" % e)
    name = id[len(PREFIX):] if id.startswith(PREFIX) else id
    note = ""
    if wait_for and not re.search(wait_for, screen, re.M):
        note = " (wait_for not seen yet)"
    return ok(_render(name, target, screen, note))


@register(
    "terminal_read", "terminal",
    "Read a terminal's screen (and scrollback) without typing. Use wait_ms to wait for a running program to produce more.",
    {
        "id": S("string", "Terminal name."),
        "lines": S("integer", "Lines of screen/scrollback to return (default 50).", minimum=1, maximum=5000),
        "wait_ms": S("integer", "Wait up to this long for the screen to change and settle (default 0).", minimum=0, maximum=120000),
        "wait_for": S("string", "Regular expression to wait for."),
    },
    ["id"], title="Read a terminal", read_only=True,
)
def terminal_read(ctx: Ctx, id: str, lines: int = 50, wait_ms: int = 0, wait_for: Optional[str] = None):
    target = _target(id)
    name = id[len(PREFIX):] if id.startswith(PREFIX) else id
    screen = _settle(target, lines, wait_ms, 400, wait_for, ctx) if wait_ms else _capture(target, lines)
    return ok(_render(name, target, screen))


def _sessions() -> List[Dict[str, Any]]:
    if not proc.which("tmux"):
        return []
    r = tmux("list-sessions", "-F", "#{session_name}|#{session_created}|#{session_attached}|#{pane_current_command}|#{pane_current_path}|#{pane_dead}")
    out = []
    for line in r["stdout"].splitlines():
        parts = line.split("|")
        if len(parts) >= 6 and parts[0].startswith(PREFIX):
            out.append({"name": parts[0][len(PREFIX):], "created": int(parts[1] or 0), "attached": parts[2] != "0",
                        "command": parts[3], "cwd": parts[4], "exited": parts[5] == "1"})
    return out


@register("terminal_list", "terminal", "List the open terminals with what runs in them and where.", {},
          title="List terminals", read_only=True, idempotent=True)
def terminal_list(ctx: Ctx):
    sessions = _sessions()
    if not sessions:
        return ok("No terminals open.", {"sessions": []})
    lines = ["%-12s %-14s %-9s %s" % ("NAME", "RUNNING", "SINCE", "CWD")]
    for s in sessions:
        lines.append("%-12s %-14s %-9s %s%s" % (s["name"], "(exited)" if s["exited"] else s["command"],
                                                time.strftime("%H:%M:%S", time.localtime(s["created"])), s["cwd"],
                                                "  [someone is attached]" if s["attached"] else ""))
    lines.append("Attach from a shell: tmux -S %s attach -t %s<name>" % (socket_path(), PREFIX))
    return ok("\n".join(lines), {"sessions": sessions})


@register("terminal_close", "terminal", "Close a terminal and everything running in it.",
          {"id": S("string", "Terminal name.")}, ["id"], title="Close a terminal", destructive=True)
def terminal_close(ctx: Ctx, id: str):
    target = _target(id)
    r = tmux("kill-session", "-t", target.rstrip(":"))
    if r["exit_code"] != 0:
        raise ToolError("could not close it: %s" % r["stderr"].strip())
    return ok("Closed terminal %s." % id)


@register("terminal_resize", "terminal", "Change the size of a terminal (some programs lay themselves out by it).",
          {"id": S("string", "Terminal name."), "cols": S("integer", "Width.", minimum=20, maximum=500),
           "rows": S("integer", "Height.", minimum=5, maximum=200)},
          ["id", "cols", "rows"], title="Resize a terminal")
def terminal_resize(ctx: Ctx, id: str, cols: int, rows: int):
    target = _target(id)
    r = tmux("resize-window", "-t", target, "-x", str(cols), "-y", str(rows))
    if r["exit_code"] != 0:
        raise ToolError("could not resize: %s" % r["stderr"].strip())
    return ok("Resized to %dx%d." % (cols, rows))
