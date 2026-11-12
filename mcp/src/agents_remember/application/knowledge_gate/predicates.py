"""Each registered worklist kind's satisfying rule over a stored item (MIK-R09 rules 1, 2 and 7).

The gate decides every item of the recomputed worklist through **its own kind's predicate**,
registered here beside the kind (:func:`register_gate_predicate`), never through a generic row
lookup: kinds differ in what satisfies them (carried from L06, L10, L14 and L30). A predicate takes
the item document and the :class:`GateContext` -- the leaf's parsed history file from K_C, K_C
itself and the code tree C -- and returns why the item is still open, or ``None`` when it is
satisfied. An item whose kind has no predicate is open: nothing that cannot be decided passes.

* ``touched_invariant`` / ``stale_invariant`` -- the leaf's **invariant row** about the invariant is
  current (rule 2): its ``covers`` include every entry the facts list as ``touched``,
  ``moved_or_absent`` or ``stale_at_base`` and every added, retired or re-anchored entry; each
  covered entry that still exists in K_C has the MIK-R03 state ``current`` at C; its ``revision``
  is the invariant's revision in K_C. While that revision differs from the parent line's, the row
  is the ``changed`` row of the change (or the ``deleted`` row that retires the invariant): a later
  row of another disposition cannot govern a changed record.
* ``reached_family`` -- the leaf's **family row** is current: ``examined`` names exactly the union
  of the family's K_B and K_C members; every examined revision is that member's K_C revision (or the
  member is absent from K_C), so a sibling whose meaning changed after the row reopens it (D7); and
  no member has an open invariant item.
* ``family_route_condition`` -- :func:`family_route_item_open` over the parsed history file, decided
  by the item's **subject** ``<FAM-ID>#<condition>``, so an item whose ID changed between recomputes
  (L06 review N2) is answered by the same row.
* ``onboarding_trace`` -- :func:`onboarding_row_open`: a counted change, or the leaf's current
  onboarding row about the subject -- its ``sources`` equal the facts' blobs (MIK-R53 rule 3).
* ``unexplained_hunk`` / ``unexplained_file`` -- :func:`unexplained_row_open`: a covered item by
  its ``no_invariant`` row; an uncovered item by the same current onboarding row (MIK-R53 rule 3).
* ``unassigned_invariant`` -- :func:`membership_row_open`: the leaf's ``membership`` row at the
  invariant's current revision (MIK-R53 rule 6).
* ``touched_record`` -- :func:`record_row_open`: the leaf's ``record`` row at the candidate
  revision with the disposition the change takes (MIK-R53 rule 8).
* ``planned_untouched`` -- :func:`planned_item_open`.
* ``reconsideration_candidate`` -- :func:`reconsideration_item_open`: an unanswered candidate
  blocks closeout (D29).

Only authored rows satisfy items (rule 5); nothing here writes, and there is no waiver. An entry
whose state cannot be observed because a Git read failed or timed out raises
:class:`GitReadFailed`: the gate makes the whole run ``incomplete`` rather than deciding an item on
a read that did not happen (L09 review R1, finding 9).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any, Final

from agents_remember.application.knowledge_currentness.observe import (
    EntryObservation,
    OpenedCodeTree,
    observe_entry,
)
from agents_remember.application.knowledge_worklist.classify import COVERING_CLASSES
from agents_remember.application.knowledge_worklist.code import CodeReadError
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.link_targets import holds_target
from agents_remember.application.knowledge_worklist.reconsideration_gate import (
    reconsideration_gate_reason,
)
from agents_remember.application.knowledge_worklist.registry import GUARANTEE_CHANGED, ITEM_KINDS
from agents_remember.application.knowledge_worklist.route_conditions import (
    family_route_item_open,
)
from agents_remember.memory.knowledge_index import Entry
from agents_remember.models.knowledge.item_verdict import ItemVerdict
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    InvariantRow,
    MembershipRow,
    OnboardingTraceRow,
    RecordRow,
    changed_family_row_violation,
    changed_record_row_violation,
    membership_invariant,
    winning_row_closed,
)
from agents_remember.models.knowledge_files.planned import planned_item_open
from agents_remember.models.knowledge_files.records import (
    DependentAnchorLink,
    DependentFileLink,
    DependentRecord,
    DependentRecordLink,
)
from agents_remember.models.knowledge_files.sidecars import ProofEntry
from agents_remember.models.knowledge_files.unexplained import (
    COVERED,
    UNCOVERED,
    row_subject,
    unexplained_satisfied_by,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    onboarding_row_answers,
    row_sources_current,
)

__all__ = [
    "GATE_PREDICATES",
    "INVARIANT_KINDS",
    "GateContext",
    "GitReadFailed",
    "ItemPredicate",
    "item_open_reason",
    "item_verdict",
    "register_gate_predicate",
]

INVARIANT_KINDS: Final = ("touched_invariant", "stale_invariant")


class GitReadFailed(Exception):
    """An entry's MIK-R03 state could not be observed: a Git read failed or timed out."""


