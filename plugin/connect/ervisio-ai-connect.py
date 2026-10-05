#!/usr/bin/env python3
"""Connect the AI tools on THIS computer to an Ervisio AI server.

Run it on your own PC (Windows, macOS or Linux), not on the server. It finds Claude Code, Codex, Cursor, VS Code,
Kilo Code, Windsurf, Antigravity, Gemini CLI, Zed, OpenCode, Claude Desktop, Cline and Roo, and adds the server to
each one you choose. Nothing is changed without a backup, and ``--dry-run`` shows what would happen.

  python ervisio-ai-connect.py                      # uses the settings the Ervisio page put in this file
  python ervisio-ai-connect.py --list               # show the tools it can find
  python ervisio-ai-connect.py --mode ssh --host my-vps --user root
  python ervisio-ai-connect.py --mode http --url https://mcp.example.com/mcp --token eai_...
  python ervisio-ai-connect.py --remove             # take the server out again

Python 3.7 or newer, standard library only.
"""
import argparse
import base64  # noqa: F401  (a preset filled in by the Ervisio page is read with it)
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

VERSION = "0.1.0"

# The Ervisio page fills this in when you download the script from it, so that you only have to run it.
PRESET = None  # __PRESET__

MARK_BEGIN = "# >>> ervisio-ai:%s (managed by ervisio-ai-connect, do not edit) >>>"
MARK_END = "# <<< ervisio-ai:%s <<<"


# --------------------------------------------------------------------------------------------------------------------
# Where things live on this computer

class Env:
    def __init__(self, home=None):
        self.system = platform.system()  # Linux, Darwin, Windows
        self.home = home or os.path.expanduser("~")
        self.appdata = os.environ.get("APPDATA") or os.path.join(self.home, "AppData", "Roaming")

    def app_support(self, app):
        """The per-user data folder of an Electron app such as Code or Cursor."""
        if self.system == "Darwin":
            return os.path.join(self.home, "Library", "Application Support", app)
        if self.system == "Windows":
            return os.path.join(self.appdata, app)
        return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(self.home, ".config"), app)

    def in_path(self, name):
        return shutil.which(name) is not None


EDITORS = ["Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf", "Antigravity", "Kiro", "Trae"]


# --------------------------------------------------------------------------------------------------------------------
# Reading and writing configuration files without breaking them

class ConfigError(Exception):
    pass


def strip_jsonc(text):
    """Remove // and /* */ comments and trailing commas outside strings. Returns (clean text, had_comments)."""
    out, i, n, had = [], 0, len(text), False
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            had = True
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            had = True
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        else:
            out.append(c)
        i += 1
    clean = "".join(out)
    no_commas = re.sub(r",(\s*[}\]])", r"\1", clean)
    return no_commas, had or no_commas != clean


