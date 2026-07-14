"""Seat retirement mechanics: shared by manual retire and explicit landed archive cleanup.

Retirement kills the tmux session (best-effort; an already-gone session is not an error), then
persists the catalog's terminal mark + retirement provenance. Transcripts are never touched:
retiring is a catalog-and-tmux operation only.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_client import stop_control_session
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

if TYPE_CHECKING:
    from agents_remember.serving.terminal import TerminalHost


def retire_entry(
    catalog: TerminalCatalog,
    host: TerminalHost,
    entry: TerminalCatalogEntry,
    *,
    at: str,
    by_session: str | None,
    reason: str,
    edge: str,
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
    return catalog.mark_retired(entry.id, at=at, by_session=by_session, reason=reason, edge=edge)
