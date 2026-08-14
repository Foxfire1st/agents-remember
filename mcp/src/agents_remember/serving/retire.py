"""Seat retirement mechanics: shared by manual retire and explicit landed archive cleanup.

Retirement kills the tmux session (best-effort; an already-gone session is not an error), then
persists the catalog's terminal mark + retirement provenance. Transcripts are never touched:
retiring is a catalog-and-tmux operation only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from agents_remember.errors import HarnessControlError
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.harness_control_client import stop_control_session
from agents_remember.serving.ports import TerminalCatalogPort

if TYPE_CHECKING:
    from agents_remember.serving.terminal import TerminalHost


@dataclass(frozen=True)
class SeatClosure:
    """Why a seat stopped, when, and on whose authority -- the terminal mark's whole provenance.

    Both closure paths write it: retirement (killed) and landing (archived). The four facts are one
    record -- a timestamp with no reason is an unexplained tombstone, and a reason with no edge
    cannot be traced back to the chain step that closed the seat -- so they are chosen together at
    the one place that decides to close it.
    """

    at: str
    reason: str
    edge: str
    by_session: str | None = None


def retire_entry(
    catalog: TerminalCatalogPort,
    host: TerminalHost,
    entry: TerminalCatalogEntry,
    closure: SeatClosure,
) -> TerminalCatalogEntry | None:
    """Kill ``entry``'s tmux session and persist the terminal mark + retirement provenance.

    ``host.terminate`` is idempotent against an already-gone tmux session (the terminate endpoint
    already relies on this). Returns the updated row, or ``None`` if the catalog no longer has it
    (a concurrent retire/terminate raced this one) or ``entry`` unchanged if it was already retired.
    """
    if entry.control_endpoint is not None:
        try:
            stop_control_session(entry)
        except HarnessControlError as exc:
            # Retirement must still reap an orphaned tmux process when its control socket is gone.
            # Persist the failed graceful-stop evidence instead of silently treating the kill as a
            # protocol shutdown.
            entry = replace(
                entry,
                control_raw={
                    **(entry.control_raw or {}),
                    "retireControlStopError": str(exc),
                },
            )
            catalog.upsert(entry)
    host.terminate(entry.id, tmux_name=entry.tmux_name)
    return catalog.mark_retired(
        entry.id,
        at=closure.at,
        by_session=closure.by_session,
        reason=closure.reason,
        edge=closure.edge,
    )
