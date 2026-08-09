"""Policy for one readiness-gated durable dispatch brief."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from agents_remember.controlplane.expectation_rows import (
    Expectation,
    ExpectationRow,
    ExpectationRowStore,
    ExpectationSubject,
    write_expectation_row,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxMessageKind,
    OperatorInboxEntry,
)
from agents_remember.kernel.agentic_settings import (
    DEFAULT_EXPECTATION_SLA_SECONDS,
    load_agentic_settings,
)
from agents_remember.observer import observer_root
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.hosted_readiness import (
    HostedReadinessHost,
    HostedReadinessResult,
    hosted_session_identity,
    hosted_session_readiness,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import TerminalPaster

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

DISPATCH_BRIEF_KIND = "dispatch-brief"
ReadinessCheck = Callable[[TerminalCatalog, HostedReadinessHost, str], HostedReadinessResult]


@dataclass(frozen=True)
class HostedDelivery:
    """Hosted inbox delivery collaborators owned by the serving performer layer."""

    enabled: bool = True
    catalog: TerminalCatalog | None = None
    host: TerminalHost | None = None
    paster: TerminalPaster | None = None
    readiness: ReadinessCheck | None = None
    gate: DispatchBriefGate | None = None


HOSTED_DELIVERY = HostedDelivery()
"""Deliver through the real hosted-session collaborators."""

NO_HOSTED_DELIVERY = HostedDelivery(enabled=False)
"""Persist the inbox entry without pushing it into a hosted session."""


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


def expectation_store(config: McpRuntimeConfig) -> ExpectationRowStore:
    return ExpectationRowStore(observer_root(config))


def expectation_sla_seconds(config: McpRuntimeConfig | None, kind: str) -> float:
    if config is None:
        return DEFAULT_EXPECTATION_SLA_SECONDS[kind]
    return load_agentic_settings(config.coordination_root).expectations.sla_for(kind)


def require_dispatch_target(
    *,
    message_kind: InboxMessageKind,
    agent_id: str | None,
    delivery: HostedDelivery,
    host: HostedReadinessHost,
) -> TerminalCatalogEntry | None:
    """Return the exact ready dispatch target, or refuse before persistence."""

    if message_kind != DISPATCH_BRIEF_KIND:
        return None
    if delivery.catalog is None:
        raise ValueError("dispatch-brief requires runtime configuration")
    if agent_id is None or not delivery.enabled:
        raise ValueError("dispatch-brief requires exact agent_id and deliver_to_hosted=true")
    observed = (delivery.readiness or _readiness_check)(delivery.catalog, host, agent_id)
    if observed.status != "ready" or observed.entry is None or observed.entry.id != agent_id:
        raise ValueError(
            "dispatch-brief requires prior exact-session status=ready; "
            f"observed {observed.status}: {observed.detail or 'not ready'}"
        )
    return observed.entry


def start_dispatch_expectations(
    config: McpRuntimeConfig,
    entry: OperatorInboxEntry,
    target: TerminalCatalogEntry,
) -> None:
    """Start the briefed-by clock from the one durable dispatch row.

    The turn-report-by clock is retired: completion truth comes from the catalog turn
    projection, never from artifact/clock inference.
    """

    store = expectation_store(config)
    created_at = datetime.fromisoformat(entry.createdAt)
    leaf_key = target.binding_leaf_key
    if store.find_by_source(entry.id, kind="briefed-by") is not None:
        return
    write_expectation_row(
        store,
        Expectation(
            kind="briefed-by",
            source_id=entry.id,
            subject=ExpectationSubject(
                agent_id=target.id,
                lifecycle_id=target.lifecycle_id,
                leaf_key=leaf_key,
                seat_role=target.binding_role,
            ),
            note=f"briefed-by: {target.label} ({target.spawn_role or target.kind})",
        ),
        row_id=new_ulid(),
        now=created_at,
        sla_seconds=expectation_sla_seconds(config, "briefed-by"),
    )


def fulfill_dispatch_expectation(config: McpRuntimeConfig, entry: OperatorInboxEntry) -> None:
    fulfill_briefed_expectation(expectation_store(config), entry)


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
