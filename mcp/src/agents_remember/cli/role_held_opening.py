"""Open a held seat with its stored handover and waiting messages in one turn (MIK-R100 rule 4).

Extracted from ``role_held_messages`` so the held-seat creation/opening path and its
concurrent-close fence live in one focused owner while keeping ``role_held_messages``
under the file-size budget.
"""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_launch import LaunchExecutionPolicy, run_launch_call
from agents_remember.cli.role_activity import publish_receipt_role_agent
from agents_remember.cli.role_deferred_held import (
    is_deferred_held_receipt,
    retried_outcome_agent_id,
)
from agents_remember.cli.role_held_messages import (
    _END_DELIVERED,
    _END_NOT_DELIVERED,
    _confirmed,
    _DrainBatch,
    _end_batch,
    _stage_batch_ends,
)
from agents_remember.cli.role_held_store import (
    HeldSeatClosed,
    _release_claim,
    _remove_ids,
    open_held_store,
)
from agents_remember.cli.role_launch_confirmation import (
    held_open_outcome_extras,
    held_seat_lost_settlement,
    is_held_seat_lost,
)
from agents_remember.cli.role_launch_receipts import (
    _leaf_before_create,
    _leaf_is_closing,
    _read_receipt,
    _refuse_closing_launch,
    _restore_launch_handover,
    _write_receipt,
)
from agents_remember.cli.role_opening_offload import prepare_held_opening_batch
from agents_remember.controlplane.held_store_layout import combined_text
from agents_remember.kernel.agentic_settings import AgenticSettingsError
from agents_remember.models.role_identity import LEAF_ROLES


def _run_held_open_call(
    batch: _DrainBatch, call: dict[str, Any], policy: LaunchExecutionPolicy
) -> Any:
    """Run the held opening call, fencing leaf seats against a concurrent close."""

    if batch.receipt.get("role") not in LEAF_ROLES:
        return run_launch_call(batch.config, call, execution=policy)
    outcome = run_launch_call(
        batch.config,
        call,
        before_create=lambda: _leaf_before_create(batch.path, batch.receipt, opening_held=True),
        execution=policy,
    )
    current = _read_receipt(batch.path)
    if current is not None and _leaf_is_closing(current):
        _refuse_closing_launch(
            batch.config,
            batch.path,
            batch.receipt,
            creation_returned=outcome.kind == "created" or outcome.lost_agent_id is not None,
            retried_agent_id=retried_outcome_agent_id(outcome, current),
        )
        raise HeldSeatClosed(batch.agent_id, "closed")
    if outcome.kind != "no-answer":
        with open_held_store(batch.ref) as transaction:
            transaction.receipt.pop("heldOpenPending", None)
            _write_receipt(batch.path, transaction.receipt)
    return outcome


def open_held_seat(batch: _DrainBatch) -> dict[str, Any]:
    """Give a held seat its handover and its waiting messages in one turn (rule 4)."""

    messages = _confirmed(batch)

    launch_call = batch.receipt.get("replayRequest")
    opening = batch.receipt.get("heldOpeningPrompt")
    if not isinstance(launch_call, dict) or not isinstance(opening, str) or not opening:
        _release_claim(batch.ref, batch.claim)
        return {
            "outcome": "cannot-open",
            "detail": "The held seat kept no launch call or opening handover.",
        }
    try:
        with open_held_store(batch.ref):
            _restore_launch_handover(batch.path, batch.receipt, launch_call)
    except HeldSeatClosed:
        raise
    except ValueError as error:
        _release_claim(batch.ref, batch.claim)
        return {"outcome": "cannot-open", "detail": str(error)}
    try:
        bounded_batch = prepare_held_opening_batch(
            batch.config, batch.receipt, combined_text(messages)
        )
    except (AgenticSettingsError, OSError, ValueError) as error:
        _release_claim(batch.ref, batch.claim)
        return {"outcome": "open-failed", "detail": f"The held seat stays held: {error}"}
    from agents_remember.cli.product_start_message import (  # noqa: PLC0415 - sends through the role tools, which import role_held_messages
        held_seat_opening,
    )

    # A held Reviewer or Curator gets the product's start message as it opens.
    product = held_seat_opening(batch.config, batch.path, batch.receipt)
    opening_text = "\n\n".join(filter(None, (opening, product, bounded_batch)))
    agent_call = {**(launch_call.get("agent") or {}), "prompt": opening_text}
    if "parentAgentId" in agent_call:
        agent_call["allowMissingParent"] = True
    call = {**launch_call, "agent": agent_call}
    publish_receipt_role_agent(batch.config, batch.receipt)
    policy = LaunchExecutionPolicy(
        mode="create" if is_deferred_held_receipt(batch.receipt) else "open-held",
        on_retry_agent=lambda agent_id: publish_receipt_role_agent(
            batch.config, {**batch.receipt, "agentId": agent_id}
        ),
    )
    outcome = _run_held_open_call(batch, call, policy)
    if outcome.kind == "created":
        _stage_batch_ends(batch, messages, _END_DELIVERED)
        removed = _remove_ids(batch.ref, {row["id"] for row in messages}, batch.claim)
        return {
            "outcome": "opened",
            "detail": "The held seat opened with its handover and its waiting messages in one turn.",
            "ended": removed,
            "how": _END_DELIVERED,
            "execution": outcome.execution,
            "applied": outcome.applied,
            **held_open_outcome_extras(outcome),
        }
    if is_held_seat_lost(outcome):
        detail, updates, remove_fields, extras = held_seat_lost_settlement(outcome)
        ended = _end_batch(
            batch, "rejected", receipt_updates=updates, remove_receipt_fields=remove_fields
        )
        return {
            "outcome": "seat-lost",
            "detail": detail,
            "ended": ended,
            "how": _END_NOT_DELIVERED,
            **extras,
        }
    _release_claim(batch.ref, batch.claim)
    return {
        "outcome": "open-failed",
        "detail": f"The held seat stays held: {outcome.message}",
    }
