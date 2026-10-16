"""Provider admission follows durable worktree facts, independent of sessions."""

from __future__ import annotations

from pathlib import Path

from agents_remember.observer.projection import EnclosureNode

INACTIVE_CLEANUP_STATES = frozenset({"completed", "abandoned", "reopened"})


def admitted_worktree_groups(enclosures: list[EnclosureNode]) -> set[str]:
    """Provider stacks remain operational before closeout/integration is complete."""
    return {
        Path(enclosure.worktreeGroup).name
        for enclosure in enclosures
        if enclosure.worktreeGroup
        and enclosure.cleanup not in INACTIVE_CLEANUP_STATES
        and enclosure.closeoutStatus != "completed"
        and enclosure.integrationStatus != "completed"
    }


def active_enclosure_worktree_groups(enclosures: list[EnclosureNode]) -> set[str]:
    """Contract cleanup decides which worktrees remain active."""
    return {
        Path(enclosure.worktreeGroup).name
        for enclosure in enclosures
        if enclosure.worktreeGroup and enclosure.cleanup not in INACTIVE_CLEANUP_STATES
    }
