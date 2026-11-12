"""The process that delivers durably held role messages (MIK-R100 rules 4a/4b).

A message the host cannot take now -- the recipient's start has not finished, it waits
at a permission prompt, or it is in a turn the host cannot write into -- is held by the
product instead of refused, and delivered when the recipient can be written to. A held
seat (rule 2) is the same store with its handover still unopened: its first drain
delivers the handover and everything held for it in one turn (rule 4).

What a waiting message *is* on disk -- the store file's name, schema and bounds, the one
small queue of holders, the terminal lines a message leaves in its recipient's launch
record, and the bounded discovery that finds the stores that exist -- belongs to
:mod:`agents_remember.controlplane.held_store_layout`: this module writes through it, and the
native attention queue reads it (MIK-R200 rule 3). What stays here is delivery: the claim
that makes one batch, the drain that sends it, the record of how each message ended, and
the sweep that drives the drains.

Delivery: one process delivers (``drain_held`` under the store file's lock, so no two
deliveries to one recipient run at once). Everything held for a recipient goes in one
delivery, in the order taken, each under the line naming its sender; a further message
is held behind what waits even when the host could take it at that moment. The store
shares only the generic file lock with the rest of the product. How the product learns
that a prompt was answered, a turn ended, or a start finished is one read of the agent
before each drain; the dashboard sweep runs that read every
``HELD_DELIVERY_SWEEP_SECONDS``.

Two things make a waiting message reachable without another message being sent to the
recipient (MIK-R171 rule 1). Every holder names its receipt in the one small bounded queue
(``note_held_receipt``), and the sweep drains that queue before it walks the receipts,
so a receipt that holds messages is visited ahead of the others. And the dashboard reads
the host's own activity record: the agents whose turn it reports ended are drained
directly (``deliver_ended_turns``) within ``HELD_TURN_END_TICK_SECONDS``, while the
sweep stays the fallback for everything the record does not show.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_bridge import (
    BRIDGE_REFUSED,
    BRIDGE_TIMEOUT,
    SEND_OUTCOME_UNKNOWN,
    PaseoBridgeFailure,
    bridge_call,
)
from agents_remember.cli.paseo_launch import run_launch_call
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_activity import publish_receipt_role_agent, resume_role_agent
from agents_remember.cli.role_launch_receipts import (
    _read_receipt,
    _restore_launch_handover,
    _write_receipt,
)
from agents_remember.controlplane.held_store_discovery import (
    EXECUTIONS_DIRECTORY,
    receipt_roots,
)
from agents_remember.controlplane.held_store_discovery import (
    ReceiptWalk as _CandidateWalk,
)
from agents_remember.controlplane.held_store_layout import (
    MAX_HELD_PER_RECIPIENT,
    STALE_CLAIM_SECONDS,
    HeldMessage,
    HeldMessageCommitted,
    HeldStoreFull,
    _clear_pending_lines,
    _now_iso,
    _read_store_counts,
    _read_store_file,
    _stage_pending_lines,
    _store_endpoints,
    _write_store_file,
    combined_message_id,
    combined_text,
    end_waiting_as,
    forget_held_receipt,
    held_queue_receipts,
    hold_message,
    live_claim,
    note_held_receipt,
    record_lines,
    recount_waiting_for,
    sender_terminal_lines,
    store_path_for,
    waiting_counts,
    waiting_total,
    withdraw_message,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.projections.host_activity import agent_in_turn

# The store's own names are re-exported here on purpose: this module was their home, and its
# callers and tests import them from it. They are listed so the surface stays one thing a reader
# can see, and so a linter does not read a kept name as a leftover import.
__all__ = [
    "EXECUTIONS_DIRECTORY",
    "MAX_HELD_PER_RECIPIENT",
    "STALE_CLAIM_SECONDS",
    "HeldMessage",
    "HeldMessageCommitted",
    "HeldStoreFull",
    "_read_store_counts",
    "_read_store_file",
    "_write_store_file",
    "combined_text",
    "end_waiting_as",
    "held_queue_receipts",
    "hold_message",
    "note_held_receipt",
    "record_lines",
    "recount_waiting_for",
    "sender_terminal_lines",
    "waiting_counts",
    "waiting_total",
    "withdraw_message",
]

# The store's own names -- schema, bounds, queue, terminal lines -- live in one owner
# (``controlplane/held_store_layout.py``) so this delivery line and the attention queue read one
# definition of the product's layout (MIK-R200 rule 3; rule 12). What stays beside the
# sweep here is only how often it looks.
# How often the dashboard sweep reads every recipient with waiting messages: the stated
# delay with which an answered prompt, an ended turn, or a finished start is observed.
HELD_DELIVERY_SWEEP_SECONDS = 15.0
# How often the dashboard reads the host's activity record for a turn end (MIK-R171
# rule 1): one stat, and one small file read when it changed.
HELD_TURN_END_TICK_SECONDS = 3.0
# At most this many recipients are drained for a turn end per tick.
HELD_TURN_END_MAX_DRAINS = 32

logger = logging.getLogger(__name__)

_END_DELIVERED = "delivered"
_END_NOT_DELIVERED = "not-delivered-archived-or-gone"
_END_WITHDRAWN = "withdrawn"


def _live_claim(store: dict[str, Any]) -> str | None:
    """The store's delivery claim when one is live; ``None`` when absent or stale."""

    return live_claim(store, STALE_CLAIM_SECONDS)


