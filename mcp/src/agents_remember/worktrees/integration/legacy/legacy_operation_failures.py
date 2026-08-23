"""Bounded public failures for the isolated legacy lifecycle bridge."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
    public_lifecycle_evidence_pair,
)


class LegacyBridgeError(RuntimeError):
    """Bounded legacy refusal preserving the original bytes in place."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object] | None = None,
        observed: Mapping[str, object] | None = None,
        next_action: str = "developer-decision",
    ) -> None:
        self.status = status
        self.detail = detail
        public = public_lifecycle_evidence_pair(expected or {}, observed or {})
        self.expected = public.expected
        self.observed = public.observed
        self.next_action = next_action
        super().__init__(f"{status}: {detail}")


@dataclass(frozen=True)
class LegacyIoFailure:
    """Private exception plus the stable public bridge coordinates it maps to."""

    stage: str
    side: str
    name: str
    error: Exception


def legacy_io_error(
    status: str,
    detail: str,
    *,
    failure: LegacyIoFailure,
    observed: Mapping[str, object] | None = None,
    next_action: str = "developer-decision",
) -> LegacyBridgeError:
    """Translate private bridge I/O detail at the lifecycle-owned boundary."""

    return LegacyBridgeError(
        status,
        detail,
        observed={
            "failure": public_failure_evidence(
                stage=failure.stage,
                side=failure.side,
                name=failure.name,
                error_type=type(failure.error).__name__,
                observed=observed or {"state": "unreadable"},
            )
        },
        next_action=next_action,
    )
