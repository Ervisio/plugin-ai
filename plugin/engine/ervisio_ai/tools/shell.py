"""shell_exec and background jobs."""
import json
import os
import signal as signals
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, Optional

from .. import paths, policy, proc
from ..registry import Ctx, S, ToolError, fail, ok, register
from ..util import now_iso

CONFIRM = S("boolean", "Set to true only to repeat a command the safety guard refused, when the user really wants it.")


def expand(path: Optional[str]) -> Optional[str]:
    return os.path.expanduser(path) if path else None


def render(res: Dict[str, Any], cwd: Optional[str]) -> str:
    head = "exit %s in %ss" % (res["exit_code"], res["duration"])
    if res.get("timed_out"):
        head += " (TIMED OUT, process killed)"
    if res.get("cancelled"):
        head += " (cancelled)"
    if cwd:
        head += "  cwd=%s" % cwd
    parts = [head]
    out, err = res.get("stdout", ""), res.get("stderr", "")
    if out:
        parts.append(out.rstrip("\n"))
    if err:
        parts.append("[stderr]\n" + err.rstrip("\n"))
    if not out and not err:
        parts.append("(no output)")
    if res.get("saved_output"):
        parts.append("[output was cut; the full text is in %s]" % ", ".join(sorted(res["saved_output"].values())))
    hint = proc.sudo_hint(err)
    if hint:
        parts.append("[hint] " + hint)
    return "\n".join(parts)


@register(
    "shell_exec", "shell",
    "Run a shell command on this machine and wait for it to finish (bash -c, no terminal, no stdin). Returns the exit "
    "code, stdout and stderr. For anything that can take more than a few minutes use job_start; for interactive "
    "programs use terminal_open.",
    {
        "command": S("string", "The command line, run by bash. Pipes, redirects, && and quoting all work."),
        "cwd": S("string", "Working directory (default: the home directory of the server user)."),
        "timeout": S("integer", "Seconds before the command is killed (default 120).", minimum=1, maximum=86400),
        "sudo": S("boolean", "Run as root (not needed when the server already runs as root)."),
        "user": S("string", "Run as this other user."),
        "env": S("object", "Extra environment variables, as an object of strings."),
        "stdin": S("string", "Text to feed to the command's standard input."),
        "max_output": S("integer", "Characters of output to return before cutting the middle (default 100000).", minimum=1000, maximum=2000000),
        "confirm_dangerous": CONFIRM,
    },
    ["command"], title="Run a shell command", destructive=True, open_world=True,
)
def shell_exec(ctx: Ctx, command: str, cwd: Optional[str] = None, timeout: int = 120, sudo: bool = False,
               user: Optional[str] = None, env: Optional[Dict[str, Any]] = None, stdin: Optional[str] = None,
               max_output: int = 100000, confirm_dangerous: bool = False):
    policy.guard_command(ctx.policy, command, {"confirm_dangerous": confirm_dangerous})
    cwd = expand(cwd)
    if cwd and not os.path.isdir(cwd):
        raise ToolError("cwd %s is not a directory." % cwd)
    timeout = min(timeout, ctx.policy["max_timeout_sec"])
    argv = proc.elevate(proc.shell_argv(command), sudo, user)
    res = proc.run(argv, cwd=cwd, env=env, timeout=timeout, stdin=stdin, cancel=ctx.cancel, max_output=max_output,
                   on_output=lambda stream, text: ctx.progress(text.rstrip("\n")[-500:]))
    res["root"] = bool(sudo) or os.geteuid() == 0
    text = render(res, cwd)
    bad = res["exit_code"] != 0 or res.get("timed_out") or res.get("cancelled")
    return (fail if bad else ok)(text, {k: res[k] for k in ("exit_code", "duration", "root") if k in res})


# --- background jobs -------------------------------------------------------------------------------------------

def _jobs_dir() -> str:
    return paths.sub("jobs", create=True)


def _job_path(job_id: str) -> str:
    if not job_id or not all(c in "0123456789abcdef" for c in job_id) or len(job_id) > 16:
        raise ToolError("%r is not a job id (use job_list)." % job_id)
    return os.path.join(_jobs_dir(), job_id)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie is not alive
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _status(path: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(path, "meta.json")) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        raise ToolError("job %s has no metadata" % os.path.basename(path))
    exit_file = os.path.join(path, "exit")
    meta["id"] = os.path.basename(path)
    if os.path.exists(exit_file):
        try:
            with open(exit_file) as f:
                meta["exit_code"] = int(f.read().strip() or "-1")
        except ValueError:
            meta["exit_code"] = -1
        meta["state"] = "exited"
    elif _alive(int(meta.get("pid", 0))):
        meta["state"] = "running"
    else:
        meta["state"] = "lost"  # the process is gone and wrote no exit code (killed hard, machine rebooted)
    try:
        meta["output_bytes"] = os.path.getsize(os.path.join(path, "out.log"))
    except OSError:
        meta["output_bytes"] = 0
    return meta


