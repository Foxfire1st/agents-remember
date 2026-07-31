"""Memory-root tools: drift, quality, route index, init, baseline, and carryover."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.controllers.memory_tools import (
    CarryoverCommitMessages,
    CarryoverSelection,
    MemoryBranches,
)

from ..config import McpRuntimeConfig
from ..tools import (
    drift_check_payload,
    memory_baseline_adopt_payload,
    memory_baseline_status_payload,
    memory_carryover_apply_payload,
    memory_carryover_plan_payload,
    memory_init_payload,
    memory_quality_check_payload,
    route_index_refresh_payload,
)


def register_memory_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def drift_check(repo_id: str, detail_limit: int = 50) -> dict[str, Any]:
        """Task-start gate: classify how far onboarding has drifted from the code since it was last
        verified. Read-only (writes only a temp drift report). A nonzero actionable count is
        expected after code changes, not a failure."""
        return drift_check_payload(config, repo_id, detail_limit=detail_limit)

    @server.tool()
    def memory_quality_check(
        repo_id: str,
        checks: list[str] | None = None,
        detail_limit: int = 50,
    ) -> dict[str, Any]:
        """Closeout memory-quality gate: runs drift-integrity and style checks over onboarding.
        Read-only. ok=false means findings exist (e.g. drifted onboarding), not that the tool
        failed. Pass `checks` to run a subset; default runs all."""
        return memory_quality_check_payload(
            config,
            repo_id,
            checks=checks,
            detail_limit=detail_limit,
        )

    @server.tool()
    def route_index_refresh(repo_id: str, dry_run: bool = False) -> dict[str, Any]:
        """Regenerate the overview.index.json route indexes so they match the current onboarding
        tree. Writes index files under the memory root; does not touch source or onboarding
        content. Preview with dry_run=true."""
        return route_index_refresh_payload(config, repo_id, dry_run=dry_run)

    @server.tool()
    def memory_init(
        repo_id: str,
        dry_run: bool = False,
        initialize_git: bool = True,
    ) -> dict[str, Any]:
        """Initialize or repair a repository's memory root (scaffold system/ files, onboarding
        layout, optionally `git init`). Does not overwrite existing onboarding content. Preview
        with dry_run=true. Usually driven by the c-00-initialize-memory-repo skill."""
        return memory_init_payload(
            config,
            repo_id,
            dry_run=dry_run,
            initialize_git=initialize_git,
        )

    @server.tool()
    def memory_baseline_status(repo_id: str) -> dict[str, Any]:
        """Report drift and ledger state to decide whether an external-memory baseline can be
        adopted. Read-only."""
        return memory_baseline_status_payload(config, repo_id)

    @server.tool()
    def memory_baseline_adopt(
        repo_id: str,
        accept_drift: bool = False,
        source_branch: str | None = None,
        work_branch: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create the first ledgered memory baseline for an external memory repo. Mutating: writes
        the ledger and commits memory. Gated on clean drift unless accept_drift=true. Preview with
        dry_run=true. Usually driven by the c-10-adopt-memory-baseline skill."""
        return memory_baseline_adopt_payload(
            config,
            repo_id,
            accept_drift=accept_drift,
            branches=MemoryBranches(source_branch=source_branch, work_branch=work_branch),
            dry_run=dry_run,
        )

    @server.tool()
    def memory_carryover_plan(
        repo_id: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Plan (non-mutating) carrying richer onboarding from a source/feature branch into official
        memory once code has landed. Returns a plan to review; apply with memory_carryover_apply."""
        return memory_carryover_plan_payload(
            config,
            CarryoverSelection(
                repo_id=repo_id,
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
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        intent_note: str,
        replace_existing: bool = False,
        include_review_required: list[str] | None = None,
        memory_commit_message: str = "Carry over landed branch memory",
        ledger_commit_message: str = "Record branch memory carryover",
    ) -> dict[str, Any]:
        """Apply an approved carryover plan: writes onboarding and commits memory + ledger. Mutating
        and approval-gated — run memory_carryover_plan first and only apply after the code has landed
        officially. Requires intent_note."""
        return memory_carryover_apply_payload(
            config,
            CarryoverSelection(
                repo_id=repo_id,
                source_memory=source_memory,
                official_code_ref=official_code_ref,
                source_code_ref=source_code_ref,
                old_base=old_base,
                replace_existing=replace_existing,
            ),
            intent_note=intent_note,
            include_review_required=include_review_required,
            messages=CarryoverCommitMessages(
                memory=memory_commit_message, ledger=ledger_commit_message
            ),
        )
