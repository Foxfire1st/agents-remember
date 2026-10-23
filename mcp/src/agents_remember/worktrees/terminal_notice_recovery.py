"""Settle an interrupted terminal notice from its exact native operation result (MIK-R200 rule 5).

Exactly one thing decides whether an interrupted notice is real: the durable record the *publishing
route itself* left, read for the exact operation the intent names. This module is that reader. It
answers one question per pending intent -- published, refuted, or undecided -- and it never answers
from lifecycle prose, from an authored task status, or from a generic "this cell says completed"
observation:

* a final landing's own record is the integration cell the landing route wrote
  (:func:`agents_remember.worktrees.modules.landing_record.record_landed_integration`): its status,
  its code commit and its strategy;
* a completed direct-landing generation's own record is its operation journal
  (:class:`~agents_remember.models.lifecycles.operation.LifecycleOperationRecord`): status,
  ``operationKey`` with its numeric generation, and ``result.codeCommit``. The integration cell is
  **not** evidence for this route. A direct landing publishes no landing cell for its own
  generation, so a completed cell on the same enclosure belongs to some other publication; it may
  neither prove this generation nor refute it, and on its own it leaves the intent undecided. The
  journal's verdict always wins -- a cancelled or still-running generation is not overridden by a
  cell that happens to look finished;
* an abandonment's own record is its native successful-result receipt: for an archived
  enclosure it is bound to that exact terminal archive generation; for an admitted no-remains
  enclosure it is the typed receipt beside the retained canonical contract, bound to that
  contract, lifecycle identity and final native hash. The live ``integration.cleanup: abandoned``
  cell may still roll back, and the archive itself precedes the guard's success (L190-R1-F06).
  Neither a cell, a bare archive nor the no-remains admission predicate proves or refutes success
  alone: the intent stays undecided until its exact native receipt commits the outcome.

The three verdicts are the only three honest answers, and the asymmetry between them is the point:

``PUBLISHED``
    one of those records names this exact occurrence -- same task document, same commit or
    generation -- and the record belongs to this repository and this coordination root.

``REFUTED``
    a record of the same subject proves this occurrence did *not* happen: it names a different
    commit or generation, it belongs to another repository or root, the generation was cancelled
    before publication, or the enclosure was collected without ever recording this publication.

``UNDECIDED``
    nothing readable settles it: the record cannot be read, the operation is still active or in
    flight, the enclosure is still live and may yet publish (including a rollbackable abandonment
    cell), or the leaf document that names the enclosure is gone. An undecidable intent is kept for
    a later pass, never erased: erasing it is how a successful occurrence is lost.

Everything here is read-only and bounded: one leaf document, one contract (or its locator and
terminal archive), and at most :data:`MAX_DIRECT_RECORDS` generation journals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ValidationError

from agents_remember.controlplane.attention_notices import (
    PendingTerminalNotice,
    split_generation_operation_key,
)
from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    TerminalEnclosureArchive,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.attention_terminal_notices import (
    LeafTask,
    leaf_task,
    leaf_task_for_reference,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    validate_terminal_proof,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    lifecycle_operation_locator_path,
)
from agents_remember.worktrees.integration.terminal_abandon_commit import (
    no_remains_abandon_committed,
    terminal_abandon_commit_record,
)
from agents_remember.worktrees.integration.terminal_enclosure_evidence import _sha256
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    parse_contract_text,
)

logger = logging.getLogger(__name__)

MAX_DIRECT_RECORDS = 8
"""Generation journals one answer may read; the current record plus its kept predecessors."""

DIRECT_LANDING_RECORD = "direct-landing-operation.json"
"""The canonical journal name the direct-landing route advances for its current generation."""


class _UnreadableRecord(Exception):
    """A durable record this pass cannot read, so the honest answer stays undecided."""

    def __init__(self, record: str) -> None:
        self.record = record
        super().__init__(record)


class _ForeignRecord(Exception):
    """A durable record that belongs to another subject, so the intent is refuted."""

    def __init__(self, reason: str, record: str) -> None:
        self.reason = reason
        self.record = record
        super().__init__(reason)


class NoticeVerdict(StrEnum):
    """What the durable native records say about one interrupted publication intent."""

    PUBLISHED = "published"
    REFUTED = "refuted"
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class NoticeSettlement:
    """One intent's verdict, the record that decided it, and why in one line."""

    verdict: NoticeVerdict
    reason: str
    record: str

    @property
    def proved(self) -> bool | None:
        """The same answer in the shape ``settle_terminal_notices`` takes."""

        if self.verdict is NoticeVerdict.PUBLISHED:
            return True
        if self.verdict is NoticeVerdict.REFUTED:
            return False
        return None


