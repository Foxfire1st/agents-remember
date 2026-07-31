"""Tool-layer orchestration for one readiness-gated durable dispatch brief."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from agents_remember.controlplane.expectation_rows import (
    Expectation,
    ExpectationKind,
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
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer import observer_root
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.dispatch_brief import (
    DISPATCH_BRIEF_KIND,
    DispatchBriefGate,
    fulfill_briefed_expectation,
)
from agents_remember.serving.hosted_readiness import (
    HostedReadinessHost,
    HostedReadinessResult,
    hosted_session_readiness,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import TerminalPaster

DispatchReadinessCheck = Callable[
    [TerminalCatalog, HostedReadinessHost, str], HostedReadinessResult
]


@dataclass(frozen=True)
class HostedDelivery:
    """Whether an inbox entry is pushed into its recipient's live hosted session, and the
    collaborators that do the pushing.

    ``enabled`` is the caller's request to deliver at all; the rest are the seams the
    delivery runs through -- the session catalog that locates the recipient, the terminal
    host and paster that write into it, the readiness probe a ``dispatch-brief`` must pass
    before a durable row is created, and the gate that admits it. Each is optional so
    production takes the real collaborator and tests inject a double.
    """

    enabled: bool = True
    catalog: TerminalCatalog | None = None
    host: TerminalHost | None = None
    paster: TerminalPaster | None = None
    readiness: DispatchReadinessCheck | None = None
    gate: DispatchBriefGate | None = None


HOSTED_DELIVERY = HostedDelivery()
"""Deliver to the recipient's hosted session using the real catalog, host, and paster."""

NO_HOSTED_DELIVERY = HostedDelivery(enabled=False)
"""Record the entry durably and stop -- nothing is pushed into a live session."""


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
    """Return the exact ready target, or refuse before the durable row is created."""

    if message_kind != DISPATCH_BRIEF_KIND:
        return None
    if delivery.catalog is None:
        raise ValueError("dispatch-brief requires runtime configuration")
    if agent_id is None or not delivery.enabled:
        raise ValueError("dispatch-brief requires exact agent_id and deliver_to_hosted=true")
    observed = (delivery.readiness or _readiness)(delivery.catalog, host, agent_id)
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
    """Start assignment clocks from the one durable dispatch row's timestamp and id."""

    store = expectation_store(config)
    created_at = datetime.fromisoformat(entry.createdAt)
    leaf_key = target.binding_leaf_key
    rows: list[tuple[ExpectationKind, str]] = [
        ("briefed-by", f"briefed-by: {target.label} ({target.spawn_role or target.kind})")
    ]
    if leaf_key is not None:
        rows.append(("turn-report-by", f"turn-report-by: {leaf_key}"))
    for kind, note in rows:
        if store.find_by_source(entry.id, kind=kind) is not None:
            continue
        write_expectation_row(
            store,
            Expectation(
                kind=kind,
                source_id=entry.id,
                subject=ExpectationSubject(
                    agent_id=target.id,
                    lifecycle_id=target.lifecycle_id,
                    leaf_key=leaf_key,
                    seat_role=target.binding_role,
                ),
                note=note,
            ),
            row_id=new_ulid(),
            now=created_at,
            sla_seconds=expectation_sla_seconds(config, kind),
        )


def fulfill_dispatch_expectation(
    config: McpRuntimeConfig,
    entry: OperatorInboxEntry,
) -> None:
    fulfill_briefed_expectation(expectation_store(config), entry)


def _readiness(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    session_id: str,
) -> HostedReadinessResult:
    return hosted_session_readiness(catalog, host, session_id=session_id)
