"""The Model Context Protocol, independent of the transport.

A transport (stdio or HTTP) gives every incoming JSON-RPC message to ``Server.handle`` together with the session it
belongs to, and sends back what ``handle`` returns. Progress notifications go out through the ``notify`` callback.
"""
import itertools
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import NAME, __version__, content, policy as policy_mod, registry
from .registry import Ctx

LATEST = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603

Notify = Callable[[Dict[str, Any]], None]


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class Session:
    def __init__(self, session_id: str, transport: str, scope: str = "full", label: str = "", ip: str = "") -> None:
        self.id = session_id
        self.transport = transport
        self.scope = scope
        self.label = label  # token label, or "ssh:user@host" for stdio
        self.ip = ip
        self.client_info = {}  # type: Dict[str, Any]
        self.protocol = LATEST
        self.created = time.time()
        self.last_seen = self.created

    @property
    def client_name(self) -> str:
        name = str(self.client_info.get("name") or "unknown")
        return "%s (%s)" % (name, self.label) if self.label else name


class Server:
    def __init__(self) -> None:
        self._inflight = {}  # type: Dict[Any, threading.Event]
        self._lock = threading.Lock()

    # -- entry point -----------------------------------------------------------------------------------------

    def handle(self, msg: Any, session: Session, notify: Optional[Notify] = None) -> Optional[Dict[str, Any]]:
        """Handle one JSON-RPC message. Returns the response for a request, None for a notification."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message")
        method = msg.get("method")
        mid = msg.get("id")
        if method is None:
            return None  # a response to something we never asked
        if mid is None:
            self._notification(method, msg.get("params") or {}, session)
            return None
        try:
            result = self._request(method, msg.get("params") or {}, mid, session, notify)
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except RpcError as e:
            return _error(mid, e.code, e.message, e.data)
        except Exception as e:
            return _error(mid, INTERNAL_ERROR, "%s: %s" % (type(e).__name__, e))

    def cancel(self, session: Session, request_id: Any) -> None:
        with self._lock:
            ev = self._inflight.get((session.id, request_id))
        if ev:
            ev.set()

    def cancel_all(self, session: Session) -> None:
        with self._lock:
            events = [ev for (sid, _), ev in self._inflight.items() if sid == session.id]
        for ev in events:
            ev.set()

    # -- notifications ---------------------------------------------------------------------------------------

    def _notification(self, method: str, params: Dict[str, Any], session: Session) -> None:
        if method == "notifications/cancelled":
            rid = params.get("requestId")
            if rid is not None:
                self.cancel(session, rid)

    # -- requests --------------------------------------------------------------------------------------------

    def _request(self, method: str, params: Dict[str, Any], mid: Any, session: Session, notify: Optional[Notify]) -> Any:
        if method == "initialize":
            return self._initialize(params, session)
        if method == "ping":
            return {}
        if method == "tools/list":
            pol = policy_mod.load()
            return {"tools": [t.to_mcp() for t in registry.visible_tools(pol, session.scope)]}
        if method == "tools/call":
            return self._call(params, mid, session, notify)
        if method == "resources/list":
            return {"resources": content.resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str):
                raise RpcError(INVALID_PARAMS, "uri is required")
            body = content.read_resource(uri)
            if body is None:
                raise RpcError(-32002, "Resource not found: %s" % uri)
            return {"contents": [body]}
        if method == "prompts/list":
            return {"prompts": content.prompts()}
        if method == "prompts/get":
            name = params.get("name")
            prompt = content.get_prompt(str(name), params.get("arguments") or {})
            if prompt is None:
                raise RpcError(INVALID_PARAMS, "Unknown prompt: %s" % name)
            return prompt
        if method == "logging/setLevel":
            return {}
        if method == "completion/complete":
            return {"completion": {"values": [], "hasMore": False}}
        raise RpcError(METHOD_NOT_FOUND, "Method not found: %s" % method)

    def _initialize(self, params: Dict[str, Any], session: Session) -> Dict[str, Any]:
        asked = params.get("protocolVersion")
        session.protocol = asked if asked in SUPPORTED else LATEST
        info = params.get("clientInfo")
        if isinstance(info, dict):
            session.client_info = {k: str(v)[:100] for k, v in info.items() if k in ("name", "version", "title")}
        return {
            "protocolVersion": session.protocol,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": NAME, "title": "Ervisio AI", "version": __version__},
            "instructions": content.instructions(),
        }

    def _call(self, params: Dict[str, Any], mid: Any, session: Session, notify: Optional[Notify]) -> Dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise RpcError(INVALID_PARAMS, "name is required")
        meta = params.get("_meta") or {}
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        counter = itertools.count(1)
        cancel = threading.Event()

        def progress(message: str, done: Optional[float], total: Optional[float]) -> None:
            if notify is None or token is None:
                return
            p = {"progressToken": token, "progress": float(next(counter)) if done is None else float(done), "message": message}
            if total is not None:
                p["total"] = float(total)
            notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": p})

        ctx = Ctx(client=session.client_name, scope=session.scope, transport=session.transport, ip=session.ip,
                  progress=progress, cancel=cancel)
        key = (session.id, mid)
        with self._lock:
            self._inflight[key] = cancel
        try:
            return registry.call_tool(name, params.get("arguments"), ctx)
        finally:
            with self._lock:
                self._inflight.pop(key, None)


def _error(mid: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err = {"code": code, "message": message}  # type: Dict[str, Any]
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}