@dataclass(frozen=True)
class GateContext:
    """What a predicate reads: the leaf's history in K_C, K_C itself, and the code tree C."""

    owner: str | None
    history: HistoryFile | None
    candidate: KnowledgeSide
    code: OpenedCodeTree
    open_invariants: frozenset[str] = field(default=frozenset())
    """Invariants with an open ``touched_invariant`` or ``stale_invariant`` item (the family rule)."""

    @cached_property
    def rows_by_subject(self) -> dict[str, str]:
        """Each row subject of the leaf's history file, to its row ID."""

        return {} if self.history is None else {row.subject: row.id for row in self.history.rows}

    def entry_state(self, entry_id: str) -> EntryObservation | None:
        """The MIK-R03 state at C of an entry of K_C; ``None`` when K_C no longer holds it.

        Raises :class:`GitReadFailed` when the state is ``unverifiable`` because a read failed.
        """

        indexed = self.candidate.entries.get(entry_id)
        if indexed is None:
            return None
        entry = Entry(
            id=entry_id,
            kind="proof" if isinstance(indexed.entry, ProofEntry) else "realization",
            invariant=indexed.entry.invariant,
            path=indexed.path,
            sidecar=indexed.sidecar,
            document={"anchor": indexed.entry.anchor.to_document()},
        )
        observed = observe_entry(entry, self.code)
        if observed.read_failed:
            raise GitReadFailed(f"entry {entry_id} at {indexed.path}: {observed.reason}")
        return observed


ItemPredicate = Callable[[Mapping[str, Any], GateContext], str | None]
"""``(item, context) -> why the item is open``, or ``None`` when a current row satisfies it."""

GATE_PREDICATES: Final[dict[str, ItemPredicate]] = {}


def register_gate_predicate(kind: str, predicate: ItemPredicate) -> ItemPredicate:
    """Register ``kind``'s stored-item predicate; the kind must be registered, and only once."""

    if kind not in ITEM_KINDS:
        raise ValueError(f"no worklist item kind {kind!r} is registered")
    if kind in GATE_PREDICATES:
        raise ValueError(f"a gate predicate for {kind!r} is already registered")
    GATE_PREDICATES[kind] = predicate
    return predicate


