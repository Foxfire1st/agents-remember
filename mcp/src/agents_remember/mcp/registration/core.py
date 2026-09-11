"""Server identity, orientation reads, and runtime installation tools."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.runtime.startup import mcp_serving_build_payload
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.core import ServingBuildPayload

from ..tools import (
    context_packet_payload,
    ping_payload,
    read_ar_files_payload,
    resolve_context_payload,
    runtime_install_payload,
    server_info_payload,
    skills_install_payload,
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

    @server.tool()
    def ping() -> dict[str, Any]:
        """Liveness check. Returns server name, version, and transport. Read-only; no side effects."""
        return ping_payload()

    @server.tool()
    def server_info() -> dict[str, Any]:
        """Report the resolved configuration: coordination/workspace/transcript roots, allowed
        repo ids, allowed provider ids, full tool list, and the boot-resolved package identity.
        Read-only; reflects the settings and exact runtime loaded at startup (settings-file or
        package changes need a harness restart to take effect)."""
        return server_info_payload(config, serving_build)


def _register_orientation_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Read-only reads that orient a session in a repository before it changes anything."""

    @server.tool()
    def context_packet(
        repo_id: str,
        include_providers: bool = True,
        include_drift: bool = False,
        include_freshness: bool = False,
    ) -> dict[str, Any]:
        """Bundle a repository's current state into one packet: repo/git state, resolved paths,
        memory mode/storage, worktree state, and (optionally) provider status, drift, and branch
        freshness (include_freshness fetches remote-tracking refs and reports ahead/behind for the
        code and memory checkouts plus whether the ledger maps code HEAD). Read-only apart from
        that optional fetch. Preferred single call to orient at task start."""
        return context_packet_payload(
            config,
            repo_id,
            include_providers=include_providers,
            include_drift=include_drift,
            include_freshness=include_freshness,
        )

    @server.tool()
    def read_ar_files(
        repo_id: str,
        files: list[dict[str, Any]],
        refresh: bool = False,
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
        building begins."""
        return read_ar_files_payload(config, repo_id, files, refresh=refresh)

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
        """Resolve a repository's coordination/memory context: topology (internal/external), code
        and memory roots, settings paths, storage mode, and path rules. Read-only. Use this (or
        context_packet) before relying on onboarding, task files, or provider tools."""
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
    ) -> dict[str, Any]:
        """Install/refresh the packaged coordinator runtime into the coordination root. Safe to
        re-run over an existing install.

        PRESERVES user data: memory-repos/ (onboarding) and providers/data/ (DBs, Ollama model,
        FalkorDB graph). REPLACES managed scaffold: skills/, AGENTS.md templates, provider
        compose/docker/requirements ("shape"), and with install_provider_deps=true may refresh
        providers/runners/ after stopping watchers so containers rebind cleanly. Removes the
        legacy scripts/ dir.

        With install_provider_deps=true (default) it also builds provider images, but SKIPS any
        image whose tag already exists, then starts/rechecks watchers without rebuilding indexes.
        Pass no_cache=true to force a true from-scratch rebuild (bypasses that skip AND adds
        --no-cache to docker build). include_benchmarks=true also installs benchmark fixtures.
        ALWAYS preview with dry_run=true first."""
        return runtime_install_payload(
            config,
            dry_run=dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
            no_cache=no_cache,
        )

    @server.tool()
    def skills_install(
        dry_run: bool = False,
        overwrite: bool = False,
        archive_existing: bool = False,
    ) -> dict[str, Any]:
        """Copy the packaged skills into the harness skill root (e.g. .claude/skills) so the harness
        can discover them. The packaged skills are flat (one folder per skill), so each is copied by
        its frontmatter name. overwrite replaces existing skill files; archive_existing backs them up
        first. Most harnesses only discover newly-installed skills after a restart. Preview with
        dry_run=true."""
        return skills_install_payload(
            config,
            dry_run=dry_run,
            overwrite=overwrite,
            archive_existing=archive_existing,
        )
