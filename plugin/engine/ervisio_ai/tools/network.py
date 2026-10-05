"""Talk to the network from this machine: HTTP requests, connectivity checks, SSH to other hosts, the firewall."""
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .. import __version__, policy, proc
from ..registry import Ctx, S, ToolError, fail, ok, register
from ..util import human_bytes
from .files import resolve
from .shell import CONFIRM, render


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:  # type: ignore[override]
        return None


@register(
    "net_request", "network",
    "Make an HTTP(S) request from this machine (like curl). Returns the status, main headers and the body. Use it to "
    "test a service from the server's own point of view, call an API, or download a file with save_to.",
    {
        "url": S("string", "http:// or https:// address."),
        "method": S("string", "GET (default), POST, PUT, PATCH, DELETE, HEAD or OPTIONS.", enum=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
        "headers": S("object", "Request headers as an object of strings."),
        "body": S("string", "Request body as text."),
        "json": S("object", "Request body as JSON (sets Content-Type)."),
        "timeout": S("integer", "Seconds (default 30).", minimum=1, maximum=600),
        "follow_redirects": S("boolean", "Follow redirects (default true)."),
        "insecure": S("boolean", "Do not verify the TLS certificate."),
        "max_bytes": S("integer", "Largest body to return (default 200000).", minimum=100, maximum=5000000),
        "save_to": S("string", "Write the response body to this file instead of returning it (any size)."),
    },
    ["url"], title="HTTP request", destructive=True, open_world=True,
)
def net_request(ctx: Ctx, url: str, method: str = "GET", headers: Optional[Dict[str, Any]] = None, body: Optional[str] = None,
                json: Optional[Dict[str, Any]] = None, timeout: int = 30, follow_redirects: bool = True, insecure: bool = False,  # noqa: A002
                max_bytes: int = 200000, save_to: Optional[str] = None):
    import json as jsonlib

    if not re.match(r"^https?://", url, re.I):
        raise ToolError("url must start with http:// or https://")
    hdrs = {"User-Agent": "ervisio-ai/%s" % __version__}
    hdrs.update({str(k): str(v) for k, v in (headers or {}).items()})
    data = None
    if json is not None:
        data = jsonlib.dumps(json).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body.encode()
    handlers = []  # type: List[Any]
    if insecure:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    if not follow_redirects:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    started = time.time()
    status, reason, rheaders, raw = 0, "", {}, b""
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e
    except urllib.error.URLError as e:
        raise ToolError("Request failed: %s" % e.reason)
    except (socket.timeout, TimeoutError):
        raise ToolError("Request timed out after %ds." % timeout)
    except (ssl.SSLError, OSError) as e:
        raise ToolError("Request failed: %s" % e)
    status, reason = resp.getcode(), getattr(resp, "reason", "")
    rheaders = {k.lower(): v for k, v in resp.headers.items()}
    if save_to:
        dest = resolve(save_to)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        total = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk or ctx.cancel.is_set():
                    break
                f.write(chunk)
                total += len(chunk)
        raw = b""
        shown = "[saved %s to %s]" % (human_bytes(total), dest)
    else:
        raw = resp.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        if b"\0" in raw[:2000]:
            shown = "[binary body, %d bytes; use save_to to keep it]" % len(raw)
        else:
            shown = raw.decode("utf-8", "replace") + ("\n… (body cut at %d bytes)" % max_bytes if truncated else "")
    keep = ["content-type", "content-length", "location", "server", "date", "www-authenticate", "set-cookie", "cache-control", "x-powered-by"]
    head = "%d %s  (%.2fs)  %s" % (status, reason, time.time() - started, resp.geturl() if hasattr(resp, "geturl") else url)
    lines = [head] + ["%s: %s" % (k, rheaders[k]) for k in keep if k in rheaders] + ["", shown]
    return (ok if status < 400 else fail)("\n".join(lines), {"status": status})


@register(
    "net_check", "network",
    "Check how a host is reachable from this machine: DNS resolution, a TCP connection to a port, the TLS certificate "
    "(issuer, expiry) and ping.",
    {
        "host": S("string", "Host name or IP."),
        "port": S("integer", "TCP port to connect to (omit to check DNS and ping only).", minimum=1, maximum=65535),
        "tls": S("boolean", "Also read the TLS certificate on that port (default true for 443/8443/993/995/465)."),
        "ping": S("boolean", "Also ping three times (default true)."),
    },
    ["host"], title="Check connectivity", read_only=True, open_world=True,
)
def net_check(ctx: Ctx, host: str, port: Optional[int] = None, tls: Optional[bool] = None, ping: bool = True):
    if not re.match(r"^[A-Za-z0-9._:-]{1,253}$", host):
        raise ToolError("%r is not a host name." % host)
    lines = []  # type: List[str]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        lines.append("dns: %s -> %s" % (host, ", ".join(addrs)))
    except socket.gaierror as e:
        return fail("dns: %s does not resolve (%s)" % (host, e))
    if port:
        t0 = time.time()
        try:
            s = socket.create_connection((host, port), timeout=8)
            lines.append("tcp %d: open (%.0f ms)" % (port, (time.time() - t0) * 1000))
            s.close()
        except OSError as e:
            lines.append("tcp %d: FAILED (%s)" % (port, e))
            port = None
    if port and (tls is True or tls is None and port in (443, 8443, 993, 995, 465)):
        try:
            c = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=8) as raw:
                with c.wrap_socket(raw, server_hostname=host) as ss:
                    cert = ss.getpeercert()
                    lines.append("tls: %s, %s" % (ss.version(), ss.cipher()[0]))
            subj = dict(x[0] for x in cert.get("subject", ()))
            iss = dict(x[0] for x in cert.get("issuer", ()))
            not_after = ssl.cert_time_to_seconds(cert["notAfter"])
            days = int((not_after - time.time()) / 86400)
            names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
            lines.append("certificate: valid, %s, issued by %s, expires %s (%d days)" % (subj.get("commonName", "?"), iss.get("organizationName", iss.get("commonName", "?")),
                                                                                         time.strftime("%Y-%m-%d", time.gmtime(not_after)), days))
            if names:
                lines.append("names: " + ", ".join(names[:12]))
        except ssl.SSLCertVerificationError as e:
            lines.append("certificate: NOT VALID (%s)" % e.verify_message)
        except (ssl.SSLError, OSError) as e:
            lines.append("tls: FAILED (%s)" % e)
    if ping and proc.which("ping"):
        r = proc.run(["ping", "-c", "3", "-W", "2", host], timeout=15)
        tail = [l for l in r["stdout"].splitlines() if "packet loss" in l or "rtt" in l or "round-trip" in l]
        lines.append("ping: " + (" | ".join(tail) if tail else "no answer"))
    return ok("\n".join(lines))


