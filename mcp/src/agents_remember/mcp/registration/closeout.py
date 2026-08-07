"""Worktree tools for the landing half of a task: close out, integrate, reclaim."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.worktree_tools import (
    CloseoutApproval,
    CloseoutCommitMessages,
)

from ..config import McpRuntimeConfig
from ..tools import (
    worktree_abandon_payload,
    worktree_cleanup_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
)


def register_closeout_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_closeout_preview(
        contract_path: str,
        code_commit_message: str,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
    ) -> dict[str, Any]:
        """Non-mutating preview of a worktree-backed closeout: proposed commits and whether
        the leaf change-set-scoped quality gate (--targeted: changed files, reverse-import
        closure, derived test subset, mandatory CRAP enforcement over changed modules) runs
        over the staged task worktree before the code commit. memory_quality_check stays a
        per-leaf closeout gate."""
        return worktree_closeout_preview_payload(
            config,
            contract_path,
            CloseoutCommitMessages(
                code=code_commit_message,
                memory=memory_commit_message,
                ledger=ledger_commit_message,
            ),
        )

    @server.tool()
    def worktree_closeout_apply(
        contract_path: str,
        intent_note: str,
        code_commit_message: str,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply an approved worktree closeout. When code would commit and the checkout
        carries the wrapper, resets the index, stages the whole task worktree, and runs the
        leaf change-set-scoped contract (--targeted: changed files, reverse-import closure,
        derived test subset, mandatory CRAP enforcement over changed modules) over exactly
        that staged content, before any code, memory, ledger, contract, or applied-gate
        commit; then commits in order. The full wrapper is NOT a leaf gate: it runs once per
        master at the master integration gate, memory-capped. A refused gate leaves the task
        worktree staged and commits nothing; retries reset and restage from the working tree.
        MUTATING and commit-gated: preview and approval precede apply. Requires intent_note."""
        return worktree_closeout_apply_payload(
            config,
            contract_path,
            CloseoutCommitMessages(
                code=code_commit_message,
                memory=memory_commit_message,
                ledger=ledger_commit_message,
            ),
            CloseoutApproval(intent_note=intent_note, dry_run=dry_run),
        )

    @server.tool()
    def worktree_integrate(
        contract_path: str,
        strategy: str = "ff-only",
        ledger_commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Land a closed task branch back onto its source branch (strategy 'ff-only' or
        'replay'). Runs the altitude-routed quality gate before any merge: leaf integration
        certifies its change set (--targeted); master integration runs the full wrapper once,
        memory-capped (orchestration.qualityGate.memoryCapBytes), inside this step. MUTATING:
        moves branch refs; preview with dry_run=true."""
        return worktree_integrate_payload(
            config,
            contract_path,
            strategy=strategy,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_cleanup(
        contract_path: str, dry_run: bool = False, teardown_providers: bool = True
    ) -> dict[str, Any]:
        """Remove a task's worktrees and merged task branches after integration. MUTATING and
        destructive (deletes worktrees/branches) — run only after worktree_integrate. Preview with
        dry_run=true. teardown_providers=true (default) also reclaims the worktree's isolated
        provider stack (containers, networks, provider-runtime tree)."""
        return worktree_cleanup_payload(
            config, contract_path, dry_run=dry_run, teardown_providers=teardown_providers
        )

    @server.tool()
    def worktree_abandon(
        contract_path: str, dry_run: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Discard a worktree-backed task WITHOUT integrating it: reclaim its isolated provider
        stack (containers, networks, provider-runtime tree), remove the code and memory worktrees,
        delete the task branches, and remove the worktree group dir. MUTATING and destructive.
        Unlike worktree_cleanup it needs no completed integration. Without force it refuses dirty
        worktrees and unmerged branches (reporting the commits); force=true discards them
        (git worktree remove --force, git branch -D). Preview with dry_run=true."""
        return worktree_abandon_payload(config, contract_path, dry_run=dry_run, force=force)
