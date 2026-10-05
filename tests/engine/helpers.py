"""Shared setup: every test works in its own temporary data folder and calls tools the way a transport does."""
import os
import shutil
import sys
import tempfile
import unittest

ENGINE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin", "engine"))
sys.path.insert(0, ENGINE)

from ervisio_ai import registry  # noqa: E402
from ervisio_ai.registry import Ctx  # noqa: E402


class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.mkdtemp(prefix="eai-test-")
        os.environ["ERVISIO_AI_HOME"] = self.home
        self.work = os.path.join(self.home, "work")
        os.makedirs(self.work)
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def call(self, tool, /, ctx=None, **args):
        return registry.call_tool(tool, args, ctx or Ctx(client="test"))

    def text(self, result) -> str:
        return result["content"][0]["text"]

    def ok(self, tool, /, **args) -> str:
        r = self.call(tool, **args)
        self.assertFalse(r["isError"], "%s failed: %s" % (tool, self.text(r)))
        return self.text(r)

    def err(self, tool, /, **args) -> str:
        r = self.call(tool, **args)
        self.assertTrue(r["isError"], "%s should have failed but said: %s" % (tool, self.text(r)))
        return self.text(r)

    def path(self, *parts) -> str:
        return os.path.join(self.work, *parts)

    def write(self, rel, content="") -> str:
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
        return p

    def read(self, path, mode="r"):
        with open(path, mode) as f:
            return f.read()
