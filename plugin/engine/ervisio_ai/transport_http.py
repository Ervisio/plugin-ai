"""MCP over HTTP (the "Streamable HTTP" transport): ``POST /mcp`` with a bearer token.

* every request needs ``Authorization: Bearer eai_...`` (or ``X-Ervisio-Token``); wrong tokens are throttled;
* browsers cannot talk to it: a request with an ``Origin`` header is refused unless it is in ``allowed_origins``, and
  when the server listens on loopback the ``Host`` header must be a loopback name (DNS-rebinding protection);
* a quick tool call is answered with plain JSON; a long one (or one with a progress token) is answered as a
  Server-Sent Events stream with progress messages and keep-alive comments, so proxies do not cut it.
"""
import json
import queue
import select
import socket
import ssl
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from . import NAME, __version__, auth, bridge
from .mcp import INVALID_REQUEST, PARSE_ERROR, Server, Session, _error

MAX_BODY = 16 * 1024 * 1024
SESSION_TTL = 24 * 3600
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class SessionStore:
    def __init__(self) -> None:
        self._items = {}  # type: Dict[str, Session]
        self._lock = threading.Lock()

    def create(self, scope: str, label: str, ip: str) -> Session:
        s = Session(uuid.uuid4().hex, "http", scope, label, ip)
        with self._lock:
            now = time.time()
            for k in [k for k, v in self._items.items() if now - v.last_seen > SESSION_TTL]:
                del self._items[k]
            if len(self._items) > 2000:
                self._items.clear()
            self._items[s.id] = s
        return s

    def get(self, sid: str) -> Optional[Session]:
        with self._lock:
            s = self._items.get(sid)
            if s:
                s.last_seen = time.time()
            return s

    def drop(self, sid: str) -> None:
        with self._lock:
            self._items.pop(sid, None)


class McpHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], cfg: Dict[str, Any], tls_ctx: Optional[ssl.SSLContext]) -> None:
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        self.cfg = cfg
        self.tls_ctx = tls_ctx
        self.mcp = Server()
        self.sessions = SessionStore()
        self.throttle = auth.Throttle()
        self.loopback = addr[0] in ("127.0.0.1", "::1", "localhost")
        super().__init__(addr, Handler)

    def get_request(self) -> Tuple[Any, Any]:
        sock, addr = super().get_request()
        if self.tls_ctx is not None:
            sock = self.tls_ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr


class Handler(BaseHTTPRequestHandler):
    server_version = "ervisio-ai/" + __version__
    protocol_version = "HTTP/1.1"
    timeout = 60

    def setup(self) -> None:
        super().setup()
        if isinstance(self.request, ssl.SSLSocket):
            self.request.settimeout(10)
            self.request.do_handshake()
            self.request.settimeout(self.timeout)

    def log_message(self, fmt: str, *args: Any) -> None:  # the audit log is the record; keep stderr for problems
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (ssl.SSLError, ConnectionError, socket.timeout, OSError):
            pass

    # -- helpers ---------------------------------------------------------------------------------------------

    def _reply(self, status: int, body: bytes = b"", ctype: str = "application/json", headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: Any, headers: Optional[Dict[str, str]] = None) -> None:
        self._reply(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), headers=headers)

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _check_origin_host(self) -> bool:
        cfg = self.server.cfg
        origin = self.headers.get("Origin")
        if origin and origin not in cfg["allowed_origins"]:
            self._json(403, {"error": "Origin not allowed"})
            return False
        if self.server.loopback:
            host = _host_name(self.headers.get("Host") or "")
            if host not in LOOPBACK_HOSTS and host not in cfg["allowed_hosts"]:
                self._json(403, {"error": "Host not allowed"})
                return False
        return True

    def _authenticate(self) -> Optional[Dict[str, Any]]:
        ip = self._ip()
        wait = self.server.throttle.blocked(ip)
        if wait:
            self._json(429, {"error": "Too many wrong tokens. Try again later."}, {"Retry-After": str(wait)})
            return None
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else self.headers.get("X-Ervisio-Token", "").strip()
        rec = auth.verify(token, ip)
        if rec is None:
            self.server.throttle.fail(ip)
            self._json(401, {"error": "A valid bearer token is required"}, {"WWW-Authenticate": 'Bearer realm="ervisio-ai"'})
            return None
        self.server.throttle.ok(ip)
        return rec

    # -- verbs -----------------------------------------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/healthz":
            self._json(200, {"ok": True, "name": NAME, "version": __version__})
            return
        if not self._check_origin_host():
            return
        if self._authenticate() is None:
            return
        # No server-initiated stream is offered: the spec allows answering 405.
        self._reply(405, b'{"error":"This server answers POST /mcp only"}', headers={"Allow": "POST, DELETE"})

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_DELETE(self) -> None:
        if not self._check_origin_host() or self._authenticate() is None:
            return
        sid = self.headers.get("Mcp-Session-Id")
        if sid:
            s = self.server.sessions.get(sid)
            if s:
                self.server.mcp.cancel_all(s)
            self.server.sessions.drop(sid)
        self._reply(204)

    def do_OPTIONS(self) -> None:
        self._reply(403, b'{"error":"Cross-origin requests are not allowed"}')

    def do_POST(self) -> None:
        if self.path.split("?")[0] not in ("/mcp", "/"):
            self._json(404, {"error": "Not found. The MCP endpoint is /mcp"})
            return
        if not self._check_origin_host():
            return
        rec = self._authenticate()
        if rec is None:
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0:
            self._json(411, {"error": "Content-Length is required"})
            return
        if length > MAX_BODY:
            self._json(413, {"error": "Request too large"})
            return
        raw = self.rfile.read(length)
        try:
            msg = json.loads(raw)
        except ValueError:
            self._json(400, _error(None, PARSE_ERROR, "Parse error"))
            return

        items = msg if isinstance(msg, list) else [msg]
        if not items or not all(isinstance(m, dict) for m in items):
            self._json(400, _error(None, INVALID_REQUEST, "Invalid request"))
            return
        is_init = any(m.get("method") == "initialize" for m in items)
        sid = self.headers.get("Mcp-Session-Id")
        session = None  # type: Optional[Session]
        if sid:
            session = self.server.sessions.get(sid)
            if session is None:
                self._json(404, _error(None, INVALID_REQUEST, "Unknown session: start a new one with initialize"))
                return
        elif is_init:
            session = self.server.sessions.create(rec["scope"], rec["label"], self._ip())
        else:
            session = self.server.sessions.create(rec["scope"], rec["label"], self._ip())
        # the token decides the scope, whatever the session says
        session.scope, session.label = rec["scope"], rec["label"]
        extra = {"Mcp-Session-Id": session.id} if is_init else {}

        requests = [m for m in items if m.get("method") is not None and m.get("id") is not None]
        for m in items:
            if m not in requests:
                self.server.mcp.handle(m, session)
        if not requests:
            self._reply(202, b"", headers=extra)
            return
        if isinstance(msg, list):  # a batch: answer in order, as one JSON array
            out = [self.server.mcp.handle(m, session) for m in requests]
            self._json(200, [o for o in out if o is not None], extra)
            return
        self._answer(requests[0], session, extra)

    def _client_gone(self) -> bool:
        """True when the client closed its end of the connection (a plain socket; TLS finds out when it writes)."""
        sock = self.connection
        if isinstance(sock, ssl.SSLSocket):
            return False
        try:
            readable, _, _ = select.select([sock], [], [], 0)
            return bool(readable) and sock.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    # -- one request, answered as JSON or as an event stream ---------------------------------------------------

    def _answer(self, req: Dict[str, Any], session: Session, extra: Dict[str, str]) -> None:
        events = queue.Queue()  # type: "queue.Queue[Any]"
        done = object()
        params = req.get("params") or {}
        meta = params.get("_meta") if isinstance(params, dict) else None
        wants_stream = isinstance(meta, dict) and meta.get("progressToken") is not None
        box = {}  # type: Dict[str, Any]

        def run() -> None:
            try:
                box["resp"] = self.server.mcp.handle(req, session, lambda n: events.put(n))
            finally:
                events.put(done)

        threading.Thread(target=run, daemon=True).start()
        first = None  # type: Any
        if not wants_stream:
            deadline = time.time() + 5
            while first is None and time.time() < deadline:
                try:
                    first = events.get(timeout=0.25)
                except queue.Empty:
                    if self._client_gone():
                        self.server.mcp.cancel(session, req.get("id"))
                        return
            if first is done:
                self._json(200, box["resp"], extra)
                return
        pending = [first] if first is not None else []
        # Stream: headers now, then events until the answer is ready.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.close_connection = True

        def emit(obj: Any) -> bool:
            try:
                self.wfile.write(b"event: message\ndata: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                return True
            except (OSError, ValueError):
                return False

        for item in pending:
            if item is done:
                emit(box["resp"])
                return
            emit(item)
        try:  # tell the client (and any proxy in between) the stream is alive right away
            self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
        except (OSError, ValueError):
            self.server.mcp.cancel(session, req.get("id"))
            return
        last_ping = time.time()
        while True:
            try:
                item = events.get(timeout=0.25)
            except queue.Empty:
                if self._client_gone():
                    self.server.mcp.cancel(session, req.get("id"))
                    return
                if time.time() - last_ping > 5:
                    last_ping = time.time()
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (OSError, ValueError):
                        self.server.mcp.cancel(session, req.get("id"))
                        return
                continue
            if item is done:
                emit(box["resp"])
                return
            if not emit(item):
                self.server.mcp.cancel(session, req.get("id"))
                return


def _host_name(header: str) -> str:
    """The host part of a Host header, lower case, without the port ([::1] keeps its brackets)."""
    h = header.strip().lower()
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h.rsplit(":", 1)[0] if ":" in h else h


def build_tls(cfg: Dict[str, Any]) -> Optional[ssl.SSLContext]:
    if cfg["tls"] == "none":
        return None
    cert, key = cfg["cert"], cfg["key"]
    if cfg["tls"] == "self-signed":
        from . import config
        made = config.self_signed(cfg.get("allowed_hosts", []))
        cert, key = made["cert"], made["key"]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def serve_http(cfg: Dict[str, Any]) -> int:
    tls = build_tls(cfg)
    httpd = McpHTTPServer((cfg["bind"], int(cfg["port"])), cfg, tls)
    scheme = "https" if tls else "http"
    print("ervisio-ai %s listening on %s://%s:%s/mcp" % (__version__, scheme, cfg["bind"], cfg["port"]), file=sys.stderr, flush=True)
    stop = threading.Event()

    def shutdown(*_: Any) -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=httpd.shutdown, daemon=True).start()

    import signal
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        bridge.shutdown()
    return 0
