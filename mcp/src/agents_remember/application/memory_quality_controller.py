"""Typed sync/start/poll controller for repository-scoped memory quality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.application.memory_quality_runs import (
    QualityRunIdentity,
    poll_quality_run,
    start_quality_run,
)
from agents_remember.application.memory_scope import MemoryScope, resolve_memory_scope
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory_quality.check import (
    DRIFT_CHECK_NAME,
    DriftCheckContext,
    normalize_checks,
    run_memory_quality_check,
)
from agents_remember.memory_quality.curator_checklist import (
    CuratorChecklist,
    split_commit_owned_findings,
    write_curator_checklist,
)
from agents_remember.memory_quality.integrity.check_missing_onboarding import (
    check_missing_onboarding,
)
from agents_remember.models.memory import (
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
    MemoryQualitySyncRequest,
)

_CAPACITY_GUIDANCE = (
    "Poll an existing run or wait for active memory-quality work to finish, then submit "
    "this start request again."
)
_RUN_NOT_FOUND_GUIDANCE = (
    "The run was evicted, belongs to another repository, or the server restarted; "
    "submit a new start request."
)


@dataclass(frozen=True)
class MemoryQualityExecution:
    """One canonical execution and the result-affecting publication decision."""

    scope: MemoryScope
    checks: tuple[str, ...]
    detail_limit: int
    publish_curator_report: bool

    @property
    def identity(self) -> QualityRunIdentity:
        return QualityRunIdentity(
            repo_id=self.scope.repo_id,
            scope=self.scope.identity,
            checks=self.checks,
            detail_limit=self.detail_limit,
            publish_curator_report=self.publish_curator_report,
        )


def run_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest,
) -> dict[str, object]:
    """Resolve and synchronously execute one explicit sync request."""

    return _execute_memory_quality(_resolve_execution(config, request))


def start_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualityStartRequest,
) -> dict[str, object]:
    """Resolve and admit one explicit async-start request."""

    execution = _resolve_execution(config, request)
    admission = start_quality_run(
        execution.identity,
        lambda: _execute_memory_quality(execution),
    )
    if admission.state == "capacity-reached":
        return {
            "ok": False,
            "operation": "memory_quality_check",
            "repoId": execution.scope.repo_id,
            "status": "capacity-reached",
            "guidance": _CAPACITY_GUIDANCE,
        }
    if admission.run_id is None:
        raise RuntimeError("memory-quality admission did not retain its run identity")
    return {
        "ok": True,
        "operation": "memory_quality_check",
        "repoId": execution.scope.repo_id,
        "status": admission.state,
        "runId": admission.run_id,
    }


def poll_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualityPollRequest,
) -> dict[str, object]:
    """Poll one run only through its configured canonical repository."""

    repo_id = require_repo(config, request.repo_id).repo_id
    snapshot = poll_quality_run(repo_id, request.run_id)
    if snapshot is None:
        return {
            "ok": False,
            "operation": "memory_quality_check",
            "repoId": repo_id,
            "status": "run-not-found",
            "runId": request.run_id,
            "guidance": _RUN_NOT_FOUND_GUIDANCE,
        }
    if snapshot.status == "running":
        return {
            "ok": True,
            "operation": "memory_quality_check",
            "repoId": repo_id,
            "status": "running",
            "runId": snapshot.run_id,
        }
    if snapshot.status == "failed":
        return {
            "ok": True,
            "operation": "memory_quality_check",
            "repoId": repo_id,
            "status": "failed",
            "runId": snapshot.run_id,
            "error": snapshot.error,
        }
    return {
        **dict(snapshot.result or {}),
        "status": "completed",
        "runId": snapshot.run_id,
    }


def _resolve_execution(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest | MemoryQualityStartRequest,
) -> MemoryQualityExecution:
    checks = tuple(sorted(set(normalize_checks(request.checks, include_integrity=True))))
    scope = resolve_memory_scope(
        config,
        repo_id=request.repo_id,
        contract_path=request.contract_path,
    )
    return MemoryQualityExecution(
        scope=scope,
        checks=checks,
        detail_limit=request.detail_limit,
        publish_curator_report=scope.curator_report_path is not None and not request.checks,
    )


def _execute_memory_quality(execution: MemoryQualityExecution) -> dict[str, object]:
    scope = execution.scope
    payload = run_memory_quality_check(
        scope.onboarding_root,
        checks=execution.checks,
        drift_context=DriftCheckContext(
            code_repository_root=scope.code_root,
            context=scope.context,
            detail_limit=execution.detail_limit,
            unstamped_code_commit=scope.unstamped_code_commit,
            report_path=(scope.curator_report_path if execution.publish_curator_report else None),
            include_rows=execution.publish_curator_report,
            write_report=not execution.publish_curator_report,
        ),
        include_report_only_findings=execution.publish_curator_report,
    )
    response: dict[str, object] = {
        "operation": "memory_quality_check",
        "repoId": scope.repo_id,
        "onboardingRoot": scope.onboarding_root.as_posix(),
        **payload,
    }
    if not execution.publish_curator_report:
        return response
    _attach_curator_checklist(scope, payload, response)
    return response


def _attach_curator_checklist(
    scope: MemoryScope,
    payload: dict[str, Any],
    response: dict[str, object],
) -> None:
    checks = payload.get("checks")
    drift_result = checks.get(DRIFT_CHECK_NAME, {}) if isinstance(checks, dict) else {}
    drift_rows = drift_result.pop("rows", []) if isinstance(drift_result, dict) else []
    report_only = payload.pop("reportOnlyFindings", [])
    findings = payload.get("findings")
    style_findings = (
        [
            finding
            for finding in findings
            if isinstance(finding, dict) and finding.get("check") != DRIFT_CHECK_NAME
        ]
        if isinstance(findings, list)
        else []
    )
    repair_findings, commit_owned_findings = split_commit_owned_findings(
        style_findings,
        scope.onboarding_root,
    )
    missing_onboarding = check_missing_onboarding(
        code_repository_root=scope.code_root,
        onboarding_root=scope.onboarding_root,
        settings=scope.context.storage,
        code_repository_name=scope.context.code_repository_name,
    )
    route_indexes = build_route_indexes(
        code_root=scope.code_root,
        onboarding_root=scope.onboarding_root,
        repository=scope.context.code_repository_name,
        storage=scope.context.storage,
        dry_run=True,
    )
    if scope.curator_report_path is None:
        raise RuntimeError("curator publication has no enclosure-local report path")
    checklist = write_curator_checklist(
        CuratorChecklist(
            report_path=scope.curator_report_path,
            repo_id=scope.repo_id,
            code_root=scope.code_root,
            onboarding_root=scope.onboarding_root,
            quality=payload,
            repair_findings=repair_findings,
            commit_owned_findings=commit_owned_findings,
            missing_onboarding=missing_onboarding,
            stale_route_indexes=route_indexes.stale_indexes,
            drift_rows=drift_rows,
            report_only_findings=report_only,
        )
    )
    response.pop("reportOnlyFindings", None)
    response.update(checklist)
