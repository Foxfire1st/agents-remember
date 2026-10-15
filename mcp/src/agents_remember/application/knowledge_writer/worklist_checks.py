"""Persisted-worklist refusals shared with the gate (MIK-R54 rule 1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    InvariantRow,
    changed_family_row_violation,
    changed_record_row_violation,
    membership_invariant,
)
from agents_remember.models.knowledge_files.unexplained import row_subject

from .handoff import HandoffDocument, Problem

if TYPE_CHECKING:
    from .authoring import Authoring

RECOMPUTE = "recompute the worklist with `memory_quality_check`, or correct the subject"


def worklist_shape_problem(worklist: Any) -> Problem | None:
    """Validate consumed shapes before preprocessing or authoring can use them."""
    if worklist is None:
        return Problem(
            "worklist",
            "this leaf has no persisted worklist; compute it with `memory_quality_check`",
        )
    if not isinstance(worklist, Mapping) or not isinstance(worklist.get("items"), list):
        return Problem(
            "worklist",
            "the persisted worklist is unreadable: items must be a list of objects; compute it with `memory_quality_check`",
        )
    if any(_unreadable_item(item) for item in worklist["items"]):
        return Problem(
            "worklist",
            "the persisted worklist is unreadable: malformed item fields; compute it with `memory_quality_check`",
        )
    return None


def _unreadable_item(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return True
    if any(not isinstance(item[key], str) for key in ("id", "kind", "subject") if key in item):
        return True
    facts = item.get("facts", {})
    if not isinstance(facts, Mapping):
        return True
    record = facts.get("record", {}) if item.get("kind") != "link_target" else {}
    if not isinstance(record, Mapping) or (
        record.get("baseRevision") is not None and type(record["baseRevision"]) is not int
    ):
        return True
    if not isinstance(facts.get("onboardingTrace", {}), Mapping):
        return True
    return _unreadable_collections(facts)


def _unreadable_collections(facts: Mapping[str, Any]) -> bool:
    for key, member_type in (("reachedBy", str), ("sources", str), ("changed", Mapping)):
        values = facts.get(key, [])
        if not isinstance(values, list) or any(not isinstance(one, member_type) for one in values):
            return True
    for changed in facts.get("changed", []):
        entry = changed.get("entry", {})
        if not isinstance(entry, Mapping) or not isinstance(entry.get("candidate", {}), Mapping):
            return True
    return False


def worklist_problems(
    operation: Authoring, document: HandoffDocument, worklist: Mapping[str, Any] | None
) -> list[Problem]:
    if operation.owner.kind != "leaf":
        return []
    if worklist is None:
        return [
            Problem(
                "worklist",
                "this leaf has no persisted worklist; compute it with `memory_quality_check`",
            )
        ]
    items = worklist.get("items")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        return [
            Problem(
                "worklist",
                "the persisted worklist is unreadable: items must be a list of objects; compute it with `memory_quality_check`",
            )
        ]
    subjects = {item.get("subject") for item in items if isinstance(item.get("subject"), str)}
    subjects.update(subject for item in items if (subject := row_subject(item)) is not None)
    subjects.update(
        invariant for subject in tuple(subjects) if (invariant := membership_invariant(subject))
    )
    return [
        *_subject_problems(operation, document, subjects),
        *_judgment_problems(operation, items),
    ]


def _subject_problems(
    operation: Authoring, document: HandoffDocument, subjects: set[str]
) -> list[Problem]:
    problems: list[Problem] = []
    for request in document.rows:
        subject = operation.handles.get(request.subject.removeprefix("handoff:"), request.subject)
        if request.disposition == "raise":
            continue
        if (
            request.disposition == "still_rejected" or subject.startswith(("planned:", "hunk:"))
        ) and subject not in subjects:
            problems.append(
                Problem(
                    f"history[{request.position}]",
                    f"{subject}: it answers no item of the leaf's worklist; {RECOMPUTE}",
                )
            )
    for row in operation.rows:
        subject = row.subject
        if row.disposition == "raise":
            continue
        found = operation.state.record(subject)
        origin = found[2].get("origin", {}) if found else {}
        if origin.get("leaf") == operation.owner.id and subject not in subjects:
            problems.append(
                Problem(
                    subject, "a record first authored in this leaf needs no row (MIK-R07 rule 6)"
                )
            )
    return problems


def _judgment_problems(operation: Authoring, items: list[Any]) -> list[Problem]:
    problems: list[Problem] = []
    ids = {outcome.id for outcome in operation.rows}
    for path, history in operation.state.changed_documents().items():
        if not path.startswith("knowledge/history/"):
            continue
        for raw in history.get("rows", []):
            if raw.get("id") not in ids:
                continue
            problem = _hidden_change(operation, raw, items)
            if problem:
                problems.append(
                    Problem(str(raw["subject"]), f"{problem}; name the `changed` row again")
                )
    return problems


def _hidden_change(operation: Authoring, raw: Mapping[str, Any], items: list[Any]) -> str | None:
    for item in items:
        if item.get("subject") != raw.get("subject"):
            continue
        facts = item.get("facts") or {}
        found = operation.state.record(str(raw["subject"]))
        revision = found[2].get("revision") if found else None
        violation = None
        try:
            if item.get("kind") in {"touched_invariant", "stale_invariant"}:
                record = facts.get("record") or {}
                violation = changed_record_row_violation(
                    InvariantRow.model_validate(raw),
                    base_revision=record.get("baseRevision"),
                    candidate_revision=revision,
                )
            if item.get("kind") == "reached_family":
                violation = changed_family_row_violation(
                    FamilyRow.model_validate(raw),
                    guarantee_changed="guarantee-changed" in (facts.get("reachedBy") or []),
                )
        except ValidationError:
            continue  # normal row authoring already names its shape refusal
        if violation:
            return violation
    return None
