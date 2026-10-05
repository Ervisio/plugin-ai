"""System information, processes, network and logs."""
import os
import pwd
import re
import shutil
import signal as signals
import socket
from typing import Any, Dict, List, Optional

from .. import proc
from ..registry import Ctx, S, ToolError, fail, ok, register
from ..util import clip, human_bytes, human_duration

REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "vfat", "exfat", "ntfs", "ntfs3", "nfs", "nfs4", "cifs", "f2fs", "overlay", "fuseblk"}


def current_user() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


def _os_release() -> Dict[str, str]:
    out = {}  # type: Dict[str, str]
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k:
                    out[k] = v.strip('"')
    except OSError:
        pass
    return out


def _meminfo() -> Dict[str, int]:
    out = {}  # type: Dict[str, int]
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                parts = v.split()
                if parts:
                    out[k] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return out


def _cpu() -> str:
    model, cores = "", 0
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name") and not model:
                    model = line.split(":", 1)[1].strip()
                if line.startswith("processor"):
                    cores += 1
    except OSError:
        pass
    return "%s x%d" % (model or "unknown CPU", cores or os.cpu_count() or 1)


def _disks() -> List[Dict[str, Any]]:
    seen, out = set(), []
    try:
        with open("/proc/mounts") as f:
            rows = [l.split() for l in f]
    except OSError:
        return out
    for r in rows:
        if len(r) < 3 or r[2] not in REAL_FS or r[0] in seen:
            continue
        seen.add(r[0])
        try:
            u = shutil.disk_usage(r[1])
        except OSError:
            continue
        out.append({"mount": r[1], "device": r[0], "fstype": r[2], "total": u.total, "used": u.used, "free": u.free,
                    "percent": round(100.0 * u.used / u.total, 1) if u.total else 0.0})
    return out


def overview_text() -> str:
    rel = _os_release()
    mem = _meminfo()
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
    except (OSError, ValueError):
        up = 0.0
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    lines = [
        "os: %s, kernel %s %s" % (rel.get("PRETTY_NAME", "Linux"), os.uname().release, os.uname().machine),
        "host: %s, up %s, load %.2f %.2f %.2f" % (socket.gethostname(), human_duration(up), load[0], load[1], load[2]),
        "cpu: " + _cpu(),
    ]
    if mem.get("MemTotal"):
        used = mem["MemTotal"] - mem.get("MemAvailable", mem.get("MemFree", 0))
        lines.append("memory: %s used of %s" % (human_bytes(used), human_bytes(mem["MemTotal"])))
    if mem.get("SwapTotal"):
        lines.append("swap: %s used of %s" % (human_bytes(mem["SwapTotal"] - mem.get("SwapFree", 0)), human_bytes(mem["SwapTotal"])))
    for d in _disks():
        lines.append("disk %s: %s used of %s (%s%%) on %s" % (d["mount"], human_bytes(d["used"]), human_bytes(d["total"]), d["percent"], d["device"]))
    virt = proc.which("systemd-detect-virt")
    if virt:
        r = proc.run([virt], timeout=5)
        if r["stdout"].strip():
            lines.append("virtualization: " + r["stdout"].strip())
    ip = proc.which("ip")
    if ip:
        r = proc.run([ip, "-br", "addr"], timeout=5)
        addrs = [l for l in r["stdout"].splitlines() if l and not l.startswith("lo ")]
        if addrs:
            lines.append("network: " + "; ".join(" ".join(a.split()) for a in addrs[:6]))
    return "\n".join(lines)


@register("system_info", "system", "Operating system, kernel, CPU, memory, swap, disks, uptime, load, virtualization and addresses of this machine.",
          {}, title="System information", read_only=True, idempotent=True)
def system_info(ctx: Ctx):
    rel = _os_release()
    return ok(overview_text(), {"hostname": socket.gethostname(), "os": rel.get("PRETTY_NAME"), "kernel": os.uname().release,
                                "arch": os.uname().machine, "disks": _disks(), "memory": _meminfo()})


