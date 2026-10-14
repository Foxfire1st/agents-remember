"""History-row authoring: the rows of the leaf's history file and the covers that answer entries.

The second half of :class:`~agents_remember.application.knowledge_writer.authoring.Authoring`, split
along its own section boundary (``-- history rows``) so that the record and entry half and this half
each stay well under the file-size limit. The methods are moved text-identical: they are a mixin
over the operation's state, which :class:`Authoring` supplies as fields and methods (declared below
for the type checker only).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, get_args

from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.registry import ITEM_KINDS, satisfying_row
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.knowledge_writer.handoff import (
    HANDLE_PREFIX,
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
from agents_remember.kernel.git_command import GitPreparationError
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTreeReadError,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge.effect import EffectLabel
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import (
    HISTORY_SCHEMA,
    RECORD_ROW_SUBJECT_PATTERN,
    FamilyRow,
    HistoryFile,
    InvariantRow,
    MembershipRow,
    OnboardingTraceRow,
    PlannedEffectRow,
    ReconsiderationRow,
    RecordRow,
    UnexplainedChangeRow,
    is_closed_history,
    membership_invariant,
    membership_subject,
)
from agents_remember.models.knowledge_files.ids import (
    EntryKind,
    RecordKind,
    RowKind,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    card_paths,
    counted_change,
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


_EVIDENCE_MARKER: Final = " Evidence ("


class _LeafBaseUnreadable(ValueError):
    """The leaf's paired base is named but its tree cannot be read."""


