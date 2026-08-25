"""Exact completion proof for a branchless organizational master."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    LedgerRow,
    find_unique_mapping,
    parse_ledger_text,
)
from agents_remember.models.lifecycles.door import CloseoutDoorGeneration
from agents_remember.models.lifecycles.operation import (
    IntegrationQualityCertification,
    OrganizationalTaskPublicationIntent,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, completion_blockers, render_markdown
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.worktrees.integration.integration_branch_authority import integration_targets
from agents_remember.worktrees.modules.git import is_ancestor, repository_identity, require_git
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


class OrganizationalCompletionError(RuntimeError):
    """The proposed organizational completion edge is not exact or publishable."""


class OrganizationalCompletionPublicationError(RuntimeError):
    """Exact task-document publication found a persistent third byte state."""

    def __init__(
        self,
        detail: str,
        *,
        expected: Mapping[str, object],
        observed: Mapping[str, object],
    ) -> None:
        self.detail = detail
        self.expected = dict(expected)
        self.observed = dict(observed)
        super().__init__(detail)


@dataclass(frozen=True)
class OrganizationalCompletionPublicationState:
    """Pure exact JSON/Markdown state for one journaled completion intent."""

    state: Literal["convergent", "published", "developer-decision"]
    expected: dict[str, object]
    observed: dict[str, object]

    @property
    def mechanically_convergent(self) -> bool:
        return self.state != "developer-decision"

    def decision_payload(self) -> dict[str, object]:
        detail = "organizational task document has a third byte state"
        return {
            "state": "organizational-completion-publication-conflict",
            "reason": detail,
            "summary": detail,
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "nextAction": "developer-decision",
            "expected": self.expected,
            "observed": self.observed,
        }


_COMPLETION_DECISION = "Complete organizational master at its certified final-leaf landing."
_COMPLETION_RATIONALE_PREFIX = (
    "Canonical sibling contracts proved every sibling landed, the full master gate passed "
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
    candidate: CloseoutDoorGeneration


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
    if context.candidate.owningMasterTaskDocumentRef != context.master.ref:
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


def prepare_organizational_master_completion(
    plan: OrganizationalCompletionPlan,
    *,
    certification: IntegrationQualityCertification,
    completed_at: str,
) -> OrganizationalTaskPublicationIntent:
    """Choose and bind exact before/after task bytes before protected refs move."""

    if certification.completionFingerprint != plan.fingerprint:
        raise OrganizationalCompletionError(
            "organizational completion quality certificate does not match the final-leaf plan"
        )
    accepted_json = plan.master_path.read_text(encoding="utf-8")
    current = TaskDocument.model_validate_json(accepted_json)
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
            "at": completed_at,
            "decision": _COMPLETION_DECISION,
            "rationale": f"{_COMPLETION_RATIONALE_PREFIX}{plan.fingerprint}",
        },
        *payload.get("decisions", []),
    ]
    intended = TaskDocument.model_validate(payload)
    intended_json = intended.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n"
    markdown_path = plan.master_path.with_suffix(".md")
    accepted_markdown = markdown_path.read_text(encoding="utf-8")
    expected_markdown = render_markdown(current)
    if accepted_markdown != expected_markdown:
        raise OrganizationalCompletionError(
            "organizational master Markdown changed outside its accepted JSON render"
        )
    intended_markdown = render_markdown(intended)
    return OrganizationalTaskPublicationIntent(
        masterTaskDocument=plan.master_path.as_posix(),
        sprintTaskDocument=plan.sprint_ref.key,
        candidateTaskDocument=plan.candidate_ref.key,
        completionFingerprint=plan.fingerprint,
        certificationResultSha256=certification.resultSha256,
        completedAt=completed_at,
        acceptedJson=accepted_json,
        acceptedJsonSha256=_text_sha256(accepted_json),
        intendedJson=intended_json,
        intendedJsonSha256=_text_sha256(intended_json),
        acceptedMarkdown=accepted_markdown,
        acceptedMarkdownSha256=_text_sha256(accepted_markdown),
        intendedMarkdown=intended_markdown,
        intendedMarkdownSha256=_text_sha256(intended_markdown),
    )


def publish_organizational_master_completion(
    intent: OrganizationalTaskPublicationIntent,
) -> None:
    """CAS/prove the exact journaled JSON+Markdown completion bytes."""

    classification = classify_organizational_master_completion(intent)
    if not classification.mechanically_convergent:
        raise OrganizationalCompletionPublicationError(
            "organizational task document has a third byte state",
            expected=classification.expected,
            observed=classification.observed,
        )
    json_path = Path(intent.masterTaskDocument)
    markdown_path = json_path.with_suffix(".md")
    try:
        current_json = json_path.read_text(encoding="utf-8")
        current_markdown = markdown_path.read_text(encoding="utf-8")
        if current_json != intent.intendedJson:
            atomic_write_text(json_path, intent.intendedJson)
        if current_markdown != intent.intendedMarkdown:
            atomic_write_text(markdown_path, intent.intendedMarkdown)
    except (OSError, UnicodeError, ValueError) as exc:
        interrupted = classify_organizational_master_completion(intent)
        raise OrganizationalCompletionPublicationError(
            "organizational task publication was interrupted",
            expected=interrupted.expected,
            observed=interrupted.observed,
        ) from exc
    final = classify_organizational_master_completion(intent)
    if final.state != "published":
        raise OrganizationalCompletionPublicationError(
            "organizational task publication did not produce its exact intended bytes",
            expected=final.expected,
            observed=final.observed,
        )


def classify_organizational_master_completion(
    intent: OrganizationalTaskPublicationIntent,
) -> OrganizationalCompletionPublicationState:
    """Read and classify exact journal-owned task bytes without mutating them."""

    expected: dict[str, object] = {
        "acceptedJsonSha256": intent.acceptedJsonSha256,
        "intendedJsonSha256": intent.intendedJsonSha256,
        "acceptedMarkdownSha256": intent.acceptedMarkdownSha256,
        "intendedMarkdownSha256": intent.intendedMarkdownSha256,
    }
    observed: dict[str, object] = {}
    json_path = Path(intent.masterTaskDocument)
    try:
        current_json = json_path.read_bytes()
        observed["jsonSha256"] = _bytes_sha256(current_json)
    except (OSError, UnicodeError, ValueError) as exc:
        return OrganizationalCompletionPublicationState(
            "developer-decision",
            expected,
            {
                **observed,
                "readFailure": {
                    "side": "json",
                    "name": json_path.name,
                    "errorType": type(exc).__name__,
                },
            },
        )
    markdown_path = json_path.with_suffix(".md")
    try:
        current_markdown = markdown_path.read_bytes()
        observed["markdownSha256"] = _bytes_sha256(current_markdown)
    except (OSError, UnicodeError, ValueError) as exc:
        return OrganizationalCompletionPublicationState(
            "developer-decision",
            expected,
            {
                **observed,
                "readFailure": {
                    "side": "markdown",
                    "name": markdown_path.name,
                    "errorType": type(exc).__name__,
                },
            },
        )
    if current_json not in {
        intent.acceptedJson.encode("utf-8"),
        intent.intendedJson.encode("utf-8"),
    } or current_markdown not in {
        intent.acceptedMarkdown.encode("utf-8"),
        intent.intendedMarkdown.encode("utf-8"),
    }:
        return OrganizationalCompletionPublicationState("developer-decision", expected, observed)
    state: Literal["convergent", "published"] = (
        "published"
        if current_json == intent.intendedJson.encode("utf-8")
        and current_markdown == intent.intendedMarkdown.encode("utf-8")
        else "convergent"
    )
    return OrganizationalCompletionPublicationState(state, expected, observed)


def require_published_organizational_master_completion(
    document: TaskDocument,
    *,
    fingerprint: str,
) -> None:
    """Prove the final logical edge from its claimed door and root-journal authority."""

    if document.status != "Completed" or not _has_completion_marker(
        document,
        fingerprint=fingerprint,
    ):
        raise OrganizationalCompletionError(
            "organizational master completion marker is not durably published"
        )


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_candidate_identity(
    contract: WorktreeContract,
    candidate: CloseoutDoorGeneration,
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
        or candidate.disposition != "claimed"
        or contract.closeout_door != candidate
        or candidate.sprintTaskDocumentRef != sprint_ref
        or candidate.contractPath != contract.contract_path.as_posix()
    ):
        raise OrganizationalCompletionError(
            "organizational completion candidate is not the exact claimed leaf edge"
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
    _require_sibling_code_ancestry(contract, completing_contract, child_ref)
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


def _require_sibling_code_ancestry(
    contract: WorktreeContract,
    completing_contract: WorktreeContract,
    child_ref: TaskDocumentRef,
) -> None:
    code_ancestry = (
        is_ancestor(
            contract.code_repo_path,
            contract.code_base_commit,
            contract.integrated_code_commit,
        ),
        is_ancestor(
            contract.code_repo_path,
            contract.integrated_code_commit,
            completing_contract.code_base_commit,
        ),
    )
    if not all(code_ancestry):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} code commit is not on the sprint super"
        )


def _require_sibling_contract_identity(
    contract: WorktreeContract,
    expected: _SiblingExpectation,
) -> None:
    completing_contract = expected.completing_contract
    child_ref = expected.child_ref
    source_branch = expected.source_branch
    door = contract.closeout_door
    identity_mismatch = any(
        (
            contract.kind != "leaf",
            contract.task_id != completing_contract.task_id,
            contract.task_name != completing_contract.task_name,
            contract.repo_name != completing_contract.repo_name,
            contract.coordination_root.resolve() != completing_contract.coordination_root.resolve(),
            contract.task_root.resolve() != completing_contract.task_root.resolve(),
            contract.contract_path != expected.contract_path,
            contract.leaf_id != expected.child_id,
            contract.parent_task_name != completing_contract.parent_task_name,
            contract.parent_contract_path is not None,
            contract.memory_mode != completing_contract.memory_mode,
            getattr(door, "disposition", None) != "claimed",
            getattr(door, "sprintTaskDocumentRef", None) != expected.sprint_ref,
            getattr(door, "taskDocumentRef", None) != child_ref,
            contract.code_source_branch != source_branch,
            not contract.integrated_code_commit,
            contract.integrated_code_commit != contract.code_commit,
        )
    )
    if identity_mismatch:
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
    memory_repository = contract.memory_repo_path
    completing_memory_repository = completing_contract.memory_repo_path
    identity = (
        memory_repository is not None,
        completing_memory_repository is not None,
        bool(contract.integrated_memory_content_commit),
        bool(contract.integrated_ledger_commit),
        contract.integrated_memory_content_commit == contract.memory_content_commit,
        contract.integrated_ledger_commit == contract.ledger_commit,
    )
    if identity != (True, True, True, True, True, True):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} has no exact landed memory edge"
        )
    memory_repository = cast(Path, memory_repository)
    completing_memory_repository = cast(Path, completing_memory_repository)
    if not _same_repository(memory_repository, completing_memory_repository):
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
    ancestry = (
        is_ancestor(
            contract.memory_repo_path,
            contract.memory_base_commit,
            contract.integrated_memory_content_commit,
        ),
        is_ancestor(
            contract.memory_repo_path,
            contract.memory_base_commit,
            contract.integrated_ledger_commit,
        ),
        is_ancestor(
            contract.memory_repo_path,
            contract.integrated_memory_content_commit,
            contract.integrated_ledger_commit,
        ),
        is_ancestor(
            contract.memory_repo_path,
            contract.integrated_ledger_commit,
            completing_contract.memory_base_commit,
        ),
    )
    if not all(ancestry):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} memory mapping is not on the sprint super"
        )


def _require_sibling_memory_mapping(
    contract: WorktreeContract,
    child_ref: TaskDocumentRef,
    mapping: LedgerRow | None,
    final_mapping: LedgerRow | None,
) -> None:
    if getattr(mapping, "memory_commit", None) != contract.integrated_memory_content_commit:
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} memory mapping is not on the sprint super"
        )
    if getattr(final_mapping, "memory_commit", None) != contract.integrated_memory_content_commit:
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

    if _path_crosses_symlink(master_root, contract_path):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} contract escapes through a symlink"
        )
    resolved_root, resolved_contract = _resolved_sibling_paths(
        master_root,
        contract_path,
        child_ref,
    )
    if not resolved_contract.is_relative_to(resolved_root):
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} contract escapes its master task root"
        )


def _path_crosses_symlink(master_root: Path, contract_path: Path) -> bool:
    relative = contract_path.relative_to(master_root)
    cursor = master_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            return True
    return False


def _resolved_sibling_paths(
    master_root: Path,
    contract_path: Path,
    child_ref: TaskDocumentRef,
) -> tuple[Path, Path]:
    try:
        return master_root.resolve(strict=True), contract_path.resolve(strict=True)
    except OSError as error:
        raise OrganizationalCompletionError(
            f"organizational sibling {child_ref.key} has no readable landing contract"
        ) from error


def _commit_tree(repository: Path, commit: str) -> str:
    result = run_git(repository, ["rev-parse", f"{commit}^{{tree}}"])
    if result.returncode != 0:
        raise OrganizationalCompletionError("cannot resolve the organizational candidate tree")
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
    rationale = _completion_marker_rationale(decision)
    if rationale is None:
        return None
    fingerprint = rationale.removeprefix(_COMPLETION_RATIONALE_PREFIX)
    if fingerprint == rationale:
        return None
    return _valid_completion_fingerprint(fingerprint)


def _completion_marker_rationale(decision: object) -> str | None:
    if getattr(decision, "decision", None) != _COMPLETION_DECISION:
        return None
    rationale = getattr(decision, "rationale", None)
    return rationale if isinstance(rationale, str) else None


def _valid_completion_fingerprint(fingerprint: str) -> str | None:
    return fingerprint if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None else None