@register(
    "system_processes", "system",
    "List running processes with CPU, memory and command line, busiest first. Filter by a regular expression on the command.",
    {
        "sort": S("string", "cpu (default), mem or pid.", enum=["cpu", "mem", "pid"]),
        "filter": S("string", "Regular expression matched against the command line."),
        "limit": S("integer", "How many processes (default 25).", minimum=1, maximum=500),
    },
    title="List processes", read_only=True, idempotent=True, elevatable=True,
)
def system_processes(ctx: Ctx, sort: str = "cpu", filter: Optional[str] = None, limit: int = 25):  # noqa: A002
    key = {"cpu": "-pcpu", "mem": "-pmem", "pid": "pid"}[sort]
    ps = proc.which("ps")
    if not ps:
        raise ToolError("ps is not installed.")
    r = proc.run([ps, "-eo", "pid,ppid,user:14,pcpu,pmem,rss,etime,stat,args", "--sort=" + key, "-ww"], timeout=15)
    rows = r["stdout"].splitlines()
    head, rows = rows[:1], rows[1:]
    if filter:
        try:
            rx = re.compile(filter)
        except re.error as e:
            raise ToolError("bad regular expression: %s" % e)
        rows = [l for l in rows if rx.search(l)]
    return ok("\n".join(head + [clip(l, 300) for l in rows[:limit]]) + ("\n(%d more)" % (len(rows) - limit) if len(rows) > limit else ""))


@register(
    "process_signal", "system",
    "Send a signal to a process by pid, or to every process with an exact name (like killall). Default TERM; use KILL "
    "if it ignores it, HUP to make many daemons reload.",
    {
        "pid": S("integer", "Process id.", minimum=2),
        "name": S("string", "Exact process name (not the command line)."),
        "signal": S("string", "TERM (default), KILL, HUP, INT, USR1, USR2, STOP or CONT.",
                    enum=["TERM", "KILL", "HUP", "INT", "USR1", "USR2", "STOP", "CONT"]),
        "confirm_dangerous": S("boolean", "Needed to signal the server's own process."),
    },
    title="Signal a process", destructive=True, elevatable=True,
)
def process_signal(ctx: Ctx, pid: Optional[int] = None, name: Optional[str] = None, signal: str = "TERM", confirm_dangerous: bool = False):
    if (pid is None) == (name is None):
        raise ToolError("Give either pid or name.")
    pids = [pid] if pid is not None else []
    if name:
        pgrep = proc.which("pgrep")
        if not pgrep:
            raise ToolError("pgrep is not installed; use pid.")
        pids = [int(x) for x in proc.run([pgrep, "-x", name], timeout=5)["stdout"].split()]
        if not pids:
            raise ToolError("No process is named %r." % name)
    sig = getattr(signals, "SIG" + signal)
    done, failed = [], []
    for p in pids:
        if p == os.getpid() and not confirm_dangerous:
            failed.append("%d (this server; repeat with confirm_dangerous=true)" % p)
            continue
        try:
            os.kill(p, sig)
            done.append(p)
        except ProcessLookupError:
            failed.append("%d (no such process)" % p)
        except PermissionError:
            failed.append("%d (permission denied: use sudo=true)" % p)
    text = "Sent %s to %s" % (signal, ", ".join(map(str, done)) or "nothing")
    if failed:
        text += "\nNot sent: " + "; ".join(failed)
    return (fail if failed and not done else ok)(text)


def _hex_addr(h: str) -> str:
    raw = bytes.fromhex(h)
    if len(raw) == 4:
        return socket.inet_ntoa(raw[::-1])
    return socket.inet_ntop(socket.AF_INET6, b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))


