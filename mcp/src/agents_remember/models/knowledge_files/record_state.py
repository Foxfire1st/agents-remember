"""Which state a record's revision has: proposed, reviewed or consolidated.

The state is never stored in a record; it is read from the history files of the same tree:

* **reviewed** -- the history file of the owner that wrote the revision (the file whose ``written``
  names it) is closed and holds a review fact (:class:`.history.ReviewFact`): the owner's memory
  review passed and the owner was closed out. The closeout writes the fact and ``closed`` in one
  write, so a fact on an open file is no closeout and counts for nothing;
* **consolidated** -- a closed file whose review fact names a consolidator as the agent that handed
  the change over either wrote the revision, or holds a rule or family row that names this revision
  as the one it read and kept (:data:`KEPT_DISPOSITIONS`, or a card-entry-only ``deleted`` rule row
  without ``effect == "retire"``), without writing a newer revision of it.
  A kept row counts only for a record the pass's list reached, which the file names in
  ``consolidationSubjects``: a family the list did not name, reached only because a member
  changed, is not consolidated by its row. A row about a record of another kind, one that changed
  or retired its subject, or an ``unresolved`` row, keeps nothing;
* **proposed** -- otherwise, also for a revision written outside any owner.

A retired record shows no state. Each attempt of a reopened leaf speaks for the revisions it wrote:
a revision a later, still open attempt wrote reads proposed, while a revision an earlier attempt
wrote keeps the state its own file gives it.

Two lines can each name one revision number of a record in their owners' files, when a sync
combined their edits into a revision the product wrote, or when each line wrote its own content
under the same number. The order of file names never decides between them. A current revision's
writing attempt is the one whose row binds the writer to the record's exact content. A row that
predates content digests relies on an unambiguous matching stamp. A row that kept only the
revision number cannot give a shared number a state, since it cannot say which content it read.
Two rows with different stamps and the same digest identify one content.

The crossing records a shared number whenever its two inputs have different contents at that
number. This preserves ambiguity even if one input has no stamp and no history row. The fact's reviewed tree identifies
which content was reviewed. A shared stamp alone cannot make that distinction.

A revision whose writing owner names it in no ``written`` list (a revision written before stamps
existed) is attributed through the record's stamp: the owner the stamp names, in the earliest
attempt whose ``changed`` row names the revision, else the owner's first file. A row that only read
the revision wrote nothing, so a later attempt's read never takes the revision from its writer.

An owner closed before reviews were recorded in the memory has its fact in the converted review
facts (:mod:`.converted_reviews`), keyed by owner and attempt; such a fact counts exactly like the
file's own ``review``, also for an owner that has no history file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Final, Literal

from agents_remember.kernel.canonical_json import canonical_json_bytes, prefixed_sha256_digest
from agents_remember.models.knowledge_files.converted_reviews import ConvertedReviews
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    HistoryRow,
    InvariantRow,
    ReviewFact,
    WrittenRevision,
)
from agents_remember.models.knowledge_files.history_product import (
    CrossingSelection,
    crossing_selection_digest,
    record_content_digest,
    stored_record_document,
)
from agents_remember.models.knowledge_files.records import LEGACY_STATE_WORDS, KnowledgeRecord
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp

RecordState = Literal["proposed", "reviewed", "consolidated"]
ReviewContentMatch = Callable[[str, str, int, str], bool | None]
RECORD_STATES: Final[tuple[RecordState, ...]] = ("proposed", "reviewed", "consolidated")
CONSOLIDATOR_ROLE: Final = "consolidator"
# The disposition a consolidator gives a record it could not judge; such a row keeps no revision.
UNRESOLVED: Final = "unresolved"
# The row kinds and dispositions that read a rule or family at the row's revision and left its text
# as it was: ``stands`` read it in full, ``moved``, ``extended`` and card-entry-only ``deleted``
# (``effect != "retire"``) change card entries only, ``rerouted`` and ``assigned`` keep the
# family's revision unless the owner also wrote a newer one, which the reader checks.
KEPT_DISPOSITIONS: Final[dict[type[HistoryRow], frozenset[str]]] = {
    InvariantRow: frozenset({"stands", "no_impact", "moved", "extended"}),
    FamilyRow: frozenset({"stands", "no_impact", "rerouted", "assigned"}),
}


def _keeps_revision(row: HistoryRow) -> bool:
    """Whether ``row`` kept its subject's revision (including card-entry-only deletes)."""

    if row.disposition in KEPT_DISPOSITIONS.get(type(row), ()):
        return True
    return isinstance(row, InvariantRow) and row.disposition == "deleted" and row.effect != "retire"


