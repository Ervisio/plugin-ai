"""Docker and Compose, through the docker command line."""
import os
from typing import Any, Dict, List, Optional

from .. import policy, proc
from ..registry import Ctx, S, ToolError, fail, ok, register
from .files import resolve
from .shell import CONFIRM, render

READ_ONLY = {
    "ps", "images", "inspect", "logs", "stats", "info", "version", "top", "port", "diff", "history", "events", "search", "ls",
    "df", "config", "list", "context",
}
READ_ONLY_SUB = {"container", "image", "network", "volume", "system", "compose", "context", "node", "service", "stack", "secret", "config"}
READ_ONLY_VERBS = {"ls", "list", "ps", "inspect", "logs", "config", "df", "images", "top", "events", "port", "version", "info", "stats", "history"}


def _is_read_only(args: Dict[str, Any]) -> bool:
    words = [a for a in args.get("args", []) if isinstance(a, str) and not a.startswith("-")]
    if not words:
        return False
    if words[0] in READ_ONLY_SUB:
        return len(words) > 1 and words[1] in READ_ONLY_VERBS
    return words[0] in READ_ONLY


@register(
    "docker_cli", "docker",
    "Run the docker command line with the arguments you give (no shell). Examples: ['ps','-a'], "
    "['logs','--tail','100','web'], ['compose','up','-d'] (with cwd = the project folder), "
    "['exec','web','nginx','-t'], ['system','prune','-f'].",
    {
        "args": S("array", "The arguments after 'docker', one per item.", items={"type": "string"}),
        "cwd": S("string", "Working directory, e.g. the Compose project folder."),
        "timeout": S("integer", "Seconds before it is killed (default 300).", minimum=1, maximum=86400),
        "stdin": S("string", "Text for standard input."),
        "confirm_dangerous": CONFIRM,
    },
    ["args"], title="Run docker", destructive=True, open_world=True, elevatable=True, read_only_fn=_is_read_only,
)
def docker_cli(ctx: Ctx, args: List[str], cwd: Optional[str] = None, timeout: int = 300, stdin: Optional[str] = None,
               confirm_dangerous: bool = False):
    docker = proc.which("docker")
    if not docker:
        raise ToolError("Docker is not installed on this machine.")
    if not args:
        raise ToolError("args must not be empty.")
    argv = [docker] + args
    if args[0] == "compose":
        check = proc.run([docker, "compose", "version"], timeout=10)
        if check["exit_code"] != 0 and proc.which("docker-compose"):
            argv = [proc.which("docker-compose")] + args[1:]  # type: ignore[list-item]
    if args[:2] in (["system", "prune"], ["volume", "prune"]) and "-a" in args and not confirm_dangerous and ctx.policy["guard"]:
        raise policy.Denied("Safety guard: 'docker %s -a' deletes every unused image or volume. Nothing was run. If the user really wants "
                            "this, repeat the call with confirm_dangerous=true." % " ".join(args[:2]))
    folder = resolve(cwd) if cwd else None
    if folder and not os.path.isdir(folder):
        raise ToolError("cwd %s is not a directory." % folder)
    res = proc.run(argv, cwd=folder, timeout=min(timeout, ctx.policy["max_timeout_sec"]), stdin=stdin, cancel=ctx.cancel,
                   max_output=100000, on_output=lambda s, t: ctx.progress(t.rstrip("\n")[-300:]))
    hint = ""
    if "permission denied" in res.get("stderr", "").lower() and "docker.sock" in res.get("stderr", ""):
        hint = "\n[hint] This user may not use Docker. Retry with sudo=true."
    bad = res["exit_code"] != 0 or res.get("timed_out")
    return (fail if bad else ok)(render(res, folder) + hint, {"exit_code": res["exit_code"]})
