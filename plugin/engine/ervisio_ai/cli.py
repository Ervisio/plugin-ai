"""Command line of the engine."""
import json
import os
import re
import signal
import socket
import sys
import threading
from typing import Any, Dict, List, Optional

from . import NAME, __version__, config, paths, policy, registry

USAGE = """ervisio-ai %s

MCP server
  serve                 headless MCP server over HTTP (token required)
  stdio                 MCP over stdin/stdout (for IDEs that start it through ssh)

Administration (as root)
  status                what is installed, running, and how to connect
  install               install the engine and the service   [--port N] [--bind ADDR] [--tls none|self-signed]
  uninstall             remove the service and the engine    [--purge]
  start | stop | restart
  token create LABEL    make a token                         [--scope full|readonly] [--days N]
  token list | token revoke ID
  policy                show the policy; set with: policy set KEY VALUE
  audit                 recent AI activity                   [--limit N] [--tool NAME] [--errors]
  doctor                check this machine

Used by the Ervisio page
  call ID | ctl ID | tools | whoami | call-inline
""" % __version__

REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_spool(request_id: str) -> Dict[str, Any]:
    if not REQUEST_ID.match(request_id):
        _print({"t": "error", "message": "bad request id"})
        sys.exit(2)
    path = os.path.join(paths.spool_dir(), request_id + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        _print({"t": "error", "message": "cannot read the request: %s" % e})
        sys.exit(2)
    try:
        os.unlink(path)
    except OSError:
        pass
    return data


def cmd_call(request_id: str) -> int:
    req = _read_spool(request_id)
    cancel = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: cancel.set())

    def progress(message: str, done: Optional[float], total: Optional[float]) -> None:
        _print({"t": "progress", "message": message})

    ctx = registry.Ctx(client=str(req.get("client") or "ervisio-chat"), scope="full", transport="ervisio", progress=progress, cancel=cancel)
    if req.get("classify"):
        # the page asks whether this call only reads, to decide if it must ask the person first; nothing is run
        tool = registry.load_tools().get(str(req.get("tool", "")))
        read_only = tool is not None and tool.is_read_only(registry.safe_args(tool, req.get("args")))
        _print({"t": "result", "result": {"classify": {"readOnly": bool(read_only), "known": tool is not None}}})
        return 0
    result = registry.call_tool(str(req.get("tool", "")), req.get("args") or {}, ctx)
    _print({"t": "result", "result": result})
    from . import bridge
    bridge.shutdown()
    return 0


def cmd_call_inline() -> int:
    """Run one tool from a JSON request on stdin and print its MCP result. Used for ``sudo`` re-execution."""
    req = json.load(sys.stdin)
    ctx = registry.Ctx(client=str(req.get("client") or "ervisio-chat"), scope="full", transport=str(req.get("transport") or "stdio"))
    result = registry.call_tool(str(req.get("tool", "")), req.get("args") or {}, ctx)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    from . import bridge
    bridge.shutdown()
    return 0


def cmd_tools() -> int:
    pol = policy.load()
    _print({"version": __version__, "policy": pol, "tools": [t.to_mcp() for t in registry.visible_tools(pol, "full")]})
    return 0


def cmd_whoami() -> int:
    import pwd
    try:
        user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        user = str(os.geteuid())
    _print({"user": user, "uid": os.geteuid(), "home": paths.home_of_user(), "root": os.geteuid() == 0, "hostname": socket.gethostname(),
            "version": __version__, "engine": os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_dir": paths.data_dir()})
    return 0


def _parse_flags(argv: List[str]) -> Dict[str, Any]:
    out = {}  # type: Dict[str, Any]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:].replace("-", "_")
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                val = argv[i + 1]
                i += 1
                try:
                    out[key] = json.loads(val)
                except ValueError:
                    out[key] = val
            else:
                out[key] = True
        i += 1
    return out


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd in ("version", "--version"):
        print("%s %s" % (NAME, __version__))
        return 0
    if cmd == "stdio":
        from .transport_stdio import serve_stdio
        return serve_stdio()
    if cmd == "serve":
        from .transport_http import serve_http
        cfg = config.load()
        flags = _parse_flags(rest)
        for k in ("bind", "port", "tls", "cert", "key"):
            if k in flags:
                cfg[k] = int(flags[k]) if k == "port" else flags[k]
        return serve_http(cfg)
    if cmd == "tools":
        return cmd_tools()
    if cmd == "whoami":
        return cmd_whoami()
    if cmd == "call" and len(rest) == 1:
        return cmd_call(rest[0])
    if cmd == "call-inline":
        return cmd_call_inline()
    from . import ctl
    if cmd == "ctl" and len(rest) == 1:
        req = _read_spool(rest[0])
        try:
            _print({"t": "result", "result": ctl.run(str(req.get("action", "")), req.get("params") or {})})
        except ctl.CtlError as e:
            _print({"t": "error", "message": str(e)})
            return 1
        return 0
    return ctl.cli(cmd, rest)
