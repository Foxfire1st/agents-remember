"""Application census proving whether one planning leaf acquired execution authority."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.lifecycle.lifecycle_operation_location import (
    primary_operation_projection,
)
from agents_remember.application.memory_mode_refusal import memory_mode_refusal_payload
from agents_remember.errors import MemoryModeUnsupportedError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.tasks import (
    DiscardSourceProof,
    DiscardUnstartedProof,
    TaskDocSourceSnapshot,
    TaskDocument,
    TaskExecutionRegistration,
    capture_task_doc_source,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_location_cache import (
    resolve_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleLocatorObservation,
    LifecycleOperationLocationError,
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.lifecycle.observation.projection import (
    current_operation_projections,
    unreadable_contract_operation_projections,
)
from agents_remember.worktrees.task_leaf_binding import LeafTaskBinding
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

RecoveryRoute = tuple[str, str | None, dict[str, object] | None]


@dataclass(frozen=True)
class TaskUnstartedEvidence:
    state: Literal["unstarted", "started", "ambiguous"]
    fingerprint: str
    facts: tuple[dict[str, Any], ...]
    binding: LeafTaskBinding
    child_source: TaskDocSourceSnapshot
    contract: WorktreeContract | None = None
    locator: LifecycleLocatorObservation | None = None
    next_action: str | None = None
    next_tool: str | None = None
    next_args: dict[str, object] | None = None

    @property
    def unstarted(self) -> bool:
        return self.state == "unstarted"

    def public_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "state": self.state,
            "fingerprint": self.fingerprint,
            "taskDocumentRef": self.binding.task_ref.model_dump(mode="json"),
            "facts": list(self.facts),
        }
        if self.next_action is not None:
            payload["nextAction"] = self.next_action
        if self.next_tool is not None:
            payload["nextTool"] = self.next_tool
        if self.next_args is not None:
            payload["nextArgs"] = self.next_args
        return payload

    def persisted_proof(self) -> DiscardUnstartedProof:
        if not self.unstarted:
            raise ValueError("only an unstarted evidence result can become a discard audit")
        return DiscardUnstartedProof(
            taskDocumentRef=self.binding.task_ref,
            childJson=_discard_source_proof(self.child_source.json_bytes),
            childMarkdown=_discard_source_proof(self.child_source.markdown_bytes),
            fingerprint=self.fingerprint,
        )


@dataclass
class _EvidenceCensus:
    binding: LeafTaskBinding
    facts: list[dict[str, Any]]
    severity: list[Literal["started", "ambiguous"]]


@dataclass(frozen=True)
class _StartedRouteEvidence:
    binding: LeafTaskBinding
    contract: WorktreeContract | None
    locator: LifecycleLocatorObservation
    projections: tuple[LifecycleOperationProjection, ...]
    task_registrations: tuple[TaskExecutionRegistration, ...]


def prove_task_unstarted(
    _config: McpRuntimeConfig,
    binding: LeafTaskBinding,
) -> TaskUnstartedEvidence:
    """Prove absence from canonical task, locator/contract and journal sources.

    Every source is addressed through the binding. The configuration that the callers of
    the landing chain pass is not read.
    """

    facts: list[dict[str, Any]] = []
    severity: list[Literal["started", "ambiguous"]] = []
    census = _EvidenceCensus(binding, facts, severity)
    child_source = capture_task_doc_source(binding.leaf_json_path)
    _task_facts(census)
    locator = inspect_lifecycle_operation_locator(
        binding.coordination_root,
        binding.contract_path,
    )
    if locator.state == "missing":
        facts.append(_fact("locator", "absent", locator.path))
    elif locator.state == "unreadable":
        severity.append("ambiguous")
        facts.append(
            _fact(
                "locator",
                "unreadable",
                locator.path,
                detail=locator.detail or "canonical locator is present but unreadable",
            )
        )
    else:
        severity.append("started")
        facts.append(_fact("locator", locator.state, locator.path))

    contract, contract_failure = _contract_fact(binding, facts, severity)
    projections = _operation_facts(
        census,
        locator,
        contract,
        contract_failure,
    )
    state: Literal["unstarted", "started", "ambiguous"] = (
        "ambiguous" if "ambiguous" in severity else "started" if severity else "unstarted"
    )
    fingerprint = _evidence_fingerprint(binding, child_source, facts)
    next_action = next_tool = None
    next_args: dict[str, object] | None = None
    if state != "unstarted":
        next_action, next_tool, next_args = _started_route(
            _StartedRouteEvidence(
                binding=binding,
                contract=contract,
                locator=locator,
                projections=projections,
                task_registrations=(
                    tuple(binding.leaf.executionRegistrations) if binding.leaf is not None else ()
                ),
            )
        )
    return TaskUnstartedEvidence(
        state,
        fingerprint,
        tuple(facts),
        binding,
        child_source,
        contract=contract,
        locator=locator,
        next_action=next_action,
        next_tool=next_tool,
        next_args=next_args,
    )


def _task_facts(census: _EvidenceCensus) -> None:
    binding = census.binding
    row = binding.row
    if row.status != "planning":
        census.severity.append("started")
        census.facts.append(
            _fact("task", "execution-progress", binding.parent_path, detail=f"row={row.status}")
        )
    leaf = binding.leaf
    if leaf is None:
        census.facts.append(_fact("task", "planning-unstarted", binding.leaf_json_path))
        return
    blockers = _leaf_execution_blockers(leaf)
    if blockers:
        census.severity.append("started")
        census.facts.append(
            _fact(
                "task",
                "execution-progress",
                binding.leaf_json_path,
                detail=", ".join(blockers),
            )
        )
        return
    census.facts.append(_fact("task", "planning-unstarted", binding.leaf_json_path))


def _leaf_execution_blockers(leaf: TaskDocument) -> list[str]:
    return [
        *_leaf_authority_blockers(leaf),
        *_leaf_step_blockers(leaf),
        *_leaf_substep_blockers(leaf),
    ]


def _leaf_authority_blockers(leaf: TaskDocument) -> list[str]:
    candidates = (
        (leaf.status != "planning", f"status={leaf.status}"),
        (leaf.lifecycleId is not None, "lifecycleId"),
        (bool(leaf.enclosures), "enclosures"),
        (bool(leaf.executionRegistrations), "executionRegistrations"),
        (leaf.routeReview is not None, "routeReview"),
    )
    return [label for present, label in candidates if present]


def _leaf_step_blockers(leaf: TaskDocument) -> list[str]:
    return [
        f"step:{step.id}:{step.status}"
        for step in leaf.steps
        if (step.status, step.disposition) != ("pending", None)
    ]


def _leaf_substep_blockers(leaf: TaskDocument) -> list[str]:
    return [
        f"substep:{step.id}/{substep.id}:{substep.status}"
        for step in leaf.steps
        for substep in step.substeps
        if (substep.status, substep.disposition) != ("pending", None)
    ]


def _contract_fact(
    binding: LeafTaskBinding,
    facts: list[dict[str, Any]],
    severity: list[Literal["started", "ambiguous"]],
) -> tuple[WorktreeContract | None, dict[str, object] | None]:
    path = binding.contract_path
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        facts.extend(
            (
                _fact("enclosure", "absent", path),
                _fact("operation", "absent", path),
                _fact("commit", "absent", path),
            )
        )
        return None, {"state": "missing", "errorType": "FileNotFoundError"}
    except OSError as exc:
        severity.append("ambiguous")
        facts.append(_fact("enclosure", "unreadable", path, detail=type(exc).__name__))
        return None, {"state": "unreadable", "errorType": type(exc).__name__}
    if not stat.S_ISREG(mode):
        severity.append("ambiguous")
        facts.append(_fact("enclosure", "unreadable", path, detail=f"mode={mode}"))
        return None, {"state": "unreadable", "errorType": "NonRegularFile"}
    try:
        contract = load_contract(path)
    except MemoryModeUnsupportedError as exc:
        # The unstarted census must report the removed mode, not a generic unreadable enclosure:
        # the operator's next action depends on which of the two it is.
        severity.append("ambiguous")
        facts.append(_fact("enclosure", "unreadable", path, detail=exc.requested))
        facts.append(
            {
                "kind": "memoryMode",
                "state": "removed",
                "address": path.as_posix(),
                **memory_mode_refusal_payload(exc),
            }
        )
        return None, {
            "state": "removed-mode",
            "errorType": type(exc).__name__,
            **memory_mode_refusal_payload(exc),
        }
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        severity.append("ambiguous")
        facts.append(_fact("enclosure", "unreadable", path, detail=type(exc).__name__))
        return None, {"state": "unreadable", "errorType": type(exc).__name__}
    severity.append("started")
    facts.append(
        _fact(
            "enclosure",
            "present",
            path,
            detail=f"cleanup={contract.cleanup}",
        )
    )
    commit_present = any(
        (
            contract.code_commit,
            contract.memory_content_commit,
            contract.integrated_code_commit,
            contract.integrated_memory_content_commit,
        )
    )
    facts.append(_fact("commit", "present" if commit_present else "absent", path))
    facts.append(_fact("operation", "addressable-through-enclosure", path))
    return contract, None


def _operation_facts(
    census: _EvidenceCensus,
    locator: LifecycleLocatorObservation,
    contract: WorktreeContract | None,
    contract_failure: dict[str, object] | None,
) -> tuple[LifecycleOperationProjection, ...]:
    """Read exact locator-addressed manifest/journals even when contract bytes are lost."""

    binding = census.binding
    facts = census.facts
    severity = census.severity
    if locator.state == "terminal-archived" and locator.locator is not None:
        terminal = locator.locator
        facts.append(
            _fact(
                "terminal-archive",
                "present",
                Path(terminal.terminalArchivePath or ""),
                detail=terminal.terminalArchiveSha256,
            )
        )
        facts.append(
            _fact(
                "terminal-receipt",
                "present",
                Path(terminal.terminalReceiptPath or ""),
            )
        )
        return ()
    if locator.state != "addressable":
        return ()
    try:
        location = resolve_lifecycle_operation_location(
            binding.coordination_root,
            binding.contract_path,
        )
    except LifecycleOperationLocationError as exc:
        severity.append("ambiguous")
        facts.append(
            _fact(
                "manifest",
                "unreadable",
                Path(locator.locator.manifestPath) if locator.locator is not None else locator.path,
                detail=f"{exc.status}: {exc.detail}",
            )
        )
        return ()
    facts.append(_fact("manifest", "present", location.manifest_path))
    if contract is not None:
        projections = current_operation_projections(
            binding.contract_path,
            contract=contract,
            location=location,
        )
    else:
        failure = contract_failure or {"state": "unreadable", "errorType": "ContractError"}
        error_type = failure["errorType"]
        projections = unreadable_contract_operation_projections(
            location,
            error_type=error_type if isinstance(error_type, str) else "ContractError",
            name=binding.contract_path.name,
        )
    if not projections:
        facts.append(_fact("operation", "absent", location.lifecycle_directory))
        return ()
    for projection in projections:
        severity.append("ambiguous" if projection.status == "unreadable" else "started")
        facts.append(
            _fact(
                "operation",
                projection.status,
                location.journal_path(projection.kind),
                detail=(
                    f"kind={projection.kind}, generation={projection.generation or 0}, "
                    f"phase={projection.phase}"
                ),
            )
        )
    return tuple(projections)


def _started_route(evidence: _StartedRouteEvidence) -> RecoveryRoute:
    terminal_route = _terminal_archive_route(evidence)
    if terminal_route is not None:
        return terminal_route
    operation_route = _projection_recovery_route(evidence)
    if operation_route is not None:
        return operation_route
    contract_route = _contract_recovery_route(evidence)
    if contract_route is not None:
        return contract_route
    locator_route = _locator_recovery_route(evidence)
    if locator_route is not None:
        return locator_route
    return _record_recovery_route(evidence)


def _terminal_archive_route(evidence: _StartedRouteEvidence) -> RecoveryRoute | None:
    binding = evidence.binding
    locator = evidence.locator
    if locator.state == "terminal-archived" and locator.locator is not None:
        return (
            "restart-terminal-enclosure",
            "worktree_start",
            {
                "repo_id": binding.repo_id,
                "task_name": binding.task_name,
                "worktree_name": Path(locator.locator.worktreeGroup).name,
                "leaf_id": binding.row.number,
            },
        )
    return None


def _projection_recovery_route(evidence: _StartedRouteEvidence) -> RecoveryRoute | None:
    projection = primary_operation_projection(list(evidence.projections))
    if projection is None:
        return None
    control_route = _projection_control_route(projection)
    if control_route is not None:
        return control_route
    return _projection_without_control(evidence, projection)


def _projection_control_route(projection: LifecycleOperationProjection) -> RecoveryRoute | None:
    controls = [control for control in projection.legalControls if isinstance(control, dict)]
    if not controls:
        return None
    control = controls[0]
    return (
        _control_route_value(control, "action", f"recover-{projection.kind}"),
        _control_route_value(control, "tool", "worktree_operation_control"),
        dict(control.get("arguments") or {}),
    )


def _control_route_value(control: dict[str, object], key: str, default: str) -> str:
    value = control.get(key)
    return str(value) if value else default


def _projection_without_control(
    evidence: _StartedRouteEvidence,
    projection: LifecycleOperationProjection,
) -> RecoveryRoute:
    if projection.status == "completed" and evidence.contract is not None:
        return _completed_projection_route(evidence, projection)
    return (
        f"recover-{projection.kind}-authority",
        "worktree_status",
        {"contract_path": evidence.binding.contract_path.as_posix()},
    )


def _completed_projection_route(
    evidence: _StartedRouteEvidence,
    projection: LifecycleOperationProjection,
) -> RecoveryRoute:
    assert evidence.contract is not None
    contract_args: dict[str, object] = {"contract_path": evidence.contract.contract_path.as_posix()}
    if projection.kind == "closeout":
        return "complete-integration", "worktree_integrate", contract_args
    return "complete-started-task", "task_doc", _task_route_args(evidence.binding)


def _contract_recovery_route(evidence: _StartedRouteEvidence) -> RecoveryRoute | None:
    binding = evidence.binding
    contract = evidence.contract
    if contract is not None:
        contract_args: dict[str, object] = {"contract_path": contract.contract_path.as_posix()}
        if contract.integration_status == "completed" or contract.cleanup == "completed":
            return "complete-started-task", "task_doc", _task_route_args(binding)
        if contract.cleanup == "abandoned":
            return "complete-abandoned-task", "task_doc", _task_route_args(binding)
        if contract.closeout_status == "completed":
            return "complete-integration", "worktree_integrate", contract_args
        return "abandon-started-work", "worktree_abandon", contract_args
    return None


def _locator_recovery_route(evidence: _StartedRouteEvidence) -> RecoveryRoute | None:
    binding = evidence.binding
    locator = evidence.locator
    if locator.state in {"reserved", "manifest-proven"}:
        assert locator.locator is not None
        return (
            "recover-start-publication",
            "worktree_start",
            {
                "repo_id": binding.repo_id,
                "task_name": binding.task_name,
                "worktree_name": Path(locator.locator.worktreeGroup).name,
                "leaf_id": binding.row.number,
            },
        )
    return None


def _record_recovery_route(evidence: _StartedRouteEvidence) -> RecoveryRoute:
    if evidence.task_registrations:
        return "complete-started-task", "task_doc", _task_route_args(evidence.binding)
    return "developer-decision", None, None


def _task_route_args(binding: LeafTaskBinding) -> dict[str, object]:
    return {
        "repo_id": binding.repo_id,
        "task_name": binding.task_name,
        "task_document_ref": binding.task_ref.model_dump(mode="json"),
    }


def _fact(
    kind: str,
    state: str,
    address: Path,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "kind": kind,
        "state": state,
        "address": address.as_posix(),
    }
    if detail:
        fact["detail"] = detail
    return fact


def _evidence_fingerprint(
    binding: LeafTaskBinding,
    child_source: TaskDocSourceSnapshot,
    facts: list[dict[str, Any]],
) -> str:
    payload = {
        "version": "task-unstarted-evidence/v1",
        "taskDocumentRef": binding.task_ref.model_dump(mode="json"),
        "row": binding.row.model_dump(mode="json"),
        "leaf": binding.leaf.model_dump(mode="json") if binding.leaf is not None else None,
        "childSource": child_source.evidence(),
        "facts": facts,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _discard_source_proof(payload: bytes | None) -> DiscardSourceProof:
    if payload is None:
        return DiscardSourceProof(state="missing")
    return DiscardSourceProof(
        state="present",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )
