"""The one-time conversion of a memory tree to derived states and stamped writers.

``agents-remember knowledge-convert-states`` drives it. The plan is computed from the bytes of the
tree's knowledge files and the task documents' facts (:mod:`.task_review_facts`), and changes:

* every record file under ``knowledge/<kind-dir>/``: ``status`` leaves it where it reads one of the
  old typed states ``proposed`` or ``accepted``; ``retired`` and the words a kind has for its own
  life stay. The current revision gets the conversion's ``writer`` stamp where it has none
  (:mod:`.state_owners`). Every other byte stays: the file is rewritten in the canonical
  formatting it already had. A record typed ``accepted`` is converted like every other record:
  the word leaves, nothing else is added, and its state is derived like any other;
* ``knowledge/history/converted-reviews.json``: a review fact for the one attempt a passed memory
  review round is proven for, and only with the round's verdict reference. The proof is that the
  owner has a single attempt: one closed history file without a fact of its own, and a task
  document that records no reopening. A reopened leaf's document records one sequence of rounds
  for all its attempts, and no round names its attempt or its time, so no attempt of it gets a
  fact: a later attempt closed without a fresh pass would otherwise inherit an earlier attempt's
  pass. Every owner whose passed round gives no fact is named in the report with the reason.
  Frozen history files are never written.

Run a second time the plan is empty: a stamped revision keeps its stamp, a removed word is gone and
a converted fact is kept as written. The plan applies all or nothing; where restoring after a
failed write fails too, the error names every file left changed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.errors import StateConversionError
from agents_remember.memory.conversion.state_owners import (
    HistoryIndex,
    Owner,
    OwnerFinding,
    record_stamp,
    review_fact,
    revision_owner,
)
from agents_remember.memory.conversion.task_review_facts import PassedReview, TaskOwners
from agents_remember.memory.reviewed_content import reviewed_content
from agents_remember.models.knowledge_files.canonical import (
    FORMATTER_COMMAND,
    canonical_text,
    parse_json,
)
from agents_remember.models.knowledge_files.converted_reviews import (
    CONVERTED_REVIEWS_PATH,
    ConvertedReview,
    ConvertedReviews,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    parse_document_text,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.history_product import stored_record_content_digest
from agents_remember.models.knowledge_files.record_state import RevisionStates
from agents_remember.models.knowledge_files.records import (
    LEGACY_STATE_WORDS,
    RECORD_MODELS,
    KnowledgeRecord,
    record_status,
)
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp

HISTORY_PREFIX = f"{KNOWLEDGE_ROOT}/history/"
_RECORD_PREFIXES = tuple(f"{KNOWLEDGE_ROOT}/{name}/" for name in RECORD_DIRECTORIES.values())
_RECORD_TYPES = tuple(RECORD_MODELS.values())


@dataclass
class StateCounts:
    """What the converted tree reads, per current revision, and what the plan changes."""

    reviewed: int = 0
    proposed: int = 0
    no_leaf_found: int = 0
    ambiguous_writer: int = 0
    consolidated: int = 0
    retired: int = 0
    typed_accepted: int = 0
    words_removed: int = 0
    stamps_added: int = 0
    facts_added: int = 0

    def to_document(self) -> dict[str, int]:
        return {
            "reviewed": self.reviewed,
            "proposed": self.proposed,
            "noLeafFound": self.no_leaf_found,
            "ambiguousWriter": self.ambiguous_writer,
            "consolidated": self.consolidated,
            "retiredNoState": self.retired,
            "typedAccepted": self.typed_accepted,
            "statusWordsRemoved": self.words_removed,
            "stampsAdded": self.stamps_added,
            "reviewFactsAdded": self.facts_added,
        }


@dataclass
class StateConversionPlan:
    """The files the conversion changes (new bytes, and the bytes it read), and its counts."""

    changes: dict[str, bytes] = field(default_factory=dict)
    originals: dict[str, bytes | None] = field(default_factory=dict)
    counts: StateCounts = field(default_factory=StateCounts)
    owners_without_fact: dict[str, str] = field(default_factory=dict)
    """Owner ID -> why its passed review gave no converted fact."""


@dataclass(frozen=True)
class _ConversionEvidence:
    index: HistoryIndex
    owners: TaskOwners
    states: RevisionStates


def plan_state_conversion(
    files: Mapping[str, bytes], owners: TaskOwners, *, at: str, repository: Path | None = None
) -> StateConversionPlan:
    """Plan the conversion of the knowledge files ``files`` (memory-relative path -> bytes)."""

    plan = StateConversionPlan()
    histories = _histories(files)
    index = HistoryIndex.of(histories)
    existing = _converted_reviews(files)
    converted = _plan_review_facts(index, owners, existing, plan, at=at)
    matches = reviewed_content(repository) if repository is not None else None
    states = RevisionStates.of(histories, converted, review_content_matches=matches)
    evidence = _ConversionEvidence(index, owners, states)
    for path in sorted(files):
        if path.startswith(_RECORD_PREFIXES) and path.endswith(".json"):
            _plan_record(path, files[path], evidence, plan, at=at)
    if converted is not None:
        _change(plan, CONVERTED_REVIEWS_PATH, files.get(CONVERTED_REVIEWS_PATH), converted)
    return plan


def _parsed[T](path: str, parse: Callable[[], T]) -> T:
    """``parse()`` of the file ``path``; a file that does not parse stops the run, named."""

    try:
        return parse()
    except ValidationError as error:
        first = error.errors()[0]
        place = ".".join(str(part) for part in first["loc"]) or "the document"
        reason = f"{error.error_count()} error(s), first at {place}: {first['msg']}"
    except (ValueError, RecursionError) as error:  # text decoding, JSON, canonical form, depth
        reason = f"{type(error).__name__}: {error}"
    raise StateConversionError(f"{path} does not parse ({reason}); the conversion changes nothing")


def _histories(files: Mapping[str, bytes]) -> list[HistoryFile]:
    return [
        _parsed(path, lambda path=path, data=data: parse_history_document(path, data.decode()))
        for path, data in sorted(files.items())
        if path.startswith(HISTORY_PREFIX) and path != CONVERTED_REVIEWS_PATH
    ]


def _converted_reviews(files: Mapping[str, bytes]) -> ConvertedReviews | None:
    data = files.get(CONVERTED_REVIEWS_PATH)
    if data is None:
        return None
    document = _parsed(CONVERTED_REVIEWS_PATH, lambda: parse_document_text(data.decode()))
    if not isinstance(document, ConvertedReviews):
        raise StateConversionError(f"{CONVERTED_REVIEWS_PATH} is not the converted review facts")
    return document


def _plan_review_facts(
    index: HistoryIndex,
    owners: TaskOwners,
    existing: ConvertedReviews | None,
    plan: StateConversionPlan,
    *,
    at: str,
) -> ConvertedReviews | None:
    entries = {
        (one.owner, one.attempt_number): one for one in (existing.reviews if existing else ())
    }
    for owner_id in sorted(owners.ambiguous):
        plan.owners_without_fact[owner_id] = "more than one task document records a passed round"
    for owner_id, unreadable in sorted(owners.unreadable_reviews.items()):
        attempts = index.attempts.get(owner_id, {})
        own_facts = bool(attempts) and all(one.review is not None for one in attempts.values())
        if not own_facts and (owner_id, 1) not in entries:
            plan.owners_without_fact.setdefault(owner_id, unreadable)
    for owner_id, passed in sorted(owners.passed.items()):
        attempts = index.attempts.get(owner_id, {})
        own_facts = bool(attempts) and all(one.review is not None for one in attempts.values())
        if own_facts or (owner_id, 1) in entries or owner_id in owners.unreadable_reviews:
            continue  # its own facts, a kept converted one, or reported unreadable above
        unproven = _unproven_pass(passed, attempts, owners.reopenings.get(owner_id, 0))
        if unproven is not None:
            plan.owners_without_fact[owner_id] = unproven
            continue
        owner = Owner(attempts[1].owner_kind, owner_id)
        try:
            entry = ConvertedReview.model_validate(
                {"owner": owner_id, "fact": review_fact(owner, passed, owners, at=at)}
            )
        except ValidationError as error:
            plan.owners_without_fact[owner_id] = _unstorable(passed, error)
            continue
        entries[(owner_id, 1)] = entry
        plan.counts.facts_added += 1
    if not entries:
        return None
    return ConvertedReviews(reviews=tuple(entries[key] for key in sorted(entries)))


def _unstorable(passed: PassedReview, error: ValidationError) -> str:
    """Why the fact of ``passed`` cannot be stored: the review fact's own first objection."""

    first = error.errors()[0]
    place = ".".join(str(part) for part in first["loc"])
    return (
        f"its last passed memory round {passed.round} cannot be stored as a review fact "
        f"({place}: {first['msg']}), so the conversion attaches it to none"
    )


