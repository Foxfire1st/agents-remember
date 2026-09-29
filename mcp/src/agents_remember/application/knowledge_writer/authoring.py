"""Turn a read hand-off document into edits of the memory tree, filling every mechanical field.

The curator authors meaning -- statements, scope, admission, facets, links, dispositions, reasons.
Everything else is mechanical and filled here (MIK-R12 rule 2):

* **IDs** are minted (MIK-R21 rule 2) or, on a rerun, reused from the record or entry whose
  ``origin`` names this owner and the same hand-off entry (MIK-R21 rules 4 and 5), so the same list
  produces the same files with the same IDs;
* **anchors** are resolved at C and record ``blob`` and ``content`` (:mod:`.code_anchors`);
* **revisions** increment once per leaf when a record's meaning changes against the base: every field
  except ``id``, ``schema``, ``origin``, ``revision``, ``admission`` and ``status`` is meaning
  (MIK-R07 rule 3: ``admission``, ``origin`` and the slug carry none);
* **origin** names the task, the leaf or wave, the hand-off list and entry, and the entry's evidence
  (MIK-R12 rule 3: evidence is never dropped);
* **history rows** get their ID, the invariant's revision, each examined member's revision, and each
  covered entry's ``before`` (its anchor in the base) and ``after`` anchor, which is written into the
  entry in the same operation (MIK-R07 rule 4).

Nothing here judges meaning, and nothing is written to disk: the writer validates the result first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.knowledge_writer.handoff import (
    HANDLE_PREFIX,
    CoverRequest,
    EntryRequest,
    HandoffDocument,
    Problem,
    ProofRequest,
    RecordRequest,
    RowRequest,
    TargetRequest,
    TestFileMention,
    TestReference,
    read_locator,
)
from agents_remember.application.knowledge_writer.memory_state import (
    EntryLocation,
    MemoryState,
    Owner,
    canonical_equal,
    deep_copy,
)
from agents_remember.application.knowledge_writer.report import (
    Action,
    CitedTest,
    EntryOutcome,
    EvidenceOutcome,
    RecordOutcome,
    RowOutcome,
)
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.models.knowledge_files.history import (
    HISTORY_SCHEMA,
    InvariantRow,
    OnboardingTraceRow,
    PlannedEffectRow,
)
from agents_remember.models.knowledge_files.ids import (
    RECORD_ID_PATTERN,
    EntryKind,
    RecordKind,
    RowKind,
    mint_id,
)
from agents_remember.models.knowledge_files.records import schema_name

DecisionResolver = Callable[[str], str | None]
"""The task owner's answer about one decision entry of the leaf's task document: ``None`` when it
resolves, else why not (MIK-R11 rule 5, ``dropped``)."""

NON_MEANING_FIELDS: Final = frozenset({"id", "schema", "origin", "revision", "admission", "status"})
ABSENT: Final = "absent"
_SLUG_WORDS: Final = 6


def meaning(document: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a record that carry meaning; a change to any of them is a new revision."""

    return {key: value for key, value in document.items() if key not in NON_MEANING_FIELDS}


def slug_of(text: str, fallback: str) -> str:
    """A display slug from the first words of ``text``: lowercase, ``-`` separated."""

    words = re.findall(r"[a-z0-9]+", text.lower())[:_SLUG_WORDS]
    return "-".join(words)[:60].strip("-") or fallback


