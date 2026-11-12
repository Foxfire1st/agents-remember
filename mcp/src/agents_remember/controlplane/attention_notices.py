"""Terminal notices the attention queue reports once per native occurrence.

A notice is not derived from lifecycle prose (MIK-R200 rules 5 and 6): the operation that
publishes the native fact writes a *pending* row here before it publishes and confirms it after,
so a crash between the two leaves a row that recovery can check against the native record instead
of a notice that either lies or goes missing. The row is keyed by an occurrence identity -- the
canonical task plus the native operation identity -- so an exact retry publishes the same notice
and never a second one.

The store is disposable working state under D1-D4, like the acknowledgement log beside it: it is
bounded, compacted in place, and its loss costs a notification, never a lifecycle fact. It is one
whole-file record rather than an append-only journal on purpose: there is no event history to
keep, and no second authoritative lifecycle journal is admitted (rule 10).

Three read policies follow from that and are not interchangeable:

* **The cut (rule 6).** The store carries one :class:`StoreCut` taken when it is *created*: the
  position in the native terminal-record publication order (landing and abandonment) read inside
  the same lock that creates the store, which is what orders creation against native publication.
  It is not zero, it is not the intent's pre-publication stamp, and it is not a projection
  sequence. It is never retaken on a dashboard, plug-in or service restart -- a restart re-reads
  the same cut, so a pending, unacknowledged notice is published again as the same occurrence
  (that is not backfill). Only a store that is missing or unreadable is created again, with a new
  cut. Nothing here scans task records: there is no periodic historical detector, and there is
  nothing to detect, because an arrival exists only as a row a publishing route wrote.
* **Bounds.** Every bound is enforced over the exact document that is about to be written --
  confirmed rows *and* interrupted intents, measured through the same serialization function the
  writer publishes (``serialize_notice_store``). A cap measured on one representation and applied
  to another is not a cap.
* **Observation.** A missing file, a readable store that holds nothing, and bytes this build
  cannot interpret are three different observations. The third is *unreadable*: it is never read
  as emptiness and never overwritten, because a caller that mistakes it for a complete empty
  observation reclaims the acknowledgements of notices that are still there. A later write moves
  those bytes aside to a ``.torn.<timestamp>`` sibling and creates a fresh store with a new cut.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.task_document_ref import TaskDocumentRef

logger = logging.getLogger(__name__)

ATTENTION_NOTICE_SCHEMA = "ar-attention-notice/v1"
"""The one schema name this store carries; a file without it is not read as a notice store."""

ATTENTION_NOTICE_FILE = "attention-notices.json"
"""The store below the coordination control plane: one bounded file per installation."""

#: Rule 10's named bound: occurrences retained for acknowledgement. Older occurrences fall out
#: first, and an evicted occurrence is reclaimed from the acknowledgement log by the same pass.
MAX_TERMINAL_NOTICES = 64
#: Unconfirmed publication intents kept at once; a publication confirms its own row immediately,
#: so this only ever holds the rows of an interrupted operation.
MAX_PENDING_NOTICES = 16
#: Per-row detail bound. A notice names a commit or an abandonment, never a message body.
MAX_NOTICE_DETAIL = 280
#: Whole-file bound, counted over the exact serialized document this module writes -- the cut,
#: pending intents included, multibyte identities counted as their UTF-8 bytes. The oldest rows
#: are dropped until the document fits.
MAX_NOTICE_STORE_BYTES = 64 * 1024

TERMINAL_KINDS = frozenset({"task-landed", "task-abandoned"})
type TerminalKind = Literal["task-landed", "task-abandoned"]

type NoticeStoreState = Literal["missing", "empty", "present", "unreadable"]
"""What one read of the store actually observed.

``missing`` is first activation (no file yet), ``empty`` is a readable store holding no rows,
``present`` is a readable store holding rows, and ``unreadable`` is bytes this build cannot
interpret. Only the last one is an unknown observation: it supports no publication and ends no
acknowledgement.
"""

ATTENTION_NOTICE_OWNERSHIP = StoreOwnership(
    store="attention-notices",
    writers=("mcp", "dashboard"),
    compaction_owner="dashboard",
    rationale=(
        "The worktree operations publish a notice at their own native publication boundary "
        "(mcp), and the projection pass settles the interrupted ones against the native record "
        "it has already read (dashboard). Both are whole-file read-modify-writes, so the store's "
        "lock is what makes the pair safe; there is no append."
    ),
)

RETIRED_STORE_KEYS = frozenset({"baseline"})
"""Keys this store once wrote and no longer carries.

