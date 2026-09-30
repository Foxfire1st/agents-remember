"""MIK-R14's ``reconsideration_candidate`` item kind: a decision whose reconsider target changed.

**What is read.** Every decision of K_B, and each of its ``reconsider_on`` links that names a
``rejected`` or ``deferred`` alternative (:func:`~agents_remember.models.knowledge_files.decisions.
reconsider_links`). A decision absent from K_B raises nothing: it was authored in this leaf.

**Triggers (rule 1).** A link's target counts as changed when

* ``record_file`` -- it is a record ID whose record file (location and bytes) differs between K_B
  and K_C: invariants, families, assumptions, limitations and the other facets;
* ``history_row`` -- this leaf's history file in K_C holds a row about it with the disposition
  ``changed``, ``moved``, ``deleted``, ``rerouted`` or ``retired``;
* ``anchor`` -- it is an anchor that MIK-R08 definition 4 classifies ``touched`` or
  ``moved_or_absent`` (classified like an entry, although it is not one);
* ``anchor_stale`` -- it is an anchor classified ``stale_at_base``: its recorded content is no
  longer the code's at B, so it would otherwise never fire again (review ruling F3);
* ``requirement_version`` -- it is a requirement endpoint that resolves through the requirement
  owner (:func:`~agents_remember.memory.knowledge.requirement_endpoint.resolve_requirement_endpoint`)
  and whose owning task's manifest now approves a newer version (rule 2); the facts name that
  version (``latestApproved``) and its packet (``latestPacket``). An unresolved endpoint,
  or a task without a manifest (``approvalState: unknown``), does not trigger.

A decision target never triggers: a change to a decision -- including the ``raise`` that sets its
status -- never chains on to other decisions (rule 3, one hop). A ``route:<dir>`` target fires
through the ``history_row`` trigger only (ruling Q1, 2026-09-30T04:37:56): a row of this leaf whose
subject is the route, or a family row about a family whose routes (in K_B or K_C) include it, with
the disposition ``rerouted``, ``retired`` or ``deleted``; no directory-absence trigger exists.
A decision that is superseded (derived, MIK-R13 rule 2) raises nothing: its links are listed in the
summary as ``skipped: superseded`` (ruling Q5). Nothing reads ``reconsider_when``: the prose is never
evaluated (Exclusions).

**Items (rules 3 and 5).** One item per decision alternative, subject
``reconsider:<DEC-ID>#<alternative index>``, whose facts name every changed target and the trigger
that fired. It is answered by this leaf's reconsideration row with the same subject
(``still_rejected`` or ``raise``); ``satisfiedBy`` names it, and
:func:`~agents_remember.models.knowledge_files.reconsideration.reconsideration_item_open` applies the
same rule to a stored item. The run's ``reconsideration`` summary lists every link it evaluated, with
each requirement endpoint's resolution and approval state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_worklist.classify import RAISING_CLASSES, Classification
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
    subject_row,
)
from agents_remember.memory.knowledge.requirement_endpoint import (
    requirement_approval,
    resolve_requirement_endpoint,
)
from agents_remember.models.knowledge_files.decisions import (
    ReconsiderLink,
    derived_status,
    reconsider_links,
    superseded_by,
)
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES
from agents_remember.models.knowledge_files.reconsideration import (
    ANCHOR,
    ANCHOR_STALE,
    HISTORY_ROW,
    RECONSIDERATION_ITEM_KIND,
    RECORD_FILE,
    REQUIREMENT_VERSION,
    TRIGGERING_DISPOSITIONS,
    link_target_key,
    reconsideration_item_open,
)
from agents_remember.models.knowledge_files.records import DecisionRecord
from agents_remember.models.knowledge_files.shapes import Anchor, RequirementReference

__all__ = [
    "RECONSIDERATION_KIND",
    "ReconsiderationInputs",
    "ReconsiderationRun",
    "reconsideration_candidates",
]

ABSENT: Final = "absent"
ROUTE_PREFIX: Final = "route:"
ROUTE_TRIGGERING_DISPOSITIONS: Final = frozenset({"rerouted", "retired", "deleted"})
SUPERSEDED: Final = "superseded"
_DECISION_PREFIX: Final = f"{RECORD_PREFIXES['decision']}-"

RECONSIDERATION_KIND: Final = register_item_kind(
    ItemKind(
        name=RECONSIDERATION_ITEM_KIND,
        subject="decision alternative (reconsider:<DEC-ID>#<alternative index>)",
        subject_pattern=r"^reconsider:",
        facts=(
            "decision",
            "alternative",
            "option",
            "alternativeStatus",
            "reconsiderWhen",
            "decisionStatus",
            "changed",
        ),
        satisfying_row=(
            "the leaf's reconsideration row with this subject: still_rejected (the rejection "
            "holds, with a reason) or raise (the decision becomes under_reconsideration and a "
            "question goes to the developer) (MIK-R14 rule 4)"
        ),
        row_lookup=subject_row("reconsideration"),
        owner="MIK-R14",
    )
)

AnchorClassifier = Callable[[str, Anchor], Classification]


@dataclass(frozen=True)
class ReconsiderationInputs:
    """What the triggers read from one worklist run."""

    base: KnowledgeSide
    candidate: KnowledgeSide
    classify_anchor: AnchorClassifier
    owner: str | None
    coordination_root: Path | None
    """Where requirement endpoints' owning tasks live; without it no endpoint resolves."""


