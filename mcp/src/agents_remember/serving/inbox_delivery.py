"""Hosted-session delivery for durable operator inbox messages."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.observer.events import now_iso
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster

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
) -> OperatorInboxEntry:
    """Push an inbox message into the target hosted session and record the delivery state."""
    target = _target_session(catalog, entry)
    if target is None:
        return store.record_delivery(
            entry.id,
            now=now_iso(),
            delivery_state="no-hosted-session",
            delivery_detail="no running hosted session matched the inbox address",
        )
    if not host.has_session(target.tmux_name):
        return store.record_delivery(
            entry.id,
            now=now_iso(),
            delivery_state="no-hosted-session",
            delivered_to_session=target.id,
            delivery_detail="catalog row exists but tmux session is not running",
        )
    outcome = paster.paste(target.tmux_name, _push_text(entry), submit=submit)
    return store.record_delivery(
        entry.id,
        now=now_iso(),
        delivery_state="delivered" if outcome.delivered else "unconfirmed",
        delivered_to_session=target.id,
        delivery_detail="echo-confirmed" if outcome.delivered else _unconfirmed_detail(outcome),
    )


def _unconfirmed_detail(outcome: PasteResult) -> str:
    """The 260707-HFX-L3 loud-failure detail: an unverified push carries its pane capture.

    Never a bare "not echoed" -- the durable row is the forensic record a re-briefing operator
    reads, so the evidence (what the pane actually showed) rides along, tail-bounded.
    """
    if not outcome.capture:
        return "paste was not capture-verified (empty pane capture)"
    return (
        "paste was not capture-verified; pane capture (tail):\n"
        + outcome.capture[-_CAPTURE_EVIDENCE_LIMIT:]
    )


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
    ]
    if entry.artifactPath:
        parts.append(f"artifact: {entry.artifactPath}")
    parts.extend(["", entry.ask, "", entry.response])
    return "\n".join(parts)
