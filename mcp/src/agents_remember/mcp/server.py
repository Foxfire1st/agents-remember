"""Stdio MCP server wiring for Agents Remember."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from agents_remember.application.server_startup import (
    initialize_mcp_application,
    prepare_mcp_process,
)
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_config,
)

from .compact_content import install_compact_content
from .registration import TOOL_REGISTRARS


def _complete_fastmcp_settings() -> None:
    """Resolve FastMCP's generic forward reference for strict warning policies."""
    FastMCPSettings.model_rebuild(_types_namespace={"FastMCP": FastMCP})


def create_server(config: McpRuntimeConfig) -> Any:
    install_compact_content()
    bind_worktree_services(build_default_worktree_services())
    # One ambient lifecycle per server process; the _tool_payload choke point
    # tags tool calls onto it once a lifecycle is started.
    initialize_mcp_application(config)
    _complete_fastmcp_settings()
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

    # Which of the two concurrent durable-store writers THIS PROCESS is
    # (controlplane/durable_store.py). Declared here, at the process entry point, and
    # deliberately not in create_server: the role is a fact about the process, and a factory
    # that tests call in-process would stamp this one onto whatever ran next.
    # Boot-time dashboard supervision (dashboard.autoStart) is part of the same application
    # operation and remains total/threaded: it must never delay or break the stdio handshake.
    prepare_mcp_process(config)
    run_server(config)
    return 0
