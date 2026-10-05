"""Administration of the headless server: install, service, tokens, policy, audit, diagnostics.

Everything here is a plain function returning a dict, so the Ervisio page (through ``ervisio-ai ctl``) and a person at
a shell (``ervisio-ai status``) use exactly the same code.
"""
import glob
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from . import __version__, audit, auth, config, paths, policy, proc

UNIT = """[Unit]
Description=Ervisio AI MCP server
Documentation=https://github.com/Ervisio/plugin-ai
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={wrapper} serve
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
UMask=0077
# The AI is meant to have full access to this machine, so the service is not sandboxed. Only the server process is
# stopped on a restart: terminals (tmux) and background jobs it started keep running.
KillMode=process

[Install]
WantedBy=multi-user.target
"""


class CtlError(Exception):
    pass


def _need_root() -> None:
    if os.geteuid() != 0:
        raise CtlError("This needs root. Unlock administrator rights in Ervisio, or run it with sudo.")


def _engine_source() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _installed_version() -> Optional[str]:
    try:
        with open(os.path.join(paths.INSTALL_DIR, "ervisio_ai", "__init__.py")) as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def _has_systemd() -> bool:
    return bool(proc.which("systemctl")) and os.path.isdir("/run/systemd/system")


def _sc(*args: str, timeout: float = 60) -> Dict[str, Any]:
    return proc.run(["systemctl", "--no-pager"] + list(args), timeout=timeout)


def _pidfile() -> str:
    return os.path.join(paths.server_dir(), "server.pid")


def _pid_alive() -> Optional[int]:
    try:
        with open(_pidfile()) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except PermissionError:
        pass  # alive, but owned by root and we are not (the status page runs as the person, the server as root)
    except (OSError, ValueError):
        return None
    if _is_zombie(pid):  # ended, but nobody has collected it yet
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
        return None
    return pid


# --- status ----------------------------------------------------------------------------------------------------

def _hosts() -> List[str]:
    out = []  # type: List[str]
    ip = proc.which("ip")
    if ip:
        r = proc.run([ip, "-o", "-4", "addr", "show", "scope", "global"], timeout=5)
        for line in r["stdout"].splitlines():
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", line)
            if m:
                out.append(m.group(1))
    return out


def _sshd() -> Dict[str, Any]:
    port, root_login, password = 22, "unknown", "unknown"
    files = ["/etc/ssh/sshd_config"] + sorted(glob.glob("/etc/ssh/sshd_config.d/*.conf"))
    for f in files:
        try:
            for line in open(f):
                parts = line.split()
                if len(parts) >= 2 and not line.lstrip().startswith("#"):
                    k = parts[0].lower()
                    if k == "port":
                        port = int(parts[1])
                    elif k == "permitrootlogin":
                        root_login = parts[1]
                    elif k == "passwordauthentication":
                        password = parts[1]
        except (OSError, ValueError):
            continue
    running = False
    if proc.which("ss"):
        r = proc.run(["ss", "-ltnH"], timeout=5)
        running = any((":%d " % port) in l for l in r["stdout"].splitlines())
    return {"port": port, "running": running, "permit_root_login": root_login, "password_auth": password,
            "installed": bool(proc.which("sshd") or os.path.exists("/usr/sbin/sshd"))}