def item_open_reason(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Why ``item`` is open, through its kind's own predicate; an undecidable item is open."""

    kind = str(item.get("kind"))
    predicate = GATE_PREDICATES.get(kind)
    if predicate is None:
        return (
            f"no gate predicate is registered for the item kind {kind!r}, so it cannot be answered"
        )
    return predicate(item, context)


def _witness_row(item: Mapping[str, Any], context: GateContext) -> str | None:
    kind = item.get("kind")
    subject = str(item.get("subject"))
    facts = item.get("facts") or {}
    if kind in ("unexplained_hunk", "unexplained_file"):
        if (facts.get("coverage") or {}).get("state") == UNCOVERED:
            subject = str((facts.get("onboardingTrace") or {}).get("subject"))
        else:
            subject = row_subject(item) or subject
    elif kind == "family_route_condition":
        subject = subject.split("#", 1)[0]
    return context.rows_by_subject.get(subject)


def item_verdict(item: Mapping[str, Any], context: GateContext) -> ItemVerdict:
    """The registered predicate's verdict, with its actual row or counted-change witness.

    Failed reads are undecided, not a pass and not a guessed open reason. The caller
    supplies open invariant subjects before judging family items, as the gate does.
    """
    row = _witness_row(item, context)
    try:
        reason = item_open_reason(item, context)
        # If the predicate answers without history, that change -- not an incidental
        # row about the same subject -- is its witness.
        if (
            reason is None
            and row is not None
            and item_open_reason(item, replace(context, history=None)) is None
        ):
            row = None
    except GitReadFailed as error:
        return ItemVerdict(state="undecided", reason=str(error)[:12000], row_id=row)
    if reason is not None:
        return ItemVerdict(state="open", reason=reason[:12000], row_id=row)
    return ItemVerdict(state="answered", witness=row or "counted change", row_id=row)


# --------------------------------------------------------------------------------------------------
# Invariant rows (rule 2)
# --------------------------------------------------------------------------------------------------


def _ids(facts: Iterable[Any]) -> set[str]:
    return {str(one["id"]) for one in facts if isinstance(one, Mapping) and "id" in one}


def required_covers(item: Mapping[str, Any]) -> set[str]:
    """The entries an invariant row must cover: the raising and stale entries, and every added,
    retired or re-anchored one."""

    facts = item.get("facts") or {}
    entries = facts.get("entries") or ()
    if item.get("kind") == "stale_invariant":
        required = _ids(entries)  # every entry a stale item names is stale at base
    else:
        required = _ids(
            one
            for one in entries
            if isinstance(one, Mapping) and one.get("class") in COVERING_CLASSES
        )
    for key in ("added", "retired", "reanchored"):
        required |= _ids(facts.get(key) or ())
    return required


def _not_current(row: InvariantRow, context: GateContext) -> list[str]:
    reasons = []
    for cover in row.covers:
        observed = context.entry_state(cover.id)
        if observed is not None and observed.state != "current":
            reasons.append(f"{cover.id} is {observed.state} ({observed.reason})")
    return reasons


def invariant_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 2's invariant-row currentness for a ``touched_invariant`` or ``stale_invariant``."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, InvariantRow):
        return f"the leaf's history file holds no invariant row about {subject}"
    missing = sorted(required_covers(item) - {cover.id for cover in row.covers})
    revision = context.candidate.revision(subject)
    record = (item.get("facts") or {}).get("record") or {}
    hidden = changed_record_row_violation(
        row, base_revision=record.get("baseRevision"), candidate_revision=revision
    )
    if hidden is not None:
        governing = f"row {row.id}: {hidden}; restate that changed row so that it governs again"
        return governing + (
            f"; row {row.id} does not cover {', '.join(missing)}" if missing else ""
        )
    if missing:
        return f"row {row.id} does not cover {', '.join(missing)}"
    if row.revision != revision:
        return (
            f"row {row.id} records revision {row.revision}, but {subject} is at revision "
            f"{revision} in K_C"
        )
    stale = _not_current(row, context)
    if stale:
        return f"row {row.id} covers entries not current at C: {'; '.join(stale)}"
    return None


# --------------------------------------------------------------------------------------------------
# Family rows (rule 2)
# --------------------------------------------------------------------------------------------------


def _again(disposition: str) -> str:
    """What naming a stale family row again needs: a changed row carries its effect again."""

    return "a changed row with its effect" if disposition == "changed" else f"a {disposition} row"


def family_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 2's family-row currentness for a ``reached_family`` item."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, FamilyRow):
        return f"the leaf's history file holds no family row about {subject}"
    revision = context.candidate.revision(subject)
    if row.revision is not None and row.revision != revision:
        return (
            f"row {row.id} records own family revision {row.revision}, but {subject} is at "
            f"revision {revision} in K_C; the family changed after that judgment, so name "
            f"{subject} again in the hand-off's 'history' ({_again(row.disposition)}) and "
            "let the writer record the current revision"
        )
    facts = item.get("facts") or {}
    members = _ids(facts.get("members") or ())
    return (
        _hidden_guarantee_reason(row, facts.get("reachedBy") or ())
        or _examined_set_reason(row, members)
        or _moved_members_reason(row, context)
        or _uncovered_members_reason(members, context)
    )


def _hidden_guarantee_reason(row: FamilyRow, reached_by: Iterable[Any]) -> str | None:
    """While K_C restates the family's guarantee, the family's one governing row is ``changed``."""

    hidden = changed_family_row_violation(row, guarantee_changed=GUARANTEE_CHANGED in reached_by)
    if hidden is None:
        return None
    return f"row {row.id}: {hidden}; restate that changed row so that it governs again"


def _examined_set_reason(row: FamilyRow, members: set[str]) -> str | None:
    """``examined`` must name exactly the union of the family's K_B and K_C members."""

    examined = {member.id for member in row.examined}
    unexamined, extra = sorted(members - examined), sorted(examined - members)
    parts = [f"does not examine {', '.join(unexamined)}"] if unexamined else []
    parts += [f"examines non-members {', '.join(extra)}"] if extra else []
    if not parts:
        return None
    return f"row {row.id} {' and '.join(parts)} (examined must be the K_B and K_C members)"


def _moved_members_reason(row: FamilyRow, context: GateContext) -> str | None:
    """Each examined revision is the member's K_C revision, or the member left K_C (D7)."""

    moved = sorted(
        f"{member.id} (examined at {member.revision}, now {now})"
        for member in row.examined
        if (now := context.candidate.revision(member.id)) is not None and now != member.revision
    )
    if not moved:
        return None
    return f"row {row.id} examined members whose revision changed since: {', '.join(moved)}"


def _uncovered_members_reason(members: set[str], context: GateContext) -> str | None:
    """No member may have an open invariant item."""

    uncovered = sorted(members & context.open_invariants)
    return f"members without a current invariant row: {', '.join(uncovered)}" if uncovered else None


# --------------------------------------------------------------------------------------------------
# The registrants' own predicates
# --------------------------------------------------------------------------------------------------


def _by_rows(
    predicate: Callable[[Mapping[str, Any], Mapping[str, str]], bool],
) -> ItemPredicate:
    def decide(item: Mapping[str, Any], context: GateContext) -> str | None:
        if not predicate(item, context.rows_by_subject):
            return None
        return "no satisfying row or change answers it"

    return decide


def _route_condition_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    if not family_route_item_open(item, context.history):
        return None
    facts = item.get("facts") or {}
    if facts.get("recordSatisfiesRoutes") is not True:
        return "the family record in K_C does not satisfy MIK-R04 (routes), or no family row answers it"
    return "no family row about the family has a satisfying disposition"


# --------------------------------------------------------------------------------------------------
# Onboarding rows (MIK-R53 rules 1-4)
# --------------------------------------------------------------------------------------------------


def _row_own_closed(context: GateContext, subject: str, history: HistoryFile | None) -> bool | None:
    """The winning row's own file ``closed`` flag for ``subject`` (MIK-R53 rule 4).

    A merged history carries only its latest file's flag, losing the owning file's
    provenance across attempts. The candidate's parsed history files keep every file,
    so the winning row's own flag is resolved from them, mirroring the trace module's
    per-file reading. Falls back to the merged flag where the parsed files are absent.
    """

    fallback = None if history is None else history.closed
    candidate = getattr(context, "candidate", None)
    parsed = getattr(candidate, "parsed", None)
    files = getattr(parsed, "history_files", None)
    if not isinstance(files, dict) or context.owner is None:
        return fallback
    owned = [one for one in files.values() if getattr(one, "owner_id", None) == context.owner]
    if not owned:
        return fallback
    return winning_row_closed(owned).get(subject, fallback)


def _onboarding_trace_open(
    subject: str,
    facts: Mapping[str, Any],
    history: HistoryFile | None,
    context: GateContext | None = None,
) -> str | None:
    """Why the onboarding trace ``subject`` is open over stored ``facts`` (MIK-R53 rules 1-4).

    Only a current eligible authored row answers it: the leaf's latest row about the
    subject answers while its ``sources`` equal the facts' blobs, with ``updated`` rows
    additionally requiring the facts' counted change; a sourceless row stays valid in a
    closed file and answers nothing in an open one. A counted card edit alone never
    answers; the row is required.
    """

    if not isinstance(facts, Mapping) or "sidecarUnreadable" in facts:
        return "the card's sidecar cannot be parsed in K_C; repair it through the writer"
    row = None if history is None else history.row_about(subject)
    if not isinstance(row, OnboardingTraceRow):
        return f"the leaf's history file holds no onboarding row about {subject}"
    if context is None:
        closed = None if history is None else history.closed
    else:
        closed = _row_own_closed(context, subject, history)
    return _onboarding_row_currency(row, subject, facts, closed)


def _onboarding_row_currency(
    row: OnboardingTraceRow, subject: str, facts: Mapping[str, Any], closed: bool | None
) -> str | None:
    """Why the leaf's ``row`` about ``subject`` is open over the stored ``facts``."""

    blobs = facts.get("sourceBlobs")
    counted = facts.get("countedChange") is True
    if onboarding_row_answers(
        row, closed is True, blobs if isinstance(blobs, dict) else {}, counted
    ):
        return None
    if row.sources is None:
        return (
            f"row {row.id} predates the source binding and answers nothing in an open file; "
            f"name {subject} again through the writer so the row binds the current blobs"
        )
    if not isinstance(blobs, dict) or not blobs:
        return (
            f"the item for {subject} carries no source blobs; recompute the worklist and name "
            f"{subject} again through the writer"
        )
    if not row_sources_current(row, blobs):
        return (
            f"the source file(s) changed after row {row.id} was written; read the change and "
            f"name {subject} again through the writer so the row binds the current blobs"
        )
    return (
        f"row {row.id} is an updated row but the card has no counted change between the "
        f"memory base and the candidate; update the card or name {subject} again as a "
        "no_impact row through the writer"
    )


def onboarding_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 3's onboarding-row currency for an ``onboarding_trace`` item."""

    return _onboarding_trace_open(
        str(item.get("subject")), item.get("facts") or {}, context.history, context
    )


def unexplained_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """MIK-R10's satisfying rule over a stored item, with MIK-R53's currency for uncovered files.

    A covered item is answered by its ``no_invariant`` row, as before. An uncovered item is
    answered by the same current onboarding row that answers the file's own trace item.
    """

    facts = item.get("facts") or {}
    coverage = facts.get("coverage") or {}
    state = coverage.get("state") if isinstance(coverage, Mapping) else None
    if state == COVERED:
        if unexplained_satisfied_by(item, context.rows_by_subject) is not None:
            return None
        return "no no_invariant row answers it"
    if state != UNCOVERED:
        return "the item states no coverage, so nothing can answer it"
    trace = facts.get("onboardingTrace")
    if not isinstance(trace, Mapping):
        return "the item names no onboarding trace, so nothing answers it"
    return _onboarding_trace_open(str(trace.get("subject")), trace, context.history, context)


# --------------------------------------------------------------------------------------------------
# Membership and record rows (MIK-R53 rules 6 and 8)
# --------------------------------------------------------------------------------------------------


def membership_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 6's membership-row currency for an ``unassigned_invariant`` item."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, MembershipRow):
        return f"the leaf's history file holds no membership row about {subject}"
    invariant = membership_invariant(subject)
    revision = None if invariant is None else context.candidate.revision(invariant)
    if revision is not None and row.revision != revision:
        return (
            f"row {row.id} records revision {row.revision}, but {invariant} is at revision "
            f"{revision} in K_C; name {subject} again in the hand-off's 'history' and let "
            "the writer record the current revision"
        )
    return None


def record_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 8's record-row currency for a ``touched_record`` item."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, RecordRow):
        return f"the leaf's history file holds no record row about {subject}"
    facts = item.get("facts") or {}
    if row.revision != facts.get("revision"):
        return (
            f"row {row.id} records revision {row.revision}, but {subject} is at revision "
            f"{facts.get('revision')} in K_C; name {subject} again in the hand-off's "
            "'history' and let the writer record the current revision"
        )
    expected = _expected_record_disposition(facts)
    if row.disposition != expected:
        return (
            f"row {row.id} is a {row.disposition} row, but {subject} takes a {expected} row "
            f"(revision {facts.get('baseRevision')} to {facts.get('revision')}, status "
            f"{facts.get('baseStatus')} to {facts.get('status')}); name {subject} again as "
            f"a {expected} row through the writer"
        )
    return None


def _expected_record_disposition(facts: Mapping[str, Any]) -> str:
    """The disposition a ``touched_record`` item takes (MIK-R53 rule 8).

    Newly retired wins at any revision; ``changed`` applies to a higher revision that is
    not newly retired; otherwise ``no_impact``.
    """

    if facts.get("status") == "retired" and facts.get("baseStatus") != "retired":
        return "retired"
    base_revision, revision = facts.get("baseRevision"), facts.get("revision")
    if isinstance(base_revision, int) and isinstance(revision, int) and revision > base_revision:
        return "changed"
    return "no_impact"


register_gate_predicate("touched_invariant", invariant_row_open)
register_gate_predicate("stale_invariant", invariant_row_open)
register_gate_predicate("reached_family", family_row_open)
register_gate_predicate("family_route_condition", _route_condition_open)
register_gate_predicate("onboarding_trace", onboarding_row_open)
register_gate_predicate("unexplained_hunk", unexplained_row_open)
register_gate_predicate("unexplained_file", unexplained_row_open)
register_gate_predicate("planned_untouched", _by_rows(planned_item_open))


def reconsideration_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    try:
        return reconsideration_gate_reason(
            item, context.candidate, context.code, context.rows_by_subject
        )
    except CodeReadError as error:
        raise GitReadFailed(str(error)) from error


register_gate_predicate("reconsideration_candidate", reconsideration_row_open)


def link_target_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    facts = item.get("facts") or {}
    return (
        "K_C still holds the link whose target this leaf removed; remove or restate it "
        "through the writer; no row answers it"
        if holds_target(context.candidate, str(facts.get("record")), str(facts.get("targetKey")))
        else None
    )


register_gate_predicate("link_target", link_target_open)
register_gate_predicate("unassigned_invariant", membership_row_open)
register_gate_predicate("touched_record", record_row_open)


def weakness_anchor_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """MIK-R118 rule 7: the leaf answers whether a weakness it walked past still stands.

    The answer is a ``record`` row about the weakness at its K_C revision, and it is ``changed``
    exactly when the record file differs in K_C: the Curator moved the anchor with the code, or
    closed the weakness. A ``no_impact`` row says the weakness still stands where it stood, and
    answers the item too -- nothing here refuses a leaf for a weakness.
    """

    return _record_row_about(item, context, changed_required=False)


def changed_document_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """MIK-R124 rule 5: only this task's confirmed dependent record answers a changed document.

    A dependent's every write is a confirmation at the task's tip, so the answer is a ``changed``
    ``record`` row about it at its K_C revision -- and the record K_C holds must be the fresh
    one: it names the changed document, and its ``confirmed.task`` is not the task that confirmed
    it last time. A row alone proves nothing about the document (F07). A document this leaf
    deleted has its record removed; that removal is answered by a ``retired`` row about it.
    """

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, RecordRow):
        return f"the leaf's history file holds no record row about {subject}"
    indexed = context.candidate.parsed.records.get(subject)
    if indexed is None:
        return _removed_document_reason(subject, row)
    record = indexed.record
    if not isinstance(record, DependentRecord):
        return f"{subject} is a {type(record).__name__}, not the dependent record this item names"
    return _confirmation_answers(item, context, record)


