import http.client
import io
import json
import os
import re
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import ENGINE, EngineCase
from mcpproc import free_port

from ervisio_ai import auth, cli, config, ctl, paths, policy


class Admin(EngineCase):
    """Install, run and remove the headless server for real, in a sandbox of temporary folders."""

    def setUp(self):
        super().setUp()
        self.keep = (paths.INSTALL_DIR, paths.WRAPPER, paths.UNIT_PATH, ctl._has_systemd)
        paths.INSTALL_DIR = os.path.join(self.home, "usr-local-lib", "ervisio-ai")
        paths.WRAPPER = os.path.join(self.home, "usr-local-bin", "ervisio-ai")
        paths.UNIT_PATH = os.path.join(self.home, "ervisio-ai.service")
        ctl._has_systemd = lambda: False      # the pidfile fallback, which works anywhere
        self.addCleanup(self._restore)
        self.addCleanup(self._kill)

    def _restore(self):
        paths.INSTALL_DIR, paths.WRAPPER, paths.UNIT_PATH, ctl._has_systemd = self.keep

    def _kill(self):
        pid = ctl._pid_alive()
        if pid:
            os.kill(pid, 15)
            time.sleep(0.3)

    def healthz(self, port):
        for _ in range(40):
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                c.request("GET", "/healthz")
                ok = c.getresponse().status == 200
                c.close()
                return ok
            except OSError:
                time.sleep(0.15)
        return False

    def test_install_run_upgrade_reconfigure_uninstall(self):
        # the engine is copied clean (no bytecode, no tests) and the wrapper works
        ctl._copy_engine()
        files = {os.path.relpath(os.path.join(d, f), paths.INSTALL_DIR) for d, _, fs in os.walk(paths.INSTALL_DIR) for f in fs}
        self.assertIn("__main__.py", files)
        self.assertIn("ervisio_ai/tools/shell.py", files)
        self.assertFalse([f for f in files if "__pycache__" in f or f.endswith(".pyc")])
        port = free_port()
        res = ctl.run("install", {"port": port, "token_label": "ci"})
        st = res["status"]
        self.assertTrue(st["engine"]["installed"])
        self.assertEqual(st["engine"]["version"], st["version"])
        self.assertTrue(os.access(paths.WRAPPER, os.X_OK))
        import subprocess
        self.assertIn(st["version"], subprocess.run([paths.WRAPPER, "version"], stdout=subprocess.PIPE, universal_newlines=True).stdout)
        # the service answers and the first token is valid
        self.assertTrue(self.healthz(port))
        self.assertTrue(st["service"]["active"])
        self.assertIsNotNone(auth.verify(res["token"]["token"]))
        self.assertEqual(ctl.run("status", {})["url"], "http://127.0.0.1:%d/mcp" % port)
        self.assertTrue(ctl.run("status", {})["reachable"])
        # restart gives a new process
        pid = ctl.run("status", {})["service"]["pid"]
        again = ctl.run("restart", {})["status"]["service"]
        self.assertTrue(again["active"])
        self.assertNotEqual(again["pid"], pid)
        # an old engine is noticed and upgraded
        init = os.path.join(paths.INSTALL_DIR, "ervisio_ai", "__init__.py")
        with open(init) as f:
            text = f.read()
        with open(init, "w") as f:
            f.write(text.replace('__version__ = "', '__version__ = "0.0.'))
        self.assertTrue(ctl.run("status", {})["engine"]["outdated"])
        self.assertFalse(next(c for c in ctl.run("doctor", {})["checks"] if c["name"] == "engine up to date")["ok"])
        ctl.run("upgrade", {})
        self.assertFalse(ctl.run("status", {})["engine"]["outdated"])
        self.assertTrue(ctl.run("status", {})["service"]["active"])
        # reconfigure: new port, restarted
        new_port = free_port()
        out = ctl.run("config_set", {"port": new_port})
        self.assertEqual(out["config"]["port"], new_port)
        self.assertTrue(self.healthz(new_port))
        # stop / start
        self.assertFalse(ctl.run("stop", {})["status"]["service"]["active"])
        self.assertTrue(ctl.run("start", {})["status"]["service"]["active"])
        self.assertTrue(self.healthz(new_port))
        # uninstall keeps the data, purge removes it
        ctl.run("uninstall", {})
        self.assertFalse(os.path.exists(paths.INSTALL_DIR))
        self.assertFalse(os.path.exists(paths.WRAPPER))
        self.assertTrue(os.path.exists(os.path.join(self.home, "tokens.json")))
        time.sleep(0.5)
        self.assertFalse(ctl._pid_alive())
        ctl.run("uninstall", {"purge": True})
        self.assertFalse(os.path.exists(self.home))

    def test_engine_only_install_for_ssh_use(self):
        res = ctl.run("install", {"service": False})
        self.assertTrue(res["status"]["engine"]["installed"])
        self.assertFalse(res["status"]["service"]["active"])
        import subprocess
        self.assertEqual(subprocess.run([paths.WRAPPER, "stdio"], input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n', stdout=subprocess.PIPE,
                                        universal_newlines=True, timeout=20).stdout.strip(), '{"jsonrpc":"2.0","id":1,"result":{}}')

    def test_a_server_owned_by_root_still_counts_as_alive_for_other_users(self):
        with open(ctl._pidfile(), "w") as f:
            f.write(str(os.getpid()))
        with mock.patch.object(ctl.os, "kill", side_effect=PermissionError(1, "Operation not permitted")):
            self.assertEqual(ctl._pid_alive(), os.getpid())
        os.unlink(ctl._pidfile())  # the cleanup would otherwise stop the pid in it: this very process

    def test_doctor_reports_who_the_server_runs_as_and_hides_what_only_root_can_see(self):
        with mock.patch.object(ctl, "_server_uid", return_value=0):
            checks = {c["name"]: c for c in ctl.doctor({})["checks"]}
        self.assertEqual(checks["root"]["detail"], "the server runs as root")
        with mock.patch.object(ctl.os, "geteuid", return_value=1000), mock.patch.object(ctl, "_server_uid", return_value=0):
            self.assertIsNone(ctl.status({})["tokens"])
            names = [c["name"] for c in ctl.doctor({})["checks"]]
        self.assertNotIn("tokens", names)

    def test_config_validation(self):
        for bad in ({"port": 0}, {"port": 70000}, {"tls": "maybe"}, {"tls": "custom"}, {"nonsense": 1}, {"bind": 5}):
            with self.assertRaises((ctl.CtlError, ValueError), msg=str(bad)):
                config.save(bad) if "nonsense" in bad or "bind" in bad else ctl.run("config_set", dict(bad, restart=False))

    def test_tokens_and_policy_through_ctl(self):
        t = ctl.run("token_create", {"label": "phone", "scope": "readonly", "days": 7})["token"]
        self.assertEqual(t["scope"], "readonly")
        self.assertEqual([x["label"] for x in ctl.run("token_list", {})["tokens"]], ["phone"])
        self.assertNotIn("hash", json.dumps(ctl.run("token_list", {})))
        with self.assertRaises(ctl.CtlError):
            ctl.run("token_create", {"label": ""})
        ctl.run("token_revoke", {"id": "phone"})
        with self.assertRaises(ctl.CtlError):
            ctl.run("token_revoke", {"id": "phone"})
        ctl.run("policy_set", {"mode": "readonly", "disabled_tools": ["docker_cli"]})
        self.assertEqual(ctl.run("policy_get", {})["policy"]["mode"], "readonly")
        self.assertIn("docker", ctl.run("policy_get", {})["categories"])
        with self.assertRaises(ctl.CtlError):
            ctl.run("policy_set", {"mode": "chaos"})
        self.assertEqual(oct(os.stat(os.path.join(self.home, "policy.json")).st_mode & 0o777), "0o644")  # not secret, readable by users

    def test_audit_logs_and_unknown_actions(self):
        self.call("system_info")
        self.assertEqual(ctl.run("audit", {"limit": 5})["entries"][0]["tool"], "system_info")
        self.assertIsInstance(ctl.run("logs", {})["text"], str)
        with self.assertRaises(ctl.CtlError):
            ctl.run("self_destruct", {})

    def test_doctor_and_connect_info(self):
        d = ctl.run("doctor", {})
        names = [c["name"] for c in d["checks"]]
        for n in ("python", "root", "engine installed", "tmux", "ssh server", "ervisio bridge", "policy"):
            self.assertIn(n, names)
        for c in d["checks"]:
            self.assertIn(c["level"], ("ok", "warn", "info"))
        info = ctl.run("connect_info", {})
        self.assertIn("port", info["ssh"])
        self.assertTrue(info["hostname"])
        self.assertTrue(any(u["name"] == "root" for u in info["users"]))
        self.assertIn("stdio", info["stdio_command"])

    def test_connect_snippets_come_from_the_connector(self):
        res = ctl.run("connect_snippets", {"mode": "ssh", "host": "vps.example.com", "user": "root", "name": "box"})
        by = {c["id"]: c for c in res["clients"]}
        for cid in ("claude-code", "codex", "cursor", "vscode", "kilo", "antigravity", "windsurf", "gemini-cli", "zed", "opencode", "claude-desktop", "cline", "roo"):
            self.assertIn(cid, by)
            self.assertTrue(by[cid]["where"] and by[cid]["body"], cid)
        self.assertTrue(by["claude-code"]["body"].startswith("claude mcp add-json box '"))
        self.assertEqual(by["claude-code"]["lang"], "shell")
        self.assertEqual(by["codex"]["lang"], "toml")
        self.assertIn("[mcp_servers.box]", by["codex"]["body"])
        self.assertIn("root@vps.example.com", by["cursor"]["body"])
        self.assertEqual(json.loads(by["vscode"]["body"])["servers"]["box"]["type"], "stdio")
        self.assertNotIn(self.home, json.dumps(res))     # paths are the ones on the person's computer, never this server's
        res = ctl.run("connect_snippets", {"mode": "http", "name": "web", "url": "https://x.example/mcp", "token": "eai_abc"})
        by = {c["id"]: c for c in res["clients"]}
        self.assertEqual(json.loads(by["windsurf"]["body"])["mcpServers"]["web"]["serverUrl"], "https://x.example/mcp")
        self.assertIn("Bearer eai_abc", by["cursor"]["body"])
        self.assertIn("npx", by["claude-desktop"]["body"])

    def test_doctor_warns_about_plain_http_on_the_network(self):
        ctl.run("install", {"service": False})
        config.save({"bind": "0.0.0.0", "port": free_port()})
        fake = {"installed": True, "active": False, "manager": "pidfile", "enabled": False, "pid": None}
        patcher = mock.patch.object(ctl, "_service_state", lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        d = ctl.run("doctor", {})
        self.assertTrue(any(c["name"] == "transport security" and not c["ok"] for c in d["checks"]))


class CommandLine(EngineCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_help_and_version(self):
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("serve", out)
        code, out = self.run_cli("version")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"ervisio-ai \d+\.\d+\.\d+")
        self.assertEqual(self.run_cli("bogus")[0], 2)

    def test_token_commands(self):
        code, out = self.run_cli("token", "create", "work-pc", "--scope", "readonly")
        self.assertEqual(code, 0)
        token = re.search(r"eai_\S+", out).group(0)
        self.assertIsNotNone(auth.verify(token))
        self.assertIn("work-pc", self.run_cli("token", "list")[1])
        self.assertEqual(self.run_cli("token", "revoke", "work-pc")[0], 0)
        self.assertIsNone(auth.verify(token))

    def test_policy_commands(self):
        self.run_cli("policy", "set", "guard", "false")
        self.assertFalse(policy.load()["guard"])
        self.assertIn('"guard": false', self.run_cli("policy")[1])

    def test_doctor_and_audit_commands(self):
        self.call("system_info")
        self.assertIn("system_info", self.run_cli("audit", "--limit", "5")[1])
        code, out = self.run_cli("doctor")
        self.assertIn("python", out)

    def test_tools_and_whoami_are_machine_readable(self):
        code, out = self.run_cli("tools")
        data = json.loads(out)
        self.assertGreater(len(data["tools"]), 40)
        self.assertEqual(data["policy"]["mode"], "full")
        who = json.loads(self.run_cli("whoami")[1])
        self.assertEqual(who["uid"], os.geteuid())
        self.assertTrue(who["data_dir"])


class Spool(EngineCase):
    """The Ervisio page writes a request file and runs `ervisio-ai call <id>`: a stream of JSON lines comes back."""

    def run_call(self, tool, args, rid="a" * 32, **extra):
        spool = paths.spool_dir()
        os.makedirs(spool, exist_ok=True)
        path = os.path.join(spool, rid + ".json")
        with open(path, "w") as f:
            json.dump(dict({"tool": tool, "args": args, "client": "ervisio-chat"}, **extra), f)
        import subprocess
        p = subprocess.run([sys.executable, "-B", ENGINE, "call", rid], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           env=dict(os.environ, ERVISIO_AI_HOME=self.home), timeout=60)
        self.assertFalse(os.path.exists(path), "the request file must be deleted once read")
        return [json.loads(l) for l in p.stdout.splitlines()], p

    def test_call_streams_progress_then_the_result(self):
        lines, p = self.run_call("shell_exec", {"command": "echo one; sleep 1; echo two"})
        self.assertEqual(p.returncode, 0)
        kinds = [l["t"] for l in lines]
        self.assertEqual(kinds[-1], "result")
        self.assertIn("progress", kinds)
        self.assertIn("two", lines[-1]["result"]["content"][0]["text"])

    def test_bad_and_missing_requests(self):
        import subprocess
        for rid in ("short", "../../etc/passwd", "g" * 32):
            p = subprocess.run([sys.executable, "-B", ENGINE, "call", rid], stdout=subprocess.PIPE, universal_newlines=True,
                               env=dict(os.environ, ERVISIO_AI_HOME=self.home))
            self.assertEqual(p.returncode, 2)
            self.assertEqual(json.loads(p.stdout)["t"], "error")
        lines, _ = self.run_call("nope", {}, "b" * 32)
        self.assertTrue(lines[-1]["result"]["isError"])

    def test_client_name_is_recorded(self):
        self.run_call("system_info", {})
        from ervisio_ai import audit
        self.assertEqual(audit.read(1)[0]["client"], "ervisio-chat")
        self.assertEqual(audit.read(1)[0]["transport"], "ervisio")

    def test_classify_says_whether_a_call_only_reads_and_runs_nothing(self):
        marker = self.path("must-not-exist")
        for tool, args, read_only in (
            ("shell_exec", {"command": "touch " + marker}, False),
            ("fs_list", {"path": self.work}, True),
            ("fs_write", {"path": marker, "content": "x"}, False),
            ("service_manage", {"action": "status", "unit": "x"}, True),
            ("service_manage", {"action": "restart", "unit": "x"}, False),
            ("docker_cli", {"args": ["ps"]}, True),
            ("docker_cli", {"args": ["rm", "web"]}, False),
            ("fs_read", {"path": 5}, True),     # invalid arguments still classify; the call itself will explain the error
        ):
            lines, _ = self.run_call(tool, args, "d" * 32, classify=True)
            self.assertEqual(lines[-1]["result"]["classify"], {"readOnly": read_only, "known": True}, (tool, args))
        self.assertFalse(os.path.exists(marker))
        lines, _ = self.run_call("nope", {}, "e" * 32, classify=True)
        self.assertEqual(lines[-1]["result"]["classify"], {"readOnly": False, "known": False})

    def test_ctl_request(self):
        spool = paths.spool_dir()
        os.makedirs(spool, exist_ok=True)
        with open(os.path.join(spool, "c" * 32 + ".json"), "w") as f:
            json.dump({"action": "token_list", "params": {}}, f)
        import subprocess
        p = subprocess.run([sys.executable, "-B", ENGINE, "ctl", "c" * 32], stdout=subprocess.PIPE, universal_newlines=True,
                           env=dict(os.environ, ERVISIO_AI_HOME=self.home))
        self.assertEqual(json.loads(p.stdout)["result"], {"tokens": []})

    def test_call_inline_reruns_a_tool_from_stdin(self):
        import subprocess
        p = subprocess.run([sys.executable, "-B", ENGINE, "call-inline"], input=json.dumps({"tool": "shell_exec", "args": {"command": "echo inline"}}),
                           stdout=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, ERVISIO_AI_HOME=self.home, ERVISIO_AI_NOAUDIT="1"))
        self.assertIn("inline", json.loads(p.stdout)["content"][0]["text"])
        from ervisio_ai import audit
        self.assertEqual(audit.read(5), [])  # the parent process already logged this call


