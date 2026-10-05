"""A client for ``ervisio-bridge``: every Ervisio module (services, files, logs, software, users, terminal, plugins,
config, updates...) through the same newline-delimited JSON protocol the console itself uses.

As root the bridge is started with ``--admin``, so every method works. As another user only the methods of that user
work. The methods the console answers itself (jobs, notifications, environments) are not in the bridge.
"""
import base64
import glob
import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import paths
from .proc import which

CANDIDATES = [
    "/usr/lib/ervisio/current/bin/ervisio-bridge",
    "/usr/lib/ervisio/ervisio-bridge",
    "/usr/local/lib/ervisio/ervisio-bridge",
]


class BridgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def find_bridge() -> Optional[str]:
    override = os.environ.get("ERVISIO_BRIDGE")
    if override and os.access(override, os.X_OK):
        return override
    for c in CANDIDATES:
        if os.access(c, os.X_OK):
            return c
    versions = sorted(glob.glob("/usr/lib/ervisio/versions/*/bin/ervisio-bridge"), key=os.path.getmtime, reverse=True)
    for c in versions:
        if os.access(c, os.X_OK):
            return c
    return which("ervisio-bridge")


class Bridge:
    def __init__(self, path: str, admin: bool) -> None:
        self.path = path
        self.admin = admin
        self.hello = {}  # type: Dict[str, Any]
        self._next = 0
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._queues = {}  # type: Dict[int, "queue.Queue[Dict[str, Any]]"]
        self._dead = False
        self._stderr = []  # type: List[str]
        env = {"PATH": paths.SAFE_PATH, "HOME": paths.home_of_user(), "LANG": "C.UTF-8"}
        if admin:
            env["HOME"] = "/root"
        argv = [path] + (["--admin"] if admin else [])
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd="/",
                                     start_new_session=True)
        hello_q = queue.Queue()  # type: "queue.Queue[Dict[str, Any]]"
        self._queues[0] = hello_q
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        try:
            msg = hello_q.get(timeout=10)
        except queue.Empty:
            self.close()
            raise BridgeError("unavailable", "The Ervisio bridge did not say hello within 10 seconds. %s" % " ".join(self._stderr[-3:]))
        if "hello" not in msg:
            self.close()
            raise BridgeError("unavailable", "The Ervisio bridge exited: %s" % " ".join(self._stderr[-3:]))
        self.hello = msg["hello"]

    # -- plumbing --------------------------------------------------------------------------------------------

    def _read(self) -> None:
        try:
            for raw in self.proc.stdout:  # type: ignore[union-attr]
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if "hello" in m:
                    self._queues[0].put(m)
                    continue
                q = self._queues.get(m.get("id", -1))
                if q is not None:
                    q.put(m)
        except (ValueError, OSError):
            pass  # closed under us
        finally:
            self._dead = True
            self.proc.poll()
            for q in list(self._queues.values()):
                q.put({"error": {"code": "unavailable", "message": "The Ervisio bridge process exited."}, "event": "end", "_dead": True})

    def _read_err(self) -> None:
        try:
            for raw in self.proc.stderr:  # type: ignore[union-attr]
                self._stderr.append(raw.decode("utf-8", "replace").strip())
                del self._stderr[:-20]
        except (ValueError, OSError):
            pass

    def _send(self, msg: Dict[str, Any]) -> None:
        data = (json.dumps(msg) + "\n").encode()
        with self._wlock:
            try:
                self.proc.stdin.write(data)  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError):
                raise BridgeError("unavailable", "The Ervisio bridge process is gone.")

    def _new_id(self) -> Tuple[int, "queue.Queue[Dict[str, Any]]"]:
        with self._lock:
            self._next += 1
            q = queue.Queue()  # type: "queue.Queue[Dict[str, Any]]"
            self._queues[self._next] = q
            return self._next, q

    @property
    def alive(self) -> bool:
        return not self._dead and self.proc.poll() is None

    def close(self) -> None:
        self._dead = True
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        for pipe in (self.proc.stdout, self.proc.stderr):
            try:
                pipe.close()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass

    # -- calls -----------------------------------------------------------------------------------------------

    def level(self, method: str) -> Optional[str]:
        return self.hello.get("methods", {}).get(method)

    def is_stream(self, method: str) -> bool:
        return method in self.hello.get("streams", [])

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60,
             cancel: Optional[threading.Event] = None) -> Any:
        if method not in self.hello.get("methods", {}):
            raise BridgeError("not_found", "The bridge has no method %r. Use ervisio_methods to list them." % method)
        mid, q = self._new_id()
        try:
            self._send({"id": mid, "method": method, "params": params or {}})
            deadline = time.time() + timeout
            while True:
                if cancel is not None and cancel.is_set():
                    self._send({"id": mid, "cancel": True})
                    raise BridgeError("cancelled", "cancelled")
                try:
                    m = q.get(timeout=0.2)
                    break
                except queue.Empty:
                    if time.time() > deadline:
                        self._send({"id": mid, "cancel": True})
                        raise BridgeError("unavailable", "%s did not answer within %ds" % (method, timeout))
            if m.get("error"):
                e = m["error"]
                raise BridgeError(e.get("code", "internal"), e.get("message", "error"))
            return m.get("result")
        finally:
            self._queues.pop(mid, None)

    def stream(self, method: str, params: Optional[Dict[str, Any]] = None, seconds: float = 5, max_events: int = 50,
               cancel: Optional[threading.Event] = None) -> Tuple[List[Any], bool]:
        """Collect events of a stream method for a few seconds. Returns (events, ended_by_itself)."""
        if method not in self.hello.get("streams", []):
            raise BridgeError("invalid", "%s is not a stream method." % method)
        mid, q = self._new_id()
        events = []  # type: List[Any]
        ended = False
        try:
            self._send({"id": mid, "method": method, "params": params or {}, "stream": True})
            deadline = time.time() + seconds
            while time.time() < deadline and len(events) < max_events:
                if cancel is not None and cancel.is_set():
                    break
                try:
                    m = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if m.get("error"):
                    e = m["error"]
                    raise BridgeError(e.get("code", "internal"), e.get("message", "error"))
                if m.get("event") == "end":
                    ended = True
                    break
                if m.get("event") == "data":
                    data = m.get("data")
                    if m.get("b64") and isinstance(data, str):
                        data = base64.b64decode(data).decode("utf-8", "replace")
                    events.append(data)
                    self._send({"id": mid, "ack": 1})
            if not ended:
                self._send({"id": mid, "cancel": True})
        finally:
            self._queues.pop(mid, None)
        return events, ended


_shared = {}  # type: Dict[bool, Bridge]
_shared_lock = threading.Lock()


def get_bridge(admin: Optional[bool] = None) -> Bridge:
    """The bridge shared by every call of this process, started on first use and restarted if it dies.

    ``admin=True`` is the root bridge (methods that need administrator rights); ``admin=False`` is the plain bridge of
    the same user, which is the one that may run a plugin's commands that are not declared ``admin``. The default is
    the root bridge for root and the plain one for everybody else.
    """
    want = (os.geteuid() == 0) if admin is None else admin
    if want and os.geteuid() != 0:
        raise BridgeError("needs_admin", "Administrator rights need the server to run as root.")
    with _shared_lock:
        b = _shared.get(want)
        if b is not None and b.alive:
            return b
        path = find_bridge()
        if not path:
            raise BridgeError("unavailable", "ervisio-bridge was not found on this machine. Is Ervisio installed? "
                                             "(set ERVISIO_BRIDGE to its path if it lives somewhere unusual)")
        _shared[want] = Bridge(path, admin=want)
        return _shared[want]


def shutdown() -> None:
    with _shared_lock:
        for b in _shared.values():
            b.close()
        _shared.clear()