def _removed_document_reason(subject: str, row: RecordRow) -> str | None:
    """Why a row does not answer for a record K_C no longer holds, or ``None`` when it does."""

    if row.disposition == "retired":
        return None
    return (
        f"row {row.id} is a {row.disposition} row, but K_C holds no record {subject}: the "
        "document it named was removed, so the row that answers it is a retired row about it"
    )


def _confirmation_answers(
    item: Mapping[str, Any], context: GateContext, record: DependentRecord
) -> str | None:
    """Why this task's confirmation is not yet the record's, or ``None`` when it is."""

    reason = _record_row_about(item, context, changed_required=True)
    if reason is not None:
        return reason
    facts = item.get("facts") or {}
    if record.document != facts.get("document"):
        return (
            f"{record.id} names {record.document!r}, not the changed document "
            f"{facts.get('document')!r}: confirm the document this leaf changed"
        )
    # The confirmation must be *this* task's, and it must be newer than the one the item was
    # raised against: a record still confirmed by the previous confirmer is that confirmer's
    # judgment, and a third party's is nobody's here (F07).
    if record.confirmed.task == facts.get("confirmedTask"):
        return (
            f"{record.id} is still confirmed by {record.confirmed.task!r}; a changed row names "
            "the confirmation this task made, not the previous one -- write the dependent "
            "through the writer in this task, with this task's confirming task and "
            "one-sentence judgment"
        )
    if context.owner is not None and record.confirmed.task != context.owner:
        return (
            f"{record.id} is confirmed by {record.confirmed.task!r}, but this task is "
            f"{context.owner!r}: a changed row names the confirmation this task made, and no other "
            "task's confirmation answers it"
        )
    stale = _stale_confirmation_reason(record, context)
    if stale is not None:
        return stale
    return None