class JsonFile:
    """A JSON (or JSONC) settings file. Plain JSON is rewritten; a file with comments is left alone unless forced."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.exists = os.path.exists(path)
        self.had_comments = False
        if self.exists:
            with open(path, "r", encoding="utf-8-sig") as f:
                raw = f.read()
            if raw.strip():
                clean, self.had_comments = strip_jsonc(raw)
                try:
                    self.data = json.loads(clean)
                except ValueError as e:
                    raise ConfigError("%s is not valid JSON (%s)" % (path, e))
                if not isinstance(self.data, dict):
                    raise ConfigError("%s does not hold a JSON object" % path)

    def save(self, force_comments=False):
        if self.had_comments and not force_comments:
            raise ConfigError("%s has comments or trailing commas; rewriting it would lose them (use --force-jsonc to do it anyway, a backup is kept)" % self.path)
        backup(self.path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp-ervisio"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, self.path)


_backed_up = set()


def backup(path):
    if os.path.exists(path) and path not in _backed_up:
        _backed_up.add(path)
        shutil.copy2(path, "%s.bak-ervisio-ai-%s" % (path, time.strftime("%Y%m%d-%H%M%S")))


def edit_toml_block(path, name, block):
    """Replace (or add, or with block=None remove) the block this script manages in a TOML file. Other text is untouched."""
    begin, end = MARK_BEGIN % name, MARK_END % name
    text = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    pattern = re.compile(r"\n?" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    had = bool(pattern.search(text))
    if re.search(r"^\s*\[mcp_servers\.%s\]" % re.escape(name), pattern.sub("", text), re.M):
        raise ConfigError("%s already has an [mcp_servers.%s] section that this script did not write; pick another --name" % (path, name))
    text = pattern.sub("\n", text).rstrip("\n")
    if block is not None:
        text = (text + "\n\n" if text else "") + begin + "\n" + block.rstrip("\n") + "\n" + end
    text = text.rstrip("\n") + "\n" if text else ""
    backup(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return had


def toml_str(value):
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


# --------------------------------------------------------------------------------------------------------------------
# What to connect to

class Target:
    """The server, and how a client reaches it: an SSH command (stdio) or an HTTP URL with a bearer token."""

    def __init__(self, name, mode, host="", user="", port=22, identity="", engine="ervisio-ai", url="", token="", insecure=False,
                 ssh_password=False):
        self.name, self.mode = name, mode
        self.host, self.user, self.port, self.identity, self.engine = host, user, int(port or 22), identity, engine
        self.url, self.token, self.insecure, self.ssh_password = url, token, insecure, ssh_password

    def ssh_args(self, batch=True):
        a = ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4"]
        if batch and not self.ssh_password:
            a += ["-o", "BatchMode=yes"]
        if self.port != 22:
            a += ["-p", str(self.port)]
        if self.identity:
            a += ["-i", self.identity]
        a.append("%s@%s" % (self.user, self.host) if self.user else self.host)
        return a

    def stdio_command(self):
        """(command, args) a client runs to talk to the server over SSH."""
        return "ssh", self.ssh_args() + [self.engine, "stdio"]

    @property
    def headers(self):
        return {"Authorization": "Bearer " + self.token}


# --------------------------------------------------------------------------------------------------------------------
# The clients

class Client:
    id = ""
    label = ""
    note = ""

    def __init__(self, env):
        self.env = env

    def detect(self):
        """A short description of where it was found, or None."""
        raise NotImplementedError

    def supports(self, target):
        return None  # a reason why not, or None

    def apply(self, target, args):
        """Add or update the server. Returns a one-line result."""
        raise NotImplementedError

    def remove(self, target, args):
        raise NotImplementedError

    lang = "json"

    def where(self):
        """Where the settings go, as a person on any computer would find it (not a path on this machine)."""
        return ""

    def body(self, target):
        """The settings to paste there."""
        return ""

    def snippet(self, target):
        """What to paste by hand when the file cannot be edited safely."""
        return "In %s:\n%s" % (self.where(), self.body(target))


class JsonClient(Client):
    """A client whose servers live under one key of one JSON file."""

    key = "mcpServers"

    def paths(self):
        return []

    def path(self):
        found = [p for p in self.paths() if os.path.exists(p)]
        return found[0] if found else (self.paths()[0] if self.paths() else None)

    def entry(self, target):
        raise NotImplementedError

    def detect(self):
        for p in self.paths():
            if os.path.exists(p) or os.path.isdir(os.path.dirname(p)):
                return p
        return None

    def apply(self, target, args):
        f = JsonFile(self.path())
        f.data.setdefault(self.key, {})
        if not isinstance(f.data[self.key], dict):
            raise ConfigError("%s: %r is not an object" % (f.path, self.key))
        f.data[self.key][target.name] = self.entry(target)
        if not args.dry_run:
            f.save(args.force_jsonc)
        return "%s %s" % ("would write" if args.dry_run else "wrote", f.path)

    def remove(self, target, args):
        p = self.path()
        if not p or not os.path.exists(p):
            return "nothing to remove"
        f = JsonFile(p)
        if target.name not in f.data.get(self.key, {}):
            return "not configured"
        del f.data[self.key][target.name]
        if not args.dry_run:
            f.save(args.force_jsonc)
        return "%s from %s" % ("would remove" if args.dry_run else "removed", p)

    def body(self, target):
        return json.dumps({self.key: {target.name: self.entry(target)}}, indent=2)


def stdio_entry(target, with_type=None):
    cmd, argv = target.stdio_command()
    e = {"command": cmd, "args": argv}
    if with_type:
        e = dict([("type", with_type)] + list(e.items()))
    return e


class ClaudeCode(JsonClient):
    id, label = "claude-code", "Claude Code"

    def paths(self):
        return [os.path.join(self.env.home, ".claude.json")]

    def detect(self):
        if self.env.in_path("claude"):
            return "claude command"
        return super().detect() if os.path.exists(self.paths()[0]) else None

    def where(self):
        return "a terminal"

    def entry(self, target):
        if target.mode == "ssh":
            return stdio_entry(target, "stdio")
        e = {"type": "http", "url": target.url, "headers": target.headers}
        return e

    lang = "shell"

    def body(self, target):
        import shlex
        return "claude mcp add-json %s %s --scope user" % (shlex.quote(target.name), shlex.quote(json.dumps(self.entry(target))))

    def apply(self, target, args):
        if self.env.in_path("claude"):
            if args.dry_run:
                return "would run: claude mcp add-json %s <config> --scope user" % target.name
            subprocess.run(["claude", "mcp", "remove", target.name, "--scope", "user"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            r = subprocess.run(["claude", "mcp", "add-json", target.name, json.dumps(self.entry(target)), "--scope", "user"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            if r.returncode == 0:
                return "added with 'claude mcp add-json' (user scope: every project)"
            raise ConfigError("claude mcp add-json failed: %s" % (r.stderr.strip() or r.stdout.strip()))
        return super().apply(target, args)

    def remove(self, target, args):
        if self.env.in_path("claude"):
            if args.dry_run:
                return "would run: claude mcp remove %s --scope user" % target.name
            r = subprocess.run(["claude", "mcp", "remove", target.name, "--scope", "user"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            return "removed" if r.returncode == 0 else "not configured"
        return super().remove(target, args)


class ClaudeDesktop(JsonClient):
    id, label = "claude-desktop", "Claude Desktop"

    def paths(self):
        return [os.path.join(self.env.app_support("Claude"), "claude_desktop_config.json")]

    def where(self):
        return "claude_desktop_config.json (Claude Desktop > Settings > Developer > Edit Config)"

    def supports(self, target):
        if target.mode == "http" and not self.env.in_path("npx"):
            return "Claude Desktop reads local servers only; for HTTP it needs Node's 'npx' (mcp-remote) or use SSH mode"
        return None

    def entry(self, target):
        if target.mode == "ssh":
            return stdio_entry(target)
        e = {"command": "npx", "args": ["-y", "mcp-remote", target.url, "--header", "Authorization: Bearer " + target.token]}
        if target.insecure:
            e["env"] = {"NODE_TLS_REJECT_UNAUTHORIZED": "0"}
        return e


class Cursor(JsonClient):
    id, label = "cursor", "Cursor"

    def paths(self):
        return [os.path.join(self.env.home, ".cursor", "mcp.json")]

    def detect(self):
        return self.paths()[0] if os.path.isdir(os.path.join(self.env.home, ".cursor")) else None

    def where(self):
        return "~/.cursor/mcp.json (or Cursor Settings > MCP)"

    def entry(self, target):
        return stdio_entry(target) if target.mode == "ssh" else {"url": target.url, "headers": target.headers}


class Windsurf(JsonClient):
    id, label = "windsurf", "Windsurf"

    def paths(self):
        return [os.path.join(self.env.home, ".codeium", "windsurf", "mcp_config.json")]

    def detect(self):
        return self.paths()[0] if os.path.isdir(os.path.join(self.env.home, ".codeium", "windsurf")) else None

    def where(self):
        return "~/.codeium/windsurf/mcp_config.json"

    def entry(self, target):
        return stdio_entry(target) if target.mode == "ssh" else {"serverUrl": target.url, "headers": target.headers}


class Antigravity(JsonClient):
    id, label = "antigravity", "Google Antigravity"

    def paths(self):
        # Antigravity 2.x keeps one shared file; earlier builds used ~/.gemini/antigravity.
        return [os.path.join(self.env.home, ".gemini", "config", "mcp_config.json"),
                os.path.join(self.env.home, ".gemini", "antigravity", "mcp_config.json")]

    def detect(self):
        g = os.path.join(self.env.home, ".gemini")
        for sub in ("config", "antigravity", "antigravity-cli"):
            if os.path.isdir(os.path.join(g, sub)):
                return os.path.join(g, sub)
        return "agy command" if self.env.in_path("agy") else (self.env.app_support("Antigravity") if os.path.isdir(self.env.app_support("Antigravity")) else None)

    def path(self):
        config, legacy = self.paths()
        if os.path.exists(config):
            return config
        if os.path.exists(legacy) or (os.path.isdir(os.path.dirname(legacy)) and not os.path.isdir(os.path.dirname(config))):
            return legacy
        return config

    def where(self):
        return "~/.gemini/config/mcp_config.json (Antigravity: Settings > Customizations > Open MCP Config)"

    def entry(self, target):
        return stdio_entry(target) if target.mode == "ssh" else {"serverUrl": target.url, "headers": target.headers}


class GeminiCli(JsonClient):
    id, label = "gemini-cli", "Gemini CLI"

    def paths(self):
        return [os.path.join(self.env.home, ".gemini", "settings.json")]

    def detect(self):
        return "gemini command" if self.env.in_path("gemini") else (self.paths()[0] if os.path.exists(self.paths()[0]) else None)

    def where(self):
        return "~/.gemini/settings.json"

    def entry(self, target):
        if target.mode == "ssh":
            e = stdio_entry(target)
            e["timeout"] = 900000
            return e
        return {"httpUrl": target.url, "headers": target.headers, "timeout": 900000}


class VSCode(Client):
    """VS Code and its forks keep user MCP servers in <user data>/User/mcp.json under "servers"."""
    id, label = "vscode", "VS Code (Copilot)"

    def files(self):
        out = []
        for app in ("Code", "Code - Insiders", "VSCodium"):
            d = self.env.app_support(app)
            if os.path.isdir(d):
                out.append(os.path.join(d, "User", "mcp.json"))
        return out

    def detect(self):
        f = self.files()
        return ", ".join(os.path.dirname(os.path.dirname(x)) for x in f) if f else None

    def entry(self, target):
        if target.mode == "ssh":
            return stdio_entry(target, "stdio")
        return {"type": "http", "url": target.url, "headers": target.headers}

    def apply(self, target, args):
        done = []
        for p in self.files():
            f = JsonFile(p)
            f.data.setdefault("servers", {})[target.name] = self.entry(target)
            if not args.dry_run:
                f.save(args.force_jsonc)
            done.append(p)
        return "%s %s" % ("would write" if args.dry_run else "wrote", ", ".join(done))

    def remove(self, target, args):
        gone = []
        for p in self.files():
            if os.path.exists(p):
                f = JsonFile(p)
                if target.name in f.data.get("servers", {}):
                    del f.data["servers"][target.name]
                    if not args.dry_run:
                        f.save(args.force_jsonc)
                    gone.append(p)
        return ("removed from " + ", ".join(gone)) if gone else "not configured"

    def where(self):
        return "the user mcp.json (command palette: MCP: Open User Configuration)"

    def body(self, target):
        return json.dumps({"servers": {target.name: self.entry(target)}}, indent=2)


class ExtensionClient(Client):
    """A VS Code extension (Kilo Code, Roo Code, Cline) that keeps its own MCP settings in the editor's global storage."""

    ext_id = ""
    file = ""
    key = "mcpServers"

    def files(self):
        out = []
        for app in EDITORS:
            base = os.path.join(self.env.app_support(app), "User", "globalStorage", self.ext_id)
            if os.path.isdir(base):
                out.append(os.path.join(base, "settings", self.file))
        return out

    def detect(self):
        f = self.files()
        return ", ".join(os.path.dirname(os.path.dirname(x)) for x in f) if f else None

    def entry(self, target):
        raise NotImplementedError

    def apply(self, target, args):
        done = []
        for p in self.files():
            f = JsonFile(p)
            f.data.setdefault(self.key, {})[target.name] = self.entry(target)
            if not args.dry_run:
                f.save(args.force_jsonc)
            done.append(p)
        return "%s %s" % ("would write" if args.dry_run else "wrote", ", ".join(done))

    def remove(self, target, args):
        gone = []
        for p in self.files():
            if os.path.exists(p):
                f = JsonFile(p)
                if target.name in f.data.get(self.key, {}):
                    del f.data[self.key][target.name]
                    if not args.dry_run:
                        f.save(args.force_jsonc)
                    gone.append(p)
        return ("removed from " + ", ".join(gone)) if gone else "not configured"

    def where(self):
        return "the MCP settings of %s (its MCP panel > Edit)" % self.label

    def body(self, target):
        return json.dumps({self.key: {target.name: self.entry(target)}}, indent=2)