def _carried_evidence_segments(reason: str) -> list[str]:
    """Previously appended foreign-evidence segments of a stored row reason (MIK-R53 rule 8).

    :meth:`HistoryRowAuthoring._reason_with_evidence` is the sole producer of ``" Evidence ("``
    segments, so scanning for the marker recovers every segment a prior run appended
    byte-exact -- including a trailing empty one, where an accepted reason ends in the bare
    marker and the split form would drop that suffix on a later reason-only rewrite.
    """

    segments: list[str] = []
    start = 0
    while (found := reason.find(_EVIDENCE_MARKER, start)) >= 0:
        end = reason.find(_EVIDENCE_MARKER, found + len(_EVIDENCE_MARKER))
        segments.append(reason[found:] if end < 0 else reason[found:end])
        start = found + len(_EVIDENCE_MARKER)
    return segments


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
        onboarding_sources: Mapping[str, tuple[str, ...]]
        """The persisted worklist's onboarding items' covered sources by subject (MIK-R53)."""
        onboarding_counted: Mapping[str, bool]
        """The persisted worklist's onboarding items' counted change by subject (MIK-R53)."""
        memory_base: str | None
        """The leaf's paired memory base commit: the gate's K_B for the card comparison."""
        handles: dict[str, str]

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
        if re.match(RECORD_ROW_SUBJECT_PATTERN, request.subject):
            return self._record_row(request, rows.get(request.subject), where)
        return self._aliased_or_stored_row(request, rows, where)

    def _aliased_or_stored_row(
        self, request: RowRequest, rows: Mapping[str, Any], where: str
    ) -> dict[str, Any] | None:
        """A ``handoff:`` alias, a membership form, or the stored-record fallback (rules 6, 8)."""

        if request.subject.startswith(HANDLE_PREFIX):
            if request.disposition == "no_family":
                handoff_membership = self._handoff_membership(request, rows)
                if handoff_membership is not None:
                    canonical, existing = handoff_membership
                    return self._membership_row(
                        replace(request, subject=canonical), existing, where
                    )
            canonical = self._resolve_record_subject(request)
            if canonical is not None:
                return self._record_row(
                    replace(request, subject=canonical), rows.get(canonical), where
                )
            return self._stored_record_row(request, rows, where)
        membership_existing = self._membership_existing(request, rows, where)
        if membership_existing is not None:
            _canonical, existing = membership_existing
            return self._membership_row(request, existing, where)
        if request.disposition == "no_family" or membership_invariant(request.subject) is not None:
            return None  # refused above with its reason
        return self._stored_record_row(request, rows, where)

    def _handoff_membership(
        self, request: RowRequest, rows: Mapping[str, Any]
    ) -> tuple[str, Mapping[str, Any] | None] | None:
        """The canonical membership subject a ``handoff:`` no_family alias names, if any.

        A ``no_family`` row about a stored invariant is a membership row, whatever alias form
        names it; resolving before the stored-record fallback keeps the alias equivalent to
        the literal and bare forms, with a stable row ID on rerun. Returns ``None`` -- without
        recording anything -- when the alias names no stored invariant, so wrong-kind and
        unknown aliases keep the stored row's refusal and no malformed ID is ever guessed.
        """

        key = request.subject.removeprefix(HANDLE_PREFIX)
        resolved = self.handles.get(key) or self.state.record_by_origin(
            "invariant", self.owner, key
        )
        if resolved is None:
            found = self.state.record(key)
            resolved = key if found is not None and found[1] == "invariant" else None
        else:
            found = self.state.record(resolved)
            if found is None or found[1] != "invariant":
                return None
        if resolved is None:
            return None
        canonical = membership_subject(resolved)
        existing = rows.get(canonical)
        return canonical, existing if isinstance(existing, Mapping) else None

    def _resolve_record_subject(self, request: RowRequest) -> str | None:
        """The canonical decision/facet record ID a ``handoff:`` row subject names (MIK-R53 rule 8).

        Valid alias forms resolve before dispatch so every new sibling produces equivalent
        row fields; the stored row is looked up by the canonical subject, keeping the row ID
        stable on rerun. Returns ``None`` silently for anything else -- an invariant/family
        alias the stored-record fallback owns, a wrong-kind or unknown alias it refuses --
        so no malformed ID is ever normalized into a guess.
        """

        key = request.subject.removeprefix(HANDLE_PREFIX)
        candidate = self.handles.get(key)
        if candidate is None:
            for kind in (
                "decision",
                "incident",
                "assumption",
                "limitation",
                "failure_mode",
                "scenario",
                "diagnostic",
                "term",
            ):
                candidate = self.state.record_by_origin(kind, self.owner, key)
                if candidate is not None:
                    break
        if candidate is None:
            return None
        found = self.state.record(candidate)
        if found is None or found[1] not in (
            "decision",
            "incident",
            "assumption",
            "limitation",
            "failure_mode",
            "scenario",
            "diagnostic",
            "term",
        ):
            return None
        return candidate

    def _membership_existing(
        self, request: RowRequest, rows: Mapping[str, Any], where: str
    ) -> tuple[str, Mapping[str, Any] | None] | None:
        """The canonical membership subject and its stored row, if this is a membership row.

        Bare-invariant and alias forms resolve to ``membership:<INV>`` before the stored-row
        lookup, so an unchanged rerun finds its row instead of minting a new ID. Returns
        ``None`` for non-membership subjects, and -- after recording the refusal -- for
        unresolvable ones.
        """

        if membership_invariant(request.subject) is not None:
            canonical = request.subject
        elif request.disposition != "no_family":
            return None
        else:
            resolved = self._resolve_membership_subject(request, where)
            if resolved is None:
                return None
            canonical = resolved[1]
        existing = rows.get(canonical)
        return canonical, existing if isinstance(existing, Mapping) else None

    def _stored_record_row(
        self, request: RowRequest, rows: Mapping[str, Any], where: str
    ) -> dict[str, Any] | None:
        """A row about a stored invariant or family record, by resolved ID."""

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
        """An ``onboarding_trace`` row (MIK-R30, MIK-R53 rules 1 and 2).

        Disposition ``updated`` or ``no_impact``; it carries nothing an invariant, family or
        planned row carries. The writer fills ``sources`` from the code candidate: one
        ``{path, blob}`` per covered source file, ``absent`` for a deleted file. A file card's
        path comes from its subject; a route overview's from the leaf's persisted worklist item.
        An ``updated`` row whose card has no counted change between the memory base and the
        produced candidate is refused. Marker lines a crossing sync moved into an existing row
        (MIK-R24 rule 8 step 1) are kept.
        """

        extra = [
            name
            for name, value in (
                ("covers", request.covers),
                ("effect", request.effect),
                ("because", request.because),
                ("examined", request.examined),
                ("considered", request.considered),
                ("ref", request.ref),
            )
            if value
        ]
        if extra:
            self.problem(where, f"an onboarding_trace row carries no {extra}")
            return None
        paths = card_paths(request.subject)
        if paths is None:
            self.problem(where, f"the subject {request.subject!r} names no card or overview")
            return None
        if request.subject.endswith("/overview") or request.subject == "onboarding:overview":
            sources = self.onboarding_sources.get(request.subject)
            if sources is None:
                self.problem(
                    where,
                    f"no worklist item covers {request.subject}; recompute the leaf's worklist "
                    "and name the row again once an item needs it",
                )
                return None
        else:
            sources = (request.subject.removeprefix("onboarding:"),)
        markdown, sidecar = paths
        if request.disposition == "updated" and not self._row_counted_change(
            request.subject, markdown, sidecar, where
        ):
            self.problem(
                where,
                f"an updated onboarding row needs a counted change of its card between the "
                f"memory base and the candidate, and {markdown} has none: update the card's "
                "Markdown or its sidecar's references, or name the row no_impact",
            )
            return None
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": request.subject,
            "disposition": request.disposition,
            "reason": request.reason,
            "items": list(request.items),
            "sources": [
                {"path": path, "blob": self.code.blobs.get(path) or ABSENT} for path in sources
            ],
        }
        if existing is not None and existing.get("markers"):
            row["markers"] = list(existing["markers"])
        try:
            OnboardingTraceRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return row

    def _row_counted_change(self, subject: str, markdown: str, sidecar: str, where: str) -> bool:
        """The card comparison, failing closed when the leaf's paired base cannot be read."""

        try:
            return self._card_counted_change(subject, markdown, sidecar)
        except _LeafBaseUnreadable as error:
            self.problem(where, str(error))
            return False

    def _card_counted_change(self, subject: str, markdown: str, sidecar: str) -> bool:
        """Whether the card has a counted change from the leaf's base to this run's candidate.

        With the leaf's paired base named, the current base-to-candidate comparison decides:
        a persisted worklist flag is the gate's comparison at worklist time, and a card the
        leaf restored or re-edited after that makes it stale, so the flag never overrides the
        known base (MIK-R53 rule 2). Without the paired base the persisted flag decides when
        present -- the worklist is computed against the paired base, so writer and gate agree
        in preclosed and reopened leaves -- and only when neither is known is the operation's
        own HEAD compared instead.
        """

        if self.memory_base is None:
            counted = self.onboarding_counted.get(subject)
            if counted is not None:
                return counted
        return counted_change(
            self._counted_base_files(),
            {path: self._produced_bytes(path) for path in (markdown, sidecar)},
            markdown,
            sidecar,
        )

    def _counted_base_files(self) -> Mapping[str, bytes]:
        """The card-comparison base: the leaf's paired base, else the operation's own base."""

        if self.memory_base is None:
            base = self.state.base
            return base.files if base is not None else {}
        try:
            tree = knowledge_tree_from_git(
                self.state.root, self.memory_base, label="leaf paired base"
            )
        except (
            KnowledgeTreeReadError,
            GitPreparationError,
            KeyError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            raise _LeafBaseUnreadable(
                f"the leaf's paired memory base {self.memory_base[:12]} cannot be read, so an "
                f"updated onboarding row cannot be judged: {error!r}"
            ) from error
        return tree.files

    def _produced_bytes(self, path: str) -> bytes | None:
        """The candidate bytes at ``path`` as this run produces them (``None`` when deleted)."""

        if path in self.state.removed:
            return None
        document = self.state.documents.get(path)
        if document is not None:
            return canonical_text(document).encode("utf-8")
        return self.state.files.get(path)

    def _membership_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """A ``membership`` row (MIK-R53 rule 6): the invariant belongs to no family.

        The subject is ``membership:<invariant ID>``; a ``no_family`` disposition with any other
        subject resolves it to the stored invariant it names. ``considered`` names the examined
        families. The row carries the invariant's revision in the produced tree, and is refused
        for an invariant a family of the produced tree lists.
        """

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
            self.problem(where, f"a membership row carries no {extra}")
            return None
        resolved = self._resolve_membership_subject(request, where)
        if resolved is None:
            return None
        invariant, subject, found = resolved
        listing = self._listing_family(invariant)
        if listing is not None:
            self.problem(
                where,
                f"{invariant} is listed by family {listing} of the produced tree, so no "
                "no_family row is written for it; list the change under that family's row, or "
                "remove the member first",
            )
            return None
        considered = self._resolve_considered(request, where)
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": subject,
            "disposition": request.disposition,
            "reason": request.reason,
            "items": list(request.items),
            "revision": found[2].get("revision"),
            "considered": considered,
        }
        try:
            MembershipRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return row

    def _resolve_membership_subject(
        self, request: RowRequest, where: str
    ) -> tuple[str, str, Any] | None:
        """The membership row's ``(invariant, subject, stored record)``, or ``None``."""

        invariant = membership_invariant(request.subject)
        if invariant is None:
            if request.disposition != "no_family":
                self.problem(
                    where, f"the subject {request.subject!r} names no record (unknown subject)"
                )
                return None
            resolved = self.resolve_id(request.subject, where)
            found = None if resolved is None else self.state.record(resolved)
            if resolved is None:
                return None
            if found is None or found[1] != "invariant":
                self.problem(where, f"the subject {request.subject!r} names no stored invariant")
                return None
            return resolved, membership_subject(resolved), found
        found = self.state.record(invariant)
        if found is None or found[1] != "invariant":
            self.problem(where, f"the subject {request.subject!r} names no stored invariant")
            return None
        return invariant, request.subject, found

    def _resolve_considered(self, request: RowRequest, where: str) -> list[str]:
        """The considered families as stored IDs, refusing the ones that resolve to none."""

        considered = []
        for one in request.considered:
            family = self.resolve_id(one, where)
            member = None if family is None else self.state.record(family)
            if member is None or member[1] != "family":
                key = one.removeprefix("handoff:")
                family = self.handles.get(key) or self.state.record_by_origin(
                    "family", self.owner, key
                )
                member = None if family is None else self.state.record(family)
            if family is None or member is None or member[1] != "family":
                self.problem(where, f"considered family {one!r} is not a stored family")
                continue
            considered.append(family)
        return considered

    def _listing_family(self, invariant_id: str) -> str | None:
        """A family of the produced tree listing ``invariant_id``, or ``None``."""

        for record_id, (_path, kind) in self.state.records.items():
            if kind != "family":
                continue
            found = self.state.record(record_id)
            if found is not None and invariant_id in (found[2].get("members") or ()):
                return record_id
        return None

    def _record_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """A ``record`` row (MIK-R53 rule 8): the disposition of a decision or facet record.

        It carries the record's revision in the produced tree, ``effect`` (required on
        ``changed``, ``retire`` on ``retired``) and optional ``because``. Evidence for a record
        another owner authored is stored in its reason, which answers the writer's demand for a
        row about that record.
        """

        extra = [
            name
            for name, value in (
                ("covers", request.covers),
                ("examined", request.examined),
                ("considered", request.considered),
                ("ref", request.ref),
            )
            if value
        ]
        if extra:
            self.problem(where, f"a record row carries no {extra}")
            return None
        refusal = self._record_effect_refusal(request)
        if refusal is not None:
            self.problem(where, refusal)
            return None
        subject = self.resolve_id(request.subject, where)
        found = None if subject is None else self.state.record(subject)
        if subject is None:
            return None
        if found is None or found[1] not in {
            "decision",
            "incident",
            "assumption",
            "limitation",
            "failure_mode",
            "scenario",
            "diagnostic",
            "term",
        }:
            self.problem(
                where, f"the subject {request.subject!r} names no decision or facet record"
            )
            return None
        row: dict[str, Any] = {
            "id": existing["id"] if existing is not None else self.mint("history_row"),
            "subject": subject,
            "disposition": request.disposition,
            "reason": self._reason_with_evidence(
                subject, request.reason, self._foreign_carry(existing, found[2])
            ),
            "items": list(request.items),
            "revision": found[2].get("revision"),
        }
        if request.effect is not None:
            row["effect"] = request.effect
        if request.because:
            row["because"] = [self.resolve(one, where) for one in request.because]
        try:
            RecordRow.model_validate(row)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return row

    def _record_effect_refusal(self, request: RowRequest) -> str | None:
        """Why a record row's effect input is refused, or ``None`` (MIK-R53 rule 8)."""

        if request.disposition == "changed" and request.effect is None:
            return (
                f"record {request.subject}: a changed row records the curator's judgment of "
                "the meaning change, so it needs an 'effect', one of "
                f"{', '.join(get_args(EffectLabel))}; add it to this 'history' entry"
            )
        if request.disposition == "retired" and request.effect != "retire":
            return (
                f"record {request.subject}: a retired row's effect is 'retire'; set it on "
                "this 'history' entry, or make it another disposition"
            )
        if request.disposition not in {"changed", "retired"} and request.effect_supplied:
            return (
                f"record {request.subject}: a {request.disposition} row records no effect "
                "(only a changed or retired row does); remove 'effect' from this "
                "'history' entry, or make it a changed row"
            )
        return None

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

    def _foreign_carry(
        self, existing: Mapping[str, Any] | None, record: Mapping[str, Any]
    ) -> str | None:
        """A stored foreign row's reason to carry, or ``None`` (MIK-R53 rule 8).

        Only a foreign-owned record carries: same-owner row reasons stay authorial, and the
        R54 replacement grant applies to same-owner origin only. A missing reason carries
        nothing.
        """

        if not isinstance(existing, Mapping):
            return None
        origin = record.get("origin")
        if not isinstance(origin, Mapping):
            return None
        if origin.get(self.owner.kind) == self.owner.id and origin.get("task") == self.owner.task:
            return None
        reason = existing.get("reason")
        return reason if isinstance(reason, str) else None

    def _reason_with_evidence(self, subject: str, reason: str, carry: str | None = None) -> str:
        """The row's reason, with the evidence of this run's updates of another owner's record.

        ``carry`` is a stored row's reason whose previously appended foreign-evidence segments
        survive the rewrite: two foreign updates with distinct evidence, and a later reason-only
        rewrite, retain every evidence value (MIK-R53 rule 8). Segments already present are
        never duplicated.
        """

        if carry:
            for segment in _carried_evidence_segments(carry):
                if segment not in reason or (
                    segment == _EVIDENCE_MARKER and not reason.endswith(segment)
                ):
                    reason = f"{reason}{segment}"
        pending = self._foreign_evidence.pop(subject, [])
        for handoff_entry, evidence in pending:
            segment = f" Evidence ({handoff_entry}): {'; '.join(evidence)}"
            if segment not in reason:
                reason = f"{reason}{segment}"
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
