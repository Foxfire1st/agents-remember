"""Bind captured case artifacts to the product-backed history grader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_writer.authoring_rows import meaning
from agents_remember.errors import AuthorityError
from agents_remember.models.knowledge_files.history import FamilyRow, HistoryFile, InvariantRow
from agents_remember.models.knowledge_files.records import FamilyRecord, InvariantRecord
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.sidecars import FileSidecar

from ..adapters.models import NativeCapture, NativeEvent, SourceFile
from ..artifacts import EvidenceFile, read_object
from ..fixtures import CaseBundle
from .history import HistoryObservation, grade_history
from .models import Grade
from .routes import command_heads
from .trajectory import arguments

MEMORY = "projects/memory/"


def _kind(path: str) -> str | None:
    if not path.startswith(MEMORY) or not path.endswith(".json"):
        return None
    relative = path.removeprefix(MEMORY)
    if relative.startswith(("knowledge/invariants/", "knowledge/families/")):
        return "record"
    if relative.startswith("knowledge/history/"):
        return "history"
    if Path(relative).name == "overview.index.json":
        return None
    return "sidecar" if relative.startswith("onboarding/") else None


@dataclass(frozen=True)
class Snapshot:
    root: Path
    files: dict[str, EvidenceFile]
    records: dict[str, EvidenceFile]
    histories: dict[str, HistoryFile]
    sidecars: tuple[EvidenceFile, ...]
    entries: dict[str, dict[str, Anchor]]

    @classmethod
    def read(
        cls,
        root: Path,
        inventory: list[dict[str, str]],
        known_sidecars: frozenset[str] = frozenset(),
    ) -> Snapshot:
        files = {item["path"]: EvidenceFile(**item) for item in inventory}
        records: dict[str, EvidenceFile] = {}
        histories: dict[str, HistoryFile] = {}
        sidecars = []
        entries: dict[str, dict[str, Anchor]] = {}
        for path, evidence in files.items():
            kind = _kind(path)
            if kind is None:
                continue
            raw = read_object(evidence.verify(root))
            if kind == "record":
                model = FamilyRecord if raw.get("schema") == "ar-family/v1" else InvariantRecord
                subject = model.model_validate(raw).id
                if subject in records:
                    raise ValueError(f"subject occurs in two captured record files: {subject}")
                records[subject] = evidence
            elif kind == "history":
                histories[path] = HistoryFile.model_validate(raw)
            else:
                if raw.get("schema") != "ar-onboarding-file/v1" and path not in known_sidecars:
                    continue
                card = FileSidecar.model_validate(raw)
                for entry in (*card.realizes, *(card.proves or ())):
                    owned = entries.setdefault(entry.invariant, {})
                    if entry.id in owned:
                        raise ValueError(f"entry occurs in two captured sidecars: {entry.id}")
                    owned[entry.id] = entry.anchor.model_copy(update={"path": card.path})
                sidecars.append(evidence)
        return cls(root, files, records, histories, tuple(sidecars), entries)


@dataclass(frozen=True)
class ExpectedJudgment:
    subject: str
    revision: int
    disposition: str
    reference: str


def _expected_judgment(case: CaseBundle) -> ExpectedJudgment | None:
    path = case.manifest.get("setup", {}).get("expected")
    if path is None:
        return None
    evidence = next((item for item in case.files if item.path == path), None)
    if evidence is None:
        raise AuthorityError(f"declared expected outcome is not hash-bound: {path}")
    raw = read_object(evidence.verify(case.path))
    if not {"subject", "revision", "disposition"}.intersection(raw):
        return None
    subject, revision, disposition = (
        raw.get("subject"),
        raw.get("revision"),
        raw.get("disposition"),
    )
    if (
        not isinstance(subject, str)
        or not subject.startswith(("INV-", "FAM-"))
        or type(revision) is not int
        or revision < 1
        or disposition not in (*InvariantRow.dispositions, *FamilyRow.dispositions)
    ):
        raise AuthorityError(f"invalid structured expected history judgment: {path}")
    # reasonMustName is already part of the stated semantic rubric; do not
    # convert it into a literal phrase test or append new judge criteria.
    return ExpectedJudgment(subject, revision, disposition, f"{path}@sha256:{evidence.sha256}")


def _capture(raw: dict[str, Any]) -> NativeCapture:
    return NativeCapture(
        raw["harness"],
        raw["session_id"],
        tuple(NativeEvent(**event) for event in raw["events"]),
        tuple(SourceFile(**source) for source in raw["source_files"]),
        tuple(raw["raw_records"]),
        tuple(raw["unknowns"]),
    )


def _authoring_calls(capture: NativeCapture) -> tuple[int, ...]:
    sequences = []
    for event in capture.events:
        if event.kind != "tool_call":
            continue
        raw_name = event.payload.get("name")
        if not isinstance(raw_name, str):
            continue
        name = raw_name.rsplit("__", 1)[-1]
        if name in {"knowledge_write", "citation_fix", "citation_note_revision", "apply_patch"}:
            sequences.append(event.sequence)
            continue
        params = arguments(event)
        command = params.get("cmd", params.get("command")) if params is not None else None
        if not isinstance(command, str):
            continue
        try:
            heads = command_heads(command)
        except ValueError as error:
            raise AuthorityError("native authoring command could not be parsed") from error
        for head in heads:
            cli = Path(head[0]).name == "agents-remember" or head[1:3] in (
                ["-m", "agents_remember"],
                ["-m", "agents_remember.cli"],
            )
            if cli and "knowledge-ingest" in head[1:]:
                sequences.append(event.sequence)
                break
    return tuple(sequences)


def _record_state(snapshot: Snapshot, subject: str) -> dict[str, Any] | None:
    evidence = snapshot.records.get(subject)
    if evidence is None:
        return None
    raw = read_object(evidence.verify(snapshot.root))
    # Admission/attribution metadata does not change a record's meaning. A
    # revision or status change still needs a judgment even if its prose did
    # not change, and absence remains distinct from any existing record.
    return {
        "meaning": meaning(raw),
        "revision": raw.get("revision"),
        "status": raw.get("status"),
    }


def _changed_subjects(before: Snapshot, after: Snapshot) -> set[str]:
    records = before.records.keys() | after.records.keys()
    changed = {
        subject
        for subject in records
        if _record_state(before, subject) != _record_state(after, subject)
    }
    changed.update(
        subject for subject in records if before.entries.get(subject) != after.entries.get(subject)
    )
    return changed


def _row_subjects(before: Snapshot, after: Snapshot) -> set[str]:
    subjects = set()
    for path, history in after.histories.items():
        old = before.histories.get(path)
        for row in history.rows:
            if isinstance(row, (InvariantRow, FamilyRow)) and (
                old is None or old.row_about(row.subject) != row
            ):
                subjects.add(row.subject)
    return subjects


def _observe(
    subject: str,
    before: Snapshot,
    after: Snapshot,
    capture: NativeCapture,
    expected: ExpectedJudgment | None,
) -> Grade:
    old = before.records.get(subject)
    current = after.records.get(subject)
    if current is None:
        return Grade.produced(
            __file__,
            "GT2",
            "fail" if old is not None else "unknown",
            (f"existing subject record removed: {old.path}",)
            if old is not None
            else (f"record for changed history subject not captured: {subject}",),
        )
    histories = [
        (path, history)
        for path, history in after.histories.items()
        if isinstance(history.row_about(subject), (InvariantRow, FamilyRow))
    ]
    if not histories:
        if expected is not None:
            return Grade.produced(
                __file__,
                "GT2",
                "fail",
                (
                    expected.reference,
                    f"stated outcome requires {subject} history disposition "
                    f"{expected.disposition} at revision {expected.revision}; no row was captured",
                ),
            )
        return Grade.produced(
            __file__,
            "GT2",
            "fail" if old is not None else "pass",
            (f"changed existing subject has no captured judgment row: {subject}",) if old else (),
        )
    changed = [
        (path, history)
        for path, history in histories
        if path not in before.histories
        or before.histories[path].row_about(subject) != history.row_about(subject)
    ]
    candidates = changed or histories
    if len({history.owner_id for _, history in candidates}) > 1:
        return Grade.produced(
            __file__, "GT2", "unknown", (f"multiple current history owners for {subject}",)
        )
    path, history = max(candidates, key=lambda item: item[1].attempt_number)
    row = history.row_about(subject)
    if expected is not None and (
        getattr(row, "revision", None) != expected.revision
        or getattr(row, "disposition", None) != expected.disposition
    ):
        return Grade.produced(
            __file__,
            "GT2",
            "fail",
            (
                expected.reference,
                f"stated outcome requires {subject} history disposition "
                f"{expected.disposition} at revision {expected.revision}; actual row differs",
            ),
        )
    prior = tuple(
        after.files[name]
        for name, candidate in after.histories.items()
        if candidate.owner_id == history.owner_id
        and candidate.attempt_number < history.attempt_number
    )
    members = tuple(
        item for name, item in after.records.items() if name.startswith("INV-") and name != subject
    )
    grade = grade_history(
        HistoryObservation(
            subject,
            before.root,
            after.root,
            current.path,
            old,
            current,
            after.files[path],
            capture,
            _authoring_calls(capture),
            before.sidecars,
            after.sidecars,
            members,
            before_history=before.files.get(path),
            prior_history_files=prior,
        )
    )
    return Grade.produced(
        __file__,
        grade.grader_id,
        grade.status,
        (*grade.evidence, *((expected.reference,) if expected is not None else ())),
        grade.questions,
    )


def grade_case_history(case: CaseBundle, attempt: dict, root: Path) -> Grade:
    """Check observable rows, without inferring authority from outcome prose."""
    required = {
        item["destination"]
        for item in case.manifest.get("actorInputs", ())
        if _kind(item["destination"]) is not None
    }
    captured = {item["path"] for item in attempt["before"]}
    missing = sorted(required - captured)
    if missing:
        return Grade.produced(
            __file__,
            "GT2",
            "unknown",
            tuple("case input not captured before: " + path for path in missing),
        )
    try:
        expected = _expected_judgment(case)
        before = Snapshot.read(root / "before", attempt["before"])
    except (OSError, AuthorityError, KeyError, TypeError, ValueError) as error:
        return Grade.produced(
            __file__, "GT2", "unknown", (f"starting case artifacts could not be observed: {error}",)
        )
    try:
        after = Snapshot.read(
            root / "after", attempt["after"], frozenset(item.path for item in before.sidecars)
        )
        capture = _capture(attempt["capture"])
        for path, history in before.histories.items():
            if history.closed and before.files[path] != after.files.get(path):
                return Grade.produced(
                    __file__, "GT2", "fail", (f"closed history was changed or removed: {path}",)
                )
        subjects = _changed_subjects(before, after) | _row_subjects(before, after)
        if expected is not None:
            subjects.add(expected.subject)
        grades = [
            _observe(
                subject,
                before,
                after,
                capture,
                expected if expected is not None and expected.subject == subject else None,
            )
            for subject in sorted(subjects)
        ]
    except (OSError, AuthorityError, KeyError, TypeError) as error:
        return Grade.produced(__file__, "GT2", "unknown", (str(error),))
    except ValueError as error:
        return Grade.produced(
            __file__, "GT2", "fail", (f"captured history/record contradiction: {error}",)
        )
    status = (
        "fail"
        if any(grade.status == "fail" for grade in grades)
        else "unknown"
        if any(grade.status == "unknown" for grade in grades)
        else "pass"
    )
    return Grade.produced(
        __file__,
        "GT2",
        status,
        tuple(ref for grade in grades for ref in grade.evidence),
        tuple(question for grade in grades for question in grade.questions),
    )
