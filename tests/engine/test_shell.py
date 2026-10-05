import os
import subprocess
import threading
import time
import unittest

from helpers import EngineCase

from ervisio_ai import proc, registry
from ervisio_ai.registry import Ctx


class ShellExec(EngineCase):
    def test_output_exit_code_and_stderr(self):
        r = self.call("shell_exec", command="echo out; echo err >&2; exit 3")
        self.assertTrue(r["isError"])
        t = self.text(r)
        self.assertIn("exit 3", t)
        self.assertIn("out", t)
        self.assertIn("[stderr]\nerr", t)
        self.assertEqual(r["structuredContent"]["exit_code"], 3)

    def test_success_is_not_an_error(self):
        out = self.ok("shell_exec", command="echo hello && pwd", cwd="/tmp")
        self.assertIn("hello", out)
        self.assertIn("/tmp", out)
        self.assertIn("(no output)", self.ok("shell_exec", command="true"))

    def test_pipes_quotes_and_env(self):
        self.assertIn("A B", self.ok("shell_exec", command="echo \"$X $Y\" | tr a-z A-Z", env={"X": "a", "Y": "b"}))

    def test_stdin(self):
        self.assertIn("fed text", self.ok("shell_exec", command="cat", stdin="fed text"))

    def test_nothing_waits_for_a_prompt(self):
        t0 = time.time()
        r = self.call("shell_exec", command="read -p 'name? ' x; echo got=[$x]", timeout=10)
        self.assertLess(time.time() - t0, 5)
        self.assertIn("got=[]", self.text(r))

    def test_timeout_kills_the_whole_group(self):
        marker = self.path("alive")
        r = self.call("shell_exec", command="(sleep 5; touch %s) & sleep 5" % marker, timeout=1)
        self.assertIn("TIMED OUT", self.text(r))
        time.sleep(0.4)
        found = subprocess.run(["pgrep", "-f", "[t]ouch " + marker], stdout=subprocess.PIPE)
        self.assertEqual(found.stdout.strip(), b"")

    def test_cancel(self):
        ctx = Ctx(client="t")
        threading.Timer(0.5, ctx.cancel.set).start()
        t0 = time.time()
        r = registry.call_tool("shell_exec", {"command": "sleep 30"}, ctx)
        self.assertLess(time.time() - t0, 5)
        self.assertIn("cancelled", self.text(r))

    def test_big_output_is_cut_in_the_middle_and_saved(self):
        r = self.call("shell_exec", command="seq 1 200000", max_output=5000)
        t = self.text(r)
        self.assertIn("characters omitted", t)
        self.assertIn("\n1\n", t)
        self.assertIn("200000", t)
        self.assertIn("the full text is in", t)
        path = t.split("the full text is in ")[1].split("]")[0].split(",")[0]
        with open(path) as f:
            self.assertEqual(len(f.read().split()), 200000)

    def test_background_process_holding_the_pipe_does_not_block(self):
        t0 = time.time()
        self.ok("shell_exec", command="(sleep 20 &) ; echo started", timeout=30)
        self.assertLess(time.time() - t0, 6)

    def test_bad_cwd_and_missing_command(self):
        self.assertIn("not a directory", self.err("shell_exec", command="true", cwd="/nonexistent"))
        r = self.call("shell_exec", command="definitely-not-a-command-xyz")
        self.assertIn("not found", self.text(r))

    def test_progress_messages(self):
        seen = []
        ctx = Ctx(client="t", progress=lambda m, d, t: seen.append(m))
        registry.call_tool("shell_exec", {"command": "echo a; sleep 1; echo b"}, ctx)
        self.assertTrue(any("a" in m for m in seen), seen)

    def test_environment_is_non_interactive(self):
        out = self.ok("shell_exec", command="echo $TERM $PAGER $DEBIAN_FRONTEND $GIT_TERMINAL_PROMPT")
        self.assertIn("dumb cat noninteractive 0", out)

    def test_unicode(self):
        self.assertIn("caffè ☕", self.ok("shell_exec", command="echo 'caffè ☕'"))

    def test_elevate_helper(self):
        me = proc._current_user()
        self.assertEqual(proc.elevate(["id"], sudo=True) if os.geteuid() == 0 else None, ["id"] if os.geteuid() == 0 else None)
        self.assertEqual(proc.elevate(["id"], user=me), ["id"])
        self.assertEqual(proc.elevate(["id"], user="nobody")[-2:], ["--", "id"])


class Jobs(EngineCase):
    def test_lifecycle(self):
        out = self.ok("job_start", command="for i in 1 2 3; do echo line $i; sleep 0.3; done; exit 4", name="counter")
        job = out.split("job ")[1].split(" ")[0]
        self.assertIn("running", self.ok("job_list"))
        for _ in range(40):
            o = self.ok("job_output", id=job)
            if "exited" in o.split("\n")[0]:
                break
            time.sleep(0.25)
        self.assertIn("exited (exit 4)", o.split("\n")[0])
        self.assertIn("line 3", o)
        self.assertIn("exited", self.ok("job_list"))

    def test_incremental_reading_and_wait(self):
        job = self.ok("job_start", command="echo first; sleep 1; echo second").split("job ")[1].split(" ")[0]
        time.sleep(0.4)
        a = self.ok("job_output", id=job)
        self.assertIn("first", a)
        self.assertNotIn("second", a)
        nxt = int(a.split("next_offset=")[1].split("\n")[0].split()[0])
        b = self.ok("job_output", id=job, offset=nxt, wait=5)  # returns as soon as new bytes exist
        self.assertIn("second", b)
        self.assertNotIn("first", b)
        done = self.ok("job_output", id=job, wait=5)  # without an offset it waits for the job to finish
        self.assertIn("exited (exit 0)", done)

    def test_kill(self):
        job = self.ok("job_start", command="sleep 60").split("job ")[1].split(" ")[0]
        self.assertIn("Sent TERM", self.ok("job_kill", id=job))
        for _ in range(20):
            if "running" not in self.ok("job_list").split("\n")[1]:
                break
            time.sleep(0.2)
        self.assertNotIn("running", self.ok("job_list").split("\n")[1])
        self.assertIn("not running", self.ok("job_kill", id=job))

    def test_survives_the_caller(self):
        marker = self.path("done")
        self.ok("job_start", command="sleep 0.5; touch %s" % marker)
        time.sleep(1.5)
        self.assertTrue(os.path.exists(marker))

    def test_bad_ids_and_guard(self):
        self.assertIn("not a job id", self.err("job_output", id="../etc"))
        self.assertIn("not a job id", self.err("job_kill", id="zzzz"))
        self.assertIn("Safety guard", self.err("job_start", command="reboot"))

    def test_empty_list(self):
        self.assertIn("No jobs", self.ok("job_list"))


if __name__ == "__main__":
    unittest.main()
