"""Gate tools: raise the public lifecycle gate, decide an open one, list them."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.gates import (
    GateKind,
    StructuralGateDecisionRequest,
    StructuralLifecycleGateRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

from ..tools.gates import (
    structural_gate_decide_payload,
    structural_gate_list_payload,
    structural_lifecycle_gate_payload,
)


def register_gate_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def lifecycle_gate(
        kind: GateKind,
        ask: dict[str, Any] | None = None,
        *,
        packet: dict[str, Any] | None = None,
        required_decision: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Raise a gate on this hosted seat's canonical task document.

        The control plane derives enclosure, repository, lifecycle, and private gate identity from
        ambient state. ``wait=false`` remains policy-limited to delegated seam kinds. The response
        contains only task document, role, kind, and state; no gate or lifecycle id is exposed.
        """
        return structural_lifecycle_gate_payload(
            config,
            StructuralLifecycleGateRequest(
                kind=kind,
                ask=ask,
                packet=packet,
                required_decision=required_decision,
                evidence_refs=evidence_refs,
                wait=wait,
            ),
        )

    @server.tool()
    def gate_decide(
        task_document_ref: TaskDocumentRef,
        kind: GateKind,
        decision: str,
        note: str | None = None,
        *,
        evidence_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Decide the one open gate matching an authorized child document and kind.

        The caller's ambient role supplies attribution and policy authority. Zero or multiple open
        matches fail closed; models never receive or submit gate/lifecycle ids.
        """
        return structural_gate_decide_payload(
            config,
            StructuralGateDecisionRequest(
                task_document_ref=task_document_ref,
                kind=kind,
                decision=decision,
                note=note,
                evidence_refs=evidence_refs,
            ),
        )

    @server.tool()
    def gate_list() -> dict[str, Any]:
        """List folded gates in this seat's canonical document scope without private ids."""
        return structural_gate_list_payload(config)
