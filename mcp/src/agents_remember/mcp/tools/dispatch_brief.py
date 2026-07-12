"""Tool-layer orchestration for one readiness-gated durable dispatch brief."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from agents_remember.controlplane.expectation_rows import (
    ExpectationKind,
    ExpectationRowStore,
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
    fulfill_briefed_expectation,
)
from agents_remember.serving.hosted_readiness import (
    HostedReadinessHost,
    HostedReadinessResult,
    hosted_session_readiness,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

DispatchReadinessCheck = Callable[
    [TerminalCatalog, HostedReadinessHost, str], HostedReadinessResult
]


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
    deliver_to_hosted: bool,
    catalog: TerminalCatalog | None,
    host: HostedReadinessHost,
    readiness: DispatchReadinessCheck | None,
) -> TerminalCatalogEntry | None:
    """Return the exact ready target, or refuse before the durable row is created."""

    if message_kind != DISPATCH_BRIEF_KIND:
        return None
    if catalog is None:
        raise ValueError("dispatch-brief requires runtime configuration")
    if agent_id is None or not deliver_to_hosted:
        raise ValueError("dispatch-brief requires exact agent_id and deliver_to_hosted=true")
    observed = (readiness or _readiness)(catalog, host, agent_id)
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
            row_id=new_ulid(),
            now=created_at,
            kind=kind,
            sla_seconds=expectation_sla_seconds(config, kind),
            source_id=entry.id,
            subject_agent_id=target.id,
            subject_lifecycle_id=target.lifecycle_id,
            leaf_key=leaf_key,
            seat_role=target.binding_role,
            note=note,
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
