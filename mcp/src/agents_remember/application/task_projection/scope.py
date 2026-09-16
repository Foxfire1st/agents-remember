"""Bind the projection to the actual admitted worktree, branch and task document.

Every fact here comes from an existing AR owner, and this module decides nothing
that those owners already decide:

* **coordination context** -- :func:`resolve_coordination_context` with the
  worktree contract reader, i.e. the same resolution the ``context_packet`` tool
  uses. It answers where the coordination root, the task root and the memory
  worktree are, or refuses.
* **worktree contract** -- ``WorktreeContractReader.load_contract`` answers which
  work branch, source branch and base commit the admitted worktree actually has,
  and which task and leaf the enclosure belongs to.
* **task document** -- :class:`TaskDocumentTopology` answers whether the admitted
  task reference resolves to exactly one document, at what altitude, and whether
  the bound role may sit there. The exact accepted JSON bytes come from the task
  store's own ``capture_task_doc_source``, so the reported document digest is the
  bytes the owner read rather than a re-read that could have moved underneath.

Wrong branch, unknown task, a task outside the admitted worktree's root, a role
that cannot sit at the bound altitude, or an unresolved enclosure are each a
distinct typed refusal with a named remedy. There is no fallback branch, no
nearest match and no silent widening of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import TaskProjectionSourceError
from agents_remember.kernel.coordination_context.models import (
    ContractReaderPort,
    CoordinationHints,
    CoordinationRequest,
    EnclosureSelector,
    MissingMemoryError,
)
from agents_remember.kernel.coordination_context_resolver import resolve_coordination_context
from agents_remember.models.role_capsules.types import CapsuleBinding, compute_content_digest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskAltitude,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.store import TaskDocSourceSnapshot, capture_task_doc_source
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

from .statuses import (
    STATUS_BINDING_UNRESOLVED,
    STATUS_BRANCH_MISMATCH,
    STATUS_CONTRACT_UNAVAILABLE,
    STATUS_MEMORY_BINDING_UNAVAILABLE,
    STATUS_REPOSITORY_MISMATCH,
    STATUS_ROLE_ALTITUDE_MISMATCH,
    STATUS_TASK_BINDING_MISMATCH,
    STATUS_TASK_REFERENCE_INVALID,
    STATUS_TASK_REVISION_MISMATCH,
    STATUS_TASK_UNKNOWN,
)
from .types import BoundTask, ProjectionReadPlan, WorktreeBinding

_RECONCILE_REMEDY = (
    "re-run worktree_status for this task and admit the binding it reports; a projection "
    "is never produced from a branch or task the enclosure does not own"
)


def parse_task_reference(task_reference: str) -> TaskDocumentRef:
    """Parse the admitted task reference into the task layer's canonical identity.

    The accepted form is exactly :attr:`TaskDocumentRef.key` --
    ``"<repository>/<path-inside-tasks/<repository>>"``, e.g.
    ``"agents-remember/260915_role-capsules-and-native-eve/03_scoped-task-context.json"``.
    It is the task layer's own stable address, so there is no second identity
    vocabulary to keep in step.
    """

    repository, separator, path = task_reference.partition("/")
    if not separator:
        raise TaskProjectionSourceError(
            STATUS_TASK_REFERENCE_INVALID,
            f"admitted task reference {task_reference!r} is not '<repository>/<task path>'",
            next_action=(
                "admit the task document's canonical reference key, e.g. "
                "'agents-remember/<task-name>/<slug>.json'"
            ),
        )
    try:
        return TaskDocumentRef(repository=repository, path=path)
    except ValueError as error:
        raise TaskProjectionSourceError(
            STATUS_TASK_REFERENCE_INVALID,
            f"admitted task reference {task_reference!r} is not canonical: {error}",
            next_action="admit the task document's canonical reference key",
        ) from error


@dataclass(frozen=True, slots=True)
class ProjectionScopeRequest:
    """The admitted context hints a caller already knows, as one typed value.

    ``selector`` names the enclosure; the rest are hints the coordination resolver
    already accepts. They travel together because a caller never supplies one
    without deciding the others, and because six positional arguments made every
    call site a matrix no reader could check.
    """

    selector: EnclosureSelector
    workspace_root: Path | None = None
    code_repository_root: Path | None = None
    contract_reader: ContractReaderPort | None = None


@dataclass(frozen=True, slots=True)
class ResolvedProjectionScope:
    """One resolution's binding plus the live owner handles the projection reads through."""

    coordination_root: Path
    task_root: Path
    bound_task: BoundTask
    worktree: WorktreeBinding
    topology: TaskDocumentTopology
    bound_document: ResolvedTaskDocument
    parent_document: ResolvedTaskDocument | None
    parent_altitude: TaskAltitude | None
    memory_gap: str | None


