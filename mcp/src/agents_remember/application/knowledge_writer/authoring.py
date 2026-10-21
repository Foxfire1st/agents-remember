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
* **history rows** get their ID, the invariant's or family's own revision, each examined member's
  revision, and each covered entry's ``before`` (its anchor in the base) and ``after`` anchor,
  which is written into the entry in the same operation (MIK-R07 rule 4).

Nothing here judges meaning, and nothing is written to disk: the writer validates the result first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_writer import case_proof
from agents_remember.application.knowledge_writer.authoring_rows import meaning
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.knowledge_writer.entry_handles import (
    assign_entries,
    ordered_entries,
)
from agents_remember.application.knowledge_writer.handoff import (
    HANDLE_PREFIX,
    EntryRequest,
    HandoffDocument,
    Problem,
    ProofRequest,
    RecordRequest,
    TargetRequest,
    TestFileMention,
    TestRangeReference,
    TestReference,
    read_locator,
)
from agents_remember.application.knowledge_writer.memory_state import (
    MemoryState,
    MergeStageError,
    Owner,
    canonical_equal,
    deep_copy,
)
from agents_remember.application.knowledge_writer.reconsideration import (
    OpenQuestions,
)
from agents_remember.application.knowledge_writer.record_updates import record_origin, remap_links
from agents_remember.application.knowledge_writer.refresh_restoration import restore_refreshes
from agents_remember.application.knowledge_writer.repoint import RepointRowAuthoring
from agents_remember.application.knowledge_writer.report import (
    Action,
    CitedTest,
    EntryOutcome,
    EvidenceOutcome,
    RecordOutcome,
    RowOutcome,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
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

# The record kinds that carry a ``status``; a weakness carries a state and a dependent neither, and
# the writer never adds a field its model refuses (MIK-R118 rule 1, MIK-R124 rule 1).
_STATUS_KINDS: Final = frozenset(
    {
        "invariant",
        "family",
        "decision",
        "incident",
        "assumption",
        "limitation",
        "failure_mode",
        "scenario",
        "diagnostic",
        "term",
    }
)

_SLUG_WORDS: Final = 6


def slug_of(text: str, fallback: str) -> str:
    """A display slug from the first words of ``text``: lowercase, ``-`` separated."""

    words = re.findall(r"[a-z0-9]+", text.lower())[:_SLUG_WORDS]
    return "-".join(words)[:60].strip("-") or fallback


def _rev_parse(root: Path, revision: str) -> str | None:
    """One revision's object id at ``root``, or ``None`` when it cannot be read."""

    result = run_git(
        root,
        ["rev-parse", "--verify", "--quiet", revision],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    shown = result.stdout.strip()
    return shown if result.returncode == 0 and shown else None


def code_state_of(snapshot: CodeSnapshot) -> tuple[str, str, bool] | None:
    """``(commit, tree, uncommitted)`` for what a code snapshot's values were read from.

    A snapshot taken at a committed state names that commit, whatever the root's own ``HEAD`` is
    -- the links describe the selected commit, and saying otherwise attributes them to code that
    was never read (MIK-R124 rule 1). A worktree capture names the commit the root sits on and
    reports ``uncommitted`` when C is not that commit's tree: the record then says its links
    describe the worktree, and no future or borrowed hash is invented. ``None`` when the root's
    commit cannot be read.
    """

    if snapshot.commit is not None:
        return snapshot.commit, snapshot.tree, False
    commit = _rev_parse(snapshot.root, "HEAD")
    head_tree = _rev_parse(snapshot.root, "HEAD^{tree}")
    if commit is None or head_tree is None:
        return None
    return commit, snapshot.tree, head_tree != snapshot.tree


@dataclass
class Authoring(RepointRowAuthoring):
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
    # The leaf task document's ``openQuestions`` for ``raise`` rows (MIK-R14); ``None``: no owner.
    questions: OpenQuestions | None = None
    # The ``raise`` questions a committing run appends before it writes: ``(key, text)``.
    raised: list[tuple[str, str]] = field(default_factory=list)
    # The leaf worklist's reconsideration items by subject, for a still_rejected refresh (MIK-R14).
    reconsiderations: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # The leaf's persisted worklist items as ``(kind, subject, id)``, for a row's ``items``.
    worklist_items: tuple[tuple[str, str, str], ...] = ()
    # ``(onboarding subject, id)`` of each unexplained item an onboarding row answers (MIK-R10).
    trace_items: tuple[tuple[str, str], ...] = ()
    # The persisted worklist's onboarding items' covered sources by subject (MIK-R53 rule 1).
    onboarding_sources: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # The persisted worklist's onboarding items' counted change by subject (MIK-R53 rule 2):
    # the gate's own base, which the writer's ``updated`` check follows instead of own HEAD
    # when no paired base is named.
    onboarding_counted: Mapping[str, bool] = field(default_factory=dict)
    # The leaf's paired memory base commit (the gate's K_B): the card comparison's base, which
    # a persisted worklist flag never overrides (MIK-R53 rule 2); ``None`` keeps own HEAD.
    memory_base: str | None = None
    coordination_root: Path | None = None

    # -- the operation ------------------------------------------------------------------------

    def run(self, document: HandoffDocument) -> None:
        self._taken = self.state.known_ids()
        self._assign_ids(document)
        restore_refreshes(self)
        tests: dict[str, tuple[CitedTest, ...]] = {}
        for entry in ordered_entries(document):
            tests[entry.entry_id] = self._write_entry(entry)
        by_entry = {entry.entry_id: entry for entry in (*document.entries, *document.rulings)}
        # A dependent records the stored revision of what it stands on, so it is written after the
        # records it can name: a same-handoff family revision must exist before the link is read
        # (MIK-R124 rule 2). Before this, input order decided which revision a dependent captured.
        ordered = [one for one in document.records if one.kind != "dependent"]
        ordered += [one for one in document.records if one.kind == "dependent"]
        for record in ordered:
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
        assign_entries(self, document)
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
        replace_evidence: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        return record_origin(self, stored, handoff_entry, evidence, replace_evidence)

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
        revision = self._revision(record_id, document, before)
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

    def _revision(
        self, record_id: str, document: Mapping[str, Any], before: Mapping[str, Any] | None
    ) -> int:
        """The record's revision after this operation.

        A record a merge left unmerged (both sides changed it, MIK-R24 rule 8 step 4) is resolved
        at one more than the higher side's revision. Otherwise the base's revision, incremented once
        when the meaning changed; 1 for a record new to the base.
        """

        try:
            merged = self.state.merged_sides_revision(record_id)
        except MergeStageError as error:
            self.problem(f"record {record_id}", str(error))
            merged = None
        if merged is not None:
            return merged + 1
        reference = self.state.base_record(record_id)
        if reference is None and self.state.base is None:
            reference = before
        if reference is None:
            return 1
        base_revision = reference.get("revision")
        return (base_revision if isinstance(base_revision, int) else 1) + (
            0 if canonical_equal(meaning(reference), meaning(document)) else 1
        )

    def _write_entry(self, entry: EntryRequest) -> tuple[CitedTest, ...]:
        invariant_id = self.handles.get(entry.entry_id)
        if invariant_id is None:
            return ()
        found = self.state.record(invariant_id)
        before = deep_copy(found[2]) if found is not None else None
        document = self._invariant_document(entry, invariant_id, before)
        if self._attaches_to_retired(entry, invariant_id, document):
            return ()
        self._place_record(
            "invariant",
            document,
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
        proven = case_proof.proven_tests(
            entry.proofs, lambda proof: self._write_proof(entry, invariant_id, proof)
        )
        self._remove_unnamed_entries(entry.entry_id, invariant_id)
        return tuple(self._cited_test(entry, test, proven) for test in entry.cited_tests())

    def _attaches_to_retired(
        self, entry: EntryRequest, invariant_id: str, document: Mapping[str, Any]
    ) -> bool:
        """MIK-R10: a change is never attached to a retired invariant; the refusal is named."""

        if entry.invariant_id is None or not entry.targets or document["status"] != "retired":
            return False
        self.problem(
            f"entry {entry.entry_id}",
            f"{invariant_id} is retired: a change is not attached to a retired invariant "
            "(MIK-R10); author a new invariant, or record no_invariant with a reason",
        )
        return True

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
        document["origin"], stored = self._origin(
            before, entry.entry_id, entry.evidence, entry.replace_evidence
        )
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
        if request.remove:
            self._remove_record(request, record_id, before, where)
            return
        fields = self.resolve(dict(request.fields), where)
        if request.kind == "decision":
            remap_links(self, before, fields, where)
        if isinstance(fields.get("links"), list):
            fields["links"] = [self._link(link, f"{where}.links") for link in fields["links"]]
        document: dict[str, Any] = (
            deep_copy(before)
            if before is not None
            else {"schema": schema_name(request.kind), "id": record_id}
        )
        document.update(fields)
        if request.kind in _STATUS_KINDS and "status" not in document:
            document["status"] = "active" if request.kind == "decision" else "proposed"
        if request.kind == "weakness":
            self._weakness_anchor(document, where, before)
        elif request.kind == "dependent":
            self._confirm_dependent(document, record_id, where, fields)
        evidence = entry.evidence if entry is not None else ()
        document["origin"], stored = self._origin(
            before, request.handoff_entry, evidence, entry.replace_evidence if entry else False
        )
        self._place_record(
            request.kind,
            document,
            slug=request.slug,
            before=before,
            handoff_entry=request.handoff_entry,
        )
        if stored and request.entry is not None:
            self._stored_in.setdefault(request.entry, []).append(record_id)

    # -- the two record kinds whose place is filled from the code candidate (MIK-R118, MIK-R124) --

    def _remove_record(
        self,
        request: RecordRequest,
        record_id: str,
        before: Mapping[str, Any] | None,
        where: str,
    ) -> None:
        """Remove a dependent record whose document this task deleted (MIK-R124 rule 5).

        Only a dependent leaves the tree: its document is gone, so the record has no subject left,
        and a kept record would name a file the code does not hold. A weakness is closed, never
        deleted, and every older kind retires. The record's removal is answered by the leaf's
        ``retired`` history row about it, which the curator authors.
        """

        if request.kind != "dependent":
            self.problem(
                where,
                f"a {request.kind} record is not removed: only a document that was deleted takes "
                "its dependent record with it -- a weakness is closed, and every older kind is "
                "retired",
            )
            return
        if before is None:
            self.problem(where, f"no stored record {record_id} to remove")
            return
        removed = self.state.remove_record(record_id)
        if removed is None:  # pragma: no cover - the record was read a line above
            self.problem(where, f"no stored record {record_id} to remove")
            return
        path, kind = removed
        revision = before.get("revision")
        self.records.append(
            RecordOutcome(
                kind,
                record_id,
                path,
                "removed",
                revision if isinstance(revision, int) else 0,
                request.handoff_entry,
            )
        )

    def _weakness_anchor(
        self, document: dict[str, Any], where: str, before: Mapping[str, Any] | None
    ) -> None:
        """MIK-R118 rule 2: the weakness' file and symbol anchor, recorded at C.

        The curator authors ``{path, locator}``; ``blob`` and ``content`` are the candidate's own
        values and are filled here. A weakness without a file is refused with its field: there is
        no place to record it.

        An anchor C cannot resolve keeps the place it was recorded at -- the file or symbol the
        cause was removed from, which is exactly when a weakness is closed (MIK-R118 rule 7). The
        stored ``anchor`` and ``seen`` data are never rewritten to make a resolution succeed, and
        a *new* open mark still refuses: it names a place that must resolve at C.
        """

        anchor = document.get("anchor")
        if not isinstance(anchor, Mapping):
            self.problem(f"{where}.anchor", "a weakness anchors to a file: {path, locator}")
            return
        path = anchor.get("path")
        if not isinstance(path, str) or not path:
            self.problem(f"{where}.anchor.path", "a weakness names the file it occurs in")
            return
        locator = read_locator(anchor.get("locator"))
        if isinstance(locator, str):
            self.problem(f"{where}.anchor.locator", locator)
            return
        try:
            resolved = self.code.resolve(path, locator.document)
        except AnchorResolutionError as error:
            stored = before.get("anchor") if isinstance(before, Mapping) else None
            if isinstance(stored, Mapping) and stored.get("path") == path:
                # The place this weakness was recorded at, kept as recorded: the file may have
                # been deleted or the symbol removed, and a closure still names where it was.
                document["anchor"] = dict(stored)
                return
            self.problem(f"{where}.anchor", f"{path} ({locator.describe()}): {error}")
            return
        document["anchor"] = {**resolved.to_document(), "path": path}

    def _confirm_dependent(
        self,
        document: dict[str, Any],
        record_id: str,
        where: str,
        authored: Mapping[str, Any],
    ) -> None:
        """MIK-R124 rules 2 and 5: record what stands at every link at the task's tip.

        Every write of a dependent record is a confirmation: no recorded value is typed in.
        The document must be a tracked file at C, no other record may name it, and every link
        must resolve -- an anchor at C, a whole file at C, or a stored invariant or family.

        The confirmation is *this* task's, never the stored object's: ``confirmed.task`` and
        ``confirmed.role`` are the curator's for this write, and a write that leaves every link
        as it stood -- the document confirmed without being changed -- carries this task's own
        one-sentence ``statement`` (MIK-R124 rule 5). The writer judges no document truth; it
        only refuses to attribute a fresh confirmation to the previous task.
        """

        document_path = document.get("document")
        if not isinstance(document_path, str) or not document_path:
            self.problem(f"{where}.document", "a dependent names the document it describes")
            return
        if not self.code.has_file(document_path):
            self.problem(
                f"{where}.document",
                f"{document_path!r} is not a tracked file at the code candidate; a dependent "
                "names a tracked file of the code repository",
            )
            return
        other = self._dependent_naming(document_path, record_id)
        if other is not None:
            self.problem(
                f"{where}.document",
                f"{other} already names {document_path!r}: a document has at most one dependent record",
            )
            return
        links = document.get("stands_on")
        if not isinstance(links, list) or not links:
            self.problem(f"{where}.stands_on", "a dependent has at least one link")
            return
        confirmed: list[dict[str, Any]] = []
        for position, link in enumerate(links, start=1):
            at = f"{where}.stands_on[{position}]"
            recorded = self._dependent_link(link, at)
            if recorded is not None:
                confirmed.append(recorded)
        if len(confirmed) != len(links):
            return
        document["stands_on"] = confirmed
        document["confirmed"] = self._confirmation(authored.get("confirmed"), where)

    def _dependent_link(self, link: Any, where: str) -> dict[str, Any] | None:
        """One link with its recorded value filled from the task's tip, or a named problem."""

        if not isinstance(link, Mapping):
            self.problem(
                where,
                "a link is {kind: anchor, anchor} | {kind: file, path} | {kind: record, id}",
            )
            return None
        builders = {
            "anchor": self._anchor_link,
            "file": self._file_link,
            "record": self._record_link,
        }
        kind = link.get("kind")
        build = builders.get(kind) if isinstance(kind, str) else None
        if build is None:
            self.problem(where, f"unknown link kind {kind!r}")
            return None
        recorded = build(link, where)
        heading = link.get("heading")
        if recorded is not None and heading is not None:
            recorded["heading"] = heading
        return recorded

    def _anchor_link(self, link: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        """A place in a file, recorded at C: the anchor a realization entry carries."""

        anchor = link.get("anchor")
        if not isinstance(anchor, Mapping):
            self.problem(where, "an anchor link names 'path' and a locator")
            return None
        path = anchor.get("path")
        locator = read_locator(anchor.get("locator"))
        if not isinstance(path, str) or isinstance(locator, str):
            self.problem(
                where,
                "an anchor link names 'path' and a locator: "
                + (locator if isinstance(locator, str) else "{kind, name|start, end|file}"),
            )
            return None
        try:
            resolved = self.code.resolve(path, locator.document)
        except AnchorResolutionError as error:
            self.problem(where, f"{path} ({locator.describe()}): {error}")
            return None
        return {"kind": "anchor", "anchor": {**resolved.to_document(), "path": path}}

    def _file_link(self, link: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        """A whole file, recorded at C: the recorded value is the file's blob."""

        path = link.get("path")
        if not isinstance(path, str) or not self.code.has_file(path):
            self.problem(where, f"a whole-file link names a tracked file, not {path!r}")
            return None
        return {"kind": "file", "path": path, "blob": self.code.blobs[path]}

    def _record_link(self, link: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        """An invariant or a family, recorded at its stored revision."""

        record = link.get("id")
        found = self.state.record(str(record)) if isinstance(record, str) else None
        if found is None or found[1] not in ("invariant", "family"):
            self.problem(where, f"a record link names a stored invariant or family, not {record!r}")
            return None
        revision = found[2].get("revision")
        if not isinstance(revision, int):
            self.problem(where, f"{record} is stored without a revision to record")
            return None
        # The link names the record, not the file the record lives in (MIK-R124 rule 2): the
        # stored location is not an ID, and strict validation refuses it as one.
        recorded_id = found[2].get("id")
        if not isinstance(recorded_id, str) or not recorded_id:
            self.problem(where, f"{record} is stored without its own id to link to")
            return None
        return {"kind": "record", "id": recorded_id, "revision": revision}

    def _confirmation(self, authored: Any, where: str) -> dict[str, Any]:
        """The confirmation the writer completes: this task's attribution, C's own code state.

        ``task`` and ``role`` are the curator's for *this* write; they are never taken from the
        record being updated, so a second task cannot refresh a document under the previous
        task's name. **Every** write of a dependent is a confirmation, so every confirmation
        carries this task's one-sentence ``statement`` -- whether the document stood still, moved
        with its code, or the link under it moved alone (MIK-R124 rule 5). A confirmation without
        its judgment is refused: it would claim agreement this task never stated. ``commit``,
        ``codeTree`` and ``uncommitted`` describe the code state the links were actually read from
        (MIK-R124 rule 1); a worktree candidate that no commit names says so instead of borrowing
        a hash.
        """

        if not isinstance(authored, Mapping):
            self.problem(
                f"{where}.confirmed",
                "a dependent records its confirmation {task, role, statement}: this task's "
                "one-sentence judgment of the document (MIK-R124 rule 5)",
            )
            return {}
        confirmed = {key: authored[key] for key in ("task", "role", "statement") if key in authored}
        for name in ("task", "role"):
            value = confirmed.get(name)
            if not isinstance(value, str) or not value.strip():
                self.problem(
                    f"{where}.confirmed.{name}",
                    f"the confirmation names the {name} confirming the document now; a partial "
                    "update never keeps the previous confirmation's attribution",
                )
        statement = confirmed.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            self.problem(
                f"{where}.confirmed.statement",
                "every write of a dependent is a confirmation: it carries this task's one-sentence "
                "judgment of the document, also when the document itself stood still and only the "
                "code under its links moved (MIK-R124 rule 5)",
            )
        commit, tree, uncommitted = self._code_state(f"{where}.confirmed")
        confirmed["commit"] = commit
        confirmed["codeTree"] = tree
        confirmed["uncommitted"] = uncommitted
        return confirmed

    def _code_state(self, where: str) -> tuple[str, str | None, bool]:
        """``(commit, tree, uncommitted)`` for the code state the recorded values came from."""

        state = code_state_of(self.code)
        if state is None:
            self.problem(where, "the code candidate's commit cannot be read")
            return "", self.code.tree, True
        return state

    def _dependent_naming(self, document_path: str, record_id: str) -> str | None:
        """The ID of another dependent record that already names ``document_path``, if one does."""

        for other_id, (path, kind) in sorted(self.state.records.items()):
            if kind != "dependent" or other_id == record_id:
                continue
            stored = self.state.document(path)
            if isinstance(stored, Mapping) and stored.get("document") == document_path:
                return other_id
        return None

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

    def _write_proof(
        self, entry: EntryRequest, invariant_id: str, proof: ProofRequest
    ) -> tuple[str, tuple[TestReference, ...]]:
        """Write one proof, and name the evidence references it pairs with (MIK-R194 rules 1-3).

        A ``path::name`` proof resolves the symbol, as today. A proof named by the lines of a test
        case resolves the range, and only after the range is shown to be one case that runs and that
        no name reaches; it then pairs with the evidence naming the case's title, so the same case
        cited by name is reported ``proof_written`` rather than ``unresolvable``.
        """
        locator, paired = self._proof_locator(entry, proof)
        if locator is None:
            return "", ()
        try:
            anchor = self.code.resolve(proof.test.path, locator)
        except AnchorResolutionError as error:
            self.problem(
                f"entry {entry.entry_id}",
                f"proof {proof.position} ({proof.test.spelling}): {error}",
            )
            return str(error), ()
        self._upsert_entry(
            kind="proof",
            source_path=proof.test.path,
            invariant_id=invariant_id,
            handoff_entry=entry.entry_id,
            fields={"anchor": anchor.to_document(), "facet": proof.facet},
        )
        return f"proof {self.entries[-1].id} in {self.entries[-1].sidecar}", paired

    def _proof_locator(
        self, entry: EntryRequest, proof: ProofRequest
    ) -> tuple[Mapping[str, Any] | None, tuple[TestReference, ...]]:
        """The locator a proof resolves by, and the evidence references a range proof pairs with.

        ``None`` is a proof that writes nothing; the refusal is already a problem of this entry.
        """

        test = proof.test
        if not isinstance(test, TestRangeReference):
            return {"kind": "symbol", "name": test.name}, ()
        found = case_proof.range_proof(self.code, test)
        if found.refusal is not None:
            self.problem(
                f"entry {entry.entry_id}",
                f"proof {proof.position} ({test.spelling}): {found.refusal}",
            )
            return None, ()
        return found.locator, tuple(TestReference(test.path, one) for one in found.titles)

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
