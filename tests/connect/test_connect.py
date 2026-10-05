import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENGINE = os.path.join(ROOT, "plugin", "engine")
spec = importlib.util.spec_from_file_location("connect", os.path.join(ROOT, "plugin", "connect", "ervisio-ai-connect.py"))
connect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connect)


class Sandbox(unittest.TestCase):
    """A fake home directory with some AI tools "installed", and fake `ssh` and `claude` commands on PATH."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="eai-connect-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.bin = os.path.join(self.home, "bin")
        os.makedirs(self.bin)
        self.env = dict(os.environ, HOME=self.home, XDG_CONFIG_HOME=os.path.join(self.home, ".config"), PATH=self.bin + ":/usr/bin:/bin",  # no real claude, codex, npx... from the machine running the tests
                        
                        ERVISIO_AI_HOME=os.path.join(self.home, "eai"), CODEX_HOME="")
        self.env.pop("CODEX_HOME")
        patcher = unittest.mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # the engine on the "server" side
        self.script("ervisio-ai", '#!/bin/sh\nexec %s -B %s "$@"\n' % (sys.executable, ENGINE))
        # an ssh that runs the remote command locally: skips its own options, the host, and `--`
        self.script("ssh", '#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -o|-p|-i|-J) shift 2;; -T|-t) shift;; --) shift;;'
                           ' *) break;; esac; done\nshift\nexec "$@"\n')

    def script(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, 0o755)
        return p

    def p(self, *parts):
        return os.path.join(self.home, *parts)

    def put(self, rel, content):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content, indent=2))
        return path

    def load(self, rel):
        with open(self.p(rel)) as f:
            return json.load(f)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def run_connect(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = connect.main(list(argv) + ["--home", self.home])
        return code, out.getvalue(), err.getvalue()


SSH = ["--mode", "ssh", "--host", "vps.example.com", "--user", "root", "--yes", "--no-check"]
HTTP = ["--mode", "http", "--url", "https://mcp.example.com/mcp", "--token", "eai_secret_token_value", "--yes", "--no-check"]


class Writing(Sandbox):
    def setUp(self):
        super().setUp()
        self.put(".cursor/mcp.json", {"mcpServers": {"other": {"command": "x"}}})
        self.put(".codeium/windsurf/mcp_config.json", {"mcpServers": {}})
        self.put(".config/Code/User/settings.json", "{}")
        self.put(".gemini/settings.json", {"theme": "dark"})
        self.put(".config/zed/settings.json", {"theme": "One"})
        self.put(".config/opencode/opencode.json", {"$schema": "x"})
        self.put(".codex/config.toml", 'model = "o3"\n\n[mcp_servers.existing]\ncommand = "keep"\n')
        self.put(".config/Claude/claude_desktop_config.json", {"mcpServers": {}})
        self.put(".config/Code/User/globalStorage/rooveterinaryinc.roo-cline/x", "")
        self.put(".config/Code/User/globalStorage/saoudrizwan.claude-dev/x", "")
        self.put(".config/Code/User/globalStorage/kilocode.kilo-code/x", "")
        self.put(".config/kilo/x", "")

    def test_list_finds_what_is_installed(self):
        code, out, _ = self.run_connect("--list")
        self.assertEqual(code, 0)
        for cid in ("cursor", "windsurf", "vscode", "gemini-cli", "zed", "opencode", "codex", "claude-desktop", "roo", "cline", "kilo"):
            self.assertIn(cid, out)
        self.assertNotIn("antigravity", out)

    def test_ssh_entries_for_every_client(self):
        code, out, err = self.run_connect(*SSH)
        self.assertEqual(code, 0, out + err)
        name = "ervisio-vps-example-com"
        want_args = ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes", "root@vps.example.com", "ervisio-ai", "stdio"]
        cur = self.load(".cursor/mcp.json")["mcpServers"]
        self.assertEqual(cur[name], {"command": "ssh", "args": want_args})
        self.assertEqual(cur["other"], {"command": "x"})  # existing servers are kept
        self.assertEqual(self.load(".codeium/windsurf/mcp_config.json")["mcpServers"][name]["args"], want_args)
        self.assertEqual(self.load(".config/Code/User/mcp.json")["servers"][name], {"type": "stdio", "command": "ssh", "args": want_args})
        self.assertEqual(self.load(".gemini/settings.json")["mcpServers"][name]["command"], "ssh")
        self.assertEqual(self.load(".gemini/settings.json")["theme"], "dark")
        self.assertEqual(self.load(".config/zed/settings.json")["context_servers"][name], {"command": "ssh", "args": want_args, "env": {}})
        self.assertEqual(self.load(".config/opencode/opencode.json")["mcp"][name]["command"], ["ssh"] + want_args)
        self.assertEqual(self.load(".config/opencode/opencode.json")["mcp"][name]["type"], "local")
        self.assertEqual(self.load(".config/Claude/claude_desktop_config.json")["mcpServers"][name]["command"], "ssh")
        roo = self.load(".config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json")
        self.assertEqual(roo["mcpServers"][name]["command"], "ssh")
        self.assertEqual(self.load(".config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json")["mcpServers"][name]["command"], "ssh")
        self.assertEqual(self.load(".config/Code/User/globalStorage/kilocode.kilo-code/settings/mcp_settings.json")["mcpServers"][name]["command"], "ssh")
        kilo = self.load(".config/kilo/kilo.json")
        self.assertEqual(kilo["mcp"][name]["type"], "local")

    def test_http_entries_use_each_tools_own_keys(self):
        code, out, err = self.run_connect(*HTTP)
        self.assertEqual(code, 0, out + err)
        name = "ervisio-mcp-example-com"
        hdr = {"Authorization": "Bearer eai_secret_token_value"}
        url = "https://mcp.example.com/mcp"
        self.assertEqual(self.load(".cursor/mcp.json")["mcpServers"][name], {"url": url, "headers": hdr})
        self.assertEqual(self.load(".codeium/windsurf/mcp_config.json")["mcpServers"][name], {"serverUrl": url, "headers": hdr})
        self.assertEqual(self.load(".config/Code/User/mcp.json")["servers"][name], {"type": "http", "url": url, "headers": hdr})
        self.assertEqual(self.load(".gemini/settings.json")["mcpServers"][name]["httpUrl"], url)
        self.assertEqual(self.load(".config/zed/settings.json")["context_servers"][name], {"url": url, "headers": hdr})
        self.assertEqual(self.load(".config/opencode/opencode.json")["mcp"][name]["type"], "remote")
        self.assertEqual(self.load(".config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json")["mcpServers"][name]["type"], "streamableHttp")
        self.assertEqual(self.load(".config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json")["mcpServers"][name]["type"], "streamable-http")
        self.assertEqual(self.load(".config/Code/User/globalStorage/kilocode.kilo-code/settings/mcp_settings.json")["mcpServers"][name]["type"], "streamable-http")
        self.assertEqual(self.load(".config/kilo/kilo.json")["mcp"][name]["type"], "remote")
        # Claude Desktop only runs local servers: with no npx it is skipped with a reason, the others still work
        self.assertIn("skipped", out)
        self.assertNotIn(name, self.load(".config/Claude/claude_desktop_config.json")["mcpServers"])

    def test_claude_desktop_http_goes_through_mcp_remote_when_npx_exists(self):
        self.script("npx", "#!/bin/sh\nexit 0\n")
        self.run_connect(*HTTP)
        e = self.load(".config/Claude/claude_desktop_config.json")["mcpServers"]["ervisio-mcp-example-com"]
        self.assertEqual(e["command"], "npx")
        self.assertEqual(e["args"][:3], ["-y", "mcp-remote", "https://mcp.example.com/mcp"])
        self.assertIn("Authorization: Bearer eai_secret_token_value", e["args"])

    def test_codex_toml_block_is_added_replaced_and_removed_without_touching_the_rest(self):
        path = self.p(".codex/config.toml")
        self.run_connect(*SSH, "--clients", "codex")
        text = self.read(path)
        self.assertIn('model = "o3"', text)
        self.assertIn('[mcp_servers.existing]\ncommand = "keep"', text)
        self.assertIn("[mcp_servers.ervisio-vps-example-com]", text)
        self.assertIn('command = "ssh"', text)
        self.assertIn("tool_timeout_sec = 900", text)
        first = text
        self.run_connect(*SSH, "--clients", "codex")  # idempotent
        self.assertEqual(self.read(path), first)
        self.run_connect(*HTTP, "--clients", "codex", "--name", "ervisio-vps-example-com")  # replaced, not duplicated
        text = self.read(path)
        self.assertEqual(text.count("[mcp_servers.ervisio-vps-example-com]"), 1)
        self.assertIn('url = "https://mcp.example.com/mcp"', text)
        self.assertIn('http_headers = { Authorization = "Bearer eai_secret_token_value" }', text)
        self.assertNotIn('command = "ssh"', text)
        self.run_connect(*SSH, "--clients", "codex", "--remove", "--name", "ervisio-vps-example-com")
        text = self.read(path)
        self.assertNotIn("ervisio", text)
        self.assertIn('[mcp_servers.existing]', text)
        self.assertTrue(text.startswith('model = "o3"'))

    def test_codex_refuses_to_overwrite_a_section_it_did_not_write(self):
        code, out, _ = self.run_connect(*SSH, "--clients", "codex", "--name", "existing")
        self.assertEqual(code, 1)
        self.assertIn("did not write", out)
        self.assertIn('command = "keep"', self.read(self.p(".codex/config.toml")))

    def test_backups_are_made_once_before_the_first_change(self):
        self.run_connect(*SSH, "--clients", "cursor")
        baks = [n for n in os.listdir(self.p(".cursor")) if ".bak-ervisio-ai-" in n]
        self.assertEqual(len(baks), 1)
        self.assertEqual(json.loads(self.read(self.p(".cursor", baks[0]))), {"mcpServers": {"other": {"command": "x"}}})

    def test_dry_run_changes_nothing(self):
        def snapshot():
            out = {}
            for d, _, files in os.walk(self.home):
                if d == self.bin:
                    continue
                for f in files:
                    with open(os.path.join(d, f), "rb") as fh:
                        out[os.path.join(d, f)] = fh.read()
            return out

        before = snapshot()
        code, out, _ = self.run_connect(*SSH, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would", out)
        self.assertEqual(before, snapshot())

    def test_remove_takes_only_our_entry_out(self):
        self.run_connect(*SSH)
        code, out, _ = self.run_connect("--mode", "ssh", "--host", "vps.example.com", "--yes", "--remove")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.load(".cursor/mcp.json"), {"mcpServers": {"other": {"command": "x"}}})
        self.assertEqual(self.load(".config/Code/User/mcp.json"), {"servers": {}})
        self.assertEqual(self.load(".gemini/settings.json"), {"theme": "dark", "mcpServers": {}})

    def test_choosing_clients(self):
        self.run_connect(*SSH, "--clients", "cursor,zed")
        self.assertIn("ervisio-vps-example-com", self.load(".cursor/mcp.json")["mcpServers"])
        self.assertEqual(self.load(".codeium/windsurf/mcp_config.json"), {"mcpServers": {}})
        code, _, err = self.run_connect(*SSH, "--clients", "nonsense")
        self.assertEqual(code, 2)
        self.assertIn("unknown client", err)

    def test_options_reach_the_ssh_command(self):
        self.run_connect("--mode", "ssh", "--host", "h", "--user", "u", "--port", "2222", "--identity", "/k/id", "--engine", "/opt/x/ervisio-ai",
                         "--name", "box", "--yes", "--no-check", "--clients", "cursor")
        args = self.load(".cursor/mcp.json")["mcpServers"]["box"]["args"]
        self.assertIn("-p", args)
        self.assertEqual(args[args.index("-p") + 1], "2222")
        self.assertEqual(args[args.index("-i") + 1], "/k/id")
        self.assertEqual(args[-3:], ["u@h", "/opt/x/ervisio-ai", "stdio"])

    def test_the_token_never_appears_in_the_output(self):
        _, out, err = self.run_connect(*HTTP)
        self.assertNotIn("eai_secret_token_value", out + err)


class Careful(Sandbox):
    def test_a_file_with_comments_is_not_rewritten_but_the_snippet_is_shown(self):
        path = self.put(".config/zed/settings.json", '// my settings\n{\n  "theme": "One", // nice\n}\n')
        before = self.read(path)
        code, out, _ = self.run_connect(*SSH, "--clients", "zed")
        self.assertEqual(code, 1)
        self.assertIn("comments", out)
        self.assertIn('"context_servers"', out)  # the snippet to paste
        self.assertEqual(self.read(path), before)

    def test_force_jsonc_rewrites_with_a_backup(self):
        path = self.put(".config/zed/settings.json", '// my settings\n{"theme": "One",}\n')
        code, out, _ = self.run_connect(*SSH, "--clients", "zed", "--force-jsonc")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.load(".config/zed/settings.json")["theme"], "One")
        self.assertTrue([n for n in os.listdir(os.path.dirname(path)) if ".bak-" in n])

    def test_invalid_json_is_never_overwritten(self):
        path = self.put(".cursor/mcp.json", "{this is broken")
        code, out, _ = self.run_connect(*SSH, "--clients", "cursor")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)
        self.assertEqual(self.read(path), "{this is broken")

    def test_nothing_found(self):
        code, out, _ = self.run_connect(*SSH)
        self.assertEqual(code, 1)
        self.assertIn("no supported AI tool", out)

    def test_bad_arguments(self):
        for argv, text in ((["--yes"], "say how to connect"), (["--mode", "ssh"], "--host is required"), (["--mode", "http", "--url", "https://x/mcp"], "--url and --token")):
            code, _, err = self.run_connect(*argv)
            self.assertEqual(code, 2)
            self.assertIn(text, err)

    def test_preset_filled_in_by_the_page_is_used(self):
        self.put(".cursor/mcp.json", {})
        connect.PRESET = {"mode": "ssh", "host": "preset-host", "user": "root", "name": "from-preset"}
        self.addCleanup(setattr, connect, "PRESET", None)
        code, out, err = self.run_connect("--yes", "--no-check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("from-preset", self.load(".cursor/mcp.json")["mcpServers"])

    def test_print_shows_every_clients_settings(self):
        code, out, _ = self.run_connect(*SSH, "--print")
        self.assertEqual(code, 0)
        for label in ("Claude Code", "OpenAI Codex", "Cursor", "Kilo Code", "Zed", "OpenCode"):
            self.assertIn("# " + label, out)


class ClaudeCodeCli(Sandbox):
    def test_uses_the_claude_command_with_user_scope(self):
        log = self.p("claude.log")
        self.script("claude", '#!/bin/sh\necho "$@" >> %s\nexit 0\n' % log)
        code, out, _ = self.run_connect(*SSH, "--clients", "claude-code")
        self.assertEqual(code, 0, out)
        lines = self.read(log).splitlines()
        self.assertTrue(lines[0].startswith("mcp remove ervisio-vps-example-com --scope user"))
        self.assertTrue(lines[1].startswith("mcp add-json ervisio-vps-example-com {"))
        self.assertTrue(lines[1].endswith("--scope user"))
        entry = json.loads(lines[1][len("mcp add-json ervisio-vps-example-com "):-len(" --scope user")])
        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["command"], "ssh")

    def test_http_entry_for_claude_code(self):
        log = self.p("claude.log")
        self.script("claude", '#!/bin/sh\necho "$@" >> %s\nexit 0\n' % log)
        self.run_connect(*HTTP, "--clients", "claude-code")
        entry = json.loads(self.read(log).splitlines()[1].split(" ", 3)[3].rsplit(" --scope", 1)[0])
        self.assertEqual(entry, {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer eai_secret_token_value"}})

    def test_without_the_command_it_edits_claude_json(self):
        self.put(".claude.json", {"numStartups": 5, "mcpServers": {}})
        code, _, _ = self.run_connect(*SSH, "--clients", "claude-code")
        self.assertEqual(code, 0)
        d = self.load(".claude.json")
        self.assertEqual(d["numStartups"], 5)
        self.assertEqual(d["mcpServers"]["ervisio-vps-example-com"]["type"], "stdio")

    def test_a_failing_claude_command_is_reported(self):
        self.script("claude", "#!/bin/sh\necho boom >&2\nexit 1\n")
        code, out, _ = self.run_connect(*SSH, "--clients", "claude-code")
        self.assertEqual(code, 1)
        self.assertIn("boom", out)


class ConnectionCheck(Sandbox):
    def test_ssh_check_talks_to_the_real_engine(self):
        t = connect.Target("n", "ssh", host="h", user="root")
        self.assertRegex(connect.check_ssh(t, False), r"^\d+\.\d+\.\d+$")

    def test_a_server_without_the_engine_is_explained(self):
        os.unlink(self.p("bin", "ervisio-ai"))
        t = connect.Target("n", "ssh", host="h")
        with self.assertRaises(connect.ConfigError) as e:
            connect.check_ssh(t, False)
        self.assertIn("not installed on the server", str(e.exception))

    def test_ssh_login_failure_is_explained(self):
        self.script("ssh", "#!/bin/sh\necho 'root@h: Permission denied (publickey).' >&2\nexit 255\n")
        with self.assertRaises(connect.ConfigError) as e:
            connect.check_ssh(connect.Target("n", "ssh", host="h"), False)
        self.assertIn("cannot answer prompts", str(e.exception))

    def test_check_blocks_the_write_when_it_fails(self):
        self.put(".cursor/mcp.json", {})
        self.script("ssh", "#!/bin/sh\necho 'Permission denied' >&2\nexit 255\n")
        code, out, err = self.run_connect("--mode", "ssh", "--host", "h", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("Nothing was changed", err)
        self.assertEqual(self.load(".cursor/mcp.json"), {})

    def test_http_check_against_the_real_server(self):
        sys.path.insert(0, ENGINE)
        port = _free_port()
        from ervisio_ai import auth
        os.environ["ERVISIO_AI_HOME"] = self.p("eai")
        token = auth.create("t")["token"]
        srv = subprocess.Popen([sys.executable, "-B", ENGINE, "serve", "--port", str(port)], env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (srv.terminate(), srv.wait(10), srv.stdout.close(), srv.stderr.close()))
        import time
        for _ in range(50):
            try:
                connect.check_http(connect.Target("n", "http", url="http://127.0.0.1:%d/mcp" % port, token=token))
                break
            except connect.ConfigError:
                time.sleep(0.1)
        self.assertRegex(connect.check_http(connect.Target("n", "http", url="http://127.0.0.1:%d/mcp" % port, token=token)), r"^\d+\.\d+\.\d+$")
        with self.assertRaises(connect.ConfigError) as e:
            connect.check_http(connect.Target("n", "http", url="http://127.0.0.1:%d/mcp" % port, token="eai_" + "x" * 43))
        self.assertIn("wrong or revoked token", str(e.exception))
        with self.assertRaises(connect.ConfigError):
            connect.check_http(connect.Target("n", "http", url="http://127.0.0.1:1/mcp", token=token))

    def test_end_to_end_ssh_then_write(self):
        self.put(".cursor/mcp.json", {})
        code, out, err = self.run_connect("--mode", "ssh", "--host", "h", "--yes")
        self.assertEqual(code, 0, out + err)
        self.assertIn("answers", out)
        self.assertIn("ervisio-h", self.load(".cursor/mcp.json")["mcpServers"])


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Jsonc(unittest.TestCase):
    def test_strip(self):
        clean, had = connect.strip_jsonc('{"a": "http://x//y", /* c */ "b": [1, 2,], // end\n}')
        self.assertTrue(had)
        self.assertEqual(json.loads(clean), {"a": "http://x//y", "b": [1, 2]})
        clean, had = connect.strip_jsonc('{"a": 1}')
        self.assertFalse(had)
        self.assertEqual(json.loads(clean), {"a": 1})


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
