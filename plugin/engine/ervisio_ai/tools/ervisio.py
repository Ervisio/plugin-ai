"""The Ervisio console on this machine: every module of its bridge, its plugins and its activity log."""
import glob
import json
import os
import re
import ssl
import urllib.request
from typing import Any, Dict, List, Optional

from .. import proc
from ..bridge import BridgeError, find_bridge, get_bridge
from ..registry import Ctx, S, ToolError, ok, register
from ..util import clip

CONF = "/etc/ervisio/ervisio.conf"
AUDIT_DIR = "/var/lib/ervisio/audit"


def _bridge_call(ctx: Ctx, method: str, params: Optional[Dict[str, Any]] = None, admin: Optional[bool] = None, timeout: float = 60) -> Any:
    try:
        return get_bridge(admin).call(method, params, timeout=timeout, cancel=ctx.cancel)
    except BridgeError as e:
        raise ToolError("Ervisio %s: %s (%s)" % (method, e.message, e.code))


def _daemon_health() -> str:
    listen = "0.0.0.0:9090"
    try:
        with open(CONF) as f:
            m = re.search(r'^\s*listen\s*=\s*"([^"]+)"', f.read(), re.M)
            if m:
                listen = m.group(1)
    except OSError:
        pass
    port = listen.rsplit(":", 1)[-1]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for scheme in ("https", "http"):
        try:
            with urllib.request.urlopen("%s://127.0.0.1:%s/api/health" % (scheme, port), timeout=3, context=ctx) as r:
                body = json.loads(r.read().decode())
                return "up (%s://127.0.0.1:%s) version %s" % (scheme, port, body.get("version", "?"))
        except Exception:
            continue
    return "not answering on port %s" % port


@register("ervisio_status", "ervisio",
          "State of the Ervisio console on this machine: version, whether the daemon answers, the bridge, and the installed plugins.",
          {}, title="Ervisio status", read_only=True, idempotent=True)
def ervisio_status(ctx: Ctx):
    lines = []  # type: List[str]
    path = find_bridge()
    if not path:
        return ok("Ervisio is not installed on this machine (no ervisio-bridge found). The shell, files and system tools still work.",
                  {"installed": False})
    lines.append("bridge: " + path)
    try:
        b = get_bridge()
        h = b.hello
        lines.append("bridge version %s, running as uid %s, %s, %d methods" % (h.get("version"), h.get("uid"),
                     "administrator" if h.get("admin") else "user", len(h.get("methods", {}))))
    except BridgeError as e:
        lines.append("bridge: cannot start (%s)" % e.message)
        return ok("\n".join(lines), {"installed": True, "bridge": False})
    lines.append("daemon: " + _daemon_health())
    if proc.which("systemctl"):
        r = proc.run(["systemctl", "is-active", "ervisio"], timeout=5)
        lines.append("service ervisio: " + (r["stdout"].strip() or "unknown"))
    try:
        plugins = _bridge_call(ctx, "plugins.list")
        lines.append("plugins (%d):" % len(plugins))
        for p in plugins:
            lines.append("  %-14s %-8s %s%s" % (p.get("id"), p.get("version"), "enabled" if p.get("enabled") else "disabled",
                                                  "  [%s]" % p.get("location", "") if p.get("location") else ""))
    except ToolError as e:
        lines.append("plugins: %s" % e)
    return ok("\n".join(lines), {"installed": True, "bridge": True, "version": h.get("version")})


@register(
    "ervisio_methods", "ervisio",
    "List the Ervisio methods you can call with ervisio_call, grouped by area (services, files, logs, software, users, "
    "terminal, plugins, config, updates, system, prefs, overview). 'admin' ones need root; 'stream' ones deliver events.",
    {"filter": S("string", "Only methods containing this text, e.g. 'services' or 'plugins.'.")},
    title="List Ervisio methods", read_only=True, idempotent=True,
)
def ervisio_methods(ctx: Ctx, filter: Optional[str] = None):  # noqa: A002
    try:
        h = get_bridge().hello
    except BridgeError as e:
        raise ToolError(e.message)
    methods = h.get("methods", {})
    streams = set(h.get("streams", []))
    names = sorted(m for m in methods if not filter or filter in m)
    if not names:
        return ok("No method matches %r." % filter)
    out, area = [], ""
    for n in names:
        a = n.split(".")[0]
        if a != area:
            area = a
            out.append("\n[%s]" % a)
        out.append("  %-34s %s%s" % (n, methods[n], " stream" if n in streams else ""))
    return ok("%d methods\n%s\n\nCall them with ervisio_call {method, params}. The reference for the parameters is docs/api/<area>.md "
              "in https://github.com/Ervisio/ervisio." % (len(names), "\n".join(out)), {"methods": names})


