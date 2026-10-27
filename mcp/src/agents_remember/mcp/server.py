"""Stdio MCP server wiring for Agents Remember."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from importlib import import_module
from typing import Any

from anyio import CancelScope
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from agents_remember.application.agent_binding import (
    TOOL_SERVER_NAME,
    read_agent_binding,
)
from agents_remember.application.runtime import startup as server_startup
from agents_remember.application.tool_answer_log import (
    ToolCallRecord,
    current_tool_call,
    read_tool_caller,
    record_tool_call,
    resolve_log_roots,
    tool_call_context,
)
from agents_remember.canvas.explainer_answers import refusal
from agents_remember.canvas.explainer_binding import LaunchedSeat, require_launched_seat
from agents_remember.canvas.explainer_state import ExplainerRefused

# Application owns process trust and startup composition; transport only invokes it.
# Server factories stay state-free for in-process tests.
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_config,
)

from .compact_content import install_compact_content
from .read_only import apply_read_only_marks
from .registration import TOOL_REGISTRARS
from .registration.explainer_surface import (
    EXPLAINER_SURFACE,
    read_explainer_binding,
    refuse_unexposed,
    register_explainer_surface,
)


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

    # MIK-R186 @ v2 rule 3: when this process was launched as an explainer seat, the only tools it
    # advertises *and* dispatches are the explainer's. None on an ordinary server.
    _bound_surface: tuple[str, ...] | None = None

    # MIK-R186 @ v2 rule 3: the seat this process was launched for. Set with `_bound_surface`; every
    # dispatched call is checked against it, so a forged or replaced seat answers by name.
    _explainer_binding: Any | None = None

    _synchronous_write_lock: asyncio.Lock | None = None

    async def list_tools(self) -> list[Any]:
        answer = await super().list_tools()
        # MIK-R204 @ v1 rule 3 as amended (80f71dff): the one post-discovery start -- the
        # outside-scratch sweep always and dashboard supervision when autoStart is true -- is
        # scheduled from the event loop right after this first discovery answer has been produced,
        # so neither initialize nor the answer waits for it. The first tool call below is the
        # fallback for a client that never lists; one latch covers both, so it happens once.
        loop = asyncio.get_running_loop()
        loop.call_soon(
            server_startup.start_after_discovery,
            getattr(self, "_agents_remember_config", None),
        )
        return answer

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        # MIK-R204 @ v1 rule 3 as amended: this is the fallback start, for a client that never
        # lists tools. The same once-only latch runs the outside-scratch sweep and, when autoStart
        # is set, dashboard supervision; a second call is a no-op. Both launches are daemon threads
        # and the attempt is total, so this neither delays nor changes the answer below. The
        # tool-event writer stays at startup because it is the boot-time caller identity of rule 4.
        config = getattr(self, "_agents_remember_config", None)
        server_startup.start_after_discovery(config)
        # MIK-R186 @ v2 rule 3: a bound explainer server answers its own surface and nothing else. The
        # list is enforced here as well as at registration, so naming a tool directly is refused by name
        # rather than merely absent from discovery.
        bound = getattr(self, "_bound_surface", None)
        if bound is not None and name not in bound:
            raise refuse_unexposed(name)
        # MIK-R108 rule 3: every call is written to the daily log with its
        # arguments, outcome, duration and answer size. The context carries
        # the exact MCP arguments, the call start, and this server's config
        # to the answer boundary (application/tool_response.py), which logs
        # the completed call; a call that raises is logged here and re-raised.
        # Dispatch input refusals happen inside the context so they are
        # logged like any other raised call.
        with tool_call_context(name, arguments, config=config):
            launched = getattr(self, "_explainer_binding", None)
            if launched is not None:
                # MIK-R186 @ v2 rule 3 sentences 3-4: every tool of this server checks that the caller
                # is the seat the server was launched for, and answers a forged or replaced seat as
                # data. One check covers all 22 tools because it sits ahead of every one of them.
                not_this_seat = _launched_seat_refusal(launched)
                if not_this_seat is not None:
                    return not_this_seat
            try:
                return await self._dispatch_tool(name, arguments)
            except Exception as error:
                _log_raised_tool_call(name, arguments, config, error)
                raise

    async def _dispatch_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Keep synchronous writes ordered while their handlers leave the event loop."""
        tool = self._tool_manager.get_tool(name)
        if tool is None or tool.is_async:
            return await super().call_tool(name, arguments)
        # Metadata was built from the original function; only its execution changes.
        # A per-call copy avoids mutating registration or the advertised signature.
        threaded = tool.model_copy(update={"fn": _threaded(tool.fn), "is_async": True})
        context = self.get_context()
        if _nonwriting_call(tool, arguments):
            return await threaded.run(arguments, context=context, convert_result=True)
        if self._synchronous_write_lock is None:
            self._synchronous_write_lock = asyncio.Lock()
        async with self._synchronous_write_lock:
            return await threaded.run(arguments, context=context, convert_result=True)