def observe_anchor(code: Any, anchor: Any) -> EntryObservation:
    """Observe one dependent anchor link at C, exactly as the reader observes a weakness anchor."""

    entry = Entry(
        id="",
        kind="realization",
        invariant="",
        path=anchor.path or "",
        sidecar="",
        document={"anchor": anchor.to_document()},
    )
    return observe_entry(entry, code)


def _stale_confirmation_reason(record: DependentRecord, context: GateContext) -> str | None:
    """Why the confirmation does not name the code at C, or ``None`` when it does.

    A confirmation records the values the links were read at, so a link whose value changed since
    is a confirmation of the previous code: it answers a document that moved with its links, not
    one whose code moved under it (F07).
    """

    for link in record.stands_on:
        reason = _link_moved_reason(record, link, context)
        if reason is not None:
            return reason
    return None


def _link_moved_reason(record: DependentRecord, link: Any, context: GateContext) -> str | None:
    """Why one stand-on link is no longer the value the confirmation recorded, or ``None``."""

    if isinstance(link, DependentRecordLink):
        return _record_link_reason(record, link, context)
    if isinstance(link, DependentAnchorLink):
        return _anchor_link_reason(record, link, context)
    if not isinstance(link, DependentFileLink):
        return None
    observed = context.code.files.get(link.path)
    if observed is None:
        return (
            f"{record.id} stands on {link.path}, which C does not hold: the confirmation names "
            "code that is not there -- write the dependent again, or remove the link"
        )
    if link.blob == observed:
        return None
    return (
        f"{record.id} records {link.path} at blob {link.blob}, but C holds {observed}: the "
        "confirmation names code that has moved -- write the dependent again in this task so "
        "its links and judgment are taken at C"
    )


