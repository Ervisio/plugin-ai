"""systemd services and cron."""
import os
import re
from typing import Any, Dict, Optional

from .. import policy, proc
from ..registry import Ctx, S, ToolError, fail, ok, register
from ..util import clip
from .shell import CONFIRM

UNIT_RE = re.compile(r"^[A-Za-z0-9:_.@\\-]{1,200}$")
ACTIONS = ["list", "status", "start", "stop", "restart", "reload", "enable", "disable", "mask", "unmask", "cat", "daemon_reload", "create_unit"]


def _systemctl() -> str:
    exe = proc.which("systemctl")
    if not exe or not os.path.isdir("/run/systemd/system"):
        raise ToolError("systemd is not running on this machine. Use shell_exec with the init system's own tools "
                        "(service, rc-service, supervisorctl...).")
    return exe


def _sc(*args: str, timeout: float = 60) -> Dict[str, Any]:
    return proc.run([_systemctl(), "--no-pager", "--no-ask-password"] + list(args), timeout=timeout)


def _unit(name: Optional[str]) -> str:
    if not name or not UNIT_RE.match(name):
        raise ToolError("unit is required (letters, digits and : _ . @ -).")
    return name


@register(
    "service_manage", "services",
    "Manage systemd units: list them, show status with recent log lines, start, stop, restart, reload, enable, "
    "disable, mask, read the unit file, reload systemd, or create a new unit file from text.",
    {
        "action": S("string", "What to do.", enum=ACTIONS),
        "unit": S("string", "Unit name, e.g. nginx or nginx.service (not needed for list/daemon_reload)."),
        "pattern": S("string", "list: only units whose name contains this."),
        "state": S("string", "list: running, failed, inactive or all (default all loaded units).", enum=["running", "failed", "inactive", "all"]),
        "now": S("boolean", "enable/disable: also start/stop it right now."),
        "lines": S("integer", "status: how many recent log lines (default 15).", minimum=0, maximum=500),
        "content": S("string", "create_unit: the full text of the unit file."),
        "confirm_dangerous": CONFIRM,
    },
    ["action"], title="Manage services", destructive=True, elevatable=True,
    read_only_actions={"list", "status", "cat"},
)
def service_manage(ctx: Ctx, action: str, unit: Optional[str] = None, pattern: Optional[str] = None, state: str = "all", now: bool = False,
                   lines: int = 15, content: Optional[str] = None, confirm_dangerous: bool = False):
    if action == "list":
        argv = ["list-units", "--type=service", "--all", "--no-legend", "--plain"]
        if state == "running":
            argv = ["list-units", "--type=service", "--state=running", "--no-legend", "--plain"]
        elif state == "failed":
            argv = ["list-units", "--type=service", "--state=failed", "--no-legend", "--plain"]
        elif state == "inactive":
            argv = ["list-units", "--type=service", "--state=inactive", "--no-legend", "--plain"]
        r = _sc(*argv)
        rows = [l for l in r["stdout"].splitlines() if not pattern or pattern.lower() in l.lower()]
        return ok("%-40s %-8s %-9s %-10s %s\n%s" % ("UNIT", "LOAD", "ACTIVE", "SUB", "DESCRIPTION", "\n".join(rows[:400])) if rows else "No units match.")
    if action == "daemon_reload":
        r = _sc("daemon-reload")
        return (ok if r["exit_code"] == 0 else fail)("systemd reloaded" if r["exit_code"] == 0 else r["stderr"].strip())
    u = _unit(unit)
    if action == "create_unit":
        if content is None:
            raise ToolError("content (the unit file text) is required.")
        name = u if re.search(r"\.(service|timer|socket|mount|path|target)$", u) else u + ".service"
        path = "/etc/systemd/system/" + name
        existed = os.path.exists(path)
        try:
            with open(path, "w") as f:
                f.write(content if content.endswith("\n") else content + "\n")
        except PermissionError:
            raise ToolError("Permission denied writing %s. Retry with sudo=true." % path)
        r = _sc("daemon-reload")
        note = "%s %s; systemd reloaded" % ("Replaced" if existed else "Created", path)
        if now:
            r = _sc("enable", "--now", name)
            note += "; enabled and started (%s)" % ("ok" if r["exit_code"] == 0 else r["stderr"].strip())
        else:
            note += ". Start it with action=start, enable it with action=enable."
        return ok(note)
    if action == "status":
        r = _sc("show", u, "-p", "Id,Description,LoadState,ActiveState,SubState,UnitFileState,MainPID,MemoryCurrent,Result,"
                                "ActiveEnterTimestamp,FragmentPath,ExecStart")
        if "LoadState=not-found" in r["stdout"]:
            raise ToolError("Unit %s was not found. Use action=list with a pattern." % u)
        text = r["stdout"].strip()
        if lines:
            jc = proc.which("journalctl")
            if jc:
                j = proc.run([jc, "--no-pager", "-o", "short-iso", "-u", u, "-n", str(lines)], timeout=20)
                text += "\n\n--- last %d log lines ---\n%s" % (lines, (j["stdout"] or j["stderr"]).strip())
        return ok(text)
    if action == "cat":
        r = _sc("cat", u)
        return (ok if r["exit_code"] == 0 else fail)(clip(r["stdout"] or r["stderr"], 100000))
    verb = action.replace("_", "-")
    policy.guard_command(ctx.policy, "systemctl %s %s" % (verb, u.replace(".service", "")), {"confirm_dangerous": confirm_dangerous})
    argv = [verb] + (["--now"] if now and action in ("enable", "disable") else []) + [u]
    r = _sc(*argv, timeout=120)
    if r["exit_code"] != 0:
        return fail("systemctl %s %s failed (exit %d): %s" % (verb, u, r["exit_code"], (r["stderr"] or r["stdout"]).strip()))
    after = _sc("is-active", u)["stdout"].strip()
    return ok("%s %s: ok. Now %s." % (verb, u, after or "unknown"), {"active": after})


