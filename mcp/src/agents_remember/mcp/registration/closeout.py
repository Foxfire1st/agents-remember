"""Worktree tools for the landing half of a task: close out, integrate, reclaim."""

from importlib import import_module
from typing import Any

from mcp.server.mcpserver import MCPServer

from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
    LandedCommits,
    OperationControlRequest,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.certification.corrective import RedCatalogDisposition
from agents_remember.models.closeout.source import CandidateAdmissionFacts, SchedulingGradeInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import IntegrateStrategy
from agents_remember.models.lifecycles.operation_kinds import (
    LifecycleControlAction,
    LifecycleOperationKind,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

from .lazy import tools


def register_closeout_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    """Register landing tools through cohesive bounded registration groups."""
    _register_direct_landing_tools(server, config)
    _register_closeout_command_tools(server, config)
    _register_integration_command_tools(server, config)
    _register_reclamation_command_tools(server, config)


def _register_direct_landing_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def direct_landing(
        contract_path: str,
        leaf_task_document_ref: TaskDocumentRef,
        *,
        code_commit: str | None = None,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
        intent_note: str = "",
        candidate_tree: str | None = None,
        dry_run: bool = False,
        leaf_id: str | None = None,
    ) -> dict[str, Any]:
        """Land a grant-gated leaf's verified series code commit and memory via a durable generation (recover via worktree_operation_control). States: landed, would-land, refused. Docs: docs/reference/mcp-tools.md."""
        return tools.direct_landing_payload(
            config,
            import_module(
                "agents_remember.application.lifecycle.direct_landing"
            ).DirectLandingRequest(
                contract_path=contract_path,
                code_commit=code_commit,
                leaf_task_document_ref=leaf_task_document_ref,
                code_commit_message=code_commit_message,
                memory_commit_message=memory_commit_message,
                intent_note=intent_note,
                candidate_tree=candidate_tree,
                dry_run=dry_run,
                leaf_id=leaf_id,
            ),
        )

    @server.tool()
    def direct_execution_grant(
        task_document_ref: TaskDocumentRef,
        action: str,
    ) -> dict[str, Any]:
        """Architect-only: give, revoke, or reclaim direct-execution grants so a leaf can run on its master's branch. give/revoke are idempotent; reclaim clears unneeded grants. MUTATING (grant record only). Docs: docs/reference/mcp-tools.md."""
        return tools.direct_execution_grant_payload(config, task_document_ref, action)


def _register_closeout_command_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_closeout_preview(
        *,
        contract_path: str,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Read-only preview of worktree closeout: changed path count, proposed commits, blockers, and invariant gate check (knowledge-gate-refused carries findings). States: would-closeout, blocked, closeout-gate-blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_closeout_preview_payload(
            config,
            contract_path,
            CloseoutCommitMessages(
                code=code_commit_message,
                memory=memory_commit_message,
            ),
        )

    @server.tool()
    def worktree_closeout_apply(
        *,
        contract_path: str,
        intent_note: str,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
        dry_run: bool = False,
        corrective_dispositions: list[RedCatalogDisposition] | None = None,
    ) -> dict[str, Any]:
        """Apply an approved worktree closeout in one call; judges candidate trees via the invariant gate (knowledge-gate-refused carries findings) and reports delivered-vs-reviewed status. States: closed, blocked, closeout-gate-blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_closeout_apply_payload(
            config,
            contract_path,
            CloseoutCommitMessages(
                code=code_commit_message,
                memory=memory_commit_message,
            ),
            CloseoutApproval(intent_note=intent_note, dry_run=dry_run),
            corrective_dispositions=tuple(corrective_dispositions or ()),
        )


def _register_integration_command_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_integrate(
        *,
        contract_path: str,
        strategy: IntegrateStrategy = "ff-only",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Land a closed-out task onto its target branch (ff-only or replay) and report delivered-vs-reviewed status. States: integrated, would-integrate, blocked, handover-gate-blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_integrate_payload(
            config,
            contract_path,
            strategy=strategy,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_checkpoint_landing(
        *,
        contract_path: str,
        strategy: IntegrateStrategy = "ff-only",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """PUBLISH finished leaves of an unfinished series master; not a pause (pausing is a separate matter and is NOT this call: use worktree_pause). States: checkpointed, would-checkpoint, blocked, handover-gate-blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_checkpoint_landing_payload(
            config,
            contract_path,
            strategy=strategy,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_record_landing(
        *,
        contract_path: str,
        landed_code_commit: str,
        landed_memory_content_commit: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Record a commit already on the landing target. States: recorded, already-recorded, would-record, blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_record_landing_payload(
            config,
            contract_path,
            landed=LandedCommits(
                code=landed_code_commit,
                memory_content=landed_memory_content_commit,
            ),
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_operation_control(
        *,
        contract_path: str,
        operation_kind: LifecycleOperationKind,
        action: LifecycleControlAction,
        expected_generation: int,
        intent_note: str,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
        grade: SchedulingGradeInput | None = None,
        admission: CandidateAdmissionFacts | None = None,
        corrective_dispositions: list[RedCatalogDisposition] | None = None,
        dry_run: bool = False,
        caller: DeclaredCaller | None = None,
    ) -> dict[str, Any]:
        """Control one direct-landing journal generation (retry, recover, resume, cancel, retire, supersede); closeout/integrate return their replacement tool or generation mismatch. States: refused or generation status. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_operation_control_payload(
            config,
            OperationControlRequest(
                contract_path=contract_path,
                operation_kind=operation_kind,
                action=action,
                expected_generation=expected_generation,
                intent_note=intent_note,
                code_commit_message=code_commit_message,
                memory_commit_message=memory_commit_message,
                grade=grade,
                admission=admission,
                corrective_dispositions=tuple(corrective_dispositions or ()),
                dry_run=dry_run,
                caller=caller,
            ),
        )


def _register_reclamation_command_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_cleanup(*, contract_path: str, dry_run: bool = False) -> dict[str, Any]:
        """After integration, archive lifecycle evidence and remove worktrees, merged branches, reports, and enclosure. States: cleanup-completed, already-clean, would-cleanup, blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_cleanup_payload(config, contract_path, dry_run=dry_run)

    @server.tool()
    def worktree_abandon(
        *, contract_path: str, dry_run: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Abandon an unintegrated worktree task while preserving terminal archive proof. States: abandoned, would-abandon, abandon-blocked, blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.worktree_abandon_payload(config, contract_path, dry_run=dry_run, force=force)
