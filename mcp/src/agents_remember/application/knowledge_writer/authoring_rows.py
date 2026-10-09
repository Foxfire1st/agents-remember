"""History-row authoring: the rows of the leaf's history file and the covers that answer entries.

The second half of :class:`~agents_remember.application.knowledge_writer.authoring.Authoring`, split
along its own section boundary (``-- history rows``) so that the record and entry half and this half
each stay well under the file-size limit. The methods are moved text-identical: they are a mixin
over the operation's state, which :class:`Authoring` supplies as fields and methods (declared below
for the type checker only).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.registry import ITEM_KINDS, satisfying_row
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.knowledge_writer.handoff import (
    CoverRequest,
    RowRequest,
    family_effect_refusal,
)
from agents_remember.application.knowledge_writer.history_check import REOPEN_STEP
from agents_remember.application.knowledge_writer.memory_state import (
    EntryLocation,
    MemoryState,
    Owner,
    canonical_equal,
    deep_copy,
)
from agents_remember.application.knowledge_writer.reconsideration import (
    UNDER_RECONSIDERATION,
    Answer,
    CodeAtC,
    OpenQuestions,
    raise_question,
    reconsidered_alternative,
    refreshed_links,
)
from agents_remember.application.knowledge_writer.report import (
    RowOutcome,
)
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.models.knowledge_files.history import (
    HISTORY_SCHEMA,
    FamilyRow,
    HistoryFile,
    InvariantRow,
    OnboardingTraceRow,
    PlannedEffectRow,
    ReconsiderationRow,
    UnexplainedChangeRow,
    is_closed_history,
)
from agents_remember.models.knowledge_files.ids import (
    EntryKind,
    RecordKind,
    RowKind,
)

if TYPE_CHECKING:
    from agents_remember.application.knowledge_writer.authoring import DecisionResolver

NON_MEANING_FIELDS: Final = frozenset({"id", "schema", "origin", "revision", "admission", "status"})
ABSENT: Final = "absent"


def meaning(document: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a record that carry meaning; a change to any of them is a new revision."""

    return {key: value for key, value in document.items() if key not in NON_MEANING_FIELDS}


def _first_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in one['loc']) or '-'}: {one['msg']}"
        for one in error.errors()
    )


