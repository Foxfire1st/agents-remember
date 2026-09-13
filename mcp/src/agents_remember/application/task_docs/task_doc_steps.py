"""The task-document step plane: exact addressing, and one operation per intent.

``set_step`` updates exactly one existing unit and never creates; ``add_step``
creates exactly one and never updates; ``remove_step`` deletes exactly one and
records why; ``skip_step`` keeps the unit, resolves it, and records why it was
deliberately not done. All four share one addressing rule -- ``parent`` selects the
namespace, and zero or multiple matches refuse rather than guess.

That rule is the fix for a real defect: ``set_step`` used to be an unconditional
upsert that matched a bare id only at top level, so a call meant for the substep
``S1.1`` found nothing, minted a new top-level step titled after the id, reported
success, and left the real substep pending. ``_upsert`` also dropped a top-level
``note`` on the floor, because the top-level key set omitted it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agents_remember.tasks import Step, SubStep

from .task_doc_route_review import TaskDocError

_STEP_UPDATE_KEYS = ("title", "status", "note")
"""The step fields a caller may restate. ``note`` is here for both levels: a top-level note used
to be accepted and silently dropped, because the top-level key set omitted it."""


def step_reason(payload: dict[str, Any] | None) -> str | None:
    """The caller's nonblank audit reason for removing or skipping a unit, else ``None``."""
    if not payload:
        return None
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason.strip()


