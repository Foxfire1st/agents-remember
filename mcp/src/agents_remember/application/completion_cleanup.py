"""Report-gated cleanup for seats whose worktree completion edge has succeeded."""

from __future__ import annotations

import contextlib
from pathlib import Path

from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.observer.events import now_iso
from agents_remember.serving.landing import land_seats_for_leaf
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.seat_events import log_landed_event, log_retire_event
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    terminal_catalog_path,
)
from agents_remember.worktrees.worktree_contract import load_contract

CLOSABLE_LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})
"""Finite automatic-cleanup boundary; coordination-owner seats never enter it."""


def auto_complete_seats(
    config: McpRuntimeConfig,
    contract_path: Path,
    *,
    reason: str,
    edge: str,
) -> dict[str, list[str]]:
    """Close or archive completed leaf seats without endangering the successful edge.

    Auto-close is fail-closed per seat: the exact session must already have a durable turn-report
    row for the exact qualified leaf before normal retirement may kill its tmux process. With the
    behavior disabled, the same role set follows the landed/archive path. Manager and orchestrator
    are structurally excluded by ``CLOSABLE_LEAF_ROLES`` at both edges.

    Cleanup is subordinate to an already-succeeded integrate/finalize operation, so contract,
    inbox, catalog, host, or event-log failures never rewrite that completion truth.
    """
    try:
        contract = load_contract(contract_path)
        leaf_key = f"{contract.repo_name}/{contract.task_root.name}/{contract.task_id}"
        catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
        closure = SeatClosure(reason=reason, edge=edge, at=now_iso())
        if config.retirement.auto_close_completed_seats:
            return _retire_reported_leaf_seats(config, catalog, leaf_key, closure)
        landed = land_seats_for_leaf(
            catalog,
            closure,
            leaf_key=leaf_key,
            roles=CLOSABLE_LEAF_ROLES,
        )
        for entry in landed:
            log_landed_event(config, entry)
        return {"autoLandedSeats": [entry.id for entry in landed]}
    except Exception:
        if config.retirement.auto_close_completed_seats:
            return {
                "autoClosedSeats": [],
                "autoCloseDeferredSeats": [],
                "autoCloseFailedSeats": [],
            }
        return {"autoLandedSeats": []}


def _retire_reported_leaf_seats(
    config: McpRuntimeConfig,
    catalog: TerminalCatalog,
    leaf_key: str,
    closure: SeatClosure,
) -> dict[str, list[str]]:
    """Retire eligible leaf seats after the durable turn-report ordering barrier."""
    reports = OperatorInboxStore(observer_root(config)).current().values()
    reported_sessions = {
        report.senderAgentId
        for report in reports
        if report.messageKind == "turn-report"
        and report.leafKey == leaf_key
        and report.senderAgentId is not None
    }
    candidates = _completion_candidates(catalog, leaf_key)
    host = TerminalHost()
    closed: list[str] = []
    deferred: list[str] = []
    failed: list[str] = []
    for candidate in candidates:
        if candidate.id not in reported_sessions:
            deferred.append(candidate.id)
            continue
        try:
            retired = retire_entry(catalog, host, candidate, closure)
        except Exception:
            failed.append(candidate.id)
            continue
        if retired is not None and retired.status == "terminated":
            closed.append(retired.id)
            # Catalog provenance is durable; event-log failure cannot undo process cleanup.
            with contextlib.suppress(Exception):
                log_retire_event(config, retired)
    return {
        "autoClosedSeats": closed,
        "autoCloseDeferredSeats": deferred,
        "autoCloseFailedSeats": failed,
    }


def _completion_candidates(catalog: TerminalCatalog, leaf_key: str) -> list[TerminalCatalogEntry]:
    """Return live/archive leaf-altitude seats; never coordination-owner seats."""
    return [
        entry
        for entry in catalog.list(include_terminated=True)
        if entry.leaf_key == leaf_key
        and entry.status != "terminated"
        and entry.binding_role in CLOSABLE_LEAF_ROLES
    ]