``baseline`` claimed an activation cut nothing ever performed: no code assigned it, so every
file the previous build wrote carried ``0`` and ``published()`` filtered on a constant. It is
replaced by :class:`StoreCut`, never by a zero default. The reader still *tolerates* the retired
key, because files written by that build carry it under ``extra="forbid"`` -- refusing them would
turn every existing installation's store into an unreadable one.
"""

TORN_STORE_INFIX = ".torn."
"""Where an unreadable store's bytes go, as ``<stem>.torn.<timestamp><suffix>`` beside the store.

Moved, never deleted and never overwritten in place: the bytes are the only remaining record of
the intents they carried, so a human can still read what was lost. The name carries no ``.json``
prefix of the store's own, so no reader of the store's fixed path picks it up.
"""


class StoreCut(BaseModel):
    """The one cut this store takes when it is created: where native publication begins for it.

    ``at`` is the position in the native terminal-record publication order -- the same clock
    (:func:`occurrence_at`) that stamps a landing or an abandonment -- **read inside the lock that
    creates the store**, so creating the store is ordered against native publication. A record
    published after this read carries a later position and gets exactly one notice; anything
    earlier is history this store never sees, because no publishing route wrote an intent into a
    store that did not exist yet.

    ``sequence`` is the first position in this store's own publication order that belongs to it,
    ``1`` for a fresh store: positions are assigned when an occurrence is confirmed, so position 1
    is the first occurrence this store admits, never a number assigned to the intent beforehand.
    A notice is published only when it is at or after *both*, so a store whose cut sits later than
    a retained row does not publish that row. That is the difference between a cut and a default:
    a default of zero admits every earlier record, which is not a cut at all.

    The cut is written once, inside the same locked rewrite that creates the store, and every
    later rewrite preserves it byte for byte (:func:`serialize_notice_store` renders it from the
    store's own field, so nothing re-derives it).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    at: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)

    def admits(self, notice: TerminalNotice) -> bool:
        """Whether this notice is at or after the cut in both publication order and instant."""

        return notice.sequence >= self.sequence and _publication_order(
            notice.at
        ) >= _publication_order(self.at)


GENERATION_SEPARATOR = ":g"
"""How one native operation's generation is written into its stored operation identity."""


def generation_operation_key(operation_key: str, generation: int) -> str:
    """The exact generation of one native operation: its journal key plus its numeric generation.

    A journal key names an *operation*; a generation names one run of it. The notice store is keyed
    by the generation, so both parts belong in the identity it persists: two generations of one
    operation are two native occurrences, and an intent that named only the key could not tell them
    apart.
    """

    if generation < 1:
        raise ValueError("an operation generation starts at 1")
    return f"{operation_key}{GENERATION_SEPARATOR}{generation}"


def split_generation_operation_key(value: str) -> tuple[str, int | None]:
    """The journal key and its numeric generation; ``(value, None)`` when it names no generation.

    Rows written before the generation was part of the identity carry the bare journal key, and a
    reader must keep reading them: ``None`` says "this identity does not name a generation", which
    is a different statement from generation 1.
    """

    key, separator, generation = value.rpartition(GENERATION_SEPARATOR)
    if not separator or not key or not generation.isdigit():
        return value, None
    number = int(generation)
    return (key, number) if number >= 1 else (value, None)


def _retired_keys_dropped(value: Any) -> Any:
    """Drop :data:`RETIRED_STORE_KEYS` from a payload before field validation (read-only shim)."""

    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key not in RETIRED_STORE_KEYS}
    return value


