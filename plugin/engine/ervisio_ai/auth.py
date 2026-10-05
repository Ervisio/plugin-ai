"""Bearer tokens for the HTTP transport.

Only the SHA-256 of a token is stored (``tokens.json``, mode 0600): the token itself is shown once, when it is
created. Every token has a label (``laptop``, ``work-pc``), a scope (``full`` or ``readonly``) and an optional expiry.
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from . import paths
from .util import now_iso, read_json, write_json

PREFIX = "eai_"
_lock = threading.Lock()
_last_saved = {}  # type: Dict[str, float]


def tokens_file() -> str:
    return os.path.join(paths.data_dir(), "tokens.json")


def _load() -> List[Dict[str, Any]]:
    data = read_json(tokens_file(), {"tokens": []})
    items = data.get("tokens", []) if isinstance(data, dict) else []
    return [t for t in items if isinstance(t, dict) and t.get("hash")]


def _save(items: List[Dict[str, Any]]) -> None:
    write_json(tokens_file(), {"tokens": items}, 0o600)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(label: str, scope: str = "full", expires_days: Optional[int] = None) -> Dict[str, Any]:
    if scope not in ("full", "readonly"):
        raise ValueError("scope must be full or readonly")
    label = (label or "").strip()
    if not label or len(label) > 64:
        raise ValueError("give the token a label of 1 to 64 characters (the device it is for)")
    token = PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    record = {
        "id": secrets.token_hex(4),
        "label": label,
        "scope": scope,
        "hash": _hash(token),
        "created": now_iso(),
        "expires": None,
        "last_used": None,
        "last_ip": None,
    }  # type: Dict[str, Any]
    if expires_days:
        record["expires"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + int(expires_days) * 86400))
    with _lock:
        items = _load()
        items.append(record)
        _save(items)
    public = dict(record)
    del public["hash"]
    public["token"] = token
    return public


def list_tokens() -> List[Dict[str, Any]]:
    out = []
    for t in _load():
        t = dict(t)
        t.pop("hash", None)
        out.append(t)
    return out


def revoke(token_id: str) -> bool:
    with _lock:
        items = _load()
        kept = [t for t in items if t.get("id") != token_id and t.get("label") != token_id]
        if len(kept) == len(items):
            return False
        _save(kept)
    return True


def verify(token: str, ip: str = "") -> Optional[Dict[str, Any]]:
    """The token's record (id, label, scope) or None. Constant-time on the hash."""
    if not token or not token.startswith(PREFIX) or len(token) > 200:
        return None
    digest = _hash(token)
    match = None
    for t in _load():
        if hmac.compare_digest(str(t.get("hash", "")), digest):
            match = t
    if match is None:
        return None
    exp = match.get("expires")
    if exp and str(exp) < now_iso():
        return None
    _touch(match["id"], ip)
    return {"id": match["id"], "label": match.get("label", ""), "scope": match.get("scope", "full")}


def _touch(token_id: str, ip: str) -> None:
    """Remember when and from where a token was last used, at most once a minute per token."""
    now = time.time()
    if now - _last_saved.get(token_id, 0) < 60:
        return
    _last_saved[token_id] = now
    with _lock:
        items = _load()
        for t in items:
            if t.get("id") == token_id:
                t["last_used"] = now_iso()
                t["last_ip"] = ip
        try:
            _save(items)
        except OSError:
            pass


class Throttle:
    """Slows down clients that keep sending wrong tokens: 10 failures in 5 minutes block that address for 5 minutes."""

    def __init__(self, limit: int = 10, window: int = 300) -> None:
        self.limit = limit
        self.window = window
        self._fails = {}  # type: Dict[str, List[float]]
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> int:
        now = time.time()
        with self._lock:
            hits = [t for t in self._fails.get(ip, []) if now - t < self.window]
            self._fails[ip] = hits
            if len(hits) >= self.limit:
                return int(self.window - (now - hits[0])) + 1
        return 0

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails.setdefault(ip, []).append(time.time())
            if len(self._fails) > 5000:
                self._fails.clear()

    def ok(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
