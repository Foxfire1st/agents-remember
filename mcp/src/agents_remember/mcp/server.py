"""Stdio MCP server wiring for Agents Remember."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from agents_remember.application.runtime import startup as server_startup

# Application owns process trust and startup composition; transport only invokes it.
# Server factories stay state-free for in-process tests.
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
from .public_surface import DISPATCH_AGENT_INPUT_FIELDS
from .registration import TOOL_REGISTRARS


class AgentsRememberMCP(FastMCP):
    """FastMCP boundary with the project's strict public dispatch contract.

    FastMCP 1.x generates argument models whose undeclared fields are ignored.  That
    would turn a caller-supplied model/effort or other spend knob into a silent no-op.
    The project owns a stricter contract: advertise the flat schema as closed and reject
    every undeclared dispatch input before the registered handler runs.
    """

    async def list_tools(self) -> list[Any]:
        tools = await super().list_tools()
        for tool in tools:
            if tool.name == "dispatch_agent":
                tool.inputSchema = {**tool.inputSchema, "additionalProperties": False}
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "dispatch_agent":
            extras = sorted(set(arguments) - DISPATCH_AGENT_INPUT_FIELDS)
            if extras:
                raise ToolError("dispatch_agent rejects undeclared inputs: " + ", ".join(extras))
        return await super().call_tool(name, arguments)


def _complete_fastmcp_settings() -> None:
    """Resolve FastMCP's generic forward reference for strict warning policies."""
    FastMCPSettings.model_rebuild(_types_namespace={"FastMCP": FastMCP})


def create_server(config: McpRuntimeConfig) -> Any:
    install_compact_content()
    bind_worktree_services(build_default_worktree_services())
    # One ambient lifecycle per server process; the _tool_payload choke point
    # tags tool calls onto it once a lifecycle is started.
    server_startup.initialize_mcp_application(config)
    _complete_fastmcp_settings()
    server = AgentsRememberMCP("Agents Remember")
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

    # The trust declaration must precede config loading.  Without it, code
    # imported from a task worktree is checkout CLI code and receives only that
    # leaf's disposable dev coordinator.
    server_startup.declare_mcp_process()
    try:
        config = load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))

    # Preparation idempotently retains the process declaration, then owns boot-time dashboard
    # supervision.  It remains total/threaded so it cannot delay or break the stdio handshake.
    server_startup.prepare_mcp_process(config)
    run_server(config)
    return 0