# The disposition of a row that wrote the revision it names.
WRITING_DISPOSITION: Final = "changed"


@dataclass(frozen=True)
class _FileKey:
    owner: str
    attempt: int


@dataclass(frozen=True)
class _RevisionEvidence:
    record: str
    revision: int
    writer: WriterStamp | None
    content_digest: str | None
    excluded_reviews: frozenset[str] = frozenset()
    legacy_shape: bool = False


@dataclass(frozen=True)
class _ReviewCheck:
    fact: ReviewFact
    identity: str
    exact: bool | None


@dataclass(frozen=True)
class ResolvedStanding:
    """The state, writer, and contributing review facts of one stored revision."""

    state: RecordState
    writer: WriterStamp | None
    reviews: frozenset[str] = frozenset()


@dataclass
class RevisionStates:
    """The history files of one tree, indexed once so that each state is one lookup."""

    files: dict[_FileKey, HistoryFile] = field(default_factory=dict)
    written_by: dict[tuple[str, int], list[tuple[_FileKey, WrittenRevision]]] = field(
        default_factory=dict
    )
    """Every file whose ``written`` names the revision, in the order of the files' keys."""
    newest_written: dict[tuple[_FileKey, str], int] = field(default_factory=dict)
    shared_by_crossing: dict[tuple[str, int], list[WriterStamp | None]] = field(
        default_factory=dict
    )
    """Distinct contents witnessed by a crossing, with any stamp that it could not distinguish."""
    crossing_selections: dict[tuple[str, int, str], dict[str, CrossingSelection]] = field(
        default_factory=dict
    )
    row_files: dict[tuple[str, int], list[_FileKey]] = field(default_factory=dict)
    """Every file with a judgment row naming the revision, whatever it did with it."""
    writing_rows: dict[tuple[str, int], list[_FileKey]] = field(default_factory=dict)
    kept_rows: dict[tuple[str, int], list[_FileKey]] = field(default_factory=dict)
    converted: dict[_FileKey, ReviewFact] = field(default_factory=dict)
    review_content_matches: ReviewContentMatch | None = None

    @classmethod
    def of(
        cls,
        histories: Iterable[HistoryFile],
        converted: ConvertedReviews | None = None,
        *,
        review_content_matches: ReviewContentMatch | None = None,
    ) -> RevisionStates:
        states = cls(review_content_matches=review_content_matches)
        if converted is not None:
            states.converted = {
                _FileKey(owner, attempt): fact
                for (owner, attempt), fact in converted.by_attempt().items()
            }
        ordered = sorted(histories, key=lambda history: (history.owner_id, history.attempt_number))
        for history in ordered:
            key = _FileKey(history.owner_id, history.attempt_number)
            states.files[key] = history
            for shared in history.shared_revisions or ():
                states.shared_by_crossing.setdefault((shared.record, shared.revision), []).append(
                    shared.conflicting_writer
                )
            for selection in history.crossing_selections or ():
                identity = (selection.record, selection.revision, selection.document_digest)
                states.crossing_selections.setdefault(identity, {})[key.owner] = selection
            for written in history.written or ():
                named = (written.record, written.revision)
                states.written_by.setdefault(named, []).append((key, written))
                newest = states.newest_written.get((key, written.record), 0)
                states.newest_written[(key, written.record)] = max(newest, written.revision)
            listed = frozenset(history.consolidation_subjects or ())
            for row in history.rows:
                states._index_row(key, row, listed)
        return states

    def _index_row(self, key: _FileKey, row: HistoryRow, listed: frozenset[str]) -> None:
        revision = getattr(row, "revision", None)
        if not isinstance(revision, int) or row.disposition == UNRESOLVED:
            return
        named = (row.subject, revision)
        self.row_files.setdefault(named, []).append(key)
        if row.disposition == WRITING_DISPOSITION:
            self.writing_rows.setdefault(named, []).append(key)
        if row.subject in listed and _keeps_revision(row):
            self.kept_rows.setdefault(named, []).append(key)

    def state_of(
        self,
        record: str,
        revision: int,
        *,
        retired: bool,
        writer: WriterStamp | None,
        content_digest: str | None = None,
    ) -> RecordState | None:
        """The state of ``record`` at ``revision`` holding the content ``writer`` stamped; None
        for a retired record."""

        if retired:
            return None
        return self._resolve(_RevisionEvidence(record, revision, writer, content_digest)).state

    def current_standing(
        self, record: KnowledgeRecord, *, retired: bool
    ) -> tuple[RecordState | None, WriterStamp | None]:
        """Resolve the current revision and omit its state if it is retired."""

        standing = self.resolve_current(record)
        return (None if retired else standing.state), standing.writer

    def resolve_current(self, record: KnowledgeRecord) -> ResolvedStanding:
        """Apply the same content proof to the public views and to either input of a crossing."""

        document = stored_record_document(record)
        digest = record_content_digest(document)
        evidence = _RevisionEvidence(
            record.id,
            record.revision,
            record.writer,
            digest,
            legacy_shape=getattr(record, "status", None) in LEGACY_STATE_WORDS,
        )
        selection = crossing_selection_digest(document) if self.crossing_selections else None
        return self._resolve(evidence, document_digest=selection)

    def _resolve(
        self, evidence: _RevisionEvidence, *, document_digest: str | None = None
    ) -> ResolvedStanding:
        checks = self._review_proof(evidence)
        if evidence.legacy_shape:
            return ResolvedStanding("proposed", None)
        evidence = self._bound_evidence(evidence, document_digest)
        eligible = {
            key: check
            for key, check in checks.items()
            if check.identity not in evidence.excluded_reviews
        }
        exact = self._proven_writes(evidence, eligible)
        writer = evidence.writer
        reviews = {key: eligible[key] for key in exact}
        if exact:
            writer = writer or _common_writer(exact.values())
        else:
            writer = self._attributed_writer(evidence, document_digest)
            evidence = replace(evidence, writer=writer)
            if claim := self._unbound_writing_review(evidence, eligible):
                reviews[claim[0]] = claim[1]
        kept = self._kept_reviews(evidence, eligible)
        consolidated = bool(kept) or any(
            _handed_over_by_consolidator(check.fact) for check in reviews.values()
        )
        claims = frozenset(check.identity for check in (*reviews.values(), *kept))
        state = "consolidated" if consolidated else "reviewed" if claims else "proposed"
        return ResolvedStanding(state, writer, claims)

    def _bound_evidence(
        self, evidence: _RevisionEvidence, document_digest: str | None
    ) -> _RevisionEvidence:
        if document_digest is None or not self.crossing_selections:
            return evidence
        identity = (evidence.record, evidence.revision, document_digest)
        excluded = frozenset(
            fact
            for entry in self.active_selections(*identity).values()
            for fact in entry.excluded_reviews
        )
        return replace(evidence, excluded_reviews=excluded)

    def _attributed_writer(
        self, evidence: _RevisionEvidence, document_digest: str | None
    ) -> WriterStamp | None:
        if evidence.writer is not None:
            return evidence.writer
        if evidence.content_digest is None:
            return None
        if document_digest is not None and self.crossing_selections:
            identity = (evidence.record, evidence.revision, document_digest)
            selected = self.active_selections(*identity)
            if selected:
                return _common_writer(entry.writer for entry in selected.values())
        return self._exact_writer(evidence.record, evidence.revision, evidence.content_digest)

    def active_selections(
        self, record: str, revision: int, document_digest: str
    ) -> dict[str, CrossingSelection]:
        entries = self.crossing_selections.get((record, revision, document_digest), {})
        replaced = {crossing for entry in entries.values() for crossing in entry.supersedes}
        return {crossing: entry for crossing, entry in entries.items() if crossing not in replaced}

    def _review_proof(self, evidence: _RevisionEvidence) -> dict[_FileKey, _ReviewCheck]:
        """Look up each relevant fact's exact content before choosing a writing attempt."""

        named = (evidence.record, evidence.revision)
        keys = {key for key, _ in self.written_by.get(named, ())}
        keys.update(self.writing_rows.get(named, ()))
        keys.update(self.kept_rows.get(named, ()))
        owner = evidence.writer.owner_id if evidence.writer is not None else None
        if owner is not None:
            keys.add(_FileKey(owner, 1))
        checks: dict[_FileKey, _ReviewCheck] = {}
        for key in keys:
            if (fact := self._review(key)) is not None:
                exact = self._review_match(fact, *named, evidence.content_digest)
                checks[key] = _ReviewCheck(fact, _review_digest(key, fact), exact)
        return checks

    def _proven_writes(
        self, evidence: _RevisionEvidence, checks: dict[_FileKey, _ReviewCheck]
    ) -> dict[_FileKey, WriterStamp | None]:
        """Find authoring claims whose own reviews prove the selected meaning."""

        named = (evidence.record, evidence.revision)
        writes = self.written_by.get(named, ())
        proven: dict[_FileKey, WriterStamp | None] = {
            key: row.writer
            for key, row in writes
            if key in checks
            and checks[key].exact is True
            and row.content_digest in (None, evidence.content_digest)
            and evidence.writer in (None, row.writer)
        }
        writer = evidence.writer
        if writer is None:
            return proven
        owner = writer.owner_id
        if owner is None and writer.by != "conversion":
            return proven
        if any(
            row.writer == writer and row.content_digest in (None, evidence.content_digest)
            for _, row in writes
        ):
            return proven
        legacy = self._legacy_files(*named)
        if owner is not None and not any(key.owner == owner for key in legacy):
            first = _FileKey(owner, 1)
            if not any(key == first for key, _ in writes):
                legacy.add(first)
        for key in legacy:
            # Conversion can leave the owner unknown even when a review proves the bytes.
            if (
                (owner is None or key.owner == owner)
                and key in checks
                and checks[key].exact is True
            ):
                proven[key] = writer
        return proven

    def _review(self, key: _FileKey) -> ReviewFact | None:
        """The review fact of one owner's attempt: its closed file's own, else its converted fact."""

        history = self.files.get(key)
        if history is not None and history.closed and history.review is not None:
            return history.review
        return self.converted.get(key)

    def _review_match(
        self, fact: ReviewFact, record: str, revision: int, content_digest: str | None
    ) -> bool | None:
        if fact.tree is None or content_digest is None or self.review_content_matches is None:
            return None
        return self.review_content_matches(fact.tree, record, revision, content_digest)

    def _unbound_writing_review(
        self, evidence: _RevisionEvidence, checks: dict[_FileKey, _ReviewCheck]
    ) -> tuple[_FileKey, _ReviewCheck] | None:
        writing = self._writing_file(
            evidence.record, evidence.revision, evidence.writer, evidence.content_digest
        )
        if writing is None or (check := checks.get(writing)) is None:
            return None
        if check.exact is None and self._writing_identifies_content(writing, evidence):
            return writing, check
        return None

    def _writing_identifies_content(self, writing: _FileKey, evidence: _RevisionEvidence) -> bool:
        record, revision = evidence.record, evidence.revision
        writer, content_digest = evidence.writer, evidence.content_digest
        named = self.written_by.get((record, revision), ())
        row = next(
            (row for key, row in named if key == writing and row.writer == writer),
            None,
        )
        if row is None:
            return not self.shared(record, revision, writer, content_digest)
        if row.content_digest is not None and row.content_digest == content_digest:
            return True
        # Multiple old stamps alone may describe a combined revision. A witnessed crossing or
        # a different known digest establishes that a stamp alone cannot carry the review.
        different = (record, revision) in self.shared_by_crossing or (
            content_digest is not None
            and any(other.content_digest not in (None, content_digest) for _, other in named)
        )
        return not different

    def conversion_writer(
        self, record: KnowledgeRecord, *, legacy_writer: WriterStamp
    ) -> WriterStamp | HistoryFile | None:
        """Find an old writing owner from exact review before applying the legacy inference."""

        document = stored_record_document(record)
        digest = record_content_digest(document)
        evidence = _RevisionEvidence(record.id, record.revision, legacy_writer, digest)
        if self.crossing_selections:
            evidence = self._bound_evidence(evidence, crossing_selection_digest(document))
        checks = self._review_proof(evidence)
        exact = {
            key.owner: self.files[key]
            for key in self._legacy_files(record.id, record.revision)
            if key in checks
            and checks[key].identity not in evidence.excluded_reviews
            and checks[key].exact is True
        }
        if exact:
            return next(iter(exact.values())) if len(exact) == 1 else None
        if recorded := self.written_stamp(record.id, record.revision, content_digest=digest):
            return recorded
        if record.revision > 1 and self.shared(record.id, record.revision, content_digest=digest):
            return None
        writing = self._writing_file(record.id, record.revision, legacy_writer, digest)
        if writing in checks and checks[writing].exact is False:
            return None
        return legacy_writer

    def written_stamp(
        self, record: str, revision: int, *, content_digest: str | None = None
    ) -> WriterStamp | None:
        """The stamp the history files record for ``record`` at ``revision``, where they record
        exactly one write of it; None where none does or two different writes share the number."""

        writes = self._writes(record, revision)
        if content_digest is not None and any(digest is not None for _, digest in writes):
            return self._exact_writer(record, revision, content_digest)
        return writes[0][0] if len(writes) == 1 and not self.shared(record, revision) else None

    def _exact_writer(self, record: str, revision: int, content_digest: str) -> WriterStamp | None:
        matches = [
            stamp for stamp, digest in self._writes(record, revision) if digest == content_digest
        ]
        return matches[0] if len(matches) == 1 else None

    def shared(
        self,
        record: str,
        revision: int,
        writer: WriterStamp | None = None,
        content_digest: str | None = None,
    ) -> bool:
        """Whether two different contents of ``record`` carry ``revision``. The one count of them:
        each distinct content digest of the ``written`` rows, or each stamp for older rows without
        one; each file with a ``changed`` row for it but
        no ``written`` row (a write from before stamps, one per file and attempt); and the content
        the record holds under ``writer``, where neither names it. A crossing also witnesses
        distinct contents independently of their history."""

        if (record, revision) in self.shared_by_crossing:
            return True
        writes = self._writes(record, revision)
        legacy = self._legacy_files(record, revision)
        unnamed = _unmatched_content(writes, legacy, writer, content_digest)
        digests = {digest for _, digest in writes if digest is not None}
        unstamped_content = sum(digest is None for _, digest in writes)
        return len(digests) + unstamped_content + len(legacy) + int(unnamed) > 1

    def _legacy_files(self, record: str, revision: int) -> set[_FileKey]:
        """The files that mark ``revision`` only by a ``changed`` row, without a ``written`` row."""

        stamped = {key for key, _row in self.written_by.get((record, revision), ())}
        return {key for key in self.writing_rows.get((record, revision), ()) if key not in stamped}

    def _writes(self, record: str, revision: int) -> list[tuple[WriterStamp, str | None]]:
        """The distinct stamp-and-content pairs of the rows naming the revision."""

        identities = {
            (canonical_json_bytes(row.writer.to_document()), row.content_digest): row.writer
            for _, row in self.written_by.get((record, revision), ())
        }
        return [(writer, digest) for (_, digest), writer in identities.items()]

    def _writing_file(
        self, record: str, revision: int, writer: WriterStamp | None, content_digest: str | None
    ) -> _FileKey | None:
        """The file that wrote the content ``writer`` stamped at ``revision``; None where no file
        can be shown to have written exactly that content."""

        named = self.written_by.get((record, revision), [])
        if named:
            stamped = self._file_with_stamp(record, revision, writer, content_digest)
            if stamped is not None:
                return stamped
        owner = writer.owner_id if writer is not None else None
        if owner is None:
            return None
        attempts = [key for key in self._legacy_files(record, revision) if key.owner == owner]
        if attempts:
            return min(attempts, key=lambda key: key.attempt)
        first = _FileKey(owner, 1)
        if any(key == first for key, _ in named):
            return None
        return first if first in self.files or first in self.converted else None

    def _file_with_stamp(
        self, record: str, revision: int, writer: WriterStamp | None, content_digest: str | None
    ) -> _FileKey | None:
        if writer is None:
            if content_digest is not None:
                return None  # No row proved a writer for the live content.
            writer = self.written_stamp(record, revision)
        if writer is None:
            return None
        named = self.written_by[(record, revision)]
        carrying = [(key, row.content_digest) for key, row in named if row.writer == writer]
        if content_digest is not None and any(digest is not None for _, digest in carrying):
            matches = [key for key, digest in carrying if digest == content_digest]
            return _file_of_one_write(matches, writer)
        if writer in self.shared_by_crossing.get((record, revision), ()):
            return None
        if content_digest is not None and len(carrying) > 1:
            return None  # A stamp alone cannot distinguish between its own unstamped-era rows.
        return _file_of_one_write([key for key, _ in carrying], writer)

    def _kept_reviews(
        self, evidence: _RevisionEvidence, checks: dict[_FileKey, _ReviewCheck]
    ) -> list[_ReviewCheck]:
        """Read a keeper's fact, with unbound credit only at an unshared number."""

        record, revision = evidence.record, evidence.revision
        kept = [
            checks[key]
            for key in set(self.kept_rows.get((record, revision), ()))
            if key in checks
            and _handed_over_by_consolidator(checks[key].fact)
            and self.newest_written.get((key, record), 0) <= revision
        ]
        if not kept:
            return []
        shared = self.shared(record, revision, evidence.writer, evidence.content_digest)
        return [
            check for check in kept if check.exact is True or (check.exact is None and not shared)
        ]


