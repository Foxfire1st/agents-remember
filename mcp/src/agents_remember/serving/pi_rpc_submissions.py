"""The Pi adapter's bounded prompt-correlation ledger."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Literal

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_models import PromptRequest

SubmissionKnowledge = Literal["pending", "accepted", "rejected", "unknown"]


@dataclass(frozen=True)
class PiSubmissionEvidence:
    """What one submitted prompt is known to be, and the entry cursor it was sent after.

    ``cursor_before`` is the reconciliation input: an ambiguous send is resolved by reading the
    durable entries after it, so the cursor has to be captured before the write, not after.
    """

    request: PromptRequest
    cursor_before: str | None
    state: SubmissionKnowledge


class PiSubmissionLedger:
    """Bounded request-id ledger for Pi prompts, from which only a settled row may be evicted.

    An ``unknown`` row is the only evidence that a disconnected write may have landed, so a full
    ledger holding nothing settled refuses the next submission rather than forgetting one.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._rows: OrderedDict[str, PiSubmissionEvidence] = OrderedDict()

    def knows(self, request_id: str) -> bool:
        return request_id in self._rows

    def get(self, request_id: str) -> PiSubmissionEvidence | None:
        return self._rows.get(request_id)

    def remember(self, request_id: str, evidence: PiSubmissionEvidence) -> None:
        if len(self._rows) >= self._limit:
            evictable = next(
                (
                    key
                    for key, value in self._rows.items()
                    if value.state in {"accepted", "rejected"}
                ),
                None,
            )
            if evictable is None:
                raise HarnessControlError("Pi reconciliation ledger is full of ambiguous sends")
            self._rows.pop(evictable)
        self._rows[request_id] = evidence

    def mark(self, request_id: str, state: SubmissionKnowledge) -> None:
        self._rows[request_id] = replace(self._rows[request_id], state=state)

    def discard(self, request_id: str) -> None:
        self._rows.pop(request_id, None)