def _contract_of(contract_path: Path | None) -> WorktreeContract:
    if contract_path is None:
        raise TaskProjectionSourceError(
            STATUS_BINDING_UNRESOLVED,
            "the admitted enclosure did not resolve to a worktree contract",
            next_action=(
                "name the enclosure explicitly (contract path, or task name plus leaf id or "
                "worktree name) and retry; without a contract there is no admitted branch or "
                "task to bind to"
            ),
        )
    try:
        return load_contract(contract_path)
    except (ContractError, OSError) as error:
        raise TaskProjectionSourceError(
            STATUS_CONTRACT_UNAVAILABLE,
            f"worktree contract {contract_path.as_posix()} could not be read: {error}",
            next_action="run worktree_status for this task and repair the enclosure contract",
            owner_status=type(error).__name__,
        ) from error


def require_admitted_revision(
    binding: CapsuleBinding,
    observed_digest: str,
    task_reference: str,
) -> None:
    """Refuse a binding whose admitted task revision is not the revision that was read.

    ``task_document_digest`` is an *admission*: an existing AR owner computed it
    from the exact bytes it handed over. The projection computes the digest of the
    bytes it actually read, and those two facts about the same thing must be
    compared -- otherwise a stale or forged admission projects the current document
    successfully while the capsule later seals the unverified digest
    (``models/role_capsules/compiler.py``), and a changed revision would be applied
    as if it were the selected one.

    This is the one place the comparison happens, so the scope path and the
    provider path cannot drift into two spellings of the same check.
    """

    admitted = binding.admitted.task_document_digest
    if admitted == observed_digest:
        return
    raise TaskProjectionSourceError(
        STATUS_TASK_REVISION_MISMATCH,
        f"the admitted revision of {task_reference} is {admitted} but the bytes actually read "
        f"digest to {observed_digest}; the admitted task revision is not the one on disk",
        next_action=(
            "re-admit the binding from the current task document (task_document_digest must be "
            "the digest of the exact JSON bytes the projection reads), then re-project; a "
            "capsule is never compiled against a task revision it did not read"
        ),
    )


def _require_admitted_identity(
    binding: CapsuleBinding,
    contract: WorktreeContract,
) -> None:
    admitted = binding.admitted
    if contract.repo_name != admitted.repository_id:
        raise TaskProjectionSourceError(
            STATUS_REPOSITORY_MISMATCH,
            f"the admitted repository {admitted.repository_id!r} does not own the enclosure "
            f"contract, which declares {contract.repo_name!r}",
            next_action=_RECONCILE_REMEDY,
        )
    if contract.code_work_branch != admitted.work_branch:
        raise TaskProjectionSourceError(
            STATUS_BRANCH_MISMATCH,
            f"the admitted work branch {admitted.work_branch!r} is not the branch this "
            f"enclosure owns ({contract.code_work_branch!r}); the worktree at "
            f"{contract.code_worktree.as_posix()} is on {contract.code_work_branch!r}",
            next_action=_RECONCILE_REMEDY,
        )


def _bound_document(
    topology: TaskDocumentTopology,
    ref: TaskDocumentRef,
) -> tuple[ResolvedTaskDocument, TaskDocSourceSnapshot]:
    """Resolve exactly one task document from its accepted source bytes."""

    accepted = capture_task_doc_source(topology.path_for_ref(ref))
    if accepted.json_bytes is None:
        raise TaskProjectionSourceError(
            STATUS_TASK_UNKNOWN,
            f"the admitted task document {ref.key} does not exist at "
            f"{accepted.json_path.as_posix()}",
            next_action="admit the task reference of the document this enclosure was started for",
            owner_status="task-document-not-found",
        )
    reader = TaskDocumentTopology(topology.coordination_root, accepted_sources=[accepted])
    try:
        return reader.resolve(ref), accepted
    except TaskDocumentRefError as error:
        raise TaskProjectionSourceError(
            STATUS_TASK_UNKNOWN,
            f"the admitted task reference {ref.key} did not resolve: {error}",
            next_action="admit the task reference of the document this enclosure was started for",
            owner_status=error.status,
        ) from error


