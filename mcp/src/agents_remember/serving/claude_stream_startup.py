"""Claude stream-json startup negotiation through control initialize and system/init."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.serving.claude_stream_capabilities import parse_list_models_response
from agents_remember.serving.claude_stream_protocol import (
    ClaudeControlInitialization,
    ClaudeSystemInitialization,
    bootstrap_message,
    initialization_request,
    is_successful_bootstrap_result,
    list_models_request,
    parse_control_initialization,
    parse_system_initialization,
)
from agents_remember.serving.claude_stream_transport import ClaudeStreamTransport
from agents_remember.serving.harness_capabilities import CapabilitySnapshot

INITIALIZE_REQUEST_ID = "ar-claude-initialize"
LIST_MODELS_REQUEST_ID = "ar-claude-list-models"


@dataclass
class _StartupCollector:
    cwd: Path
    control: ClaudeControlInitialization | None = None
    system: ClaudeSystemInitialization | None = None
    bootstrap_complete: bool = False

    @property
    def complete(self) -> bool:
        return self.control is not None and self.system is not None and self.bootstrap_complete

    def accept(self, frame: dict[str, object]) -> None:
        parsed_control = parse_control_initialization(frame, INITIALIZE_REQUEST_ID)
        if parsed_control is not None:
            self.control = parsed_control
            return
        parsed_system = parse_system_initialization(frame, expected_cwd=self.cwd)
        if parsed_system is not None:
            self.system = parsed_system
            return
        if self.system is not None:
            self.bootstrap_complete = self.bootstrap_complete or is_successful_bootstrap_result(
                frame, self.system.session_id
            )

    def result(self) -> tuple[ClaudeControlInitialization, ClaudeSystemInitialization]:
        if self.control is None or self.system is None or not self.bootstrap_complete:
            raise HarnessControlError("Claude protocol initialization was incomplete")
        return self.control, self.system


async def negotiate_claude_startup(
    transport: ClaudeStreamTransport,
    *,
    cwd: Path,
    timeout_seconds: float,
) -> tuple[ClaudeControlInitialization, ClaudeSystemInitialization]:
    """Write the documented handshake frames and wait for explicit structured readiness."""

    await transport.write_frame(initialization_request(INITIALIZE_REQUEST_ID))
    await transport.write_frame(bootstrap_message())
    collector = _StartupCollector(cwd)
    try:
        async with asyncio.timeout(timeout_seconds):
            while not collector.complete:
                frame = await transport.read_frame()
                if frame is None:
                    raise HarnessControlError(
                        "Claude Code disconnected before protocol initialization completed"
                    )
                collector.accept(frame)
    except TimeoutError as exc:
        raise HarnessControlError("Claude protocol initialization timed out") from exc
    return collector.result()


async def negotiate_claude_catalog(
    transport: ClaudeStreamTransport,
    *,
    current_model: str,
    timeout_seconds: float,
) -> CapabilitySnapshot:
    """Fetch the dynamic catalog before the long-running state reader owns stdout."""

    await transport.write_frame(list_models_request(LIST_MODELS_REQUEST_ID))
    try:
        async with asyncio.timeout(timeout_seconds):
            frame = await transport.read_frame()
            if frame is None:
                raise HarnessControlError("Claude Code disconnected before list_models completed")
            capabilities = parse_list_models_response(
                frame,
                LIST_MODELS_REQUEST_ID,
                current_model=current_model,
            )
            if capabilities is None:
                raise HarnessControlError(
                    "Claude Code emitted an unexpected frame during list_models"
                )
            return capabilities
    except TimeoutError as exc:
        raise HarnessControlError("Claude list_models request timed out") from exc
