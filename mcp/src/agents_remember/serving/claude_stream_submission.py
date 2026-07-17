"""Internal submission record lifecycle for the Claude stream-json adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agents_remember.serving.harness_control_models import (
    AcceptanceState,
    ControlOperationRef,
    PromptRequest,
    SubmissionReceipt,
    TerminalResult,
)


@dataclass
class ClaudeSubmission:
    request: PromptRequest
    correlation_id: str
    wire_text: str
    replay_text: str
    operation: ControlOperationRef
    acceptance_future: asyncio.Future[SubmissionReceipt]
    terminal_future: asyncio.Future[TerminalResult]
    acceptance: AcceptanceState = "unknown"
    accepted_at: str | None = None
    completed: bool = False
    abandoned: bool = False


def consume_future_exception(future: asyncio.Future[Any]) -> None:
    """Retrieve a late protocol error after its bounded waiter returned."""

    if not future.cancelled():
        future.exception()
