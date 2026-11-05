"""GT2: actual history/record artifacts under invariant and family row schemas.

Product models own shape/revision rules. These checks expose contradictions in
captured artifacts; meaning, reason and examination adequacy remain rubric work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_writer.authoring_rows import meaning
from agents_remember.errors import AuthorityError
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    InvariantRow,
    frozen_history_violation,
    invariant_revision_violation,
    merged_leaf_history,
    stale_examined_members,
)
from agents_remember.models.knowledge_files.records import FamilyRecord, InvariantRecord
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.sidecars import FileSidecar
from pydantic import ValidationError

from ..adapters.models import NativeCapture
from ..artifacts import EvidenceFile, child_path, read_object
from .models import Grade, native_reference, referenced_events

Record = InvariantRecord | FamilyRecord


@dataclass(frozen=True, slots=True)
class HistoryObservation:
    subject: str
    before_root: Path
    after_root: Path
    record_path: str
    before_record: EvidenceFile | None
    after_record: EvidenceFile | None
    history_file: EvidenceFile
    capture: NativeCapture
    native_call_sequences: tuple[int, ...]
    before_entry_files: tuple[EvidenceFile, ...] = ()
    after_entry_files: tuple[EvidenceFile, ...] = ()
    after_member_files: tuple[EvidenceFile, ...] = ()
    authority_refs: tuple[str, ...] = ()
    before_history: EvidenceFile | None = None
    prior_history_files: tuple[EvidenceFile, ...] = ()


def _record(root: Path, record_path: str, evidence: EvidenceFile | None) -> Record | None:
    if evidence is None:
        if child_path(root, record_path).exists():
            raise AuthorityError(f"record exists without captured hash: {root / record_path}")
        return None
    raw = read_object(evidence.verify(root))
    model = FamilyRecord if raw.get("schema") == "ar-family/v1" else InvariantRecord
    return model.model_validate(raw)


def _entries(root: Path, files: tuple[EvidenceFile, ...], subject: str) -> dict[str, Anchor]:
    entries: dict[str, Anchor] = {}
    for evidence in files:
        card = FileSidecar.model_validate(read_object(evidence.verify(root)))
        for entry in (*card.realizes, *(card.proves or ())):
            if entry.invariant == subject:
                if entry.id in entries:
                    raise AuthorityError(f"entry {entry.id} occurs twice in captured sidecars")
                entries[entry.id] = entry.anchor.model_copy(update={"path": card.path})
    return entries


def _artifact_refs(observation: HistoryObservation) -> list[str]:
    evidence = [
        observation.history_file,
        *observation.before_entry_files,
        *observation.after_entry_files,
        *observation.after_member_files,
        *observation.prior_history_files,
    ]
    evidence.extend(
        item
        for item in (
            observation.before_record,
            observation.after_record,
            observation.before_history,
        )
        if item is not None
    )
    return [f"{item.path}@sha256:{item.sha256}" for item in evidence]


def _native_calls(observation: HistoryObservation) -> tuple[list[str], list[str]]:
    events, issues = referenced_events(observation.capture, observation.native_call_sequences)
    refs = [native_reference(event) for event in events]
    unknowns = [*issues, *observation.capture.unknowns]
    if not events:
        unknowns.append("no native authoring call was captured for this observation")
    for event in events:
        if event.kind != "tool_call":
            unknowns.append(f"event {event.sequence} is not an actual native authoring call")
    return refs, unknowns


def _family_checks(
    row: FamilyRow,
    before: Record | None,
    after: FamilyRecord,
    observation: HistoryObservation,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    unknowns: list[str] = []
    if row.revision is not None and row.revision != after.revision:
        failures.append("family row revision differs from its actual candidate record")
    if isinstance(before, FamilyRecord):
        guarantee_changed = before.guarantee != after.guarantee
        if guarantee_changed and row.disposition not in ("changed", "retired"):
            failures.append("family guarantee changed but its governing row is not changed/retired")
        if row.disposition == "changed" and after.revision != before.revision + 1:
            failures.append("changed family record did not increment its own revision")
    if row.disposition == "retired" and after.status != "retired":
        failures.append("retired family row leaves its actual family record active")
    revisions: dict[str, int] = {}
    for evidence in observation.after_member_files:
        member = InvariantRecord.model_validate(
            read_object(evidence.verify(observation.after_root))
        )
        revisions[member.id] = member.revision
    missing = [member.id for member in row.examined if member.id not in revisions]
    if missing:
        unknowns.append(f"examined member records not captured: {', '.join(missing)}")
    stale = stale_examined_members(row, revisions)
    if stale:
        failures.append(f"family examined revisions are stale: {', '.join(stale)}")
    return failures, unknowns


def _side_observed(
    root: Path, files: tuple[EvidenceFile, ...], other: tuple[EvidenceFile, ...]
) -> bool:
    # An omitted file is known absent only when its actual counterpart path is absent.
    return bool(files) or (
        bool(other) and all(not child_path(root, item.path).exists() for item in other)
    )


def _covers_checks(
    row: InvariantRow, observation: HistoryObservation
) -> tuple[list[str], list[str]]:
    before = _entries(observation.before_root, observation.before_entry_files, row.subject)
    after = _entries(observation.after_root, observation.after_entry_files, row.subject)
    failures: list[str] = []
    unknowns: list[str] = []
    for cover in row.covers:
        for label, claimed, entries in (
            ("before", cover.before, before),
            ("after", cover.after, after),
        ):
            actual = entries.get(cover.id)
            if claimed == "absent":
                if actual is not None:
                    failures.append(
                        f"{cover.id} claimed absent {label} but actual sidecar contains it"
                    )
            elif actual is None:
                unknowns.append(f"{cover.id} actual {label} sidecar anchor was not captured")
            elif claimed != actual:
                failures.append(
                    f"{cover.id} {label} anchor contradicts the actual captured sidecar"
                )
    before_known = _side_observed(
        observation.before_root, observation.before_entry_files, observation.after_entry_files
    )
    after_known = _side_observed(
        observation.after_root, observation.after_entry_files, observation.before_entry_files
    )
    if not before_known or not after_known:
        unknowns.append("before/after sidecar observations are incomplete")
        return failures, unknowns
    failures.extend(_entry_set_findings(row, before, after))
    return failures, unknowns


def _entry_set_findings(
    row: InvariantRow, before: dict[str, Anchor], after: dict[str, Anchor]
) -> list[str]:
    failures: list[str] = []
    added, removed = set(after) - set(before), set(before) - set(after)
    if row.disposition in ("moved", "no_impact") and (added or removed):
        failures.append(f"{row.disposition} contradicts actual entry additions/removals")
    if row.disposition == "extended" and not added:
        failures.append("extended row has no actual added entry in captured sidecars")
    if row.disposition == "moved" and before == after:
        failures.append("moved row has no actual re-anchored entry")
    return failures


def _prior_revision(observation: HistoryObservation, history: HistoryFile) -> int | None:
    # The existing exception is models/knowledge_files/history.py:791-822: the
    # governing changed row of an earlier frozen attempt, never an inferred row.
    files = [
        HistoryFile.model_validate(read_object(item.verify(observation.after_root)))
        for item in observation.prior_history_files
    ]
    if not files or any(
        not item.closed
        or item.owner_id != history.owner_id
        or item.attempt_number >= history.attempt_number
        for item in files
    ):
        return None
    merged = merged_leaf_history(files)
    row = merged.row_about(observation.subject) if merged else None
    return row.revision if isinstance(row, InvariantRow) and row.disposition == "changed" else None


def _revision_checks(
    row: InvariantRow,
    before: Record | None,
    after: InvariantRecord,
    observation: HistoryObservation,
    history: HistoryFile,
) -> tuple[list[str], list[str]]:
    restated = _prior_revision(observation, history)
    violation = invariant_revision_violation(
        row,
        base_revision=before.revision if before else None,
        candidate_revision=after.revision,
        restated_revision=restated,
    )
    if violation is None:
        return [], []
    possible_restatement = (
        isinstance(before, InvariantRecord)
        and row.disposition == "changed"
        and row.revision == before.revision == after.revision
        and history.attempt_number > 1
    )
    if possible_restatement and restated is None:
        return [], [
            "same-revision later-attempt row lacks a matching frozen governing changed-row witness"
        ]
    return [violation], []


def _invariant_checks(
    row: InvariantRow,
    before: Record | None,
    after: InvariantRecord,
    observation: HistoryObservation,
    history: HistoryFile,
) -> tuple[list[str], list[str]]:
    failures, unknowns = _covers_checks(row, observation)
    revision_failures, revision_unknowns = _revision_checks(
        row, before, after, observation, history
    )
    failures.extend(revision_failures)
    unknowns.extend(revision_unknowns)
    if isinstance(before, InvariantRecord):
        before_fields = before.model_dump(mode="json", by_alias=True)
        after_fields = after.model_dump(mode="json", by_alias=True)
        if meaning(before_fields) != meaning(after_fields):
            if before.revision == after.revision:
                failures.append(
                    "meaning-bearing record fields changed without a revision increment"
                )
            if row.disposition not in ("changed", "deleted"):
                failures.append(
                    "meaning-bearing record fields changed under a non-changed disposition"
                )
        if row.disposition == "deleted" and row.effect == "retire" and after.status != "retired":
            failures.append("retire effect leaves actual invariant record active")
    return failures, unknowns


def _record_row_checks(
    observation: HistoryObservation, before: Record, after: Record | None, history: HistoryFile
) -> tuple[list[str], list[str]]:
    row = history.row_about(observation.subject)
    if row is None:
        return ["existing subject has no judgment row in the captured history file"], []
    if after is None:
        return ["existing invariant/family record was removed instead of retained/retired"], []
    if isinstance(row, InvariantRow) and isinstance(after, InvariantRecord):
        return _invariant_checks(row, before, after, observation, history)
    if isinstance(row, FamilyRow) and isinstance(after, FamilyRecord):
        return _family_checks(row, before, after, observation)
    return ["history row kind and actual record kind disagree"], []


def _grade_artifacts(observation: HistoryObservation) -> tuple[list[str], list[str]]:
    before = _record(observation.before_root, observation.record_path, observation.before_record)
    after = _record(observation.after_root, observation.record_path, observation.after_record)
    history_path = observation.history_file.verify(observation.after_root)
    history = HistoryFile.model_validate(read_object(history_path))
    if observation.before_history is not None:
        base_bytes = observation.before_history.verify(observation.before_root).read_bytes()
        if frozen_history_violation([base_bytes], history_path.read_bytes()):
            return ["closed history file was changed after closeout"], []
    if any(record.id != observation.subject for record in (before, after) if record is not None):
        return ["captured record identity differs from the history subject"], []
    if before is None:
        return [], []  # R07 rule 6: new records need no own row, attribution stays rubric work.
    return _record_row_checks(observation, before, after, history)


def grade_history(observation: HistoryObservation) -> Grade:
    refs, unknowns = _native_calls(observation)
    refs.extend(_artifact_refs(observation))
    refs.extend(observation.authority_refs)
    try:
        failures, artifact_unknowns = _grade_artifacts(observation)
    except (OSError, AuthorityError) as exc:
        return Grade.produced(__file__, "GT2", "unknown", (*refs, str(exc)))
    except (ValidationError, ValueError) as exc:
        return Grade.produced(
            __file__, "GT2", "fail", (*refs, f"captured history/record schema contradiction: {exc}")
        )
    if failures:
        return Grade.produced(__file__, "GT2", "fail", (*refs, *failures))
    unknowns.extend(artifact_unknowns)
    if unknowns:
        return Grade.produced(__file__, "GT2", "unknown", (*refs, *unknowns))
    return Grade.produced(
        __file__,
        "GT2",
        "needs_rubric",
        tuple(refs),
        (
            "Do reason and disposition describe actual invariant meaning/entry changes under R07 rule 3, or family changes under rule 5?",
            "Were examined member revisions, semantic effects and episode authority adequate on the actual before/after trees?",
        ),
    )
