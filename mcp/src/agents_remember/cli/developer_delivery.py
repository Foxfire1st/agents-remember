"""Deliver one filed developer message, or hold it, without an agent binding (MIK-R188 rule 9).

This is role_message's deliver-or-hold (``paseo_role_tools._deliver_or_hold`` and ``_deliver``)
for the developer's own channel, which has no sender agent, no binding and no wait: the same
order of decisions, through the same bridge call, store and drain. role_message itself is not
changed (the packet's preservation boundary).

- A held seat, or a recipient for which anything waits, gets the message held behind it:
  nothing overtakes (MIK-R100 rule 4b).
- A start that has not answered, a turn the host's activity record reports, a busy or permission
  refusal: held with that reason (rules 4a/4b; MIK-R171 rule 2).
- A host that did not answer: held with the id the send went out under, so a later delivery
  goes out under that id (MIK-R171 rule 3, INV-AMJAZT).
- A closed session is resumed as role_message resumes one (the Revive scope check for a leaf
  role), and then sent; nothing is started, retired or bound.
- An archived or unknown recipient is refused by name.
- A held message that the drain right after it ends as not delivered -- the recipient archived or
  gone, a held seat whose agent can never take its first message -- is refused by name
  (``recipient-archived``, ``seat-lost``): its file is marked undelivered and the tray keeps its
  picks (rules 9 and 10, Failure and Recovery). A drain that delivered part of what waited
  answers by this message's own part: delivered when its part went out, else held. A held message
  that ends later is told by the recipient's launch record, which the sent-picks log reads
  (``developer_message_files.settle_held``); nothing is sent again.

The held entry is text only, under the sender key ``developer`` and the message's first line;
its text ends with the line naming the message file, so no image or text pick is lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.cli.developer_explainer_seats import EXPLAINER_RECIPIENT
from agents_remember.cli.investigator_receipts import investigator_message_capacity
from agents_remember.cli.paseo_role_tools import (
    _archived,
    _note_waiting,
    _resume,
    _send,
    _undelivered,
)
from agents_remember.cli.provider_limit_hold import PROVIDER_LIMIT_REASON, provider_limit_hold
from agents_remember.cli.role_held_messages import (
    MAX_HELD_PER_RECIPIENT,
    HeldMessage,
    HeldStoreFull,
    apply_drain_to_receipt,
    drain_held,
    hold_message,
    note_held_receipt,
    waiting_total,
)
from agents_remember.cli.role_message_delivery import _Recipient, _Refused
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.projections.host_activity import agent_in_turn

# The sender key of every held developer message; the message file's head names the same.
DEVELOPER_SENDER = "developer"
_UNFINISHED_START_STATUSES = frozenset({"starting", "unknown"})
_WHY_HELD = {
    "held-seat-unopened": "the seat is held and takes its first turn with its first message",
    "held-behind-waiting": "earlier messages still wait for the recipient",
    "start-unfinished": "the recipient's start has not finished",
    "permission-pending": "the recipient waits for a permission decision",
    "recipient-in-turn": "the recipient is in a turn the host cannot write into",
    "host-unreachable": "the Paseo runtime did not answer; the message is held, not lost",
    "provider-limit": "the recipient's provider limit has not reset; the message is held",
}


@dataclass(frozen=True, slots=True)
class DeveloperDelivery:
    """One filed message for one recipient: its record, its first line and its texts.

    ``held_text`` is what a held entry keeps under the first line; it ends with the line naming
    the message file. ``payload`` is the bridge's ``agent-send`` payload, with the images and
    text attachments the host renders natively.
    """

    config: McpRuntimeConfig
    agent_id: str
    receipt_path: Path
    receipt: dict[str, Any]
    first_line: str
    held_text: str
    payload: dict[str, Any]

    @property
    def store_folder(self) -> str | None:
        workspace = self.receipt.get("workspace")
        folder = workspace.get("path") if isinstance(workspace, dict) else None
        return folder if isinstance(folder, str) and folder else None


def deliver_or_hold(delivery: DeveloperDelivery) -> dict[str, Any]:
    """``{state: delivered|held, ...}``; a refusal raises ``_Refused`` naming its reason."""

    folder = delivery.store_folder
    status = delivery.receipt.get("status")
    if folder is not None and status == "held":
        # F20: a message queued behind a waiting one is still held by the limit, so its answer
        # names the reset too; otherwise only the first hold would say when work resumes.
        return _hold(delivery, reason="held-seat-unopened", limited_until=_limited_until(delivery))
    if folder is not None and waiting_total(folder, delivery.agent_id) > 0:
        return _hold(delivery, reason="held-behind-waiting", limited_until=_limited_until(delivery))
    limited = provider_limit_hold(delivery.config, delivery.agent_id)
    if limited is not None:
        return _hold(
            delivery,
            reason=PROVIDER_LIMIT_REASON,
            limited_until=str(limited["limitedUntil"]) if limited.get("limitedUntil") else None,
        )
    try:
        if status in _UNFINISHED_START_STATUSES:
            raise _busy(delivery, "its start has not finished", "start-unfinished")
        return _deliver(delivery)
    except _Refused as undelivered:
        if folder is None:
            raise
        if undelivered.refusal == "recipient-busy" and undelivered.fields.get("holdReason"):
            permission = undelivered.fields.get("permission")
            return _hold(
                delivery,
                reason=str(undelivered.fields["holdReason"]),
                permission=permission if isinstance(permission, str) else None,
            )
        if undelivered.refusal == "host-unreachable":
            return _hold(
                delivery,
                reason="host-unreachable",
                detail=undelivered.detail,
                attempted_message_id=str(delivery.payload["messageId"]),
            )
        raise


def _busy(delivery: DeveloperDelivery, why: str, reason: str) -> _Refused:
    return _Refused(
        "recipient-busy",
        f"Agent {delivery.agent_id} cannot take the message now: {why}.",
        "The message is held and delivered when the recipient can be written to.",
        recipientAgentId=delivery.agent_id,
        holdReason=reason,
    )


def _deliver(delivery: DeveloperDelivery) -> dict[str, Any]:
    config = delivery.config
    if agent_in_turn(config, delivery.agent_id):
        raise _busy(delivery, "the host's own activity record reports a turn", "recipient-in-turn")
    resumed = False
    try:
        with investigator_message_capacity(config, delivery.receipt):
            sent = _send(config, delivery.payload)
            if sent.get("refused") == "closed":
                if delivery.receipt.get("role") == EXPLAINER_RECIPIENT:
                    # An explainer seat is resumed by its leaf's own service, never as a role.
                    raise _Refused(
                        "recipient-closed",
                        f"The explainer seat {delivery.agent_id} is closed.",
                        "Open the leaf's review page; its explainer control resumes the seat.",
                        recipientAgentId=delivery.agent_id,
                    )
                _resume(config, _Recipient(delivery.agent_id, delivery.receipt))
                resumed = True
                tool_server = delivery.receipt.get("toolServer")
                waits = isinstance(tool_server, dict) and tool_server.get("applied") is True
                sent = _send(
                    config, {**delivery.payload, "afterResume": True} if waits else delivery.payload
                )
    except HTTPException as error:
        raise _Refused(
            "investigator-capacity",
            str(error.detail),
            "Close or archive an Investigator execution of that selection first.",
            recipientAgentId=delivery.agent_id,
        ) from error
    if sent.get("delivered") is not True:
        raise _undelivered(delivery.agent_id, sent)
    taken = sent.get("taken")
    answer: dict[str, Any] = {
        "state": "delivered",
        "taken": taken if taken in {"steered", "replaced"} else "started",
        "detail": (
            "The recipient's running turn took the message up."
            if taken == "steered"
            else "The host replaced the recipient's running turn to take the message."
            if taken == "replaced"
            else "The recipient started a turn with the message."
        ),
    }
    if resumed:
        answer["resumed"] = True
    return answer


def _hold(  # noqa: PLR0913 - one hold states the message's own reasons
    delivery: DeveloperDelivery,
    *,
    reason: str,
    permission: str | None = None,
    detail: str = "",
    attempted_message_id: str | None = None,
    limited_until: str | None = None,
) -> dict[str, Any]:
    """Take the text into the recipient's held store and drain what waits, as role_message does."""

    folder = delivery.store_folder
    assert folder is not None, "held for a recipient without a store"
    path, receipt = delivery.receipt_path, delivery.receipt
    try:
        taken = hold_message(
            folder,
            delivery.agent_id,
            HeldMessage(
                sender_line=delivery.first_line,
                sender_agent_id=DEVELOPER_SENDER,
                text=delivery.held_text,
                reason=reason,
                permission=permission,
                attempted_message_id=attempted_message_id,
                limited_until=limited_until,
            ),
        )
    except HeldStoreFull as full:
        raise _Refused(
            "recipient-busy",
            f"Agent {delivery.agent_id} already holds {MAX_HELD_PER_RECIPIENT} messages: {full}",
            "Wait for a delivery; none was dropped.",
            recipientAgentId=delivery.agent_id,
        ) from full
    except ValueError as error:
        raise _Refused(
            "recipient-not-found", str(error), "", recipientAgentId=delivery.agent_id
        ) from error
    note_held_receipt(delivery.config, path)
    _note_waiting(path, taken["waitingTotal"])
    try:
        drain = drain_held(delivery.config, path, receipt)
    except Exception as error:  # the drain is retried by the sweep; the message stays held
        drain = {"outcome": "drain-failed", "detail": str(error)}
    apply_drain_to_receipt(path, drain)
    outcome = str(drain.get("outcome") or "")
    # Both end every waiting message as not delivered, this one with them: a loss, never a wait.
    if outcome == "recipient-gone":
        raise _archived(delivery.agent_id)
    if outcome == "seat-lost":
        raise _Refused(
            "seat-lost",
            f"Agent {delivery.agent_id} can never take its first message, so the message was not "
            f"delivered: {drain.get('detail') or 'the held seat was lost'}",
            "Start the role again if the work continues; the message stays in its file, marked "
            "undelivered.",
            recipientAgentId=delivery.agent_id,
        )
    mine = taken.get("heldMessageId")
    # A drain that delivered part of what waited answers by this message's own part.
    if outcome in {"delivered", "opened"} or (
        outcome == "partially-delivered" and mine in _ended_ids(drain)
    ):
        return {
            "state": "delivered",
            "detail": "The message was held and then delivered with what waited, in order.",
            "heldMessageId": mine,
        }
    waits = {"still-held", "drain-failed", "nothing-waiting", "partially-delivered", ""}
    final = reason if outcome in waits else outcome
    last = str(drain.get("detail") or "") or detail
    held: dict[str, Any] = {
        "state": "held",
        "reason": final,
        "detail": (
            f"The message is held for agent {delivery.agent_id}: {_WHY_HELD.get(final, final)}. "
            "It is delivered when the recipient can be written to, in the order it was taken; "
            "nothing has to be sent again." + (f" The last attempt said: {last}" if last else "")
        ),
        "heldMessageId": mine,
    }
    # MIK-R190 rule 9: the reset time the waiting row carries reaches the caller too, as it does
    # in role_message's answer -- the tray's word names the moment work resumes (rule 13).
    until = taken.get("limitedUntil") or limited_until
    if until:
        held["limitedUntil"] = until
    return held


def _limited_until(delivery: DeveloperDelivery) -> str | None:
    """The reset a hold answers with, or ``None`` when the recipient is not limited (F20).

    A message queued behind a waiting one is still held by the provider limit, so its answer carries
    the reset the same way the first hold's does; the read is the same one the limit branch takes.
    """

    limited = provider_limit_hold(delivery.config, delivery.agent_id)
    until = limited.get("limitedUntil") if limited else None
    return str(until) if until else None


def _ended_ids(drain: dict[str, Any]) -> set[str]:
    """The held ids one drain ended."""

    ended = drain.get("ended")
    rows = ended if isinstance(ended, list) else []
    return {str(row.get("id")) for row in rows if isinstance(row, dict)}