def _socket_owners() -> Dict[str, str]:
    """inode -> "pid/program" for every socket we are allowed to see (root sees all)."""
    owners = {}  # type: Dict[str, str]
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return owners
    for pid in pids:
        try:
            fds = os.listdir("/proc/%s/fd" % pid)
        except OSError:
            continue
        name = ""
        for fd in fds:
            try:
                link = os.readlink("/proc/%s/fd/%s" % (pid, fd))
            except OSError:
                continue
            if link.startswith("socket:["):
                if not name:
                    try:
                        with open("/proc/%s/comm" % pid) as f:
                            name = f.read().strip()
                    except OSError:
                        name = "?"
                owners[link[8:-1]] = "%s/%s" % (pid, name)
    return owners


def proc_sockets(established: bool = False) -> str:
    """Listening (or established) sockets read straight from /proc/net, for machines without iproute2."""
    owners = _socket_owners()
    rows = ["%-5s %-26s %-26s %s" % ("PROTO", "LOCAL", "REMOTE", "PROCESS")]
    for proto in ("tcp", "tcp6") if established else ("tcp", "tcp6", "udp", "udp6"):
        try:
            with open("/proc/net/" + proto) as f:
                lines = f.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 10:
                continue
            state = f[3]
            if proto.startswith("tcp") and state != ("01" if established else "0A"):
                continue
            if proto.startswith("udp") and established:
                continue
            la, lp = f[1].split(":")
            ra, rp = f[2].split(":")
            local = "%s:%d" % (_hex_addr(la), int(lp, 16))
            remote = "%s:%d" % (_hex_addr(ra), int(rp, 16)) if established else "*"
            rows.append("%-5s %-26s %-26s %s" % (proto, local, remote, owners.get(f[9], "")))
    return "\n".join(rows)


def proc_interfaces() -> str:
    import fcntl
    import struct
    rows = ["%-12s %-8s %-18s %s" % ("NAME", "STATE", "MAC", "IPv4")]
    for _, name in socket.if_nameindex():
        def sysfs(key: str) -> str:
            try:
                with open("/sys/class/net/%s/%s" % (name, key)) as f:
                    return f.read().strip()
            except OSError:
                return "?"
        ip4 = "-"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                ip4 = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))[20:24])
        except OSError:
            pass
        rows.append("%-12s %-8s %-18s %s" % (name, sysfs("operstate"), sysfs("address"), ip4))
    return "\n".join(rows)


def proc_routes() -> str:
    rows = []
    try:
        with open("/proc/net/route") as f:
            for line in f.read().splitlines()[1:]:
                p = line.split()
                if len(p) > 7:
                    dest = socket.inet_ntoa(bytes.fromhex(p[1])[::-1])
                    gw = socket.inet_ntoa(bytes.fromhex(p[2])[::-1])
                    mask = socket.inet_ntoa(bytes.fromhex(p[7])[::-1])
                    rows.append("%s via %s dev %s mask %s" % ("default" if dest == "0.0.0.0" else dest, gw, p[0], mask))
    except OSError:
        pass
    return "\n".join(rows) or "(no routes)"


@register(
    "system_network", "system",
    "Network state: listening ports with the program behind them, open connections, interfaces and addresses, routes, "
    "DNS settings or the firewall rules.",
    {"what": S("string", "listening (default), connections, interfaces, routes, dns or firewall.",
               enum=["listening", "connections", "interfaces", "routes", "dns", "firewall"])},
    title="Network state", read_only=True, idempotent=True, elevatable=True,
)
def system_network(ctx: Ctx, what: str = "listening"):
    def run(argv: List[str]) -> str:
        exe = proc.which(argv[0])
        if not exe:
            return "(%s is not installed)" % argv[0]
        r = proc.run([exe] + argv[1:], timeout=20)
        return (r["stdout"] + r["stderr"]).strip()

    if what == "listening":
        return ok(run(["ss", "-tulpn"]) if proc.which("ss") else proc_sockets())
    if what == "connections":
        return ok(clip(run(["ss", "-tnp", "state", "established"]) if proc.which("ss") else proc_sockets(True), 30000))
    if what == "interfaces":
        if not proc.which("ip"):
            return ok(proc_interfaces())
        return ok(run(["ip", "-br", "addr"]) + "\n\n" + run(["ip", "-br", "link"]))
    if what == "routes":
        if not proc.which("ip"):
            return ok(proc_routes())
        return ok(run(["ip", "route"]) + "\n" + run(["ip", "-6", "route"]))
    if what == "dns":
        try:
            with open("/etc/resolv.conf") as f:
                text = f.read()
        except OSError:
            text = "(no /etc/resolv.conf)"
        extra = run(["resolvectl", "status"]) if proc.which("resolvectl") else ""
        return ok(text.strip() + ("\n\n" + clip(extra, 4000) if extra else ""))
    parts = []
    for argv in (["ufw", "status", "verbose"], ["firewall-cmd", "--list-all"], ["nft", "list", "ruleset"], ["iptables", "-S"]):
        if proc.which(argv[0]):
            parts.append("$ " + " ".join(argv) + "\n" + clip(run(argv), 8000))
    return ok("\n\n".join(parts) or "No firewall tool found (ufw, firewalld, nft, iptables).")


