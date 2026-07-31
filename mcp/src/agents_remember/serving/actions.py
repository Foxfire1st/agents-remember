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
* **Attention dismissals** (``dismiss``, leaf-28 S5.2): lifecycle-bound acknowledgements
  for one queue occurrence, plus the repo-level ``actionable-drift`` one-shot signal.
  The router keeps only current acknowledgement state and prunes lifecycle rows with
  the lifecycle; ``gate-open`` dismiss still cancels/deletes the gate and needs no
  acknowledgement row when the gate disappears.
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
    # Attention-queue dismiss (leaf-28 S5.2): the lifecycle-bound item being cleared.
    # ``kind`` lets the router pair a ``gate-open`` dismissal with the gate cancel it
    # also performs.
    itemId: str | None = None
    kind: str | None = None


# The gate-decision verbs the dashboard can POST (slice 6b); distinct from the
# lifecycle-transition actions (resume / integrate / cleanup) the reducer emits.
GATE_DECISION_ACTIONS = ("approve", "reject", "request-revision", "cancel")

# The attention-queue dismiss verb (leaf-28 S5.2): records a current lifecycle-bound
# acknowledgement, and (for a gate item) cancels the gate too.
DISMISS_ACTION = "dismiss"


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
class DismissalIntent:
    """An attention-item dismissal the router must apply (leaf-28 S5.2).

    Mirrors :class:`GateDecisionIntent`: the pure evaluator returns the intent and
    the router writes a compact lifecycle acknowledgement -- or, for a ``gate-open``
    item, cancels the gate so no acknowledgement row is needed. ``dismissed_at`` is
    the server moment the reducer compares against the item's triggering signal.
    """

    item_id: str
    dismissed_at: str
    kind: str | None = None
    lifecycle_id: str | None = None
    gate_id: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ActionOutcome:
    """The decided HTTP status + JSON body for one action request.

    ``gate_decision`` is set (slice 6b) when the action is a gate-decision verb, and
    the router executes it against the gate store; ``dismissal`` is set (leaf-28
    S5.2) for an attention dismiss, which the router applies. The evaluator itself
    stays pure for both.
    """

    status_code: int
    body: dict[str, Any]
    gate_decision: GateDecisionIntent | None = None
    dismissal: DismissalIntent | None = None


@dataclass(frozen=True)
class ActionEvaluationContext:
    """Shared request context that all pure action evaluators echo into intents."""

    actor: str
    now: str
    gate_id: str | None = None
    note: str | None = None
    item_id: str | None = None
    kind: str | None = None


def _find_actions(projection: WorkspaceProjection, target: str) -> list[ActionAvailability] | None:
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
    item_id: str | None = None,
    kind: str | None = None,
) -> ActionOutcome:
    """Map (action, target) onto a status + body; gate-decision verbs carry an intent (6b).

    The ``dismiss`` verb (leaf-28 S5.2) clears one attention item: it returns a pure
    :class:`DismissalIntent` the router applies. Unlike a lifecycle transition it
    needs no precomputed availability, but non-gate items must carry the lifecycle id
    that scopes their acknowledgement row, except the repo-level ``actionable-drift``
    signal whose snapshot timestamp scopes the occurrence.
    """
    context = ActionEvaluationContext(
        actor=actor,
        now=now,
        gate_id=gate_id,
        note=note,
        item_id=item_id,
        kind=kind,
    )
    if action == DISMISS_ACTION:
        return _dismiss_action_outcome(action, target, context)
    if action in GATE_DECISION_ACTIONS:
        return _gate_decision_outcome(action, target, context)
    return _precomputed_action_outcome(projection, action, target, context)


def _dismiss_action_outcome(
    action: str, target: str | None, context: ActionEvaluationContext
) -> ActionOutcome:
    item = (context.item_id or "").strip()
    if not item:
        return ActionOutcome(
            400,
            {
                "status": "missing-item",
                "detail": "dismiss requires the itemId of the attention item to clear",
                "action": action,
            },
        )
    if target is None and not (
        (context.kind == "gate-open" and context.gate_id) or context.kind == "actionable-drift"
    ):
        return ActionOutcome(
            400,
            {
                "status": "missing-lifecycle",
                "detail": "dismiss requires the lifecycle target for lifecycle-bound attention items",
                "action": action,
                "itemId": item,
            },
        )
    intent_body: dict[str, Any] = {
        "actor": context.actor,
        "source": "dashboard",
        "ts": context.now,
        "action": action,
        "itemId": item,
    }
    if context.kind is not None:
        intent_body["kind"] = context.kind
    if target is not None:
        intent_body["target"] = target
    if context.gate_id is not None:
        intent_body["gateId"] = context.gate_id
    return ActionOutcome(
        202,
        {"status": "received", "detail": "attention item dismissed", "intent": intent_body},
        dismissal=DismissalIntent(
            item_id=item,
            dismissed_at=context.now,
            kind=context.kind,
            lifecycle_id=target,
            gate_id=context.gate_id,
            note=context.note,
        ),
    )


def _gate_decision_outcome(
    action: str, target: str | None, context: ActionEvaluationContext
) -> ActionOutcome:
    if action == "reject" and not (context.note or "").strip():
        return ActionOutcome(
            400,
            {
                "status": "missing-rejection-reason",
                "detail": "reject decisions require a reason",
                "target": target,
            },
        )
    if target is None and not (action == "cancel" and context.gate_id):
        return ActionOutcome(
            400,
            {
                "status": "missing-target",
                "detail": "gate decisions require a lifecycle target unless cancelling a gate id",
                "action": action,
            },
        )
    intent: dict[str, Any] = {
        "actor": context.actor,
        "source": "dashboard",
        "ts": context.now,
        "action": action,
    }
    if target is not None:
        intent["target"] = target
    if context.gate_id is not None:
        intent["gateId"] = context.gate_id
    if context.note is not None:
        intent["note"] = context.note
    return ActionOutcome(
        202,
        {"status": "received", "detail": "gate decision recorded", "intent": intent},
        gate_decision=GateDecisionIntent(
            lifecycle_id=target,
            decision=action,
            gate_id=context.gate_id,
            note=context.note,
        ),
    )


def _precomputed_action_outcome(
    projection: WorkspaceProjection,
    action: str,
    target: str | None,
    context: ActionEvaluationContext,
) -> ActionOutcome:
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
    intent = {
        "actor": context.actor,
        "source": "dashboard",
        "ts": context.now,
        "action": action,
        "target": target,
    }
    return ActionOutcome(
        202,
        {
            "status": "received",
            "detail": "attributed intent recorded; enforced in slice 06",
            "intent": intent,
        },
    )
