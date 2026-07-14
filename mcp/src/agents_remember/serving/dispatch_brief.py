"""Policy for one readiness-gated durable dispatch brief."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agents_remember.controlplane.expectation_rows import ExpectationRow, ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.serving.hosted_readiness import (
    HostedReadinessHost,
    HostedReadinessResult,
    hosted_session_identity,
    hosted_session_readiness,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

DISPATCH_BRIEF_KIND = "dispatch-brief"
ReadinessCheck = Callable[[TerminalCatalog, HostedReadinessHost, str], HostedReadinessResult]


def _readiness_check(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    session_id: str,
) -> HostedReadinessResult:
    return hosted_session_readiness(catalog, host, session_id=session_id)


@dataclass(frozen=True)
class DispatchBriefGate:
    """Exact-session protocol readiness gate; terminal input mode has no authority."""

    readiness: ReadinessCheck = _readiness_check

    def check(
        self,
        catalog: TerminalCatalog,
        host: HostedReadinessHost,
        target: TerminalCatalogEntry,
        *,
        recovery: bool = False,
    ) -> str | None:
        del recovery  # retries obey the same protocol handshake; no compatibility readiness path
        observed = self.readiness(catalog, host, target.id)
        failure = _exact_running_failure(
            observed,
            target,
            phase="during adapter readiness check",
        )
        if failure is not None:
            return failure
        if _final_readiness_allows_input(observed):
            return None
        return f"dispatch target is {observed.status}: {observed.detail or 'not adapter-ready'}"


def _exact_running_failure(
    observed: HostedReadinessResult,
    target: TerminalCatalogEntry,
    *,
    phase: str,
) -> str | None:
    entry = observed.entry
    if entry is None or hosted_session_identity(entry) != hosted_session_identity(target):
        return f"dispatch target identity changed {phase}; no input sent"
    if entry.status != "running":
        return f"dispatch target is {observed.status}: {observed.detail or 'not running'}"
    return None


def _final_readiness_allows_input(
    observed: HostedReadinessResult,
) -> bool:
    return observed.status == "ready"


def with_prompt_keywords(target: TerminalCatalogEntry, text: str) -> str:
    """Prepend settings-owned prompt keywords as exactly one line on the durable brief."""

    if not target.prompt_keywords:
        return text
    return f"{' '.join(target.prompt_keywords)}\n\n{text}"


def delivery_is_briefed(entry: OperatorInboxEntry) -> bool:
    return (
        entry.messageKind == DISPATCH_BRIEF_KIND
        and entry.deliveryState == "delivered"
        and entry.adapterDeliveryState in {"accepted", "queued", "completed"}
    )


def dispatch_stays_on_exact_session(entry: OperatorInboxEntry) -> bool:
    """Pending dispatch rows never enter a ladder that can readdress their exact agent id."""

    return entry.messageKind == DISPATCH_BRIEF_KIND and entry.state == "pending"


def fulfill_briefed_expectation(
    store: ExpectationRowStore,
    entry: OperatorInboxEntry,
    *,
    current: dict[str, ExpectationRow] | None = None,
) -> None:
    """Fulfill the entry-id-addressed brief clock only from both required proof fields."""

    if not delivery_is_briefed(entry):
        return
    row = store.find_by_source(entry.id, kind="briefed-by", current=current)
    if row is not None:
        store.mark_met(row.id, now=entry.deliveredAt or entry.ts, current=current)
