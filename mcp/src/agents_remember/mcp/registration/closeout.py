"""Worktree tools for the landing half of a task: close out, integrate, reclaim."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.controllers.worktree_tools import (
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
        """Non-mutating preview of a worktree-backed closeout. Reports the proposed
        code/memory/ledger commits and whether strict project-owned code quality, including
        mandatory CRAP enforcement, will run over the staged task worktree
        before the code commit. Pair with worktree_closeout_apply after approval."""
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
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply an approved worktree closeout. When code would commit AND the checkout carries
        the project-owned quality wrapper, resets the index, stages the whole task worktree, and
        runs strict quality with mandatory CRAP enforcement over exactly that staged content,
        before any code, memory, ledger, contract, or applied-gate commit; then commits code,
        memory, and ledger in order. Staging is what lets the gate see files the task created
        rather than only the ones it edited; the reset is what makes a retry stage what a first
        run would, instead of inheriting a refused attempt's index. Staging is not undone if the
        gate refuses: the checkout staged is the task's own disposable worktree. The two refusals
        guard that staging step, so they run only where the gate runs -- it refuses before staging
        when the code checkout is not a task worktree (a series/master contract records the
        repository path itself) or has unresolved merge conflicts. A checkout carrying no wrapper
        runs neither the gate nor those refusals, and reaches the ordinary commit step's own
        'git add -A' exactly as it always has. MUTATING and commit-gated: preview and
        approval precede apply. Requires intent_note."""
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
        """Land a closed task branch back onto its source branch (strategy 'ff-only' or 'replay').
        MUTATING: moves branch refs. Do not move protected branches without explicit approval.
        Preview with dry_run=true."""
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
