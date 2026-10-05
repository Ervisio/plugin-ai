"""What the AI may do on this machine.

The default is **full access**: every tool, root included. The administrator narrows it in ``policy.json`` (written
from the Ervisio page or by hand). A token can be narrower still (``readonly``). The *guard* is not a limit: it only
stops a handful of commands that cannot be undone or lock everybody out and asks the model to repeat the call with
``confirm_dangerous: true``, so a mistake costs one more round trip and a deliberate choice costs nothing.
"""
import os
import re
from typing import Any, Dict, Optional

from . import paths
from .util import read_json, write_json

DEFAULTS = {
    "mode": "full",  # full | readonly
    "disabled_tools": [],
    "disabled_categories": [],
    "allow_root": True,
    "guard": True,
    "deny_paths": [],
    "ervisio_deny_methods": [],
    "max_timeout_sec": 3600,
    "audit": True,
}  # type: Dict[str, Any]

CATEGORIES = ("shell", "terminal", "files", "system", "services", "packages", "network", "docker", "ervisio", "meta")


def policy_file() -> str:
    own = os.path.join(paths.data_dir(), "policy.json")
    if os.path.exists(own) or os.geteuid() == 0:
        return own
    return paths.system_policy_path()


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    data = read_json(policy_file(), {})
    if isinstance(data, dict):
        for k, v in data.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                cfg[k] = v
    return cfg


