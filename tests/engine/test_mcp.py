import http.client
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import unittest

from helpers import ENGINE, EngineCase
from mcpproc import Stdio, free_port

from ervisio_ai import auth


class StdioProtocol(EngineCase):
    def setUp(self):
        super().setUp()
        self.c = Stdio(self.home)
        self.addCleanup(self.c.close)

    def test_initialize_negotiates_the_version(self):
        r = self.c.initialize("2025-06-18")["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(r["serverInfo"]["name"], "ervisio-ai")
        for cap in ("tools", "resources", "prompts"):
            self.assertIn(cap, r["capabilities"])
        self.assertIn("server_context", r["instructions"])
        self.assertEqual(Stdio(self.home).initialize("2024-11-05")["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(Stdio(self.home).initialize("2099-01-01")["result"]["protocolVersion"], "2025-06-18")

    def test_tools_list_has_schemas_and_annotations(self):
        self.c.initialize()
        tools = self.c.request("tools/list")["result"]["tools"]
        self.assertGreaterEqual(len(tools), 40)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
            self.assertTrue(t["description"], t["name"])
            self.assertIn("readOnlyHint", t["annotations"])
        names = {t["name"] for t in tools}
        for must in ("shell_exec", "fs_read", "fs_edit", "terminal_send", "ervisio_call", "service_manage", "package_manage", "server_context"):
            self.assertIn(must, names)

    def test_call_and_error_shapes(self):
        self.c.initialize()
        r = self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "echo hi"}})["result"]
        self.assertFalse(r["isError"])
        self.assertIn("hi", r["content"][0]["text"])
        r = self.c.request("tools/call", {"name": "fs_read", "arguments": {"path": "/nonexistent"}})["result"]
        self.assertTrue(r["isError"])
        r = self.c.request("tools/call", {"name": "shell_exec"})
        self.assertTrue(r["result"]["isError"])  # a missing argument is a tool error the model can fix, not a protocol error
        r = self.c.request("tools/call", {})
        self.assertEqual(r["error"]["code"], -32602)

    def test_protocol_errors(self):
        self.c.initialize()
        self.assertEqual(self.c.request("no/such/method")["error"]["code"], -32601)
        self.c.send("{not json")
        self.assertEqual(self.c.recv(None)["error"]["code"], -32700)
        self.c.send({"hello": "world"})
        self.assertEqual(self.c.recv(None)["error"]["code"], -32600)
        self.assertEqual(self.c.request("ping")["result"], {})

    def test_notifications_get_no_answer(self):
        self.c.initialize()
        self.c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.c.send({"jsonrpc": "2.0", "method": "notifications/whatever"})
        self.assertEqual(self.c.request("ping")["result"], {})  # the next answer is for ping, nothing came before it

    def test_concurrent_calls_finish_out_of_order(self):
        self.c.initialize()
        slow = self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "sleep 2; echo slow"}}, wait=False)
        t0 = time.time()
        fast = self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "echo fast"}})
        self.assertLess(time.time() - t0, 1.5)
        self.assertIn("fast", fast["result"]["content"][0]["text"])
        self.assertIn("slow", self.c.recv(slow)["result"]["content"][0]["text"])

    def test_progress_notifications(self):
        self.c.initialize()
        mid = self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "echo one; sleep 1; echo two"},
                                              "_meta": {"progressToken": "tok1"}}, wait=False)
        seen = []
        while True:
            m = self.c.recv(None)
            if m.get("method") == "notifications/progress":
                self.assertEqual(m["params"]["progressToken"], "tok1")
                seen.append(m["params"]["message"])
            elif m.get("id") == mid:
                break
        self.assertTrue(any("one" in s for s in seen), seen)

    def test_cancellation_kills_the_command(self):
        self.c.initialize()
        mid = self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "sleep 60", "timeout": 120}}, wait=False)
        time.sleep(0.7)
        self.c.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": mid}})
        t0 = time.time()
        r = self.c.recv(mid)
        self.assertLess(time.time() - t0, 5)
        self.assertIn("cancelled", r["result"]["content"][0]["text"])

    def test_resources_and_prompts(self):
        self.c.initialize()
        uris = [r["uri"] for r in self.c.request("resources/list")["result"]["resources"]]
        self.assertIn("ervisio-ai://memory", uris)
        body = self.c.request("resources/read", {"uri": "ervisio-ai://system"})["result"]["contents"][0]
        self.assertIn("cpu:", body["text"])
        self.assertEqual(self.c.request("resources/read", {"uri": "ervisio-ai://nope"})["error"]["code"], -32002)
        prompts = {p["name"] for p in self.c.request("prompts/list")["result"]["prompts"]}
        self.assertTrue({"server-audit", "debug-service", "deploy-app", "harden-ssh"} <= prompts)
        p = self.c.request("prompts/get", {"name": "setup-reverse-proxy", "arguments": {"domain": "a.example.com", "upstream": "127.0.0.1:3000"}})["result"]
        self.assertIn("a.example.com", p["messages"][0]["content"]["text"])
        self.assertIn("127.0.0.1:3000", p["messages"][0]["content"]["text"])
        self.assertEqual(self.c.request("prompts/get", {"name": "nope"})["error"]["code"], -32602)

    def test_policy_hides_tools_from_the_list(self):
        from ervisio_ai import policy
        policy.save({"disabled_categories": ["docker", "network"]})
        c = Stdio(self.home)
        self.addCleanup(c.close)
        c.initialize()
        names = {t["name"] for t in c.request("tools/list")["result"]["tools"]}
        self.assertNotIn("docker_cli", names)
        self.assertNotIn("net_request", names)
        self.assertIn("shell_exec", names)

    def test_exits_when_the_client_goes_away_and_kills_children(self):
        self.c.initialize()
        self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "sleep 77"}}, wait=False)
        time.sleep(0.8)
        t0 = time.time()
        self.assertEqual(self.c.close(), 0)
        self.assertLess(time.time() - t0, 8)
        for _ in range(30):  # the group gets SIGTERM; give the kernel a moment under load
            if subprocess.run(["pgrep", "-f", "[s]leep 77"], stdout=subprocess.PIPE).stdout.strip() == b"":
                break
            time.sleep(0.1)
        else:
            self.fail("the command outlived the connection")

    def test_stray_output_cannot_corrupt_the_protocol(self):
        # a command that prints to the engine's own stdout would break stdio framing if fd 1 were not protected
        self.c.initialize()
        r = self.c.request("tools/call", {"name": "shell_exec", "arguments": {"command": "echo '{\"jsonrpc\": \"2.0\"}'"}})
        self.assertIn("jsonrpc", r["result"]["content"][0]["text"])