def _require_task_ownership(
    resolved: ResolvedTaskDocument,
    contract: WorktreeContract,
) -> None:
    task_root = contract.task_root.resolve(strict=False)
    if not resolved.path.resolve(strict=False).is_relative_to(task_root):
        raise TaskProjectionSourceError(
            STATUS_TASK_BINDING_MISMATCH,
            f"task document {resolved.path.as_posix()} is outside the admitted task root "
            f"{task_root.as_posix()}",
            next_action=_RECONCILE_REMEDY,
        )
    if contract.leaf_id and resolved.document.id != contract.leaf_id:
        raise TaskProjectionSourceError(
            STATUS_TASK_BINDING_MISMATCH,
            f"the enclosure is for leaf {contract.leaf_id!r} but the admitted task document "
            f"declares id {resolved.document.id!r}",
            next_action=_RECONCILE_REMEDY,
        )


def _altitude_of(
    topology: TaskDocumentTopology,
    ref: TaskDocumentRef,
    role: str | None,
) -> TaskAltitude:
    try:
        if role is None:
            return topology.altitude(ref)
        return topology.validate_role(ref, role)
    except TaskDocumentRefError as error:
        raise TaskProjectionSourceError(
            STATUS_ROLE_ALTITUDE_MISMATCH,
            f"task document {ref.key} cannot carry the bound seat: {error}",
            next_action=(
                "bind the seat to a task document at its own altitude (sprint, master or leaf) "
                "and retry"
            ),
            owner_status=error.status,
        ) from error


def _parent_of(
    topology: TaskDocumentTopology,
    ref: TaskDocumentRef,
    altitude: TaskAltitude,
) -> tuple[ResolvedTaskDocument | None, TaskAltitude | None]:
    """Resolve a leaf's immediate parent, and only a leaf's.

    A leaf's parent is one bounded read, and the read plan may either read it or
    reference it. A master's or sprint's own parent is deliberately not resolved:
    the only owner that answers it walks the whole repository master census, and
    no plan reads that document -- so paying for it would be cost without a fact.
    """

    if altitude != "leaf":
        return None, None
    # ``_altitude_of`` has already resolved this exact parent through the same owner, so a
    # refusal here is unreachable and is not written: a branch nothing can take is dead code,
    # not a safeguard. A leaf that is not declared by one master already failed there with
    # ``STATUS_ROLE_ALTITUDE_MISMATCH``.
    parent_ref = topology.parent(ref)
    if parent_ref is None:
        return None, None
    return topology.resolve(parent_ref), topology.altitude(parent_ref)


def _worktree_binding(contract: WorktreeContract) -> WorktreeBinding:
    return WorktreeBinding(
        repository_id=contract.repo_name,
        contract_path=contract.contract_path.as_posix(),
        code_worktree=contract.code_worktree.as_posix(),
        work_branch=contract.code_work_branch,
        source_branch=contract.code_source_branch,
        base_commit=contract.code_base_commit,
        memory_mode=contract.memory_mode,
        memory_worktree=(
            None if contract.memory_worktree is None else contract.memory_worktree.as_posix()
        ),
        memory_work_branch=contract.memory_work_branch,
        ledger_path=None if contract.ledger_path is None else contract.ledger_path.as_posix(),
    )


def _memory_gap(binding: WorktreeBinding) -> str | None:
    if binding.memory_mode == "disabled" or binding.memory_worktree is not None:
        return None
    return (
        f"memory mode is {binding.memory_mode!r} but the enclosure records no memory worktree; "
        "memory reads are unbound for this seat"
    )