class Roo(ExtensionClient):
    id, label = "roo", "Roo Code"
    ext_id, file = "rooveterinaryinc.roo-cline", "mcp_settings.json"

    def entry(self, target):
        if target.mode == "ssh":
            e = stdio_entry(target)
            e["timeout"] = 900
            return e
        return {"type": "streamable-http", "url": target.url, "headers": target.headers, "timeout": 900}


class Cline(ExtensionClient):
    id, label = "cline", "Cline"
    ext_id, file = "saoudrizwan.claude-dev", "cline_mcp_settings.json"

    def entry(self, target):
        if target.mode == "ssh":
            e = stdio_entry(target)
            e["timeout"] = 900
            return e
        return {"type": "streamableHttp", "url": target.url, "headers": target.headers, "timeout": 900}


class Kilo(Client):
    """Kilo Code: the current version reads kilo.json(c) (key "mcp"), the older VS Code extension a settings file."""
    id, label = "kilo", "Kilo Code"

    def cli_files(self):
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(self.env.home, ".config"), "kilo")
        return [os.path.join(base, "kilo.jsonc"), os.path.join(base, "kilo.json")], base

    def legacy_files(self):
        out = []
        for app in EDITORS:
            base = os.path.join(self.env.app_support(app), "User", "globalStorage", "kilocode.kilo-code")
            if os.path.isdir(base):
                out.append(os.path.join(base, "settings", "mcp_settings.json"))
        return out

    def detect(self):
        files, base = self.cli_files()
        found = [p for p in files if os.path.exists(p)] or ([base] if os.path.isdir(base) else [])
        found += [os.path.dirname(os.path.dirname(p)) for p in self.legacy_files()]
        if self.env.in_path("kilo"):
            found.append("kilo command")
        return ", ".join(found) if found else None

    def new_entry(self, target):
        if target.mode == "ssh":
            cmd, argv = target.stdio_command()
            return {"type": "local", "command": [cmd] + argv, "enabled": True, "timeout": 900000}
        return {"type": "remote", "url": target.url, "headers": target.headers, "enabled": True, "timeout": 900000}

    def old_entry(self, target):
        if target.mode == "ssh":
            e = stdio_entry(target)
            e["timeout"] = 900
            return e
        return {"type": "streamable-http", "url": target.url, "headers": target.headers, "timeout": 900}

    def _cli_path(self):
        files, base = self.cli_files()
        for p in files:
            if os.path.exists(p):
                return p
        return files[1] if os.path.isdir(base) or self.env.in_path("kilo") else None

    def apply(self, target, args):
        done = []
        p = self._cli_path()
        if p:
            f = JsonFile(p)
            f.data.setdefault("mcp", {})[target.name] = self.new_entry(target)
            if not args.dry_run:
                f.save(args.force_jsonc)
            done.append(p)
        for p in self.legacy_files():
            f = JsonFile(p)
            f.data.setdefault("mcpServers", {})[target.name] = self.old_entry(target)
            if not args.dry_run:
                f.save(args.force_jsonc)
            done.append(p)
        if not done:
            raise ConfigError("no Kilo Code settings folder found")
        return "%s %s" % ("would write" if args.dry_run else "wrote", ", ".join(done))

    def remove(self, target, args):
        gone = []
        for p, key in [(self._cli_path(), "mcp")] + [(x, "mcpServers") for x in self.legacy_files()]:
            if p and os.path.exists(p):
                f = JsonFile(p)
                if target.name in f.data.get(key, {}):
                    del f.data[key][target.name]
                    if not args.dry_run:
                        f.save(args.force_jsonc)
                    gone.append(p)
        return ("removed from " + ", ".join(gone)) if gone else "not configured"

    def where(self):
        return "~/.config/kilo/kilo.jsonc"

    def body(self, target):
        return json.dumps({"mcp": {target.name: self.new_entry(target)}}, indent=2)