@register(
    "ervisio_call", "ervisio",
    "Call any method of the Ervisio console (the same API its web UI uses) as the server's user, with full "
    "administrator rights when the server runs as root. Examples: services.list, services.restart {unit}, "
    "files.list {path}, logs.query, software.updates, users.list, terminal.list, config.get, plugins.list, "
    "system.metrics. Stream methods (logs.follow, system.metricsStream) are sampled for a few seconds.",
    {
        "method": S("string", "Method name such as services.list (see ervisio_methods)."),
        "params": S("object", "The method's parameters as an object (default {})."),
        "admin": S("boolean", "Use the administrator bridge (default: yes when the server is root)."),
        "timeout": S("integer", "Seconds to wait (default 60).", minimum=1, maximum=900),
        "seconds": S("integer", "Stream methods: how long to collect events (default 5).", minimum=1, maximum=120),
        "max_events": S("integer", "Stream methods: stop after this many events (default 50).", minimum=1, maximum=1000),
    },
    ["method"], title="Call an Ervisio method", destructive=True,
)
def ervisio_call(ctx: Ctx, method: str, params: Optional[Dict[str, Any]] = None, admin: Optional[bool] = None, timeout: int = 60,
                 seconds: int = 5, max_events: int = 50):
    try:
        b = get_bridge(admin)
        if b.is_stream(method):
            events, ended = b.stream(method, params, seconds=seconds, max_events=max_events, cancel=ctx.cancel)
            text = json.dumps(events, indent=1, ensure_ascii=False, default=str)
            return ok("%d event%s%s\n%s" % (len(events), "" if len(events) == 1 else "s", " (stream ended)" if ended else "", clip(text, 200000)),
                      {"events": events})
        result = b.call(method, params, timeout=timeout, cancel=ctx.cancel)
    except BridgeError as e:
        hint = ""
        if e.code == "needs_admin":
            hint = " This method needs administrator rights: run the server as root."
        raise ToolError("Ervisio %s failed: %s (%s).%s" % (method, e.message, e.code, hint))
    text = json.dumps(result, indent=1, ensure_ascii=False, default=str)
    return ok(clip(text, 300000), {"result": result})


@register(
    "ervisio_plugin_exec", "ervisio",
    "Run a command declared by an installed Ervisio plugin (for example the docker plugin's own commands) exactly as the "
    "plugin's page would, with the plugin's argument rules. Use ervisio_call plugins.list to see plugins and their "
    "commands.",
    {
        "plugin": S("string", "Plugin id, e.g. docker."),
        "command": S("string", "Command name declared in the plugin's manifest."),
        "args": S("array", "Arguments, in the order the command declares them.", items={"type": "string"}),
    },
    ["plugin", "command"], title="Run a plugin command", destructive=True,
)
def ervisio_plugin_exec(ctx: Ctx, plugin: str, command: str, args: Optional[List[str]] = None):
    plugins = _bridge_call(ctx, "plugins.list")
    entry = next((p for p in plugins if p.get("id") == plugin), None)
    if entry is None:
        raise ToolError("There is no plugin %r. Installed: %s" % (plugin, ", ".join(p.get("id", "?") for p in plugins) or "none"))
    cmd = next((c for c in entry.get("capabilities", {}).get("commands", []) if c.get("name") == command), None)
    if cmd is None:
        names = ", ".join(c.get("name", "?") for c in entry.get("capabilities", {}).get("commands", []))
        raise ToolError("Plugin %s has no command %r. It declares: %s" % (plugin, command, names or "none"))
    res = _bridge_call(ctx, "plugins.exec", {"plugin": plugin, "command": command, "args": args or []},
                       admin=bool(cmd.get("admin")), timeout=float(cmd.get("timeoutSec", 30)) + 5)
    text = "exit %s\n%s%s" % (res.get("exitCode"), res.get("stdout", ""), ("\n[stderr]\n" + res["stderr"]) if res.get("stderr") else "")
    return ok(clip(text, 200000), res)


@register(
    "ervisio_audit", "ervisio",
    "Read Ervisio's own activity log (who signed in, which plugin commands ran, settings and plugin changes). "
    "Newest first.",
    {
        "limit": S("integer", "Entries to return (default 50).", minimum=1, maximum=1000),
        "text": S("string", "Only entries containing this text."),
        "plugin": S("string", "Only this plugin's entries."),
        "user": S("string", "Only this account."),
    },
    title="Ervisio activity log", read_only=True, idempotent=True, elevatable=True,
)
def ervisio_audit(ctx: Ctx, limit: int = 50, text: Optional[str] = None, plugin: Optional[str] = None, user: Optional[str] = None):
    files = sorted(glob.glob(os.path.join(AUDIT_DIR, "audit-*.jsonl")), reverse=True)
    if not files:
        raise ToolError("No Ervisio activity log at %s (it is readable by root only: retry with sudo=true)." % AUDIT_DIR)
    out = []  # type: List[str]
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as e:
            raise ToolError("%s: %s (retry with sudo=true)" % (f, e.strerror))
        for raw in reversed(lines):
            if text and text.lower() not in raw.lower():
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if plugin and e.get("plugin") != plugin or user and e.get("user") != user:
                continue
            out.append("%s %-10s %-9s %-8s %s %s" % (e.get("time", "")[:19], e.get("user", ""), e.get("plugin", e.get("source", "")),
                                                    e.get("action", ""), e.get("result", ""), clip(str(e.get("target", "")), 160)))
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return ok("\n".join(out) if out else "No matching entries.", {"count": len(out)})
