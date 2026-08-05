"""Gate tools: raise the public lifecycle gate, decide an open one, list them."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.models.application_requests import (
    GateDecisionRequest,
    LifecycleGateRequest,
)

from ..config import McpRuntimeConfig
from ..tools.gates import (
    gate_list_payload,
    registered_gate_decide_payload,
    registered_lifecycle_gate_payload,
)


def register_gate_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def lifecycle_gate(
        kind: str,
        ask: dict[str, Any] | None = None,
        lifecycle_id: str | None = None,
        enclosure: str | None = None,
        repo_id: str | None = None,
        packet: dict[str, Any] | None = None,
        required_decision: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Public lifecycle-gate junction for agents. Creates the durable typed gate,
        blocks the active lifecycle with the developer-facing ask, and waits for the
        developer decision or gate-specific response in one operation. kind is the dashboard junction
        (plan-approval, worktree-intent, closeout-approval, etc.); ask.kind is the
        answer shape (decision, question, conflict). Do not add a separate wait call
        as live gate choreography. wait=false raises without blocking — reserved for
        delegated seam kinds (master-handover-approval under a delegating policy; any
        other kind blocks) and it requires enclosure=<master task name>, the address
        integration enforcement matches the gate by: the call returns the gateId, the
        raiser carries it in the handover packet, and the delegated decider resolves
        it by id via gate_decide(deciding_role=...)."""
        return registered_lifecycle_gate_payload(
            config,
            LifecycleGateRequest(
                kind=kind,
                ask=ask,
                lifecycle_id=lifecycle_id,
                enclosure=enclosure,
                repo_id=repo_id,
                packet=packet,
                required_decision=required_decision,
                evidence_refs=evidence_refs,
                wait=wait,
            ),
        )

    @server.tool()
    def gate_decide(
        gate_id: str,
        decision: str,
        lifecycle_id: str | None = None,
        note: str | None = None,
        deciding_role: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Record a decision on an open gate (decision: approve | reject | request-revision
        | cancel). Append-only -- the decision is a new snapshot, never an overwrite. By
        default the decision is attributed to the model via the cli; with deciding_role it is
        attributed to the active lifecycle via orchestration and checked against the configured
        gate policy."""
        return registered_gate_decide_payload(
            config,
            GateDecisionRequest(
                gate_id=gate_id,
                decision=decision,
                lifecycle_id=lifecycle_id,
                note=note,
                decided_by="" if deciding_role is not None else "model",
                decided_via="orchestration" if deciding_role is not None else "cli",
                deciding_role=deciding_role,
                evidence_refs=evidence_refs,
            ),
        )

    @server.tool()
    def gate_list(lifecycle_id: str | None = None) -> dict[str, Any]:
        """List the current (folded) gates for a lifecycle. With no lifecycle id it
        defaults to the ACTIVE (ambient) lifecycle — poll your own raised gate without
        handling lifecycle ids — and lists the workspace gates only when no lifecycle
        is active. Read-only."""
        return gate_list_payload(config, lifecycle_id=lifecycle_id)