class OpenCode(JsonClient):
    id, label = "opencode", "OpenCode"
    key = "mcp"

    def paths(self):
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(self.env.home, ".config")
        return [os.path.join(base, "opencode", "opencode.json"), os.path.join(base, "opencode", "opencode.jsonc")]

    def detect(self):
        return "opencode command" if self.env.in_path("opencode") else super().detect() if any(os.path.exists(p) for p in self.paths()) else None

    def where(self):
        return "~/.config/opencode/opencode.json"

    def entry(self, target):
        if target.mode == "ssh":
            cmd, argv = target.stdio_command()
            return {"type": "local", "command": [cmd] + argv, "enabled": True, "timeout": 900000}
        return {"type": "remote", "url": target.url, "headers": target.headers, "enabled": True, "timeout": 900000}


class Zed(JsonClient):
    id, label = "zed", "Zed"
    key = "context_servers"

    def paths(self):
        if self.env.system == "Windows":
            return [os.path.join(self.env.appdata, "Zed", "settings.json")]
        return [os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(self.env.home, ".config"), "zed", "settings.json")]

    def detect(self):
        d = os.path.dirname(self.paths()[0])
        return self.paths()[0] if os.path.isdir(d) else None

    def where(self):
        return "Zed settings.json (command palette: zed: open settings file)"

    def entry(self, target):
        if target.mode == "ssh":
            cmd, argv = target.stdio_command()
            return {"command": cmd, "args": argv, "env": {}}
        return {"url": target.url, "headers": target.headers}


