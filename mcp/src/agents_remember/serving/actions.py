"""The POST action skeleton: shape + attribution only, never enforcement (slice 4b).

This is the upstream return-channel slice 06 will enforce. Here it does exactly three
things and no more: resolve the target in the current projection, check the requested
action against the reducer's **precomputed** ``ActionAvailability`` (the UI never decides
safety -- North-Star), and capture attribution (``actor`` + ``source:"dashboard"`` + ts).
It performs **no durable mutation**: a permitted request returns ``202`` "recorded;
enforced in slice 06", because gate state is mutated server-side by the MCP tools, not by
the dashboard. Disabled actions return ``409`` with the reducer's ``disabledReason``;
unknown targets return ``404``.

``evaluate_action`` is a pure function over a projection so the availability rules and the
HTTP status mapping are unit-testable without a client; ``app.py`` owns only the routing.
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

    target: str
    actor: Actor = "developer"


@dataclass(frozen=True)
class ActionOutcome:
    """The decided HTTP status + JSON body for one action request (no side effects)."""

    status_code: int
    body: dict[str, Any]


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
    target: str,
    *,
    actor: str,
    now: str,
) -> ActionOutcome:
    """Map (action, target) onto a status + body using the reducer's availability; no mutation."""
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