def resolve_pending_notice(
    coordination_root: Path,
    notice: PendingTerminalNotice,
    *,
    contract: WorktreeContract | None = None,
) -> NoticeSettlement:
    """Settle one interrupted intent against the native records of the operation it names.

    ``contract`` is the live enclosure contract the caller already read, when it has one; it is
    only used if it publishes the very task document the intent names. Without it -- and after the
    enclosure root is gone, which is the same case -- the subject is read back from the durable
    leaf task document, which outlives every enclosure generation.

    The intent's own identity is checked first: an intent that names another repository, or an
    enclosure root that is not this coordination root's, is never this root's publication.
    """

    subject = _subject(coordination_root, notice, contract)
    if subject is None:
        return _undecided(
            "the leaf task document this intent names is absent or unreadable",
            "leaf task document",
        )
    occurrence = _occurrence(notice)
    if occurrence is None:
        return _undecided(
            "this intent's occurrence does not name its own task and publication",
            "notice intent",
        )
    foreign = _foreign_identity(coordination_root, notice, subject)
    if foreign is not None:
        return _refuted(foreign, "notice intent")
    if notice.kind != "task-abandoned" and occurrence.operation_key:
        return _direct(coordination_root, notice, subject, occurrence)
    records = _records(coordination_root, subject)
    if (
        notice.root is not None
        and records.worktree_group
        and not _same_root(Path(notice.root), Path(records.worktree_group))
    ):
        records = replace(
            records, refuted="the native publication belongs to another enclosure root"
        )
    if notice.kind == "task-abandoned":
        return _abandoned(records)
    return _landed(records, occurrence)


def verify_pending_notice(
    coordination_root: Path,
    notice: PendingTerminalNotice,
    *,
    contract: WorktreeContract | None = None,
) -> bool | None:
    """The verdict in the ``bool | None`` shape ``settle_terminal_notices`` asks for."""

    return resolve_pending_notice(coordination_root, notice, contract=contract).proved


# --- the subject -------------------------------------------------------------


def _subject(
    coordination_root: Path,
    notice: PendingTerminalNotice,
    contract: WorktreeContract | None,
) -> LeafTask | None:
    """The leaf this intent names: the passed contract when it is that leaf, else the leaf doc."""

    if contract is not None:
        published = leaf_task(contract)
        if published is not None and published.reference == notice.taskReference:
            return published
    return leaf_task_for_reference(
        coordination_root,
        notice.taskReference,
        enclosure_path=Path(notice.contractPath) if notice.contractPath is not None else None,
    )


def _foreign_identity(
    coordination_root: Path,
    notice: PendingTerminalNotice,
    subject: LeafTask,
) -> str | None:
    """Why this intent is not this root's publication, or ``None`` when it names no foreign one.

    Only fields the intent actually carries are checked: a row written before the identity existed
    proves nothing either way, and its verdict still comes from the records below.
    """

    if notice.repository is not None and notice.repository != subject.reference.repository:
        return "the intent names another repository than the task document it settles against"
    if notice.root is not None and not _root_confined(
        coordination_root, subject.reference.repository, notice.root
    ):
        return "the intent names an enclosure root outside this coordination root"
    return None


def _root_confined(coordination_root: Path, repository: str, root: str) -> bool:
    """Whether a recorded enclosure root is one of this coordination root's worktrees."""

    worktrees = (Path(coordination_root) / "worktrees" / repository).resolve(strict=False)
    try:
        recorded = Path(root).resolve(strict=False)
    except OSError:
        return False
    return recorded.is_relative_to(worktrees)


# --- the intent's own occurrence ---------------------------------------------


@dataclass(frozen=True)
class _Occurrence:
    """What the intent says was published: the commit, and the generation when it names one."""

    code_commit: str
    operation_key: str
    generation: int | None = None