def _nonwriting_call(tool: Any, arguments: dict[str, Any]) -> bool:
    """Only advertised reads and explicitly non-writing modes bypass write ordering."""
    if tool.annotations is not None and tool.annotations.readOnlyHint is True:
        return True
    if tool.name == "context_packet":
        return arguments.get("include_freshness", False) is False
    if tool.name == "memory_quality_check":
        request = arguments.get("request")
        return isinstance(request, dict) and request.get("mode") in ("start", "poll")
    if tool.name == "citation_fix":
        return arguments.get("mode") in ("start", "poll") or arguments.get("dry_run") is True
    return False


def _threaded(function: Any) -> Any:
    async def execute(**arguments: Any) -> Any:
        # asyncio.to_thread carries the request and tool-log context into the worker.
        worker = asyncio.create_task(asyncio.to_thread(partial(function, **arguments)))
        try:
            return await asyncio.shield(worker)
        except BaseException:
            # A thread cannot be cancelled. Keep the write lock until it exits, even
            # under repeated transport cancellation; a second writer must not race it.
            with CancelScope(shield=True):
                while not worker.done():
                    try:
                        await asyncio.wait((worker,))
                    except asyncio.CancelledError:
                        continue
            if not worker.cancelled():
                worker.exception()
            raise

    return execute


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


def _launched_seat_refusal(binding: LaunchedSeat) -> dict[str, Any] | None:
    """MIK-R186 @ v2 rule 3: the caller must be the seat this bound server was launched for.

    The comparison is `canvas.explainer_binding.require_launched_seat`, one owner for both halves of
    the launch identity; a refusal is answered as the same data shape every explainer tool answers,
    never as a transport error.
    """

    try:
        require_launched_seat(binding)
    except ExplainerRefused as refused:
        return refusal(refused.reason, refused.detail)
    return None


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


def _worktree_services(config: McpRuntimeConfig) -> Any:
    """Build this process's worktree services, on the first operation that asks for them."""

    from agents_remember.cli.role_launch_archive import LeafAgentArchive  # noqa: PLC0415

    composed = import_module("agents_remember.application.worktree_services")
    return composed.build_default_worktree_services(leaf_agent_archive=LeafAgentArchive(config))


def create_server(config: McpRuntimeConfig) -> Any:
    install_compact_content()
    # MIK-R204 @ v1 rule 3: the worktree services are an operation collaborator, not discovery work. The
    # composition names how to build them; the first worktree operation that needs them builds them once.
    # The services package is a worktree collaborator, not a startup dependency: naming the supplier is the
    # only thing this process needs from it, and it is resolved here rather than imported at module level.
    binding = import_module("agents_remember.kernel.worktree_service_binding")
    binding.set_worktree_services_supplier(lambda: _worktree_services(config))
    # Initialize the process's application services before registering tools.
    server_startup.initialize_mcp_application(config)
    _complete_fastmcp_settings()
    server = AgentsRememberMCP("Agents Remember", instructions=launched_server_instructions())
    # MIK-R108 rule 3: the dispatch carries this server's config (coordination
    # and transcript roots, toolLog keeping) to the answer boundary per call.
    server._agents_remember_config = config
    # The tool surface itself lives in `.registration`, one module per family; this is the only place
    # that decides which families a server advertises. MIK-R186 @ v2 rule 3: a launch that bound this
    # process as an explainer seat gets one surface -- the explainer's -- and an invalid binding refuses
    # service instead of falling through to the ordinary roster.
    binding = read_explainer_binding(config)
    if binding is None:
        for register_tools in TOOL_REGISTRARS:
            register_tools(server, config)
    else:
        server._bound_surface = EXPLAINER_SURFACE
        # MIK-R186 @ v2 rule 3: the binding names the seat this process serves, and the dispatch checks
        # every call against it.
        server._explainer_binding = binding
        register_explainer_surface(server, config, binding)
    # MIK-R109 rule 7: the one list of read-only marks rides on the advertised
    # annotations, applied here so every family is covered and an unlisted
    # tool breaks creation instead of shipping unmarked.
    apply_read_only_marks(server)
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

    # Preparation retains only the process declaration. Dashboard supervision starts right after
    # the first tool-list answer (or on the first tool call as a fallback), so initialize never
    # starts a dashboard thread (MIK-R204 @ v1 rule 3 as amended).
    server_startup.prepare_mcp_process()
    run_server(config)
    return 0