@dataclass
class Authoring:
    """One operation's edits: what it placed, what it reported, and every problem it found."""

    state: MemoryState
    code: CodeSnapshot
    owner: Owner
    handoff_path: str
    problems: list[Problem] = field(default_factory=list)
    handles: dict[str, str] = field(default_factory=dict)
    records: list[RecordOutcome] = field(default_factory=list)
    entries: list[EntryOutcome] = field(default_factory=list)
    rows: list[RowOutcome] = field(default_factory=list)
    evidence: list[EvidenceOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _taken: set[str] = field(default_factory=set)
    _stored_in: dict[str, list[str]] = field(default_factory=dict)
    # Evidence for records another owner authored: stored in this owner's history row about the
    # record (ruling R2-1), keyed by record ID.
    _foreign_evidence: dict[str, list[tuple[str, tuple[str, ...]]]] = field(default_factory=dict)
    # The task owner's decision resolution for planned ``dropped`` rows; ``None`` has no task owner.
    decisions: DecisionResolver | None = None

    # -- the operation ------------------------------------------------------------------------

    def run(self, document: HandoffDocument) -> None:
        self._taken = self.state.known_ids()
        self._assign_ids(document)
        tests: dict[str, tuple[CitedTest, ...]] = {}
        for entry in document.entries:
            tests[entry.entry_id] = self._write_entry(entry)
        by_entry = {entry.entry_id: entry for entry in (*document.entries, *document.rulings)}
        for record in document.records:
            self._write_record(record, by_entry.get(record.entry or ""))
        if document.rows:
            self._write_rows(document.rows)
        for record_id, pending in sorted(self._foreign_evidence.items()):
            for handoff_entry, _evidence in pending:
                self.problem(
                    f"entry {handoff_entry}",
                    f"{record_id} was authored by another leaf or wave, so its origin is kept and "
                    "this entry's evidence is stored in this leaf's history row about it: add a "
                    f"row with subject {record_id} to 'history'",
                )
        for entry in (*document.entries, *document.rulings):
            if entry.evidence or tests.get(entry.entry_id):
                self.evidence.append(
                    EvidenceOutcome(
                        entry=entry.entry_id,
                        evidence=entry.evidence,
                        stored_in=tuple(self._stored_in.get(entry.entry_id, ())),
                        tests=tests.get(entry.entry_id, ()),
                    )
                )

    def problem(self, where: str, message: str) -> None:
        self.problems.append(Problem(where, message))

    def mint(self, kind: RecordKind | EntryKind | RowKind) -> str:
        while True:
            minted = mint_id(kind)
            if minted not in self._taken:
                self._taken.add(minted)
                self.state.minted.add(minted)
                return minted

    # -- identities ---------------------------------------------------------------------------

    def _assign_ids(self, document: HandoffDocument) -> None:
        for entry in document.entries:
            if entry.invariant_id is not None:
                self._require_kind(entry.invariant_id, "invariant", f"entry {entry.entry_id}")
                self.handles[entry.entry_id] = entry.invariant_id
                continue
            self.handles[entry.entry_id] = self.state.record_by_origin(
                "invariant", self.owner, entry.entry_id
            ) or self.mint("invariant")
        claimed = {("invariant", entry.entry_id) for entry in document.entries}
        for record in document.records:
            where = f"record {record.key}"
            if (record.kind, record.handoff_entry) in claimed:
                self.problem(
                    where, f"a second {record.kind} from hand-off entry {record.handoff_entry}"
                )
            claimed.add((record.kind, record.handoff_entry))
            if record.record_id is not None:
                self._require_kind(record.record_id, record.kind, where)
                self.handles[record.key] = record.record_id
                continue
            self.handles[record.key] = self.state.record_by_origin(
                record.kind, self.owner, record.handoff_entry
            ) or self.mint(record.kind)

    def _require_kind(self, record_id: str, kind: RecordKind, where: str) -> None:
        found = self.state.record(record_id)
        if found is None:
            self.problem(where, f"no stored record {record_id} to update")
        elif found[1] != kind:
            self.problem(where, f"{record_id} is a {found[1]} record, not a {kind}")

    def resolve_id(self, value: str, where: str) -> str | None:
        """An ID as written: a record ID, ``handoff:<key>``, or a bare hand-off entry id."""

        if re.match(RECORD_ID_PATTERN, value):
            return value
        key = value.removeprefix(HANDLE_PREFIX)
        resolved = self.handles.get(key) or self.state.record_by_origin(
            "invariant", self.owner, key
        )
        if resolved is None:
            self.problem(where, f"{value!r} names no record of this hand-off or this owner")
        return resolved

    def resolve(self, value: Any, where: str) -> Any:
        """Replace every ``handoff:<key>`` string in ``value`` with the ID it names."""

        if isinstance(value, str) and value.startswith(HANDLE_PREFIX):
            return self.resolve_id(value, where) or value
        if isinstance(value, Mapping):
            return {key: self.resolve(item, where) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, where) for item in value]
        return value

    # -- records ------------------------------------------------------------------------------

    def _origin(
        self,
        stored: Mapping[str, Any] | None,
        handoff_entry: str,
        evidence: Sequence[str],
    ) -> tuple[dict[str, Any], bool]:
        """The record's origin, and whether ``evidence`` is stored in it.

        A record this owner authored keeps its origin and gains this run's evidence. A record another
        leaf or wave authored keeps its origin exactly: its ``handoff`` is that owner's list, so an
        updater's evidence is never written there; the report names it for the updater's history
        row instead.
        """

        if stored is not None and isinstance(stored.get("origin"), Mapping):
            origin = deep_copy(stored["origin"])
            if not evidence:
                return origin, False
            if (
                origin.get(self.owner.kind) != self.owner.id
                or origin.get("task") != self.owner.task
            ):
                self._foreign_evidence.setdefault(str(stored.get("id")), []).append(
                    (handoff_entry, tuple(evidence))
                )
                return origin, False
            handoff = dict(origin.get("handoff") or {})
            handoff.setdefault("path", self.handoff_path)
            handoff["evidence"] = list(dict.fromkeys([*handoff.get("evidence", []), *evidence]))
            origin["handoff"] = handoff
            return origin, True
        handoff: dict[str, Any] = {"path": self.handoff_path}
        if evidence:
            handoff["evidence"] = list(dict.fromkeys(evidence))
        origin = {**self.owner.origin(), "handoff": handoff, "handoffEntry": handoff_entry}
        return origin, bool(evidence)

    def _place_record(
        self,
        kind: RecordKind,
        document: dict[str, Any],
        *,
        slug: str | None,
        before: Mapping[str, Any] | None,
        handoff_entry: str,
    ) -> None:
        record_id = str(document["id"])
        reference = self.state.base_record(record_id)
        if reference is None and self.state.base is None:
            reference = before
        if reference is None:
            revision = 1
        else:
            base_revision = reference.get("revision")
            revision = (base_revision if isinstance(base_revision, int) else 1) + (
                0 if canonical_equal(meaning(reference), meaning(document)) else 1
            )
        document["revision"] = revision
        path = self.state.put_record(kind, record_id, slug, document)
        action: Action = (
            "created"
            if before is None
            else "unchanged"
            if canonical_equal(before, document) and path in self.state.files
            else "updated"
        )
        self.records.append(RecordOutcome(kind, record_id, path, action, revision, handoff_entry))

    def _write_entry(self, entry: EntryRequest) -> tuple[CitedTest, ...]:
        invariant_id = self.handles.get(entry.entry_id)
        if invariant_id is None:
            return ()
        found = self.state.record(invariant_id)
        before = deep_copy(found[2]) if found is not None else None
        self._place_record(
            "invariant",
            self._invariant_document(entry, invariant_id, before),
            slug=None if before is not None else slug_of(entry.statement, "invariant"),
            before=before,
            handoff_entry=entry.entry_id,
        )
        seen: set[str] = set()
        for target in entry.targets:
            key = json.dumps([target.path, dict(target.locator.document)], sort_keys=True)
            if key in seen:
                self.problem(
                    f"entry {entry.entry_id}", f"target {target.position} names {target.path} twice"
                )
                continue
            seen.add(key)
            self._write_realization(entry, invariant_id, target)
        proven = {
            proof.test: self._write_proof(entry, invariant_id, proof) for proof in entry.proofs
        }
        self._remove_unnamed_entries(entry.entry_id, invariant_id)
        return tuple(self._cited_test(entry, test, proven) for test in entry.cited_tests())

    def _invariant_document(
        self, entry: EntryRequest, invariant_id: str, before: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """The invariant an entry authors: its verbatim statement and the curator's scope."""

        document: dict[str, Any] = (
            deep_copy(before)
            if before is not None
            else {
                "schema": schema_name("invariant"),
                "id": invariant_id,
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
            }
        )
        document["statement"] = entry.statement
        if entry.scope is not None:
            document["applicability"] = entry.scope.applicability
            document["conditions"] = list(entry.scope.conditions)
            document["exclusions"] = list(entry.scope.exclusions)
        if entry.admission is not None:
            document["admission"] = entry.admission
        document["status"] = entry.status or document.get("status") or "proposed"
        if entry.supersedes:
            resolved = [self.resolve_id(one, f"entry {entry.entry_id}") for one in entry.supersedes]
            document["supersedes"] = list(dict.fromkeys(one for one in resolved if one))
        document["origin"], stored = self._origin(before, entry.entry_id, entry.evidence)
        if stored:
            self._stored_in.setdefault(entry.entry_id, []).append(invariant_id)
        return document

    def _write_record(self, request: RecordRequest, entry: EntryRequest | None) -> None:
        where = f"record {request.key}"
        record_id = self.handles.get(request.key)
        if record_id is None:
            return
        found = self.state.record(record_id)
        before = deep_copy(found[2]) if found is not None else None
        fields = self.resolve(dict(request.fields), where)
        if isinstance(fields.get("links"), list):
            fields["links"] = [self._link(link, f"{where}.links") for link in fields["links"]]
        document: dict[str, Any] = (
            deep_copy(before)
            if before is not None
            else {"schema": schema_name(request.kind), "id": record_id}
        )
        document.update(fields)
        if "status" not in document:
            document["status"] = "active" if request.kind == "decision" else "proposed"
        evidence = entry.evidence if entry is not None else ()
        document["origin"], stored = self._origin(before, request.handoff_entry, evidence)
        self._place_record(
            request.kind,
            document,
            slug=request.slug,
            before=before,
            handoff_entry=request.handoff_entry,
        )
        if stored and request.entry is not None:
            self._stored_in.setdefault(request.entry, []).append(record_id)

    def _link(self, link: Any, where: str) -> Any:
        """A link whose target is ``{path, locator}`` gets its anchor resolved at C."""

        if not isinstance(link, Mapping) or not isinstance(link.get("target"), Mapping):
            return link
        target = link["target"]
        if "locator" not in target or "blob" in target:
            return link
        locator = read_locator(target.get("locator"))
        path = target.get("path")
        if isinstance(locator, str) or not isinstance(path, str):
            self.problem(where, f"an anchor target names 'path' and a locator: {locator}")
            return link
        try:
            anchor = self.code.resolve(path, locator.document)
        except AnchorResolutionError as error:
            self.problem(where, f"{path} ({locator.describe()}): {error}")
            return link
        return {**link, "target": {**anchor.to_document(), "path": path}}

    # -- realization and proof entries --------------------------------------------------------

    def _upsert_entry(
        self,
        *,
        kind: EntryKind,
        source_path: str,
        invariant_id: str,
        handoff_entry: str,
        fields: dict[str, Any],
    ) -> None:
        list_key = "realizes" if kind == "realization" else "proves"
        sidecar_path, sidecar = self.state.file_sidecar(source_path)
        entries = sidecar.setdefault(list_key, [])
        locator = fields["anchor"]["locator"]
        index, existing = next(
            (
                (index, one)
                for index, one in enumerate(entries)
                if one.get("invariant") == invariant_id
                and self.owner.authored(one, handoff_entry)
                and canonical_equal((one.get("anchor") or {}).get("locator"), locator)
            ),
            (None, None),
        )
        entry = {
            "id": existing["id"] if existing is not None else self.mint(kind),
            "invariant": invariant_id,
            **fields,
            "origin": {"leaf": self.owner.id, "handoffEntry": handoff_entry},
        }
        if index is None:
            entries.append(entry)
        else:
            entries[index] = entry
        self.state.touch(sidecar_path)
        action: Action = (
            "created"
            if existing is None
            else "unchanged"
            if canonical_equal(existing, entry)
            else "updated"
        )
        self.entries.append(
            EntryOutcome(kind, entry["id"], invariant_id, sidecar_path, action, handoff_entry)
        )

    def _remove_unnamed_entries(self, handoff_entry: str, invariant_id: str) -> None:
        """Remove what this owner wrote from ``handoff_entry`` earlier and this run no longer names.

        Only entries whose origin names this owner and this hand-off entry are candidates, so an
        entry another leaf or wave authored is never removed.
        """

        written = {
            one.id
            for one in self.entries
            if one.handoff_entry == handoff_entry and one.invariant == invariant_id
        }
        for entry_id in self.state.entries_by_origin(self.owner, handoff_entry, invariant_id):
            if entry_id in written:
                continue
            located = self.state.find_entry(entry_id)
            if located is None:
                continue
            kind: EntryKind = "realization" if located.entries == "realizes" else "proof"
            self.state.remove_entry(entry_id)
            self.entries.append(
                EntryOutcome(
                    kind, entry_id, invariant_id, located.sidecar, "removed", handoff_entry
                )
            )

    def _write_realization(
        self, entry: EntryRequest, invariant_id: str, target: TargetRequest
    ) -> None:
        try:
            anchor = self.code.resolve(target.path, target.locator.document)
        except AnchorResolutionError as error:
            self.problem(
                f"entry {entry.entry_id}",
                f"target {target.position} ({target.path}, {target.locator.describe()}): {error}",
            )
            return
        self._upsert_entry(
            kind="realization",
            source_path=target.path,
            invariant_id=invariant_id,
            handoff_entry=entry.entry_id,
            fields={
                "anchor": anchor.to_document(),
                "role": target.role,
                "rationale": target.rationale,
            },
        )

    def _write_proof(self, entry: EntryRequest, invariant_id: str, proof: ProofRequest) -> str:
        locator = {"kind": "symbol", "name": proof.test.name}
        try:
            anchor = self.code.resolve(proof.test.path, locator)
        except AnchorResolutionError as error:
            self.problem(
                f"entry {entry.entry_id}",
                f"proof {proof.position} ({proof.test.spelling}): {error}",
            )
            return str(error)
        self._upsert_entry(
            kind="proof",
            source_path=proof.test.path,
            invariant_id=invariant_id,
            handoff_entry=entry.entry_id,
            fields={"anchor": anchor.to_document(), "facet": proof.facet},
        )
        return f"proof {self.entries[-1].id} in {self.entries[-1].sidecar}"

    def _cited_test(
        self,
        entry: EntryRequest,
        test: TestReference | TestFileMention,
        proven: Mapping[Any, str],
    ) -> CitedTest:
        """What became of one test the evidence names: proved, awaiting a facet, or unresolvable."""

        if isinstance(test, TestFileMention):
            return CitedTest(
                test.spelling,
                "unresolvable",
                "the evidence names this test file but no test in it; write "
                f"'{test.path}::<name>' (or '{test.path} -k <name>') to prepare a proof",
            )
        if test in proven:
            return CitedTest(test.spelling, "proof_written", proven[test])
        try:
            self.code.resolve(test.path, {"kind": "symbol", "name": test.name})
        except AnchorResolutionError as error:
            return CitedTest(test.spelling, "unresolvable", str(error))
        return CitedTest(
            test.spelling,
            "needs_facet",
            "the test resolves at C; the curator authors its facet in 'proofs' to record it "
            f"(draft from the statement: {entry.statement[:200]!r})",
        )

    # -- history rows -------------------------------------------------------------------------

    def _write_rows(self, requests: Sequence[RowRequest]) -> None:
        path = history_path(self.owner.id)
        stored = self.state.document(path)
        if stored is not None and stored.get("closed") is True:
            self.problem(path, "this history file is closed and frozen (MIK-R07 rule 7)")
            return
        document = (
            deep_copy(stored)
            if stored is not None
            else {
                "schema": HISTORY_SCHEMA,
                self.owner.kind: self.owner.id,
                "closed": False,
                "rows": [],
            }
        )
        rows = {row["subject"]: row for row in document.get("rows") or () if isinstance(row, dict)}
        for request in requests:
            row = self._row(request, rows)
            if row is not None:
                rows[row["subject"]] = row
                self.rows.append(RowOutcome(row["id"], row["subject"], row["disposition"], path))
        document["rows"] = list(rows.values())
        self.state.put(path, document)

    def _row(self, request: RowRequest, rows: Mapping[str, Any]) -> dict[str, Any] | None:
        where = f"history[{request.position}]"
        if re.match(OnboardingTraceRow.subject_pattern, request.subject):
            return self._onboarding_row(request, rows.get(request.subject), where)
        if re.match(PlannedEffectRow.subject_pattern, request.subject):
            return self._planned_row(request, rows.get(request.subject), where)
        subject = self.resolve_id(request.subject, where)
        found = None if subject is None else self.state.record(subject)
        if subject is None or found is None:
            self.problem(
                where, f"the subject {request.subject!r} names no record (unknown subject)"
            )
            return None
        existing = rows.get(subject)
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": subject,
            "disposition": request.disposition,
            "reason": self._reason_with_evidence(subject, request.reason),
            "items": list(request.items),
        }
        if found[1] == "invariant":
            return self._invariant_row(request, row, found[2], where)
        if found[1] == "family":
            return self._family_row(request, row, where)
        self.problem(where, f"no registered history row kind has a {found[1]} subject")
        return None

    def _onboarding_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """An ``onboarding_trace`` row (MIK-R30): a reviewed no-impact judgment about a card or
        route overview. It carries no covers, effect, because or examined members. Marker lines a
        crossing sync moved into an existing row (MIK-R24 rule 8 step 1) are kept."""

        extra = [
            name
            for name, value in (
                ("covers", request.covers),
                ("effect", request.effect),
                ("because", request.because),
                ("examined", request.examined),
            )
            if value
        ]
        if extra:
            self.problem(where, f"an onboarding_trace row carries no {extra}")
            return None
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": request.subject,
            "disposition": request.disposition,
            "reason": request.reason,
            "items": list(request.items),
        }
        if existing is not None and existing.get("markers"):
            row["markers"] = list(existing["markers"])
        try:
            OnboardingTraceRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return row

    def _planned_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """A planned row (MIK-R11 rule 5): the disposition of a declared effect no row delivered.

        It carries ``ref`` and nothing an invariant or family row carries. What ``ref`` names must
        exist: a stored row or invariant for ``realized_elsewhere``, and for ``dropped`` a decision
        entry of the leaf's task document, which the task owner resolves now -- an unresolved one
        refuses the row.
        """

        extra = [
            name
            for name, value in (
                ("covers", request.covers),
                ("effect", request.effect),
                ("because", request.because),
                ("examined", request.examined),
            )
            if value
        ]
        if extra or request.ref is None:
            self.problem(
                where,
                f"a planned row carries 'ref' and no {extra}"
                if extra
                else "a planned row carries 'ref'",
            )
            return None
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": request.subject,
            "disposition": request.disposition,
            "reason": request.reason,
            "items": list(request.items),
            "ref": dict(request.ref),
        }
        try:
            model = PlannedEffectRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        unresolved = self._unresolved_ref(model)
        if unresolved is not None:
            self.problem(where, unresolved)
            return None
        return row

    def _unresolved_ref(self, row: PlannedEffectRow) -> str | None:
        ref = row.ref
        if ref.decision is not None:
            if self.decisions is None:
                return (
                    "a dropped planned row cites a decision of the leaf's task document, and this "
                    "write has no task owner to resolve it"
                )
            detail = self.decisions(ref.decision)
            return (
                None if detail is None else f"the dropped row's decision does not resolve: {detail}"
            )
        if ref.invariant is not None:
            found = self.state.record(ref.invariant)
            if found is None or found[1] != "invariant":
                return f"ref.invariant {ref.invariant} names no stored invariant"
        if ref.row is not None and ref.row not in self.state.known_ids():
            return f"ref.row {ref.row} names no history row"
        return None

    def _reason_with_evidence(self, subject: str, reason: str) -> str:
        """The row's reason, with the evidence of this run's updates of another owner's record."""

        pending = self._foreign_evidence.pop(subject, [])
        for handoff_entry, evidence in pending:
            reason = f"{reason} Evidence ({handoff_entry}): {'; '.join(evidence)}"
            self._stored_in.setdefault(handoff_entry, []).append(
                f"{history_path(self.owner.id)}#{subject}"
            )
        return reason

    def _invariant_row(
        self,
        request: RowRequest,
        row: dict[str, Any],
        record: Mapping[str, Any],
        where: str,
    ) -> dict[str, Any] | None:
        moving = request.disposition == "moved"
        row["covers"] = [
            cover
            for one in request.covers
            for cover in self._covers(one, row["subject"], f"{where}.covers", moving=moving)
        ]
        row["revision"] = record.get("revision")
        if request.effect is not None:
            row["effect"] = request.effect
        if request.because:
            row["because"] = [self.resolve(one, where) for one in request.because]
        try:
            InvariantRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return row

    def _family_row(
        self, request: RowRequest, row: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        if not request.examined:
            self.problem(where, "a family row names every member the curator examined")
            return None
        examined = []
        for one in request.examined:
            member = self.resolve_id(one, where)
            found = None if member is None else self.state.record(member)
            if member is None or found is None or found[1] != "invariant":
                self.problem(where, f"examined member {one!r} is not a stored invariant")
                continue
            examined.append({"id": member, "revision": found[2].get("revision")})
        row["examined"] = examined
        return row

    def _covers(
        self, request: CoverRequest, subject: str, where: str, *, moving: bool = False
    ) -> list[dict[str, Any]]:
        if request.path is not None and not moving:
            self.problem(where, "a cover names another source path only on a moved row")
            return []
        if request.handoff is not None:
            identified = self.state.entries_by_origin(self.owner, request.handoff, subject)
            if not identified:
                self.problem(where, f"no entry of {subject} was written from {request.handoff!r}")
            covers = [self._cover(one, None, subject, where, reanchor=False) for one in identified]
        else:
            covers = [self._cover(str(request.entry_id), request, subject, where, reanchor=True)]
        return [cover for cover in covers if cover is not None]

    def _cover(
        self,
        entry_id: str,
        request: CoverRequest | None,
        subject: str,
        where: str,
        *,
        reanchor: bool,
    ) -> dict[str, Any] | None:
        before: Any = self.state.base_entry_anchor(entry_id) or ABSENT
        removing = request is not None and request.remove
        located = self.state.find_entry(entry_id)
        if located is None:
            return self._cover_of_absent(entry_id, before, removing=removing, where=where)
        if located.document.get("invariant") != subject:
            self.problem(where, f"entry {entry_id} realizes or proves another invariant")
            return None
        after = self._cover_after(located, request, where, reanchor=reanchor)
        if after is None or (before == ABSENT and after == ABSENT):
            return None
        return {"id": entry_id, "before": before, "after": after}

    def _cover_of_absent(
        self, entry_id: str, before: Any, *, removing: bool, where: str
    ) -> dict[str, Any] | None:
        """An entry the candidate no longer holds: a rerun of its removal, or an error."""

        if removing and before != ABSENT:
            return {"id": entry_id, "before": before, "after": ABSENT}
        self.problem(where, f"no entry {entry_id} to cover")
        return None

    def _cover_after(
        self,
        located: EntryLocation,
        request: CoverRequest | None,
        where: str,
        *,
        reanchor: bool,
    ) -> Any:
        """The entry's ``after``: ``absent`` once removed, else its anchor, re-anchored at C first
        when the cover asks for it (``None`` when that fails)."""

        if request is not None and request.remove:
            self.state.remove_entry(str(located.document.get("id")))
            return ABSENT
        locator = request.locator.document if request and request.locator else None
        anchor = located.document["anchor"]
        if request is not None and request.path not in (None, located.source_path):
            located = self._relocate(located, str(request.path))
        if reanchor:
            resolved = self._reanchor(located.source_path, locator or anchor["locator"], where)
            if resolved is None:
                return None
            located.document["anchor"] = anchor = resolved
            self.state.touch(located.sidecar)
        return {**anchor, "path": located.source_path}

    def _relocate(self, located: EntryLocation, path: str) -> EntryLocation:
        """Move the entry into ``path``'s file sidecar (a moved row's ``after``, MIK-R07 rule 4).

        The entry keeps its ID, invariant and authored fields; the caller re-anchors it at C, so
        its ``blob`` and ``content`` are C's.
        """

        self.state.remove_entry(str(located.document.get("id")))
        sidecar_path, sidecar = self.state.file_sidecar(path)
        sidecar.setdefault(located.entries, []).append(located.document)
        self.state.touch(sidecar_path)
        return EntryLocation(sidecar_path, path, located.entries, located.document)

    def _reanchor(self, path: str, locator: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        try:
            return self.code.resolve(path, locator).to_document()
        except AnchorResolutionError as error:
            self.problem(where, f"{path}: {error}")
            return None


def _first_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in one['loc']) or '-'}: {one['msg']}"
        for one in error.errors()
    )
