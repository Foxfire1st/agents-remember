"""Worktree lifecycle payload builders."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    DEFAULT_START_EXECUTION,
    DEFAULT_TASK_BASES,
    CloseoutApproval,
    CloseoutCommitMessages,
    LandedCommits,
    OperationControlRequest,
    StartExecution,
    TaskBases,
    TaskIdentity,
    summarized_worktree_start_tool,
    worktree_abandon_tool,
    worktree_attach_tool,
    worktree_checkpoint_landing_tool,
    worktree_cleanup_tool,
    worktree_closeout_apply_tool,
    worktree_closeout_preview_tool,
    worktree_integrate_tool,
    worktree_operation_control_tool,
    worktree_pause_tool,
    worktree_record_landing_tool,
    worktree_status_tool,
    worktree_sync_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.certification.corrective import RedCatalogDisposition
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import (
    IntegrateStrategy,
)
from agents_remember.models.tools.tool_answer import is_valid_tool_code
from agents_remember.models.worktree import (
    MemorySyncChoice,
    SyncResolutionInput,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    IntegrationBranchAuthorityError,
)
from agents_remember.worktrees.modules.closeout_lineage import SourceLineageRefusal
from agents_remember.worktrees.modules.landing_commit_check import LandingCommitCheckRefusal
from agents_remember.worktrees.modules.models import CommitPublicationRefusal
from agents_remember.worktrees.modules.start import WorktreeTargetResolutionError
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError

from .base import _tool_payload


def _valid_code(candidate: str | None, fallback: str) -> str:
    if candidate is not None and is_valid_tool_code(candidate):
        return candidate
    return fallback


def _source_lineage_refusal_payload(
    operation: str,
    contract_path: str,
    error: SourceLineageRefusal,
) -> dict[str, Any]:
    code = _valid_code(error.status, "source-lineage-refused")
    payload: dict[str, Any] = dict(error.payload)
    payload["ok"] = False
    payload["operation"] = operation
    payload["code"] = code
    payload.setdefault("state", "blocked")
    payload.setdefault("status", code)
    payload.setdefault("summary", str(error))
    payload.setdefault("detail", str(error))
    payload.setdefault("contract_path", contract_path)
    if "nextTool" not in payload:
        payload["nextTool"] = "worktree_sync"
        payload["nextArgs"] = {"contract_path": contract_path, "dry_run": False}
    return payload


def _commit_publication_refusal_payload(
    operation: str,
    contract_path: str,
    error: CommitPublicationRefusal,
) -> dict[str, Any]:
    if isinstance(error, LandingCommitCheckRefusal):
        code = _valid_code(f"commit-check-{error.reason}", "commit-check-refused")
        extra: dict[str, Any] = {}
        if error.step is not None:
            extra["step"] = error.step
        if error.log is not None:
            extra["logPath"] = error.log.as_posix()
        if error.changed_paths:
            extra["changedPaths"] = list(error.changed_paths)
    else:
        code = "commit-publication-refused"
        extra = {}
    return {
        "ok": False,
        "operation": operation,
        "state": "blocked",
        "code": code,
        "status": code,
        "summary": str(error),
        "detail": str(error),
        "contract_path": contract_path,
        **extra,
    }


def worktree_start_payload(
    config: McpRuntimeConfig,
    identity: TaskIdentity,
    *,
    bases: TaskBases = DEFAULT_TASK_BASES,
    execution: StartExecution = DEFAULT_START_EXECUTION,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_start",
        summarized_worktree_start_tool(config, identity, bases=bases, execution=execution),
    )


def worktree_sync_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    memory_sync_choice: MemorySyncChoice | None = None,
    resolution: SyncResolutionInput | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_sync",
        worktree_sync_tool(
            config,
            contract_path=contract_path,
            memory_sync_choice=memory_sync_choice,
            resolution=resolution,
            dry_run=dry_run,
        ),
    )


def worktree_attach_payload(config: McpRuntimeConfig, task: TaskRef) -> dict[str, Any]:
    return _tool_payload("worktree_attach", worktree_attach_tool(config, task))


def worktree_pause_payload(
    config: McpRuntimeConfig,
    contract_path: str,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_pause",
        worktree_pause_tool(config, contract_path=contract_path),
    )


def worktree_status_payload(
    config: McpRuntimeConfig,
    task: TaskRef,
    *,
    caller: DeclaredCaller | None = None,
) -> dict[str, Any]:
    try:
        payload = worktree_status_tool(config, task, caller=caller)
    except WorktreeTargetResolutionError as error:
        code = _valid_code(error.code, "worktree-target-resolution-failed")
        payload = {
            "ok": False,
            "operation": "worktree_status",
            "state": "refused",
            "code": code,
            "status": code,
            "summary": str(error),
            "detail": str(error),
        }
    except ValidationError as error:
        code = "worktree-status-validation-failed"
        payload = {
            "ok": False,
            "operation": "worktree_status",
            "state": "refused",
            "code": code,
            "status": code,
            "summary": str(error),
            "detail": str(error),
        }
    return _tool_payload("worktree_status", payload)


def worktree_closeout_preview_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    messages: CloseoutCommitMessages,
) -> dict[str, Any]:
    try:
        payload = worktree_closeout_preview_tool(config, contract_path, messages)
    except SourceLineageRefusal as error:
        payload = _source_lineage_refusal_payload("worktree_closeout_preview", contract_path, error)
    except CommitPublicationRefusal as error:
        payload = _commit_publication_refusal_payload(
            "worktree_closeout_preview", contract_path, error
        )
    return _tool_payload("worktree_closeout_preview", payload)


def worktree_closeout_apply_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    messages: CloseoutCommitMessages,
    approval: CloseoutApproval,
    *,
    corrective_dispositions: tuple[RedCatalogDisposition, ...] = (),
) -> dict[str, Any]:
    try:
        payload = worktree_closeout_apply_tool(
            config,
            contract_path,
            messages,
            approval,
            corrective_dispositions=corrective_dispositions,
        )
    except SourceLineageRefusal as error:
        payload = _source_lineage_refusal_payload("worktree_closeout_apply", contract_path, error)
    except CommitPublicationRefusal as error:
        payload = _commit_publication_refusal_payload(
            "worktree_closeout_apply", contract_path, error
        )
    return _tool_payload("worktree_closeout_apply", payload)


def worktree_integrate_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    strategy: IntegrateStrategy = "ff-only",
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_integrate",
        worktree_integrate_tool(
            config,
            contract_path=contract_path,
            strategy=strategy,
            dry_run=dry_run,
        ),
    )


def worktree_checkpoint_landing_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    strategy: IntegrateStrategy = "ff-only",
    dry_run: bool = False,
) -> dict[str, Any]:
    try:
        payload = worktree_checkpoint_landing_tool(
            config,
            contract_path=contract_path,
            strategy=strategy,
            dry_run=dry_run,
        )
    except CloseoutQueueError as error:
        code = _valid_code(error.status, "closeout-queue-refused")
        payload = {
            "ok": False,
            "operation": "worktree_checkpoint_landing",
            "state": "blocked",
            "code": code,
            "status": code,
            "summary": error.detail or str(error),
            "detail": str(error),
            "contract_path": contract_path,
        }
    except IntegrationBranchAuthorityError as error:
        code = _valid_code(error.code, "integration-branch-authority-refused")
        payload = {
            "ok": False,
            "operation": "worktree_checkpoint_landing",
            "state": "blocked",
            "code": code,
            "status": code,
            "summary": str(error),
            "detail": str(error),
            "contract_path": contract_path,
        }
    return _tool_payload("worktree_checkpoint_landing", payload)


def worktree_record_landing_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    landed: LandedCommits,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_record_landing",
        worktree_record_landing_tool(
            config,
            contract_path=contract_path,
            landed=landed,
            dry_run=dry_run,
        ),
    )


def worktree_operation_control_payload(
    config: McpRuntimeConfig,
    request: OperationControlRequest,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_operation_control",
        worktree_operation_control_tool(config, request),
    )


def worktree_cleanup_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_cleanup",
        worktree_cleanup_tool(
            config,
            contract_path=contract_path,
            dry_run=dry_run,
        ),
    )


def worktree_abandon_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_abandon",
        worktree_abandon_tool(
            config,
            contract_path=contract_path,
            dry_run=dry_run,
            force=force,
        ),
    )