def _remove_ids(
    workspace_path: str | Path,
    agent_id: str,
    message_ids: set[str],
    claim: str | None,
    *,
    clear_claim: bool = True,
) -> list[dict[str, Any]]:
    """Delete exactly the delivered texts; the receipt lines are the caller's to write.

    A multi-part drain keeps its claim across its parts and releases it at the end;
    only the final removal clears it.
    """

    path = store_path_for(workspace_path, agent_id)
    removed: list[dict[str, Any]] = []
    with exclusive_file_lock(path, "held-message store"):
        store = _read_store_file(path, agent_id)
        if claim is not None and store.get("deliveryClaim") != claim:
            return []
        kept = []
        for row in store["messages"]:
            if row.get("id") in message_ids:
                removed.append(row)
            else:
                kept.append(row)
        store["messages"] = kept
        if clear_claim:
            store.pop("deliveryClaim", None)
            store.pop("deliveryClaimAt", None)
            store.pop("deliveryClaimIds", None)
        _write_store_file(path, store)
    return removed


def _flush_pending_lines(
    workspace_path: str | Path,
    agent_id: str,
    receipt_path: Path,
    receipt: dict[str, Any],
) -> bool:
    """Move staged ends into the receipt lines; best-effort, never raises.

    Returns ``True`` when nothing is staged or the staged ends reached the
    receipt file. Grouped by outcome so one shared writer keeps its rule.
    """

    try:
        path = store_path_for(workspace_path, agent_id)
        with exclusive_file_lock(path, "held-message store"):
            store = _read_store_file(path, agent_id)
            staged = [e for e in store.get("pendingLines", []) if isinstance(e, dict)]
            if not staged:
                return True
        by_how: dict[str, list[dict[str, Any]]] = {}
        for entry in staged:
            rows = by_how.setdefault(str(entry.get("how") or _END_DELIVERED), [])
            rows.append(
                {
                    "id": entry.get("messageId"),
                    "senderLine": entry.get("sender"),
                    "senderAgentId": entry.get("senderAgentId"),
                }
            )
        for how, rows in by_how.items():
            record_lines(receipt, rows, how)
        try:
            _write_receipt(receipt_path, receipt)
        except OSError:
            return False
        with exclusive_file_lock(path, "held-message store"):
            store = _read_store_file(path, agent_id)
            staged_now = store.get("pendingLines")
            if isinstance(staged_now, list):
                store["pendingLines"] = [e for e in staged_now if not isinstance(e, dict)]
            _write_store_file(path, store)
    except (ValueError, OSError):
        return False
    return True


def _claim_batch(
    workspace_path: str | Path, agent_id: str
) -> tuple[list[dict[str, Any]], str] | None:
    """Claim everything waiting for one delivery; a live claim is never taken over.

    A stale claim resumes exactly its batch, with its stable id: the earlier drain
    may have sent it before the crash, so the host's message-id deduplication
    settles the resend. Later arrivals stay ordered behind it for the next batch.
    The stored claim is reused unchanged: recomputing it from the survivors
    would hand the resend a new id and defeat that same deduplication.
    """

    path = store_path_for(workspace_path, agent_id)
    with exclusive_file_lock(path, "held-message store"):
        store = _read_store_file(path, agent_id)
        messages = [row for row in store["messages"] if isinstance(row, dict)]
        if not messages:
            return None
        if _live_claim(store) is not None:
            return None
        if isinstance(store.get("deliveryClaim"), str):
            member_ids = [
                entry for entry in store.get("deliveryClaimIds", []) if isinstance(entry, str)
            ]
            if member_ids:
                by_id = {row.get("id"): row for row in messages if isinstance(row.get("id"), str)}
                resumed = [by_id[entry] for entry in member_ids if entry in by_id]
                if resumed:
                    messages = resumed
                    claim = str(store["deliveryClaim"])
                    store["deliveryClaimAt"] = _now_iso()
                    _write_store_file(path, store)
                    return messages, claim
        claim = combined_message_id(messages)
        store["deliveryClaim"] = claim
        store["deliveryClaimAt"] = _now_iso()
        store["deliveryClaimIds"] = [row.get("id") for row in messages]
        _write_store_file(path, store)
    return messages, claim


def _release_claim(workspace_path: str | Path, agent_id: str, claim: str) -> None:
    path = store_path_for(workspace_path, agent_id)
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, agent_id)
        except ValueError:
            return
        if store.get("deliveryClaim") == claim:
            store.pop("deliveryClaim", None)
            store.pop("deliveryClaimAt", None)
            store.pop("deliveryClaimIds", None)
            with suppress(OSError):
                _write_store_file(path, store)


