"""Worktree tools for the landing half of a task: close out, integrate, reclaim."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.lifecycle.direct_landing import DirectLandingRequest
from agents_remember.application.worktree_tools import (
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

from ..tools import (
    direct_execution_grant_payload,
    direct_landing_payload,
    worktree_abandon_payload,
    worktree_checkpoint_landing_payload,
    worktree_cleanup_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
    worktree_operation_control_payload,
    worktree_record_landing_payload,
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
        """Land one verified series code commit with its memory content through a durable direct-
        landing generation, recovering through worktree_operation_control. States: landed,
        would-land, refused. Answers the state, what blocks and the next call; the whole result
        is in the log file. Reference: docs/reference/mcp-tools.md."""
        return direct_landing_payload(
            config,
            DirectLandingRequest(
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
        """Give, take back, or reclaim direct-execution grants, as an architect.

        A leaf runs directly on its master's branch only while a grant for
        it exists, and only a caller bound to the architect role may call
        this. ``action`` is ``give``, ``revoke`` or ``reclaim``; giving a
        grant that exists, or taking back one that does not, changes nothing
        and says so. Reclaim takes back one task's grants that no leaf still
        requires. The grant is read at the moment of every call.
        MUTATING (grant record only).
        Reference: docs/reference/mcp-tools.md.
        """
        return direct_execution_grant_payload(config, task_document_ref, action)


def _register_closeout_command_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_closeout_preview(
        *,
        contract_path: str,
        code_commit_message: str | None = None,
        memory_commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Preview a worktree closeout without mutating: the count of changed code paths and
        proposed commits with subjects up to the list bound, and what blocks (states:
        would-closeout, blocked, closeout-gate-blocked). A preview is a read; the whole result
        is in the log file.
        Reference: docs/reference/mcp-tools.md."""
        return worktree_closeout_preview_payload(
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
        """Apply an approved worktree closeout and return promptly. States: closed, blocked,
        closeout-gate-blocked. Answers the state reached and the next call; the whole result
        is in the log file. Delivered-against-reviewed names
        same, different, or no review recorded. Poll worktree_status for progress.
        Reference: docs/reference/mcp-tools.md."""
        return worktree_closeout_apply_payload(
            config,
            contract_path,
            CloseoutCommitMessages(
                code=code_commit_message,
                memory=memory_commit_message,
            ),
            CloseoutApproval(intent_note=intent_note, dry_run=dry_run),
            corrective_dispositions=tuple(corrective_dispositions or ()),
        )


def _register_integration_command_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_integrate(
        *,
        contract_path: str,
        strategy: IntegrateStrategy = "ff-only",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Land a task onto its target branch, ff-only or replay. Answers the state (integrated,
        would-integrate, blocked, handover-gate-blocked), the commit, and cleanup with its
        arguments as the next call.
        A refusal names the unfinished step and the call that finishes it. Delivered-against-
        reviewed names same, different, or no review recorded. Reference:
        docs/reference/mcp-tools.md."""
        return worktree_integrate_payload(
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
        """PUBLISH the finished leaves of an unfinished series master; this call is not a pause. Pausing is a separate matter and is NOT this call: use worktree_pause to stop a master. States: checkpointed, would-checkpoint, blocked, handover-gate-blocked. Answers the state reached and the next call. Reference: docs/reference/mcp-tools.md."""
        return worktree_checkpoint_landing_payload(
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
        """Record a commit the landing target already holds. States: recorded, already-recorded, would-record, blocked. Answers the state reached; a refusal
        names the commit that is missing or elsewhere and what to do. Reference:
        docs/reference/mcp-tools.md."""
        return worktree_record_landing_payload(
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
        """Retry, recover, resume, cancel, retire or supersede one closeout, integrate or direct-
        direct-landing generation. States: refused, or the controlled generation's status. Answers the state reached and the next call;
        the log file. Reference: docs/reference/mcp-tools.md."""
        return worktree_operation_control_payload(
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


def _register_reclamation_command_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def worktree_cleanup(
        *, contract_path: str, dry_run: bool = False, teardown_providers: bool = True
    ) -> dict[str, Any]:
        """Archive lifecycle evidence, then remove worktrees, merged branches, reports, logs and
        the enclosure root once integrated. States: cleanup-completed, already-clean, would-cleanup,
        blocked, provider-setup-running. Answers the state reached; a dry run with
        blockers answers a blocked state. Reference: docs/reference/mcp-tools.md."""
        return worktree_cleanup_payload(
            config, contract_path, dry_run=dry_run, teardown_providers=teardown_providers
        )

    @server.tool()
    def worktree_abandon(
        *, contract_path: str, dry_run: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Abandon a worktree task without integrating, preserving terminal archive proof. States: abandoned, would-abandon, abandon-blocked, blocked. Answers the state reached
        and the next call. Reference: docs/reference/mcp-tools.md."""
        return worktree_abandon_payload(config, contract_path, dry_run=dry_run, force=force)
