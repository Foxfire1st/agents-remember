"""Pure action availability and current attention-item dismissal intents."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
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
    # The stable item and kind published in the attention queue.
    itemId: str | None = None
    kind: str | None = None


# The current attention acknowledgement, independent of task activity.
DISMISS_ACTION = "dismiss"


@dataclass(frozen=True)
class DismissalIntent:
    """One current acknowledgement for the item the projection actually published."""

    item_id: str
    dismissed_at: str
    kind: str
    actionable_fingerprint: str | None = None


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
    for enclosure in chain(
        projection.enclosures, chain.from_iterable(task.worktrees for task in projection.tasks)
    ):
        if enclosure.enclosure == target:
            return enclosure.actions
    return None


def evaluate_action(
    projection: WorkspaceProjection,
    action: str,
    target: str | None,
    context: ActionEvaluationContext,
) -> ActionOutcome:
    """Map one requested action to its published availability or current item."""
    if action == DISMISS_ACTION:
        return _dismiss_action_outcome(projection, action, context)
    return _precomputed_action_outcome(projection, action, target, context)


def _dismiss_action_outcome(
    projection: WorkspaceProjection, action: str, context: ActionEvaluationContext
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
    published = next((row for row in projection.analytics.attentionQueue if row.id == item), None)
    if published is None:
        return ActionOutcome(404, {"status": "unknown-item", "itemId": item})
    # MIK-R200@v2 rule 7: every published entry is acknowledged once per occurrence, so the
    # backend accepts the dismissal of every published kind and refuses only an item this
    # projection does not publish. The acknowledgement hides the occurrence; it changes nothing
    # at its cause, which ends on its own native fact.
    if context.kind != published.kind:
        return ActionOutcome(400, {"status": "item-kind-mismatch", "itemId": item})
    intent_body: dict[str, Any] = {
        "actor": context.actor,
        "source": "dashboard",
        "ts": context.now,
        "action": action,
        "itemId": item,
    }
    if context.kind is not None:
        intent_body["kind"] = context.kind
    return ActionOutcome(
        202,
        {"status": "received", "detail": "attention item dismissed", "intent": intent_body},
        dismissal=DismissalIntent(
            item_id=item,
            dismissed_at=context.now,
            kind=published.kind,
            actionable_fingerprint=published.actionableFingerprint,
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