def resolve_task_projection_scope(
    binding: CapsuleBinding,
    *,
    coordination_root: Path,
    scope_request: ProjectionScopeRequest,
) -> ResolvedProjectionScope:
    """Resolve the admitted worktree/branch/task context the projection will read.

    Raises :class:`~agents_remember.errors.TaskProjectionSourceError` for every
    unresolved or contradictory input. The bound task document is only read; no
    task state, memory content or Git ref is written by this call.
    """

    reader = scope_request.contract_reader or WorktreeContractReader()
    admitted = binding.admitted
    try:
        context = resolve_coordination_context(
            code_repository_name=admitted.repository_id,
            workspace_root=scope_request.workspace_root,
            code_repository_root=scope_request.code_repository_root,
            request=CoordinationRequest(
                hints=CoordinationHints(coordination_root=coordination_root),
                selector=scope_request.selector,
                contract_reader=reader,
            ),
        )
    except MissingMemoryError as error:
        raise TaskProjectionSourceError(
            STATUS_MEMORY_BINDING_UNAVAILABLE,
            str(error),
            next_action=(
                "initialize or point the coordination root at this repository's memory, then "
                "retry; the projection binds task and memory reads together"
            ),
            owner_status="missing-memory",
        ) from error
    except (ValueError, OSError) as error:
        raise TaskProjectionSourceError(
            STATUS_BINDING_UNRESOLVED,
            f"coordination context did not resolve for repository "
            f"{admitted.repository_id!r}: {error}",
            next_action=(
                "supply the coordination root and an unambiguous enclosure selector (contract "
                "path, or task name plus leaf id or worktree name)"
            ),
            owner_status=type(error).__name__,
        ) from error

    contract = _contract_of(context.contract_path)
    _require_admitted_identity(binding, contract)

    topology = TaskDocumentTopology(context.coordination_root)
    ref = parse_task_reference(admitted.task_reference)
    resolved, accepted = _bound_document(topology, ref)
    _require_task_ownership(resolved, contract)
    assert accepted.json_bytes is not None  # narrowed by _bound_document
    observed_digest = compute_content_digest(accepted.json_bytes)
    require_admitted_revision(binding, observed_digest, ref.key)

    role = admitted.seat.role
    altitude = _altitude_of(topology, ref, role)
    parent, parent_altitude = _parent_of(topology, ref, altitude)
    worktree = _worktree_binding(contract)
    return ResolvedProjectionScope(
        coordination_root=context.coordination_root,
        task_root=contract.task_root.resolve(strict=False),
        bound_task=BoundTask(
            reference=ref.key,
            task_id=resolved.document.id,
            title=resolved.document.title,
            kind=resolved.document.kind,
            altitude=altitude,
            objective=resolved.document.objective,
            document_digest=observed_digest,
            task_root=contract.task_root.resolve(strict=False).as_posix(),
            markdown_path=resolved.path.with_suffix(".md").as_posix(),
        ),
        worktree=worktree,
        topology=topology,
        bound_document=resolved,
        parent_document=parent,
        parent_altitude=parent_altitude,
        memory_gap=_memory_gap(worktree),
    )


def read_documents(
    scope: ResolvedProjectionScope,
    plan: ProjectionReadPlan,
) -> tuple[ResolvedTaskDocument, ...]:
    """Exactly the documents this plan reads, in plan order.

    This is the single enforcement point for "read only the task altitude and the
    relevant ancestors the bound role and operation require": a document the plan
    does not name is not handed to the projection, so it cannot be injected by
    accident further down.
    """

    documents = [scope.bound_document]
    if len(plan.read_altitudes) > 1 and scope.parent_document is not None:
        documents.append(scope.parent_document)
    return tuple(documents)


def referenced_documents(
    scope: ResolvedProjectionScope,
    plan: ProjectionReadPlan,
) -> tuple[ResolvedTaskDocument, ...]:
    """The resolved documents this plan points at instead of reading."""

    if scope.parent_document is None:
        return ()
    read = read_documents(scope, plan)
    if any(document.ref == scope.parent_document.ref for document in read):
        return ()
    return (scope.parent_document,)


__all__ = [
    "ProjectionScopeRequest",
    "ResolvedProjectionScope",
    "parse_task_reference",
    "read_documents",
    "referenced_documents",
    "require_admitted_revision",
    "resolve_task_projection_scope",
]
