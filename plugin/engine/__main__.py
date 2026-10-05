#!/usr/bin/env python3
"""Entry point: ``python3 -B <engine folder> <command>`` or the installed ``ervisio-ai`` wrapper."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ervisio_ai.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
