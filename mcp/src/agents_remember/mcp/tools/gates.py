"""MCP response adapters for gate application operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.gate_tools import (
    ANY_INBOX_ENTRY,
    BLOCKING_GATE_WAIT,
    DEFAULT_GATE_WAIT,
    SHORT_GATE_WAIT,
    GateRaise,
    GateWait,
    InboxWatch,
    gate_create_tool,
    gate_decide_for_lifecycle_tool,
    gate_decide_tool,
    gate_list_tool,
    gate_response_wait_tool,
    gate_wait_tool,
    lifecycle_gate_tool,
    raise_lifecycle_gate,
    record_gate_decision,
)
from agents_remember.application.structural.gate_tools import (
    StructuralGateRuntime,
    structural_gate_decide_tool,
    structural_gate_list_tool,
    structural_lifecycle_gate_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.application_requests import (
    GateDecisionRequest,
    LifecycleGateRequest,
)
from agents_remember.models.structural.gates import (
    StructuralGateDecisionRequest,
    StructuralLifecycleGateRequest,
)

from .base import _tool_payload


def gate_create_payload(
    config: McpRuntimeConfig,
    *,
    kind: str,
    anchor: Any = None,
    request: Any = None,
) -> dict[str, Any]:
    return _tool_payload(
        "gate_create",
        gate_create_tool(config, kind=kind, anchor=anchor, request=request),
    )


def lifecycle_gate_payload(
    config: McpRuntimeConfig,
    raised: GateRaise,
    *,
    wait: GateWait = BLOCKING_GATE_WAIT,
) -> dict[str, Any]:
    return _tool_payload("lifecycle_gate_internal", lifecycle_gate_tool(config, raised, wait=wait))


def registered_lifecycle_gate_payload(
    config: McpRuntimeConfig,
    request: LifecycleGateRequest,
) -> dict[str, Any]:
    """Complete the registered flat lifecycle-gate request at the response boundary."""
    return _tool_payload(
        "lifecycle_gate_internal",
        raise_lifecycle_gate(config, request),
    )


def structural_lifecycle_gate_payload(
    config: McpRuntimeConfig,
    request: StructuralLifecycleGateRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "lifecycle_gate",
        structural_lifecycle_gate_tool(
            config,
            request,
            StructuralGateRuntime(**overrides),
        ),
    )


def gate_decide_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    verdict: Any,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "gate_decide_internal",
        gate_decide_tool(
            config,
            gate_id=gate_id,
            lifecycle_id=lifecycle_id,
            verdict=verdict,
            evidence_refs=evidence_refs,
        ),
    )


def registered_gate_decide_payload(
    config: McpRuntimeConfig,
    request: GateDecisionRequest,
) -> dict[str, Any]:
    """Complete a registered decision after application-owned verdict composition."""
    return _tool_payload(
        "gate_decide_internal",
        record_gate_decision(config, request),
    )


def structural_gate_decide_payload(
    config: McpRuntimeConfig,
    request: StructuralGateDecisionRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "gate_decide",
        structural_gate_decide_tool(
            config,
            request,
            StructuralGateRuntime(**overrides),
        ),
    )


def gate_decide_for_lifecycle(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str,
    verdict: Any,
    expected_gate_id: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "gate_decide_internal",
        gate_decide_for_lifecycle_tool(
            config,
            lifecycle_id=lifecycle_id,
            verdict=verdict,
            expected_gate_id=expected_gate_id,
            evidence_refs=evidence_refs,
        ),
    )


def gate_wait_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    wait: GateWait = SHORT_GATE_WAIT,
) -> dict[str, Any]:
    return _tool_payload(
        "gate_wait",
        gate_wait_tool(config, gate_id=gate_id, lifecycle_id=lifecycle_id, wait=wait),
    )


def gate_response_wait_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    inbox: InboxWatch = ANY_INBOX_ENTRY,
    wait: GateWait = DEFAULT_GATE_WAIT,
) -> dict[str, Any]:
    return _tool_payload(
        "gate_response_wait",
        gate_response_wait_tool(
            config,
            gate_id=gate_id,
            lifecycle_id=lifecycle_id,
            inbox=inbox,
            wait=wait,
        ),
    )


def gate_list_payload(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
) -> dict[str, Any]:
    return _tool_payload("gate_list_internal", gate_list_tool(config, lifecycle_id=lifecycle_id))


def structural_gate_list_payload(
    config: McpRuntimeConfig,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload("gate_list", structural_gate_list_tool(config, **overrides))