class HttpTransport(EngineCase):
    def setUp(self):
        super().setUp()
        self.port = free_port()
        self.full = auth.create("laptop", "full")["token"]
        self.ro = auth.create("viewer", "readonly")["token"]
        env = dict(os.environ, ERVISIO_AI_HOME=self.home)
        self.srv = subprocess.Popen([sys.executable, "-B", ENGINE, "serve", "--port", str(self.port)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self._stop)
        for _ in range(50):
            try:
                self.req("GET", "/healthz")
                break
            except OSError:
                time.sleep(0.1)

    def _stop(self):
        self.srv.terminate()
        try:
            self.srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.srv.kill()
        self.srv.stdout.close()
        self.srv.stderr.close()

    def req(self, method, path, body=None, token=None, headers=None, raw=False, host=None, conn=None):
        c = conn or http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        if host:
            h["Host"] = host
        data = body if isinstance(body, (bytes, str)) else (json.dumps(body) if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        if conn is None:
            c.close()
        if raw:
            return r, payload
        return r, (json.loads(payload) if payload else None)

    def rpc(self, method, params=None, token=None, sid=None, mid=1, headers=None):
        h = dict(headers or {})
        if sid:
            h["Mcp-Session-Id"] = sid
        return self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}, token or self.full, h)

    def init(self, token=None):
        r, body = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "raw-http"}}, token)
        return r.getheader("Mcp-Session-Id"), body

    def test_health_is_public_and_says_nothing_secret(self):
        r, body = self.req("GET", "/healthz")
        self.assertEqual(r.status, 200)
        self.assertEqual(set(body), {"ok", "name", "version"})

    def test_authentication(self):
        for tok, expect in ((None, 401), ("eai_" + "x" * 43, 401), ("not-a-token", 401), (self.full, 200)):
            r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, tok)
            self.assertEqual(r.status, expect, tok)
        r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, None)
        self.assertIn("Bearer", r.getheader("WWW-Authenticate"))
        r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, None, {"X-Ervisio-Token": self.full})
        self.assertEqual(r.status, 200)

    def test_revoking_a_token_takes_effect_at_once(self):
        t = auth.create("temp")
        r, _ = self.rpc("ping", token=t["token"])
        self.assertEqual(r.status, 200)
        auth.revoke(t["id"])
        r, _ = self.rpc("ping", token=t["token"])
        self.assertEqual(r.status, 401)

    def test_wrong_tokens_are_throttled(self):
        for _ in range(10):
            self.req("POST", "/mcp", {}, "eai_" + "y" * 43)
        r, _ = self.req("POST", "/mcp", {}, "eai_" + "y" * 43)
        self.assertEqual(r.status, 429)
        self.assertTrue(r.getheader("Retry-After"))
        r, _ = self.rpc("ping")  # even the right token waits: the address is blocked
        self.assertEqual(r.status, 429)

    def test_browsers_and_dns_rebinding_are_refused(self):
        r, _ = self.rpc("ping", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, self.full, host="evil.example:%d" % self.port)
        self.assertEqual(r.status, 403)
        r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, self.full, host="localhost:%d" % self.port)
        self.assertEqual(r.status, 200)
        r, _ = self.req("OPTIONS", "/mcp")
        self.assertEqual(r.status, 403)

    def test_sessions(self):
        sid, body = self.init()
        self.assertTrue(sid)
        self.assertEqual(body["result"]["serverInfo"]["name"], "ervisio-ai")
        r, body = self.rpc("tools/list", sid=sid)
        self.assertEqual(r.status, 200)
        r, body = self.rpc("tools/list", sid="0" * 32)
        self.assertEqual(r.status, 404)  # clients start over with initialize
        r, _ = self.req("DELETE", "/mcp", token=self.full, headers={"Mcp-Session-Id": sid})
        self.assertEqual(r.status, 204)
        r, _ = self.rpc("tools/list", sid=sid)
        self.assertEqual(r.status, 404)

    def test_works_without_a_session_for_stateless_clients(self):
        r, body = self.rpc("tools/list")
        self.assertEqual(r.status, 200)
        self.assertGreater(len(body["result"]["tools"]), 30)

    def test_notifications_are_accepted_with_202(self):
        r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, self.full, raw=True)[0], None
        self.assertEqual(r.status, 202)

    def test_batches(self):
        r, body = self.req("POST", "/mcp", [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "no/such"}], self.full)
        self.assertEqual(r.status, 200)
        self.assertEqual([m["id"] for m in body], [1, 2])
        self.assertIn("error", body[1])

    def test_bad_requests(self):
        r, _ = self.req("POST", "/mcp", "{broken", self.full)
        self.assertEqual(r.status, 400)
        r, _ = self.req("POST", "/mcp", [], self.full)
        self.assertEqual(r.status, 400)
        r, _ = self.req("POST", "/elsewhere", {}, self.full)
        self.assertEqual(r.status, 404)
        r, _ = self.req("GET", "/mcp", token=self.full)
        self.assertEqual(r.status, 405)

    def test_oversized_body_is_refused_without_reading_it(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", "/mcp")
        c.putheader("Authorization", "Bearer " + self.full)
        c.putheader("Content-Length", str(64 * 1024 * 1024))
        c.putheader("Content-Type", "application/json")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()

    def test_scope_is_decided_by_the_token(self):
        sid, _ = self.init(self.ro)
        r, body = self.rpc("tools/call", {"name": "shell_exec", "arguments": {"command": "echo no"}}, token=self.ro, sid=sid)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("read-only", body["result"]["content"][0]["text"])
        r, body = self.rpc("tools/call", {"name": "fs_list", "arguments": {"path": "/etc", "limit": 3}}, token=self.ro, sid=sid)
        self.assertFalse(body["result"]["isError"])
        # a session id from a full token does not give a read-only token more rights
        full_sid, _ = self.init(self.full)
        r, body = self.rpc("tools/call", {"name": "shell_exec", "arguments": {"command": "echo no"}}, token=self.ro, sid=full_sid)
        self.assertTrue(body["result"]["isError"])

    def test_long_call_becomes_an_event_stream_with_keepalive(self):
        t0 = time.time()
        r, raw = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                            "params": {"name": "shell_exec", "arguments": {"command": "sleep 7; echo late"}}}, self.full, raw=True)
        self.assertGreater(time.time() - t0, 6.5)  # past the 5 s mark the answer comes as a stream
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        text = raw.decode()
        self.assertIn("event: message", text)
        self.assertIn(": keepalive", text)
        data = [l[6:] for l in text.splitlines() if l.startswith("data: ")]
        self.assertIn("late", json.loads(data[-1])["result"]["content"][0]["text"])

    def test_progress_token_gets_an_event_stream_from_the_start(self):
        r, raw = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": "shell_exec", "arguments": {"command": "echo a; sleep 1; echo b"}, "_meta": {"progressToken": 99}}}, self.full, raw=True)
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        msgs = [json.loads(l[6:]) for l in raw.decode().splitlines() if l.startswith("data: ")]
        self.assertEqual(msgs[-1]["id"], 6)
        self.assertTrue(any(m.get("method") == "notifications/progress" and m["params"]["progressToken"] == 99 for m in msgs))

    def test_cancellation_over_http(self):
        sid, _ = self.init()
        import threading
        out = {}

        def long():
            out["r"] = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                                  "params": {"name": "shell_exec", "arguments": {"command": "sleep 60", "timeout": 120}}},
                                self.full, {"Mcp-Session-Id": sid})

        th = threading.Thread(target=long)
        th.start()
        time.sleep(1)
        r, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7}}, self.full,
                        {"Mcp-Session-Id": sid}, raw=True)
        self.assertEqual(r.status, 202)
        th.join(10)
        self.assertFalse(th.is_alive())
        self.assertIn("cancelled", out["r"][1]["result"]["content"][0]["text"])

    def test_client_disconnect_cancels_the_command(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "shell_exec", "arguments": {"command": "sleep 55", "timeout": 120}, "_meta": {"progressToken": 1}}}),
            {"Authorization": "Bearer " + self.full, "Content-Type": "application/json"})
        time.sleep(1)
        self.assertNotEqual(subprocess.run(["pgrep", "-f", "[s]leep 55"], stdout=subprocess.PIPE).stdout.strip(), b"")
        c.sock.close()
        for _ in range(20):
            time.sleep(0.25)
            if subprocess.run(["pgrep", "-f", "[s]leep 55"], stdout=subprocess.PIPE).stdout.strip() == b"":
                break
        else:
            self.fail("the command kept running after the client went away")

    def test_audit_names_the_client_and_token(self):
        sid, _ = self.init()
        self.rpc("tools/call", {"name": "system_info"}, sid=sid)
        from ervisio_ai import audit
        e = audit.read(1)[0]
        self.assertEqual(e["tool"], "system_info")
        self.assertIn("raw-http", e["client"])
        self.assertIn("laptop", e["client"])
        self.assertEqual(e["transport"], "http")
        self.assertEqual(e["ip"], "127.0.0.1")


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed for a self-signed certificate")
class HttpsTransport(EngineCase):
    def test_self_signed_https(self):
        port = free_port()
        token = auth.create("laptop")["token"]
        env = dict(os.environ, ERVISIO_AI_HOME=self.home)
        srv = subprocess.Popen([sys.executable, "-B", ENGINE, "serve", "--port", str(port), "--tls", "self-signed"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (srv.terminate(), srv.wait(10), srv.stdout.close(), srv.stderr.close()))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        for _ in range(60):
            try:
                c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
                c.request("GET", "/healthz")
                self.assertEqual(c.getresponse().status, 200)
                break
            except (OSError, ssl.SSLError):
                time.sleep(0.2)
        else:
            self.fail("https server did not come up")
        c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
        c.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), {"Authorization": "Bearer " + token})
        self.assertEqual(c.getresponse().status, 200)
        self.assertTrue(os.path.exists(os.path.join(self.home, "tls", "server.crt")))
        self.assertEqual(oct(os.stat(os.path.join(self.home, "tls", "server.key")).st_mode & 0o777), "0o600")
        plain = http.client.HTTPConnection("127.0.0.1", port, timeout=5)  # plain HTTP to a TLS port must not hang the server
        try:
            plain.request("GET", "/healthz")
            plain.getresponse()
        except (OSError, http.client.HTTPException):
            pass
        c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
        c.request("GET", "/healthz")
        self.assertEqual(c.getresponse().status, 200)


if __name__ == "__main__":
    unittest.main()