def _unproven_pass(
    passed: PassedReview, attempts: Mapping[int, HistoryFile], reopenings: int
) -> str | None:
    """Why ``passed`` proves no attempt's review, or None when it proves the single attempt's."""

    if not attempts:
        return "no history file: the owner was not closed out"
    if passed.verdict_ref is None:
        return (
            f"its last passed memory round {passed.round} records no usable verdict reference "
            "(absent, blank, or not printable text within the reference length), and a converted "
            "fact must name the verdict"
        )
    if set(attempts) != {1} or reopenings:
        files = ", ".join(str(number) for number in sorted(attempts))
        return (
            f"reopened leaf (history attempts {files}; {reopenings} reopening(s) recorded): its "
            "task document records one sequence of review rounds for all attempts, and no "
            f"record says which attempt round {passed.round} passed, so the conversion attaches it "
            "to none"
        )
    if not attempts[1].closed:
        return "its history file is open"
    return None


def _plan_record(
    path: str,
    data: bytes,
    evidence: _ConversionEvidence,
    plan: StateConversionPlan,
    *,
    at: str,
) -> None:
    document = _canonical_document(path, data)
    record = _record(path, document)
    states = evidence.states
    converted = dict(document)
    if converted.get("status") == "accepted":
        plan.counts.typed_accepted += 1
    if converted.get("status") in LEGACY_STATE_WORDS:
        del converted["status"]
        plan.counts.words_removed += 1
    finding = revision_owner(record, evidence.index)
    ambiguous = finding.ambiguous
    if "writer" not in converted:
        stamp, ambiguous = _conversion_stamp(record, finding, evidence, at=at)
        converted["writer"] = stamp.to_document()
        plan.counts.stamps_added += 1
    result = _record(path, converted)
    _count_state(result, ambiguous, states, plan.counts)
    if converted != document:
        _change(plan, path, data, converted)


