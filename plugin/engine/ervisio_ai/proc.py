"""Run programs the way an AI needs them run.

* no terminal and no stdin unless asked for, so nothing waits for a prompt that nobody will answer;
* its own process group, killed as a whole on timeout or cancel;
* output kept in a temp file while it runs; a huge output is cut to its start and end and saved in full;
* live output handed to ``on_output`` (at most every 0.4 s), for progress messages.
"""
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import paths
from .util import head_tail

OnOutput = Callable[[str, str], None]


class Cancelled(Exception):
    pass


def base_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    path = env.get("PATH", "")
    for p in paths.SAFE_PATH.split(":"):
        if p not in path.split(":"):
            path += ("" if not path else ":") + p
    env["PATH"] = path
    env.setdefault("LANG", "C.UTF-8")
    env.update(
        {
            "TERM": "dumb",
            "DEBIAN_FRONTEND": "noninteractive",
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "SYSTEMD_PAGER": "",
            "SYSTEMD_COLORS": "0",
            "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def which(name: str) -> Optional[str]:
    return shutil.which(name, path=base_env()["PATH"])


def shell_path() -> str:
    return which("bash") or which("sh") or "/bin/sh"


def _current_user() -> str:
    import pwd
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


def elevate(argv: List[str], sudo: bool = False, user: Optional[str] = None) -> List[str]:
    """Prefix argv so it runs as root (``sudo``) or as another user. A no-op when it already would."""
    me_root = os.geteuid() == 0
    if user and user != _current_user():
        if me_root:
            runuser = which("runuser")
            if runuser:
                return [runuser, "-u", user, "--"] + argv
        return ["sudo", "-n", "-u", user, "--"] + argv
    if sudo and not me_root:
        return ["sudo", "-n", "--"] + argv
    return argv


def sudo_hint(stderr: str) -> str:
    low = stderr.lower()
    if "a password is required" in low or "no tty present" in low or "not in the sudoers" in low:
        return (
            "sudo needs a password or this user is not allowed to use it. Connect as root over SSH, or give this user "
            "passwordless sudo, or run the server as root."
        )
    return ""


def kill_group(proc: "subprocess.Popen[bytes]", grace: float = 2.0) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class _Pump(threading.Thread):
    """Copies one pipe into a spooled file and tells the caller how much arrived."""

    def __init__(self, pipe: Any) -> None:
        super().__init__(daemon=True)
        self.pipe = pipe
        self.file = tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024)
        self.size = 0
        self.pending = []  # type: List[bytes]
        self.lock = threading.Lock()

    def run(self) -> None:
        try:
            while True:
                chunk = os.read(self.pipe.fileno(), 65536)
                if not chunk:
                    break
                self.file.write(chunk)
                with self.lock:
                    self.size += len(chunk)
                    self.pending.append(chunk)
        except (OSError, ValueError):
            pass

    def take_pending(self) -> bytes:
        with self.lock:
            data = b"".join(self.pending)
            self.pending = []
        return data

    def text(self) -> str:
        self.file.seek(0)
        return self.file.read().decode("utf-8", "replace")


def _outputs_dir() -> str:
    d = paths.sub("outputs", create=True)
    try:  # keep only the last day of saved outputs
        cutoff = time.time() - 86400
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
    except OSError:
        pass
    return d


def run(
    argv: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: float = 120,
    stdin: Optional[str] = None,
    cancel: Optional[threading.Event] = None,
    on_output: Optional[OnOutput] = None,
    max_output: int = 100000,
) -> Dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd or None,
            env=base_env(env),
            start_new_session=True,
        )
    except FileNotFoundError:
        return {"exit_code": 127, "stdout": "", "stderr": "%s: command not found" % argv[0], "duration": 0.0, "error": "not_found"}
    except NotADirectoryError:
        return {"exit_code": 126, "stdout": "", "stderr": "cwd is not a directory: %s" % cwd, "duration": 0.0, "error": "cwd"}
    except PermissionError as e:
        return {"exit_code": 126, "stdout": "", "stderr": str(e), "duration": 0.0, "error": "permission"}
    except OSError as e:
        return {"exit_code": 126, "stdout": "", "stderr": str(e), "duration": 0.0, "error": "os"}

    out_pump, err_pump = _Pump(proc.stdout), _Pump(proc.stderr)
    out_pump.start()
    err_pump.start()
    if stdin is not None:
        def feed() -> None:
            try:
                proc.stdin.write(stdin.encode("utf-8"))  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass
        threading.Thread(target=feed, daemon=True).start()

    timed_out = cancelled = False
    deadline = started + timeout
    last_flush = 0.0
    while proc.poll() is None:
        time.sleep(0.05)
        if cancel is not None and cancel.is_set():
            cancelled = True
            kill_group(proc)
            break
        if time.time() > deadline:
            timed_out = True
            kill_group(proc)
            break
        if on_output and time.time() - last_flush > 0.4:
            last_flush = time.time()
            _flush(on_output, out_pump, err_pump)
    proc.wait()
    # A grandchild that kept a pipe open must not hold the call: give the pumps a moment, then move on.
    for pump in (out_pump, err_pump):
        pump.join(1.0)
    if on_output:
        _flush(on_output, out_pump, err_pump)
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()  # type: ignore[union-attr]
        except OSError:
            pass

    result = {
        "exit_code": proc.returncode if proc.returncode >= 0 else 128 - proc.returncode,
        "signal": -proc.returncode if proc.returncode < 0 else None,
        "duration": round(time.time() - started, 2),
        "timed_out": timed_out,
        "cancelled": cancelled,
    }  # type: Dict[str, Any]
    saved = {}
    for name, pump in (("stdout", out_pump), ("stderr", err_pump)):
        text = pump.text()
        if len(text) > max_output:
            path = os.path.join(_outputs_dir(), "%s-%s.log" % (uuid.uuid4().hex[:10], name))
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            saved[name] = path
            text = head_tail(text, max_output)
        result[name] = text
        pump.file.close()
    if saved:
        result["saved_output"] = saved
    return result


def _flush(on_output: OnOutput, out_pump: _Pump, err_pump: _Pump) -> None:
    for name, pump in (("stdout", out_pump), ("stderr", err_pump)):
        data = pump.take_pending()
        if data:
            on_output(name, data.decode("utf-8", "replace"))


def shell_argv(command: str) -> List[str]:
    return [shell_path(), "-c", command]


def quote(args: List[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)
