"""Exact completion proof for a branchless organizational master."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    LedgerRow,
    find_unique_mapping,
    parse_ledger_text,
)
from agents_remember.models.closeout_queue import CloseoutCandidateRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, completion_blockers, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.worktrees.integration_branch_authority import integration_targets
from agents_remember.worktrees.modules.git import is_ancestor, repository_identity, require_git
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


class OrganizationalCompletionError(RuntimeError):
    """The proposed organizational completion edge is not exact or publishable."""


_COMPLETION_DECISION = "Complete organizational master at its certified final-leaf landing."
_COMPLETION_RATIONALE_PREFIX = (
    "The sprint queue proved every sibling landed, the full master gate passed "
    "against the exact proposed super candidate, and the paired refs moved under "
    "one integration authority. completionFingerprint="
)


@dataclass(frozen=True)
class OrganizationalCompletionPlan:
    """The exact final leaf and logical master generation certified before landing."""

    sprint_ref: TaskDocumentRef
    master_ref: TaskDocumentRef
    candidate_ref: TaskDocumentRef
    master_path: Path
    master_document: TaskDocument
    child_refs: tuple[TaskDocumentRef, ...]
    code_commit: str
    code_tree: str
    memory_content_commit: str
    ledger_commit: str
    fingerprint: str


@dataclass(frozen=True)
class OrganizationalCompletionContext:
    topology: TaskDocumentTopology
    sprint: ResolvedTaskDocument
    master: ResolvedTaskDocument
    candidate: CloseoutCandidateRecord
    candidates: Mapping[str, CloseoutCandidateRecord]


@dataclass(frozen=True)
class _CompletionScope:
    children: tuple[TaskDocumentRef, ...]
    source_branch: str


@dataclass(frozen=True)
class _SiblingExpectation:
    completing_contract: WorktreeContract
    contract_path: Path
    sprint_ref: TaskDocumentRef
    child_ref: TaskDocumentRef
    child_id: str
    source_branch: str


def organizational_completion_plan(
    context: OrganizationalCompletionContext,
    *,
    contract: WorktreeContract,
) -> OrganizationalCompletionPlan | None:
    """Return the final organizational candidate, or ``None`` while siblings remain.

    Task rows alone are deliberately insufficient: a row becomes ``Completed`` when its
    declared work units are done, before its Git edge lands. Every sibling therefore has
    to carry an exact completed leaf contract whose code/memory pair is reachable from the
    current sprint super. The proposed candidate is the sole allowed unlanded child.
    """

    scope = _completion_scope(context)
    if scope is None:
        return None
    _require_candidate_identity(
        contract,
        context.candidate,
        context.sprint.ref,
    )
    landed = _landed_siblings(context, scope, contract=contract)
    code_tree = _commit_tree(contract.code_repo_path, contract.code_commit)
    semantic_master = _semantic_master_digest(context.master.document)
    fingerprint = _fingerprint(
        {
            "sprint": context.sprint.ref.key,
            "master": context.master.ref.key,
            "candidate": context.candidate.taskDocumentRef.key,
            "children": [ref.key for ref in scope.children],
            "masterSemanticDigest": semantic_master,
            "landedSiblings": landed,
            "codeCommit": contract.code_commit,
            "codeTree": code_tree,
            "memoryContentCommit": contract.memory_content_commit,
            "ledgerCommit": contract.ledger_commit,
        }
    )
    if context.master.document.status == "Completed" and not _has_completion_marker(
        context.master.document,
        fingerprint=fingerprint,
    ):
        raise OrganizationalCompletionError(
            "completed organizational master does not carry its exact certified marker"
        )
    return OrganizationalCompletionPlan(
        sprint_ref=context.sprint.ref,
        master_ref=context.master.ref,
        candidate_ref=context.candidate.taskDocumentRef,
        master_path=context.master.path,
        master_document=context.master.document,
        child_refs=scope.children,
        code_commit=contract.code_commit,
        code_tree=code_tree,
        memory_content_commit=contract.memory_content_commit,
        ledger_commit=contract.ledger_commit,
        fingerprint=fingerprint,
    )


def _completion_scope(
    context: OrganizationalCompletionContext,
) -> _CompletionScope | None:
    nature = context.master.document.executionNature
    if nature == "atomic":
        return None
    if nature != "organizational":
        raise OrganizationalCompletionError(
            f"organizational completion requires executionNature='organizational', got {nature!r}"
        )
    if context.topology.parent(context.master.ref) != context.sprint.ref:
        raise OrganizationalCompletionError(
            "organizational completion master is not owned by the bound sprint"
        )
    if context.candidate.owningMaster != context.master.ref:
        raise OrganizationalCompletionError(
            "organizational completion candidate names a different owning master"
        )
    children = tuple(context.topology.children(context.master.ref))
    if context.candidate.taskDocumentRef not in children:
        raise OrganizationalCompletionError(
            "organizational completion candidate is not a canonical child of its master"
        )
    if completion_blockers(context.master.document):
        return None
    if any(
        queued.taskDocumentRef != context.candidate.taskDocumentRef
        and queued.owningMaster == context.master.ref
        for queued in context.candidates.values()
    ):
        return None
    source_branch = context.sprint.document.integrationBranch
    if not source_branch:
        raise OrganizationalCompletionError(
            "organizational completion requires the sprint integrationBranch"
        )
    return _CompletionScope(children, source_branch)


def _landed_siblings(
    context: OrganizationalCompletionContext,
    scope: _CompletionScope,
    *,
    contract: WorktreeContract,
) -> list[dict[str, str]]:
    landed: list[dict[str, str]] = []
    for child_ref in scope.children:
        if child_ref == context.candidate.taskDocumentRef:
            continue
        child = context.topology.resolve(child_ref)
        sibling_path = leaf_enclosure_path(context.master.path.parent, child.document.id)
        _require_confined_sibling_contract_path(
            context.master.path.parent,
            sibling_path,
            child_ref,
        )
        try:
            sibling = load_contract(sibling_path)
        except (ContractError, OSError, RuntimeError, ValueError) as error:
            raise OrganizationalCompletionError(
                f"organizational sibling {child_ref.key} has no readable landing contract"
            ) from error
        if sibling.integration_status != "completed":
            raise OrganizationalCompletionError(
                f"organizational sibling {child_ref.key} is not integrated"
            )
        landed.append(
            _require_landed_sibling(
                sibling,
                _SiblingExpectation(
                    completing_contract=contract,
                    contract_path=sibling_path,
                    sprint_ref=context.sprint.ref,
                    child_ref=child_ref,
                    child_id=child.document.id,
                    source_branch=scope.source_branch,
                ),
            )
        )
    return landed


def publish_organizational_master_completion(
    plan: OrganizationalCompletionPlan,
    *,
    certified_fingerprint: str,
) -> None:
    """Publish the logical master terminal edge after its certified ref movement."""

    if certified_fingerprint != plan.fingerprint:
        raise OrganizationalCompletionError(
            "organizational completion quality certificate does not match the final-leaf plan"
        )
    current = TaskDocument.model_validate_json(plan.master_path.read_text(encoding="utf-8"))
    if current.status == "Completed" and _has_completion_marker(
        current, fingerprint=plan.fingerprint
    ):
        write_task_doc(plan.master_path.parent, current)
        return
    if current != plan.master_document:
        raise OrganizationalCompletionError(
            "organizational master task document changed before completion publication"
        )
    if completion_blockers(current):
        raise OrganizationalCompletionError(
            "organizational master regained unresolved work before completion publication"
        )
    payload = current.model_dump(by_alias=True)
    payload["status"] = "Completed"
    payload["decisions"] = [
        {
            "at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "decision": _COMPLETION_DECISION,
            "rationale": f"{_COMPLETION_RATIONALE_PREFIX}{plan.fingerprint}",
        },
        *payload.get("decisions", []),
    ]
    write_task_doc(plan.master_path.parent, TaskDocument.model_validate(payload))


def require_published_organizational_master_completion(
    document: TaskDocument,
    *,
    fingerprint: str,
) -> None:
    """Prove the final logical edge after its queue candidate was consumed."""

    if document.status != "Completed" or not _has_completion_marker(
        document,
        fingerprint=fingerprint,
    ):
        raise OrganizationalCompletionError(
            "organizational master completion marker is not durably published"
        )


def _require_candidate_identity(
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
    sprint_ref: TaskDocumentRef,
) -> None:
    integration_is_exact = contract.integration_status == "not-started" or (
        contract.integration_status == "completed"
        and contract.integrated_code_commit == contract.code_commit
        and contract.integrated_memory_content_commit == contract.memory_content_commit
        and contract.integrated_ledger_commit == contract.ledger_commit
    )
    if (
        contract.kind != "leaf"
        or not integration_is_exact
        or contract.closeout_status != "completed"
        or contract.queue_candidate_task_document != candidate.taskDocumentRef.key
        or contract.queue_sprint_task_document != sprint_ref.key
    ):
        raise OrganizationalCompletionError(
            "organizational completion candidate contract is not the certified leaf edge"
        )
    integration_targets(contract)


def _require_landed_sibling(
    contract: WorktreeContract,
    expected: _SiblingExpectation,
) -> dict[str, str]:
    completing_contract = expected.completing_contract
    child_ref = expected.child_ref
    _require_sibling_contract_identity(contract, expected)
    if not _same_repository(contract.code_repo_path, completing_contract.code_repo_path):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} belongs to another code repository"
        )
    integration_targets(contract)
    if not (
        is_ancestor(
            contract.code_repo_path,
            contract.code_base_commit,
            contract.integrated_code_commit,
        )
        and is_ancestor(
            contract.code_repo_path,
            contract.integrated_code_commit,
            completing_contract.code_base_commit,
        )
    ):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} code commit is not on the sprint super"
        )
    if contract.memory_mode == "external":
        _require_landed_sibling_memory(contract, expected)
    return {
        "child": child_ref.key,
        "code": contract.integrated_code_commit,
        "memory": contract.integrated_memory_content_commit,
        "ledger": contract.integrated_ledger_commit,
        "codeBase": contract.code_base_commit,
        "memoryBase": contract.memory_base_commit,
    }


def _require_sibling_contract_identity(
    contract: WorktreeContract,
    expected: _SiblingExpectation,
) -> None:
    completing_contract = expected.completing_contract
    child_ref = expected.child_ref
    source_branch = expected.source_branch
    if (
        contract.kind != "leaf"
        or contract.task_id != completing_contract.task_id
        or contract.task_name != completing_contract.task_name
        or contract.repo_name != completing_contract.repo_name
        or contract.coordination_root.resolve() != completing_contract.coordination_root.resolve()
        or contract.task_root.resolve() != completing_contract.task_root.resolve()
        or contract.contract_path != expected.contract_path
        or contract.leaf_id != expected.child_id
        or contract.parent_task_name != completing_contract.parent_task_name
        or contract.parent_contract_path is not None
        or contract.memory_mode != completing_contract.memory_mode
        or contract.queue_sprint_task_document != expected.sprint_ref.key
        or contract.queue_candidate_task_document != child_ref.key
        or contract.code_source_branch != source_branch
        or not contract.integrated_code_commit
        or contract.integrated_code_commit != contract.code_commit
    ):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} has no exact landed code edge"
        )


def _require_landed_sibling_memory(
    contract: WorktreeContract,
    expected: _SiblingExpectation,
) -> None:
    completing_contract = expected.completing_contract
    child_ref = expected.child_ref
    _require_sibling_memory_identity(contract, completing_contract, child_ref)
    mapping, final_mapping = _sibling_memory_mappings(contract, completing_contract, child_ref)
    _require_sibling_memory_ancestry(contract, completing_contract, child_ref)
    _require_sibling_memory_mapping(contract, child_ref, mapping, final_mapping)


def _require_sibling_memory_identity(
    contract: WorktreeContract,
    completing_contract: WorktreeContract,
    child_ref: TaskDocumentRef,
) -> None:
    if (
        contract.memory_repo_path is None
        or completing_contract.memory_repo_path is None
        or not contract.integrated_memory_content_commit
        or not contract.integrated_ledger_commit
        or contract.integrated_memory_content_commit != contract.memory_content_commit
        or contract.integrated_ledger_commit != contract.ledger_commit
    ):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} has no exact landed memory edge"
        )
    if not _same_repository(
        contract.memory_repo_path,
        completing_contract.memory_repo_path,
    ):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} belongs to another memory repository"
        )


def _sibling_memory_mappings(
    contract: WorktreeContract,
    completing_contract: WorktreeContract,
    child_ref: TaskDocumentRef,
) -> tuple[LedgerRow | None, LedgerRow | None]:
    assert contract.memory_repo_path is not None
    assert completing_contract.memory_repo_path is not None
    try:
        mapping = find_unique_mapping(
            parse_ledger_text(
                require_git(
                    contract.memory_repo_path,
                    ["show", f"{contract.integrated_ledger_commit}:memory.md"],
                )
            ),
            contract.integrated_code_commit,
        )
        final_mapping = find_unique_mapping(
            parse_ledger_text(
                require_git(
                    completing_contract.memory_repo_path,
                    ["show", f"{completing_contract.ledger_commit}:memory.md"],
                )
            ),
            contract.integrated_code_commit,
        )
    except LedgerError as error:
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} has duplicate code mappings"
        ) from error
    return mapping, final_mapping


def _require_sibling_memory_ancestry(
    contract: WorktreeContract,
    completing_contract: WorktreeContract,
    child_ref: TaskDocumentRef,
) -> None:
    assert contract.memory_repo_path is not None
    if (
        not is_ancestor(
            contract.memory_repo_path,
            contract.memory_base_commit,
            contract.integrated_memory_content_commit,
        )
        or not is_ancestor(
            contract.memory_repo_path,
            contract.memory_base_commit,
            contract.integrated_ledger_commit,
        )
        or not is_ancestor(
            contract.memory_repo_path,
            contract.integrated_memory_content_commit,
            contract.integrated_ledger_commit,
        )
        or not is_ancestor(
            contract.memory_repo_path,
            contract.integrated_ledger_commit,
            completing_contract.memory_base_commit,
        )
    ):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} memory mapping is not on the sprint super"
        )


def _require_sibling_memory_mapping(
    contract: WorktreeContract,
    child_ref: TaskDocumentRef,
    mapping: LedgerRow | None,
    final_mapping: LedgerRow | None,
) -> None:
    if mapping is None or mapping.memory_commit != contract.integrated_memory_content_commit:
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} memory mapping is not on the sprint super"
        )
    if (
        final_mapping is None
        or final_mapping.memory_commit != contract.integrated_memory_content_commit
    ):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} mapping is not preserved in the proposed "
            "final ledger"
        )


def _same_repository(left: Path, right: Path) -> bool:
    left_identity = repository_identity(left)
    right_identity = repository_identity(right)
    return left_identity is not None and left_identity == right_identity


def _require_confined_sibling_contract_path(
    master_root: Path,
    contract_path: Path,
    child_ref: TaskDocumentRef,
) -> None:
    """Reject a sibling enclosure reached through any symlink or path escape."""

    relative = contract_path.relative_to(master_root)
    cursor = master_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise OrganizationalCompletionError(
                f"organizational sibling {child_ref.key} contract escapes through a symlink"
            )
    try:
        resolved_root = master_root.resolve(strict=True)
        resolved_contract = contract_path.resolve(strict=True)
    except OSError as error:
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} has no readable landing contract"
        ) from error
    if not resolved_contract.is_relative_to(resolved_root):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} contract escapes its master task root"
        )


def _commit_tree(repository: Path, commit: str) -> str:
    result = run_git(repository, ["rev-parse", f"{commit}^{{tree}}"])
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise OrganizationalCompletionError(
            f"cannot resolve organizational candidate {commit}" + (f": {detail}" if detail else "")
        )
    return result.stdout.strip()


def _semantic_master_digest(document: TaskDocument) -> str:
    markers = [
        index
        for index, decision in enumerate(document.decisions)
        if _completion_marker_fingerprint(decision) is not None
    ]
    candidate = document
    if document.status == "Completed" and len(markers) == 1:
        candidate = document.model_copy(
            update={
                "status": "inProgress",
                "decisions": [
                    decision
                    for index, decision in enumerate(document.decisions)
                    if index != markers[0]
                ],
            }
        )
    payload = candidate.model_dump(mode="json", by_alias=True)
    return _fingerprint(payload)


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _has_completion_marker(
    document: TaskDocument,
    *,
    fingerprint: str | None = None,
) -> bool:
    return any(
        (marker_fingerprint := _completion_marker_fingerprint(decision)) is not None
        and (fingerprint is None or marker_fingerprint == fingerprint)
        for decision in document.decisions
    )


def _completion_marker_fingerprint(decision: object) -> str | None:
    if getattr(decision, "decision", None) != _COMPLETION_DECISION:
        return None
    rationale = getattr(decision, "rationale", "")
    if not isinstance(rationale, str) or not rationale.startswith(_COMPLETION_RATIONALE_PREFIX):
        return None
    fingerprint = rationale.removeprefix(_COMPLETION_RATIONALE_PREFIX)
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        return None
    return fingerprint
