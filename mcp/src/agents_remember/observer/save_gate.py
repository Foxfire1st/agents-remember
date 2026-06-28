"""The save gate: the decision forced when leaving a fleeting lifecycle.

Switching or attaching away from a *fleeting* lifecycle (no worktree, no
artifact) forces a choice (design ``docs/design/observable-lifecycle.md``
§1.2/§1.5): **save** promotes it to persistent so the work is not lost,
**discard** ends it ``abandoned``. This module is the pure decision vocabulary --
the landing-zone scope taxonomy, the ``SaveDecision`` boundary type, and the
``SaveGateRequired`` signal the ambient raises when no decision was supplied. It
carries no I/O or threading, so the slice-3 projection reducer can reuse the
scope rule.

The gate is foundational, not interactive: 2c records the decision from an
explicit input and *blocks* (``SaveGateRequired``) when none is given. The gate
control plane (slice 06) docks the interactive resolution, the durable gate
record, and enforcement onto this same seam. There is deliberately no auto-save
default to unwind later.
"""

from __future__ import annotations

from typing import Literal, cast, get_args

from agents_remember.observer.lifecycle_state import LifecycleError

# Landing zones for saved work without a single tangible repo (design §1.5); the
# numeric prefixes sort both above the per-repo folders in the dashboard hangar.
UNSCOPED_SCOPE = "0_unscoped"
CROSS_REPO_SCOPE = "1_cross-repo"

SaveDecision = Literal["save", "discard"]
SAVE_DECISIONS: tuple[SaveDecision, ...] = get_args(SaveDecision)


class SaveGateRequired(LifecycleError):
    """Leaving a fleeting lifecycle needs an explicit save/discard decision.

    Raised when a switch/attach would abandon unsaved fleeting work and no
    decision was supplied. The caller resolves it by re-issuing the switch with
    ``on_unsaved="save"`` (promote) or ``"discard"`` (abandon); slice 06 supplies
    that answer through the interactive return channel instead.
    """

    def __init__(self, active_id: str) -> None:
        super().__init__(
            f"leaving fleeting lifecycle {active_id} needs a save decision; "
            "pass on_unsaved='save' to promote it or 'discard' to abandon it"
        )
        self.active_id = active_id


def coerce_save_decision(value: str) -> SaveDecision:
    """Validate a raw ``on_unsaved`` string from the tool boundary."""
    if value not in SAVE_DECISIONS:
        raise LifecycleError(
            f"unknown save decision {value!r}; expected one of {', '.join(SAVE_DECISIONS)}"
        )
    return cast(SaveDecision, value)


def compute_scope(repo_id: str | None, *, cross_repo: bool = False) -> str:
    """The scope tag recorded on ``lifecycle.promoted`` (design §1.5).

    A lifecycle bound to one managed repo scopes to that ``repo_id``; saved work
    spanning several repos in one enclosure is ``1_cross-repo``; work with no
    managed-repo binding is ``0_unscoped``. The dashboard (slice 4) groups by
    this tag; 2c only records it.
    """
    if cross_repo:
        return CROSS_REPO_SCOPE
    if repo_id:
        return repo_id
    return UNSCOPED_SCOPE
