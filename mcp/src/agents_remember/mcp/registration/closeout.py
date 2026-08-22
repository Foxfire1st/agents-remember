"""Worktree tools for the landing half of a task: close out, integrate, reclaim."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.direct_landing import DirectLandingRequest
from agents_remember.application.worktree_tools import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation import IntegrateStrategy

from ..tools import (
    direct_landing_payload,
    worktree_abandon_payload,
    worktree_cleanup_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
    worktree_operation_cancel_payload,
)


def register_closeout_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register landing tools through cohesive bounded registration groups."""
    _register_direct_landing_tools(server, config)
    _register_closeout_command_tools(server, config)
    _register_integration_command_tools(server, config)
    _register_reclamation_command_tools(server, config)


def _register_direct_landing_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def direct_landing(
        contract_path: str,
        code_commit: str,
        *,
        memory_commit_message: str | None = None,
        ledger_commit_message: str | None = None,
        intent_note: str = "",
        candidate_tree: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Verify one series code commit and lock-serialize its memory + ledger writes.

        The direct landing is the branch-addressed counterpart of the worktree closeout
        commit phase for sanctioned direct execution: it binds the task-root series
        contract (series-contract.md), verifies the exact code commit is the current
        series branch HEAD, commits external-memory content, and prepends the
        code-to-memory ledger row with the same ledger semantics as the worktree path.
        Message intent is normalized before the integration authority lock. The two
        synchronous Git commits are not a durable atomic operation; interruption can leave
        memory content ahead of its ledger row and requires the separately sequenced
        operation-recovery work. Policy-gated:
        directExecutionEnabled must be set in the MCP
        authority settings. The code commit is verified, never created. Pass
        candidate_tree (the staged candidate the owner gated through the Dagger
        --source/--repository-bundle contract) to keep the gate strictly pre-commit:
        a moved branch after the gate is refused before any memory or ledger commit.
        Each of memory_commit_message and ledger_commit_message must be explicit and
        nonblank only when its contract-derived leg is enabled; typed not-applicable
        legs may omit the corresponding message. The verified-existing code commit has
        no code-message input.
        MUTATING (memory + ledger commits); preview with dry_run=true."""
        return direct_landing_payload(
            config,
            DirectLandingRequest(
                contract_path=contract_path,
                code_commit=code_commit,
                memory_commit_message=memory_commit_message,
                ledger_commit_message=ledger_commit_message,
                intent_note=intent_note,
                candidate_tree=candidate_tree,
                dry_run=dry_run,
            ),
        )


def _register_closeout_command_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_closeout_preview(
        *,
        contract_path: str,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
        ledger_commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Non-mutating preview of a worktree-backed closeout: proposed commits and whether
        the leaf change-set-scoped quality gate (--targeted: changed files, reverse-import
        closure, derived test subset, mandatory CRAP enforcement over changed modules) runs
        over the staged task worktree before the code commit. memory_quality_check stays a
        per-leaf closeout gate. Every contract-enabled commit leg requires its explicit,
        nonblank message; typed not-applicable legs may be omitted."""
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
        *,
        contract_path: str,
        intent_note: str,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
        ledger_commit_message: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Start or observe an approved task-bound worktree closeout. A mutating call
        returns promptly with queued/running/current-phase state; the plane-owned worker
        survives this MCP request and server process, and worktree_status observes it by
        task context without a job id. When code would commit and the checkout
        carries the wrapper, resets the index, stages the whole task worktree, and runs the
        leaf change-set-scoped contract (--targeted: changed files, reverse-import closure,
        derived test subset, mandatory CRAP enforcement over changed modules) over exactly
        that staged content, before any code, memory, ledger, contract, or applied-gate
        commit; then commits in order. The full wrapper is NOT a leaf gate: it runs once per
        master at the master integration gate through the exact settings-selected local or
        Dagger executor. A refused gate leaves the task worktree staged and commits nothing;
        retries reset and restage only the operation's immutable accepted candidate tree.
        MUTATING and commit-gated: preview and approval precede apply. Requires intent_note.
        Every contract-enabled commit leg requires its explicit, nonblank message; typed
        not-applicable legs may be omitted.
        Repeat the same task input to observe/recover it; conflicting input refuses."""
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


def _register_integration_command_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_integrate(
        *,
        contract_path: str,
        strategy: IntegrateStrategy = "ff-only",
        ledger_commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Start or observe task-bound landing onto its source branch (strategy 'ff-only'
        or 'replay'). A mutating call returns promptly; worktree_status projects the durable
        phase and result without exposing operation identity. Leaf integration reuses the
        acceptance bound to its closeout commit without rerunning it; master integration runs
        the full wrapper once through the pinned Dagger executor inside this step.
        An explicit orchestration.qualityGate.memoryCapBytes remains available. MUTATING:
        moves branch refs; preview with dry_run=true. Repeat the same task input to
        observe/recover it; conflicting input refuses."""
        return worktree_integrate_payload(
            config,
            contract_path,
            strategy=strategy,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_operation_cancel(
        *,
        contract_path: str,
        operation_kind: str,
        intent_note: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Cancel a task's closeout or integration before its irreversible boundary.
        Addressed only by contract plus operation kind; no job/process id exists at this
        boundary. After approval claim or source merge begins, cancellation refuses and the
        same durable operation must recover. Preview with dry_run=true."""
        return worktree_operation_cancel_payload(
            config,
            contract_path,
            operation_kind=operation_kind,
            intent_note=intent_note,
            dry_run=dry_run,
        )


def _register_reclamation_command_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_cleanup(
        *, contract_path: str, dry_run: bool = False, teardown_providers: bool = True
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
        *, contract_path: str, dry_run: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Discard a worktree-backed task WITHOUT integrating it: reclaim its isolated provider
        stack (containers, networks, provider-runtime tree), remove the code and memory worktrees,
        delete the task branches, and remove the worktree group dir. MUTATING and destructive.
        Unlike worktree_cleanup it needs no completed integration. Without force it refuses dirty
        worktrees and unmerged branches (reporting the commits); force=true discards them
        (git worktree remove --force, git branch -D). Preview with dry_run=true."""
        return worktree_abandon_payload(config, contract_path, dry_run=dry_run, force=force)
