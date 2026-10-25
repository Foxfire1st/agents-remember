"""Memory-root tools: drift, quality, route index, init, baseline, and carryover."""

from importlib import import_module
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.memory import (
    MemoryQualityCheckRequest,
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
)

from .lazy import tools


def register_memory_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the memory tools, split by what they do to the memory root."""
    _register_memory_health_tools(server, config)
    _register_memory_baseline_tools(server, config)
    _register_memory_carryover_tools(server, config)


def _register_memory_health_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Read the memory root's health: drift, quality findings, route indexes.

    Each takes the same optional `contract_path` the worktree verbs take. Omitted, it means
    the configured official memory repo, exactly as before. Supplied, the tool acts on that
    leaf enclosure's memory worktree and measures it against the leaf's code worktree -- which
    is what lets a curator check its own change-set before handing it back, instead of the
    manager discovering the findings at the commit gate.
    """

    @server.tool()
    def memory_quality_check(
        request: MemoryQualityCheckRequest,
    ) -> dict[str, Any]:
        """
        Check memory before closeout: findings, counts and the checklist location, not semantic
        certification. Pass a sync, start or poll request; candidate runs use the leaf contract.
        Ready memory also requires validated curator_coherence. Four operations have no tool, by
        design: the conversion (it runs inside the crossing sync, or once per repository), the
        index build (every read builds it on demand), the formatter (the writer renders canonical
        files), and the worklist (this tool computes it and `knowledge_integrity_check` returns
        it). Longer explanation: docs/tool-details.md.
        """
        if isinstance(request, MemoryQualityPollRequest):
            return tools.memory_quality_check_poll_payload(config, request)
        if isinstance(request, MemoryQualityStartRequest):
            return tools.memory_quality_check_start_payload(config, request)
        return tools.memory_quality_check_payload(config, request)

    @server.tool()
    def citation_fix(
        repo_id: str,
        contract_path: str | None = None,
        *,
        document: str | None = None,
        documents: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Repair moved citation anchors in a leaf memory worktree. Pass repo_id and contract_path, with documents for a batch or document for one card; preview with dry_run. Renames, deletions and ambiguous anchors remain curator work. Old-format memory is refused: convert before repair. Converted repairs author sidecar references; full lists are in logged detail. Longer explanation: docs/tool-details.md."""
        return tools.citation_fix_payload(
            config,
            repo_id,
            contract_path=contract_path,
            operation_scope=import_module(
                "agents_remember.application.memory_tools"
            ).CitationOperationScope(
                document=document,
                documents=tuple(documents) if documents is not None else None,
            ),
            dry_run=dry_run,
        )

    @server.tool()
    def citation_note_revision(
        repo_id: str,
        contract_path: str,
        *,
        card: str,
        reference: int,
        note: str,
        base_digest: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Revise one reference's note in a leaf memory worktree, keeping every target, anchor and other reference byte-identical. Pass repo_id, contract_path, card (the converted card), reference (n), note and base_digest (the sidecar's sha256 as read); preview with dry_run. A missing key, a stale base_digest, an empty note, an absent card or sidecar are refused, nothing written. Moved anchors are citation_fix's work. More: docs/tool-details.md."""
        return tools.citation_note_revision_payload(
            config,
            repo_id,
            contract_path,
            card=card,
            reference=reference,
            note=note,
            base_digest=base_digest,
            dry_run=dry_run,
        )

    @server.tool()
    def route_index_refresh(
        repo_id: str,
        dry_run: bool = False,
        contract_path: str | None = None,
    ) -> dict[str, Any]:
        """Refresh route indexes for a leaf memory worktree using repo_id and contract_path. Preview with dry_run; unscoped writes require pre-baseline admission. More: docs/tool-details.md."""
        return tools.route_index_refresh_payload(
            config, repo_id, dry_run=dry_run, contract_path=contract_path
        )


def _register_memory_baseline_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Stand a memory root up and give it its first attributed baseline."""

    @server.tool()
    def memory_init(
        repo_id: str,
        dry_run: bool = False,
        initialize_git: bool = True,
        initial_branch: str | None = None,
    ) -> dict[str, Any]:
        """Initialize or repair a repository's memory root (scaffold `system/` files, the
        `onboarding/` and `docs/` layout, optionally `git init`). `initial_branch` names the code
        branch the memory is the foundation of: it becomes the memory repository's initial branch
        and the recorded `agents-remember.defaultBranch` that baseline adoption and every worktree
        entry point check against. Omitted, it defaults to the configured code repository's
        currently checked-out branch, and the call refuses rather than inventing a name when
        neither is available. Creates missing paths only and never overwrites existing onboarding
        content. Preview with dry_run=true. Usually driven by the c-00-initialize-memory-repo
        skill."""
        return tools.memory_init_payload(
            config,
            repo_id,
            dry_run=dry_run,
            initialize_git=initialize_git,
            initial_branch=initial_branch,
        )

    @server.tool()
    def memory_baseline_status(repo_id: str) -> dict[str, Any]:
        """Report drift and Git attribution state to decide whether an external-memory baseline can be
        adopted. Read-only."""
        return tools.memory_baseline_status_payload(config, repo_id)

    @server.tool()
    def memory_baseline_adopt(
        repo_id: str,
        accept_drift: bool = False,
        source_branch: str | None = None,
        work_branch: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Adopt the first attributed external-memory baseline and commit memory. Pass repo_id; preview with dry_run. accept_drift lifts the drift gate on stale anchors only: the baseline still refuses a stale code or test reference anchor as R198.1-reference-anchor. More: docs/tool-details.md."""
        return tools.memory_baseline_adopt_payload(
            config,
            repo_id,
            accept_drift=accept_drift,
            branches=import_module("agents_remember.application.memory_tools").MemoryBranches(
                source_branch=source_branch, work_branch=work_branch
            ),
            dry_run=dry_run,
        )


def _register_memory_carryover_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Plan and apply landed onboarding into an ordinary recovery leaf."""

    @server.tool()
    def memory_carryover_plan(
        repo_id: str,
        *,
        contract_path: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Plan converted pairs/records at equal versions; observe official-code anchors, review conflicts. Curator authors history; no commit/report. More: docs/tool-details.md."""
        return tools.memory_carryover_plan_payload(
            config,
            import_module("agents_remember.application.memory_tools").CarryoverSelection(
                repo_id=repo_id,
                contract_path=contract_path,
                source_memory=source_memory,
                official_code_ref=official_code_ref,
                source_code_ref=source_code_ref,
                old_base=old_base,
                replace_existing=replace_existing,
            ),
        )

    @server.tool()
    def memory_carryover_apply(
        repo_id: str,
        *,
        contract_path: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        intent_note: str,
        replace_existing: bool = False,
        include_review_required: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply converted pairs/records; reanchor, index, validate. Refusal restores bytes; skip edited targets, retry safely. No commit/history carry; curator authors rows before closeout. Requires intent_note. More: docs/tool-details.md."""
        return tools.memory_carryover_apply_payload(
            config,
            import_module("agents_remember.application.memory_tools").CarryoverSelection(
                repo_id=repo_id,
                contract_path=contract_path,
                source_memory=source_memory,
                official_code_ref=official_code_ref,
                source_code_ref=source_code_ref,
                old_base=old_base,
                replace_existing=replace_existing,
            ),
            intent_note=intent_note,
            include_review_required=include_review_required,
        )