@dataclass(frozen=True)
class ReconsiderationRun:
    """The items one run raised (with ``satisfiedBy``) and its summary of every evaluated link."""

    items: tuple[dict[str, Any], ...]
    summary: Mapping[str, Any]


def _digest(data: bytes | None) -> str:
    return ABSENT if data is None else f"sha256:{hashlib.sha256(data).hexdigest()}"


def _target_key(target: Any) -> str:
    return link_target_key(target if isinstance(target, str) else target.to_document())


def _skipped(link: ReconsiderLink) -> dict[str, Any]:
    return {
        "subject": link.subject,
        "link": link.link_index,
        "target": _target_key(link.link.target),
        "skipped": SUPERSEDED,
        "trigger": None,
    }


@dataclass
class _Evaluation:
    inputs: ReconsiderationInputs

    def run(self) -> ReconsiderationRun:
        decisions = [
            indexed.record
            for _id, indexed in sorted(self.inputs.base.parsed.records.items())
            if isinstance(indexed.record, DecisionRecord)
        ]
        superseding = superseded_by(decisions)
        history = self._history()
        rows = {} if history is None else {row.subject: row.id for row in history.rows}
        evaluated: list[dict[str, Any]] = []
        changed: dict[str, list[tuple[ReconsiderLink, dict[str, Any]]]] = {}
        for decision in decisions:
            skipped = derived_status(decision, superseding) == SUPERSEDED
            for link in reconsider_links(decision):
                fired = self._summarize(link, skipped, evaluated)
                if fired is not None:
                    changed.setdefault(link.subject, []).append((link, fired))
        records = {one.id: one for one in decisions}
        items = [
            self._item(subject, fired, records, superseding, rows)
            for subject, fired in sorted(changed.items())
        ]
        return ReconsiderationRun(tuple(items), {"links": evaluated})

    def _history(self) -> HistoryFile | None:
        owner = self.inputs.owner
        return None if owner is None else self.inputs.candidate.history(owner)

    def _summarize(
        self, link: ReconsiderLink, skipped: bool, evaluated: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Evaluate one link into the summary; its fired facts, or ``None``."""

        if skipped:  # ruling Q5: the successor carries its own links
            evaluated.append(_skipped(link))
            return None
        outcome = self._evaluate(link)
        evaluated.append(outcome["summary"])
        return outcome["fired"]

    # -- triggers ------------------------------------------------------------------------------

    def _evaluate(self, link: ReconsiderLink) -> dict[str, Any]:
        target = link.link.target
        summary: dict[str, Any] = {
            "subject": link.subject,
            "link": link.link_index,
            "target": _target_key(target),
        }
        fired: dict[str, Any] | None
        if isinstance(target, str):
            fired, note = self._record_trigger(target)
        elif isinstance(target, Anchor):
            fired, note = self._anchor_trigger(link, target)
        else:
            fired, note = self._requirement_trigger(target)
        summary.update(note)
        summary["trigger"] = None if fired is None else fired["trigger"]
        return {"summary": summary, "fired": None if fired is None else {**summary, **fired}}

    def _record_trigger(self, target: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if target.startswith(ROUTE_PREFIX):
            return self._route_trigger(target), {}
        if target.startswith(_DECISION_PREFIX):
            return None, {"note": "one hop: a change to a decision never chains on"}
        return self._record_file_trigger(target) or self._row_trigger(target), {}

    def _record_file_trigger(self, target: str) -> dict[str, Any] | None:
        before = self.inputs.base.record_file(target)
        after = self.inputs.candidate.record_file(target)
        if before == after:
            return None
        paths = [None if one is None else one[0] for one in (before, after)]
        return {
            "trigger": RECORD_FILE,
            "paths": {"base": paths[0], "candidate": paths[1]},
            "identity": [_digest(None if one is None else one[1]) for one in (before, after)],
        }

    def _row_trigger(self, target: str) -> dict[str, Any] | None:
        history = self._history()
        row = None if history is None else history.row_about(target)
        if row is None or row.disposition not in TRIGGERING_DISPOSITIONS:
            return None
        return {
            "trigger": HISTORY_ROW,
            "row": row.id,
            "disposition": row.disposition,
            "identity": [row.id, row.disposition],
        }

    def _route_trigger(self, target: str) -> dict[str, Any] | None:
        """Ruling Q1: a row of this leaf that reroutes, retires or deletes the route."""

        history = self._history()
        if history is None:
            return None
        route = target.removeprefix(ROUTE_PREFIX)
        governing = {
            family_id
            for side in (self.inputs.base, self.inputs.candidate)
            for family_id, record in side.families.items()
            if route in record.routes
        }
        for row in sorted(history.rows, key=lambda one: one.subject):
            if row.disposition not in ROUTE_TRIGGERING_DISPOSITIONS:
                continue
            if row.subject == target or row.subject in governing:
                return {
                    "trigger": HISTORY_ROW,
                    "row": row.id,
                    "rowSubject": row.subject,
                    "disposition": row.disposition,
                    "identity": [row.id, row.subject, row.disposition],
                }
        return None

    def _anchor_trigger(
        self, link: ReconsiderLink, target: Anchor
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        classified = self.inputs.classify_anchor(f"{link.subject}/links.{link.link_index}", target)
        note = {"class": classified.entry_class}
        stale = classified.entry_class == "stale_at_base"
        if classified.entry_class not in RAISING_CLASSES and not stale:
            return None, note
        return {
            "trigger": ANCHOR_STALE if stale else ANCHOR,
            "entry": classified.to_document(),
            "identity": [classified.entry_class, *classified.contents()[1:]],
        }, note

    def _requirement_trigger(
        self, target: RequirementReference
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        endpoint = resolve_requirement_endpoint(self.inputs.coordination_root, target)
        note: dict[str, Any] = {"endpoint": endpoint.state}
        if endpoint.state != "resolved" or endpoint.task_root is None:
            # An unresolved endpoint does not trigger (Failure And Recovery).
            return None, {**note, "code": endpoint.code, "approvalState": "unknown"}
        approval = requirement_approval(endpoint.task_root, target.id)
        note |= {"approvalState": approval.state, "latestApproved": approval.latest}
        if approval.detail:
            note["detail"] = approval.detail
        if not approval.newer_than(target.version):
            return None, note
        return {
            "trigger": REQUIREMENT_VERSION,
            "version": target.version,
            "latestPacket": approval.packet,
            "identity": [target.version, approval.latest],
        }, note

    # -- items ---------------------------------------------------------------------------------

    def _item(
        self,
        subject: str,
        fired: list[tuple[ReconsiderLink, dict[str, Any]]],
        records: Mapping[str, DecisionRecord],
        superseding: Mapping[str, tuple[str, ...]],
        rows: Mapping[str, str],
    ) -> dict[str, Any]:
        link = fired[0][0]
        record = records[link.decision]
        alternative = link.alternative
        changed = [
            {key: value for key, value in one.items() if key not in {"subject", "identity"}}
            for _link, one in fired
        ]
        facts = {
            "decision": link.decision,
            "alternative": link.index,
            "option": alternative.option,
            "alternativeStatus": alternative.status,
            "reconsiderWhen": alternative.reconsider_when,
            "decisionStatus": derived_status(record, superseding),
            "changed": changed,
        }
        identities = [
            [one["link"], one["target"], one["trigger"], one["identity"]] for _link, one in fired
        ]
        document = {
            "id": item_id(RECONSIDERATION_ITEM_KIND, subject, identities),
            "kind": RECONSIDERATION_ITEM_KIND,
            "subject": subject,
            "facts": facts,
        }
        open_ = reconsideration_item_open(document, rows)
        return {**document, "satisfiedBy": None if open_ else rows[subject]}


def reconsideration_candidates(inputs: ReconsiderationInputs) -> ReconsiderationRun:
    """Every ``reconsideration_candidate`` of one run, sorted by subject, and the link summary."""

    return _Evaluation(inputs).run()