def _occurrence(notice: PendingTerminalNotice) -> _Occurrence | None:
    """Read the identity the publishing route wrote, or ``None`` when it names no publication.

    The structured fields are the identity a current route writes: the commit it published and the
    operation key with its numeric generation. An older row carries only the occurrence id, so the
    same identity is recovered by parsing it -- and a row that carries both must not disagree with
    itself, because a notice whose own fields contradict its occurrence is not evidence about any
    operation.
    """

    if notice.kind == "task-abandoned":
        expected = f"task-abandoned:{notice.taskKey}"
        return _Occurrence("", "") if notice.occurrenceId == expected else None
    prefix = f"task-landed:{notice.taskKey}:"
    if not notice.occurrenceId.startswith(prefix):
        return None
    code_commit, _, operation_key = notice.occurrenceId[len(prefix) :].partition(":")
    if notice.operationKey is not None:
        operation_key = notice.operationKey
        if notice.commit is not None and notice.commit != code_commit:
            return None
        return _Occurrence(code_commit, operation_key, notice.operationGeneration)
    return _Occurrence(notice.commit or code_commit, operation_key)


# --- the durable native records ----------------------------------------------


class _NotArchived(Exception):
    """This enclosure has no terminal archive, so its records are still the live bytes."""


@dataclass(frozen=True)
class _NativeRecords:
    """Everything readable about one enclosure's native publications, and nothing inferred."""

    landing_status: str = ""
    landing_commit: str = ""
    landing_strategy: str = ""
    abandoned: bool = False
    terminal: bool = False
    committed: bool = False
    direct: tuple[LifecycleOperationRecord, ...] = ()
    direct_unreadable: bool = False
    retired: bool = False
    unreadable: bool = False
    refuted: str | None = None
    record: str = ""
    worktree_group: str = ""


def _records(coordination_root: Path, subject: LeafTask) -> _NativeRecords:
    """One enclosure's native records: its committed terminal archive, else its live bytes.

    The archive preserves this generation even when its live root is gone. Its contract cells
    precede deletion; only its native outcome receipt can prove a committed abandonment.
    """

    archived = _archived_records(coordination_root, subject)
    return archived if archived is not None else _live_records(coordination_root, subject)


def _live_records(coordination_root: Path, subject: LeafTask) -> _NativeRecords:
    """The live enclosure contract, when the enclosure root has not been archived yet."""

    if subject.enclosure_path is None:
        return _NativeRecords(unreadable=True, record="enclosure address")
    path = subject.enclosure_path
    text = _read_text(path)
    if text is None:
        return _NativeRecords(unreadable=True, record="enclosure contract")
    contract = _parse(text, path)
    if contract is None:
        return _NativeRecords(unreadable=True, record="enclosure contract")
    if not _identity_holds(coordination_root, subject.reference, contract):
        return _NativeRecords(
            refuted="the enclosure at this address belongs to another repository or root",
            record="enclosure contract",
        )
    records = _records_of(contract, record="enclosure contract")
    if contract.cleanup == "abandoned" and no_remains_abandon_committed(contract):
        return replace(records, committed=True, record="no-remains abandonment outcome")
    return records


def _records_of(contract: WorktreeContract, *, record: str) -> _NativeRecords:
    """One contract's published cells, whether the bytes were live or archived."""

    journals = _journals(contract.worktree_group)
    return _NativeRecords(
        landing_status=contract.integration_status,
        landing_commit=contract.integrated_code_commit,
        landing_strategy=contract.integration_strategy,
        abandoned=contract.cleanup == "abandoned",
        direct=journals.records,
        direct_unreadable=journals.unreadable,
        record=record,
        worktree_group=contract.worktree_group.as_posix(),
    )


