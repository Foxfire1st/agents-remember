"""The lifecycle state and phase vocabulary.

``observer.lifecycle_state`` builds its state partition from these Literals, and the
reducer reads them from the event logs.
"""

from __future__ import annotations

from typing import Literal

LiveState = Literal["running", "paused", "blocked", "awaiting-developer"]
EndOutcome = Literal["completed", "abandoned"]
TerminalState = EndOutcome
State = Literal[LiveState, TerminalState]
Phase = Literal[
    "request",
    "trust-checkpoint",
    "reframe-research",
    "decide",
    "build",
    "close",
]