class Codex(Client):
    id, label = "codex", "OpenAI Codex"

    def path(self):
        return os.path.join(os.environ.get("CODEX_HOME") or os.path.join(self.env.home, ".codex"), "config.toml")

    def detect(self):
        return "codex command" if self.env.in_path("codex") else (self.path() if os.path.exists(self.path()) else None)

    def block(self, target):
        lines = ["[mcp_servers.%s]" % target.name]
        if target.mode == "ssh":
            cmd, argv = target.stdio_command()
            lines += ["command = %s" % toml_str(cmd), "args = [%s]" % ", ".join(toml_str(a) for a in argv)]
        else:
            lines += ["url = %s" % toml_str(target.url), "http_headers = { Authorization = %s }" % toml_str("Bearer " + target.token)]
        lines += ["startup_timeout_sec = 30", "tool_timeout_sec = 900"]
        return "\n".join(lines)

    def apply(self, target, args):
        if args.dry_run:
            return "would write [mcp_servers.%s] to %s" % (target.name, self.path())
        edit_toml_block(self.path(), target.name, self.block(target))
        return "wrote %s" % self.path()

    def remove(self, target, args):
        if not os.path.exists(self.path()):
            return "nothing to remove"
        if args.dry_run:
            return "would remove [mcp_servers.%s] from %s" % (target.name, self.path())
        return ("removed from " + self.path()) if edit_toml_block(self.path(), target.name, None) else "not configured"

    lang = "toml"

    def where(self):
        return "~/.codex/config.toml"

    def body(self, target):
        return self.block(target)


