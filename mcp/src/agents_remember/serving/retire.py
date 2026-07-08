"""Seat retirement mechanics (260707-HFX-L8): shared by the ``session_retire`` MCP tool and the
integrate/finalize automation hooks so the manual and automated paths retire seats identically --
kill the tmux session (best-effort; a already-gone session is not an error), then persist the
catalog's terminal mark + retirement provenance. Transcripts are never touched: retiring is a
catalog-and-tmux operation only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

if TYPE_CHECKING:
    from agents_remember.serving.terminal import TerminalHost

RETIRABLE_ROLES = frozenset({"worker", "reviewer", "manager", "designer", "strategist"})
"""Every l-01 role that can occupy a retirable seat (the orchestrator is portfolio-level and is
never a retire TARGET through the automation hooks)."""


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
    host.terminate(entry.id, tmux_name=entry.tmux_name)
    return catalog.mark_retired(entry.id, at=at, by_session=by_session, reason=reason, edge=edge)


def retire_seats_for_leaf(
    catalog: TerminalCatalog,
    host: TerminalHost,
    *,
    leaf_key: str,
    roles: frozenset[str],
    reason: str,
    edge: str,
    at: str,
) -> list[TerminalCatalogEntry]:
    """Auto-retire every non-terminated seat bound to ``leaf_key`` whose ``spawn_role`` is in ``roles``.

    The completion-edge automation (worktree_integrate / lifecycle_finalize_task) calls this with
    no acting session -- ``by_session`` is always ``None`` here, the provenance ``reason``/``edge``
    name the automated completion edge instead of an actor.
    """
    retired: list[TerminalCatalogEntry] = []
    for candidate in catalog.list(include_terminated=True):
        if candidate.leaf_key != leaf_key or candidate.status == "terminated":
            continue
        if candidate.spawn_role not in roles:
            continue
        updated = retire_entry(
            catalog, host, candidate, at=at, by_session=None, reason=reason, edge=edge
        )
        if updated is not None:
            retired.append(updated)
    return retired
