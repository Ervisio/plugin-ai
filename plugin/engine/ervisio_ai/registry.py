"""Tool definitions, argument checking and the call pipeline (policy, audit, errors)."""
import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Set

from . import audit, policy as policy_mod
from .util import clip

ProgressFn = Callable[[str, Optional[float], Optional[float]], None]


class Ctx:
    """Who is calling and how to talk back to them while a tool runs."""

    def __init__(self, client: str = "unknown", scope: str = "full", transport: str = "stdio", ip: str = "",
                 progress: Optional[ProgressFn] = None, cancel: Optional[threading.Event] = None) -> None:
        self.client = client
        self.scope = scope
        self.transport = transport
        self.ip = ip
        self.cancel = cancel or threading.Event()
        self._progress = progress
        self.policy = policy_mod.load()

    def progress(self, message: str, done: Optional[float] = None, total: Optional[float] = None) -> None:
        if self._progress:
            try:
                self._progress(message, done, total)
            except Exception:  # a broken client connection must not abort the tool
                pass


class Result:
    def __init__(self, text: str, data: Any = None, is_error: bool = False) -> None:
        self.text = text
        self.data = data
        self.is_error = is_error


def ok(text: str, data: Any = None) -> Result:
    return Result(text, data)


def fail(text: str, data: Any = None) -> Result:
    return Result(text, data, True)


class ToolError(Exception):
    """A failure the model can act on (bad path, missing program...). Shown as an error result, not a protocol error."""


class Tool:
    def __init__(self, name: str, category: str, description: str, schema: Dict[str, Any], handler: Callable[..., Any],
                 title: str = "", read_only: bool = False, destructive: bool = False, idempotent: bool = False,
                 open_world: bool = False, read_only_actions: Optional[Set[str]] = None, action_key: str = "action",
                 elevatable: bool = False, read_only_fn: Optional[Callable[[Dict[str, Any]], bool]] = None) -> None:
        self.name = name
        self.category = category
        self.description = description
        self.schema = schema
        self.handler = handler
        self.title = title or name.replace("_", " ").title()
        self.read_only = read_only
        self.destructive = destructive
        self.idempotent = idempotent
        self.open_world = open_world
        self.read_only_actions = read_only_actions
        self.action_key = action_key
        self.elevatable = elevatable
        self.read_only_fn = read_only_fn

    def is_read_only(self, args: Dict[str, Any]) -> bool:
        if self.read_only:
            return True
        if self.read_only_fn is not None:
            return bool(self.read_only_fn(args))
        if self.read_only_actions is not None:
            return args.get(self.action_key) in self.read_only_actions
        return False

    def to_mcp(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent,
                "openWorldHint": self.open_world,
            },
        }


REGISTRY = {}  # type: Dict[str, Tool]


def register(name: str, category: str, description: str, props: Dict[str, Any], required: Optional[List[str]] = None, **kw: Any):
    props = dict(props)
    if kw.get("elevatable") and "sudo" not in props:
        props["sudo"] = S("boolean", "Run this as root through sudo (not needed when the server already runs as root).")
    schema = {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}
    if not schema["required"]:
        del schema["required"]

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY[name] = Tool(name, category, description, schema, fn, **kw)
        return fn

    return deco


def S(kind: str, description: str, **extra: Any) -> Dict[str, Any]:
    d = {"type": kind, "description": description}  # type: Dict[str, Any]
    d.update(extra)
    return d


# --- argument checking -----------------------------------------------------------------------------------------

def _coerce(value: Any, spec: Dict[str, Any], where: str) -> Any:
    kind = spec.get("type")
    if kind == "integer":
        if isinstance(value, bool):
            raise ValueError("%s must be an integer" % where)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        if isinstance(value, float) and value == value and abs(value) != float("inf") and value % 1 == 0:
            value = int(value)
        if not isinstance(value, int):
            raise ValueError("%s must be an integer" % where)
    elif kind == "number":
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise ValueError("%s must be a number" % where)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s must be a number" % where)
    elif kind == "boolean":
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, bool):
            raise ValueError("%s must be true or false" % where)
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError("%s must be a string" % where)
    elif kind == "array":
        if isinstance(value, str) and value.strip().startswith("["):
            import json
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if not isinstance(value, list):
            raise ValueError("%s must be an array" % where)
        item = spec.get("items")
        if item:
            value = [_coerce(v, item, "%s[%d]" % (where, i)) for i, v in enumerate(value)]
    elif kind == "object":
        if isinstance(value, str) and value.strip().startswith("{"):
            import json
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if not isinstance(value, dict):
            raise ValueError("%s must be an object" % where)
        props = spec.get("properties")
        if props:
            value = validate({"type": "object", "properties": props, "required": spec.get("required", []),
                              "additionalProperties": spec.get("additionalProperties", True)}, value, where + ".")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("%s must be one of: %s" % (where, ", ".join(str(v) for v in spec["enum"])))
    if "minimum" in spec and isinstance(value, (int, float)) and value < spec["minimum"]:
        raise ValueError("%s must be at least %s" % (where, spec["minimum"]))
    if "maximum" in spec and isinstance(value, (int, float)) and value > spec["maximum"]:
        raise ValueError("%s must be at most %s" % (where, spec["maximum"]))
    return value


