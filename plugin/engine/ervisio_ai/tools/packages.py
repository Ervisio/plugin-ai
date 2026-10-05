"""Packages, with whichever package manager the distribution uses."""
import re
from typing import List, Optional

from .. import proc
from ..registry import Ctx, S, ToolError, fail, ok, register
from .shell import render

PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:@=~/*-]{0,200}$")


def detect() -> Optional[str]:
    for name in ("apt-get", "dnf", "yum", "pacman", "apk", "zypper"):
        if proc.which(name):
            return name
    return None


def build(pm: str, action: str, pkgs: List[str], query: str, purge: bool) -> List[str]:
    if pm == "apt-get":
        apt = ["apt-get", "-y", "-o", "DPkg::Lock::Timeout=120", "-o", "Dpkg::Options::=--force-confold"]
        return {
            "search": ["apt-cache", "search", query],
            "info": ["apt-cache", "show"] + pkgs,
            "install": apt + ["install"] + pkgs,
            "remove": apt + ["purge" if purge else "remove"] + pkgs,
            "update": apt + ["update"],
            "upgrade": apt + (["install", "--only-upgrade"] + pkgs if pkgs else ["upgrade"]),
            "list": ["dpkg-query", "-W", "-f=${Package} ${Version} ${Status}\n"],
            "autoremove": apt + ["autoremove"],
        }[action]
    if pm in ("dnf", "yum"):
        return {
            "search": [pm, "search", query],
            "info": [pm, "info"] + pkgs,
            "install": [pm, "install", "-y"] + pkgs,
            "remove": [pm, "remove", "-y"] + pkgs,
            "update": [pm, "makecache"],
            "upgrade": [pm, "upgrade", "-y"] + pkgs,
            "list": ["rpm", "-qa", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n"],
            "autoremove": [pm, "autoremove", "-y"],
        }[action]
    if pm == "pacman":
        return {
            "search": ["pacman", "-Ss", query],
            "info": ["pacman", "-Si"] + pkgs,
            "install": ["pacman", "-S", "--noconfirm", "--needed"] + pkgs,
            "remove": ["pacman", "-Rs", "--noconfirm"] + pkgs,
            "update": ["pacman", "-Sy"],
            "upgrade": ["pacman", "-Syu", "--noconfirm"] if not pkgs else ["pacman", "-S", "--noconfirm"] + pkgs,
            "list": ["pacman", "-Q"],
        }.get(action) or []
    if pm == "apk":
        return {
            "search": ["apk", "search", query],
            "info": ["apk", "info", "-a"] + pkgs,
            "install": ["apk", "add"] + pkgs,
            "remove": ["apk", "del"] + pkgs,
            "update": ["apk", "update"],
            "upgrade": ["apk", "upgrade"] + pkgs,
            "list": ["apk", "list", "-I"],
        }.get(action) or []
    if pm == "zypper":
        return {
            "search": ["zypper", "--non-interactive", "search", query],
            "info": ["zypper", "--non-interactive", "info"] + pkgs,
            "install": ["zypper", "--non-interactive", "install", "-y"] + pkgs,
            "remove": ["zypper", "--non-interactive", "remove", "-y"] + pkgs,
            "update": ["zypper", "--non-interactive", "refresh"],
            "upgrade": ["zypper", "--non-interactive", "update", "-y"] + pkgs,
            "list": ["rpm", "-qa", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n"],
            "autoremove": [],
        }.get(action) or []
    return []


@register(
    "package_manage", "packages",
    "Install, remove, search, update and list software with the machine's package manager (apt, dnf, yum, pacman, "
    "apk or zypper, detected automatically). Runs non-interactively; long installs stream progress.",
    {
        "action": S("string", "What to do.", enum=["search", "info", "install", "remove", "update", "upgrade", "list", "autoremove"]),
        "packages": S("array", "Package names (install, remove, info, and upgrade of specific packages).", items={"type": "string"}),
        "query": S("string", "search: text to look for. list: only installed packages containing this."),
        "purge": S("boolean", "remove: also delete configuration files (apt only)."),
        "timeout": S("integer", "Seconds to allow (default 1800).", minimum=10, maximum=86400),
    },
    ["action"], title="Manage packages", destructive=True, open_world=True, elevatable=True,
    read_only_actions={"search", "info", "list"},
)
def package_manage(ctx: Ctx, action: str, packages: Optional[List[str]] = None, query: str = "", purge: bool = False, timeout: int = 1800):
    pm = detect()
    if pm is None:
        raise ToolError("No supported package manager found (apt, dnf, yum, pacman, apk, zypper).")
    pkgs = packages or []
    for p in pkgs:
        if not PKG_RE.match(p):
            raise ToolError("%r is not a valid package name." % p)
    if action in ("install", "remove", "info") and not pkgs:
        raise ToolError("packages is required for %s." % action)
    if action == "search" and not query:
        raise ToolError("query is required for search.")
    argv = build(pm, action, pkgs, query, purge)
    if not argv:
        raise ToolError("%s is not supported with %s." % (action, pm))
    res = proc.run(argv, timeout=min(timeout, ctx.policy["max_timeout_sec"]), cancel=ctx.cancel, max_output=200000,
                   on_output=lambda s, t: ctx.progress(t.rstrip("\n")[-300:]))
    if action == "list":
        rows = [l for l in res["stdout"].splitlines() if (not query or query.lower() in l.lower()) and (pm != "apt-get" or "installed" in l)]
        rows = [l.replace(" install ok installed", "") for l in rows]
        return ok("%d package%s%s\n%s" % (len(rows), "" if len(rows) == 1 else "s", " matching %r" % query if query else "", "\n".join(rows[:1000])))
    text = "%s via %s\n%s" % (action, pm, render(res, None))
    return (ok if res["exit_code"] == 0 else fail)(text, {"exit_code": res["exit_code"]})
