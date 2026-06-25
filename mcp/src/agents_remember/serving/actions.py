"""The POST action layer: pure availability mapping (slice 4b) + gate decisions (slice 6b).

``evaluate_action`` stays a **pure** function over a projection -- it resolves the target,
maps the requested action onto an HTTP status, and (slice 6b) emits a ``GateDecisionIntent``
for a gate-decision verb -- so the rules stay unit-testable without a client; ``app.py``
owns the routing *and* the one durable side effect (executing that intent via the gate store).

Two action families:

* **Gate decisions** (``approve`` / ``reject`` / ``request-revision`` / ``cancel``, slice
  6b): ``evaluate_action`` returns a ``GateDecisionIntent`` targeting the lifecycle; the
  router records it as a **developer-attributed** decision (``decidedBy="developer"`` /
  ``decidedVia="dashboard"``). That is the un-forgeable counterpart to the agent's
  ``decidedBy="model"`` path, and server-side closeout enforcement makes it binding. This
  deliberately revises the slice-4b stance that "the dashboard never mutates gate state":
  for gate *decisions* it now does, because the operator's approval must be recorded where
  the mutating tool reads it.
* **Lifecycle transitions** (``resume`` / ``integrate`` / ``cleanup``): unchanged 4b
  skeleton -- checked against the reducer's **precomputed** ``ActionAvailability`` (the UI
  never decides safety -- North-Star), attributed, and acknowledged ``202`` without mutation.
  Disabled actions return ``409`` with the reducer's ``disabledReason``; unknown targets ``404``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from agents_remember.observer.events import Actor

if TYPE_CHECKING:
    from agents_remember.observer.projection import ActionAvailability, WorkspaceProjection


class ActionRequest(BaseModel):
    """A dashboard action request: which entity, and who is asking.

    ``source`` is always ``"dashboard"`` (set server-side, not trusted from the body);
    ``actor`` is the constrained provenance the event substrate already uses.
    """

    model_config = ConfigDict(extra="forbid")

    target: str | None = None
    actor: Actor = "developer"
    gateId: str | None = None
    note: str | None = None


# The gate-decision verbs the dashboard can POST (slice 6b); distinct from the
# lifecycle-transition actions (resume / integrate / cleanup) the reducer emits.
GATE_DECISION_ACTIONS = ("approve", "reject", "request-revision", "cancel")


@dataclass(frozen=True)
class GateDecisionIntent:
    """A gate decision the router must durably record (slice 6b).

    ``lifecycle_id`` is the POST target; ``decision`` is the verb. The router
    resolves the lifecycle's latest open gate and writes a developer-attributed
    decision -- the pure evaluator never touches the store.
    """

    lifecycle_id: str | None
    decision: str
    gate_id: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ActionOutcome:
    """The decided HTTP status + JSON body for one action request.

    ``gate_decision`` is set (slice 6b) when the action is a gate-decision verb, and
    the router executes it against the gate store; the evaluator itself stays pure.
    """

    status_code: int
    body: dict[str, Any]
    gate_decision: GateDecisionIntent | None = None


def _find_actions(
    projection: WorkspaceProjection, target: str
) -> list[ActionAvailability] | None:
    """The target node's precomputed actions (lifecycle by id, enclosure by name), or None."""
    for lifecycle in projection.lifecycles:
        if lifecycle.id == target:
            return lifecycle.actions
    for enclosure in projection.enclosures:
        if enclosure.enclosure == target:
            return enclosure.actions
    return None


def evaluate_action(
    projection: WorkspaceProjection,
    action: str,
    target: str | None,
    *,
    actor: str,
    now: str,
    gate_id: str | None = None,
    note: str | None = None,
) -> ActionOutcome:
    """Map (action, target) onto a status + body; gate-decision verbs carry an intent (6b)."""
    if action in GATE_DECISION_ACTIONS:
        if action == "reject" and not (note or "").strip():
            return ActionOutcome(
                400,
                {
                    "status": "missing-rejection-reason",
                    "detail": "reject decisions require a reason",
                    "target": target,
                },
            )
        if target is None and not (action == "cancel" and gate_id):
            return ActionOutcome(
                400,
                {
                    "status": "missing-target",
                    "detail": "gate decisions require a lifecycle target unless cancelling a gate id",
                    "action": action,
                },
            )
        intent: dict[str, Any] = {
            "actor": actor,
            "source": "dashboard",
            "ts": now,
            "action": action,
        }
        if target is not None:
            intent["target"] = target
        if gate_id is not None:
            intent["gateId"] = gate_id
        if note is not None:
            intent["note"] = note
        return ActionOutcome(
            202,
            {"status": "received", "detail": "gate decision recorded", "intent": intent},
            gate_decision=GateDecisionIntent(
                lifecycle_id=target,
                decision=action,
                gate_id=gate_id,
                note=note,
            ),
        )
    if target is None:
        return ActionOutcome(
            400,
            {"status": "missing-target", "detail": "action requires a target", "action": action},
        )
    actions = _find_actions(projection, target)
    if actions is None:
        return ActionOutcome(404, {"status": "unknown-target", "target": target})
    availability = next((entry for entry in actions if entry.action == action), None)
    if availability is None:
        return ActionOutcome(
            409,
            {"status": "unavailable", "detail": f"no such action: {action}", "target": target},
        )
    if not availability.enabled:
        body: dict[str, Any] = {
            "status": "disabled",
            "detail": availability.disabledReason or "action not currently safe",
            "action": action,
            "target": target,
        }
        if availability.nextSafeAction is not None:
            body["nextSafeAction"] = availability.nextSafeAction
        return ActionOutcome(409, body)
    intent = {"actor": actor, "source": "dashboard", "ts": now, "action": action, "target": target}
    return ActionOutcome(
        202,
        {"status": "received", "detail": "attributed intent recorded; enforced in slice 06",
         "intent": intent},
    )
