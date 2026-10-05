import http.server
import json
import os
import shutil
import socket
import threading
import unittest

from helpers import EngineCase

from ervisio_ai import proc


class Terminal(EngineCase):
    def setUp(self):
        super().setUp()
        if not shutil.which("tmux"):
            self.skipTest("tmux is not installed")
        self.addCleanup(self._kill_server)

    def _kill_server(self):
        from ervisio_ai.tools import terminal
        proc.run(["tmux", "-S", terminal.socket_path(), "kill-server"], timeout=10)

    def test_open_type_read_close(self):
        out = self.ok("terminal_open", name="t1", cwd="/tmp")
        self.assertIn("tmux -S", out)
        self.assertIn("attach -t eai-t1", out)
        out = self.ok("terminal_send", id="t1", text="echo marker-$((6*7))", enter=True, wait_for="marker-42")
        self.assertIn("marker-42", out)
        self.assertIn("running", out)
        self.assertIn("t1", self.ok("terminal_list"))
        self.assertIn("marker-42", self.ok("terminal_read", id="t1"))
        self.assertIn("Closed", self.ok("terminal_close", id="t1"))
        self.assertIn("No terminals", self.ok("terminal_list"))

    def test_interactive_program_and_keys(self):
        self.ok("terminal_open", name="py", command="python3 -q")
        out = self.ok("terminal_send", id="py", text="20 + 22", enter=True, wait_for=r"^42", wait_ms=8000)
        self.assertIn("42", out)
        self.ok("terminal_send", id="py", text="input('name? ')", enter=True, wait_for="name\\?")
        out = self.ok("terminal_send", id="py", text="world", keys=["Enter"], wait_for="'world'")
        self.assertIn("'world'", out)
        self.ok("terminal_send", id="py", keys=["C-d"], wait_ms=1500)
        self.assertIn("exited", self.ok("terminal_read", id="py"))   # remain-on-exit keeps the last screen

    def test_default_name_duplicate_and_unknown(self):
        self.assertIn("'t1'", self.ok("terminal_open"))
        self.assertIn("'t2'", self.ok("terminal_open"))
        self.assertIn("already exists", self.err("terminal_open", name="t1"))
        self.assertIn("no terminal", self.err("terminal_read", id="ghost").lower())
        self.assertIn("not a terminal id", self.err("terminal_read", id="bad name;"))
        self.assertIn("not a key name", self.err("terminal_send", id="t1", keys=["rm -rf"]))

    def test_resize_and_scrollback(self):
        self.ok("terminal_open", name="big", cols=100, rows=20)
        self.assertIn("Resized", self.ok("terminal_resize", id="big", cols=120, rows=30))
        self.ok("terminal_send", id="big", text="seq 1 100", enter=True, wait_for="100")
        out = self.ok("terminal_read", id="big", lines=150)
        self.assertIn("\n1\n", out)
        self.assertIn("\n100", out)

    def test_ctrl_c_stops_a_running_program(self):
        self.ok("terminal_open", name="c")
        self.ok("terminal_send", id="c", text="sleep 300", enter=True, wait_ms=500)
        out = self.ok("terminal_read", id="c")
        self.assertIn("sleep", out)
        out = self.ok("terminal_send", id="c", keys=["C-c"], wait_ms=1500)
        self.assertNotIn("running: sleep", out)