def drain_held(
    config: McpRuntimeConfig, receipt_path: Path, receipt: dict[str, Any]
) -> dict[str, Any]:
    """Deliver everything held for one recipient in one delivery, or say why it stays held.

    A held seat opens with its handover and the waiting messages in one turn (rule 4);
    an open seat takes the waiting messages in as few deliveries as the host admits
    (rules 4a/4b, D114). The texts leave the store only after the host took them;
    every end is recorded for the receipt lines by the caller through apply, and
    staged ends left by an interrupted record are flushed first.
    """

    endpoints = _store_endpoints(receipt)
    if endpoints is not None:
        _flush_pending_lines(endpoints[0], endpoints[1], receipt_path, receipt)
    try:
        batch = _claimed_batch(config, receipt_path, receipt)
        _check_drain_readiness(batch)
        if str(batch.receipt.get("status")) == "held":
            return _open_held_seat(batch)
        return _deliver_waiting(batch)
    except _DrainPause as paused:
        return paused.outcome


@dataclass(frozen=True, slots=True)
class _DrainBatch:
    """Everything claimed for one delivery: the waiter, its messages, and its claim."""

    config: McpRuntimeConfig
    path: Path
    receipt: dict[str, Any]
    folder: str
    agent_id: str
    messages: list[dict[str, Any]]
    claim: str


class _DrainPause(Exception):
    """Why a claimed batch stays held: the outcome its caller answers."""

    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__(str(outcome.get("detail") or outcome.get("outcome")))
        self.outcome = outcome


def _claimed_batch(
    config: McpRuntimeConfig, receipt_path: Path, receipt: dict[str, Any]
) -> _DrainBatch:
    """The claimed batch of one recipient; pauses when there is nothing to deliver."""

    fresh = _read_receipt(receipt_path) or receipt
    execution = fresh.get("execution")
    agent_id = execution.get("agentId") if isinstance(execution, dict) else None
    workspace = fresh.get("workspace")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    if not isinstance(agent_id, str) or not agent_id or not isinstance(folder, str):
        raise _DrainPause(
            {"outcome": "cannot-hold", "detail": "The receipt names no agent or folder."}
        )
    try:
        claimed = _claim_batch(folder, agent_id)
    except (ValueError, OSError) as error:
        # A removed working folder reads as unreadable, not as a crash.
        raise _DrainPause({"outcome": "store-unreadable", "detail": str(error)}) from error
    if claimed is None:
        raise _DrainPause({"outcome": "nothing-waiting"})
    messages, claim = claimed
    return _DrainBatch(
        config=config,
        path=receipt_path,
        receipt=fresh,
        folder=folder,
        agent_id=agent_id,
        messages=messages,
        claim=claim,
    )


def _check_drain_readiness(batch: _DrainBatch) -> None:
    """Pause when the host cannot take the batch now; claim released on the way out.

    MIK-R190 rule 10 comes first and before any host read: a limited recipient
    pauses the drain with ``provider-limit``, so waiting for a limit costs no
    bridge process. Rule 9 is the same fact the send paths ask. That policy is
    owned by :mod:`agents_remember.cli.provider_limit_drain` (rule 19); this
    module keeps only the call site.
    """

    from agents_remember.cli.provider_limit_drain import (  # noqa: PLC0415
        pause_for_provider_limit,
    )

    pause_for_provider_limit(batch)
    reading = read_agent(batch.config, batch.agent_id)
    if not reading.reachable:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {"outcome": "host-unreachable", "detail": str(reading.unreachable_reason)}
        )
    if not reading.found or reading.archived:
        ended = end_waiting_as(batch.folder, batch.agent_id)
        raise _DrainPause(
            {
                "outcome": "recipient-gone",
                "detail": "The recipient is archived or gone; its waiting messages end as not delivered.",
                "ended": ended,
                "how": _END_NOT_DELIVERED,
            }
        )
    if reading.pending_permission is not None:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {
                "outcome": "permission-pending",
                "permission": reading.pending_permission,
                "detail": "The recipient waits for a permission decision; the messages stay held.",
            }
        )
    if str(batch.receipt.get("status")) in {"starting", "unknown"}:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {
                "outcome": "start-unfinished",
                "detail": "The recipient's start has not finished; the messages stay held.",
            }
        )
    if reading.session_closed:
        _resume_for_drain(batch)


def _resume_for_drain(batch: _DrainBatch) -> None:
    """Open the closed session for one batch; pause when it stays closed."""

    resumed = resume_role_agent(batch.config, batch.receipt)
    if not resumed.reading.reachable:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {"outcome": "host-unreachable", "detail": "The resume was not confirmed."}
        )
    if resumed.reading.session_closed or resumed.refusal is not None:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {
                "outcome": "cannot-resume",
                "detail": str(resumed.refusal or "The host left the session closed."),
            }
        )


def _host_reports_a_turn(config: McpRuntimeConfig, agent_id: str) -> bool:
    """Whether the host's activity publication says this agent is in a turn (rule 2)."""

    return agent_in_turn(config, agent_id)


def _confirmed(batch: _DrainBatch) -> list[dict[str, Any]]:
    """The claimed rows still committed to this batch; pauses when the claim moved on.

    A withdrawal or end that landed after the claim no longer belongs to it: the
    batch sends only its survivors, and pauses when none survive.
    """

    try:
        path = store_path_for(batch.folder, batch.agent_id)
    except ValueError as error:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause({"outcome": "store-unreadable", "detail": str(error)}) from error
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, batch.agent_id)
        except ValueError as error:
            _release_claim(batch.folder, batch.agent_id, batch.claim)
            raise _DrainPause({"outcome": "store-unreadable", "detail": str(error)}) from error
        if store.get("deliveryClaim") != batch.claim:
            raise _DrainPause(
                {
                    "outcome": "still-held",
                    "detail": "The delivery claim moved on; the messages stay held.",
                }
            )
        present = {row.get("id") for row in store["messages"] if isinstance(row, dict)}
    survivors = [row for row in batch.messages if row.get("id") in present]
    if not survivors:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {
                "outcome": "still-held",
                "detail": "The claimed messages ended elsewhere; the messages stay held.",
            }
        )
    return survivors


