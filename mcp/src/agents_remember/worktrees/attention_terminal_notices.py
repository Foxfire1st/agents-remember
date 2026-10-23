"""The two terminal notice intents a native publication wraps (MIK-R200 rule 5).

Every native publication of a final landing or a successful abandonment is one occurrence. This
module names that occurrence once so all three publishing routes -- ordinary integration and
``worktree_record_landing`` through :func:`record_landed_integration`, a completed direct-landing
generation, and a successful abandon -- agree on the identity and on the task they open.

The subject a notice names is the task document the enclosure *publishes*, and for a leaf
enclosure that is the leaf's own document, never the master's ``task.json`` it happens to sit
under (F05). :func:`leaf_task` resolves it through the owner that already knows how -- the
parent row's exact JSON-primary child (:func:`resolve_leaf_task_binding`) -- with
:func:`resolve_terminal_leaf_doc` behind it for a leaf whose document still declares its
enclosure but whose parent row cannot be read. A series contract keeps publishing the master
document, because there the master *is* the task.

The direct-landing route is the one place where those two facts meet: it runs on the *series*
contract (that is where the code branch is) but lands one named leaf, and it carries that leaf
as the immutable ``DirectLandingOperationInput.leafTaskDocumentRef``. Its notice therefore names
the leaf through :func:`leaf_task_for_reference`, which reads that exact document from the
coordination root the route ran in; the series contract is only the enclosure that publishes it,
so ``leaf_task(series)`` -- the master document -- is never its subject.

Every builder writes the native operation identity it has: the commit, the operation key, its
numeric generation (for the direct route), the repository and the enclosure root the operation ran
in. None of them is optional at the call site, because a recovery pass can only refuse a wrong
commit, generation, repository or root when the intent says which one it meant.

Nothing here decides whether a publication happened: the caller begins an intent, publishes, and
confirms; :mod:`agents_remember.worktrees.terminal_notice_recovery` settles an interrupted intent
against the durable native record it names. A notice write failure is logged and never turns a
completed publication into a failure -- and neither does an unresolvable leaf: a notice for a task
the queue cannot name is not written at all, rather than attributed to the master.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from agents_remember.controlplane.attention_notices import (
    PendingTerminalNotice,
    begin_terminal_notice,
    confirm_terminal_notice,
    discard_terminal_notice,
    generation_operation_key,
    occurrence_at,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.worktrees.task_leaf_binding import (
    TaskLeafBindingError,
    resolve_leaf_task_binding,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

logger = logging.getLogger(__name__)

NO_OCCURRENCE = ""
"""What a builder returns when it wrote no intent: no occurrence exists to confirm or discard.

An enclosure whose own leaf document cannot be resolved names no task the queue may report, and
the master is not a substitute for it. The caller's confirm/discard of this value is a no-op, and
the loss is logged rather than papered over with a true-looking notice about the wrong task.
"""


class LeafTaskIdentityError(ValueError):
    """A leaf enclosure's own task document cannot be resolved, and the master will not stand in."""


@dataclass(frozen=True)
class LeafTask:
    """The task document one enclosure publishes, and the enclosure it is filed under.

    ``enclosure_path`` is the contract this module was handed: a notice never guesses where its
    own enclosure lives, so the durable leaf document and the publishing contract can always be
    compared by identity. It is ``None`` for a document that declares no enclosure of its own --
    the direct route publishes a leaf through the series contract, so its leaf document need not
    declare one.

    ``title`` is the leaf's own title when it was read from its document, so a notice names the
    leaf rather than borrowing the publishing contract's (master) task name.
    """

    reference: TaskDocumentRef
    leaf_id: str
    enclosure_path: Path | None
    title: str = ""


def leaf_task(contract: WorktreeContract) -> LeafTask | None:
    """The task document this contract publishes, or ``None`` when a leaf's own is unresolvable.

    A ``series`` contract publishes the master document itself -- the series semantics the queue
    has always named -- so that case is answered from the contract as it stands. A leaf enclosure
    publishes its own leaf document. Both owners below are the existing canonical ones; nothing
    here parses a path out of a title, a slug or a directory listing.
    """

    if contract.kind == "series":
        return LeafTask(
            reference=TaskDocumentRef(
                repository=contract.repo_name,
                path=_repository_relative(contract, contract.task_artifact.with_suffix(".json")),
            ),
            leaf_id=contract.leaf_id,
            enclosure_path=contract.contract_path,
            title=contract.task_name,
        )
    resolved = _bound_leaf(contract) or _declared_leaf(contract)
    if resolved is None:
        logger.warning(
            "terminal notice: leaf %r in %s names no resolvable task document; "
            "the master document %s will not stand in for it",
            contract.leaf_id,
            contract.contract_path,
            contract.task_artifact,
        )
    return resolved


