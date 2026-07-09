"""Hosted-session delivery for durable operator inbox messages.

260707-HFX2-L3 (R1 + R3): pushing an inbox row into a hosted session now goes through the ONE
delivery path (``serving/injector.deliver``) every other payload class uses -- this module's job
narrows to translating that path's four-way ``DeliveryOutcome`` back onto the ``OperatorInboxEntry``
schema's existing ``InboxDeliveryState`` (``"queued" | "no-hosted-session" | "delivered" |
"unconfirmed"``), which this leaf leaves UNCHANGED (it rides through the dashboard, the backoff
predicate, and the L2 supervisor -- widening it is a bigger-blast-radius leaf than this one). A
``blocked`` outcome (a modal dialog trap -- codex quota/rate-limit (#20), a permission prompt) maps
to ``"unconfirmed"`` with a ``NEEDS-ATTENTION:`` prefixed detail, so it stays structured and
diagnosable in the durable row's ``deliveryDetail`` without a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.operator_inbox_records import (
    InboxDeliveryState,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.observer.events import now_iso
from agents_remember.serving.injector import DeliveryResult, DeliveryRow, deliver
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import TerminalPaster

_CAPTURE_EVIDENCE_LIMIT = 2000
"""Durable-row bound for an attached pane capture: keep the TAIL (the freshest pane output)."""


@dataclass(frozen=True)
class InboxDeliveryResult:
    """Outcome of trying to push one durable inbox row into a hosted session."""

    state: str
    session_id: str | None = None
    detail: str | None = None


def deliver_inbox_entry(
    *,
    store: OperatorInboxStore,
    catalog: TerminalCatalog,
    host: TerminalHost,
    paster: TerminalPaster,
    entry: OperatorInboxEntry,
    submit: bool = True,
    current: dict[str, OperatorInboxEntry] | None = None,
    redelivery_floor_seconds: float | None = None,
) -> OperatorInboxEntry:
    """Push an inbox message into the target hosted session and record the delivery state."""
    target = _target_session(catalog, entry)
    if target is None:
        return store.record_delivery(
            entry.id,
            now=now_iso(),
            delivery_state="no-hosted-session",
            delivery_detail="no running hosted session matched the inbox address",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    if not host.has_session(target.tmux_name):
        return store.record_delivery(
            entry.id,
            now=now_iso(),
            delivery_state="no-hosted-session",
            delivered_to_session=target.id,
            delivery_detail="catalog row exists but tmux session is not running",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    row = DeliveryRow(
        kind=entry.messageKind,
        entry_id=entry.id,
        text=_push_text(entry),
        submit=submit,
        envelope=False,  # _push_text already renders this payload's own header (below).
    )
    result = deliver(row, tmux_name=target.tmux_name, paster=paster, harness=target.harness)
    return store.record_delivery(
        entry.id,
        now=now_iso(),
        delivery_state=_delivery_state(result),
        delivered_to_session=target.id,
        delivery_detail=_delivery_detail(result),
        current=current,
        redelivery_floor_seconds=redelivery_floor_seconds,
    )


def _delivery_state(result: DeliveryResult) -> InboxDeliveryState:
    """Map the R1 ``DeliveryOutcome`` onto the unchanged ``InboxDeliveryState`` vocabulary."""
    return "delivered" if result.outcome == "acked" else "unconfirmed"


def _delivery_detail(result: DeliveryResult) -> str:
    if result.outcome == "acked":
        return "echo-confirmed"
    if result.outcome == "blocked":
        return f"NEEDS-ATTENTION: blocked ({result.reason}); {_capture_detail(result.capture)}"
    if result.outcome == "landed-unacked":
        return f"paste landed but was not acked ({result.reason}); {_capture_detail(result.capture)}"
    return _unconfirmed_detail(result.capture)


def _capture_detail(capture: str) -> str:
    if not capture:
        return "empty pane capture"
    return "pane capture (tail):\n" + capture[-_CAPTURE_EVIDENCE_LIMIT:]


def _unconfirmed_detail(capture: str) -> str:
    """The 260707-HFX-L3 loud-failure detail: an unverified push carries its pane capture.

    Never a bare "not echoed" -- the durable row is the forensic record a re-briefing operator
    reads, so the evidence (what the pane actually showed) rides along, tail-bounded.
    """
    if not capture:
        return "paste was not capture-verified (empty pane capture)"
    return "paste was not capture-verified; pane capture (tail):\n" + capture[-_CAPTURE_EVIDENCE_LIMIT:]


def _target_session(
    catalog: TerminalCatalog,
    entry: OperatorInboxEntry,
) -> TerminalCatalogEntry | None:
    if entry.agentId:
        target = catalog.get(entry.agentId)
        if target is not None and target.status == "running":
            return target
    if entry.lifecycleId:
        return next(
            (
                target
                for target in catalog.list()
                if target.status == "running" and target.lifecycle_id == entry.lifecycleId
            ),
            None,
        )
    return None


def _push_text(entry: OperatorInboxEntry) -> str:
    sender = entry.senderRole or "operator"
    if entry.senderAgentId:
        sender = f"{sender}:{entry.senderAgentId}"
    parts = [
        f"[Agents Remember inbox:{entry.messageKind}]",
        f"from: {sender}",
        f"entry: {entry.id}",
        f"ack: reply in this chat -- entry {entry.id} is the mechanical ack target",
    ]
    if entry.artifactPath:
        parts.append(f"artifact: {entry.artifactPath}")
    parts.extend(["", entry.ask, "", entry.response])
    return "\n".join(parts)