def _open_held_seat(batch: _DrainBatch) -> dict[str, Any]:
    """Give a held seat its handover and its waiting messages in one turn (rule 4)."""

    messages = _confirmed(batch)

    launch_call = batch.receipt.get("replayRequest")
    opening = batch.receipt.get("heldOpeningPrompt")
    if not isinstance(launch_call, dict) or not isinstance(opening, str) or not opening:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        return {
            "outcome": "cannot-open",
            "detail": "The held seat kept no launch call or opening handover.",
        }
    try:
        _restore_launch_handover(batch.path, batch.receipt, launch_call)
    except ValueError as error:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        return {"outcome": "cannot-open", "detail": str(error)}
    opening_text = opening + "\n\n" + combined_text(messages)
    call = {
        **launch_call,
        "agent": {**(launch_call.get("agent") or {}), "prompt": opening_text},
    }
    publish_receipt_role_agent(batch.config, batch.receipt)
    outcome = run_launch_call(batch.config, call)
    if outcome.kind == "created":
        _stage_pending_lines(batch.folder, batch.agent_id, messages, _END_DELIVERED)
        removed = _remove_ids(
            batch.folder, batch.agent_id, {row["id"] for row in messages}, batch.claim
        )
        return {
            "outcome": "opened",
            "detail": "The held seat opened with its handover and its waiting messages in one turn.",
            "ended": removed,
            "how": _END_DELIVERED,
            "execution": outcome.execution,
            "applied": outcome.applied,
        }
    if outcome.kind == "refused" and outcome.lost_agent_id:
        ended = end_waiting_as(batch.folder, batch.agent_id)
        return {
            "outcome": "seat-lost",
            "detail": (
                "The held seat's agent exists but can never take its first message; "
                f"its waiting messages end as not delivered. {outcome.message}"
            ),
            "ended": ended,
            "how": _END_NOT_DELIVERED,
        }
    _release_claim(batch.folder, batch.agent_id, batch.claim)
    return {
        "outcome": "open-failed",
        "detail": f"The held seat stays held: {outcome.message}",
    }


def _part_text_bytes(part: list[dict[str, Any]]) -> int:
    """The bytes one candidate delivery puts on the wire."""

    return len(combined_text(part).encode("utf-8"))


def _attempted_id(row: dict[str, Any]) -> str | None:
    """The host message id an unanswered send used for this waiting message, if any."""

    value = row.get("attemptedMessageId")
    return value if isinstance(value, str) and value else None


def _send_part(batch: _DrainBatch, part: list[dict[str, Any]]) -> dict[str, Any]:
    """Send one part of a batch, reopening a closed session once before giving up.

    The part's send id derives from its member messages, never from a split counter: any
    later drain that resends the same members recomputes the same id, so a host that
    records a message id delivers the resend at most once. A message the host may already
    hold from an unanswered send goes alone, under that send's own id (MIK-R171 rule 3).
    """

    payload: dict[str, Any] = {
        "agentId": batch.agent_id,
        "text": combined_text(part),
        "messageId": (len(part) == 1 and _attempted_id(part[0])) or combined_message_id(part),
    }
    if _host_reports_a_turn(batch.config, batch.agent_id):
        # MIK-R171@v2 rule 2: the host's own activity record is a read of the recipient's state
        # just before the send, and the bridge decides with it - steer for a provider known to
        # take one, hold for every other.
        payload["turnReportedByHost"] = True
    assert len(part) == 1 or not any(_attempted_id(row) for row in part), (
        "a message the host may already hold goes alone"
    )
    attempted = _attempted_id(part[0]) if len(part) == 1 else None
    if attempted is not None and _host_already_holds(batch.config, batch.agent_id, attempted):
        # The recipient's timeline already carries this message: it is recorded as
        # delivered with no send, so the delivery is at most once on any host, not only
        # on one that deduplicates by message id (MIK-R171 rule 3).
        return {"delivered": True, "taken": "already-there", "turnId": None}
    delivery = _send_once(batch.config, payload)
    if delivery.get("refused") == "closed":
        _resume_for_send(batch)
        delivery = _send_once(batch.config, payload)
    return delivery


def _host_already_holds(config: McpRuntimeConfig, agent_id: str, message_id: str) -> bool:
    """Whether the recipient's timeline already carries the message this id was sent under.

    The host's own record decides, not the sender's intention: an unanswered send may have
    been taken, and a copy the recipient already holds is not sent again (MIK-R171 rule 3).
    A timeline this call cannot read answers "not there", so the send still goes out under
    the same id, and a host that records ids keeps it at most once.
    """

    try:
        answer = bridge_call(
            config, "agent-has-message", {"agentId": agent_id, "messageId": message_id}
        )
    except PaseoBridgeFailure:
        return False
    return answer.get("present") is True