def validate(schema: Dict[str, Any], args: Any, prefix: str = "") -> Dict[str, Any]:
    """Check args against a (small subset of) JSON Schema and coerce the usual model slips ("5" for 5)."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    props = schema.get("properties", {})
    out = {}  # type: Dict[str, Any]
    for key, value in args.items():
        if key not in props:
            if schema.get("additionalProperties") is False:
                raise ValueError("unknown argument %s%s (accepted: %s)" % (prefix, key, ", ".join(sorted(props))))
            out[key] = value
            continue
        if value is None:
            continue  # models send null for "not set"
        out[key] = _coerce(value, props[key], prefix + key)
    for key in schema.get("required", []):
        if key not in out:
            raise ValueError("missing required argument %s%s" % (prefix, key))
    return out


# --- the call pipeline -----------------------------------------------------------------------------------------

def load_tools() -> Dict[str, Tool]:
    if not REGISTRY:
        from . import tools  # noqa: F401  (importing the package registers every tool)
    return REGISTRY


def visible_tools(ctx_policy: Dict[str, Any], scope: str) -> List[Tool]:
    out = []
    for t in load_tools().values():
        if t.name in ctx_policy["disabled_tools"] or t.category in ctx_policy["disabled_categories"]:
            continue
        out.append(t)
    return sorted(out, key=lambda t: (policy_mod.CATEGORIES.index(t.category), t.name))


def safe_args(tool: Tool, args: Any) -> Dict[str, Any]:
    """The validated arguments of a call, or the raw ones when they are not valid (so a classification never raises)."""
    try:
        return validate(tool.schema, args)
    except ValueError:
        return dict(args) if isinstance(args, dict) else {}


def format_result(r: Any) -> Dict[str, Any]:
    if isinstance(r, Result):
        res = {"content": [{"type": "text", "text": r.text}], "isError": r.is_error}
        if r.data is not None:
            res["structuredContent"] = r.data if isinstance(r.data, dict) else {"result": r.data}
        return res
    if isinstance(r, str):
        return {"content": [{"type": "text", "text": r}], "isError": False}
    import json
    return {"content": [{"type": "text", "text": json.dumps(r, indent=2, ensure_ascii=False, default=str)}],
            "isError": False, "structuredContent": r if isinstance(r, dict) else {"result": r}}


def call_tool(name: str, args: Any, ctx: Ctx) -> Dict[str, Any]:
    """Run a tool and return an MCP tool result. Never raises: every failure becomes an error result."""
    tools = load_tools()
    tool = tools.get(name)
    started = time.time()
    entry = {"client": ctx.client, "transport": ctx.transport, "ip": ctx.ip, "tool": name, "args": args}  # type: Dict[str, Any]
    if tool is None:
        entry.update(ok=False, error="unknown tool")
        _audit(ctx, entry, started)
        return {"content": [{"type": "text", "text": "Unknown tool %r. Call tools/list to see the tools of this server." % name}],
                "isError": True}
    try:
        clean = validate(tool.schema, args)
        entry["args"] = clean
        policy_mod.check_tool(ctx.policy, ctx.scope, tool, clean)
        if tool.elevatable and clean.pop("sudo", False) and os.geteuid() != 0:
            out = _run_as_root(tool, clean, ctx)
        else:
            clean.pop("sudo", None)
            out = format_result(tool.handler(ctx, **clean))
        result = out.get("structuredContent")
        out["content"][0]["text"] = clip(out["content"][0]["text"], 400000)
        entry["ok"] = not out.get("isError", False)
        if out.get("isError"):
            entry["error"] = clip(out["content"][0]["text"], 300)
        if isinstance(result, dict):
            for k in ("exit_code", "root"):
                if k in result:
                    entry[k] = result[k]
    except policy_mod.Denied as e:
        out = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        entry.update(ok=False, error=str(e), denied=True)
    except (ValueError, ToolError) as e:
        out = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        entry.update(ok=False, error=str(e))
    except Exception as e:  # a bug in a tool: report it, keep the server alive
        out = {"content": [{"type": "text", "text": "Internal error in %s: %s: %s" % (name, type(e).__name__, e)}], "isError": True}
        entry.update(ok=False, error="%s: %s" % (type(e).__name__, e), trace=clip(traceback.format_exc(), 1500))
    _audit(ctx, entry, started)
    return out


def _run_as_root(tool: Tool, args: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    """Run one tool again in a root process (``sudo -n``) and return its result. Used when the server is not root."""
    from . import proc

    engine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    argv = ["sudo", "-n", "--", "env", "ERVISIO_AI_NOAUDIT=1", sys.executable, "-B", engine, "call-inline"]
    payload = json.dumps({"tool": tool.name, "args": args, "client": ctx.client, "transport": ctx.transport})
    res = proc.run(argv, stdin=payload, timeout=ctx.policy["max_timeout_sec"], cancel=ctx.cancel, max_output=8000000)
    try:
        return json.loads(res["stdout"])
    except ValueError:
        hint = proc.sudo_hint(res.get("stderr", "")) or clip(res.get("stderr", "").strip(), 400)
        raise ToolError("Could not run %s as root: %s" % (tool.name, hint or "no answer"))


def _audit(ctx: Ctx, entry: Dict[str, Any], started: float) -> None:
    if not ctx.policy.get("audit", True) or os.environ.get("ERVISIO_AI_NOAUDIT"):
        return
    entry["ms"] = int((time.time() - started) * 1000)
    audit.record(entry)