class RunAsRoot(EngineCase):
    """The sudo re-execution path, with a fake `sudo` that just drops its own flags."""

    def test_elevatable_tool_is_rerun_through_sudo(self):
        bindir = os.path.join(self.home, "bin")
        os.makedirs(bindir)
        marker = os.path.join(self.home, "sudo-was-used")
        with open(os.path.join(bindir, "sudo"), "w") as f:
            f.write('#!/bin/sh\ntouch "%s"\nwhile [ "$1" = "-n" ] || [ "$1" = "--" ]; do shift; done\nexec "$@"\n' % marker)
        os.chmod(os.path.join(bindir, "sudo"), 0o755)
        p = self.write("f.txt", "content\n")
        if os.geteuid() == 0:
            from ervisio_ai import registry
            real = os.geteuid
            os.geteuid = lambda: 1000   # pretend to be a normal user so the sudo path is taken
            try:
                old_path = os.environ["PATH"]
                os.environ["PATH"] = bindir + ":" + old_path
                r = registry.call_tool("fs_read", {"path": p, "sudo": True}, registry.Ctx(client="t"))
            finally:
                os.geteuid = real
                os.environ["PATH"] = old_path
            self.assertFalse(r["isError"], r)
            self.assertIn("content", r["content"][0]["text"])
            self.assertTrue(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