def _archived_records(coordination_root: Path, subject: LeafTask) -> _NativeRecords | None:
    """The same records after the enclosure root is collected, read from its terminal archive.

    The locator and archive live in the coordination control plane and outlive the enclosure root,
    so an intent whose enclosure is gone is still settled by the records that route published --
    never by the absence of the directory. ``terminal`` is the archive's own cleanup operation,
    which selects the outcome receipt an abandonment needs. The archive itself is pre-success;
    only the successful-result receipt proves commitment. ``None`` means no archive exists yet.
    """

    try:
        archive = _terminal_archive(coordination_root, subject)
    except _NotArchived:
        return None
    except _UnreadableRecord as unreadable:
        return _NativeRecords(unreadable=True, record=unreadable.record)
    except _ForeignRecord as foreign:
        return _NativeRecords(refuted=foreign.reason, record=foreign.record)
    try:
        contract = _archived_contract(archive, coordination_root, subject)
        direct = _archived_direct_records(archive)
    except _UnreadableRecord as unreadable:
        return _NativeRecords(unreadable=True, record=unreadable.record)
    except _ForeignRecord as foreign:
        return _NativeRecords(refuted=foreign.reason, record=foreign.record)
    records = _records_of(contract, record="terminal archive")
    return replace(
        records,
        direct=direct,
        direct_unreadable=False,
        retired=True,
        terminal=archive.cleanupOperation == "worktree_abandon",
        committed=archive.cleanupOperation == "worktree_abandon"
        and _abandon_committed(archive, contract, coordination_root),
    )


def _abandon_committed(
    archive: TerminalEnclosureArchive,
    archived_contract: WorktreeContract,
    coordination_root: Path,
) -> bool:
    """Whether this archive's exact native receipt committed the abandonment.

    The archive is published before the destructive outputs and the guard, so
    ``archive.contractText`` is the pre-success contract: it authorizes deletion and preserves
    the enclosure, and it says nothing about whether the abandonment then committed. Only the
    receipt written as the native guard's final commit operation states the outcome, and
    it must still name this exact archive generation *and* the exact bytes the committed
    contract would have had. Anything else -- absent, unreadable, another generation, another
    contract -- proves nothing here, which leaves the intent undecided rather than refuted.
    """

    try:
        record = terminal_abandon_commit_record(
            coordination_root, archive.locator.publicationRequestId
        )
    except (OSError, ValueError):
        return False
    if record is None or record.cleanupRequestId != archive.cleanupRequestId:
        return False
    if record.contractPath != archive.contractPath:
        return False
    committed = amend_contract(
        archived_contract, ContractCells(cleanup="abandoned", terminal_note=record.terminalNote)
    )
    expected = contract_publication_text(committed.contract_path, committed)
    return record.committedContractSha256 == _sha256(expected.encode("utf-8"))


def _terminal_archive(coordination_root: Path, subject: LeafTask) -> TerminalEnclosureArchive:
    """The terminal archive of this exact enclosure, or the typed reason it cannot be read."""

    if subject.enclosure_path is None:
        raise _NotArchived
    locator = _locator(coordination_root, subject)
    if locator.state != "terminal-archived" or locator.terminalArchivePath is None:
        raise _NotArchived
    try:
        archive = validate_terminal_proof(coordination_root, subject.enclosure_path, locator)
    except (OSError, RuntimeError, ValueError) as error:
        raise _UnreadableRecord("terminal archive") from error
    if archive is None:
        raise _NotArchived
    return archive


def _locator(coordination_root: Path, subject: LeafTask) -> LifecycleEnclosureLocator:
    """The control-plane locator that still names this enclosure after its root is gone."""

    if subject.enclosure_path is None:
        # No enclosure was ever named for this leaf, so there is nothing to locate or collect.
        raise _NotArchived
    try:
        path = lifecycle_operation_locator_path(coordination_root, subject.enclosure_path)
    except (ValueError, OSError) as error:
        raise _UnreadableRecord("enclosure locator") from error
    if not path.exists():
        raise _NotArchived  # this enclosure has not been collected, so it has no archive
    locator = _required_model(LifecycleEnclosureLocator, path, "enclosure locator")
    if not _locator_identity_holds(coordination_root, subject.reference, locator):
        raise _ForeignRecord(
            "the enclosure locator belongs to another repository or root", "enclosure locator"
        )
    return locator