class System(EngineCase):
    def test_info(self):
        out = self.ok("system_info")
        for word in ("os:", "cpu:", "memory:", "disk /"):
            self.assertIn(word, out)

    def test_processes(self):
        out = self.ok("system_processes", limit=5)
        self.assertIn("PID", out)
        self.assertLessEqual(len(out.splitlines()), 8)
        self.assertIn("python", self.ok("system_processes", filter="python", limit=3))
        self.assertIn("regular expression", self.err("system_processes", filter="("))

    def test_process_signal(self):
        p = __import__("subprocess").Popen(["sleep", "60"])
        self.addCleanup(p.kill)
        self.assertIn("Sent TERM", self.ok("process_signal", pid=p.pid))
        p.wait(timeout=5)
        self.assertIn("Give either", self.err("process_signal"))
        self.assertIn("No process is named", self.err("process_signal", name="no-such-process-xyz"))
        self.assertIn("this server", self.err("process_signal", pid=os.getpid()))

    def test_network(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        self.assertIn(":%d" % port, self.ok("system_network", what="listening"))
        for what in ("interfaces", "routes", "dns", "firewall", "connections"):
            self.ok("system_network", what=what)

    def test_network_fallbacks_read_proc_without_ss_and_ip(self):
        from ervisio_ai.tools import system
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        out = system.proc_sockets()
        self.assertIn("127.0.0.1:%d" % port, out)
        self.assertIn("python", out)  # the owning process is named
        cli = socket.create_connection(("127.0.0.1", port))
        self.addCleanup(cli.close)
        self.assertIn("127.0.0.1:%d" % port, system.proc_sockets(established=True))
        self.assertIn("lo", system.proc_interfaces())
        self.assertIn("127.0.0.1", system.proc_interfaces())
        self.assertTrue(system.proc_routes())
        self.assertEqual(system._hex_addr("0100007F"), "127.0.0.1")
        self.assertEqual(system._hex_addr("00000000000000000000000001000000"), "::1")

    def test_logs_from_a_file(self):
        p = self.write("app.log", "".join("line %d %s\n" % (i, "ERROR" if i % 10 == 0 else "ok") for i in range(1, 101)))
        out = self.ok("system_logs", file=p, lines=3)
        self.assertIn("line 100", out)
        self.assertNotIn("line 97", out)
        out = self.ok("system_logs", file=p, grep="error", lines=50)
        self.assertEqual(out.count("ERROR"), 10)
        self.assertIn("does not exist", self.err("system_logs", file=self.path("none.log")))

    def test_journal_without_systemd_is_explained(self):
        if shutil.which("journalctl"):
            self.skipTest("journalctl exists")
        self.assertIn("journalctl", self.err("system_logs", unit="nginx"))


class Services(EngineCase):
    def test_without_systemd_the_error_says_what_to_do(self):
        if os.path.isdir("/run/systemd/system"):
            self.skipTest("systemd is running")
        self.assertIn("systemd is not running", self.err("service_manage", action="list"))

    def test_unit_name_is_validated(self):
        if not os.path.isdir("/run/systemd/system"):
            self.skipTest("needs systemd for the error order")
        self.assertIn("unit is required", self.err("service_manage", action="start", unit="a b; rm"))

    def test_guard_on_ssh(self):
        # guarded before systemd is even consulted
        self.assertIn("Safety guard", self.err("service_manage", action="stop", unit="sshd"))
        self.assertIn("Safety guard", self.err("service_manage", action="disable", unit="ssh.service"))

    def test_cron_roundtrip(self):
        if not shutil.which("crontab"):
            self.skipTest("no crontab")
        self.assertIn("schedule must be", self.err("cron_manage", action="add", schedule="every day", command="true"))
        self.ok("cron_manage", action="add", schedule="*/5 * * * *", command="echo eai-test-job")
        self.addCleanup(lambda: self.call("cron_manage", action="remove", match="eai-test-job"))
        self.assertIn("eai-test-job", self.ok("cron_manage", action="list"))
        self.assertIn("Removed 1", self.ok("cron_manage", action="remove", match="eai-test-job"))
        self.assertIn("No crontab line", self.err("cron_manage", action="remove", match="eai-test-job"))


class Packages(EngineCase):
    def test_detect_and_list(self):
        from ervisio_ai.tools import packages
        pm = packages.detect()
        if pm is None:
            self.skipTest("no package manager")
        out = self.ok("package_manage", action="list", query="bash")
        self.assertIn("bash", out)

    def test_validation(self):
        self.assertIn("not a valid package name", self.err("package_manage", action="install", packages=["x; rm -rf /"]))
        self.assertIn("packages is required", self.err("package_manage", action="install"))
        self.assertIn("query is required", self.err("package_manage", action="search"))

    def test_command_lines(self):
        from ervisio_ai.tools.packages import build
        self.assertEqual(build("apt-get", "install", ["nginx"], "", False)[-2:], ["install", "nginx"])
        self.assertIn("-y", build("dnf", "install", ["nginx"], "", False))
        self.assertIn("--noconfirm", build("pacman", "install", ["nginx"], "", False))
        self.assertEqual(build("apk", "install", ["nginx"], "", False), ["apk", "add", "nginx"])
        self.assertIn("purge", build("apt-get", "remove", ["nginx"], "", True))
        self.assertEqual(build("pacman", "autoremove", [], "", False), [])


class Docker(EngineCase):
    def test_missing_docker_is_explained(self):
        if shutil.which("docker"):
            self.skipTest("docker exists")
        self.assertIn("not installed", self.err("docker_cli", args=["ps"]))

    def test_read_only_detection(self):
        from ervisio_ai.tools.docker import _is_read_only
        for a in (["ps"], ["ps", "-a"], ["logs", "--tail", "5", "web"], ["compose", "ps"], ["container", "ls"], ["system", "df"], ["images"], ["inspect", "x"]):
            self.assertTrue(_is_read_only({"args": a}), a)
        for a in (["rm", "web"], ["run", "x"], ["compose", "up", "-d"], ["system", "prune", "-f"], ["exec", "web", "sh"], ["container", "rm", "x"], []):
            self.assertFalse(_is_read_only({"args": a}), a)

    def test_prune_all_is_guarded(self):
        if not shutil.which("docker"):
            self.skipTest("docker missing: the guard sits after the lookup")
        self.assertIn("Safety guard", self.err("docker_cli", args=["system", "prune", "-a", "-f"]))


class Network(EngineCase):
    def setUp(self):
        super().setUp()

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                    self.end_headers()
                    return
                code = 404 if self.path == "/missing" else 200
                body = b"BIN\x00\x01" if self.path == "/bin" else json.dumps({"path": self.path, "ua": self.headers.get("User-Agent"),
                                                                              "x": self.headers.get("X-Test")}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(n)
                body = json.dumps({"got": data.decode(), "ctype": self.headers.get("Content-Type")}).encode()
                self.send_response(201)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.port

    def test_get_with_headers(self):
        out = self.ok("net_request", url=self.base + "/hello", headers={"X-Test": "yes"})
        self.assertIn("200", out)
        self.assertIn('"x": "yes"', out)
        self.assertIn("ervisio-ai/", out)

    def test_status_codes_and_redirects(self):
        self.assertIn("404", self.err("net_request", url=self.base + "/missing"))
        self.assertIn('"path": "/ok"', self.ok("net_request", url=self.base + "/redirect"))
        out = self.ok("net_request", url=self.base + "/redirect", follow_redirects=False)  # a 3xx is a result, not an error
        self.assertIn("302", out)
        self.assertIn("location: /ok", out)

    def test_post_json_and_body(self):
        out = self.ok("net_request", url=self.base + "/x", method="POST", json={"a": 1})
        self.assertIn("201", out)
        self.assertIn("application/json", out)
        self.assertIn('{\\"a\\": 1}', out)
        self.assertIn("raw text", self.ok("net_request", url=self.base + "/x", method="POST", body="raw text"))

    def test_binary_and_save_to(self):
        self.assertIn("binary body", self.ok("net_request", url=self.base + "/bin"))
        dest = self.path("dl", "file.bin")
        self.assertIn("saved", self.ok("net_request", url=self.base + "/bin", save_to=dest))
        self.assertEqual(self.read(dest, "rb"), b"BIN\x00\x01")

    def test_errors(self):
        self.assertIn("must start with http", self.err("net_request", url="file:///etc/passwd"))
        self.assertIn("failed", self.err("net_request", url="http://127.0.0.1:1/", timeout=3))

    def test_net_check(self):
        out = self.ok("net_check", host="127.0.0.1", port=self.port, ping=False)
        self.assertIn("open", out)
        self.assertIn("FAILED", self.ok("net_check", host="127.0.0.1", port=1, ping=False))
        self.assertIn("does not resolve", self.err("net_check", host="no-such-host.invalid"))
        self.assertIn("not a host name", self.err("net_check", host="a b;c"))

    def test_ssh_exec_validation(self):
        self.assertIn("not a host name", self.err("ssh_exec", host="-oProxyCommand=x", command="id"))
        self.assertIn("Safety guard", self.err("ssh_exec", host="example.com", command="reboot"))


class Firewall(EngineCase):
    def test_validation(self):
        self.assertIn("port is required", self.err("firewall_manage", action="allow"))
        self.assertIn("source must be", self.err("firewall_manage", action="allow", port="443/tcp", source="1.2.3.4; id"))

    def test_status_never_fails(self):
        self.ok("firewall_manage", action="status")


class Meta(EngineCase):
    def test_server_context_and_memory(self):
        out = self.ok("server_context")
        self.assertIn("Ervisio AI", out)
        self.assertIn("available:", out)
        self.assertIn("empty", out)
        self.assertIn("empty", self.ok("memory_read"))
        self.ok("memory_write", text="nginx lives in /etc/nginx")
        self.ok("memory_write", text="deploys use docker compose")
        mem = self.ok("memory_read")
        self.assertIn("nginx lives", mem)
        self.assertIn("docker compose", mem)
        self.assertIn("nginx lives", self.ok("server_context"))
        self.ok("memory_write", text="# fresh", mode="replace")
        self.assertEqual(self.ok("memory_read").strip(), "# fresh")
        self.assertIn("too long", self.err("memory_write", text="x" * 300000))


if __name__ == "__main__":
    unittest.main()