CLIENTS = [ClaudeCode, ClaudeDesktop, Codex, Cursor, VSCode, Kilo, Roo, Cline, Windsurf, Antigravity, GeminiCli, Zed, OpenCode]


# --------------------------------------------------------------------------------------------------------------------
# Checking that the server answers

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ervisio-ai-connect", "version": VERSION}}}


def check_ssh(target, interactive):
    """Start the server through SSH exactly as an IDE will and read its answer to initialize."""
    cmd, argv = target.stdio_command()
    try:
        p = subprocess.run([cmd] + argv, input=json.dumps(INIT) + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    except FileNotFoundError:
        raise ConfigError("the 'ssh' program is not installed on this computer (on Windows: Settings > Optional features > OpenSSH Client)")
    except subprocess.TimeoutExpired:
        raise ConfigError("ssh did not answer within 30 seconds")
    line = next((l for l in p.stdout.splitlines() if l.startswith("{")), "")
    try:
        if json.loads(line)["result"]["serverInfo"]["name"] == "ervisio-ai":
            return json.loads(line)["result"]["serverInfo"]["version"]
    except (ValueError, KeyError):
        pass
    err = p.stderr.strip()
    if "Host key verification failed" in err or "Permission denied" in err or "passphrase" in err.lower():
        if interactive and sys.stdin.isatty():
            print("\nSSH needs you once (host key, passphrase or password). Running ssh interactively; type 'exit' when you are in.\n")
            subprocess.call(["ssh"] + target.ssh_args(batch=False) + ["true"])
            return check_ssh(target, False)
        raise ConfigError("SSH could not log in without asking (%s). IDEs cannot answer prompts: use a key without passphrase "
                          "or load it in ssh-agent, and connect once by hand to accept the host key." % err.splitlines()[-1] if err else "login failed")
    if "command not found" in err or "No such file" in err or p.returncode == 127:
        raise ConfigError("%s is not installed on the server yet. Open the Ervisio page > AI > Server and press 'Install engine'." % target.engine)
    raise ConfigError("the server did not answer as expected%s" % (": " + err.splitlines()[-1] if err else ""))


def check_http(target):
    import ssl
    req = urllib.request.Request(target.url, data=json.dumps(INIT).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + target.token})
    ctx = ssl._create_unverified_context() if target.insecure else None
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        raise ConfigError("the server answered %d%s" % (e.code, " (wrong or revoked token)" if e.code == 401 else ""))
    except (urllib.error.URLError, OSError) as e:
        raise ConfigError("cannot reach %s: %s" % (target.url, getattr(e, "reason", e)))
    try:
        return json.loads(body)["result"]["serverInfo"]["version"]
    except (ValueError, KeyError):
        raise ConfigError("%s did not answer like an MCP server" % target.url)


