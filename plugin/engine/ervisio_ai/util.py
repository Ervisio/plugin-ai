"""Small helpers shared by the engine."""
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Optional

_file_lock = threading.Lock()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, data: Any, mode: int = 0o600) -> None:
    """Write a JSON file atomically (temp file in the same folder, then rename)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    with _file_lock:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "… (%d more characters)" % (len(text) - limit)


def head_tail(text: str, limit: int) -> str:
    """Keep the start and the end of a long text, which is where commands put what matters."""
    if len(text) <= limit:
        return text
    keep = max(limit // 2, 1)
    cut = len(text) - 2 * keep
    return text[:keep] + "\n… [%d characters omitted] …\n" % cut + text[-keep:]


_SECRET_KEY = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|authorization|cookie)", re.I)
_SECRET_ARG = re.compile(
    r"(?i)(--?(?:pass(?:word|wd)?|secret|token|api[_-]?key|auth)[=\s]+)(\S+)|((?:pass(?:word|wd)?|secret|token|api[_-]?key)=)(\S+)"
)
_URL_CRED = re.compile(r"(\w+://[^/\s:@]+:)([^@\s/]+)(@)")
_BEARER = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")


REDACT_WINDOW = 4000  # only the start of a long text is ever stored, and the patterns below must stay cheap on it


def redact_text(text: str) -> str:
    """Mask secrets in (the first REDACT_WINDOW characters of) a text."""
    if len(text) > REDACT_WINDOW:
        text = text[:REDACT_WINDOW]
    if "://" in text:
        text = _URL_CRED.sub(r"\1***\3", text)
    text = _BEARER.sub(r"\1***", text)

    def sub(m: "re.Match[str]") -> str:
        if m.group(1):
            return m.group(1) + "***"
        return m.group(3) + "***"

    return _SECRET_ARG.sub(sub, text)


def redact(value: Any, depth: int = 0) -> Any:
    """Copy of value that is safe to write to a log: secrets masked, long strings cut."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}  # type: Dict[str, Any]
        for k, v in value.items():
            out[k] = "***" if _SECRET_KEY.search(str(k)) and isinstance(v, (str, int)) else redact(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, str):
        short = redact_text(value)
        return short[:2000] + ("… (%d more characters)" % (len(value) - 2000) if len(value) > 2000 else "")
    return value


def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%d B" % n


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append("%dd" % d)
    if h:
        parts.append("%dh" % h)
    if m and not d:
        parts.append("%dm" % m)
    if not parts:
        parts.append("%ds" % s)
    return " ".join(parts)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def first_existing(paths: "list[str]") -> Optional[str]:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None
