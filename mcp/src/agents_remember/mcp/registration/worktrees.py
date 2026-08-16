"""Worktree tools for the working half of a task: create, re-attach, observe, sync."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.task_ref import TaskRef
from agents_remember.application.worktree_tools import StartExecution, TaskBases, TaskIdentity
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from ..tools import (
    worktree_attach_payload,
    worktree_start_payload,
    worktree_status_payload,
    worktree_sync_payload,
)


def register_worktree_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the worktree tools, split by whether the contract exists yet."""
    _register_worktree_start_tools(server, config)
    _register_worktree_working_tools(server, config)


def _register_worktree_start_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Create or load the task contract and its git worktrees."""

    @server.tool()
    def worktree_start(
        repo_id: str,
        task_name: str,
        worktree_name: str,
        leaf_id: str | None = None,
        parent_task: str | None = None,
        *,
        workflow_kind: str = "light-task",
        source_branch: str | None = None,
        work_branch: str | None = None,
        memory_mode: str | None = None,
        memory_choice: str | None = None,
        stale_base_choice: str | None = None,
        skip_provider_setup: bool = False,
        retry_provider_setup: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create or load a task contract plus code (and external-memory) git worktrees. Mutating:
        creates branches/worktrees on disk. Preview with dry_run=true. Driven by the
        c-09-git-worktree-manager skill workflow; workflow_kind is the task format ('light-task' or 'chat-task').
        memory_mode is 'internal', 'external', or 'disabled'.

        A leaf whose cleanup already completed must be reset explicitly through
        task_reopen first. In that terminal state this tool returns reopen-required with
        the exact task_reopen preview arguments instead of attempting descendant-lineage
        checks against branches cleanup deliberately removed.

        Stale-base preflight (issue #54): start refuses when a source branch is behind or
        diverged from its remote tracking branch (a stale base produces wrong code and
        defeats the provider seed fast-path). On a blocked 'choose_stale_base_recovery'
        result, advance the protected source through its repository landing plane and retry,
        or re-run with stale_base_choice='proceed-stale' as an explicit override. A missing
        external-memory source branch is a topology refusal; start never creates or advances
        protected source refs.

        Returns within seconds: provider setup runs in the background and the response's
        providers block reports state 'starting' with a progressFile. Poll worktree_status
        until providers reaches a terminal state (seed copy = seconds; a refused seed falls
        back to a full reindex = minutes, flagged as seedFallback). On a failed or stale
        setup, re-run with retry_provider_setup=true to relaunch it for the existing
        contract."""
        return worktree_start_payload(
            config,
            TaskIdentity(
                repo_id=repo_id,
                task_name=task_name,
                worktree_name=worktree_name,
                leaf_id=leaf_id,
                parent_task=parent_task,
                workflow_kind=workflow_kind,
            ),
            bases=TaskBases(
                source_branch=source_branch,
                work_branch=work_branch,
                memory_mode=memory_mode,
                memory_choice=memory_choice,
                stale_base_choice=stale_base_choice,
            ),
            execution=StartExecution(
                dry_run=dry_run,
                skip_provider_setup=skip_provider_setup,
                retry_provider_setup=retry_provider_setup,
            ),
        )


def _register_worktree_working_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Work with a contract that already exists: re-attach, observe, pull the base forward."""

    @server.tool()
    def worktree_attach(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        leaf_id: str | None = None,
        parent_task: str | None = None,
        *,
        on_unsaved: str | None = None,
    ) -> dict[str, Any]:
        """Re-attach to an existing task contract without mutating git, resuming its lifecycle.
        Read-only; resume a task by task_name or contract_path. If an unsaved fleeting lifecycle is
        active, on_unsaved='save' (promote) or 'discard' (abandon) resolves the save gate."""
        return worktree_attach_payload(
            config,
            TaskRef(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                leaf_id=leaf_id,
                parent_task=parent_task,
            ),
            on_unsaved=on_unsaved,
        )

    @server.tool()
    def worktree_status(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        leaf_id: str | None = None,
        parent_task: str | None = None,
    ) -> dict[str, Any]:
        """Report a task's worktree lifecycle phase, dirty flags, and next-step hints. Read-only.
        While background provider setup runs, the providers block carries the live phase,
        heartbeat age, and seedFallback; terminal states are ok / ready-with-failed-phases /
        failed (stale = setup thread died; retry via worktree_start retry_provider_setup)."""
        return worktree_status_payload(
            config,
            TaskRef(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                leaf_id=leaf_id,
                parent_task=parent_task,
            ),
        )

    @server.tool()
    def worktree_sync(
        contract_path: str,
        memory_sync_choice: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Pull the moved official line into a live worktree (issue #54). Mutating: fetches
        upstreams, merges the source branch into the code work branch (ff when unchanged),
        fast-forwards the memory work branch under parked memory, and advances the contract's
        recorded base pair atomically (the new code tip must be ledger-mapped at the official
        memory tip — a mid-cycle official line blocks with guidance to run carryover first).
        Preview with dry_run=true. worktree_status's freshness block recommends this tool when
        the recorded bases fall behind the local source branch tips. If the memory work branch
        has local commits and official memory moved, the result blocks with
        memory_sync_choice='merge-memory' (ledger conflicts abort cleanly) or 'skip-memory'
        (defer to end-of-task carryover). Sync early — before memories are written — for the
        friction-free fast-forward path."""
        return worktree_sync_payload(
            config,
            contract_path,
            memory_sync_choice=memory_sync_choice,
            dry_run=dry_run,
        )