# --------------------------------------------------------------------------------------------------------------------

def build_target(args):
    p = dict(PRESET or {})
    for k in ("mode", "name", "host", "user", "port", "identity", "engine", "url", "token"):
        v = getattr(args, k, None)
        if v not in (None, ""):
            p[k] = v
    if args.insecure:
        p["insecure"] = True
    mode = p.get("mode")
    if mode not in ("ssh", "http"):
        raise ConfigError("say how to connect: --mode ssh --host HOST [--user USER], or --mode http --url URL --token TOKEN")
    if mode == "ssh" and not p.get("host"):
        raise ConfigError("--host is required for SSH")
    if mode == "http" and not (p.get("url") and p.get("token")):
        raise ConfigError("--url and --token are required for HTTP")
    name = p.get("name") or "ervisio-" + re.sub(r"[^a-z0-9]+", "-", (p.get("host") or re.sub(r"^\w+://([^/:]+).*$", r"\1", p.get("url", ""))).lower()).strip("-")
    name = re.sub(r"[^A-Za-z0-9_-]", "-", name)[:48] or "ervisio"
    return Target(name, mode, p.get("host", ""), p.get("user", ""), p.get("port", 22), p.get("identity", ""), p.get("engine") or "ervisio-ai",
                  p.get("url", ""), p.get("token", ""), bool(p.get("insecure")), bool(p.get("ssh_password")))


def choose(found, args):
    if args.clients:
        wanted = [x.strip() for x in args.clients.split(",") if x.strip()]
        bad = [w for w in wanted if w not in [c.id for c in CLIENTS_ALL]]
        if bad:
            raise ConfigError("unknown client(s): %s (known: %s)" % (", ".join(bad), ", ".join(c.id for c in CLIENTS_ALL)))
        return [c for c in CLIENTS_ALL if c.id in wanted]
    if args.yes or not sys.stdin.isatty():
        return found
    print("\nFound on this computer:")
    for i, c in enumerate(found, 1):
        print("  %2d. %s" % (i, c.label))
    ans = input("\nConnect which? [a = all, numbers like 1,3, q = quit] (a): ").strip().lower() or "a"
    if ans == "q":
        return []
    if ans == "a":
        return found
    picked = []
    for part in re.split(r"[,\s]+", ans):
        if part.isdigit() and 1 <= int(part) <= len(found):
            picked.append(found[int(part) - 1])
    return picked