def _users() -> List[Dict[str, Any]]:
    out = []
    for p in pwd.getpwall():
        if p.pw_uid == 0 or (p.pw_uid >= 1000 and p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false"))):
            keys = os.path.exists(os.path.join(p.pw_dir, ".ssh", "authorized_keys"))
            out.append({"name": p.pw_name, "uid": p.pw_uid, "home": p.pw_dir, "ssh_keys": keys})
    return out


def _server_uid(pid: Optional[int]) -> Optional[int]:
    """Who the running server belongs to (from /proc), or None when it is not running or cannot be told."""
    if not pid:
        return None
    try:
        return os.stat("/proc/%d" % pid).st_uid
    except OSError:
        return None


def _service_state() -> Dict[str, Any]:
    st = {"manager": "none", "installed": False, "active": False, "enabled": False, "pid": None}  # type: Dict[str, Any]
    if _has_systemd():
        st["manager"] = "systemd"
        st["installed"] = os.path.exists(paths.UNIT_PATH)
        if st["installed"]:
            st["active"] = _sc("is-active", paths.UNIT_NAME)["stdout"].strip() == "active"
            st["enabled"] = _sc("is-enabled", paths.UNIT_NAME)["stdout"].strip() == "enabled"
            pid = _sc("show", paths.UNIT_NAME, "-p", "MainPID", "--value")["stdout"].strip()
            st["pid"] = int(pid) if pid.isdigit() and int(pid) > 0 else None
    else:
        pid = _pid_alive()
        st.update(manager="pidfile", installed=os.path.exists(paths.WRAPPER), active=pid is not None, pid=pid)
    return st


def _health(cfg: Dict[str, Any]) -> bool:
    import ssl
    host = "127.0.0.1" if cfg["bind"] in ("0.0.0.0", "::") else cfg["bind"]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        scheme = "https" if cfg["tls"] != "none" else "http"
        with urllib.request.urlopen("%s://%s:%s/healthz" % (scheme, host if ":" not in host else "[%s]" % host, cfg["port"]), timeout=3, context=ctx) as r:
            return r.status == 200
    except Exception:
        return False


def status(_: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.load()
    installed = _installed_version()
    svc = _service_state() if os.geteuid() == 0 or True else {}
    scheme = "https" if cfg["tls"] != "none" else "http"
    from .bridge import find_bridge
    return {
        "version": __version__,
        "root": os.geteuid() == 0,
        "hostname": socket.gethostname(),
        "engine": {"installed": installed is not None, "version": installed, "path": paths.INSTALL_DIR, "wrapper": os.path.exists(paths.WRAPPER),
                   "outdated": installed is not None and installed != __version__},
        "service": svc,
        "config": cfg,
        "url": "%s://%s:%s/mcp" % (scheme, "127.0.0.1" if cfg["bind"] in ("0.0.0.0", "::") else cfg["bind"], cfg["port"]),
        "reachable": _health(cfg) if svc.get("active") else False,
        "tokens": len(auth.list_tokens()) if os.geteuid() == 0 else None,  # the file is root-only: unknown to anyone else
        "policy": policy.load(),
        "tools": {n: proc.which(n) is not None for n in ("tmux", "systemctl", "docker", "openssl", "ssh", "sudo")},
        "ervisio_bridge": find_bridge(),
        "python": "%d.%d.%d" % sys.version_info[:3],
    }


def _load_connector() -> Any:
    import importlib.util
    path = os.path.join(_engine_source(), "..", "connect", "ervisio-ai-connect.py")
    if not os.path.exists(path):
        raise CtlError("The connector script is not next to the engine (%s)." % os.path.normpath(path))
    spec = importlib.util.spec_from_file_location("ervisio_ai_connect", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def connect_snippets(params: Dict[str, Any]) -> Dict[str, Any]:
    """The settings of every supported AI tool for this server, written by the same code the connector uses."""
    mod = _load_connector()
    mode = params.get("mode", "ssh")
    target = mod.Target(
        params.get("name") or "ervisio", mode, host=params.get("host", ""), user=params.get("user", ""), port=int(params.get("port") or 22),
        identity=params.get("identity", ""), engine=params.get("engine") or "ervisio-ai", url=params.get("url", ""), token=params.get("token", ""),
        insecure=bool(params.get("insecure")), ssh_password=bool(params.get("ssh_password")))
    env = mod.Env()
    out = []
    for cls in mod.CLIENTS:
        c = cls(env)
        why = c.supports(target)
        out.append({"id": c.id, "label": c.label, "where": c.where(), "lang": c.lang, "body": c.body(target), "unsupported": why})
    return {"clients": out, "version": mod.VERSION}


def connect_info(_: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.load()
    return {"hostname": socket.gethostname(), "addresses": _hosts(), "ssh": _sshd(), "users": _users(), "config": cfg,
            "wrapper": paths.WRAPPER, "stdio_command": "%s stdio" % paths.WRAPPER}


# --- install / service -----------------------------------------------------------------------------------------

def _copy_engine() -> None:
    src, dst = _engine_source(), paths.INSTALL_DIR
    tmp = dst + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, mode=0o755)
    shutil.copy2(os.path.join(src, "__main__.py"), os.path.join(tmp, "__main__.py"))
    shutil.copytree(os.path.join(src, "ervisio_ai"), os.path.join(tmp, "ervisio_ai"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    old = dst + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(dst):
        os.rename(dst, old)
    os.rename(tmp, dst)
    shutil.rmtree(old, ignore_errors=True)
    wrapper = "#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable or "python3", paths.INSTALL_DIR)
    os.makedirs(os.path.dirname(paths.WRAPPER), exist_ok=True)
    with open(paths.WRAPPER, "w") as f:
        f.write(wrapper)
    os.chmod(paths.WRAPPER, 0o755)


def _stop_pidfile() -> None:
    """Stop the background server and wait until it is really gone (SIGKILL after 10 s)."""
    pid = _pid_alive()
    if not pid:
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return
    for _ in range(100):
        if _pid_alive() is None:
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        time.sleep(0.3)


def _is_zombie(pid: int) -> bool:
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return True


def _start_pidfile() -> None:
    if _pid_alive():
        return
    log = open(os.path.join(paths.server_dir(), "server.log"), "ab")
    p = subprocess.Popen([paths.WRAPPER, "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd="/")
    with open(_pidfile(), "w") as f:
        f.write(str(p.pid))
    time.sleep(0.7)


def install(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    service = params.get("service", True)
    # 0711: other users can reach server.json, the pid and the log (the status page reads them), and nothing else
    paths.ensure_dir(paths.data_dir(), 0o711)
    os.chmod(paths.data_dir(), 0o711)
    paths.ensure_dir(paths.root_spool_dir(), 0o700)
    _copy_engine()
    settings = {k: params[k] for k in ("bind", "port", "tls", "allowed_hosts", "allowed_origins", "cert", "key") if k in params}
    if settings or not os.path.exists(config.config_file()):
        config.save(settings)
    result = {"engine": paths.INSTALL_DIR, "wrapper": paths.WRAPPER}  # type: Dict[str, Any]
    if params.get("token_label"):
        result["token"] = auth.create(str(params["token_label"]), str(params.get("token_scope", "full")), params.get("token_days"))
    if service:
        if _has_systemd():
            with open(paths.UNIT_PATH, "w") as f:
                f.write(UNIT.format(wrapper=paths.WRAPPER))
            _sc("daemon-reload")
            r = _sc("enable", "--now", paths.UNIT_NAME) if not _service_state()["active"] else _sc("restart", paths.UNIT_NAME)
            if r["exit_code"] != 0:
                raise CtlError("systemd could not start the service: %s" % r["stderr"].strip())
        else:
            _start_pidfile()
        time.sleep(0.8)
    result["status"] = status({})
    return result


def upgrade(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    was_active = _service_state()["active"]
    _copy_engine()
    if was_active:
        _service_action("restart")
    return {"status": status({})}


def uninstall(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    if _has_systemd() and os.path.exists(paths.UNIT_PATH):
        _sc("disable", "--now", paths.UNIT_NAME)
        os.unlink(paths.UNIT_PATH)
        _sc("daemon-reload")
    else:
        _stop_pidfile()
    shutil.rmtree(paths.INSTALL_DIR, ignore_errors=True)
    for f in (paths.WRAPPER,):
        try:
            os.unlink(f)
        except OSError:
            pass
    if params.get("purge"):
        shutil.rmtree(paths.data_dir(), ignore_errors=True)
    return {"status": status({})}


def _service_action(action: str) -> None:
    if _has_systemd() and os.path.exists(paths.UNIT_PATH):
        r = _sc(action, paths.UNIT_NAME)
        if r["exit_code"] != 0:
            raise CtlError("systemctl %s failed: %s" % (action, r["stderr"].strip()))
        return
    if not os.path.exists(paths.WRAPPER):
        raise CtlError("The service is not installed. Install it first.")
    if action in ("stop", "restart"):
        _stop_pidfile()
    if action in ("start", "restart"):
        _start_pidfile()


def service_control(action: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    def run(_: Dict[str, Any]) -> Dict[str, Any]:
        _need_root()
        if action in ("enable", "disable"):
            if not _has_systemd():
                raise CtlError("enable/disable needs systemd.")
            r = _sc(action, paths.UNIT_NAME)
            if r["exit_code"] != 0:
                raise CtlError(r["stderr"].strip())
        else:
            _service_action(action)
        time.sleep(0.6)
        return {"status": status({})}
    return run


def config_set(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    restart = params.pop("restart", True)
    try:
        cfg = config.save({k: v for k, v in params.items() if k in config.DEFAULTS})
    except ValueError as e:
        raise CtlError(str(e))
    if restart and _service_state()["active"]:
        _service_action("restart")
        time.sleep(0.8)
    return {"config": cfg, "status": status({})}


# --- tokens, policy, audit, logs -------------------------------------------------------------------------------

def token_create(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    try:
        return {"token": auth.create(str(params.get("label", "")), str(params.get("scope", "full")), params.get("days"))}
    except ValueError as e:
        raise CtlError(str(e))


def token_list(_: Dict[str, Any]) -> Dict[str, Any]:
    return {"tokens": auth.list_tokens()}


def token_revoke(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    if not auth.revoke(str(params.get("id", ""))):
        raise CtlError("No token with that id or label.")
    return {"tokens": auth.list_tokens()}


def policy_get(_: Dict[str, Any]) -> Dict[str, Any]:
    return {"policy": policy.load(), "categories": list(policy.CATEGORIES)}


def policy_set(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    try:
        return {"policy": policy.save(params), "categories": list(policy.CATEGORIES)}
    except ValueError as e:
        raise CtlError(str(e))


def audit_read(params: Dict[str, Any]) -> Dict[str, Any]:
    return {"entries": audit.read(int(params.get("limit", 100)), params.get("tool"), params.get("client"), params.get("text"),
                                  bool(params.get("errors")), params.get("before"))}


def logs(params: Dict[str, Any]) -> Dict[str, Any]:
    n = int(params.get("lines", 100))
    if _has_systemd() and os.path.exists(paths.UNIT_PATH):
        r = proc.run(["journalctl", "--no-pager", "-o", "short-iso", "-u", paths.UNIT_NAME, "-n", str(n)], timeout=20)
        return {"text": r["stdout"] or r["stderr"]}
    try:
        with open(os.path.join(paths.server_dir(), "server.log"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 100000))
            return {"text": "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])}
    except OSError:
        return {"text": ""}


def deps_install(params: Dict[str, Any]) -> Dict[str, Any]:
    _need_root()
    from . import registry
    wanted = [p for p in params.get("packages", ["tmux"]) if p in ("tmux", "openssl", "ripgrep", "curl", "git")]
    res = registry.call_tool("package_manage", {"action": "install", "packages": wanted}, registry.Ctx(client="ervisio-ui", transport="ervisio"))
    return {"ok": not res.get("isError"), "output": res["content"][0]["text"]}


# --- doctor ----------------------------------------------------------------------------------------------------

def doctor(_: Dict[str, Any]) -> Dict[str, Any]:
    checks = []  # type: List[Dict[str, Any]]

    def add(name: str, ok: bool, detail: str, fix: str = "", level: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix, "level": level or ("ok" if ok else "warn")})

    add("python", sys.version_info >= (3, 8), "Python %d.%d.%d" % sys.version_info[:3], "Install Python 3.8 or newer.")
    st = status({})
    server_uid = _server_uid(st["service"].get("pid"))
    runs_as_root = server_uid == 0 if server_uid is not None else os.geteuid() == 0
    add("root", runs_as_root, "the server runs as %s" % ("root" if runs_as_root else "a normal user") if server_uid is not None
        else "running as %s" % ("root" if os.geteuid() == 0 else "a normal user"),
        "The headless server needs root for full access; otherwise it can only do what its user can.", "ok" if runs_as_root else "info")
    add("engine installed", st["engine"]["installed"], "version %s" % st["engine"]["version"] if st["engine"]["installed"] else "not installed",
        "Install it from the Ervisio page (or: ervisio-ai install).")
    if st["engine"]["outdated"]:
        add("engine up to date", False, "installed %s, plugin has %s" % (st["engine"]["version"], __version__), "Run upgrade.")
    tools = st["tools"]
    add("systemd", tools["systemctl"] and _has_systemd(), "systemd manages the service" if _has_systemd() else "no systemd: the server runs as a plain background process",
        "", "ok" if _has_systemd() else "info")
    add("tmux", tools["tmux"], "terminals available" if tools["tmux"] else "missing: terminal_* tools are off", "Install tmux (one click on the Ervisio page).")
    add("openssl", tools["openssl"], "HTTPS with a self-signed certificate is possible" if tools["openssl"] else "missing: no self-signed HTTPS",
        "Install openssl.", "ok" if tools["openssl"] else "info")
    add("sudo", tools["sudo"] or os.geteuid() == 0, "available" if tools["sudo"] else "not needed as root", "", "ok" if tools["sudo"] or os.geteuid() == 0 else "info")
    sshd = _sshd()
    add("ssh server", sshd["running"], "listening on port %d (root login: %s, passwords: %s)" % (sshd["port"], sshd["permit_root_login"], sshd["password_auth"]) if sshd["running"]
        else "not listening on port %d: the SSH way of connecting (recommended) needs it" % sshd["port"], "Install and start openssh-server, or use the HTTP way.")
    add("ervisio bridge", bool(st["ervisio_bridge"]), st["ervisio_bridge"] or "not found: the ervisio_* tools are off", "Install Ervisio on this machine.")
    if st["service"]["installed"]:
        add("service running", bool(st["service"]["active"]), "active" if st["service"]["active"] else "stopped", "Start it.")
        if st["service"]["active"]:
            add("answers requests", st["reachable"], "GET /healthz is ok" if st["reachable"] else "no answer on %s" % st["url"], "Check the logs.")
        cfg = st["config"]
        if cfg["bind"] not in ("127.0.0.1", "::1") and cfg["tls"] == "none":
            add("transport security", False, "listening on %s over plain HTTP: tokens and commands cross the network unencrypted" % cfg["bind"],
                "Use SSH, HTTPS (put Caddy in front, or choose self-signed), or bind to 127.0.0.1.", "warn")
        if st["tokens"] == 0:  # None = not readable from here
            add("tokens", False, "no tokens yet: nobody can connect over HTTP", "Create one for each device.", "info")
    pol = st["policy"]
    add("policy", True, policy.describe(pol), "", "info")
    return {"checks": checks, "ok": all(c["ok"] or c["level"] == "info" for c in checks)}


ACTIONS = {
    "status": status, "connect_info": connect_info, "connect_snippets": connect_snippets, "install": install, "upgrade": upgrade, "uninstall": uninstall,
    "start": service_control("start"), "stop": service_control("stop"), "restart": service_control("restart"),
    "enable": service_control("enable"), "disable": service_control("disable"), "config_set": config_set,
    "token_create": token_create, "token_list": token_list, "token_revoke": token_revoke,
    "policy_get": policy_get, "policy_set": policy_set, "audit": audit_read, "logs": logs, "doctor": doctor,
    "deps_install": deps_install,
}  # type: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]


def run(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    fn = ACTIONS.get(action)
    if fn is None:
        raise CtlError("Unknown action %r. Known: %s" % (action, ", ".join(sorted(ACTIONS))))
    return fn(dict(params))


# --- command line for people -----------------------------------------------------------------------------------

def cli(cmd: str, rest: List[str]) -> int:
    from .cli import _parse_flags

    flags = _parse_flags(rest)
    try:
        if cmd == "token":
            sub = rest[0] if rest else "list"
            if sub == "create":
                label = rest[1] if len(rest) > 1 and not rest[1].startswith("--") else flags.get("label", "")
                res = run("token_create", {"label": label, "scope": flags.get("scope", "full"), "days": flags.get("days")})
                t = res["token"]
                print("Token for %r (%s). It is shown only now, keep it safe:\n\n  %s\n" % (t["label"], t["scope"], t["token"]))
                return 0
            if sub == "revoke" and len(rest) > 1:
                run("token_revoke", {"id": rest[1]})
                print("Revoked.")
                return 0
            for t in run("token_list", {})["tokens"]:
                print("%-8s %-16s %-9s created %s  last used %s" % (t["id"], t["label"], t["scope"], t["created"], t.get("last_used") or "never"))
            return 0
        if cmd == "policy":
            if rest[:1] == ["set"] and len(rest) >= 3:
                try:
                    value = json.loads(rest[2])
                except ValueError:
                    value = rest[2]
                run("policy_set", {rest[1]: value})
            print(json.dumps(run("policy_get", {})["policy"], indent=2))
            return 0
        if cmd == "audit":
            for e in run("audit", {"limit": flags.get("limit", 30), "tool": flags.get("tool"), "errors": flags.get("errors")})["entries"]:
                print("%s %-28s %-22s %s %s" % (e.get("time", "")[:19], str(e.get("client", ""))[:28], e.get("tool", ""),
                                               "ok " if e.get("ok") else "ERR", json.dumps(e.get("args", {}), ensure_ascii=False)[:90]))
            return 0
        if cmd == "doctor":
            res = run("doctor", {})
            for c in res["checks"]:
                print("%-4s %-22s %s%s" % ({"ok": "ok", "warn": "WARN", "info": "info"}[c["level"]], c["name"], c["detail"],
                                           ("  -> " + c["fix"]) if not c["ok"] and c["fix"] else ""))
            return 0 if res["ok"] else 1
        if cmd in ACTIONS:
            res = run(cmd, flags)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            if cmd == "install" and res.get("token"):
                print("\nToken (shown only now): %s" % res["token"]["token"])
            return 0
    except CtlError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    sys.stdout.write("unknown command %r\n\n" % cmd)
    from .cli import USAGE
    sys.stdout.write(USAGE)
    return 2
