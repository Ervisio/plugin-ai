"""MCP over stdin/stdout: one JSON-RPC message per line.

An IDE starts this through SSH (``ssh host ervisio-ai stdio``). The SSH login is the authentication, so nothing
listens on the network. The protocol owns file descriptor 1 exclusively: anything a tool prints goes to stderr.
"""
import json
import os
import signal
import sys
import threading
import uuid
from typing import Any, Dict

from . import bridge
from .mcp import PARSE_ERROR, Server, Session, _error


def label_from_env() -> str:
    conn = os.environ.get("SSH_CONNECTION", "").split()
    who = os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.geteuid())
    return "ssh:%s@%s" % (who, conn[0]) if conn else "stdio:%s" % who


def serve_stdio() -> int:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    wlock = threading.Lock()
    server = Server()
    session = Session(uuid.uuid4().hex, "stdio", "full", label_from_env())
    slots = threading.BoundedSemaphore(32)
    threads = []  # type: list

    def send(msg: Any) -> None:
        line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        with wlock:
            try:
                out.write(line + "\n")
                out.flush()
            except (OSError, ValueError):
                pass

    def work(msg: Dict[str, Any]) -> None:
        try:
            resp = server.handle(msg, session, send)
            if resp is not None:
                send(resp)
        finally:
            slots.release()

    def stop(*_: Any) -> None:
        server.cancel_all(session)
        bridge.shutdown()
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            send(_error(None, PARSE_ERROR, "Parse error"))
            continue
        items = msg if isinstance(msg, list) else [msg]
        for m in items:
            if isinstance(m, dict) and m.get("method") == "notifications/cancelled":
                server.handle(m, session, send)
                continue
            slots.acquire()
            t = threading.Thread(target=work, args=(m,), daemon=True)
            t.start()
            threads.append(t)
            threads[:] = [x for x in threads if x.is_alive()]
    # stdin closed: the client is gone. Stop what is still running and leave.
    server.cancel_all(session)
    for t in threads:
        t.join(5)
    bridge.shutdown()
    return 0
