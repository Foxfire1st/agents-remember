"""Internal submission record lifecycle for the Claude stream-json adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents_remember.serving.harness_control_models import (
    AcceptanceState,
    PromptRequest,
    SubmissionReceipt,
)


@dataclass
class ClaudeSubmission:
    request: PromptRequest
    correlation_id: str
    wire_text: str
    queued: bool
    acceptance_future: asyncio.Future[SubmissionReceipt]
    acceptance: AcceptanceState = "unknown"
    accepted_at: str | None = None
    completed: bool = False


def consume_future_exception(future: asyncio.Future[SubmissionReceipt]) -> None:
    """Retrieve a late acknowledgement error after its bounded waiter returned."""

    if not future.cancelled():
        future.exception()