def _greedy_prefix(rows: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """The longest leading run of messages that fits the learned byte budget.

    At least the first message, even when it alone exceeds the budget: the
    attempt relearns the budget instead of stalling. Sender lines stay whole;
    feasibility is monotone in the prefix length, so the greedy prefix is the
    longest admittable one.
    """

    piece = [rows[0]]
    for row in rows[1:]:
        if _part_text_bytes([*piece, row]) > budget:
            break
        piece.append(row)
    return piece


def _deliver_waiting(batch: _DrainBatch) -> dict[str, Any]:
    """Hand everything waiting to an open seat in the minimum admitted deliveries.

    The bridge protocol publishes no size cap for agent-send; the host's busy
    refusal is the only admission signal, and a refused send took nothing. Every
    round first attempts the whole remainder: a remainder the host takes commits
    as one delivery, so a learned budget that sits below the true capacity can
    never force an avoidable split. Only a refused whole remainder discovers
    the fit by probing down: each shorter prefix is tried in turn, and the
    first prefix the host takes is the longest admittable one, so it commits
    with no smaller send before it. Its bytes become the learned budget the
    next refused whole remainder chunks greedily against, which keeps discovery
    short when the cap is stable. A learned budget is a lower bound on
    capacity, never an upper bound: the whole remainder is always attempted
    before the budget chunks anything. Sender lines stay whole inside their
    part, parts go in order, and every part the host took is staged and removed
    before the next part goes out, so an interruption resumes with its ends
    recorded and without loss.

    Recovery is at-least-once: a crash after the host took a part but before
    its removal resends the same members under the same id, which only a
    deduplicating host settles. A crash between two different parts never
    resends: only surviving members go out, under their own stable ids.
    """

    messages = _confirmed(batch)
    ended: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    rest = messages
    budget: int | None = None
    probe_max: int | None = None
    whole_refused = False
    while rest:
        piece = _next_piece(rest, budget, probe_max, whole_refused)
        delivery = _send_part(batch, piece)
        if delivery.get("delivered") is True:
            budget = max(_commit_part(batch, piece, delivery, ended, deliveries), budget or 0)
            probe_max = None
            whole_refused = False
            rest = rest[len(piece) :]
            continue
        refused = delivery.get("refused")
        if refused in {"archived", "not-found"}:
            return _recipient_gone(batch, ended)
        if refused == "host-unreachable":
            return _host_did_not_answer(batch, delivery, ended, deliveries)
        if refused == "busy" and len(piece) > 1:
            if probe_max is None and len(piece) == len(rest):
                whole_refused = True
            probe_max = len(piece) - 1
            continue
        return _still_held(batch, delivery, ended, deliveries)
    _release_claim(batch.folder, batch.agent_id, batch.claim)
    return {
        "outcome": "delivered",
        "detail": (
            "The held messages were delivered in one delivery."
            if len(deliveries) == 1
            else (f"The held messages were delivered in {len(deliveries)} deliveries, in order.")
        ),
        "ended": ended,
        "how": _END_DELIVERED,
        "deliveries": deliveries,
        "messageId": batch.claim,
    }


def _next_piece(
    rest: list[dict[str, Any]], budget: int | None, probe_max: int | None, whole_refused: bool
) -> list[dict[str, Any]]:
    """The part to attempt next: one unanswered send, a probe, a chunk, or the whole rest."""

    if _attempted_id(rest[0]) is not None:
        # The host may already hold this one from an unanswered send: it goes alone,
        # under that send's id, and nothing joins it (MIK-R171 rule 3).
        return rest[:1]
    if probe_max is not None:
        piece = rest[: max(probe_max, 1)]
    elif budget is not None and whole_refused:
        piece = _greedy_prefix(rest, budget)
    else:
        piece = rest
    # A row the host may already hold goes alone, so the part ends before it and the
    # rows ahead of it are delivered in the order they were held (MIK-R171 rule 3).
    # Stopping here, rather than asserting later, is what lets a queue that grew a
    # host-unreachable row behind an ordinary one drain at all.
    boundary = next(
        (index for index, row in enumerate(piece) if _attempted_id(row) is not None), len(piece)
    )
    return piece[:boundary] or rest[:1]


def _commit_part(
    batch: _DrainBatch,
    piece: list[dict[str, Any]],
    delivery: dict[str, Any],
    ended: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
) -> int:
    """Record one part the host took: stage its ends, remove its texts, count its bytes."""

    _stage_pending_lines(batch.folder, batch.agent_id, piece, _END_DELIVERED)
    ended.extend(
        _remove_ids(
            batch.folder,
            batch.agent_id,
            {row["id"] for row in piece},
            batch.claim,
            clear_claim=False,
        )
    )
    deliveries.append(
        {
            "messageId": (len(piece) == 1 and _attempted_id(piece[0]))
            or combined_message_id(piece),
            "taken": delivery.get("taken"),
            "turnId": delivery.get("turnId"),
            "ids": [row["id"] for row in piece],
        }
    )
    return _part_text_bytes(piece)


def _recipient_gone(batch: _DrainBatch, ended: list[dict[str, Any]]) -> dict[str, Any]:
    """The outcome when the host says the recipient is archived or gone."""

    if ended:
        apply_drain_to_receipt(
            batch.path,
            {"outcome": "delivered", "ended": ended, "how": _END_DELIVERED},
        )
    missing = end_waiting_as(batch.folder, batch.agent_id)
    return {
        "outcome": "recipient-gone",
        "detail": "The recipient is archived or gone; its waiting messages end as not delivered.",
        "ended": missing,
        "how": _END_NOT_DELIVERED,
    }


def _partially_delivered(
    ended: list[dict[str, Any]], deliveries: list[dict[str, Any]], reason: str
) -> dict[str, Any]:
    """The outcome when some waiting messages went out and the rest stay held."""

    return {
        "outcome": "partially-delivered",
        "detail": (
            f"{len(ended)} of the waiting messages were delivered in "
            f"{len(deliveries)} deliveries; the rest stay held: {reason}"
        ),
        "ended": ended,
        "how": _END_DELIVERED,
        "deliveries": deliveries,
    }


def _host_did_not_answer(
    batch: _DrainBatch,
    delivery: dict[str, Any],
    ended: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
) -> dict[str, Any]:
    """The outcome when the host did not answer: a delay, never a loss (rule 3)."""

    _release_claim(batch.folder, batch.agent_id, batch.claim)
    reason = str(delivery.get("detail") or "The Paseo runtime did not answer.")
    logger.warning(
        "Held messages for agent %s wait: the host did not answer (%s).",
        batch.agent_id,
        reason,
    )
    if ended:
        return _partially_delivered(ended, deliveries, reason)
    return {"outcome": "host-unreachable", "detail": reason}


def _still_held(
    batch: _DrainBatch,
    delivery: dict[str, Any],
    ended: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
) -> dict[str, Any]:
    """The outcome when the host answered busy: what is left stays held."""

    _release_claim(batch.folder, batch.agent_id, batch.claim)
    reason = str(delivery.get("detail") or "The host cannot take a message now.")
    logger.warning("Held messages for agent %s stay held: %s.", batch.agent_id, reason)
    held = {
        "permissionPending": delivery.get("permissionPending"),
        "permission": delivery.get("permission"),
    }
    if ended:
        return {**_partially_delivered(ended, deliveries, reason), **held}
    return {"outcome": "still-held", "detail": reason, **held}


def _resume_for_send(batch: _DrainBatch) -> None:
    """Open the closed session before the one resend; pause when it stays closed."""

    outcome = resume_role_agent(batch.config, batch.receipt)
    if not outcome.reading.reachable:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {"outcome": "host-unreachable", "detail": "The resume was not confirmed."}
        )
    if outcome.reading.session_closed or outcome.refusal is not None:
        _release_claim(batch.folder, batch.agent_id, batch.claim)
        raise _DrainPause(
            {
                "outcome": "cannot-resume",
                "detail": str(outcome.refusal or "The host left the session closed."),
            }
        )


