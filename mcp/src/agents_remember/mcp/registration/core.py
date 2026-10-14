"""Server identity, orientation reads, and runtime installation tools."""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from agents_remember.application.context_packet import ContextPacketRequest
from agents_remember.application.runtime.install import RuntimeInstallRequest
from agents_remember.application.runtime.startup import mcp_serving_build_payload
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.task_document_ref import TaskScopedReaderContext

from ..tools import (
    context_packet_payload,
    read_ar_files_payload,
    runtime_install_payload,
    server_info_payload,
)
from ..tools.read_files import _REMOVED_ARGUMENT


def _scrub_removed_argument(server: FastMCP, tool_name: str, argument: str) -> None:
    """Drop one receivable-but-removed argument from the advertised schema.

    The argument stays in the signature so a call that supplies it reaches
    the boundary refusal; it leaves the advertised ordinary contract so no
    caller learns it (MIK-R109 rule 1). Mirrors the post-registration mark
    application for read-only tools.
    """

    tools: dict[str, Any] = server._tool_manager._tools
    tool = tools.get(tool_name)
    if tool is None:
        return
    parameters = dict(tool.parameters)
    properties = dict(parameters.get("properties", {}))
    properties.pop(argument, None)
    parameters["properties"] = properties
    parameters["required"] = [name for name in parameters.get("required", []) if name != argument]
    tool.parameters = parameters


def register_core_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the core tools, split by what the caller is trying to find out."""
    serving_build = mcp_serving_build_payload()
    _register_identity_tools(server, config, serving_build)
    _register_orientation_tools(server, config, serving_build.commit)
    _register_installation_tools(server, config)


def _register_identity_tools(
    server: FastMCP, config: McpRuntimeConfig, serving_build: ServingBuildPayload
) -> None:
    """What this server is and how it was configured."""

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    def server_info() -> dict[str, Any]:
        """Report the resolved configuration: build, roots, allowed repos, provider keys left
        unused, and, on a role-launched server, the tool server name and agent binding.
        Read-only; reflects the settings and runtime loaded at startup. Longer explanation:
        docs/tool-details.md."""
        return server_info_payload(config, serving_build)


def _register_orientation_tools(
    server: FastMCP, config: McpRuntimeConfig, serving_build_commit: str | None
) -> None:
    """Read-only reads that orient a session in a repository before it changes anything."""

    @server.tool()
    def context_packet(
        repo_id: str,
        include_drift: bool = False,
        include_freshness: bool = False,
        task_context: TaskScopedReaderContext | None = None,
    ) -> dict[str, Any]:
        """Orient a repository or leaf; leaf calls pass task_context. include_drift measures the reference anchors of onboarding cards and validity of the tree; omit it and nothing is measured. Staleness never blocks by itself. Currentness: knowledge_read and knowledge_integrity_check. MIK-R30: update the card or record why not; citation_fix repairs moved anchors, a curator re-authors stale ones. Nothing is repaired here. include_freshness fetches remotes and writes FETCH_HEAD. More: docs/tool-details.md."""
        return context_packet_payload(
            config,
            ContextPacketRequest(
                repo_id=repo_id,
                include_drift=include_drift,
                include_freshness=include_freshness,
            ),
            task_context=task_context,
            serving_build_commit=serving_build_commit,
        )

    @server.tool()
    def read_ar_files(
        repo_id: str,
        files: list[Any],
        refresh: Any = _REMOVED_ARGUMENT,
        task_context: TaskScopedReaderContext | None = None,
    ) -> dict[str, Any]:
        """Name which knowledge governs up to 20 paths (MIK-R109).

        For each path (as strings): existence, governing card as a memory-worktree
        location (or none), record counts by state, and action-asking records by ID,
        kind, state and card heading. Overviews once per call, nearest first. No
        text is returned: read files with your own tools. `refresh` was removed
        from the ordinary contract: supplying it (true, false or null) is refused
        as `argument-removed`, like object-form entries. Stateless; no continuation.
        Leaf calls pass task_context. Longer explanation: docs/reference/mcp-tools.md."""
        return read_ar_files_payload(
            config,
            repo_id,
            files,
            refresh=refresh,
            task_context=task_context,
        )

    _scrub_removed_argument(server, "read_ar_files", "refresh")


def _register_installation_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Install or refresh what the coordinator runtime needs on disk."""

    @server.tool()
    def runtime_install(
        dry_run: bool = False,
        include_benchmarks: bool = False,
        install_provider_deps: bool = True,
        no_cache: bool = False,
        experiment: str | None = None,
    ) -> dict[str, Any]:
        """Install or refresh the packaged coordinator runtime into the coordination root, safe
        to re-run. Preserves user data (memory-repos/, providers/data/); replaces the scaffold
        (skills/, docs/) and prunes legacy binaries. No provider install/start/watch
        path remains: the install stops leftover watchers and reports enabled provider keys
        once as unused. The final host part provisions the build-pinned host/plugin from the
        shared paseoRuntime block; it never writes shared settings and defers needed restarts.
        Preview first with dry_run=true: it changes nothing. Longer explanation:
        docs/tool-details.md."""
        return runtime_install_payload(
            config,
            RuntimeInstallRequest(
                dry_run=dry_run,
                include_benchmarks=include_benchmarks,
                install_provider_deps=install_provider_deps,
                no_cache=no_cache,
                experiment=experiment,
            ),
        )
