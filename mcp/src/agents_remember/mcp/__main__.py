"""Command-line entrypoint for the Agents Remember MCP server."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