# --- cron ------------------------------------------------------------------------------------------------------

@register(
    "cron_manage", "services",
    "Scheduled jobs: list a user's crontab, /etc/cron.d files and systemd timers; add a crontab line; remove lines "
    "containing some text.",
    {
        "action": S("string", "list, add or remove.", enum=["list", "add", "remove"]),
        "user": S("string", "Whose crontab (default: the server user)."),
        "schedule": S("string", "add: five cron fields or @daily, @hourly, @reboot..., e.g. '30 3 * * *'."),
        "command": S("string", "add: the command to run."),
        "match": S("string", "remove: delete every crontab line containing this text."),
    },
    ["action"], title="Cron jobs", destructive=True, elevatable=True, read_only_actions={"list"},
)
def cron_manage(ctx: Ctx, action: str, user: Optional[str] = None, schedule: Optional[str] = None, command: Optional[str] = None,
                match: Optional[str] = None):
    crontab = proc.which("crontab")
    if not crontab:
        raise ToolError("crontab is not installed (package cron/cronie). systemd timers can be used instead.")
    who = ["-u", user] if user else []

    def current() -> str:
        r = proc.run([crontab, "-l"] + who, timeout=10)
        return r["stdout"] if r["exit_code"] == 0 else ""

    if action == "list":
        parts = ["# crontab of %s\n%s" % (user or "this user", current().rstrip() or "(empty)")]
        if os.path.isdir("/etc/cron.d"):
            for n in sorted(os.listdir("/etc/cron.d")):
                try:
                    with open(os.path.join("/etc/cron.d", n)) as f:
                        body = [l for l in f.read().splitlines() if l.strip() and not l.startswith("#")]
                    if body:
                        parts.append("# /etc/cron.d/%s\n%s" % (n, "\n".join(body)))
                except OSError:
                    pass
        if proc.which("systemctl"):
            r = proc.run(["systemctl", "list-timers", "--all", "--no-pager"], timeout=10)
            parts.append("# systemd timers\n" + r["stdout"].strip())
        return ok("\n\n".join(parts))
    if action == "add":
        if not schedule or not command:
            raise ToolError("schedule and command are required.")
        if not re.match(r"^(@\w+|\S+\s+\S+\s+\S+\s+\S+\s+\S+)$", schedule.strip()):
            raise ToolError("schedule must be five cron fields or @daily-style, got %r." % schedule)
        text = current()
        text = text + ("" if text.endswith("\n") or not text else "\n") + "%s %s\n" % (schedule.strip(), command.strip())
        r = proc.run([crontab] + who + ["-"], stdin=text, timeout=10)
        if r["exit_code"] != 0:
            raise ToolError("crontab refused it: %s" % r["stderr"].strip())
        return ok("Added: %s %s" % (schedule.strip(), command.strip()))
    if not match:
        raise ToolError("match is required.")
    lines = current().splitlines()
    kept = [l for l in lines if match not in l]
    if len(kept) == len(lines):
        raise ToolError("No crontab line contains %r." % match)
    r = proc.run([crontab] + who + ["-"], stdin="\n".join(kept) + "\n", timeout=10)
    if r["exit_code"] != 0:
        raise ToolError("crontab refused it: %s" % r["stderr"].strip())
    return ok("Removed %d line%s." % (len(lines) - len(kept), "" if len(lines) - len(kept) == 1 else "s"))