@register(
    "system_logs", "system",
    "Read logs: the systemd journal (by unit, time, priority, text) or the end of any log file.",
    {
        "unit": S("string", "systemd unit, e.g. nginx or sshd."),
        "file": S("string", "Read the end of this log file instead of the journal."),
        "lines": S("integer", "How many lines (default 100).", minimum=1, maximum=5000),
        "since": S("string", "Start time: '1 hour ago', 'yesterday', '2026-01-31 10:00'."),
        "until": S("string", "End time."),
        "priority": S("string", "Only this priority or worse: emerg, alert, crit, err, warning, notice, info, debug."),
        "grep": S("string", "Only lines matching this regular expression (case-insensitive)."),
        "boot": S("boolean", "Only the current boot."),
        "kernel": S("boolean", "Kernel messages (dmesg)."),
    },
    title="Read logs", read_only=True, idempotent=True, elevatable=True,
)
def system_logs(ctx: Ctx, unit: Optional[str] = None, file: Optional[str] = None, lines: int = 100, since: Optional[str] = None,
                until: Optional[str] = None, priority: Optional[str] = None, grep: Optional[str] = None, boot: bool = False,
                kernel: bool = False):
    rx = None
    if grep:
        try:
            rx = re.compile(grep, re.I)
        except re.error as e:
            raise ToolError("bad regular expression: %s" % e)
    if file:
        from .files import resolve
        p = resolve(file)
        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 2000000))
                text = f.read().decode("utf-8", "replace").splitlines()
        except FileNotFoundError:
            raise ToolError("%s does not exist." % p)
        except PermissionError:
            raise ToolError("Permission denied for %s. Retry with sudo=true." % p)
        if rx:
            text = [l for l in text if rx.search(l)]
        return ok("%s (last %d lines)\n%s" % (p, min(lines, len(text)), clip("\n".join(text[-lines:]), 200000)))
    jc = proc.which("journalctl")
    if not jc:
        raise ToolError("journalctl is not available (no systemd). Use file= to read a log file.")
    argv = [jc, "--no-pager", "-o", "short-iso", "-n", str(5000 if rx else lines)]
    if unit:
        argv += ["-u", unit]
    if since:
        argv += ["--since", since]
    if until:
        argv += ["--until", until]
    if priority:
        argv += ["-p", priority]
    if boot:
        argv.append("-b")
    if kernel:
        argv.append("-k")
    r = proc.run(argv, timeout=60, cancel=ctx.cancel, max_output=3000000)
    if r["exit_code"] != 0 and not r["stdout"]:
        raise ToolError(r["stderr"].strip() or "journalctl failed")
    text = r["stdout"].splitlines()
    if rx:
        text = [l for l in text if rx.search(l)]
    note = ""
    if "not seeing messages from other users" in r["stderr"] or "No journal files were opened" in r["stderr"]:
        note = "\n[hint] Only this user's journal is readable: retry with sudo=true."
    return ok(clip("\n".join(text[-lines:]), 200000) + note if text else "(no log lines)" + note)