def _conversion_stamp(
    record: KnowledgeRecord, finding: OwnerFinding, evidence: _ConversionEvidence, *, at: str
) -> tuple[WriterStamp, bool]:
    legacy_writer = record_stamp(record, finding, evidence.owners, at=at)
    writer = evidence.states.conversion_writer(record, legacy_writer=legacy_writer)
    if isinstance(writer, WriterStamp):
        return writer, finding.ambiguous
    if isinstance(writer, HistoryFile):
        owner = Owner(writer.owner_kind, writer.owner_id)
        return record_stamp(record, OwnerFinding(owner), evidence.owners, at=at), False
    digest = stored_record_content_digest(record)
    ambiguous = finding.ambiguous or (
        record.revision > 1
        and evidence.states.shared(record.id, record.revision, content_digest=digest)
    )
    return record_stamp(record, OwnerFinding(None), evidence.owners, at=at), ambiguous


def _count_state(
    record: KnowledgeRecord, ambiguous: bool, states: RevisionStates, counts: StateCounts
) -> None:
    retired = record_status(record) == "retired"
    state, _writer = states.current_standing(record, retired=retired)
    if state is None:
        counts.retired += 1
    elif state == "reviewed":
        counts.reviewed += 1
    elif state == "consolidated":
        counts.consolidated += 1
    else:
        counts.proposed += 1
        if record.writer is None or record.writer.owner_id is None:
            counts.no_leaf_found += 1
            counts.ambiguous_writer += int(ambiguous)


def _canonical_document(path: str, data: bytes) -> dict[str, Any]:
    text = _parsed(path, data.decode)
    document = _parsed(path, lambda: parse_json(text))
    if not isinstance(document, dict):
        raise StateConversionError(f"{path} is not a JSON object")
    if canonical_text(document) != text:
        raise StateConversionError(
            f"{path} is not canonically formatted, so rewriting it would change more than the "
            f"state; run `{FORMATTER_COMMAND} {path}` and land that first"
        )
    return document


def _record(path: str, document: dict[str, Any]) -> KnowledgeRecord:
    record = _parsed(path, lambda: parse_document_text(canonical_text(document)))
    if not isinstance(record, _RECORD_TYPES):
        raise StateConversionError(f"{path} lies in a record folder and is no knowledge record")
    return record


def _change(plan: StateConversionPlan, path: str, original: bytes | None, value: Any) -> None:
    rendered = value.to_document() if isinstance(value, ConvertedReviews) else value
    data = canonical_text(rendered).encode("utf-8")
    if data != original:
        plan.changes[path] = data
        plan.originals[path] = original
