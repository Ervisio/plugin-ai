#!/usr/bin/env python3
"""Writes docs/tools.md from the engine's own tool registry, so the reference can never drift from the code.

    python3 -B scripts/gen-tools-doc.py            # rewrite docs/tools.md
    python3 -B scripts/gen-tools-doc.py --check    # exit 1 when the file is out of date (CI)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "engine"))
sys.dont_write_bytecode = True

from ervisio_ai import registry  # noqa: E402

GROUPS = [
    ("shell", "Shell and background jobs"), ("terminal", "Live terminals"), ("files", "Files"),
    ("system", "System and processes"), ("services", "Services and cron"), ("packages", "Packages"),
    ("network", "Network, SSH and firewall"), ("docker", "Docker"), ("ervisio", "Ervisio itself"), ("meta", "Context and memory"),
]


def render() -> str:
    by = {}
    for tool in registry.load_tools().values():
        by.setdefault(tool.category, []).append(tool)
    out = ["# Tool reference", "", "@COUNT@", "",
           "Generated from the engine (`python3 scripts/gen-tools-doc.py`). Every tool is available in the chat and to every MCP client; ",
           "the policy can switch whole groups or single tools off. **R** = only reads, **W** = changes something, **!** = can destroy data or lock you out, ",
           "**root** = accepts `sudo: true` to run as root.", ""]
    total = 0
    for cat, title in GROUPS:
        tools = sorted(by.pop(cat, []), key=lambda t: t.name)
        if not tools:
            continue
        out += ["## " + title, "", "| Tool | | What it does |", "|---|---|---|"]
        for t in tools:
            total += 1
            flags = "R" if t.read_only else ("W!" if t.destructive else "W")
            if "sudo" in t.schema.get("properties", {}):
                flags += " root"
            out.append("| `%s` | %s | %s |" % (t.name, flags, t.description.replace("|", "\\|").replace("\n", " ")))
        out.append("")
    for cat, tools in by.items():
        raise SystemExit("tool group %r is not in GROUPS: %s" % (cat, [t.name for t in tools]))
    return "\n".join(out).replace("@COUNT@", "%d tools in ten groups." % total) + "\n"


def main() -> int:
    text = render()
    path = os.path.join(ROOT, "docs", "tools.md")
    if "--check" in sys.argv:
        current = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if current != text:
            print("docs/tools.md is out of date: run python3 -B scripts/gen-tools-doc.py")
            return 1
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
