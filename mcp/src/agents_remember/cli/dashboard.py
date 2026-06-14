"""CLI adapter: run the local mission-control dashboard server.

Mirrors the MCP server's ``--config`` contract (``load_config`` over the same trusted MCP
settings JSON), so the dashboard resolves the identical coordination context.
"""

from __future__ import annotations

import argparse

import uvicorn

from agents_remember.mcp.config import ConfigError, load_config
from agents_remember.serving.app import create_app


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", required=True, help="Absolute path to trusted MCP settings JSON."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (localhost-only by default; do not expose)."
    )
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default 8765).")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Projection refresh interval, seconds."
    )


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"error: {error}")
        return 1
    app = create_app(config, interval=args.interval)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
