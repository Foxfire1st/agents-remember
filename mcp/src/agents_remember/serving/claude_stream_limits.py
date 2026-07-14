"""Named stability bounds for one Claude stream-json adapter."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.errors import HarnessControlError


@dataclass(frozen=True)
class ClaudeAdapterLimits:
    startup_timeout_seconds: float = 30.0
    acceptance_timeout_seconds: float = 30.0
    history_limit: int = 256
    event_queue_limit: int = 64

    def __post_init__(self) -> None:
        for name, value in (
            ("startup_timeout_seconds", self.startup_timeout_seconds),
            ("acceptance_timeout_seconds", self.acceptance_timeout_seconds),
            ("history_limit", self.history_limit),
            ("event_queue_limit", self.event_queue_limit),
        ):
            if value <= 0:
                raise HarnessControlError(f"Claude adapter {name} must be positive")