def leaf_task_for_reference(
    coordination_root: Path,
    reference: TaskDocumentRef,
    *,
    enclosure_path: Path | None = None,
) -> LeafTask | None:
    """The leaf one notice names, read back from its own durable task document.

    This is the read-back side of :func:`leaf_task` and the reason a recovery pass still works
    after the enclosure root is gone: the leaf document outlives every enclosure generation and
    declares the enclosure address of the generation it belongs to. ``None`` means the document
    is absent or unreadable, which is an unknown observation and never a refutation.

    ``enclosure_path`` names the contract that publishes this leaf when the caller already knows
    it -- the direct route hands its series contract, because that is the enclosure whose journal
    the notice is about. Without it the document's own declared enclosure is used, and a document
    that declares none yields a subject with no enclosure address rather than no subject at all.
    """

    topology = TaskDocumentTopology(coordination_root)
    try:
        document = read_task_doc(topology.path_for_ref(reference))
    except (OSError, ValueError, TaskDocumentRefError):
        return None
    declared = document.enclosures[-1] if document.enclosures else None
    path = (
        enclosure_path
        if enclosure_path is not None
        else (Path(declared.enclosurePath) if declared is not None else None)
    )
    return LeafTask(
        reference=reference,
        leaf_id=document.id.strip() or (declared.leafId.strip() if declared is not None else ""),
        enclosure_path=path,
        title=document.title.strip(),
    )


def task_reference(contract: WorktreeContract) -> TaskDocumentRef:
    """The canonical task document this contract publishes, as the queue names it."""

    subject = leaf_task(contract)
    if subject is None:
        raise LeafTaskIdentityError(
            f"leaf {contract.leaf_id!r} in {contract.task_root} names no resolvable task "
            f"document; the master document is not a substitute (contract {contract.contract_path})"
        )
    return subject.reference


def landed_occurrence(contract: WorktreeContract, code_commit: str) -> str:
    """One final landing of one task: the task plus the commit that landed.

    An exact retry of the same landing publishes the same occurrence, so a recovery retry can
    neither lose the notice nor double it.
    """

    return landed_occurrence_at(task_reference(contract), code_commit)


def landed_occurrence_at(reference: TaskDocumentRef, code_commit: str) -> str:
    """One final landing of one already-resolved task document."""

    return f"task-landed:{reference.key}:{code_commit}"


def abandoned_occurrence(contract: WorktreeContract) -> str:
    """One abandonment of one task; a second attempt refuses natively, so the id is the task."""

    return abandoned_occurrence_at(task_reference(contract))


def abandoned_occurrence_at(reference: TaskDocumentRef) -> str:
    """One abandonment of one already-resolved task document."""

    return f"task-abandoned:{reference.key}"


def direct_occurrence_at(reference: TaskDocumentRef, code_commit: str, operation_key: str) -> str:
    """One completed direct-landing generation of one already-resolved task document."""

    return f"{landed_occurrence_at(reference, code_commit)}:{operation_key}"


def begin_landed_notice(contract: WorktreeContract, *, code_commit: str, strategy: str) -> str:
    """Record the intent to publish a landed notice, and name its occurrence."""

    subject = leaf_task(contract)
    if subject is None:
        return NO_OCCURRENCE
    occurrence = landed_occurrence_at(subject.reference, code_commit)
    _begin(
        contract,
        PendingTerminalNotice(
            occurrenceId=occurrence,
            kind="task-landed",
            taskKey=subject.reference.key,
            taskReference=subject.reference,
            taskTitle=subject.title or contract.task_name,
            detail=_detail(f"landed {code_commit[:12]}", strategy),
            at=occurrence_at(),
            commit=code_commit,
            repository=subject.reference.repository,
            root=contract.worktree_group.as_posix(),
            contractPath=contract.contract_path.as_posix(),
        ),
    )
    return occurrence


def begin_abandoned_notice(contract: WorktreeContract) -> str:
    """Record the intent to publish an abandonment notice, and name its occurrence.

    An abandonment names no commit and no operation generation, so this intent carries the
    identity its own native record has -- the repository and the enclosure root -- and nothing
    invented to fill the others.
    """

    subject = leaf_task(contract)
    if subject is None:
        return NO_OCCURRENCE
    occurrence = abandoned_occurrence_at(subject.reference)
    _begin(
        contract,
        PendingTerminalNotice(
            occurrenceId=occurrence,
            kind="task-abandoned",
            taskKey=subject.reference.key,
            taskReference=subject.reference,
            taskTitle=subject.title or contract.task_name,
            detail="abandoned; the enclosure keeps its record",
            at=occurrence_at(),
            repository=subject.reference.repository,
            root=contract.worktree_group.as_posix(),
            contractPath=contract.contract_path.as_posix(),
        ),
    )
    return occurrence