class PendingTerminalNotice(BaseModel):
    """One publication intent written before the native fact and confirmed after it.

    ``taskKey`` and ``taskReference`` are the canonical task identity the queue opens; nothing
    here matches a title. ``occurrenceId`` is the native occurrence identity: an exact retry
    writes the same one, so it can be neither lost nor doubled.

    The operation identity below is the exact native operation that published the fact, when the
    publishing route knows it: the commit, the operation key and its generation, the repository,
    and the enclosure root the operation ran in. A publishing route that has those facts must
    write every one of them -- an identity written partially is not an identity. Every field stays
    optional here so a store written before they existed still reads; a row that carries no
    identity is exactly a row whose settle verdict cannot be proved from identity alone, and the
    caller's ``verify`` returns ``None`` for it rather than guessing.
    """

    model_config = ConfigDict(extra="forbid")

    occurrenceId: str = Field(min_length=1, max_length=512)
    kind: TerminalKind
    taskKey: str = Field(min_length=1, max_length=512)
    taskReference: TaskDocumentRef
    taskTitle: str = Field(min_length=1, max_length=512)
    detail: str | None = Field(default=None, max_length=MAX_NOTICE_DETAIL)
    at: str = Field(min_length=1, max_length=128)
    commit: str | None = Field(default=None, min_length=1, max_length=128)
    operationKey: str | None = Field(default=None, min_length=1, max_length=512)
    operationGeneration: int | None = Field(default=None, ge=0)
    repository: str | None = Field(default=None, min_length=1, max_length=128)
    root: str | None = Field(default=None, min_length=1, max_length=4096)
    contractPath: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _operation_identity_is_coherent(self) -> PendingTerminalNotice:
        """Refuse an identity whose generation contradicts the key it is written beside."""

        if self.operationKey is None:
            if self.operationGeneration is not None:
                raise ValueError(
                    "a notice naming an operation generation must name the operation key it belongs to"
                )
            return self
        _, generation = split_generation_operation_key(self.operationKey)
        if generation is not None and self.operationGeneration != generation:
            raise ValueError(
                "the operation key's generation and the notice's operationGeneration disagree"
            )
        return self


class TerminalNotice(PendingTerminalNotice):
    """A confirmed native occurrence, numbered so a reader can order what it holds."""

    sequence: int = Field(ge=1)