CLIENTS_ALL = []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Connect AI tools on this computer to an Ervisio AI server.")
    ap.add_argument("--mode", choices=["ssh", "http"], help="ssh: the IDE starts the server through SSH (no open port). http: URL + token")
    ap.add_argument("--name", help="name of the server inside the tools (default: ervisio-<host>)")
    ap.add_argument("--host", help="ssh: the server's address")
    ap.add_argument("--user", help="ssh: user name (the one with the rights you want the AI to have, usually root)")
    ap.add_argument("--port", type=int, help="ssh: port (default 22)")
    ap.add_argument("--identity", help="ssh: private key file")
    ap.add_argument("--engine", help="ssh: command of the engine on the server (default ervisio-ai)")
    ap.add_argument("--url", help="http: address of the MCP endpoint, ending in /mcp")
    ap.add_argument("--token", help="http: the bearer token")
    ap.add_argument("--insecure", action="store_true", help="http: accept a self-signed certificate")
    ap.add_argument("--clients", help="comma-separated tools (default: everything found). See --list")
    ap.add_argument("--list", action="store_true", help="show the tools found on this computer and exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    ap.add_argument("--remove", action="store_true", help="take the server out of the tools")
    ap.add_argument("--no-check", action="store_true", help="do not test the connection first")
    ap.add_argument("--force-jsonc", action="store_true", help="rewrite settings files that contain comments (a backup is kept)")
    ap.add_argument("--print", dest="print_snippets", action="store_true", help="print the settings for every tool instead of writing them")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask questions")
    ap.add_argument("--home", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version="ervisio-ai-connect " + VERSION)
    args = ap.parse_args(argv)

    env = Env(args.home)
    global CLIENTS_ALL
    CLIENTS_ALL = [c(env) for c in CLIENTS]
    found = []
    for c in CLIENTS_ALL:
        where = c.detect()
        if where:
            found.append(c)
            if args.list:
                print("  %-16s %-15s %s" % (c.id, c.label, where))
    if args.list:
        if not found:
            print("No supported AI tools found on this computer.")
        return 0

    try:
        target = build_target(args)
    except ConfigError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2

    if args.print_snippets:
        for c in CLIENTS_ALL:
            print("# %s\n%s\n" % (c.label, c.snippet(target)))
        return 0

    try:
        chosen = choose(found, args)
    except ConfigError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if not chosen:
        print("Nothing to do: no supported AI tool was found (try --list), or none was chosen." if not found else "Nothing chosen.")
        return 1

    if not args.remove and not args.no_check and not args.dry_run:
        try:
            print("Checking the connection to %s ..." % (target.host if target.mode == "ssh" else target.url))
            version = check_ssh(target, True) if target.mode == "ssh" else check_http(target)
            print("  ok: Ervisio AI %s answers.\n" % version)
        except ConfigError as e:
            print("  FAILED: %s\n\nNothing was changed. Fix that, or use --no-check to write the settings anyway." % e, file=sys.stderr)
            return 3

    failures = 0
    print("%s '%s' %s:" % ("Removing" if args.remove else "Adding", target.name, "(dry run)" if args.dry_run else ""))
    for c in chosen:
        try:
            why = None if args.remove else c.supports(target)
            if why:
                print("  - %-16s skipped: %s" % (c.label, why))
                continue
            res = c.remove(target, args) if args.remove else c.apply(target, args)
            print("  + %-16s %s" % (c.label, res))
        except ConfigError as e:
            failures += 1
            print("  ! %-16s %s" % (c.label, e))
            if not args.remove:
                print("      Add it by hand:\n      " + c.snippet(target).replace("\n", "\n      "))
        except (OSError, subprocess.SubprocessError) as e:
            failures += 1
            print("  ! %-16s %s" % (c.label, e))
    if not args.dry_run and not args.remove:
        print("\nRestart the tools above (Claude Code: start a new session) so they load the server.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
