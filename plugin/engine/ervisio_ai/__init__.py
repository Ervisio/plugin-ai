"""Ervisio AI engine: an MCP server and tool set for one Linux machine.

Standard library only (Python 3.8+). It runs three ways:

* ``ervisio-ai serve``  headless MCP server over Streamable HTTP, with bearer tokens;
* ``ervisio-ai stdio``  the same server over stdin/stdout (started by an IDE through SSH);
* ``ervisio-ai call``   one tool call for the plugin's built-in chat (started by Ervisio).
"""

__version__ = "0.1.0"
NAME = "ervisio-ai"