def _record_link_reason(
    record: DependentRecord, link: DependentRecordLink, context: GateContext
) -> str | None:
    """A record link is the revision the candidate holds, or it is not this confirmation's."""

    current = context.candidate.revision(link.id)
    if current is None:
        return (
            f"{record.id} stands on {link.id}, which the candidate does not hold: the "
            "confirmation names a record that is not there -- write the dependent again, "
            "or remove the link"
        )
    if link.revision == current:
        return None
    return (
        f"{record.id} records {link.id} at revision {link.revision}, but it stands at "
        f"{current} in the candidate: the confirmation names a record that has moved -- "
        "write the dependent again so its links are taken at C"
    )


def _anchor_link_reason(
    record: DependentRecord, link: DependentAnchorLink, context: GateContext
) -> str | None:
    """An anchor link is current only where its locator still resolves to the recorded bytes."""

    observed = observe_anchor(context.code, link.anchor)
    if observed.state == "unverifiable":
        return (
            f"{record.id} stands on an anchor in {link.anchor.path} that cannot be read at C "
            f"({observed.reason}): a confirmation whose code cannot be read is not proved at C "
            "-- write the dependent again in this task"
        )
    if observed.state == "current":
        return None
    return (
        f"{record.id} records {link.anchor.content!r} at {link.anchor.path} "
        f"{link.anchor.locator.kind}, but C holds "
        f"{observed.observed_content or 'nothing at that locator'}: the confirmation names "
        "code that has moved -- write the dependent again in this task so its links and "
        "judgment are taken at C"
    )


def _record_row_about(
    item: Mapping[str, Any], context: GateContext, *, changed_required: bool
) -> str | None:
    """The shared rule: the leaf's record row about the subject, at K_C's revision."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, RecordRow):
        return f"the leaf's history file holds no record row about {subject}"
    revision = context.candidate.revision(subject)
    if revision is not None and row.revision != revision:
        return (
            f"row {row.id} records revision {row.revision}, but {subject} is at revision "
            f"{revision} in K_C; name {subject} again in the hand-off's 'history' and let the "
            "writer record the current revision"
        )
    if changed_required and row.disposition != "changed":
        return (
            f"row {row.id} is a {row.disposition} row, but {subject} names a document this leaf "
            "changed; only the confirmed record answers it -- write the dependent through the "
            "writer (its every write is a confirmation) and name it as a changed row"
        )
    return None


register_gate_predicate("weakness_anchor", weakness_anchor_open)
register_gate_predicate("changed_document", changed_document_open)
