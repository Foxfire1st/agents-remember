"""Worktree tools for the working half of a task: create, re-attach, observe, sync, stop."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    StartExecution,
    TaskBases,
    TaskIdentity,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.worktree import (
    MemorySyncChoice,
    SyncResolutionAction,
    SyncResolutionInput,
)

from ..tools import (
    worktree_attach_payload,
    worktree_enclosure_adopt_payload,
    worktree_pause_payload,
    worktree_start_payload,
    worktree_status_payload,
    worktree_sync_payload,
)
from ..tools.enclosure_adoption import EnclosureAdoptionRequest


def register_worktree_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the worktree tools, split by whether the contract exists yet."""
    _register_worktree_start_tools(server, config)
    _register_worktree_address_tools(server, config)
    _register_worktree_observation_tools(server, config)
    _register_worktree_stop_tools(server, config)


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
        """Create or load a task contract with its code and memory worktrees. Answers the state
        reached (started, would-start, blocked) and the next call with its arguments; the whole
        result is in the log file. Poll worktree_status for progress. Reference: docs/reference/mcp-tools.md."""
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


def _register_worktree_address_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Adopt or re-attach one existing worktree enclosure."""

    @server.tool()
    def worktree_enclosure_adopt(
        contract_path: str,
        expected_worktree_group: str,
        rationale: str,
        *,
        dry_run: bool = True,
        approved: bool = False,
        expected_publication_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly adopt one readable pre-locator enclosure. Dry-run by default. This is the
        only old-layout address migration. It validates one exact contract/root, preserves
        source digests and an audit receipt, and never runs from normal status, control,
        contract writes, or schema-1 repair. Answers the state reached. Reference:
        docs/reference/mcp-tools.md."""
        return worktree_enclosure_adopt_payload(
            config,
            EnclosureAdoptionRequest(
                contract_path=contract_path,
                expected_worktree_group=expected_worktree_group,
                rationale=rationale,
                dry_run=dry_run,
                approved=approved,
                expected_publication_request_id=expected_publication_request_id,
            ),
        )

    @server.tool()
    def worktree_attach(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        leaf_id: str | None = None,
        parent_task: str | None = None,
    ) -> dict[str, Any]:
        """Re-attach to an existing task contract without mutating Git. Answers the state reached
        (attached, attached-existing-contract, blocked) and the next call; the whole result is
        in the log file. Reference: docs/reference/mcp-tools.md."""
        return worktree_attach_payload(
            config,
            TaskRef(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                leaf_id=leaf_id,
                parent_task=parent_task,
            ),
        )


def _register_worktree_observation_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Observe or pull forward a canonically addressable worktree enclosure."""

    @server.tool()
    def worktree_status(
        repo_id: str,
        *,
        task_name: str | None = None,
        contract_path: str | None = None,
        leaf_id: str | None = None,
        parent_task: str | None = None,
        caller: DeclaredCaller | None = None,
    ) -> dict[str, Any]:
        """Report a task worktree's lifecycle status: the phase reached (worktree-started,
        integration-pending, closeout-pending, integration-blocked, carryover-pending,
        cleanup-pending, cleanup-completed, abandoned), what blocks, the one
        following call, and whether task-document copies differ from their sources. Lineage and
        refresh detail stays in the log file. Reference: docs/reference/mcp-tools.md."""
        return worktree_status_payload(
            config,
            TaskRef(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                leaf_id=leaf_id,
                parent_task=parent_task,
            ),
            caller=caller,
        )

    @server.tool()
    def worktree_sync(
        contract_path: str,
        memory_sync_choice: MemorySyncChoice | None = None,
        resolution_action: SyncResolutionAction | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Pull the official line into a live worktree. Returns synced,
        already-current, would-sync, sync-active, sync-resolution-required,
        memory-sync-choice-required or blocked, with next-call arguments and a log path.
        Tree changes mark the prior review pre-sync and offer a new review; sync movement
        never blocks. The product merges memory lines only here; raw git merge is a hand edit
        it cannot prevent. Every memory landing judges its tree; a fast-forward judges what
        it adopts before moving any ref.

        Reference: docs/reference/mcp-tools.md."""
        return worktree_sync_payload(
            config,
            contract_path,
            memory_sync_choice=memory_sync_choice,
            resolution=SyncResolutionInput(action=resolution_action),
            dry_run=dry_run,
        )


def _register_worktree_stop_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Stop a master. The pause and the publication are two different verbs."""

    @server.tool()
    def worktree_pause(
        contract_path: str,
    ) -> dict[str, Any]:
        """PAUSE an atomic master: it stops, hands control back, and releases the master's atomic-series activation selection. Publishes NOTHING: publication is a separate, explicitly requested PUBLICATION through worktree_checkpoint_landing. States: paused, blocked. Answers the state reached and the next call. Reference: docs/reference/mcp-tools.md."""
        return worktree_pause_payload(
            config,
            contract_path,
        )
