"""Stdio MCP server wiring for Agents Remember."""

import argparse
import json
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from agents_remember.application.agent_binding import TOOL_SERVER_NAME, read_agent_binding
from agents_remember.application.runtime import startup as server_startup
from agents_remember.application.tool_answer_log import (
    ToolCallRecord,
    current_tool_call,
    read_tool_caller,
    record_tool_call,
    resolve_log_roots,
    tool_call_context,
)

# Application owns process trust and startup composition; transport only invokes it.
# Server factories stay state-free for in-process tests.
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.cli.role_launch_archive import LeafAgentArchive
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

    # MIK-R108 rule 3: this server's config, carried per call to the answer
    # boundary by call_tool. Set once by create_server; None on a bare
    # instance (a test), where the log resolves without it.
    _agents_remember_config: Any | None = None

    async def list_tools(self) -> list[Any]:
        tools = await super().list_tools()
        for tool in tools:
            if tool.name == "dispatch_agent":
                tool.inputSchema = {**tool.inputSchema, "additionalProperties": False}
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        # MIK-R108 rule 3: every call is written to the daily log with its
        # arguments, outcome, duration and answer size. The context carries
        # the exact MCP arguments, the call start, and this server's config
        # to the answer boundary (application/tool_response.py), which logs
        # the completed call; a call that raises is logged here and re-raised.
        # Dispatch input refusals happen inside the context so they are
        # logged like any other raised call.
        config = getattr(self, "_agents_remember_config", None)
        with tool_call_context(name, arguments, config=config):
            try:
                if name == "dispatch_agent":
                    extras = sorted(set(arguments) - DISPATCH_AGENT_INPUT_FIELDS)
                    if extras:
                        raise ToolError(
                            "dispatch_agent rejects undeclared inputs: " + ", ".join(extras)
                        )
                return await super().call_tool(name, arguments)
            except Exception as error:
                _log_raised_tool_call(name, arguments, config, error)
                raise


def _log_raised_tool_call(
    name: str, arguments: dict[str, Any], config: Any | None, error: Exception
) -> None:
    """Write the log line for a call that raised (MIK-R108 rule 3).

    A refused call flows through the answer boundary as a payload; a call
    that raises never reaches it, so the dispatch logs it here instead. The
    log write itself never raises: every failure is contained.
    """
    with suppress(Exception):
        call = current_tool_call()
        started = call.started_perf if call is not None else 0.0
        duration_ms = (time.perf_counter() - started) * 1000.0 if call is not None else 0.0
        dest = resolve_log_roots(config, name)
        whole: dict[str, Any] = {
            "ok": False,
            "code": "raised",
            "error": f"the call raised {type(error).__name__}: {error}".strip()[:500]
            or "the call raised.",
            "raisedAt": datetime.now(UTC).isoformat(),
        }
        whole_bytes = len(
            json.dumps(whole, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        )
        record_tool_call(
            ToolCallRecord(
                tool_name=name,
                operation=name,
                arguments=dict(arguments),
                caller=read_tool_caller(),
                ok=False,
                code="raised",
                duration_ms=duration_ms,
                answer_tokens=0,
                whole_result=whole,
                result_bytes=whole_bytes,
                guard_cut=False,
                needs_detail=True,
                enclosure_dir=dest.enclosure_dir,
                coord_log_dir=dest.coord_log_dir,
                keep_days=dest.keep_days,
                max_bytes=dest.max_bytes,
                enclosure_max_bytes=dest.enclosure_max_bytes,
            )
        )


def _complete_fastmcp_settings() -> None:
    """Resolve FastMCP's generic forward reference for strict warning policies."""
    FastMCPSettings.model_rebuild(_types_namespace={"FastMCP": FastMCP})


# What the tool server of a launched agent states about itself as its MCP instructions. A harness
# may show the line beside the name under which it lists the server, which need not be spelled
# as the agent's assignment spells it.
LAUNCHED_SERVER_INSTRUCTIONS = (
    f"This server is {TOOL_SERVER_NAME}: the Agents Remember tool server of the AR build that "
    "launched this agent, and the one to use for this assignment."
)


def launched_server_instructions() -> str | None:
    """The instructions of a server that a role launch started for one agent; otherwise none.

    A binding that cannot be read states none either: the server starts as it did before, and a
    ``server_info`` call says what is wrong with the binding.
    """

    try:
        bound = read_agent_binding() is not None
    except ValueError:
        return None
    return LAUNCHED_SERVER_INSTRUCTIONS if bound else None


def create_server(config: McpRuntimeConfig) -> Any:
    install_compact_content()
    bind_worktree_services(
        build_default_worktree_services(leaf_agent_archive=LeafAgentArchive(config))
    )
    # One ambient lifecycle per server process; the _tool_payload choke point
    # tags tool calls onto it once a lifecycle is started.
    server_startup.initialize_mcp_application(config)
    _complete_fastmcp_settings()
    server = AgentsRememberMCP("Agents Remember", instructions=launched_server_instructions())
    # MIK-R108 rule 3: the dispatch carries this server's config (coordination
    # and transcript roots, toolLog keeping) to the answer boundary per call.
    server._agents_remember_config = config
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
