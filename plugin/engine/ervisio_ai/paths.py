"""Where the engine keeps its files.

As root the state lives in ``/var/lib/ervisio-ai`` (the headless server, root calls from Ervisio). As any other
user it lives in ``~/.local/share/ervisio-ai``. ``ERVISIO_AI_HOME`` overrides both (tests, containers).
"""
import os
import pwd

SYSTEM_DIR = "/var/lib/ervisio-ai"
INSTALL_DIR = "/usr/local/lib/ervisio-ai"
WRAPPER = "/usr/local/bin/ervisio-ai"
UNIT_NAME = "ervisio-ai.service"
UNIT_PATH = "/etc/systemd/system/" + UNIT_NAME

SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def home_of_user() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError:
        return os.environ.get("HOME", "/")


def data_dir() -> str:
    override = os.environ.get("ERVISIO_AI_HOME")
    if override:
        return override
    if os.geteuid() == 0:
        return SYSTEM_DIR
    return os.path.join(home_of_user(), ".local", "share", "ervisio-ai")


def server_dir() -> str:
    """Where the headless server keeps its settings, pid and log. It runs as root, so this is the system folder even
    when a normal user asks about it (the status page does)."""
    return os.environ.get("ERVISIO_AI_HOME") or SYSTEM_DIR


def ensure_dir(path: str, mode: int = 0o700) -> str:
    os.makedirs(path, mode=mode, exist_ok=True)
    return path


def sub(*parts: str, create: bool = False, mode: int = 0o700) -> str:
    path = os.path.join(data_dir(), *parts)
    if create:
        ensure_dir(path, mode)
    return path


def system_policy_path() -> str:
    """The machine-wide policy; users other than root read it so the administrator's limits apply to them too."""
    return os.path.join(SYSTEM_DIR, "policy.json")


def user_spool_dir() -> str:
    """Request files written by the Ervisio page for a non-root call (``~/.config/ervisio/plugins/ai/spool``)."""
    return os.path.join(home_of_user(), ".config", "ervisio", "plugins", "ai", "spool")


def root_spool_dir() -> str:
    return os.path.join(SYSTEM_DIR, "spool")


def spool_dir() -> str:
    return root_spool_dir() if os.geteuid() == 0 else user_spool_dir()