def _archived_contract(
    archive: TerminalEnclosureArchive,
    coordination_root: Path,
    subject: LeafTask,
) -> WorktreeContract:
    """The archived contract bytes, proved to be this enclosure's and this leaf's."""

    if subject.enclosure_path is None:
        raise _UnreadableRecord("enclosure address")
    if archive.contractPath != subject.enclosure_path.as_posix():
        raise _ForeignRecord(
            "the terminal archive belongs to another enclosure", "terminal archive"
        )
    contract = _parse(archive.contractText, subject.enclosure_path)
    if contract is None:
        raise _UnreadableRecord("terminal archive")
    if not _identity_holds(coordination_root, subject.reference, contract):
        raise _ForeignRecord(
            "the archived enclosure belongs to another repository or root", "terminal archive"
        )
    return contract


@dataclass(frozen=True)
class _DirectJournals:
    records: tuple[LifecycleOperationRecord, ...]
    unreadable: bool


def _journals(root: Path) -> _DirectJournals:
    """Read bounded generations without mistaking unreadable siblings for absent owners."""

    directory = Path(root) / ".lifecycle"
    paths = sorted(directory.glob("direct-landing-operation*.json"))[:MAX_DIRECT_RECORDS]
    records: list[LifecycleOperationRecord] = []
    unreadable = False
    for path in paths:
        text = _read_text(path)
        record = None if text is None else _model(LifecycleOperationRecord, text)
        if record is not None:
            records.append(record)
        else:
            unreadable = True
    return _DirectJournals(tuple(records), unreadable)


def _archived_direct_records(
    archive: TerminalEnclosureArchive,
) -> tuple[LifecycleOperationRecord, ...]:
    """The direct-landing generations a terminal archive preserved, bounded and typed."""

    records: list[LifecycleOperationRecord] = []
    for entry in archive.canonicalEntries[:MAX_DIRECT_RECORDS]:
        if not entry.relativePath.startswith("direct-landing-operation"):
            continue
        record = _model(LifecycleOperationRecord, entry.content)
        if record is not None:
            records.append(record)
    return tuple(records)


# --- the two terminal facts --------------------------------------------------


def _landed(records: _NativeRecords, occurrence: _Occurrence) -> NoticeSettlement:
    """An ordinary landing is proved by its own commit on its own enclosure, never by "completed".

    Only the integration cell speaks for this route: it is what
    :func:`~agents_remember.worktrees.modules.landing_record.record_landed_integration` writes, and
    the cell must name this commit. Nothing else on the enclosure -- a direct-landing generation's
    journal in particular -- is evidence about an ordinary landing.
    """

    if records.unreadable:
        return _undecided("a durable record of this publication cannot be read", records.record)
    if records.refuted is not None:
        return _refuted(records.refuted, records.record)
    candidate = _cell_candidate(records, occurrence)
    if candidate is not None and candidate.verdict is not NoticeVerdict.UNDECIDED:
        return candidate
    if records.retired:
        return _refuted(
            "the enclosure was collected without ever recording this landing",
            records.record,
        )
    if candidate is not None:
        return candidate
    return _undecided(
        "no durable record of this landing exists yet",
        records.record or "enclosure contract",
    )


def _direct(
    coordination_root: Path,
    notice: PendingTerminalNotice,
    subject: LeafTask,
    occurrence: _Occurrence,
) -> NoticeSettlement:
    """A direct-landing occurrence is settled by its own generation journal and by nothing else.

    The journal is read from the enclosure root the intent itself names -- the worktree group the
    generation ran in, which the notice store already confined to this coordination root -- or, for
    a row written before that field existed, from the subject's live enclosure or its terminal
    archive. A completed integration cell is never consulted: it belongs to another publication of
    the same enclosure, so it can neither prove this generation nor override what the journal says
    about it.
    """

    records = _direct_records(coordination_root, notice, subject)
    if records.unreadable or records.direct_unreadable:
        return _undecided(
            "the enclosure's direct-landing journal cannot be read",
            "direct-landing journal" if records.direct_unreadable else records.record,
        )
    if records.refuted is not None:
        return _refuted(records.refuted, records.record)
    if not records.direct:
        if records.retired:
            return _refuted(
                "the enclosure was collected without recording this generation",
                records.record,
            )
        return _undecided(
            "the enclosure holds no durable record of this generation yet",
            records.record,
        )
    owner = next(
        (record for record in records.direct if _is_generation(record, occurrence)),
        None,
    )
    if owner is not None:
        return _generation_verdict(owner, occurrence.code_commit)
    return _refuted(
        "the enclosure's direct-landing journal holds a different generation",
        "direct-landing journal",
    )


