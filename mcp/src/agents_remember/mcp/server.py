"""Stdio MCP server wiring for Agents Remember."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, observer_root
from agents_remember.serving.daemon import maybe_autostart_dashboard

from .compact_content import install_compact_content
from .config import ConfigError, McpRuntimeConfig, load_config
from .registration import TOOL_REGISTRARS


def create_server(config: McpRuntimeConfig) -> Any:
    install_compact_content()
    # One ambient lifecycle per server process; the _tool_payload choke point
    # tags tool calls onto it once a lifecycle is started.
    install_ambient(AmbientLifecycle(EventStore(observer_root(config))))
    server = FastMCP("Agents Remember")
    # The tool surface itself lives in `.registration`, one module per family; this loop is
    # the only place that decides which families a server advertises.
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, config)
    return server


def run_server(config: McpRuntimeConfig) -> None:
    create_server(config).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agents Remember MCP server.")
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path to trusted MCP settings JSON.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))

    # Boot-time dashboard supervision (dashboard.autoStart): total and threaded —
    # it must never delay or break the stdio handshake this process exists for.
    maybe_autostart_dashboard(config)
    run_server(config)
    return 0
