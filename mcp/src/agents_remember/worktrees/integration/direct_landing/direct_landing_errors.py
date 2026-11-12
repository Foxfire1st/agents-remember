"""Typed refusals shared by direct-landing admission and recovery."""

from __future__ import annotations

from collections.abc import Mapping


class DirectLandingError(ValueError):
    """The direct landing request is malformed or violates accepted facts."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object] | None = None,
        observed: Mapping[str, object] | None = None,
        next_action: str | None = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.expected = dict(expected or {})
        self.observed = dict(observed or {})
        self.next_action = next_action
        super().__init__(f"{status}: {detail}")


class DirectLandingPublicationRefused(DirectLandingError):
    """The landing was refused before anything was published; its generation is cancelled.

    Raised for a refused publication (the branch moved, ``HEAD`` switched, an unfinished merge),
    for a refusal by direct landing's stricter check (the memory checkout changed after it was
    judged) and for any other failure that provably moved no ref. Unlike other refusals it leaves
    nothing in flight: the journal generation is cancelled, the leaf's history file and the ignore
    rule are back as they were, and the index is as it was unless it holds someone else's staged
    edit, so the same request can simply be made again once the cause is gone.
    """