def save(update: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load()
    for k, v in update.items():
        if k not in DEFAULTS:
            raise ValueError("unknown policy key: %s" % k)
        if type(v) is not type(DEFAULTS[k]):
            raise ValueError("policy key %s must be %s" % (k, type(DEFAULTS[k]).__name__))
        cfg[k] = v
    if cfg["mode"] not in ("full", "readonly"):
        raise ValueError("mode must be full or readonly")
    write_json(os.path.join(paths.data_dir(), "policy.json"), cfg, 0o644)
    return cfg


class Denied(Exception):
    """Raised when policy, scope or the guard refuses a call. The message is shown to the model."""


def check_tool(policy: Dict[str, Any], scope: str, tool: Any, args: Dict[str, Any]) -> None:
    if tool.name in policy["disabled_tools"] or tool.category in policy["disabled_categories"]:
        raise Denied("%s is disabled by the administrator of this server." % tool.name)
    if not policy["allow_root"] and (args.get("sudo") or args.get("admin")):
        raise Denied("Root access is disabled by the administrator of this server (policy allow_root=false).")
    readonly = policy["mode"] == "readonly" or scope == "readonly"
    if readonly and not tool.is_read_only(args):
        who = "This server is in read-only mode." if policy["mode"] == "readonly" else "This token is read-only."
        raise Denied("%s %s only reads. Use a full-access token for changes." % (who, tool.name))
    if policy["deny_paths"]:
        for key in ("path", "src", "dest", "dir", "a", "b", "archive", "cwd", "file", "save_to", "identity"):
            value = args.get(key)
            if isinstance(value, str) and path_denied(policy, value):
                raise Denied("%s is protected by policy (deny_paths)." % value)
        for value in args.get("sources") or []:
            if isinstance(value, str) and path_denied(policy, value):
                raise Denied("%s is protected by policy (deny_paths)." % value)
    if tool.category == "ervisio" and args.get("method"):
        for pattern in policy["ervisio_deny_methods"]:
            if re.search(pattern, str(args["method"])):
                raise Denied("The Ervisio method %s is denied by policy." % args["method"])


def path_denied(policy: Dict[str, Any], path: str) -> bool:
    real = os.path.realpath(os.path.expanduser(path))
    for deny in policy["deny_paths"]:
        deny = os.path.realpath(os.path.expanduser(deny))
        if real == deny or real.startswith(deny.rstrip("/") + "/"):
            return True
    return False


# --- the guard -------------------------------------------------------------------------------------------------

_SYSTEM_DIRS = r"(?:/|/\*|~/?|\$HOME/?|/(?:etc|usr|var|boot|bin|sbin|lib|lib64|opt|root|home|srv|dev|proc|sys)/?\*?)"
_GUARD = [
    (re.compile(r"\brm\s+(?:-[a-zA-Z-]*\s+)*(?:--no-preserve-root\s+)?" + _SYSTEM_DIRS + r"(?:\s|$|;|&|\|)"),
     "deletes a system folder or the home folder"),
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(?:-[a-zA-Z]+\s+)*(?:--no-preserve-root)"),
     "rm with --no-preserve-root"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b"), "formats a filesystem"),
    (re.compile(r"\bwipefs\b|\bsgdisk\s+(?:-Z|--zap)|\bparted\b.*\brm\b|\bfdisk\b.*\bd\b"), "changes the partition table"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/(?:sd|nvme|vd|xvd|hd|mmcblk|loop)"), "writes straight to a disk"),
    (re.compile(r">\s*/dev/(?:sd|nvme|vd|xvd|hd|mmcblk)"), "writes straight to a disk"),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "is a fork bomb"),
    (re.compile(r"\bchmod\s+(?:-[a-zA-Z]*R[a-zA-Z]*\s+)\S*\s+/(?:\s|$)"), "changes permissions on the whole filesystem"),
    (re.compile(r"\bchown\s+(?:-[a-zA-Z]*R[a-zA-Z]*\s+)\S+\s+/(?:\s|$)"), "changes owners on the whole filesystem"),
    (re.compile(r"(?:^|[;&|]\s*|\bsudo\s+)(?:shutdown|poweroff|halt|reboot|init\s+[06]|systemctl\s+(?:poweroff|reboot|halt|kexec))\b"),
     "shuts down or reboots the machine (and this connection)"),
    (re.compile(r"\bufw\s+(?:--force\s+)?(?:enable|default\s+deny\s+incoming|reset)\b"),
     "can lock everybody out of SSH if no allow rule exists"),
    (re.compile(r"\biptables\s+(?:-P|--policy)\s+INPUT\s+(?:DROP|REJECT)\b|\bnft\s+flush\s+ruleset\b"),
     "can lock everybody out"),
    (re.compile(r"\bsystemctl\s+(?:stop|disable|mask|kill)\s+(?:--now\s+)?(?:ssh|sshd)(?:\.service|\.socket)?\b"),
     "stops the SSH server"),
    (re.compile(r"\bsystemctl\s+(?:stop|disable|mask|kill)\s+(?:--now\s+)?ervisio(?:d|\.service)?\b"),
     "stops the Ervisio console"),
    (re.compile(r"\b(?:userdel|deluser)\s+(?:-[a-zA-Z]+\s+)*root\b|\bpasswd\s+-l\s+root\b"), "locks or removes root"),
    (re.compile(r"\bcurl\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|\bwget\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
     "runs a script straight from the network"),
]  # type: List[Tuple[Any, str]]

_PROTECTED = ("/", "/etc", "/usr", "/var", "/boot", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/root", "/home", "/srv", "/dev",
              "/proc", "/sys", "/run", "/mnt", "/media")


def guard_command(policy: Dict[str, Any], command: str, args: Dict[str, Any]) -> None:
    if not policy["guard"] or args.get("confirm_dangerous") is True:
        return
    for pattern, why in _GUARD:
        if pattern.search(command):
            raise Denied(
                "Safety guard: this command %s. Nothing was run. If the user really wants this, repeat the call with "
                "confirm_dangerous=true." % why
            )


def guard_path(policy: Dict[str, Any], action: str, path: str, args: Dict[str, Any]) -> None:
    if not policy["guard"] or args.get("confirm_dangerous") is True:
        return
    real = os.path.realpath(os.path.expanduser(path)).rstrip("/") or "/"
    if action in ("delete", "move", "chmod", "chown") and (real in _PROTECTED or real == os.path.realpath(paths.home_of_user())):
        raise Denied(
            "Safety guard: %s on %s would damage the system. Nothing was done. If the user really wants this, repeat "
            "the call with confirm_dangerous=true." % (action, real)
        )


def describe(policy: Dict[str, Any]) -> str:
    bits = ["mode=%s" % policy["mode"]]
    if policy["guard"]:
        bits.append("guard on")
    if not policy["allow_root"]:
        bits.append("root disabled")
    if policy["disabled_tools"]:
        bits.append("disabled: " + ", ".join(policy["disabled_tools"]))
    if policy["disabled_categories"]:
        bits.append("disabled categories: " + ", ".join(policy["disabled_categories"]))
    return "; ".join(bits)


def ensure_valid_category(name: Optional[str]) -> bool:
    return name in CATEGORIES
