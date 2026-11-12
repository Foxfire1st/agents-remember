"""Scalar and prose mutations of a task document's prepared content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.tasks.document import (
    INTENT_REVIEW_ERASURE_REFUSAL,
    INTENT_REVIEW_REFUSAL,
    TaskDocument,
    intent_review_erasure_refusal,
    intent_review_stage_refusal,
)

from .task_doc_phrases import _MUTABLE_FIELDS
from .task_doc_route_review import TaskDocError

if TYPE_CHECKING:
    from .task_doc_tools import _Edit

#: The one canonical role that records the developer's intent-review answer (MIK-R189 rule 8).
ARCHITECT_ROLE: Final[LauncherRole] = "architect"


def enforce_intent_review_candidate(
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
    *,
    caller: object | None = None,
) -> None:
    """MIK-R189 rules 2 & 8: refuse intent-review erasure, unauthorized writes, or open stage moves."""

    erasure = intent_review_erasure_refusal(operation, original, candidate)
    if erasure is not None:
        raise TaskDocError(erasure)
    before = original.intentReview if original is not None else None
    if candidate.intentReview != before and caller is not None:
        role = canonical_role(getattr(caller, "role", "") or "")
        if role != ARCHITECT_ROLE:
            raise TaskDocError(
                f"task_doc {operation} by a hosted {role!r} seat cannot write intentReview: the "
                "architect records the developer's answer once the task document exists, and a "
                "developer writing without a seat binding is admitted."
            )
    stage_refusal = intent_review_stage_refusal(original, candidate)
    if stage_refusal is not None:
        raise TaskDocError(stage_refusal)


def _refuse_raw_intent_review_stage_move(
    data: dict[str, Any], next_status: str | None, *, before_status: str | None = None
) -> None:
    """MIK-R189 rule 2: refuse moving a leaf to ``inProgress`` while its intent review is open."""

    prior = data.get("status") if before_status is None else before_status
    if (
        data.get("kind") != "subTask"
        or next_status != "inProgress"
        or prior not in {"planning", "abandoned", None}
    ):
        return
    review = data.get("intentReview")
    state = review.get("state") if isinstance(review, dict) else getattr(review, "state", None)
    if state in {"pending", "review-first"}:
        leaf_id = str(data.get("id") or "")
        raise TaskDocError(
            f"{INTENT_REVIEW_REFUSAL}: leaf {leaf_id!r} waits for the architect's go; its intent "
            f"review is {state!r}, so no start of it is admitted. The architect records the "
            "developer's answer as `go` in `intentReview` through task_doc, then the same start is "
            "admitted. Do not retry this start."
        )


def _apply_set_status(data: dict[str, Any], edit: _Edit) -> None:
    status = edit.fields.get("status")
    if status is None:
        raise TaskDocError("set_status requires fields.status")
    _refuse_raw_intent_review_stage_move(data, status)
    data["status"] = status


def _apply_set_field(data: dict[str, Any], edit: _Edit) -> None:
    updates = {key: value for key, value in edit.fields.items() if key in _MUTABLE_FIELDS}
    if not updates:
        raise TaskDocError(
            f"set_field requires at least one of: {', '.join(sorted(_MUTABLE_FIELDS))}"
        )
    if (
        "intentReview" in updates
        and updates["intentReview"] is None
        and data.get("intentReview") is not None
    ):
        raise TaskDocError(
            f"{INTENT_REVIEW_ERASURE_REFUSAL}: set_field cannot drop intentReview; it records "
            "the developer's answer before any start. Re-supply the same value, or change it "
            "with task_doc set_field fields.intentReview."
        )
    if "status" in updates:
        _refuse_raw_intent_review_stage_move(
            {**data, **updates}, updates["status"], before_status=data.get("status")
        )
    data.update(updates)


def _apply_append_decision(data: dict[str, Any], edit: _Edit) -> None:
    if not edit.decision:
        raise TaskDocError("append_decision requires a decision object")
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(edit.decision)


def _upsert_subtask(data: dict[str, Any], subtask: dict[str, Any]) -> None:
    number = subtask.get("number")
    if not number:
        raise TaskDocError("set_subtask requires subtask.number")
    refs: list[dict[str, Any]] = data.setdefault("subTasks", [])
    updates = {key: subtask[key] for key in ("name", "file", "status", "scope") if key in subtask}
    existing = next((ref for ref in refs if ref.get("number") == number), None)
    if existing is None:
        new_ref: dict[str, Any] = {"number": str(number), "name": subtask.get("name", str(number))}
        new_ref.update(updates)
        refs.append(new_ref)
    else:
        existing.update(updates)


def _upsert_section(data: dict[str, Any], section: dict[str, Any]) -> None:
    heading = section.get("heading")
    if not heading:
        raise TaskDocError("set_section requires section.heading")
    sections: list[dict[str, Any]] = data.setdefault("sections", [])
    updates = {key: section[key] for key in ("kind", "body") if key in section}
    existing = next((sec for sec in sections if sec.get("heading") == heading), None)
    if existing is None:
        new_section: dict[str, Any] = {"heading": heading}
        new_section.update(updates)
        sections.append(new_section)
    else:
        existing.update(updates)
