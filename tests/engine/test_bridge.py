"""Against a real ervisio-bridge. Set ERVISIO_BRIDGE to its path (CI builds it from Ervisio/ervisio)."""
import json
import os
import unittest

from helpers import EngineCase

from ervisio_ai import bridge

BRIDGE = os.environ.get("ERVISIO_BRIDGE")


@unittest.skipUnless(BRIDGE and os.access(BRIDGE, os.X_OK), "ERVISIO_BRIDGE is not set")
class RealBridge(EngineCase):
    def tearDown(self):
        bridge.shutdown()

    def test_hello_lists_methods_and_streams(self):
        b = bridge.get_bridge()
        self.assertTrue(b.alive)
        self.assertGreater(len(b.hello["methods"]), 50)
        self.assertEqual(b.hello["admin"], os.geteuid() == 0)
        self.assertEqual(b.level("services.list"), "user")
        self.assertTrue(b.is_stream("system.metricsStream"))

    def test_call_and_error_mapping(self):
        b = bridge.get_bridge()
        host = b.call("system.host")
        self.assertTrue(host["hostname"])
        with self.assertRaises(bridge.BridgeError) as e:
            b.call("no.such.method")
        self.assertEqual(e.exception.code, "not_found")
        with self.assertRaises(bridge.BridgeError) as e:
            b.call("files.list", {"path": "/definitely/not/here"})
        self.assertIn(e.exception.code, ("not_found", "invalid"))

    def test_stream_sampling(self):
        events, ended = bridge.get_bridge().stream("system.metricsStream", {"interval": 250}, seconds=2, max_events=3)
        self.assertGreaterEqual(len(events), 2)
        self.assertIn("cpu", events[0])
        self.assertFalse(ended)

    def test_concurrent_calls_do_not_mix_up_answers(self):
        import threading
        b = bridge.get_bridge()
        out = {}

        def work(i):
            out[i] = b.call("files.list", {"path": "/etc"}) if i % 2 else b.call("system.host")

        threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        for i in range(12):
            self.assertEqual("hostname" in out[i], i % 2 == 0, i)

    def test_bridge_is_restarted_after_it_dies(self):
        b = bridge.get_bridge()
        b.proc.kill()
        b.proc.wait()
        self.assertFalse(bridge.get_bridge() is b)
        self.assertTrue(bridge.get_bridge().call("system.host")["hostname"])

    def test_a_dead_bridge_fails_calls_fast(self):
        b = bridge.get_bridge()
        b.proc.kill()
        with self.assertRaises(bridge.BridgeError):
            b.call("system.host", timeout=5)

    def test_tools(self):
        out = self.ok("ervisio_status")
        self.assertIn("bridge version", out)
        self.assertIn("methods", out)
        out = self.ok("ervisio_methods", filter="terminal")
        self.assertIn("terminal.create", out)
        self.assertNotIn("services.list", out)
        self.assertIn("hostname", self.ok("ervisio_call", method="system.host"))
        self.assertIn("event", self.ok("ervisio_call", method="system.metricsStream", seconds=1, max_events=1))
        self.assertIn("no method", self.err("ervisio_call", method="nope.nothing"))
        self.assertIn("no-such-plugin", self.err("ervisio_plugin_exec", plugin="no-such-plugin", command="x"))

    def test_terminal_methods_through_the_bridge(self):
        sid = bridge.get_bridge().call("terminal.create", {"cols": 80, "rows": 24, "name": "bridge-test"})["id"]
        try:
            sessions = json.loads(self.ok("ervisio_call", method="terminal.list")).get("sessions", [])
            self.assertIn(sid, [s["id"] for s in sessions])
        finally:
            bridge.get_bridge().call("terminal.kill", {"id": sid})


class WithoutBridge(EngineCase):
    def test_tools_explain_when_ervisio_is_missing(self):
        old = os.environ.get("ERVISIO_BRIDGE")
        os.environ["ERVISIO_BRIDGE"] = "/nonexistent"
        real_candidates = bridge.CANDIDATES
        bridge.CANDIDATES = []
        real_find = bridge.find_bridge
        bridge.find_bridge = lambda: None
        try:
            from ervisio_ai.tools import ervisio
            ervisio.find_bridge = lambda: None
            self.assertIn("not installed", self.ok("ervisio_status"))
            self.assertIn("not found", self.err("ervisio_call", method="system.host"))
        finally:
            bridge.CANDIDATES = real_candidates
            bridge.find_bridge = real_find
            ervisio.find_bridge = real_find
            if old is None:
                os.environ.pop("ERVISIO_BRIDGE", None)
            else:
                os.environ["ERVISIO_BRIDGE"] = old
            bridge.shutdown()


if __name__ == "__main__":
    unittest.main()