def _send_once(config: McpRuntimeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """One combined delivery, tried once more when a turn changed under the send.

    A refusal the host answered as busy is its admission answer, and the batch stays
    held. A call the bridge could not finish -- a timeout, a lost connection, an
    unreadable answer -- is not busy: it is returned as ``host-unreachable``, so the
    caller keeps the message held for that named reason instead of reporting the host's
    admission (MIK-R171 rules 3 and 5).
    """

    for attempt in (1, 2):
        try:
            reply = bridge_call(config, "agent-send", payload)
        except PaseoBridgeFailure as error:
            if error.code == BRIDGE_REFUSED and attempt == 1:
                continue
            if error.code == BRIDGE_REFUSED:
                return {"delivered": False, "refused": "busy", "detail": str(error)}
            # A call that ran out of time, or lost its connection while the message was
            # being sent, may have delivered it; the sender is told that (rule 3).
            uncertain = (
                " It is not known whether the message was delivered."
                if error.code in {BRIDGE_TIMEOUT, SEND_OUTCOME_UNKNOWN}
                and "not known whether" not in str(error)
                else ""
            )
            return {
                "delivered": False,
                "refused": "host-unreachable",
                "detail": f"{error.code}: {error}{uncertain}",
            }
        delivery = reply.get("delivery")
        if isinstance(delivery, dict):
            return delivery
        return {
            "delivered": False,
            "refused": "host-unreachable",
            "detail": "The Paseo bridge returned an unreadable delivery answer.",
        }
    raise AssertionError("unreachable")


_sweep_queue_cursor = {"index": 0}
"""Where the next sweep tick starts in the queue of receipts that hold messages."""

SWEEP_MAX_DRAINS_PER_TICK = 32
"""At most this many recipients drain in one sweep tick (rule 4a)."""

SWEEP_MAX_RECEIPT_READS_PER_TICK = 32
"""At most this many receipts are read in one sweep tick, idle ones included."""


_sweep_walks: dict[str, dict[str, _CandidateWalk]] = {}
"""Coordination root to its executions roots' resumable walks, one pass at a time."""


def _reset_sweep_cursor() -> None:
    """Forget the sweep walks and the queue slice (tests start every pass from the beginning)."""

    _sweep_queue_cursor["index"] = 0
    for walks in _sweep_walks.values():
        for walk in walks.values():
            walk.close()
    _sweep_walks.clear()


def _executions_roots(config: McpRuntimeConfig) -> list[Path]:
    """The executions directories whose receipts one sweep tick may visit."""

    return receipt_roots(config.coordination_root)


def _sweep_walks_for(config: McpRuntimeConfig) -> list[_CandidateWalk]:
    """The resumable walks for one tick, rebuilt when the roots change."""

    key = str(config.coordination_root)
    roots = [str(root) for root in _executions_roots(config)]
    walks = _sweep_walks.get(key)
    if walks is None or set(walks) != set(roots):
        for old in (walks or {}).values():
            old.close()
        walks = {root: _CandidateWalk(Path(root)) for root in roots}
        _sweep_walks[key] = walks
    else:
        for root, walk in walks.items():
            if walk.done:
                walk.close()
                walks[root] = _CandidateWalk(Path(root))
    return [walks[root] for root in roots]


def sweep_held_messages(config: McpRuntimeConfig) -> dict[str, Any]:
    """Deliver what waits for every recipient that has waiting messages.

    The dashboard runs this on its own tick without a model (rule 4a); a start of the
    product makes sure that tick runs. While it does not run, messages stay held.
    The receipts the holders named in the queue are drained first, so a receipt that
    holds messages is visited before the walk reaches it (MIK-R171 rule 1); the walk is
    the fallback for anything the queue does not name.
    One tick pulls at most ``SWEEP_MAX_RECEIPT_READS_PER_TICK`` candidate paths
    from the resumable walks and reads at most that many receipts, draining at
    most ``SWEEP_MAX_DRAINS_PER_TICK`` of them, so the per-tick census work
    stays bounded at any fleet or backlog size: no recursive glob, no full path
    set, no whole-list rotation. The walks resume across ticks and restart at
    each pass, so every receipt is visited without ever constructing the census.
    History receipts never drain: their executions are terminal, and their ends
    were recorded when they closed.
    """

    tally = {"swept": 0, "delivered": 0, "stillHeld": 0}
    reads = 0
    visited: set[str] = set()
    queue = list(held_queue_receipts(config))
    start = _sweep_queue_cursor["index"] % len(queue) if queue else 0
    # The queue is visited in a slice that starts where the last tick stopped, so a
    # receipt beyond the first SWEEP_MAX_DRAINS_PER_TICK is reached within a bounded
    # number of ticks even while the receipts ahead of it stay held (MIK-R171 rule 1).
    ordered = queue[start:] + queue[:start]
    for path in ordered[:SWEEP_MAX_DRAINS_PER_TICK]:
        reads += 1
        visited.add(path.as_posix())
        if _tally_drain(config, path, tally) in {None, "delivered"}:
            # Nothing waits there now: a delivered receipt needs no further visit.
            forget_held_receipt(config, path)
        if tally["swept"] >= SWEEP_MAX_DRAINS_PER_TICK:
            break
    if queue:
        _sweep_queue_cursor["index"] = (start + min(len(queue), SWEEP_MAX_DRAINS_PER_TICK)) % len(
            queue
        )
    for walk in _sweep_walks_for(config):
        for key in walk.take(SWEEP_MAX_RECEIPT_READS_PER_TICK - reads):
            reads += 1
            if key in visited:
                # Already visited this tick from the queue; one receipt is drained once.
                continue
            _tally_drain(config, Path(key), tally)
            if tally["swept"] >= SWEEP_MAX_DRAINS_PER_TICK:
                break
        if tally["swept"] >= SWEEP_MAX_DRAINS_PER_TICK:
            break
    return tally


def _tally_drain(config: McpRuntimeConfig, path: Path, tally: dict[str, int]) -> str | None:
    """Drain one receipt and count it; ``None`` when it has nothing to drain."""

    outcome = _sweep_receipt(config, path)
    if outcome is None:
        return None
    tally["swept"] += 1
    if outcome == "delivered":
        tally["delivered"] += 1
    else:
        tally["stillHeld"] += 1
    return outcome


def deliver_ended_turns(config: McpRuntimeConfig, agent_ids: Iterable[str]) -> dict[str, Any]:
    """Deliver what waits for the recipients whose turn the host reports ended (rule 1).

    The host's activity record is the driver: the dashboard reads the agents whose turn
    just ended and hands their ids here. Only receipts a holder named in the queue are
    candidates, so this reads at most ``HELD_TURN_END_MAX_DRAINS`` receipts and builds no
    census; a recipient the queue does not name is left to the sweep, which stays the
    fallback. Nothing is sent to the recipient and the delivery does not depend on
    another message reaching it.
    """

    ended = {agent_id for agent_id in agent_ids if agent_id}
    tally = {"swept": 0, "delivered": 0, "stillHeld": 0}
    if not ended:
        return tally
    for path in held_queue_receipts(config):
        if tally["swept"] >= HELD_TURN_END_MAX_DRAINS:
            break
        receipt = _readable_queued_receipt(path)
        if receipt is None:
            continue
        if _receipt_agent(receipt) not in ended:
            continue
        if _tally_drain(config, path, tally) in {None, "delivered"}:
            # Nothing waits there now: a delivered receipt needs no further visit.
            forget_held_receipt(config, path)
    return tally


def _readable_queued_receipt(path: Path) -> dict[str, Any] | None:
    """One queued receipt as read now; a receipt that is gone stops being queued."""

    try:
        return _read_receipt(path)
    except ValueError:
        return None


def _receipt_agent(receipt: dict[str, Any]) -> str | None:
    """The agent id one receipt records, when it records one."""

    execution = receipt.get("execution")
    agent_id = execution.get("agentId") if isinstance(execution, dict) else None
    return agent_id if isinstance(agent_id, str) and agent_id else None


def _receipt_needs_drain(receipt: dict[str, Any] | None) -> bool:
    """Whether one receipt may hold messages: its counter says so, or its store does.

    A receipt without a counter still drains when its store holds messages, and
    so does a receipt whose counter still reads zero: a crash between store
    admission and counter publication must not orphan them. A positive counter
    alone is enough to drain without touching the store.
    """

    if receipt is None:
        return False
    waiting = receipt.get("heldWaiting")
    if isinstance(waiting, int) and waiting > 0:
        return True
    endpoints = _store_endpoints(receipt)
    return endpoints is not None and waiting_total(endpoints[0], endpoints[1]) > 0


def _sweep_receipt(config: McpRuntimeConfig, path: Path) -> str | None:
    """Drain one receipt with waiting messages; ``None`` when it has none to drain."""

    try:
        receipt = _read_receipt(path)
    except ValueError as error:
        logger.warning("A held-message sweep could not read receipt %s: %s.", path, error)
        return None
    if not _receipt_needs_drain(receipt):
        return None
    assert receipt is not None, "a drainable receipt was read"
    try:
        result = drain_held(config, path, receipt)
    except Exception as error:
        # One line with the recipient and the reason (MIK-R171 rule 5): a sweep that
        # fails is never silent, and the store keeps everything that still waits.
        logger.warning(
            "A held-message drain of agent %s failed: %r.", _receipt_agent(receipt), error
        )
        _refresh_waiting_count(path)
        return "still-held"
    outcome = result.get("outcome")
    if outcome in {"delivered", "opened"}:
        _record_sweep_outcome(path, result)
        return "delivered"
    if outcome in {"recipient-gone", "seat-lost", "partially-delivered"}:
        _record_sweep_outcome(path, result)
    else:
        if outcome != "nothing-waiting" and "permissionPending" not in result:
            # A drain that paused - the host unreachable, the resume unconfirmed, the
            # store unclaimed or unreadable - is a failed drain and writes one line with
            # the recipient and the reason (MIK-R171 rule 5). A busy answer and a
            # permission wait already wrote theirs where they were seen.
            logger.warning(
                "A held-message drain of agent %s did not deliver: %s.",
                _receipt_agent(receipt),
                result.get("detail") or outcome or "the drain did not complete",
            )
        _refresh_waiting_count(path)
    return "still-held"


def _stage_and_record_ends(
    current: dict[str, Any], result: dict[str, Any]
) -> tuple[list[dict[str, Any]], tuple[str, str] | None]:
    """Stage this drain's ends for recovery, then line them; the rows and endpoints."""

    ended = result.get("ended")
    rows = [row for row in ended if isinstance(row, dict)] if isinstance(ended, list) else []
    how = str(result.get("how"))
    endpoints = _store_endpoints(current)
    if rows and endpoints is not None:
        _stage_pending_lines(endpoints[0], endpoints[1], rows, how)
    if rows:
        record_lines(current, rows, how)
    return rows, endpoints


def apply_drain_to_receipt(path: Path, result: dict[str, Any]) -> bool:
    """Write one drain's ends into the recipient's launch record; ``False`` when untouched.

    Ended messages gain their line (sender, time, how); an opened seat becomes running;
    the waiting count is recounted. The texts already left the store. Ends are staged
    first, so a receipt write that fails after the texts left still leaves them
    recoverable for the next flush; a successful write clears what it recorded.
    """

    try:
        current = _read_receipt(path)
    except ValueError:
        return False
    if current is None:
        return False
    rows, endpoints = _stage_and_record_ends(current, result)
    if result.get("outcome") == "opened":
        current.update(
            status="running",
            detail="The held seat opened with its handover and its waiting messages in one turn.",
            openedHeldAt=_now_iso(),
            hostAgentExists=True,
        )
        execution = result.get("execution")
        if isinstance(execution, dict):
            current["execution"] = execution
        applied = result.get("applied")
        if isinstance(applied, dict):
            current["agent"] = applied
    if result.get("outcome") == "seat-lost":
        current.update(
            status="rejected",
            detail=str(result.get("detail") or "The held seat can never take its first message."),
            updatedAt=_now_iso(),
        )
    recount_waiting_for(current)
    try:
        _write_receipt(path, current)
    except OSError:
        return False
    if rows and endpoints is not None:
        _clear_pending_lines(endpoints[0], endpoints[1], [str(row.get("id")) for row in rows])
    return True


def _record_sweep_outcome(path: Path, result: dict[str, Any]) -> None:
    apply_drain_to_receipt(path, result)


def _refresh_waiting_count(path: Path) -> None:
    try:
        current = _read_receipt(path)
    except ValueError:
        return
    if current is None:
        return
    recount_waiting_for(current)
    with suppress(OSError):
        _write_receipt(path, current)