def begin_direct_landed_notice(
    contract: WorktreeContract,
    *,
    leaf_reference: TaskDocumentRef | None,
    code_commit: str,
    operation_key: str,
    generation: int,
) -> str:
    """Record the intent for one completed direct-landing generation (rule 5 and F05).

    The subject is the leaf the route was admitted for -- the immutable accepted
    ``leafTaskDocumentRef`` -- read from its own task document, never the series master the
    contract happens to sit under. The occurrence is that leaf plus the exact generation's
    operation identity, so a retried or recovered generation confirms the same notice and a
    distinct generation gets its own.

    ``leaf_reference`` is ``None`` only when the accepted input could not be read at all; that is
    an unnameable task, and it writes no notice rather than a true-looking one about the master.
    """

    subject = _direct_leaf(contract, leaf_reference)
    if subject is None:
        return NO_OCCURRENCE
    exact_key, recorded = _exact_generation(operation_key, generation)
    occurrence = direct_occurrence_at(subject.reference, code_commit, exact_key)
    _begin(
        contract,
        PendingTerminalNotice(
            occurrenceId=occurrence,
            kind="task-landed",
            taskKey=subject.reference.key,
            taskReference=subject.reference,
            taskTitle=subject.title or contract.task_name,
            detail=_detail(f"direct landing {code_commit[:12]}", exact_key[:16]),
            at=occurrence_at(),
            commit=code_commit,
            operationKey=exact_key,
            operationGeneration=recorded,
            repository=subject.reference.repository,
            root=contract.worktree_group.as_posix(),
            contractPath=contract.contract_path.as_posix(),
        ),
    )
    return occurrence


def _exact_generation(operation_key: str, generation: int) -> tuple[str, int | None]:
    """The stored operation identity and its generation, for a record that may carry neither.

    A record with generation ``0`` -- an integration this process ran without a lifecycle
    generation, or a journal from before generations were tracked -- names an operation and no run
    of it, so the identity is the bare key and the recorded generation is ``None`` rather than a
    fabricated ``0``. Anything at or above the first generation is written into the key, because a
    key alone cannot separate two runs of one operation.
    """

    if generation < 1:
        return operation_key, None
    return generation_operation_key(operation_key, generation), generation


def confirm_notice(contract: WorktreeContract, occurrence: str) -> None:
    """Publish the occurrence the native record now proves."""

    with suppress(OSError):  # the store already logs and swallows its own write
        confirm_terminal_notice(contract.coordination_root, occurrence)


def discard_notice(contract: WorktreeContract, occurrence: str) -> None:
    """Forget an intent whose native publication did not happen."""

    with suppress(OSError):  # the store already logs and swallows its own write
        discard_terminal_notice(contract.coordination_root, occurrence)


def _direct_leaf(contract: WorktreeContract, reference: TaskDocumentRef | None) -> LeafTask | None:
    """The leaf a direct-landing generation names, read from its own durable document.

    The series contract publishes this leaf, so it supplies the enclosure address; the document
    supplies the identity the queue opens: its repository-relative path, its id and its title.
    A reference from another repository is not this contract's leaf, and an unreadable document
    names nothing -- in both cases no notice is written, and the master document is not a
    fallback.
    """

    if reference is None:
        logger.warning(
            "terminal notice: the direct landing on %s names no leaf task document; "
            "no notice is written rather than one about the master %s",
            contract.contract_path,
            contract.task_artifact,
        )
        return None
    if reference.repository != contract.repo_name:
        logger.warning(
            "terminal notice: the direct landing on %s names %s:%s in another repository; "
            "no notice is written",
            contract.contract_path,
            reference.repository,
            reference.path,
        )
        return None
    subject = leaf_task_for_reference(
        contract.coordination_root, reference, enclosure_path=contract.contract_path
    )
    if subject is None:
        logger.warning(
            "terminal notice: the direct landing on %s names %s:%s, which is absent or "
            "unreadable; no notice is written",
            contract.contract_path,
            reference.repository,
            reference.path,
        )
    return subject


def _bound_leaf(contract: WorktreeContract) -> LeafTask | None:
    """The leaf this enclosure's parent row names as its exact JSON-primary child."""

    try:
        binding = resolve_leaf_task_binding(
            contract.coordination_root,
            contract.repo_name,
            contract.task_root,
            contract.leaf_id,
            task_name=contract.task_name,
        )
    except (TaskLeafBindingError, OSError):
        return None
    return LeafTask(
        reference=binding.task_ref,
        leaf_id=binding.row.number,
        enclosure_path=contract.contract_path,
        title=binding.row.name,
    )


def _declared_leaf(contract: WorktreeContract) -> LeafTask | None:
    """The leaf document that declares this leaf, for an enclosure whose parent row is gone."""

    try:
        found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    except (TerminalLeafResolutionError, OSError):
        return None
    if found is None:
        return None
    path, document = found
    try:
        reference = TaskDocumentTopology(contract.coordination_root).canonical_ref(
            contract.repo_name, path
        )
    except (TaskDocumentRefError, OSError):
        return None
    return LeafTask(
        reference=reference,
        leaf_id=document.id.strip() or contract.leaf_id,
        enclosure_path=contract.contract_path,
        title=document.title.strip(),
    )


def _repository_relative(contract: WorktreeContract, path: Path) -> str:
    repository_root = (contract.coordination_root / "tasks" / contract.repo_name).resolve(
        strict=False
    )
    return path.resolve(strict=False).relative_to(repository_root).as_posix()


def _begin(contract: WorktreeContract, notice: PendingTerminalNotice) -> None:
    with suppress(OSError):  # the store already logs and swallows its own write
        begin_terminal_notice(contract.coordination_root, notice)


def _detail(cause: str, strategy: str) -> str:
    return f"{cause} via {strategy}" if strategy else cause
