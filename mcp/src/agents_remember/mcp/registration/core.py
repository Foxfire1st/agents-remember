"""Server identity, orientation reads, and runtime installation tools."""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from agents_remember.application.context_packet import ContextPacketRequest
from agents_remember.application.runtime.install import RuntimeInstallRequest
from agents_remember.application.runtime.startup import mcp_serving_build_payload
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.task_document_ref import TaskScopedReaderContext

from ..tools import (
    context_packet_payload,
    read_ar_files_payload,
    resolve_context_payload,
    runtime_install_payload,
    server_info_payload,
)


def register_core_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the core tools, split by what the caller is trying to find out."""
    _register_identity_tools(server, config, mcp_serving_build_payload())
    _register_orientation_tools(server, config)
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


def _register_orientation_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Read-only reads that orient a session in a repository before it changes anything."""

    @server.tool()
    def context_packet(
        repo_id: str,
        include_drift: bool = False,
        include_freshness: bool = False,
        task_context: TaskScopedReaderContext | None = None,
    ) -> dict[str, Any]:
        """Bundle a repository's current state into one packet: repo/git state, resolved paths,
        memory mode/storage, worktree state, and (optionally) drift, and branch
        freshness (include_freshness fetches remote-tracking refs and reports ahead/behind for the
        code and memory checkouts plus whether the ledger maps code HEAD). Read-only apart from
        that optional fetch. `include_drift` measures the reference anchors of onboarding cards
        and the validity of the tree -- a stale reference is reported and never blocks by itself; whether
        an invariant is still current is answered by `knowledge_read` and
        `knowledge_integrity_check`, not here; a leaf that changes a source file must change
        that file's card or record why not (MIK-R30); and nothing is repaired (`citation_fix`
        re-records anchors that only moved, and a curator re-authors a stale one); omit it
        and nothing is measured. For a canonical leaf, pass task_context with task_document_ref and
        contract_path together; the admitted enclosure supplies code and memory roots, and repo_id
        must match its repository. Omit task_context for configured Projects scope."""
        return context_packet_payload(
            config,
            ContextPacketRequest(
                repo_id=repo_id,
                include_drift=include_drift,
                include_freshness=include_freshness,
            ),
            task_context=task_context,
        )

    @server.tool()
    def read_ar_files(
        repo_id: str,
        files: list[dict[str, Any]],
        refresh: bool = False,
        task_context: TaskScopedReaderContext | None = None,
    ) -> dict[str, Any]:
        """Read-only batch read of up to 5 repo-relative paths inside an AR-managed repo,
        each paired with its file-level onboarding. Per file pass {"path": "...", "source":
        "full" | {"startLine": N, "endLine": M}, "onboarding": false?}; the response returns
        {path, status, source?, onboarding?} where status is the onboarding-lookup outcome
        (found | missing | disabled | unsupported | not_requested) and source is the full file
        or the exact requested range (omitted for absent or non-UTF-8 files). It also
        auto-attaches the repo overview and the governing route-overview chain, deduplicated
        per session (served once, re-served only when changed; pass refresh=true to force
        re-serve, e.g. after a compaction). Route-index rule: a file inside sourceScope but
        absent from coveredFiles reports missing without probing. In a managed repo this is
        the read for the research phase (the lifecycle up to the build decision): use it
        instead of a native read to get each file paired with its onboarding plus the
        repository and governing route overviews. Native read is the edit precondition once
        building begins. For a canonical leaf, pass task_context with task_document_ref and
        contract_path together; the admitted enclosure supplies code and memory roots, and repo_id
        must match its repository. Omit task_context for configured Projects scope."""
        return read_ar_files_payload(
            config,
            repo_id,
            files,
            refresh=refresh,
            task_context=task_context,
        )

    @server.tool()
    def resolve_context(
        repo_id: str,
        task_name: str | None = None,
        parent_task: str | None = None,
        leaf_id: str | None = None,
        contract_path: str | None = None,
        *,
        worktree_name: str | None = None,
        topology: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a repository's coordination/memory context: topology (external), code and memory
        roots, settings paths, storage mode, and path rules. Read-only. Use this (or context_packet)
        before relying on onboarding, task files, or provider tools."""
        return resolve_context_payload(
            config,
            TaskRef(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                leaf_id=leaf_id,
                parent_task=parent_task,
            ),
            worktree_name=worktree_name,
            topology=topology,
        )


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
