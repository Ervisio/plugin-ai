"""Files: read, write, edit, list, search, find, stat, manage, archive, diff."""
import base64
import difflib
import fnmatch
import grp
import hashlib
import os
import pwd
import re
import shutil
import stat as stat_mod
import tarfile
import tempfile
import time
import zipfile
from typing import Any, Dict, List, Optional

from .. import paths, policy, proc
from ..registry import Ctx, S, ToolError, ok, register
from ..util import clip, human_bytes
from .shell import CONFIRM

PSEUDO = ("/proc", "/sys", "/dev", "/run")


def resolve(path: str) -> str:
    """Absolute path; ``~`` is the home directory and a relative path starts there too."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(paths.home_of_user(), path)
    return os.path.normpath(path)


def _owner(st: os.stat_result) -> str:
    try:
        user = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        user = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return "%s:%s" % (user, group)


def _kind(st: os.stat_result) -> str:
    m = st.st_mode
    if stat_mod.S_ISDIR(m):
        return "d"
    if stat_mod.S_ISLNK(m):
        return "l"
    if stat_mod.S_ISREG(m):
        return "-"
    return "o"


def _perm(st: os.stat_result) -> str:
    return stat_mod.filemode(st.st_mode)


def _is_binary(chunk: bytes) -> bool:
    return b"\0" in chunk


def _need(path: str) -> os.stat_result:
    try:
        return os.stat(path)
    except FileNotFoundError:
        raise ToolError("%s does not exist." % path)
    except PermissionError:
        raise ToolError("Permission denied for %s. Retry with sudo=true." % path)
    except OSError as e:
        raise ToolError("%s: %s" % (path, e.strerror))


# --- read ------------------------------------------------------------------------------------------------------

@register(
    "fs_read", "files",
    "Read a text file with line numbers. Long files come in pages: pass offset (first line, 1-based) and limit. For a "
    "binary file use encoding=base64 (offset and max_bytes are then in bytes). A directory is listed instead.",
    {
        "path": S("string", "File path (absolute, ~/..., or relative to the home directory)."),
        "offset": S("integer", "First line to return, 1-based (text) or byte offset (base64). Default 1 / 0.", minimum=0),
        "limit": S("integer", "Maximum number of lines (default 2000).", minimum=1, maximum=100000),
        "encoding": S("string", "text (default) or base64.", enum=["text", "base64"]),
        "max_bytes": S("integer", "base64 only: bytes to return (default 1 MiB, max 8 MiB).", minimum=1, maximum=8388608),
    },
    ["path"], title="Read a file", read_only=True, idempotent=True, elevatable=True,
)
def fs_read(ctx: Ctx, path: str, offset: Optional[int] = None, limit: int = 2000, encoding: str = "text", max_bytes: int = 1048576):
    p = resolve(path)
    st = _need(p)
    if stat_mod.S_ISDIR(st.st_mode):
        return fs_list(ctx, p)
    if not stat_mod.S_ISREG(st.st_mode):
        raise ToolError("%s is not a regular file (%s)." % (p, _perm(st)))
    if encoding == "base64":
        start = offset or 0
        with open(p, "rb") as f:
            f.seek(start)
            data = f.read(max_bytes)
        more = start + len(data) < st.st_size
        head = "%s: bytes %d-%d of %d (base64)%s" % (p, start, start + len(data), st.st_size, " - more: offset=%d" % (start + len(data)) if more else "")
        return ok(head + "\n" + base64.b64encode(data).decode())
    start_line = max(offset or 1, 1)
    out = []  # type: List[str]
    total_chars = 0
    last = start_line - 1
    truncated = False
    with open(p, "rb") as f:
        first = f.read(8192)
        if _is_binary(first):
            raise ToolError("%s looks like a binary file (%s). Use encoding=base64, or shell tools such as 'file' and 'xxd'." % (p, human_bytes(st.st_size)))
        f.seek(0)
        for n, raw in enumerate(f, 1):
            if n < start_line:
                continue
            if len(out) >= limit or total_chars > 200000:
                truncated = True
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if len(line) > 2000:
                line = line[:2000] + "… (line cut)"
            out.append("%6d\t%s" % (n, line))
            total_chars += len(line)
            last = n
    head = "%s (%s, %s)" % (p, human_bytes(st.st_size), time.strftime("modified %Y-%m-%d %H:%M", time.localtime(st.st_mtime)))
    if not out:
        return ok(head + "\n(empty or offset past the end)")
    if truncated:
        head += " - lines %d-%d shown, more: offset=%d" % (start_line, last, last + 1)
    return ok(head + "\n" + "\n".join(out), {"path": p, "size": st.st_size})


# --- write -----------------------------------------------------------------------------------------------------

def _parse_mode(mode: Optional[str]) -> Optional[int]:
    if mode is None:
        return None
    try:
        return int(str(mode), 8)
    except ValueError:
        raise ToolError("mode must be octal like 0644 or 755, got %r" % mode)


def _chown_like(ref: os.stat_result, target: str) -> None:
    if os.geteuid() == 0:
        try:
            os.chown(target, ref.st_uid, ref.st_gid)
        except OSError:
            pass


def atomic_write(path: str, data: bytes, mode: Optional[int] = None, like: Optional[os.stat_result] = None) -> None:
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".eai-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        elif like is not None:
            os.chmod(tmp, stat_mod.S_IMODE(like.st_mode))
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        if like is not None:
            _chown_like(like, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: str) -> str:
    dest = "%s.bak.%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dest)
    return dest


@register(
    "fs_write", "files",
    "Create or overwrite a file with the given content (atomic: the old file is replaced only when the new one is "
    "complete; permissions and owner of an existing file are kept). For a small change to an existing file use fs_edit.",
    {
        "path": S("string", "File path."),
        "content": S("string", "The full content (text, or base64 when encoding=base64)."),
        "encoding": S("string", "text (default) or base64.", enum=["text", "base64"]),
        "mode": S("string", "Permissions in octal, e.g. 0644 or 0600. Default: keep the old ones, or the umask for a new file."),
        "append": S("boolean", "Add to the end of the file instead of replacing it."),
        "create_dirs": S("boolean", "Create missing parent folders (default true)."),
        "backup": S("boolean", "Copy the old file to <path>.bak.<time> first."),
    },
    ["path", "content"], title="Write a file", destructive=True, idempotent=True, elevatable=True,
)
def fs_write(ctx: Ctx, path: str, content: str, encoding: str = "text", mode: Optional[str] = None, append: bool = False,
             create_dirs: bool = True, backup: bool = False):
    p = resolve(path)
    data = base64.b64decode(content) if encoding == "base64" else content.encode("utf-8")
    like = None
    existed = os.path.lexists(p)
    if existed:
        p = os.path.realpath(p)  # write through a symlink instead of replacing it
        if os.path.isdir(p):
            raise ToolError("%s is a directory." % p)
        like = os.stat(p)
    folder = os.path.dirname(p)
    if not os.path.isdir(folder):
        if not create_dirs:
            raise ToolError("The folder %s does not exist." % folder)
        os.makedirs(folder, exist_ok=True)
    note = ""
    if backup and existed:
        note = " (old file saved as %s)" % _backup(p)
    if append and existed:
        with open(p, "rb") as f:
            data = f.read() + data
    try:
        atomic_write(p, data, _parse_mode(mode), like)
    except PermissionError:
        raise ToolError("Permission denied for %s. Retry with sudo=true." % p)
    return ok("%s %s: %s%s" % ("Appended to" if append and existed else "Overwrote" if existed else "Created", p, human_bytes(len(data)), note),
              {"path": p, "bytes": len(data)})


# --- edit ------------------------------------------------------------------------------------------------------

def _find_hint(text: str, old: str) -> str:
    first = next((l for l in old.splitlines() if l.strip()), "")
    if not first:
        return ""
    lines = text.splitlines()
    close = difflib.get_close_matches(first.strip(), [l.strip() for l in lines], n=1, cutoff=0.6)
    if not close:
        return ""
    for i, l in enumerate(lines, 1):
        if l.strip() == close[0]:
            return " The closest line is %d: %r. Check whitespace and indentation." % (i, clip(l, 120))
    return ""


def _apply_edits(path: str, text: str, edits: List[Dict[str, Any]]) -> str:
    for i, e in enumerate(edits, 1):
        old, new = e.get("old_string"), e.get("new_string")
        label = "edit %d: " % i if len(edits) > 1 else ""
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError(label + "old_string and new_string are required strings.")
        if old == "":
            raise ToolError(label + "old_string is empty. To create or replace a whole file use fs_write.")
        if old == new:
            raise ToolError(label + "old_string and new_string are identical.")
        n = text.count(old)
        if n == 0:
            raise ToolError("%s%s: old_string was not found.%s" % (label, path, _find_hint(text, old)))
        if n > 1 and not e.get("replace_all"):
            where = [text.count("\n", 0, m.start()) + 1 for m in re.finditer(re.escape(old), text)][:8]
            raise ToolError("%sold_string occurs %d times (lines %s). Add surrounding lines to make it unique, or set replace_all=true." %
                            (label, n, ", ".join(map(str, where))))
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


@register(
    "fs_edit", "files",
    "Change a text file by replacing exact text: old_string must match the file exactly (whitespace included) and be "
    "unique unless replace_all is set. Give several edits at once with edits; they apply in order and all-or-nothing. "
    "Returns the diff. Read the file first.",
    {
        "path": S("string", "File to change."),
        "old_string": S("string", "Exact text to replace."),
        "new_string": S("string", "Replacement text."),
        "replace_all": S("boolean", "Replace every occurrence."),
        "edits": S("array", "Several edits: objects with old_string, new_string, replace_all.",
                   items={"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"},
                                                          "replace_all": {"type": "boolean"}}}),
        "backup": S("boolean", "Copy the file to <path>.bak.<time> first."),
    },
    ["path"], title="Edit a file", destructive=True, elevatable=True,
)
def fs_edit(ctx: Ctx, path: str, old_string: Optional[str] = None, new_string: Optional[str] = None, replace_all: bool = False,
            edits: Optional[List[Dict[str, Any]]] = None, backup: bool = False):
    p = os.path.realpath(resolve(path))
    st = _need(p)
    if not stat_mod.S_ISREG(st.st_mode):
        raise ToolError("%s is not a regular file." % p)
    todo = list(edits or [])
    if old_string is not None:
        todo.insert(0, {"old_string": old_string, "new_string": new_string if new_string is not None else "", "replace_all": replace_all})
    if not todo:
        raise ToolError("Give old_string and new_string, or edits.")
    with open(p, "rb") as f:
        raw = f.read()
    if _is_binary(raw[:8192]):
        raise ToolError("%s is a binary file." % p)
    text = raw.decode("utf-8", "replace")
    crlf = "\r\n" in text
    work = text.replace("\r\n", "\n") if crlf else text
    updated = _apply_edits(p, work, [dict(e, old_string=str(e.get("old_string", "")).replace("\r\n", "\n"),
                                          new_string=str(e.get("new_string", "")).replace("\r\n", "\n")) if crlf else e for e in todo])
    final = updated.replace("\n", "\r\n") if crlf else updated
    note = ""
    if backup:
        note = "\nBackup: " + _backup(p)
    try:
        atomic_write(p, final.encode("utf-8"), None, st)
    except PermissionError:
        raise ToolError("Permission denied for %s. Retry with sudo=true." % p)
    diff = "".join(difflib.unified_diff(work.splitlines(True), updated.splitlines(True), "a/" + os.path.basename(p), "b/" + os.path.basename(p), n=2))
    return ok("Edited %s (%d edit%s)%s\n%s" % (p, len(todo), "" if len(todo) == 1 else "s", note, clip(diff, 6000)), {"path": p})


# --- list / find / stat ----------------------------------------------------------------------------------------

def _row(path: str, name: str, st: os.stat_result) -> str:
    link = ""
    if stat_mod.S_ISLNK(st.st_mode):
        try:
            link = " -> " + os.readlink(path)
        except OSError:
            pass
    suffix = "/" if stat_mod.S_ISDIR(st.st_mode) else ""
    return "%s %-16s %9s  %s  %s%s%s" % (_perm(st), _owner(st), human_bytes(st.st_size) if not stat_mod.S_ISDIR(st.st_mode) else "-",
                                         time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), name, suffix, link)


@register(
    "fs_list", "files",
    "List a folder: permissions, owner, size, modification time. Optionally recursive, filtered by a glob on the name.",
    {
        "path": S("string", "Folder (default: home directory)."),
        "depth": S("integer", "How many levels to descend (default 1 = just this folder).", minimum=1, maximum=20),
        "glob": S("string", "Only names matching this pattern, e.g. '*.conf'."),
        "hidden": S("boolean", "Include names starting with a dot."),
        "sort": S("string", "name (default), size or mtime.", enum=["name", "size", "mtime"]),
        "limit": S("integer", "Maximum entries (default 300).", minimum=1, maximum=5000),
    },
    title="List a folder", read_only=True, idempotent=True, elevatable=True,
)
def fs_list(ctx: Ctx, path: str = "~", depth: int = 1, glob: Optional[str] = None, hidden: bool = False, sort: str = "name", limit: int = 300):
    root = resolve(path)
    st = _need(root)
    if not stat_mod.S_ISDIR(st.st_mode):
        return ok(_row(root, os.path.basename(root), st))
    entries = []  # type: List[Any]

    def walk(d: str, level: int) -> None:
        try:
            with os.scandir(d) as it:
                items = sorted(it, key=lambda e: e.name)
        except PermissionError:
            entries.append((d, "(permission denied)", None))
            return
        for e in items:
            if not hidden and e.name.startswith("."):
                continue
            rel = os.path.relpath(e.path, root)
            try:
                est = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if not glob or fnmatch.fnmatch(e.name, glob):
                entries.append((e.path, rel, est))
            if level < depth and stat_mod.S_ISDIR(est.st_mode) and not e.path.startswith(PSEUDO):
                walk(e.path, level + 1)

    walk(root, 1)
    if sort == "size":
        entries.sort(key=lambda t: -(t[2].st_size if t[2] else 0))
    elif sort == "mtime":
        entries.sort(key=lambda t: -(t[2].st_mtime if t[2] else 0))
    shown = entries[:limit]
    lines = ["%s (%d entries%s)" % (root, len(entries), ", first %d shown" % limit if len(entries) > limit else "")]
    for full, rel, est in shown:
        lines.append(_row(full, rel, est) if est else "%s %s" % ("?", rel))
    return ok("\n".join(lines), {"path": root, "count": len(entries)})


@register(
    "fs_find", "files",
    "Find files and folders by name, type, size or age, anywhere on the machine.",
    {
        "path": S("string", "Where to search (default: /)."),
        "name": S("string", "Glob on the file name, case-insensitive, e.g. '*.log' or 'nginx*'."),
        "type": S("string", "f = files, d = folders, l = symlinks.", enum=["f", "d", "l"]),
        "min_size": S("integer", "Minimum size in bytes.", minimum=0),
        "max_size": S("integer", "Maximum size in bytes.", minimum=0),
        "modified_within_minutes": S("integer", "Changed in the last N minutes.", minimum=1),
        "older_than_days": S("integer", "Not changed for N days.", minimum=1),
        "max_depth": S("integer", "Maximum depth.", minimum=1, maximum=64),
        "limit": S("integer", "Maximum results (default 200).", minimum=1, maximum=5000),
    },
    title="Find files", read_only=True, idempotent=True, elevatable=True,
)
def fs_find(ctx: Ctx, path: str = "/", name: Optional[str] = None, type: Optional[str] = None, min_size: Optional[int] = None,  # noqa: A002
            max_size: Optional[int] = None, modified_within_minutes: Optional[int] = None, older_than_days: Optional[int] = None,
            max_depth: Optional[int] = None, limit: int = 200):
    root = resolve(path)
    _need(root)
    pat = name.lower() if name else None
    now = time.time()
    found = []  # type: List[str]
    scanned = 0
    for d, dirs, files in os.walk(root, followlinks=False):
        if root == "/" or not root.startswith(PSEUDO):
            dirs[:] = [x for x in dirs if os.path.join(d, x) not in PSEUDO]
        depth = d[len(root):].count(os.sep) + 1 if d != root else 1
        if max_depth is not None and depth >= max_depth:
            dirs[:] = []
        names = (dirs if type in (None, "d") else []) + (files if type != "d" else [])
        for n in names:
            scanned += 1
            if pat and not fnmatch.fnmatch(n.lower(), pat):
                continue
            full = os.path.join(d, n)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if type == "f" and not stat_mod.S_ISREG(st.st_mode) or type == "d" and not stat_mod.S_ISDIR(st.st_mode) \
                    or type == "l" and not stat_mod.S_ISLNK(st.st_mode):
                continue
            if min_size is not None and st.st_size < min_size or max_size is not None and st.st_size > max_size:
                continue
            if modified_within_minutes is not None and now - st.st_mtime > modified_within_minutes * 60:
                continue
            if older_than_days is not None and now - st.st_mtime < older_than_days * 86400:
                continue
            found.append("%9s  %s  %s" % (human_bytes(st.st_size), time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), full))
            if len(found) >= limit:
                break
        if len(found) >= limit or ctx.cancel.is_set():
            break
    head = "%d match%s under %s (%d entries scanned)%s" % (len(found), "" if len(found) == 1 else "es", root, scanned,
                                                        " - stopped at the limit" if len(found) >= limit else "")
    return ok(head + ("\n" + "\n".join(found) if found else ""), {"count": len(found)})


@register(
    "fs_stat", "files",
    "Details of one path: type, size, permissions, owner, times, link target, and optionally its SHA-256.",
    {"path": S("string", "Path."), "hash": S("boolean", "Also compute the SHA-256 (files up to 2 GiB).")},
    ["path"], title="Stat a path", read_only=True, idempotent=True, elevatable=True,
)
def fs_stat(ctx: Ctx, path: str, hash: bool = False):  # noqa: A002
    p = resolve(path)
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        raise ToolError("%s does not exist." % p)
    info = {"path": p, "type": {"d": "directory", "l": "symlink", "-": "file"}.get(_kind(st), "other"), "size": st.st_size,
            "mode": oct(stat_mod.S_IMODE(st.st_mode)), "perm": _perm(st), "owner": _owner(st),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "accessed": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_atime)),
            "changed": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_ctime)), "inode": st.st_ino, "links": st.st_nlink}  # type: Dict[str, Any]
    if stat_mod.S_ISLNK(st.st_mode):
        info["target"] = os.readlink(p)
    if hash and stat_mod.S_ISREG(st.st_mode):
        if st.st_size > 2 * 1024 ** 3:
            raise ToolError("The file is larger than 2 GiB; use sha256sum through shell_exec.")
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        info["sha256"] = h.hexdigest()
    file_bin = proc.which("file")
    if file_bin and stat_mod.S_ISREG(st.st_mode):
        r = proc.run([file_bin, "--brief", p], timeout=5)
        if r["exit_code"] == 0:
            info["description"] = r["stdout"].strip()
    return ok("\n".join("%-12s %s" % (k, v) for k, v in info.items()), info)


# --- search ----------------------------------------------------------------------------------------------------

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".mypy_cache", ".tox"}


@register(
    "fs_search", "files",
    "Search file contents (like grep -rn, using ripgrep when installed). Returns path:line:text, with optional "
    "context lines. Skips .git, node_modules and binary files unless include_ignored is set.",
    {
        "pattern": S("string", "What to look for (a regular expression unless fixed_string is set)."),
        "path": S("string", "File or folder to search (default: home directory)."),
        "glob": S("string", "Only files whose name matches, e.g. '*.py'."),
        "fixed_string": S("boolean", "Treat the pattern as plain text."),
        "ignore_case": S("boolean", "Case-insensitive."),
        "context": S("integer", "Lines of context around each match (0-10).", minimum=0, maximum=10),
        "max_results": S("integer", "Stop after this many matching lines (default 100).", minimum=1, maximum=2000),
        "include_ignored": S("boolean", "Also search hidden files, .gitignore'd files and skipped folders."),
    },
    ["pattern"], title="Search file contents", read_only=True, idempotent=True, elevatable=True,
)
def fs_search(ctx: Ctx, pattern: str, path: str = "~", glob: Optional[str] = None, fixed_string: bool = False,
              ignore_case: bool = False, context: int = 0, max_results: int = 100, include_ignored: bool = False):
    root = resolve(path)
    _need(root)
    rg = proc.which("rg")
    lines = []  # type: List[str]
    if rg:
        argv = [rg, "--line-number", "--with-filename", "--no-heading", "--color", "never", "--max-columns", "300",
                "--max-columns-preview", "--max-count", str(max_results)]
        if fixed_string:
            argv.append("--fixed-strings")
        if ignore_case:
            argv.append("--ignore-case")
        if context:
            argv += ["--context", str(context)]
        if include_ignored:
            argv += ["--hidden", "--no-ignore"]
        else:
            for skip in sorted(SKIP_DIRS):
                argv += ["--glob", "!" + skip]
        if glob:
            argv += ["--glob", glob]
        argv += ["-e", pattern, "--", root]
        res = proc.run(argv, timeout=60, cancel=ctx.cancel, max_output=400000)
        if res["exit_code"] == 2 and not res["stdout"]:
            raise ToolError("search failed: %s" % res["stderr"].strip())
        lines = res["stdout"].splitlines()
    else:
        try:
            rx = re.compile(re.escape(pattern) if fixed_string else pattern, re.I if ignore_case else 0)
        except re.error as e:
            raise ToolError("bad regular expression: %s" % e)
        hits = 0
        files = [root] if os.path.isfile(root) else None
        walker = os.walk(root) if files is None else [(os.path.dirname(root), [], [os.path.basename(root)])]
        for d, dirs, names in walker:
            if not include_ignored:
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
            for n in names:
                if glob and not fnmatch.fnmatch(n, glob):
                    continue
                if not include_ignored and n.startswith("."):
                    continue
                full = os.path.join(d, n)
                try:
                    if os.path.getsize(full) > 5 * 1024 * 1024:
                        continue
                    with open(full, "rb") as f:
                        if _is_binary(f.read(4096)):
                            continue
                        f.seek(0)
                        content = f.read().decode("utf-8", "replace").splitlines()
                except OSError:
                    continue
                for i, line in enumerate(content):
                    if rx.search(line):
                        for j in range(max(0, i - context), min(len(content), i + context + 1)):
                            sep = ":" if j == i else "-"
                            lines.append("%s%s%d%s%s" % (full, sep, j + 1, sep, clip(content[j], 300)))
                        hits += 1
                        if hits >= max_results:
                            break
                if hits >= max_results or ctx.cancel.is_set():
                    break
            if hits >= max_results:
                break
    if not lines:
        return ok("No matches for %r under %s." % (pattern, root), {"count": 0})
    text = "\n".join(lines)
    return ok("%d line%s\n%s" % (len(lines), "" if len(lines) == 1 else "s", clip(text, 200000)), {"count": len(lines)})


# --- manage ----------------------------------------------------------------------------------------------------

@register(
    "fs_manage", "files",
    "File operations: mkdir, move, copy, delete, chmod, chown, symlink, touch. Deleting or changing a system "
    "folder is refused by the safety guard unless confirm_dangerous=true.",
    {
        "action": S("string", "What to do.", enum=["mkdir", "move", "copy", "delete", "chmod", "chown", "symlink", "touch"]),
        "path": S("string", "The file or folder the action applies to (for symlink: the link to create)."),
        "dest": S("string", "move/copy: destination. symlink: what the link points to."),
        "recursive": S("boolean", "delete/chmod/chown/copy: include everything inside a folder."),
        "mode": S("string", "chmod: octal permissions, e.g. 0755. mkdir: permissions of the new folder."),
        "owner": S("string", "chown: user, user:group or :group."),
        "overwrite": S("boolean", "move/copy: replace an existing destination."),
        "confirm_dangerous": CONFIRM,
    },
    ["action", "path"], title="File operations", destructive=True, elevatable=True,
)
def fs_manage(ctx: Ctx, action: str, path: str, dest: Optional[str] = None, recursive: bool = False, mode: Optional[str] = None,
              owner: Optional[str] = None, overwrite: bool = False, confirm_dangerous: bool = False):
    p = resolve(path)
    policy.guard_path(ctx.policy, action, p, {"confirm_dangerous": confirm_dangerous})
    try:
        if action == "mkdir":
            os.makedirs(p, mode=_parse_mode(mode) or 0o777, exist_ok=True)
            return ok("Created folder %s" % p)
        if action == "touch":
            with open(p, "a"):
                os.utime(p, None)
            return ok("Touched %s" % p)
        if action in ("move", "copy"):
            if not dest:
                raise ToolError("dest is required for %s." % action)
            d = resolve(dest)
            if not os.path.lexists(p):
                raise ToolError("%s does not exist." % p)
            if os.path.isdir(d):  # like mv and cp: into an existing folder
                d = os.path.join(d, os.path.basename(p.rstrip("/")))
            if os.path.lexists(d):
                if not overwrite:
                    raise ToolError("%s already exists. Set overwrite=true to replace it." % d)
                policy.guard_path(ctx.policy, "delete", d, {"confirm_dangerous": confirm_dangerous})
                shutil.rmtree(d) if os.path.isdir(d) and not os.path.islink(d) else os.unlink(d)
            if action == "move":
                shutil.move(p, d)
                return ok("Moved %s -> %s" % (p, d))
            if os.path.isdir(p):
                if not recursive:
                    raise ToolError("%s is a folder: set recursive=true to copy it." % p)
                shutil.copytree(p, d, symlinks=True)
            else:
                shutil.copy2(p, d)
            return ok("Copied %s -> %s" % (p, d))
        if action == "delete":
            if not os.path.lexists(p):
                raise ToolError("%s does not exist." % p)
            if os.path.isdir(p) and not os.path.islink(p):
                if recursive:
                    shutil.rmtree(p)
                else:
                    os.rmdir(p)
            else:
                os.unlink(p)
            return ok("Deleted %s" % p)
        if action == "chmod":
            m = _parse_mode(mode)
            if m is None:
                raise ToolError("mode is required for chmod.")
            _need(p)
            targets = [p]
            if recursive and os.path.isdir(p):
                for d, dirs, files in os.walk(p):
                    targets += [os.path.join(d, n) for n in dirs + files]
            for t in targets:
                if not os.path.islink(t):
                    os.chmod(t, m)
            return ok("Changed permissions of %d item%s to %s" % (len(targets), "" if len(targets) == 1 else "s", oct(m)))
        if action == "chown":
            if not owner:
                raise ToolError("owner is required for chown.")
            user, _, group = owner.partition(":")
            _need(p)
            targets = [p]
            if recursive and os.path.isdir(p):
                for d, dirs, files in os.walk(p):
                    targets += [os.path.join(d, n) for n in dirs + files]
            for t in targets:
                shutil.chown(t, user or None, group or None)
            return ok("Changed owner of %d item%s to %s" % (len(targets), "" if len(targets) == 1 else "s", owner))
        if action == "symlink":
            if not dest:
                raise ToolError("dest (the link target) is required for symlink.")
            os.symlink(dest, p)
            return ok("Created symlink %s -> %s" % (p, dest))
    except PermissionError:
        raise ToolError("Permission denied. Retry with sudo=true.")
    except (OSError, LookupError, shutil.Error) as e:
        raise ToolError("%s failed: %s" % (action, e))
    raise ToolError("unknown action %s" % action)


# --- archive ---------------------------------------------------------------------------------------------------

def _inside(root: str, target: str) -> bool:
    root = os.path.realpath(root)
    target = os.path.realpath(target)
    return target == root or target.startswith(root.rstrip("/") + "/")


@register(
    "fs_archive", "files",
    "Create, extract or list an archive: .tar, .tar.gz/.tgz, .tar.bz2, .tar.xz or .zip (chosen by the file name). "
    "Extraction refuses paths that escape the destination folder.",
    {
        "action": S("string", "create, extract or list.", enum=["create", "extract", "list"]),
        "archive": S("string", "The archive file."),
        "sources": S("array", "create: files and folders to add.", items={"type": "string"}),
        "dest": S("string", "extract: folder to extract into (default: the archive's folder)."),
    },
    ["action", "archive"], title="Archives", destructive=True, elevatable=True,
)
def fs_archive(ctx: Ctx, action: str, archive: str, sources: Optional[List[str]] = None, dest: Optional[str] = None):
    a = resolve(archive)
    name = a.lower()
    is_zip = name.endswith(".zip")
    try:
        if action == "create":
            if not sources:
                raise ToolError("sources is required to create an archive.")
            srcs = [resolve(s) for s in sources]
            for s in srcs:
                _need(s)
            if is_zip:
                with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
                    for s in srcs:
                        if os.path.isdir(s):
                            for d, _, files in os.walk(s):
                                for n in files:
                                    full = os.path.join(d, n)
                                    z.write(full, os.path.relpath(full, os.path.dirname(s)))
                        else:
                            z.write(s, os.path.basename(s))
            else:
                mode = "w:gz" if name.endswith((".tar.gz", ".tgz")) else "w:bz2" if name.endswith(".tar.bz2") else "w:xz" if name.endswith(".tar.xz") else "w"
                with tarfile.open(a, mode) as t:
                    for s in srcs:
                        t.add(s, arcname=os.path.basename(s.rstrip("/")))
            return ok("Created %s (%s)" % (a, human_bytes(os.path.getsize(a))))
        _need(a)
        if action == "list":
            if is_zip:
                with zipfile.ZipFile(a) as z:
                    names = ["%9s  %s" % (human_bytes(i.file_size), i.filename) for i in z.infolist()]
            else:
                with tarfile.open(a) as t:
                    names = ["%9s  %s" % (human_bytes(m.size), m.name) for m in t.getmembers()]
            return ok("%d entries\n%s" % (len(names), clip("\n".join(names), 100000)))
        out = resolve(dest) if dest else os.path.dirname(a)
        os.makedirs(out, exist_ok=True)
        if is_zip:
            with zipfile.ZipFile(a) as z:
                for i in z.infolist():
                    if not _inside(out, os.path.join(out, i.filename)):
                        raise ToolError("Refusing to extract %s: it escapes the destination." % i.filename)
                z.extractall(out)
                count = len(z.infolist())
        else:
            with tarfile.open(a) as t:
                members = t.getmembers()
                for m in members:
                    if not _inside(out, os.path.join(out, m.name)):
                        raise ToolError("Refusing to extract %s: it escapes the destination." % m.name)
                    if (m.issym() or m.islnk()) and not _inside(out, os.path.join(out, os.path.dirname(m.name), m.linkname)) and m.issym():
                        raise ToolError("Refusing to extract link %s: it points outside the destination." % m.name)
                    if m.isdev():
                        raise ToolError("Refusing to extract device file %s." % m.name)
                t.extractall(out, members=members)
                count = len(members)
        return ok("Extracted %d entries into %s" % (count, out))
    except PermissionError:
        raise ToolError("Permission denied. Retry with sudo=true.")
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as e:
        raise ToolError("%s failed: %s" % (action, e))


# --- diff ------------------------------------------------------------------------------------------------------

@register(
    "fs_diff", "files",
    "Unified diff between two files, or between a file and some text.",
    {"a": S("string", "First file."), "b": S("string", "Second file."), "text_b": S("string", "Compare file a with this text instead of file b.")},
    ["a"], title="Diff files", read_only=True, idempotent=True, elevatable=True,
)
def fs_diff(ctx: Ctx, a: str, b: Optional[str] = None, text_b: Optional[str] = None):
    pa = resolve(a)
    _need(pa)
    with open(pa, "r", encoding="utf-8", errors="replace") as f:
        left = f.read()
    if text_b is not None:
        right, nb = text_b, "text"
    elif b:
        pb = resolve(b)
        _need(pb)
        with open(pb, "r", encoding="utf-8", errors="replace") as f:
            right = f.read()
        nb = pb
    else:
        raise ToolError("Give b (a second file) or text_b.")
    diff = "".join(difflib.unified_diff(left.splitlines(True), right.splitlines(True), pa, nb, n=3))
    return ok(clip(diff, 100000) if diff else "No differences.")