class HistoryRowAuthoring:
    """The history-row and cover methods of one authoring operation."""

    if TYPE_CHECKING:
        state: MemoryState
        code: CodeSnapshot
        owner: Owner
        notes: list[str]
        rows: list[RowOutcome]
        _stored_in: dict[str, list[str]]
        _foreign_evidence: dict[str, list[tuple[str, tuple[str, ...]]]]
        decisions: DecisionResolver | None
        questions: OpenQuestions | None
        raised: list[tuple[str, str]]
        reconsiderations: Mapping[str, Mapping[str, Any]]
        worklist_items: tuple[tuple[str, str, str], ...]
        trace_items: tuple[tuple[str, str], ...]

        def problem(self, where: str, message: str) -> None: ...

        def mint(self, kind: RecordKind | EntryKind | RowKind) -> str: ...

        def resolve_id(self, value: str, where: str) -> str | None: ...

        def resolve(self, value: Any, where: str) -> Any: ...

        def _place_record(self, *args: Any, **kwargs: Any) -> Any: ...

    # -- history rows -------------------------------------------------------------------------

    def _write_rows(self, requests: Sequence[RowRequest]) -> None:
        path, attempt = self.state.history_target(self.owner)
        stored = self.state.document(path)
        if stored is not None and stored.get("closed") is True:
            base = self.state.base
            if base is not None and is_closed_history(base.get(path)):
                self.problem(path, "this history file is closed and frozen (MIK-R07 rule 7)")
            else:
                self.problem(
                    path, f"this history file is closed and frozen (MIK-R07 rule 7){REOPEN_STEP}"
                )
            return
        document = (
            deep_copy(stored)
            if stored is not None
            else {
                "schema": HISTORY_SCHEMA,
                self.owner.kind: self.owner.id,
                **({"attempt": attempt} if attempt > 1 else {}),
                "closed": False,
                "rows": [],
            }
        )
        rows = {row["subject"]: row for row in document.get("rows") or () if isinstance(row, dict)}
        written: list[dict[str, Any]] = []
        for request in requests:
            row = self._row(request, rows)
            if row is not None:
                rows[row["subject"]] = row
                self.rows.append(RowOutcome(row["id"], row["subject"], row["disposition"], path))
                written.append(row)
        document["rows"] = list(rows.values())
        self._fill_items(document, written)
        self.state.put(path, document)

    def _fill_items(self, document: Mapping[str, Any], written: list[dict[str, Any]]) -> None:
        """A row whose hand-off names no item lists the worklist items it answers (MIK-R07 rule 1).

        The answer is each item kind's own satisfying-row rule over the file as written, asked of
        the leaf's persisted worklist. The field is informational: without a worklist, or with a
        file that does not parse (the render step reports it), a row keeps what its hand-off named.
        """

        unnamed = {row["subject"] for row in written if not row["items"]}
        if not (unnamed and self.worklist_items):
            return
        try:
            history = HistoryFile.model_validate(document)
        except ValidationError:
            return
        answered = self._answered_items(history)
        for row in document["rows"]:
            if row["subject"] in unnamed and row["subject"] in answered:
                row["items"] = sorted(answered[row["subject"]])

    def _answered_items(self, history: HistoryFile) -> dict[str, set[str]]:
        """``row subject -> item IDs`` of the worklist items a row of ``history`` answers."""

        answered: dict[str, set[str]] = {}
        for kind, subject, item in self.worklist_items:
            row = satisfying_row(kind, subject, history) if kind in ITEM_KINDS else None
            if row is not None:
                answered.setdefault(row.subject, set()).add(item)
        # An unexplained item in an uncovered file is answered by the file's onboarding row, which
        # the item's own kind does not find: its registry lookup names a hunk row only.
        for subject, item in self.trace_items:
            if history.row_about(subject) is not None:
                answered.setdefault(subject, set()).add(item)
        return answered

    def _row(self, request: RowRequest, rows: Mapping[str, Any]) -> dict[str, Any] | None:
        where = f"history[{request.position}]"
        for model, write in (
            (OnboardingTraceRow, self._onboarding_row),
            (PlannedEffectRow, self._planned_row),
            (UnexplainedChangeRow, self._unexplained_row),
            (ReconsiderationRow, self._reconsideration_row),
        ):
            if re.match(model.subject_pattern, request.subject):
                return write(request, rows.get(request.subject), where)
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
            return self._family_row(request, row, found[2], where)
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

    def _unexplained_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """A ``no_invariant`` row (MIK-R10): an unexplained change in a covered file that carries
        no invariant, with the curator's reason. It carries nothing an invariant, family or planned
        row carries. A ``file:<path>@<blob>`` subject must name the path's object at C (``absent``
        when C does not hold it): a row about another change of the path answers no item."""

        extra = [
            name
            for name, value in (
                ("covers", request.covers),
                ("effect", request.effect),
                ("because", request.because),
                ("examined", request.examined),
                ("ref", request.ref),
            )
            if value
        ]
        if extra:
            self.problem(where, f"a no_invariant row carries no {extra}")
            return None
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": request.subject,
            "disposition": request.disposition,
            "reason": request.reason,
            "items": list(request.items),
        }
        try:
            UnexplainedChangeRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        stale = self.code.file_subject_mismatch(request.subject)
        if stale is not None:
            self.problem(where, stale)
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

    def _reconsideration_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """A reconsideration row (MIK-R14 rule 4): ``still_rejected`` or ``raise``.

        It carries the common fields only. A ``raise`` sets the decision's status to
        ``under_reconsideration`` and plans its question for the leaf's task document; a question
        the task owner would refuse refuses the row (:mod:`.reconsideration`).
        """

        row = self._common_row(request, existing, where)
        if row is None:
            return None
        decision = request.subject.removeprefix("reconsider:").split("#", 1)[0]
        found = self.state.record(decision)
        named = reconsidered_alternative(request.subject, found)
        if isinstance(named, str):
            self.problem(where, named)
            return None
        if found is None:
            return None
        if request.disposition == "raise":
            return row if self._raise(found, named[1], row, where) else None
        return row if self._refresh(found, named[0], row, where) else None

    def _common_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """The reconsideration row's common fields, validated; ``None`` after naming a problem."""

        extra = [
            name
            for name, value in (
                ("covers", request.covers),
                ("effect", request.effect),
                ("because", request.because),
                ("examined", request.examined),
                ("ref", request.ref),
            )
            if value
        ]
        if extra:
            self.problem(where, f"a reconsideration row carries no {extra}")
            return None
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": request.subject,
            "disposition": request.disposition,
            "reason": request.reason,
            "items": list(request.items),
        }
        try:
            ReconsiderationRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return row

    def _refresh(
        self,
        found: tuple[str, str, dict[str, Any]],
        index: int,
        row: Mapping[str, Any],
        where: str,
    ) -> bool:
        """``still_rejected`` refreshes the fired links to the judged state.

        Rulings Q2/Q3, reviews R1 F1-F4, R3 and R4 (:func:`.reconsideration.refreshed_links`).
        """

        item = self._answered_item(row, where)
        if item is None:
            return False
        path, _kind, document = found
        facts = item.get("facts")
        answer = Answer(
            fired=tuple(facts.get("changed") or ()) if isinstance(facts, Mapping) else (),
            base_document=self.state.base_record(str(document.get("id"))),
            item=str(item.get("id") or ""),
            names_item=bool(row.get("items")),
        )
        code = CodeAtC(
            CodeTrees(CodeObjects(self.code.root), self.code.tree, self.code.tree), self.code.blobs
        )
        refresh = refreshed_links(document, index, answer, code)
        for problem in refresh.problems:
            self.problem(where, f"the still_rejected refresh is refused: {problem}")
        if refresh.problems:
            return False
        if canonical_equal(refresh.links, document.get("links")):
            return True
        updated = {**deep_copy(document), "links": refresh.links}
        if refresh.judged:
            # Links are meaning: the normal placement bumps the revision once per leaf (F2).
            self._place_record(
                "decision", updated, slug=None, before=document, handoff_entry=str(row["subject"])
            )
        else:
            # An earlier refresh carried to the current C: no further bump (review R4-1).
            self.state.put(path, updated)
            self.notes.append(f"{row['subject']}: the earlier refresh is carried to the current C")
        return True

    def _answered_item(self, row: Mapping[str, Any], where: str) -> Mapping[str, Any] | None:
        """The worklist item the row answers (empty if none); ``None`` after naming a problem."""

        subject = str(row["subject"])
        item = self.reconsiderations.get(subject)
        if item is None:
            self.notes.append(
                f"{subject}: the leaf's worklist has no item for it; no link refreshed"
            )
            return {}
        named = row.get("items") or ()
        if named and item.get("id") not in named:
            self.problem(
                where,
                f"the row answers {list(named)}, but the leaf's worklist item for {subject} is "
                f"{item.get('id')}; recompute the worklist and answer that item",
            )
            return None
        return item

    def _raise(
        self,
        found: tuple[str, str, dict[str, Any]],
        alternative: Mapping[str, Any],
        row: Mapping[str, Any],
        where: str,
    ) -> bool:
        key, question = raise_question(
            row["subject"],
            alternative,
            reason=row["reason"],
            leaf=self.owner.id,
            row_id=row["id"],
        )
        refusal = (
            "a raise appends a question to the leaf's task document, and this write has no task "
            "owner"
            if self.questions is None
            else self.questions.check(key, question)
        )
        if refusal is not None:
            self.problem(where, f"the raise is refused, so the item stays open: {refusal}")
            return False
        path, _kind, document = found
        if document.get("status") != UNDER_RECONSIDERATION:
            self.state.put(path, {**document, "status": UNDER_RECONSIDERATION})
        self.raised.append((key, question))
        self.notes.append(f"raise {row['subject']}: the leaf's task document gets: {question}")
        return True

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
                f"{self.state.history_target(self.owner)[0]}#{subject}"
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
        self, request: RowRequest, row: dict[str, Any], record: Mapping[str, Any], where: str
    ) -> dict[str, Any] | None:
        refusal = family_effect_refusal(request, row["subject"])
        if refusal is not None:
            self.problem(f"{where}.effect", refusal)
            return None
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
        row["revision"] = record.get("revision")
        if request.effect is not None:
            row["effect"] = request.effect
        try:
            FamilyRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, f"family {row['subject']}: {_first_error(error)}")
            return None
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
        if not self._revise_rationale(entry_id, request, where):
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

    def _revise_rationale(self, entry_id: str, request: CoverRequest | None, where: str) -> bool:
        """Replace a realization entry's rationale in place when its cover names one; ``False``
        after naming a problem. The entry is looked up again: its cover may have moved it."""

        located = self.state.find_entry(entry_id)
        if request is None or request.rationale is None or located is None:
            return True
        if located.entries != "realizes":
            self.problem(
                where, "a cover's rationale revises a realization entry; a proof has a facet"
            )
            return False
        located.document["rationale"] = request.rationale
        self.state.touch(located.sidecar)
        return True

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