def _direct_records(
    coordination_root: Path,
    notice: PendingTerminalNotice,
    subject: LeafTask,
) -> _NativeRecords:
    """The generation journals this intent can be settled against, and how they were read.

    A root-bearing intent names the worktree group its generation ran in, and that
    directory -- with the journals inside it -- is deleted when the enclosure is collected. The
    journals on disk are the live answer while they exist; once they are gone, the same
    archive-aware read an abandonment uses has to answer, because the archive preserved exact
    copies of them. The archive only answers for the root the intent itself names, so a journal
    set preserved from some other enclosure can never settle this generation.
    """

    if notice.contractPath is not None:
        subject = replace(subject, enclosure_path=Path(notice.contractPath))
    if notice.root is not None:
        root = Path(notice.root)
        journals = _journals(root)
        if journals.records or journals.unreadable:
            return _NativeRecords(
                direct=journals.records,
                direct_unreadable=journals.unreadable,
                record="direct-landing journal",
            )
        archived = _archived_records(coordination_root, subject)
        if archived is not None and _archive_covers(archived, root):
            return archived
        return _NativeRecords(direct=(), record="direct-landing journal")
    return _records(coordination_root, subject)


def _archive_covers(records: _NativeRecords, root: Path) -> bool:
    """Whether these archived records belong to the exact worktree group an intent names."""

    if records.unreadable or records.refuted is not None:
        return True
    if not records.worktree_group:
        return False
    return _same_root(Path(records.worktree_group), root)


def _is_generation(record: LifecycleOperationRecord, occurrence: _Occurrence) -> bool:
    """Whether this journal record is the exact generation the intent names."""

    key, generation = split_generation_operation_key(occurrence.operation_key)
    if record.operationKey != key:
        return False
    if generation is None:
        return True
    return record.generation == generation


def _generation_verdict(record: LifecycleOperationRecord, code_commit: str) -> NoticeSettlement:
    """One generation's own status, against the commit the intent says it published."""

    if record.status == "completed":
        landed = _record_commit(record)
        if code_commit and landed and landed != code_commit:
            return _refuted(
                "the generation completed at a different commit",
                "direct-landing journal",
            )
        if landed or not code_commit:
            return _published(
                "the direct-landing journal completed this generation",
                "direct-landing journal",
            )
        return _undecided(
            "the generation completed without naming a commit",
            "direct-landing journal",
        )
    if record.status == "cancelled":
        return _refuted(
            "the generation was cancelled before it published",
            "direct-landing journal",
        )
    return _undecided(
        f"the generation is {record.status}, not completed",
        "direct-landing journal",
    )


def _cell_candidate(records: _NativeRecords, occurrence: _Occurrence) -> NoticeSettlement | None:
    """What the integration cell the landing route wrote says about this commit."""

    if not records.landing_status and not records.landing_commit:
        return None
    if records.landing_status != "completed":
        return _undecided(
            f"the integration cell is {records.landing_status or 'absent'}, not completed",
            "integration cell",
        )
    if not occurrence.code_commit:
        return _undecided("the intent names no commit to compare", "integration cell")
    if records.landing_commit != occurrence.code_commit:
        return _refuted(
            "the final landing recorded a different commit",
            "integration cell",
        )
    return _published("the integration cell records this commit", "integration cell")


def _abandoned(records: _NativeRecords) -> NoticeSettlement:
    """An abandonment is proved by its exact native outcome receipt, and nothing authored.

    ``cleanup: abandoned`` in a live contract is a cell the terminal guard publishes and may still
    roll back (``rollback_publish`` restores the contract). Reading it as proof would announce an
    abandonment that then never happened, and the route's own discard could not take the notice
    back out of the queue -- so a live cell is undecided: the publication may still be committing,
    or it may be about to be rolled back. Proof requires the archive-owned native receipt or the
    explicitly typed no-remains receipt bound to its retained contract and lifecycle.
    """

    if records.unreadable:
        return _undecided("a durable record of this publication cannot be read", records.record)
    if records.refuted is not None:
        return _refuted(records.refuted, records.record)
    return _abandon_verdict(records)


def _abandon_verdict(records: _NativeRecords) -> NoticeSettlement:
    """What this enclosure's durable abandonment record says, once it is known to be readable."""

    if records.terminal:
        if records.committed:
            return _published(
                "the terminal archive's native outcome receipt commits the abandonment",
                "terminal archive",
            )
        # This archive was published before the guard ran, so its contract is the
        # pre-success one. Its silence about the abandonment is not a refutation: the commit may
        # be landing right now, or a restart may have interrupted it after it committed. Only
        # the native outcome receipt proves commitment; its absence leaves the intent open.
        return _undecided(
            "the terminal archive states no committed outcome yet; its contract predates the guard",
            "terminal archive",
        )
    if records.retired:
        return _refused_collection(records)
    if records.committed:
        return _published(
            "the no-remains native outcome receipt commits the abandonment", records.record
        )
    if records.abandoned:
        return _undecided(
            "cleanup: abandoned is a rollbackable cell until the terminal record commits it",
            records.record,
        )
    return _undecided(
        "the enclosure is live and may still be publishing its abandonment",
        records.record,
    )


def _refused_collection(records: _NativeRecords) -> NoticeSettlement:
    """A collected enclosure whose archive records a cleanup rather than an abandonment."""

    return _refuted(
        "the enclosure was collected without ever recording an abandonment",
        records.record,
    )


# --- bounded readers ---------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """One file's text, or ``None`` when it is absent or cannot be read."""

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _model[Model: BaseModel](model: type[Model], text: str) -> Model | None:
    """One durable record, or ``None`` when its bytes are not a valid record of that kind."""

    try:
        return model.model_validate_json(text)
    except (ValidationError, ValueError):
        return None


def _required_text(path: Path, record: str) -> str:
    """One record's bytes, or the typed reason this pass cannot read them."""

    text = _read_text(path)
    if text is None:
        raise _UnreadableRecord(record)
    return text


def _required_model[Model: BaseModel](model: type[Model], path: Path, record: str) -> Model:
    """One durable record that must be readable: its absence is undecidable, not a refutation."""

    value = _model(model, _required_text(path, record))
    if value is None:
        raise _UnreadableRecord(record)
    return value


def _parse(text: str, path: Path) -> WorktreeContract | None:
    """One contract's bytes, without inventing a temporary-file authority."""

    try:
        return parse_contract_text(text, path=path)
    except (ValueError, OSError):
        return None


def _identity_holds(
    coordination_root: Path,
    reference: TaskDocumentRef,
    contract: WorktreeContract,
) -> bool:
    """Whether this contract is the enclosure of that exact leaf in that exact repository/root."""

    return contract.repo_name == reference.repository and _same_root(
        contract.coordination_root, coordination_root
    )


def _locator_identity_holds(
    coordination_root: Path,
    reference: TaskDocumentRef,
    locator: LifecycleEnclosureLocator,
) -> bool:
    """Whether this locator is the same leaf's enclosure in that exact repository/root."""

    worktrees_root = (coordination_root / "worktrees" / reference.repository).resolve(strict=False)
    return locator.repository == reference.repository and Path(locator.worktreeGroup).resolve(
        strict=False
    ).is_relative_to(worktrees_root)


def _same_root(one: Path, other: Path) -> bool:
    try:
        return one.resolve(strict=False) == other.resolve(strict=False)
    except OSError:
        return one == other


def _record_commit(record: LifecycleOperationRecord) -> str:
    """The code commit one generation's result names, or ``""`` when it names none."""

    result = record.result or {}
    value = result.get("codeCommit")
    return value.strip() if isinstance(value, str) else ""


def _published(reason: str, record: str) -> NoticeSettlement:
    return NoticeSettlement(NoticeVerdict.PUBLISHED, reason, record)


def _refuted(reason: str, record: str) -> NoticeSettlement:
    return NoticeSettlement(NoticeVerdict.REFUTED, reason, record)


def _undecided(reason: str, record: str) -> NoticeSettlement:
    return NoticeSettlement(NoticeVerdict.UNDECIDED, reason, record)