@register(
    "job_start", "shell",
    "Start a long-running command in the background and return at once with a job id. The job keeps running if this "
    "connection drops or the server restarts. Read its output with job_output, stop it with job_kill.",
    {
        "command": S("string", "The command line, run by bash."),
        "name": S("string", "A short label so you can recognise the job later."),
        "cwd": S("string", "Working directory."),
        "sudo": S("boolean", "Run as root."),
        "user": S("string", "Run as this other user."),
        "env": S("object", "Extra environment variables."),
        "confirm_dangerous": CONFIRM,
    },
    ["command"], title="Start a background job", destructive=True, open_world=True,
)
def job_start(ctx: Ctx, command: str, name: str = "", cwd: Optional[str] = None, sudo: bool = False,
              user: Optional[str] = None, env: Optional[Dict[str, Any]] = None, confirm_dangerous: bool = False):
    policy.guard_command(ctx.policy, command, {"confirm_dangerous": confirm_dangerous})
    cwd = expand(cwd)
    if cwd and not os.path.isdir(cwd):
        raise ToolError("cwd %s is not a directory." % cwd)
    job_id = uuid.uuid4().hex[:8]
    path = os.path.join(_jobs_dir(), job_id)
    os.makedirs(path, mode=0o700)
    exit_file = os.path.join(path, "exit")
    wrapper = '( eval "$1" ); code=$?; echo $code > "$2"; exit $code'
    argv = proc.elevate([proc.shell_path(), "-c", wrapper, "ervisio-ai-job", command, exit_file], sudo, user)
    out = open(os.path.join(path, "out.log"), "ab")
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, cwd=cwd,
                             env=proc.base_env(env), start_new_session=True)
    except OSError as e:
        out.close()
        raise ToolError("could not start the job: %s" % e)
    out.close()
    threading.Thread(target=p.wait, daemon=True).start()  # reap it if this process outlives the job
    meta = {"name": name or command[:40], "command": command, "cwd": cwd, "pid": p.pid, "started": now_iso(),
            "root": bool(sudo) or os.geteuid() == 0, "client": ctx.client}
    with open(os.path.join(path, "meta.json"), "w") as f:
        json.dump(meta, f)
    return ok("Started job %s (pid %d). Read it with job_output id=%s." % (job_id, p.pid, job_id), {"id": job_id, "pid": p.pid})


@register("job_list", "shell", "List background jobs started with job_start, newest first, with their state.",
          {"limit": S("integer", "How many jobs to list (default 20).", minimum=1, maximum=200)},
          title="List background jobs", read_only=True)
def job_list(ctx: Ctx, limit: int = 20):
    root = _jobs_dir()
    jobs = []  # type: List[Dict[str, Any]]
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            st = _status(path)
        except ToolError:
            continue
        if st["state"] != "running" and time.time() - os.path.getmtime(path) > 7 * 86400:
            continue
        jobs.append(st)
    jobs.sort(key=lambda j: j.get("started", ""), reverse=True)
    jobs = jobs[:limit]
    if not jobs:
        return ok("No jobs.", {"jobs": []})
    lines = ["%-9s %-8s %-6s %-20s %s" % ("ID", "STATE", "EXIT", "STARTED", "NAME")]
    for j in jobs:
        lines.append("%-9s %-8s %-6s %-20s %s" % (j["id"], j["state"], j.get("exit_code", "-"), j.get("started", ""), j.get("name", "")))
    return ok("\n".join(lines), {"jobs": jobs})


@register(
    "job_output", "shell",
    "Read the output (stdout and stderr together) of a background job. By default the last 200 lines; pass offset "
    "(the next_offset of the previous call) to read only what is new.",
    {
        "id": S("string", "Job id from job_start or job_list."),
        "tail": S("integer", "Number of last lines to return (default 200).", minimum=1, maximum=20000),
        "offset": S("integer", "Byte offset to read from instead of the tail.", minimum=0),
        "wait": S("integer", "Seconds to wait for the job to finish or new output to appear (max 120).", minimum=0, maximum=120),
    },
    ["id"], title="Read job output", read_only=True,
)
def job_output(ctx: Ctx, id: str, tail: int = 200, offset: Optional[int] = None, wait: int = 0):
    path = _job_path(id)
    st = _status(path)
    log = os.path.join(path, "out.log")
    deadline = time.time() + wait
    while wait and st["state"] == "running" and time.time() < deadline and not ctx.cancel.is_set():
        if offset is not None and os.path.getsize(log) > offset:
            break
        time.sleep(0.5)
        st = _status(path)
    size = os.path.getsize(log)
    with open(log, "rb") as f:
        if offset is not None:
            f.seek(min(offset, size))
            data = f.read(400000)
            text = data.decode("utf-8", "replace")
            nxt = min(offset, size) + len(data)
        else:
            f.seek(max(0, size - 400000))
            text = "\n".join(f.read().decode("utf-8", "replace").splitlines()[-tail:])
            nxt = size
    head = "job %s %s" % (id, st["state"])
    if "exit_code" in st:
        head += " (exit %s)" % st["exit_code"]
    head += " next_offset=%d" % nxt
    return ok(head + "\n" + (text if text else "(no output yet)"), {"state": st["state"], "exit_code": st.get("exit_code"), "next_offset": nxt})


@register("job_kill", "shell", "Stop a background job (its whole process group). SIGTERM first; use signal KILL if it ignores it.",
          {"id": S("string", "Job id."), "signal": S("string", "TERM (default), KILL, INT or HUP.", enum=["TERM", "KILL", "INT", "HUP"])},
          ["id"], title="Stop a background job", destructive=True)
def job_kill(ctx: Ctx, id: str, signal: str = "TERM"):  # noqa: A002
    st = _status(_job_path(id))
    if st["state"] != "running":
        return ok("Job %s is not running (%s)." % (id, st["state"]))
    sig = getattr(signals, "SIG" + signal)
    try:
        os.killpg(int(st["pid"]), sig)
    except ProcessLookupError:
        return ok("Job %s had already ended." % id)
    except PermissionError:
        if os.geteuid() != 0:
            r = proc.run(["sudo", "-n", "kill", "-%s" % signal, "-%d" % int(st["pid"])], timeout=10)
            if r["exit_code"] == 0:
                return ok("Sent %s to job %s." % (signal, id))
        raise ToolError("not allowed to signal job %s (it runs as another user)" % id)
    return ok("Sent %s to job %s." % (signal, id))