class AttentionNoticeStore(BaseModel):
    """The bounded working set: the creation cut, confirmed occurrences and interrupted intents."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ar-attention-notice/v1"] = Field(
        default=ATTENTION_NOTICE_SCHEMA, alias="schema"
    )
    cut: StoreCut | None = None
    sequence: int = Field(default=0, ge=0)
    notices: list[TerminalNotice] = Field(default_factory=list)
    pending: list[PendingTerminalNotice] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_keys(cls, value: Any) -> Any:
        """Accept a store written by a build that still carried a retired key."""

        return _retired_keys_dropped(value)

    def published(self) -> list[TerminalNotice]:
        """The confirmed occurrences this store publishes, oldest first and bounded.

        A store without a cut publishes nothing: only a created store can say where its own
        publication order begins, and inventing one here would be the zero default this store
        deliberately does not have.
        """

        if self.cut is None:
            return []
        return [
            notice
            for notice in sorted(self.notices, key=lambda row: row.sequence)
            if self.cut.admits(notice)
        ]


@dataclass(frozen=True)
class NoticeStoreRead:
    """One observation of the store: which of the four states was seen, and what was read.

    ``store`` is ``None`` exactly when ``state`` is ``unreadable``. A caller that only wants
    "the rows, if there are any" may use :func:`read_notice_store`; a caller that decides whether
    it may publish, settle, or let an acknowledgement go must look at ``state``.
    """

    state: NoticeStoreState
    store: AttentionNoticeStore | None


def notice_store_path(coordination_root: Path) -> Path:
    return Path(coordination_root) / "controlplane" / ATTENTION_NOTICE_FILE


def serialize_notice_store(store: AttentionNoticeStore) -> str:
    """The one serialization of the store: what the byte bound measures and the writer writes.

    ``by_alias`` is part of the choice, not a detail: the file carries ``"schema"``, so a
    measurement taken without the alias measures a different document (it renders
    ``"schema_version"``) and can pass a store the writer then publishes above the cap.

    A store with no cut is refused rather than rendered: the cut is what says where this store's
    publication order begins, and a document without it is not a store this module writes.
    """

    if store.cut is None:
        raise ValueError("an attention notice store is written only with its creation cut")
    return store.model_dump_json(indent=2, exclude_none=True, by_alias=True) + "\n"


def read_notice_store_state(coordination_root: Path) -> NoticeStoreRead:
    """Observe the store: missing, empty, present or unreadable, never a guess.

    An unreadable store is an unknown observation, not an empty one: its caller publishes no
    notice from it and reclaims no acknowledgement of a notice that may still be there. The
    file's bytes are left exactly as they are here; only an explicit later *write* may move them
    aside (:data:`TORN_STORE_INFIX`) to create the store again.
    """

    path = notice_store_path(coordination_root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return NoticeStoreRead(state="missing", store=AttentionNoticeStore())
    except (OSError, UnicodeDecodeError):
        logger.warning("Attention notice store %s could not be read", path, exc_info=True)
        return NoticeStoreRead(state="unreadable", store=None)
    try:
        store = AttentionNoticeStore.model_validate_json(text)
    except (ValidationError, ValueError, TypeError):
        logger.warning("Attention notice store %s is not a store this build reads", path)
        return NoticeStoreRead(state="unreadable", store=None)
    state: NoticeStoreState = "present" if store.notices or store.pending else "empty"
    if store.cut is None:
        adopted = _adopted_cut(store)
        if adopted is not None:
            store = store.model_copy(update={"cut": adopted})
    return NoticeStoreRead(state=state, store=store)


def read_notice_store(coordination_root: Path) -> AttentionNoticeStore | None:
    """The store as it stands, or ``None`` when it exists and cannot be read.

    A missing file reads as an empty store (first activation); unreadable bytes read as
    ``None``. Use :func:`read_notice_store_state` when the difference has to be acted on.
    """

    return read_notice_store_state(coordination_root).store


def begin_terminal_notice(
    coordination_root: Path,
    notice: PendingTerminalNotice,
) -> None:
    """Take an intent before the native publication; a repeat is the same intent once.

    The intent's own ``at`` is when the route read the intent -- before the publication it names --
    so it is never the store's cut and never the position a notice is published at. When this
    write creates the store, the cut is the publication position read inside this same locked
    rewrite (:func:`_rewrite`), which is the moment the store begins in the native publication
    order. Records that landed or were abandoned before it are history and get no notice from this
    store.
    """

    def change(store: AttentionNoticeStore, _publication_at: str) -> bool:
        if any(notice.occurrenceId == row.occurrenceId for row in store.notices):
            return False
        store.pending = [row for row in store.pending if row.occurrenceId != notice.occurrenceId]
        store.pending.append(notice)
        return True

    _rewrite(coordination_root, change)


def confirm_terminal_notice(coordination_root: Path, occurrence_id: str) -> None:
    """Publish the intent that the native record now proves, once per occurrence.

    The confirmed row takes its ``at`` from the publication position read inside this rewrite,
    because that is when the native publication is known to have happened; the intent's own stamp
    was read before it and says nothing about where the publication sits in the order.
    """

    def change(store: AttentionNoticeStore, publication_at: str) -> bool:
        pending = [row for row in store.pending if row.occurrenceId == occurrence_id]
        if not pending:
            return False
        store.pending = [row for row in store.pending if row.occurrenceId != occurrence_id]
        row = pending[0]
        if any(row.occurrenceId == known.occurrenceId for known in store.notices):
            return True
        store.notices.append(_published_row(store, row, publication_at))
        return True

    _rewrite(coordination_root, change)


def discard_terminal_notice(coordination_root: Path, occurrence_id: str) -> None:
    """Forget an occurrence whose native publication did not survive.

    A confirmed row is retracted by the same act, not beside it: a settle pass may have confirmed
    this intent from a cell that the publishing route then rolled back, and an intent-only discard
    would leave a notice standing for a publication that does not exist. The route that discards is
    the route that published, so it is the authority on its own failure -- for a row it confirmed
    itself and for one an intervening observer confirmed from the same record.
    """

    def change(store: AttentionNoticeStore, _publication_at: str) -> bool:
        kept = [row for row in store.pending if row.occurrenceId != occurrence_id]
        notices = [row for row in store.notices if row.occurrenceId != occurrence_id]
        changed = len(kept) != len(store.pending) or len(notices) != len(store.notices)
        store.pending = kept
        store.notices = notices
        return changed

    _rewrite(coordination_root, change)


def settle_terminal_notices(
    coordination_root: Path,
    verify: Callable[[PendingTerminalNotice], bool | None],
) -> AttentionNoticeStore | None:
    """Settle every interrupted intent against the native record the caller already read.

    ``verify`` answers ``True`` when the native record proves the publication happened, ``False``
    when it proves it did not, and ``None`` when it cannot say (the record is unreadable or gone).
    A proven intent is published; a refuted one is dropped; an undecidable one is left for a later
    pass, so neither a false notice nor a lost true one is admitted.

    ``None`` means the store itself could not be observed (unreadable bytes, or a failed write).
    That is deliberately not "nothing to settle": a caller must not read an unreadable store as a
    complete empty observation, because its tally is what lets acknowledgements be reclaimed.
    """

    def change(store: AttentionNoticeStore, publication_at: str) -> bool:
        if not store.pending:
            return False
        kept: list[PendingTerminalNotice] = []
        changed = False
        for row in store.pending:
            verdict = verify(row)
            if verdict is True:
                if not any(row.occurrenceId == known.occurrenceId for known in store.notices):
                    store.notices.append(_published_row(store, row, publication_at))
                changed = True
            elif verdict is False:
                changed = True
            else:
                kept.append(row)
        store.pending = kept
        return changed

    return _rewrite(coordination_root, change)


def _published_row(
    store: AttentionNoticeStore,
    row: PendingTerminalNotice,
    publication_at: str,
) -> TerminalNotice:
    """One confirmed occurrence, numbered at the position it is published at."""

    store.sequence += 1
    return TerminalNotice(
        sequence=store.sequence,
        **row.model_copy(update={"at": publication_at}).model_dump(),
    )


def _rewrite(
    coordination_root: Path,
    change: Callable[[AttentionNoticeStore, str], bool],
) -> AttentionNoticeStore | None:
    """Apply one whole-file change under the store lock; a refused write keeps old bytes.

    A failure is logged and never raised: a notice write must not turn a completed native
    publication into a failure (MIK-R200 rules 5 and 10).

    The publication position is read *inside* this lock, and every write that creates the store
    takes its cut from that same read (:meth:`StoreCut`). That ordering is what rule 6 asks for: a
    record whose publication is confirmed after this point carries a position at or after the cut
    and gets exactly one notice, and one published before it has no intent here at all, because no
    route writes an intent into a store that does not exist yet. A position captured before this
    lock -- the intent's own stamp, taken before the native publication -- satisfies neither side
    of that: it can sit below a cut taken later by another publisher and be filtered although its
    publication came after creation.

    Two cases create the store, and both do it here, under the store's own lock, with the cut
    taken from that publication position:

    * the file is missing (first activation, or a human removed the disposable store);
    * the file is unreadable, in which case its bytes are moved aside first and are never
      overwritten in place. A write that changes nothing does not touch them at all, so an
      unreadable store still reads as unknown until something actually has to be written.
    """

    path = notice_store_path(coordination_root)
    try:
        ATTENTION_NOTICE_OWNERSHIP.check_declared_writer()
        with exclusive_access(path, ATTENTION_NOTICE_OWNERSHIP):
            publication_at = occurrence_at()
            observed = read_notice_store_state(coordination_root)
            store = observed.store
            if store is None:
                return _create_over_unreadable(
                    coordination_root, change, publication_at=publication_at
                )
            if not change(store, publication_at):
                return store
            if store.cut is None:
                store.cut = StoreCut(at=publication_at, sequence=1)
            _prune(store)
            atomic_write_text(path, serialize_notice_store(store))
            return store
    except OSError:
        logger.warning("Attention notice store %s did not hold", path, exc_info=True)
        return None


def _create_over_unreadable(
    coordination_root: Path,
    change: Callable[[AttentionNoticeStore, str], bool],
    *,
    publication_at: str,
) -> AttentionNoticeStore | None:
    """Quarantine unreadable bytes and create the store again with a new cut, never a replay."""

    path = notice_store_path(coordination_root)
    fresh = AttentionNoticeStore(cut=StoreCut(at=publication_at, sequence=1))
    if not change(fresh, publication_at):
        logger.warning(
            "Attention notice store %s is unreadable; nothing to write, keeping its bytes", path
        )
        return None
    _prune(fresh)
    if quarantine_torn_store(coordination_root) is None:
        return None
    atomic_write_text(path, serialize_notice_store(fresh))
    return fresh


def quarantine_torn_store(
    coordination_root: Path,
    now: datetime | None = None,
) -> Path | None:
    """Move an unreadable store aside to a :data:`TORN_STORE_INFIX` sibling; ``None`` if refused.

    The bytes are preserved for a human and the path is free for a newly created store. A move
    that fails leaves everything as it was: the caller then writes nothing at all, so an
    unreadable store is never replaced by silence.
    """

    path = notice_store_path(coordination_root)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.stem}{TORN_STORE_INFIX}{stamp}{path.suffix}")
    try:
        os.replace(path, target)
    except OSError:
        logger.warning("Attention notice store %s could not be quarantined", path, exc_info=True)
        return None
    logger.warning(
        "Attention notice store %s was unreadable; its bytes are now at %s", path, target
    )
    return target


def _prune(store: AttentionNoticeStore) -> None:
    """Enforce both count bounds and the whole-file byte bound by dropping the oldest rows.

    The order is a fact, not a convenience. Confirmed rows sort by ``sequence`` -- the order their
    native publications were confirmed in. Across the two lists the shared key is ``at``, the
    moment of the native publication; a pending intent ranks *before* a confirmed row with the
    same stamp because an intent is taken before the publication it later confirms. A row whose
    ``at`` this build cannot parse ranks oldest, so malformed rows leave first.

    Shedding only ever removes. It never turns a pending intent into a notice and never re-derives
    a row from the native record, so an evicted occurrence stays evicted and no publication the
    worktree routes did not make is invented here. ``sequence`` is normalised up to the largest
    retained number and never reused, so a later confirmation cannot collide with a shed row. The
    cut is not a row: no bound touches it.
    """

    store.notices.sort(key=lambda row: row.sequence)
    if store.notices:
        store.sequence = max(store.sequence, *[row.sequence for row in store.notices])
    while len(store.notices) > MAX_TERMINAL_NOTICES:
        store.notices.pop(0)
    while len(store.pending) > MAX_PENDING_NOTICES:
        store.pending.pop(0)
    while (store.notices or store.pending) and len(
        serialize_notice_store(store).encode("utf-8")
    ) > MAX_NOTICE_STORE_BYTES:
        if _pending_row_is_oldest(store):
            store.pending.pop(0)
        else:
            store.notices.pop(0)


def _adopted_cut(store: AttentionNoticeStore) -> StoreCut | None:
    """The cut a readable store written before the cut existed adopts, or ``None`` when inert.

    This is a one-time adoption, not a retake: it is derived from the store's own retained rows
    and nothing else -- no native record is read and no scan runs -- and the first write persists
    it. A store holding no rows adopts nothing here; the writing publication supplies the cut,
    which excludes nothing because there is nothing retained to exclude.
    """

    rows: list[PendingTerminalNotice] = [*store.notices, *store.pending]
    if not rows:
        return None
    oldest = min(rows, key=lambda row: _publication_order(row.at))
    return StoreCut(at=oldest.at, sequence=1)


def _pending_row_is_oldest(store: AttentionNoticeStore) -> bool:
    """Whether the oldest retained row overall is a pending intent rather than the first notice."""

    if not store.pending:
        return False
    if not store.notices:
        return True
    return _publication_order(store.pending[0].at) <= _publication_order(store.notices[0].at)


def _publication_order(stamp: str) -> tuple[int, float, str]:
    """A sortable publication moment; an unparseable stamp ranks before every real one."""

    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return (0, 0.0, stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (1, moment.timestamp(), stamp)


def occurrence_at(now: datetime | None = None) -> str:
    """The one native publication clock: the position of a landing or an abandonment.

    Both sides of rule 6 read this clock -- the store's creation cut, read inside the creating
    lock, and a confirmed notice's own ``at``, read inside the write that confirms it -- so their
    order is the order of the native publications themselves rather than of the intents taken
    before them.
    """

    return (now or datetime.now(UTC)).isoformat()
