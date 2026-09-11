"""Canonical route-review scope for deferred atomic-leaf review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.errors import TaskIntentError
from agents_remember.models.lifecycles.evidence_dependencies import (
    EVIDENCE_DEPENDENCY_VALIDATOR,
    EvidenceDependencyError,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
    require_evidence_dependencies,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.tasks.document import (
    RouteReviewChildIntent,
    RouteReviewRecord,
    RouteReviewScope,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.tasks.task_intent import (
    task_intent_identity,
    task_intent_master_projection,
)
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.route_review import (
    RouteReviewError,
    _require_evidence_files,
    _stamp_evidence_digests,
    _stamped_evidence,
    code_candidate_tree,
)
from agents_remember.worktrees.route_review import (
    require_current_route_review as require_leaf_route_review,
)
from agents_remember.worktrees.scheduling_mode import effective_execution_nature
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


@dataclass(frozen=True)
class AtomicMasterReviewScope:
    """One exact canonical master and its ordered child intent population."""

    contract: WorktreeContract
    master: ResolvedTaskDocument
    children: tuple[ResolvedTaskDocument, ...]
    review_scope: RouteReviewScope
    aggregate_intent: TaskIntentIdentity

    @property
    def membership_digest(self) -> str:
        return canonical_sha256(
            {
                "masterRef": self.master.ref.model_dump(mode="json"),
                "children": [child.ref.model_dump(mode="json") for child in self.children],
            }
        )


def resolve_atomic_master_scope(
    contract: WorktreeContract,
    *,
    master_override: ResolvedTaskDocument | None = None,
) -> AtomicMasterReviewScope | None:
    """Resolve the actual atomic owner, returning no scope for non-atomic work.

    A leaf's own enclosure is not an ownership source. When present, its parent
    series contract supplies the canonical master root. A missing master document
    is an ownership error; it cannot grant atomic deferral or silently fall back
    to a leaf review.
    """

    owner_contract = _series_owner_contract(contract)
    if owner_contract is None:
        return None
    topology = TaskDocumentTopology(owner_contract.coordination_root)
    try:
        master_ref = _master_ref(owner_contract)
    except (ValueError, OSError) as exc:
        raise RouteReviewError(
            "route-review-atomic-owner-invalid",
            f"cannot derive the canonical atomic master reference: {exc}",
        ) from exc
    master_path = owner_contract.task_root / "task.json"
    if not master_path.is_file():
        raise RouteReviewError(
            "route-review-atomic-owner-missing",
            f"atomic integration has no canonical master task document: {master_path}",
        )
    try:
        master = master_override or topology.resolve(master_ref)
        if master.ref != master_ref or master.document.kind != "master":
            raise TaskDocumentRefError(
                "task-document-altitude-invalid",
                f"canonical series task root is not a master: {master_ref.key}",
            )
        sprint_ref = topology.parent(master_ref)
        sprint = topology.resolve(sprint_ref) if sprint_ref is not None else None
        nature = effective_execution_nature(
            master.document,
            sprint.document if sprint is not None else None,
        )
        if nature != "atomic":
            return None
        children = _resolve_children(topology, master)
        if contract.kind == "leaf":
            _require_leaf_member(contract, owner_contract, topology, children)
        master_intent = _master_intent_identity(owner_contract.task_root, master)
        child_intents = tuple(
            RouteReviewChildIntent(
                ref=child.ref,
                taskIntent=task_intent_identity(owner_contract.task_root, child),
            )
            for child in children
        )
        scope = RouteReviewScope(
            masterRef=master.ref,
            masterIntent=master_intent,
            childIntents=list(child_intents),
        )
        aggregate = _aggregate_intent(scope)
    except (TaskDocumentRefError, ContractError, OSError, TaskIntentError, ValidationError) as exc:
        raise RouteReviewError(
            "route-review-atomic-owner-invalid",
            f"cannot resolve canonical atomic master review scope: {exc}",
        ) from exc
    return AtomicMasterReviewScope(
        contract=owner_contract,
        master=master,
        children=children,
        review_scope=scope,
        aggregate_intent=aggregate,
    )


def require_current_route_review(contract: WorktreeContract) -> dict[str, object]:
    """Require the current review at its owning altitude.

    Atomic children return a typed deferred result. Atomic series contracts
    require the accumulated master review; all other contracts retain the
    existing leaf/master-altitude behavior.
    """

    scope = resolve_atomic_master_scope(contract)
    if contract.kind == "leaf":
        if scope is not None:
            return {
                "required": False,
                "status": "deferred-atomic-child",
                "masterRef": scope.master.ref.model_dump(mode="json"),
                "childCount": len(scope.children),
            }
        return require_leaf_route_review(contract)
    if scope is None:
        return require_leaf_route_review(contract)
    # The series closeout/door path records the accumulated candidate. The master
    # review is not required at this boundary: quality is checked focused within the
    # leaves and an adversarial review runs before integration, so integration
    # deliberately does not re-run full code/memory quality here.
    return {
        "required": False,
        "status": "deferred-atomic-master-until-integration",
        "masterRef": scope.master.ref.model_dump(mode="json"),
        "childCount": len(scope.children),
    }


def reject_atomic_child_route_review(contract: WorktreeContract) -> None:
    """Refuse publication of a per-leaf review when the canonical owner is atomic."""

    scope = resolve_atomic_master_scope(contract)
    if scope is not None:
        raise RouteReviewError(
            "route-review-atomic-child-deferred",
            "independent review is deferred for an atomic child; review the accumulated "
            f"master {scope.master.ref.key} at integration",
        )


def build_master_route_review(
    contract: WorktreeContract,
    candidate: ResolvedTaskDocument,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> RouteReviewRecord:
    """Stamp one master review from the canonical series branch and child set."""

    expected = {"verdict", "verdictRef", "routes"}
    unknown = set(payload) - expected
    if unknown:
        raise RouteReviewError(
            "route-review-invalid",
            "record_route_review accepts only verdict, verdictRef, and routes; "
            f"the plane owns candidateTree and reviewedAt (unknown: {sorted(unknown)})",
        )
    if contract.kind != "series":
        raise RouteReviewError(
            "route-review-invalid-altitude",
            "atomic-master route review requires the canonical series contract",
        )
    scope = resolve_atomic_master_scope(contract, master_override=candidate)
    if scope is None:
        raise RouteReviewError(
            "route-review-invalid-altitude",
            "route review publication is only deferred to an effective atomic master",
        )
    if candidate.path.resolve() != (contract.task_root / "task.json").resolve():
        raise RouteReviewError(
            "route-review-master-binding-invalid",
            "master route review must target the canonical task-root task.json",
        )
    try:
        candidate_tree = code_candidate_tree(contract)
        stamped = _stamp_evidence_digests(contract.task_root, payload)
        dependencies = _master_dependencies(scope, candidate_tree, stamped)
        record_payload = {
            **stamped,
            "candidateTree": candidate_tree,
            "reviewedAt": (now or datetime.now(UTC)).replace(microsecond=0).isoformat(),
            "taskIntent": scope.aggregate_intent.model_dump(mode="json", by_alias=True),
            "scope": scope.review_scope.model_dump(mode="json", by_alias=True),
            "dependencies": dependencies.model_dump(mode="json"),
        }
        record_payload["recordDigest"] = canonical_sha256(record_payload)
        record = RouteReviewRecord.model_validate(record_payload)
    except (EvidenceDependencyError, TaskIntentError, ValidationError) as exc:
        if isinstance(exc, (EvidenceDependencyError, TaskIntentError)):
            raise RouteReviewError(exc.status, exc.detail) from exc
        raise RouteReviewError("route-review-invalid", str(exc)) from exc
    _require_evidence_files(contract.task_root, record)
    return record


def _series_owner_contract(contract: WorktreeContract) -> WorktreeContract | None:
    if contract.kind == "series":
        return contract
    if contract.kind != "leaf":
        return None
    if contract.parent_contract_path is None:
        if contract.parent_task_name:
            raise RouteReviewError(
                "route-review-atomic-owner-invalid",
                "leaf declares a canonical parent but has no parent series contract path",
            )
        return None
    if not contract.parent_task_name:
        raise RouteReviewError(
            "route-review-atomic-owner-invalid",
            "leaf has a parent series contract path but no canonical parent task name",
        )
    try:
        owner = load_contract(contract.parent_contract_path)
    except (ContractError, OSError) as exc:
        raise RouteReviewError(
            "route-review-atomic-owner-invalid",
            f"cannot load the canonical parent series contract: {contract.parent_contract_path}",
        ) from exc
    if owner.kind != "series":
        raise RouteReviewError(
            "route-review-atomic-owner-invalid",
            f"leaf parent contract is not a series contract: {owner.contract_path}",
        )
    return owner


def _master_ref(contract: WorktreeContract) -> TaskDocumentRef:
    relative = (
        (contract.task_root / "task.json")
        .resolve()
        .relative_to((contract.coordination_root / "tasks" / contract.repo_name).resolve())
    )
    return TaskDocumentRef(repository=contract.repo_name, path=relative.as_posix())


def _resolve_children(
    topology: TaskDocumentTopology,
    master: ResolvedTaskDocument,
) -> tuple[ResolvedTaskDocument, ...]:
    children: list[ResolvedTaskDocument] = []
    seen: set[TaskDocumentRef] = set()
    for row in master.document.subTasks:
        if not row.file:
            raise TaskDocumentRefError(
                "route-review-master-membership-invalid",
                f"atomic master child {row.number!r} has no canonical file",
            )
        child_path = (master.path.parent / row.file).with_suffix(".json")
        child_ref = topology.canonical_ref(master.ref.repository, child_path)
        if child_ref in seen:
            raise TaskDocumentRefError(
                "route-review-master-membership-invalid",
                f"atomic master child membership repeats {child_ref.key}",
            )
        child = topology.resolve(child_ref)
        if child.document.kind == "master":
            raise TaskDocumentRefError(
                "route-review-master-membership-invalid",
                f"atomic master child must be a leaf document: {child_ref.key}",
            )
        seen.add(child_ref)
        children.append(child)
    if not children:
        raise TaskDocumentRefError(
            "route-review-master-membership-invalid",
            f"atomic master has no canonical child documents: {master.ref.key}",
        )
    return tuple(children)


def _require_leaf_member(
    contract: WorktreeContract,
    owner_contract: WorktreeContract,
    topology: TaskDocumentTopology,
    children: tuple[ResolvedTaskDocument, ...],
) -> None:
    try:
        found = resolve_terminal_leaf_doc(owner_contract.task_root, contract.leaf_id)
    except TerminalLeafResolutionError as exc:
        raise TaskDocumentRefError("route-review-atomic-child-invalid", str(exc)) from exc
    if found is None:
        raise TaskDocumentRefError(
            "route-review-atomic-child-invalid",
            f"atomic child contract {contract.leaf_id!r} has no canonical task document",
        )
    path, document = found
    ref = topology.canonical_ref(owner_contract.repo_name, path)
    if ref not in {child.ref for child in children} or document.kind == "master":
        raise TaskDocumentRefError(
            "route-review-atomic-child-invalid",
            f"leaf contract {contract.leaf_id!r} is not a canonical child of the atomic master",
        )


def _master_intent_identity(
    task_root: Path,
    master: ResolvedTaskDocument,
) -> TaskIntentIdentity:
    projection = task_intent_master_projection(task_root, master)
    payload = {
        "schema": "task-intent/v1",
        "scope": "atomic-master",
        "masterRef": master.ref.model_dump(mode="json"),
        "projection": projection,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return TaskIntentIdentity(digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def _aggregate_intent(scope: RouteReviewScope) -> TaskIntentIdentity:
    payload = {
        "schema": "task-intent/v1",
        "scope": "atomic-master-review",
        "masterRef": scope.masterRef.model_dump(mode="json"),
        "masterIntent": scope.masterIntent.model_dump(mode="json", by_alias=True),
        "childIntents": [
            child.model_dump(mode="json", by_alias=True) for child in scope.childIntents
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return TaskIntentIdentity(digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def _candidate_tree(
    contract: WorktreeContract,
    *,
    expected_candidate_commit: str | None,
) -> str:
    if expected_candidate_commit is None:
        return code_candidate_tree(contract)
    return _require_git_tree(contract, expected_candidate_commit)


def _require_git_tree(contract: WorktreeContract, commit: str) -> str:
    try:
        return require_git(
            contract.code_repo_path,
            ["rev-parse", f"{commit}^{{tree}}"],
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise RouteReviewError(
            "route-review-master-candidate-unreadable",
            f"journaled master integration candidate cannot be resolved: {commit}",
        ) from exc


def _master_dependencies(
    scope: AtomicMasterReviewScope,
    candidate_tree: str,
    stamped: dict[str, Any],
):
    evidence = _stamped_evidence(stamped)
    edges = [
        dependency("code-tree", "candidate", candidate_tree, algorithm="git-object"),
        dependency("task-intent", "aggregate", scope.aggregate_intent.digest),
        dependency("task-intent", "master", scope.review_scope.masterIntent.digest),
        dependency("task-intent", "membership", scope.membership_digest),
        *(
            dependency("task-intent", f"child:{child.ref.key}", child.taskIntent.digest)
            for child in scope.review_scope.childIntents
        ),
        dependency(
            "validator",
            EVIDENCE_DEPENDENCY_VALIDATOR,
            canonical_sha256(EVIDENCE_DEPENDENCY_VALIDATOR),
        ),
        *(dependency("evidence-bytes", ref, digest) for ref, digest in evidence.items()),
    ]
    return build_evidence_dependencies("route-review/v1", edges)


def _require_master_dependencies(
    review: RouteReviewRecord,
    scope: AtomicMasterReviewScope,
) -> None:
    try:
        expected = _master_dependencies(
            scope,
            review.candidateTree,
            {
                "verdictRef": review.verdictRef,
                "verdictSha256": review.verdictSha256,
                "routes": [route.model_dump(mode="json", by_alias=True) for route in review.routes],
            },
        )
        observed = require_evidence_dependencies(
            review.dependencies,
            record_type="route-review/v1",
        )
    except EvidenceDependencyError as exc:
        raise RouteReviewError(exc.status, exc.detail) from exc
    if observed != expected:
        raise RouteReviewError(
            "route-review-master-dependencies-stale",
            "atomic master route-review dependencies do not match current canonical inputs",
        )


__all__ = [
    "AtomicMasterReviewScope",
    "build_master_route_review",
    "reject_atomic_child_route_review",
    "require_current_route_review",
    "resolve_atomic_master_scope",
]
