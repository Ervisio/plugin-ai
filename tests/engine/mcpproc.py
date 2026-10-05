"""Run the engine as a separate process and speak MCP to it, the way a client does."""
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time

from helpers import ENGINE


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stdio:
    def __init__(self, home: str, extra_env=None) -> None:
        env = dict(os.environ, ERVISIO_AI_HOME=home, **(extra_env or {}))
        self.p = subprocess.Popen([sys.executable, "-B", ENGINE, "stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self.q = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self) -> None:
        for line in self.p.stdout:
            self.q.put(json.loads(line))

    def send(self, obj) -> None:
        self.p.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None, wait=True):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        return self.next_id if not wait else self.recv(self.next_id)

    def recv(self, mid=None, timeout=20):
        deadline = time.time() + timeout
        stash = []
        while time.time() < deadline:
            try:
                m = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if mid is None or m.get("id") == mid:
                for s in stash:
                    self.q.put(s)
                return m
            stash.append(m)
        raise TimeoutError("no answer for %r" % mid)

    def initialize(self, version="2025-06-18"):
        r = self.request("initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "raw-test", "version": "0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def close(self) -> int:
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            return self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            return -9
        finally:
            self.p.stdout.close()
            self.p.stderr.close()
