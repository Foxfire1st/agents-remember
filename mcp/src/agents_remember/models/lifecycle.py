"""Response models for the ``lifecycle_*`` signal tools.

These are AR-owned, operation-bearing responses (strict ``ToolResponse``s, not
the persisted ``observer.Event`` record): each echoes the lifecycle's id and its
state/phase after the signal. ``state``/``phase`` reuse the observer's Literals
so the response contract is as drift-proof as the event envelope.
"""

from __future__ import annotations

from typing import Any

from agents_remember.models.base import ToolResponse
from agents_remember.observer.lifecycle_state import Phase, State


class LifecycleResponse(ToolResponse):
    """Shared shape: the lifecycle id and its state/phase after the signal."""

    lifecycleId: str
    state: State
    phase: Phase


class LifecycleStartResponse(LifecycleResponse):
    """``lifecycle_start``: a freshly minted, running lifecycle."""

    fleeting: bool


class LifecycleBlockResponse(LifecycleResponse):
    """``lifecycle_block``: blocked, with the optional ask echoed back."""

    ask: dict[str, Any] | None = None


class LifecycleResumeResponse(LifecycleResponse):
    """``lifecycle_resume``: the lifecycle is running again."""


class LifecyclePhaseResponse(LifecycleResponse):
    """``lifecycle_phase``: the lifecycle moved along the phase axis."""


class LifecycleEndResponse(LifecycleResponse):
    """``lifecycle_end``: the lifecycle reached a terminal state."""


class SwitchLifecycleResponse(LifecycleResponse):
    """``switch_lifecycle``: the newly adopted lifecycle."""

    fleeting: bool
