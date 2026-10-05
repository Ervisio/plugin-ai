"""Append-only log of everything the AI does: one JSON object per line in ``audit.jsonl``.

Arguments are redacted (passwords, tokens, bearer headers, URL credentials) and cut before they are written. The log
is rotated at 20 MiB and five old files are kept.
"""
import json
import os
import threading
from typing import Any, Dict, List, Optional

from . import paths
from .util import clip, now_iso, redact, redact_text

MAX_BYTES = 20 * 1024 * 1024
KEEP = 5
_lock = threading.Lock()


def log_path() -> str:
    return os.path.join(paths.data_dir(), "audit.jsonl")


def _rotate(path: str) -> None:
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return
    for i in range(KEEP - 1, 0, -1):
        src = "%s.%d" % (path, i)
        if os.path.exists(src):
            os.replace(src, "%s.%d" % (path, i + 1))
    os.replace(path, path + ".1")


def record(entry: Dict[str, Any]) -> None:
    entry = dict(entry)
    entry.setdefault("time", now_iso())
    if "args" in entry:
        entry["args"] = redact(entry["args"])
    for key in ("error", "trace"):  # an error message can echo what a command printed, secrets included
        if isinstance(entry.get(key), str):
            entry[key] = clip(redact_text(entry[key]), 600)
    path = log_path()
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    with _lock:
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            _rotate(path)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # a full disk must never stop the work that is being logged


def read(limit: int = 100, tool: Optional[str] = None, client: Optional[str] = None, text: Optional[str] = None,
         only_errors: bool = False, before: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest first. ``before`` is an ISO time: only older entries are returned."""
    out = []  # type: List[Dict[str, Any]]
    path = log_path()
    files = [path] + ["%s.%d" % (path, i) for i in range(1, KEEP + 1)]
    for file in files:
        if len(out) >= limit:
            break
        try:
            with open(file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if before and str(e.get("time", "")) >= before:
                continue
            if tool and e.get("tool") != tool:
                continue
            if client and client.lower() not in str(e.get("client", "")).lower():
                continue
            if only_errors and e.get("ok", True):
                continue
            if text and text.lower() not in raw.lower():
                continue
            out.append(e)
            if len(out) >= limit:
                break
    return out