def _common_writer(writers: Iterable[WriterStamp | None]) -> WriterStamp | None:
    stamps = iter(writers)
    first = next(stamps, None)
    return first if all(writer == first for writer in stamps) else None


def _unmatched_content(
    writes: list[tuple[WriterStamp, str | None]],
    legacy: set[_FileKey],
    writer: WriterStamp | None,
    content_digest: str | None,
) -> bool:
    if content_digest is not None and any(digest == content_digest for _, digest in writes):
        return False
    if writer is None:
        return content_digest is not None and bool(writes)
    same_stamp = [digest for stamp, digest in writes if stamp == writer]
    if same_stamp:
        return content_digest is not None and None not in same_stamp
    return writer.owner_id not in {key.owner for key in legacy}


def _file_of_one_write(keys: list[_FileKey], stamp: WriterStamp | None) -> _FileKey | None:
    """The file that wrote one stamped content, among the files whose rows carry its stamp: the
    earliest attempt of the owner the stamp names, else of the one owner they share; none where
    files of several other owners carry it, so no file name decides."""

    if stamp is not None and any(key.owner == stamp.owner_id for key in keys):
        keys = [key for key in keys if key.owner == stamp.owner_id]
    if len({key.owner for key in keys}) != 1:
        return None
    return min(keys, key=lambda key: key.attempt)


def _handed_over_by_consolidator(review: ReviewFact | None) -> bool:
    return review is not None and review.handed_over_by.role == CONSOLIDATOR_ROLE


def _review_digest(key: _FileKey, fact: ReviewFact) -> str:
    return prefixed_sha256_digest(
        {"owner": key.owner, "attempt": key.attempt, "fact": fact.to_document()}
    )
