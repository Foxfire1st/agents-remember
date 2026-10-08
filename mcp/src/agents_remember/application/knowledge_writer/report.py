"""What one writer operation reports: every file it wrote, or every reason it wrote nothing.

The report is the product (as for ``knowledge-ingest`` today). A written or planned operation lists
each record, entry and history row with its ID and what happened to it, the evidence of every entry
and where it is stored, and the validator's report-only findings (family coverage among them, MIK-R22
rule 5). A refused operation lists every problem and every refusing violation, and writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agents_remember.application.knowledge_writer.handoff import Problem
from agents_remember.memory_quality.knowledge_validator.report import Violation

Action = Literal["created", "updated", "unchanged", "removed"]
WriteState = Literal["written", "planned", "refused"]
EvidenceState = Literal["proof_written", "needs_facet", "unresolvable"]
PROVISIONAL_IDS_NOTE = (
    "this is a planning run: the IDs (and the file names holding them) of the records, entries "
    "and rows it would create are provisional; the committing run mints its own"
)


@dataclass(frozen=True)
class RecordOutcome:
    kind: str
    id: str
    path: str
    action: Action
    revision: int
    handoff_entry: str


@dataclass(frozen=True)
class EntryOutcome:
    kind: Literal["realization", "proof"]
    id: str
    invariant: str
    sidecar: str
    action: Action
    handoff_entry: str


@dataclass(frozen=True)
class RowOutcome:
    id: str
    subject: str
    disposition: str
    history: str


@dataclass(frozen=True)
class CitedTest:
    """One test an entry's evidence names, and what became of it."""

    test: str
    state: EvidenceState
    detail: str


@dataclass(frozen=True)
class EvidenceOutcome:
    """An entry's evidence: the record whose ``origin.handoff`` holds it, and the tests it names."""

    entry: str
    evidence: tuple[str, ...]
    stored_in: tuple[str, ...]
    tests: tuple[CitedTest, ...] = ()


@dataclass(frozen=True)
class EndpointOutcome:
    """A requirement endpoint one of this run's records links, and the owner's answer (MIK-R13).

    ``state`` is ``resolved`` or ``unresolved``; an unresolved endpoint is reported, never refused.
    """

    record: str
    field: str
    relation: str
    endpoint: str
    state: str
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class WriteReport:
    state: WriteState
    owner: str
    memory_root: str
    code_tree: str
    records: tuple[RecordOutcome, ...] = ()
    entries: tuple[EntryOutcome, ...] = ()
    rows: tuple[RowOutcome, ...] = ()
    evidence: tuple[EvidenceOutcome, ...] = ()
    rulings: tuple[str, ...] = ()
    problems: tuple[Problem, ...] = ()
    violations: tuple[Violation, ...] = ()
    written: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())
    authorization: str = ""
    carried: tuple[str, ...] = ()
    requirements: tuple[EndpointOutcome, ...] = ()

    @property
    def refused(self) -> bool:
        return self.state == "refused"

    @property
    def provisional_ids(self) -> bool:
        """Whether this report shows IDs that no file holds: a planning run that would create.

        IDs are drawn at random on each run, so the IDs a planning run prints for the records,
        entries and rows it *would create* are not the ones the committing run will mint.
        """

        return self.state == "planned" and (
            any(one.action == "created" for one in (*self.records, *self.entries))
            or bool(self.rows)
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "operation": "knowledge-write",
            "state": self.state,
            "owner": self.owner,
            "memoryRoot": self.memory_root,
            "codeTree": self.code_tree,
            "records": [_record(one) for one in self.records],
            "entries": [_entry(one) for one in self.entries],
            "historyRows": [_row(one) for one in self.rows],
            "evidence": [_evidence(one) for one in self.evidence],
            "rulings": list(self.rulings),
            "problems": [{"where": one.where, "message": one.message} for one in self.problems],
            "violations": [one.to_document() for one in self.violations],
            "written": list(self.written),
            "removed": list(self.removed),
            "notes": list(self.notes),
            "authorization": self.authorization,
            "carried": list(self.carried),
            "requirementEndpoints": [_endpoint(one) for one in self.requirements],
            "provisionalIds": self.provisional_ids,
        }

    def render(self) -> str:
        lines = [f"knowledge-write {self.state}: {self.owner} into {self.memory_root}"]
        if self.authorization:
            lines.append(f"  authorization: {self.authorization}")
        lines += [f"  problem: {one.render()}" for one in self.problems]
        lines += [f"  violation: {one.render()}" for one in self.violations]
        lines += [
            f"  {one.kind} {one.id} {one.action} (revision {one.revision}) at {one.path}"
            for one in self.records
        ]
        lines += [
            f"  {one.kind} {one.id} of {one.invariant} {one.action} in {one.sidecar}"
            for one in self.entries
        ]
        lines += [f"  row {one.id} {one.subject} {one.disposition}" for one in self.rows]
        lines += [
            f"  carried {one}: blob re-recorded at C (content unchanged)" for one in self.carried
        ]
        for one in self.evidence:
            where = ", ".join(one.stored_in) or "nothing: the entry authored no record"
            lines.append(f"  evidence of {one.entry} stored in {where}")
            lines += [f"    {test.test}: {test.state} ({test.detail})" for test in one.tests]
        lines += [
            f"  ruling {one}: recorded only through the records naming it" for one in self.rulings
        ]
        verb = "would write" if self.state == "planned" else "wrote"
        lines += [f"  {verb} {one}" for one in self.written]
        lines += [
            f"  {verb.replace('write', 'remove').replace('wrote', 'removed')} {one}"
            for one in self.removed
        ]
        lines += map(_endpoint_line, self.requirements)
        lines += [f"  note: {one}" for one in self.notes]
        if self.provisional_ids:
            lines.append(f"  note: {PROVISIONAL_IDS_NOTE}")
        return "\n".join(lines)


def _record(one: RecordOutcome) -> dict[str, Any]:
    return {
        "kind": one.kind,
        "id": one.id,
        "path": one.path,
        "action": one.action,
        "revision": one.revision,
        "handoffEntry": one.handoff_entry,
    }


def _entry(one: EntryOutcome) -> dict[str, Any]:
    return {
        "kind": one.kind,
        "id": one.id,
        "invariant": one.invariant,
        "sidecar": one.sidecar,
        "action": one.action,
        "handoffEntry": one.handoff_entry,
    }


def _row(one: RowOutcome) -> dict[str, Any]:
    return {
        "id": one.id,
        "subject": one.subject,
        "disposition": one.disposition,
        "history": one.history,
    }


def _evidence(one: EvidenceOutcome) -> dict[str, Any]:
    return {
        "entry": one.entry,
        "evidence": list(one.evidence),
        "storedIn": list(one.stored_in),
        "tests": [
            {"test": test.test, "state": test.state, "detail": test.detail} for test in one.tests
        ],
    }


def _endpoint(one: EndpointOutcome) -> dict[str, Any]:
    return {
        "record": one.record,
        "field": one.field,
        "relation": one.relation,
        "endpoint": one.endpoint,
        "state": one.state,
        "code": one.code,
        "detail": one.detail,
    }


def _endpoint_line(one: EndpointOutcome) -> str:
    refusal = f" [{one.code}] {one.detail}" if one.code else ""
    return (
        f"  requirement {one.endpoint} from {one.record} {one.field} ({one.relation}): "
        f"{one.state}{refusal}"
    )