@register(
    "ssh_exec", "network",
    "Run a command on ANOTHER machine over SSH, using this server's SSH keys (non-interactive). Handy to manage a "
    "fleet from one place. First connection to a new host accepts and remembers its key.",
    {
        "host": S("string", "Host name or IP of the other machine."),
        "command": S("string", "Command line to run there."),
        "user": S("string", "SSH user (default: this server's own default)."),
        "port": S("integer", "SSH port.", minimum=1, maximum=65535),
        "identity": S("string", "Path of a private key file on this server."),
        "jump": S("string", "ProxyJump host, e.g. user@bastion."),
        "timeout": S("integer", "Seconds (default 120).", minimum=1, maximum=86400),
        "confirm_dangerous": CONFIRM,
    },
    ["host", "command"], title="Run a command over SSH", destructive=True, open_world=True,
)
def ssh_exec(ctx: Ctx, host: str, command: str, user: Optional[str] = None, port: Optional[int] = None, identity: Optional[str] = None,
             jump: Optional[str] = None, timeout: int = 120, confirm_dangerous: bool = False):
    if not re.match(r"^[A-Za-z0-9._:@-]{1,253}$", host) or host.startswith("-"):
        raise ToolError("%r is not a host name." % host)
    policy.guard_command(ctx.policy, command, {"confirm_dangerous": confirm_dangerous})
    ssh = proc.which("ssh")
    if not ssh:
        raise ToolError("The ssh client is not installed on this server (package openssh-client).")
    argv = [ssh, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10", "-T"]
    if port:
        argv += ["-p", str(port)]
    if identity:
        argv += ["-i", resolve(identity)]
    if jump:
        argv += ["-J", jump]
    argv += [("%s@%s" % (user, host)) if user else host, "--", command]
    res = proc.run(argv, timeout=min(timeout, ctx.policy["max_timeout_sec"]), cancel=ctx.cancel,
                   on_output=lambda s, t: ctx.progress(t.rstrip("\n")[-300:]))
    bad = res["exit_code"] != 0 or res.get("timed_out")
    hint = ""
    if res["exit_code"] == 255:
        hint = "\n[hint] SSH itself failed (host unreachable, key refused, or host key changed). Check with net_check and the key."
    return (fail if bad else ok)(render(res, None) + hint, {"exit_code": res["exit_code"]})


# --- firewall --------------------------------------------------------------------------------------------------

@register(
    "firewall_manage", "network",
    "Open or close ports with the machine's firewall (ufw or firewalld, detected automatically): show its state, allow "
    "or deny a port, delete a rule, reload, enable, disable. Enabling a firewall with no SSH rule is refused by the "
    "safety guard because it would lock everybody out.",
    {
        "action": S("string", "What to do.", enum=["status", "allow", "deny", "delete", "reload", "enable", "disable"]),
        "port": S("string", "Port or range with protocol, e.g. 443/tcp, 8000:8100/tcp, or a service name like 'ssh'."),
        "source": S("string", "allow/deny: only from this address or network, e.g. 203.0.113.0/24."),
        "confirm_dangerous": CONFIRM,
    },
    ["action"], title="Firewall", destructive=True, elevatable=True, read_only_actions={"status"},
)
def firewall_manage(ctx: Ctx, action: str, port: Optional[str] = None, source: Optional[str] = None, confirm_dangerous: bool = False):
    ufw, fcmd = proc.which("ufw"), proc.which("firewall-cmd")
    if action in ("allow", "deny", "delete") and not (port and re.match(r"^[A-Za-z0-9_-]+(?:[:/][A-Za-z0-9]+)?(?:/[a-z]+)?$", port)):
        raise ToolError("port is required, like 443/tcp.")
    if source and not re.match(r"^[0-9a-fA-F.:/]+$", source):
        raise ToolError("source must be an address or network such as 203.0.113.0/24.")
    if ufw:
        if action == "status":
            r = proc.run([ufw, "status", "numbered"], timeout=20)
            return ok((r["stdout"] or r["stderr"]).strip())
        if action == "enable":
            st = proc.run([ufw, "status"], timeout=20)["stdout"]
            if not re.search(r"\b(22|ssh|OpenSSH)\b", st) and not confirm_dangerous and ctx.policy["guard"]:
                raise policy.Denied("Safety guard: ufw has no rule allowing SSH, so enabling it would lock everybody out. First run "
                                    "firewall_manage allow port=22/tcp (or your SSH port), then enable. To override, repeat with "
                                    "confirm_dangerous=true.")
            argv = [ufw, "--force", "enable"]
        elif action == "disable":
            argv = [ufw, "disable"]
        elif action == "reload":
            argv = [ufw, "reload"]
        elif action == "delete":
            argv = [ufw, "--force", "delete", "allow", port or ""]
            if source:
                argv = [ufw, "--force", "delete", "allow", "from", source, "to", "any", "port", (port or "").split("/")[0]]
        else:
            argv = [ufw, action] + (["from", source, "to", "any", "port", (port or "").split("/")[0]] + (["proto", port.split("/")[1]] if port and "/" in port else [])
                                    if source else [port or ""])
        r = proc.run(argv, timeout=60)
        return (ok if r["exit_code"] == 0 else fail)((r["stdout"] + r["stderr"]).strip() or "done")
    if fcmd:
        if action == "status":
            return ok(proc.run([fcmd, "--list-all"], timeout=20)["stdout"].strip())
        if action in ("enable", "disable"):
            sc = proc.run(["systemctl", "enable" if action == "enable" else "disable", "--now", "firewalld"], timeout=60)
            return (ok if sc["exit_code"] == 0 else fail)(sc["stderr"].strip() or "done")
        if action == "reload":
            return ok(proc.run([fcmd, "--reload"], timeout=60)["stdout"].strip())
        verb = {"allow": "--add-port=", "deny": "--remove-port=", "delete": "--remove-port="}[action]
        if source and action == "allow":
            rich = 'rule family="ipv4" source address="%s" port port="%s" protocol="%s" accept' % (source, (port or "").split("/")[0], (port or "/tcp").split("/")[-1])
            r = proc.run([fcmd, "--permanent", "--add-rich-rule=" + rich], timeout=30)
        else:
            r = proc.run([fcmd, "--permanent", verb + (port if port and "/" in port else (port or "") + "/tcp")], timeout=30)
        if r["exit_code"] == 0:
            proc.run([fcmd, "--reload"], timeout=60)
        return (ok if r["exit_code"] == 0 else fail)((r["stdout"] + r["stderr"]).strip() or "done")
    if action == "status":
        return ok("No ufw or firewalld here. Raw state:\n" + (proc.run(["nft", "list", "ruleset"], timeout=20)["stdout"] if proc.which("nft")
                  else proc.run(["iptables", "-S"], timeout=20)["stdout"] if proc.which("iptables") else "no firewall tool found"))
    raise ToolError("Only ufw and firewalld can be managed with this tool. Use shell_exec for nft or iptables.")
