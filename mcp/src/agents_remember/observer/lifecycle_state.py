"""Lifecycle state vocabulary and the session-held lifecycle record.

The observable lifecycle's states and phases (design ``docs/design/
observable-lifecycle.md`` §1.2-1.4), plus the typed errors a signal raises
against an incompatible state. This module is pure vocabulary + data with no
I/O, so the projection reducer (a later slice) can import the state types
without pulling in the ambient singleton's threading and emission machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast, get_args

from agents_remember.errors import AgentsRememberError

# One state at a time; ``completed``/``abandoned`` are terminal. ``paused`` is
# system-owned -- there is no pause signal: the projection infers it from stale
# heartbeats or a recorded switch-away, the model never declares it.
State = Literal["running", "paused", "blocked", "completed", "abandoned"]

# Orthogonal to state (a lifecycle can be ``paused`` while in phase ``build``).
# The enum is the session-lifecycle skill's heading vocabulary, hyphenated.
Phase = Literal[
    "request",
    "trust-checkpoint",
    "reframe-research",
    "decide",
    "build",
    "close",
]

TERMINAL_STATES: frozenset[str] = frozenset({"completed", "abandoned"})
INITIAL_PHASE: Phase = "request"
PHASES: tuple[Phase, ...] = get_args(Phase)


class LifecycleError(AgentsRememberError):
    """A lifecycle signal was issued against an incompatible state."""


class GuardedStartError(LifecycleError):
    """``lifecycle_start`` was issued while a lifecycle is already active.

    Guarded start is how the model is kept from ever holding two lifecycles or
    handling an id: the reminder names the active lifecycle so the model ends or
    switches it rather than stacking a new one.
    """

    def __init__(self, active_id: str) -> None:
        super().__init__(
            f"a lifecycle is already active ({active_id}); "
            "end or switch it before starting another"
        )
        self.active_id = active_id


def coerce_phase(value: str) -> Phase:
    """Validate a raw phase string from the tool boundary into a ``Phase``."""
    if value not in PHASES:
        raise LifecycleError(f"unknown phase {value!r}; expected one of {', '.join(PHASES)}")
    return cast(Phase, value)


@dataclass(frozen=True)
class LifecycleState:
    """The single lifecycle a session currently holds (zero or one).

    Frozen so each transition produces a new value -- no shared-mutable state
    surprises between the request thread and the heartbeat thread, and every
    prior state stays auditable in the event log rather than being overwritten.
    """

    id: str
    state: State
    phase: Phase
    fleeting: bool
    started_at: str

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