def _steps_with_id(items: list[dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    """Every entry whose id is this one. Counted, never collapsed: one is the contract."""
    return [item for item in items if str(item.get("id")) == step_id]


def _step_id(payload: dict[str, Any], operation: str) -> str:
    step_id = str(payload.get("id") or "").strip()
    if not step_id:
        raise TaskDocError(f"{operation} requires step.id")
    return step_id


def _step_container(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    operation: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """The addressed step list: top level, or one exact parent's substeps.

    ``parent`` selects the namespace, and a named parent must exist exactly once; it is
    never reinterpreted as a top-level id, which is how the old upsert could write into
    the wrong scope without saying so.
    """
    steps: list[dict[str, Any]] = data.setdefault("steps", [])
    parent_id = str(payload.get("parent") or "").strip()
    if not parent_id:
        return steps, None
    parents = _steps_with_id(steps, parent_id)
    parent = _one_exact_match(parents, f"parent step {parent_id!r}", operation=operation)
    return parent.setdefault("substeps", []), parent_id


def _step_miss_hint(steps: list[dict[str, Any]], step_id: str) -> str:
    """Name the likely addressing mistake behind a top-level miss.

    The concrete defect this protects: ``{'id': 'S1.1'}`` with no ``parent`` missed the
    substep and silently minted a top-level step instead. A miss now says where that id
    actually lives rather than reporting a bare "not found".
    """
    holders = [
        str(step.get("id")) for step in steps if _steps_with_id(step.get("substeps", []), step_id)
    ]
    if len(holders) == 1:
        return f"no top-level step {step_id!r}; did you mean parent {holders[0]!r}?"
    if holders:
        named = ", ".join(repr(holder) for holder in holders)
        return f"no top-level step {step_id!r}; {step_id!r} is a substep of {named} -- pass parent"
    prefix, _, child = step_id.rpartition(".")
    parents = _steps_with_id(steps, prefix) if prefix and child else []
    if parents and _steps_with_id(parents[0].get("substeps", []), child):
        return (
            f"no top-level step {step_id!r}; did you mean parent {prefix!r} with step.id {child!r}?"
        )
    return f"no top-level step {step_id!r}"


def exact_step_target(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    operation: str,
    hint: bool = False,
) -> tuple[dict[str, Any], str]:
    """Resolve exactly one existing step, at top level or under one exact parent.

    The single addressing rule every step operation shares. ``hint`` adds the "did you
    mean parent ...?" context that ``set_step``/``remove_step`` owe a caller whose bare
    id belongs to a substep; ``skip_step`` keeps its original wording.
    """
    step_id = _step_id(payload, operation)
    steps: list[dict[str, Any]] = data.setdefault("steps", [])
    parent_id = str(payload.get("parent") or "").strip()
    if not parent_id:
        matches = _steps_with_id(steps, step_id)
        if hint and not matches:
            raise TaskDocError(f"{operation}: {_step_miss_hint(steps, step_id)}")
        return _one_exact_match(
            matches, f"top-level step {step_id!r}", operation=operation
        ), step_id
    parents = _steps_with_id(steps, parent_id)
    parent = _one_exact_match(parents, f"parent step {parent_id!r}", operation=operation)
    children = _steps_with_id(parent.get("substeps", []), step_id)
    child = _one_exact_match(children, f"substep {parent_id!r}/{step_id!r}", operation=operation)
    return child, f"{parent_id}/{step_id}"


def _one_exact_match(
    matches: list[dict[str, Any]],
    label: str,
    *,
    operation: str,
) -> dict[str, Any]:
    if not matches:
        raise TaskDocError(f"{operation}: {label} not found")
    if len(matches) > 1:
        raise TaskDocError(f"{operation}: {label} is ambiguous")
    return matches[0]


def _require_step_payload(
    step: dict[str, Any] | None,
    kind: str,
    operation: str,
    missing_message: str | None = None,
) -> dict[str, Any]:
    if kind == "master":
        raise TaskDocError(f"{operation} is not valid for a master; use set_subtask")
    if not step:
        raise TaskDocError(missing_message or f"{operation} requires a step object")
    return step


def set_step(
    data: dict[str, Any],
    *,
    kind: str,
    step: dict[str, Any] | None,
) -> None:
    """Update exactly one existing unit; never create one."""
    payload = _require_step_payload(step, kind, "set_step")
    target, _qualified = exact_step_target(data, payload, operation="set_step", hint=True)
    target.update({key: payload[key] for key in _STEP_UPDATE_KEYS if key in payload})
    if "status" in payload:
        # An explicit status edit records executed/reworked state, not the old skip decision.
        target.pop("disposition", None)


def add_step(
    data: dict[str, Any],
    *,
    kind: str,
    step: dict[str, Any] | None,
) -> None:
    """Create exactly one new unit; never update one.

    ``id`` and ``title`` are both required, and an id that already exists in its scope
    is refused -- that caller wanted ``set_step``.
    """
    payload = _require_step_payload(step, kind, "add_step")
    step_id = _step_id(payload, "add_step")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise TaskDocError("add_step requires step.title")
    items, parent_id = _step_container(data, payload, operation="add_step")
    if _steps_with_id(items, step_id):
        where = f"substep {parent_id!r}/{step_id!r}" if parent_id else f"top-level step {step_id!r}"
        raise TaskDocError(f"add_step: {where} already exists; use set_step to update it")
    created: dict[str, Any] = {"id": step_id, "title": title}
    created.update({key: payload[key] for key in ("status", "note") if key in payload})
    items.append(created)


def remove_step(
    data: dict[str, Any],
    *,
    kind: str,
    step: dict[str, Any] | None,
) -> None:
    """Delete exactly one existing unit, recording why.

    ``remove_step`` means "this should never have existed" -- distinct from ``skip_step``
    ("this planned unit was deliberately not done"), which keeps and resolves the unit.
    The appended decision makes the removal traceable, so the audit entry substitutes for
    the lost step history. A ``done`` unit (developer ruling) and a ``Completed`` document
    (developer ruling, enforced in ``_enforce_terminal_status``) are both removable once
    the reason is given; that is what let the four-junk-step repair proceed on a completed
    leaf instead of forcing a full-document ``replace``.
    """
    payload = _require_step_payload(step, kind, "remove_step")
    reason = step_reason(payload)
    if reason is None:
        raise TaskDocError("remove_step requires a nonblank step.reason")
    target, qualified_id = exact_step_target(data, payload, operation="remove_step", hint=True)
    items, _parent_id = _step_container(data, payload, operation="remove_step")
    items[:] = [item for item in items if item is not target]
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "decision": f"Removed step {qualified_id}.",
            "rationale": reason,
        }
    )


def skip_step(
    data: dict[str, Any],
    *,
    kind: str,
    step: dict[str, Any] | None,
    lifecycle_id: str | None,
) -> None:
    """Keep one existing unresolved unit, mark it done, and record the intentional skip."""
    payload = _require_step_payload(
        step, kind, "skip_step", missing_message="skip_step requires step={id, reason, parent?}"
    )
    reason = step_reason(payload)
    if reason is None:
        raise TaskDocError("skip_step requires a nonblank step.reason")
    target, qualified_id = exact_step_target(data, payload, operation="skip_step")
    if target.get("status") == "done":
        raise TaskDocError(
            f"skip_step requires an unresolved target; {qualified_id} is already done"
        )
    recorded_at = datetime.now(UTC).isoformat(timespec="seconds")
    target["status"] = "done"
    target["disposition"] = {
        "kind": "intentionalSkip",
        "reason": reason,
        "recordedAt": recorded_at,
        "recordedVia": "task_doc.skip_step",
        "lifecycleId": lifecycle_id,
    }
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(
        {
            "at": recorded_at,
            "decision": f"Intentionally skip step {qualified_id}.",
            "rationale": reason,
        }
    )


def step_payloads(steps: list[Step]) -> list[dict[str, Any]]:
    """Each unit's addressing and progress facts, with its substeps nested underneath."""
    return [
        {
            "id": step.id,
            "title": step.title,
            "status": step.status,
            "note": step.note,
            "substeps": [_substep_payload(sub) for sub in step.substeps],
        }
        for step in steps
    ]


def _substep_payload(sub: SubStep) -> dict[str, Any]:
    return {"id": sub.id, "title": sub.title, "status": sub.status, "note": sub.note}
