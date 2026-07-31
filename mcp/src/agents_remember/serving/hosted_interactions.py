"""Durable gate and inbox synchronization for adapter-owned interactions and completion."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    GateRecord,
    apply_gate,
    create_gate,
    expire_gate,
    reopen_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.errors import HarnessControlError
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.harness_control_client import (
    read_control_transcript,
    respond_control_interaction,
)
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    PendingInteraction,
    interaction_question_json,
)
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

logger = logging.getLogger(__name__)

INTERACTION_DELIVERY_ATTEMPT_LIMIT = 3
"""How many times ONE decision may be re-offered to the adapter before the gate goes back open.

A control write that provably never left this process (``may_have_sent=False``) says nothing about
whether the bridge is dead or merely busy -- the same ambiguity that made the read path require
consecutive failures -- so a single failed sweep must not cost the operator their decision. An
unbounded retry is the other failure: it hides a dead bridge behind a gate that looks decided
forever. Three attempts (~3 liveness sweeps) is the bound; after that the operator is told.
"""


class HostedInteractionSynchronizer:
    """Project vendor questions into gates and terminal results onto their durable inbox rows."""

    def __init__(self, root: Path) -> None:
        self._gates = GateStore(root)
        self._inbox = OperatorInboxStore(root)

    def observe(self, entry: TerminalCatalogEntry, snapshot: AdapterSnapshot) -> None:
        self._sync_interaction(entry, snapshot.pending_interaction)
        self._sync_completions(entry)

    def _sync_interaction(
        self, entry: TerminalCatalogEntry, interaction: PendingInteraction | None
    ) -> None:
        matching = self._interaction_gate(
            entry.id, interaction.interaction_id if interaction else None
        )
        if interaction is None:
            if matching is not None and matching.state == "open":
                self._gates.append(expire_gate(matching, now=now_iso()))
            # A gate mid-retry (still decided -- see _record_delivery_failure) is deliberately NOT
            # expired here: expiry is pruned immediately (interaction_retention), which would erase
            # the record that the operator's decision never reached the adapter. It stays decided
            # carrying its adapterDecisionFailure packet -- listable, and inert because
            # agent-question is not a mutating-tool gate kind -- until ordinary retention reclaims
            # it. An unanswerable question is not worth a lost account of what happened to it.
            return
        if matching is None:
            self._gates.append(
                create_gate(
                    kind="agent-question",
                    lifecycle_id=entry.lifecycle_id,
                    gate_id=new_ulid(),
                    now=now_iso(),
                    packet={
                        "adapterInteraction": {
                            "sessionId": entry.id,
                            "interactionId": interaction.interaction_id,
                            "kind": interaction.kind,
                            "prompt": interaction.prompt,
                            "choices": list(interaction.choices),
                            "createdAt": interaction.created_at,
                            "raw": dict(interaction.raw),
                            "questions": [
                                interaction_question_json(question)
                                for question in interaction.questions
                            ],
                        }
                    },
                    required_decision=(
                        list(interaction.choices) if interaction.choices else ["approve", "reject"]
                    ),
                )
            )
            return
        if matching.state not in {
            "approved",
            "rejected",
            "revision-requested",
            "cancelled",
        }:
            return
        try:
            response = _gate_response(matching)
        except HarnessControlError as exc:
            # The decision itself cannot be spoken to the adapter (a note the vendor answer
            # contract rejects). Nothing was written and no retry can change that -- only a fresh
            # decision can -- so the gate reopens now, carrying what was decided and why it failed.
            self._reopen_undelivered(matching, entry, exc, delivery="not-sent", attempts=0)
            return
        try:
            respond_control_interaction(
                entry,
                interaction_id=interaction.interaction_id,
                response=response,
            )
        except HarnessControlError as exc:
            self._record_delivery_failure(matching, entry, exc)
            return
        self._gates.append(apply_gate(matching, now=now_iso()))

    def _record_delivery_failure(
        self,
        gate: GateRecord,
        entry: TerminalCatalogEntry,
        exc: HarnessControlError,
    ) -> None:
        """Route one failed respond by its retry-safety evidence, never by the base class alone.

        ``may_have_sent`` (``errors.py``) is the ONLY fact separating "the answer never left this
        process" from "the vendor may already have consumed it", and the two need opposite
        handling: retrying the second would answer one interaction twice, while reopening the
        first on its first failure throws away a decision a busy bridge would have taken a sweep
        later. So a certified pre-write failure keeps the gate decided and re-offers it on the
        next sweep (bounded by :data:`INTERACTION_DELIVERY_ATTEMPT_LIMIT`); everything else stops
        and hands the interaction back with the delivery recorded as unknown.
        """

        attempts = _delivery_attempts(gate) + 1
        if not _proven_unsent(exc):
            self._reopen_undelivered(gate, entry, exc, delivery="unknown", attempts=attempts)
            return
        if attempts >= INTERACTION_DELIVERY_ATTEMPT_LIMIT:
            self._reopen_undelivered(gate, entry, exc, delivery="not-sent", attempts=attempts)
            return
        logger.warning(
            "interaction gate %s decision reached no bytes of session %s "
            "(attempt %d of %d); keeping the decision and retrying on the next sweep: %s",
            gate.id,
            entry.id,
            attempts,
            INTERACTION_DELIVERY_ATTEMPT_LIMIT,
            exc,
        )
        # The gate stays decided so the next sweep re-enters this path -- that per-sweep retry is
        # the whole point. The attempt count rides the packet rather than process memory so the
        # budget survives a serving restart and stays auditable in the append-only log.
        self._gates.append(
            gate.model_copy(
                update={
                    "ts": now_iso(),
                    "packet": _with_delivery_failure(
                        gate, exc, delivery="not-sent", attempts=attempts
                    ),
                }
            )
        )

    def _reopen_undelivered(
        self,
        gate: GateRecord,
        entry: TerminalCatalogEntry,
        exc: HarnessControlError,
        *,
        delivery: str,
        attempts: int,
    ) -> None:
        """Reopen a gate whose decision could not be applied -- and say so in the packet.

        ``reopen_gate`` clears the decision attribution, which is right: the reopened snapshot
        genuinely holds no decision, and every reader downstream (``gate_policy``, the projection,
        the dashboard) treats ``decidedBy``/``decidedAt`` as proof that someone decided. But the
        decision must not vanish either -- being asked to retype an answers map with no hint that
        the first one may already be live is its own dishonesty -- so it is copied verbatim into
        ``packet.adapterDecisionFailure`` together with the failure reason and whether the vendor
        may already hold it. Open plus that record is the truthful pair: nobody has decided the
        gate as it now stands, and here is exactly what happened to the last attempt.
        """

        logger.warning(
            "interaction gate %s decision could not reach session %s "
            "(delivery %s after %d attempt(s)); reopening the gate: %s",
            gate.id,
            entry.id,
            delivery,
            attempts,
            exc,
        )
        stamped = gate.model_copy(
            update={
                "packet": _with_delivery_failure(gate, exc, delivery=delivery, attempts=attempts)
            }
        )
        self._gates.append(reopen_gate(stamped, now=now_iso()))

    def _interaction_gate(self, session_id: str, interaction_id: str | None) -> GateRecord | None:
        if interaction_id is None:
            return next(
                (
                    gate
                    for gate in self._gates.all_current().values()
                    if gate.state == "open"
                    and (_interaction_identity(gate) or (None, None))[0] == session_id
                ),
                None,
            )
        return next(
            (
                gate
                for gate in self._gates.all_current().values()
                if _interaction_identity(gate) == (session_id, interaction_id)
            ),
            None,
        )

    def _sync_completions(self, entry: TerminalCatalogEntry) -> None:
        try:
            transcript = read_control_transcript(entry)
        except HarnessControlError:
            return
        current = self._inbox.current()
        for item in transcript:
            terminal_result = item.get("terminalResult")
            if not isinstance(terminal_result, Mapping):
                continue
            request_id = _completion_request_id(item, current, session_id=entry.id)
            if request_id is None or current[request_id].adapterDeliveryState == "completed":
                continue
            outcome = terminal_result.get("outcome")
            completed_at = terminal_result.get("completedAt")
            correlation = item.get("vendorCorrelationId")
            self._inbox.record_adapter_completion(
                request_id,
                now=completed_at if isinstance(completed_at, str) else now_iso(),
                vendor_correlation_id=correlation if isinstance(correlation, str) else None,
                detail=f"adapter terminal result: {outcome}",
                current=current,
            )


def _completion_request_id(
    item: Mapping[str, object],
    current: Mapping[str, OperatorInboxEntry],
    *,
    session_id: str,
) -> str | None:
    request_id = item.get("requestId")
    if isinstance(request_id, str):
        return request_id if request_id in current else None
    if request_id is not None:
        raise HarnessControlError("adapter terminal result carried a non-text requestId")
    return _request_id_from_vendor_correlation(item, current, session_id=session_id)


def _request_id_from_vendor_correlation(
    item: Mapping[str, object],
    current: Mapping[str, OperatorInboxEntry],
    *,
    session_id: str,
) -> str:
    correlation = item.get("vendorCorrelationId")
    if not isinstance(correlation, str) or not correlation.strip():
        raise HarnessControlError(
            "adapter terminal result without requestId requires vendorCorrelationId"
        )
    matches = [
        row
        for row in current.values()
        if row.adapterVendorCorrelationId == correlation and row.deliveredToSession == session_id
    ]
    if not matches:
        raise HarnessControlError(
            "adapter terminal result vendor correlation does not match an accepted inbox row "
            "for the hosted session"
        )
    if len(matches) > 1:
        matched_ids = ", ".join(sorted(row.id for row in matches))
        raise HarnessControlError(
            f"adapter terminal result vendor correlation matches multiple inbox rows: {matched_ids}"
        )
    matched = matches[0]
    if matched.adapterDeliveryState not in {"accepted", "completed"}:
        raise HarnessControlError(
            "adapter terminal result vendor correlation matches an inbox row without "
            "accepted delivery evidence"
        )
    return matched.id


def _proven_unsent(exc: HarnessControlError) -> bool:
    """Whether a failed control write certifies that ZERO request bytes reached the bridge.

    ``may_have_sent`` is that certificate (``errors.py``: "retry-safety evidence"), and only
    ``False`` proves the answer never left this process -- the connect-stage failures a dead or
    booting bridge produces. A base :class:`HarnessControlError` carries no certificate at all,
    and several of them are raised *after* a completed exchange (``respond_control_interaction``
    validates the returned snapshot's identity once the vendor already has the answer), so "no
    certificate" must read as "may have been delivered" and never as retryable.
    """

    return getattr(exc, "may_have_sent", None) is False


def _delivery_failure(gate: GateRecord) -> Mapping[str, object] | None:
    """The packet's delivery-failure record for THIS decision, or None.

    The attempt budget is per decision, not per gate: a fresh decision copies the packet forward
    (``decide_gate`` is a ``model_copy``), so a record stamped against an older ``decidedAt`` is
    history from a previous attempt round and must not spend the new decision's budget.
    """

    record = gate.packet.get("adapterDecisionFailure")
    if not isinstance(record, Mapping):
        return None
    return record if record.get("decidedAt") == gate.decidedAt else None


def _delivery_attempts(gate: GateRecord) -> int:
    record = _delivery_failure(gate)
    attempts = record.get("attempts") if record is not None else None
    return attempts if isinstance(attempts, int) else 0


def _with_delivery_failure(
    gate: GateRecord,
    exc: HarnessControlError,
    *,
    delivery: str,
    attempts: int,
) -> dict[str, Any]:
    """The gate packet plus an honest record of what happened to the operator's decision.

    ``delivery`` is ``not-sent`` (proven: no bytes reached the bridge) or ``unknown`` (the vendor
    may already hold the answer) -- never a claim we cannot prove. The decided fields are copied
    verbatim so a reopened gate can show the operator their own answer instead of a blank prompt.
    """

    record = {
        "delivery": delivery,
        "attempts": attempts,
        "reason": str(exc),
        "observedAt": now_iso(),
        "decision": gate.state,
        "decidedAt": gate.decidedAt,
        "decidedBy": gate.decidedBy,
        "decidedVia": gate.decidedVia,
        "decidingRole": gate.decidingRole,
        "decisionNote": gate.decisionNote,
    }
    return {
        **gate.packet,
        "adapterDecisionFailure": {
            key: value for key, value in record.items() if value is not None
        },
    }


def _interaction_identity(gate: GateRecord) -> tuple[str, str | None] | None:
    raw = gate.packet.get("adapterInteraction")
    if not isinstance(raw, dict):
        return None
    session_id = raw.get("sessionId")
    interaction_id = raw.get("interactionId")
    if not isinstance(session_id, str) or not isinstance(interaction_id, str):
        return None
    return session_id, interaction_id


def _gate_response(gate: GateRecord) -> str:
    """The response text returned to the adapter for one decided interaction gate.

    A gate whose packet carries structured question pages (AskUserQuestion) requires the
    decision note to be the answers map -- a JSON object mapping every question's exact text
    to its answer -- the same contract the session-direct interaction-response route sends.
    Anything else raises honestly here (the caller reopens the gate) instead of being
    swallowed downstream after a doomed respond. Permission-kind gates keep the plain-note
    contract (``allow``/``deny`` or the state fallback).
    """

    if gate.decisionNote:
        if _structured_questions(gate):
            _answers_map(gate.decisionNote)
        return gate.decisionNote
    return {
        "approved": "approved",
        "rejected": "rejected",
        "revision-requested": "request revision",
        "cancelled": "cancelled",
    }[gate.state]


def _structured_questions(gate: GateRecord) -> list[object]:
    raw = gate.packet.get("adapterInteraction")
    if not isinstance(raw, dict):
        return []
    questions = raw.get("questions")
    return questions if isinstance(questions, list) else []


def _answers_map(note: str) -> None:
    try:
        decoded = json.loads(note)
    except json.JSONDecodeError as exc:
        raise HarnessControlError(
            "a structured question gate requires the decision note to be a JSON object "
            "mapping each question's exact text to its answer"
        ) from exc
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in decoded.items()
    ):
        raise HarnessControlError(
            "a structured question gate requires the decision note to map question text "
            "to string answers"
        )
