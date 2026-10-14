"""The POST action layer: pure availability mapping and attention dismissals.

``evaluate_action`` stays a **pure** function over a projection -- it resolves the target and
maps the requested action onto an HTTP status -- so the rules stay unit-testable without a
client; the router owns the routing *and* the one durable side effect (the dismissal row).

Two action families:

* **Enclosure transitions** (``integrate`` / ``cleanup``): checked against the
  reducer's **precomputed** ``ActionAvailability`` (the UI never decides safety -- North-Star),
  attributed, and acknowledged ``202`` without mutation. Disabled actions return ``409`` with
  the reducer's ``disabledReason``; unknown targets ``404``.
* **Attention dismissals** (``dismiss``, leaf-28 S5.2): lifecycle-bound acknowledgements
  for one queue occurrence, plus the repo-level ``actionable-drift`` one-shot signal.
  The router keeps only current acknowledgement state and prunes lifecycle rows with
  the lifecycle.
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
    # Attention-queue dismiss (leaf-28 S5.2): the lifecycle-bound item being cleared.
    itemId: str | None = None
    kind: str | None = None


# The attention-queue dismiss verb (leaf-28 S5.2): records a current lifecycle-bound
# acknowledgement.
DISMISS_ACTION = "dismiss"


@dataclass(frozen=True)
class DismissalIntent:
    """An attention-item dismissal the router must apply (leaf-28 S5.2).

    The pure evaluator returns the intent and the router writes a compact lifecycle
    acknowledgement. ``dismissed_at`` is the server moment the reducer compares against
    the item's triggering signal.
    """

    item_id: str
    dismissed_at: str
    kind: str | None = None
    lifecycle_id: str | None = None


@dataclass(frozen=True)
class ActionOutcome:
    """The decided HTTP status + JSON body for one action request.

    ``dismissal`` is set (leaf-28 S5.2) for an attention dismiss, which the router
    applies. The evaluator itself stays pure.
    """

    status_code: int
    body: dict[str, Any]
    dismissal: DismissalIntent | None = None


@dataclass(frozen=True)
class ActionEvaluationContext:
    """Shared request context that all pure action evaluators echo into intents.

    Who asked, when, and every identifier the specific verb needs to name its object -- the
    attention item and its kind.
    """

    actor: str
    now: str
    item_id: str | None = None
    kind: str | None = None


def _find_actions(projection: WorkspaceProjection, target: str) -> list[ActionAvailability] | None:
    """The target enclosure's precomputed actions (by name), or None."""
    for enclosure in projection.enclosures:
        if enclosure.enclosure == target:
            return enclosure.actions
    return None


def evaluate_action(
    projection: WorkspaceProjection,
    action: str,
    target: str | None,
    context: ActionEvaluationContext,
) -> ActionOutcome:
    """Map (action, target) onto a status + body.

    The ``dismiss`` verb (leaf-28 S5.2) clears one attention item: it returns a pure
    :class:`DismissalIntent` the router applies. Unlike an enclosure transition it
    needs no precomputed availability, but an item must carry the lifecycle id
    that scopes its acknowledgement row, except the repo-level ``actionable-drift``
    signal whose snapshot timestamp scopes the occurrence.
    """
    if action == DISMISS_ACTION:
        return _dismiss_action_outcome(action, target, context)
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
    if target is None and context.kind != "actionable-drift":
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
    return ActionOutcome(
        202,
        {"status": "received", "detail": "attention item dismissed", "intent": intent_body},
        dismissal=DismissalIntent(
            item_id=item,
            dismissed_at=context.now,
            kind=context.kind,
            lifecycle_id=target,
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
