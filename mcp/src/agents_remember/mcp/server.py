"""Stdio MCP server wiring for Agents Remember."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from importlib import import_module
from typing import Any, Literal, NotRequired, TypedDict, override

from mcp.server.mcpserver import Context, Icon, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolResult, InputRequiredResult, ToolAnnotations
from mcp.types import Tool as MCPTool

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
from agents_remember.application.tool_build_currentness import tool_build_currentness
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
from agents_remember.kernel.primitives.version import SERVER_VERSION
from agents_remember.kernel.request_memo import request_memo_scope
from agents_remember.memory_quality.style.citations.extents import definition_scope
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.role_tool_scope import tools_for_role
from agents_remember.models.tools.tool_schema_compaction import compact_tool_schema
from agents_remember.serving.build_currentness import build_currentness
from agents_remember.serving.server_bindings import (
    BoundIdentityPayload,
    register_server_binding,
)
from agents_remember.serving.server_registry import register_server

from .read_only import read_only_annotations
from .registration import TOOL_REGISTRARS
from .registration.capsule_serving import build_skills_extension
from .registration.explainer_surface import (
    EXPLAINER_SURFACE,
    read_explainer_binding,
    register_explainer_surface,
)
from .registration.skills_extension import preserve_handshake_extensions
from .thread_workers import (
    run_in_request_thread,
    track_worker_cancellation,
)
from .tool_results import build_early_refusal_result, structured_output_spec
from .tool_wrapper import RegisteredToolPolicy, wrap_registered_tool


class AgentsRememberMCP(MCPServer[Any]):
    """MCPServer boundary with Agents Remember's request-scope, logging, and offload contract."""

    # This server's config, carried per call to the answer boundary by call_tool.
    # Set once by create_server; None on a bare instance (a test), where the log resolves without it.
    _agents_remember_config: Any | None = None

    # The seat this process was launched for when bound as an explainer seat; each of the
    # explainer's own tools is checked against it, so a forged or replaced seat answers by name.
    _explainer_binding: Any | None = None

    # Snapshotted once at server creation from read_agent_binding(); None when unbound or
    # malformed. Used to suppress falsely reported provider-limit windows after a live turn
    # starts strictly later than recordedAt.
    _bound_agent_id: str | None = None

    _synchronous_write_lock: asyncio.Lock | None = None

    def __init__(
        self,
        *args: Any,
        enforce_read_only_marks: bool = False,
        **kwargs: Any,
    ) -> None:
        self._enforce_read_only_marks = enforce_read_only_marks
        self._compact_listed_schemas = False
        self._registered_tools: dict[str, ToolAnnotations | None] = {}
        super().__init__(*args, **kwargs)

    def registered_tool_names(self) -> tuple[str, ...]:
        """Return currently registered tool names in registration order without triggering discovery."""
        return tuple(self._registered_tools)

    @override
    def add_tool(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        tool_name = name or fn.__name__
        effective_annotations = (
            read_only_annotations(tool_name, annotations)
            if self._enforce_read_only_marks
            else annotations
        )
        output_spec = structured_output_spec(fn, structured_output=structured_output)
        effective_structured_output = False if output_spec is None else structured_output
        wrapped = wrap_registered_tool(
            fn,
            RegisteredToolPolicy(
                tool_name=tool_name,
                annotations=effective_annotations,
                output_spec=output_spec,
                acquire_write_lock=self._write_lock_for_call,
                on_write_lock_cancelled=self._log_write_lock_cancellation,
            ),
        )
        super().add_tool(
            wrapped,
            name=name,
            title=title,
            description=description,
            annotations=effective_annotations,
            icons=icons,
            meta=meta,
            structured_output=effective_structured_output,
        )
        self._registered_tools.setdefault(tool_name, effective_annotations)

    @override
    def remove_tool(self, name: str) -> None:
        super().remove_tool(name)
        self._registered_tools.pop(name, None)

    def _write_lock_for_call(self, _tool_name: str, _admitted: dict[str, Any]) -> asyncio.Lock:
        if self._synchronous_write_lock is None:
            self._synchronous_write_lock = asyncio.Lock()
        return self._synchronous_write_lock

    def _log_write_lock_cancellation(self, tool_name: str, admitted: dict[str, Any]) -> None:
        call = current_tool_call()
        logged_args = dict(call.arguments) if call is not None else admitted
        _log_cancelled_wait(tool_name, logged_args, self._agents_remember_config, "write-lock")

    async def list_tools(self) -> list[MCPTool]:
        answer = await super().list_tools()
        if self._compact_listed_schemas:
            answer = [
                tool.model_copy(update={"input_schema": compact_tool_schema(tool.input_schema)})
                for tool in answer
            ]
        # Schedule the once-only post-discovery start (outside-scratch sweep and optional
        # dashboard supervision) on the event loop right after producing the listing answer so
        # neither initialize nor discovery waits for it; call_tool carries the same latch as fallback.
        loop = asyncio.get_running_loop()
        loop.call_soon(
            server_startup.start_after_discovery,
            getattr(self, "_agents_remember_config", None),
        )
        return answer

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[Any, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        try:
            with request_memo_scope():
                return await self._execute_tool_call(name, arguments, context)
        finally:
            from agents_remember.serving import heap_diag  # noqa: PLC0415

            # The arena walk can block, so run it off the event loop after the call scopes unwind.
            await run_in_request_thread(heap_diag.trim_malloc)

    async def _execute_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[Any, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        # Fallback post-discovery start for a client that never lists tools; the once-only
        # latch runs the outside-scratch sweep and (when autoStart is true) dashboard
        # supervision in daemon threads without delaying or altering the answer.
        config = getattr(self, "_agents_remember_config", None)
        server_startup.start_after_discovery(config)
        # Record every call (including raised dispatch refusals) to the daily tool log.
        with (
            definition_scope(),
            tool_call_context(name, arguments, config=config),
            track_worker_cancellation(
                partial(_log_raised_tool_call, name, arguments, config, request_cancelled=True),
                partial(_log_cancelled_wait, name, arguments, config, "worker-thread"),
            ) as worker_cancellation,
        ):
            if (refused := self._explainer_seat_refusal(name)) is not None:
                return refused
            waiting_for: Literal["build-currentness"] | None = "build-currentness"
            try:
                reading = (
                    await asyncio.to_thread(build_currentness, config)
                    if config is not None
                    else None
                )
                waiting_for = None
                await self._maybe_suppress_provider_limits(config)
                with tool_build_currentness(reading):
                    return await self._dispatch_tool(name, arguments, context)
            except asyncio.CancelledError:
                _log_interrupted_dispatch(
                    name, arguments, config, waiting_for, worker_cancellation.error
                )
                raise
            except Exception as error:
                _log_raised_tool_call(name, arguments, config, error)
                raise

    def _explainer_seat_refusal(self, name: str) -> CallToolResult | None:
        launched = getattr(self, "_explainer_binding", None)
        if launched is None or name not in EXPLAINER_SURFACE:
            return None
        # Guard only explainer tools still registered after role narrowing.
        if name not in self._registered_tools:
            return None
        not_this_seat = _launched_seat_refusal(launched)
        return build_early_refusal_result(not_this_seat) if not_this_seat is not None else None

    async def _maybe_suppress_provider_limits(self, config: Any | None) -> None:
        """Suppress falsely limited windows when a bound seat proves a post-limit live turn."""
        agent_id = getattr(self, "_bound_agent_id", None)
        if config is None or not isinstance(agent_id, str) or not agent_id:
            return
        with suppress(Exception):
            from agents_remember.serving.paseo.provider_limits import (  # noqa: PLC0415
                suppress_provider_limits_for_live_tool_call,
            )

            await asyncio.to_thread(suppress_provider_limits_for_live_tool_call, config, agent_id)

    async def _dispatch_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[Any, Any] | None,
    ) -> CallToolResult | InputRequiredResult:
        """Dispatch through MCPServer.call_tool and preserve ToolError cause messages."""
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as error:
            cause = error.__cause__
            if isinstance(cause, Exception):
                raise ToolError(f"Error executing tool {name}: {cause}") from cause
            raise


type _CancelledWait = Literal["build-currentness", "write-lock", "worker-thread"]


class _FailedToolResult(TypedDict):
    ok: Literal[False]
    code: Literal["raised", "cancelled"]
    error: str
    raisedAt: NotRequired[str]
    requestCancelled: NotRequired[Literal[True]]
    waitingFor: NotRequired[_CancelledWait]


def _log_interrupted_dispatch(
    name: str,
    arguments: dict[str, Any],
    config: Any | None,
    waiting_for: Literal["build-currentness"] | None,
    worker_error: Exception | None,
) -> None:
    if waiting_for is not None:
        _log_cancelled_wait(name, arguments, config, waiting_for)
    elif worker_error is not None:
        _log_raised_tool_call(name, arguments, config, worker_error, request_cancelled=True)


def _log_cancelled_wait(
    name: str,
    arguments: dict[str, Any],
    config: Any | None,
    waiting_for: _CancelledWait,
) -> None:
    wait = {
        "build-currentness": "the serving build's currentness",
        "write-lock": "the write lock",
        "worker-thread": "a worker thread",
    }[waiting_for]
    _log_failed_tool_call(
        name,
        arguments,
        config,
        {
            "ok": False,
            "code": "cancelled",
            "error": f"the request was cancelled before the tool ran, while waiting for {wait}.",
            "waitingFor": waiting_for,
        },
    )


def _log_raised_tool_call(
    name: str,
    arguments: dict[str, Any],
    config: Any | None,
    error: Exception,
    *,
    request_cancelled: Literal[True] | None = None,
) -> None:
    """Write the log line for a call that raised (MIK-R108 rule 3).

    A refused call flows through the answer boundary as a payload; a call
    that raises never reaches it, so the dispatch logs it here instead. The
    log write itself never raises: every failure is contained.
    """
    with suppress(Exception):
        whole: _FailedToolResult = {
            "ok": False,
            "code": "raised",
            "error": f"the call raised {type(error).__name__}: {error}".strip()[:500]
            or "the call raised.",
            "raisedAt": datetime.now(UTC).isoformat(),
        }
        if request_cancelled is not None:
            whole["requestCancelled"] = request_cancelled
        _log_failed_tool_call(name, arguments, config, whole)


def _log_failed_tool_call(
    name: str, arguments: dict[str, Any], config: Any | None, whole: _FailedToolResult
) -> None:
    with suppress(Exception):
        call = current_tool_call()
        duration_ms = (time.perf_counter() - call.started_perf) * 1000.0 if call else 0.0
        dest = resolve_log_roots(config, name)
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
                code=whole["code"],
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


def _bound_role_tools(explainer_present: bool) -> frozenset[str] | None:
    """The tools a role-bound process may serve; ``None`` keeps the whole registered roster.

    An unbound process serves every public tool, and so does an explainer-only launch, whose four
    tools are added to that roster. A role binding narrows the server to its own allowlist, plus
    the explainer's four when the same process also serves an explainer seat. A binding that is
    present but unreadable is not a role: the process keeps the existing no-launched-instructions
    start and serves only ``server_info``, whose answer names what the binding got wrong.
    """

    try:
        binding = read_agent_binding()
    except ValueError:
        return frozenset({"server_info"})
    if binding is None:
        return None
    allowed = set(tools_for_role(binding.role))
    if explainer_present:
        allowed.update(EXPLAINER_SURFACE)
    return frozenset(allowed)


def _compact_server_tool_schemas(server: AgentsRememberMCP) -> None:
    """Enable lossless inputSchema compaction on the server's public ``list_tools`` output."""

    server._compact_listed_schemas = True


def _scope_server_tools(server: AgentsRememberMCP, allowed: frozenset[str]) -> None:
    """Remove every registered tool outside ``allowed``, keeping the survivors' order.

    ``remove_tool`` is MCPServer's public operation: removal is the only change, so the surviving
    tools keep their registration order, schemas and annotations.
    """

    for name in server.registered_tool_names():
        if name not in allowed:
            server.remove_tool(name)


def _snapshot_bound_agent_id() -> str | None:
    """Capture the bound agent id once at server creation; ``None`` if unbound or malformed."""

    try:
        agent_binding = read_agent_binding()
    except ValueError:
        return None
    return agent_binding.agent_id if agent_binding is not None else None


def create_server(config: McpRuntimeConfig) -> AgentsRememberMCP:
    # Supply worktree services lazily on the first worktree operation.
    binding = import_module("agents_remember.kernel.worktree_service_binding")
    binding.set_worktree_services_supplier(lambda: _worktree_services(config))
    server_startup.initialize_mcp_application(config)
    extensions = [build_skills_extension()]
    server = AgentsRememberMCP(
        "Agents Remember",
        instructions=launched_server_instructions(),
        version=SERVER_VERSION,
        extensions=extensions,
        middleware=[preserve_handshake_extensions(extensions)],
        subscriptions=False,
        enforce_read_only_marks=True,
    )
    # Carry server config per call to the answer boundary.
    server._agents_remember_config = config
    server._bound_agent_id = _snapshot_bound_agent_id()
    # Register AR tool families plus the explainer surface when bound as an explainer seat.
    # Central read-only annotations are validated and attached at registration time.
    explainer = read_explainer_binding(config)
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, config)
    if explainer is not None:
        # Verify explainer tool calls against the launched seat.
        server._explainer_binding = explainer
        register_explainer_surface(server, config, explainer)
    # Compact advertised schemas in list_tools and narrow by role binding.
    _compact_server_tool_schemas(server)
    allowed = _bound_role_tools(explainer_present=explainer is not None)
    if allowed is not None:
        _scope_server_tools(server, allowed)
    return server


def _document_ref_dict(ref: TaskDocumentRef | None) -> dict[str, str] | None:
    if ref is None:
        return None
    return {"repository": ref.repository, "path": ref.path}


def _register_process_binding(config: McpRuntimeConfig) -> Callable[[], None]:
    """L51-R1: publish this MCP server's binding snapshot before MCPServer runs."""
    try:
        agent_binding = read_agent_binding()
    except ValueError as error:
        return register_server_binding(
            config,
            binding_state="unreadable",
            unreadable_reason=str(error),
        )
    if agent_binding is None:
        return register_server_binding(config, binding_state="unbound")
    return register_server_binding(
        config,
        binding_state="bound",
        binding=BoundIdentityPayload(
            agentId=agent_binding.agent_id,
            requestId=agent_binding.request_id,
            role=agent_binding.role,
            sprintDocumentRef=_document_ref_dict(agent_binding.sprint_ref),
            masterDocumentRef=_document_ref_dict(agent_binding.master_ref),
            taskDocumentRef=_document_ref_dict(agent_binding.task_ref),
        ),
    )


def run_server(config: McpRuntimeConfig) -> None:
    cleanup = register_server(config, "mcp")
    cleanup_binding: Callable[[], None] | None = None
    try:
        cleanup_binding = _register_process_binding(config)
        create_server(config).run()
    finally:
        try:
            if cleanup_binding is not None:
                cleanup_binding()
        finally:
            cleanup()


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
