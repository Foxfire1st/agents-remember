"""Plane-resolved agent dispatch and structural seat-management tools."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    RetireChildRequest,
    StructuralRole,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

from ..tools.structural_agent import dispatch_agent_payload, retire_child_payload


def register_session_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register operations that never ask a model for a runtime occupant id."""

    @server.tool()
    def dispatch_agent(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        brief: str,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Create and durably brief one child seat on a canonical task document plus role.

        The caller kind decides the mode. A plane-hosted seat (this process carries
        AR_HOSTED_SESSION_ID) uses the structural path: the caller is proven from
        plane-injected identity, direct-child scope is authorized, and the target is the
        canonical sprint/master/leaf document plus role. An ambient caller (no
        AR_HOSTED_SESSION_ID -- a launcher chat, not a hosted seat) spawns in ambient mode:
        the occupant is created on the canonical task document with the pinned dispatch
        brief and the same rollback when the brief cannot persist; there is no parent seat,
        so seat-authority and child-scope checks do not apply, but the role is still
        validated against the document's altitude. Harness selection, runtime identity,
        readiness, exact initial brief pinning, delivery correlation, and retry remain
        private control-plane work in both modes. A queued brief is already durable and
        needs no model-held retry id. A present but stale, invalid, mismatched, unbound, or
        unauthorized plane identity remains a plane refusal and never falls back to ambient.
        """
        return dispatch_agent_payload(
            config,
            DispatchAgentRequest(
                task_document_ref=task_document_ref,
                role=role,
                brief=brief,
                label=label,
            ),
        )
    @server.tool()
    def retire_child(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        reason: str = "delegated seat retired",
        agent_id: str | None = None,
        interrupt_turn: bool = False,
    ) -> dict[str, Any]:
        """Retire the receipt-tracked agent in one authorized direct child seat.

        MIK-R100 rule 8: the agent that started the seat retires the agent in it
        without giving it a turn, and the launch record names the caller and the
        reason. An agent in a turn is not retired unless interrupt_turn says the
        turn may be interrupted. Any other role agent is refused with the starter
        named; an unknown or ambiguous agent id is refused by name and nothing
        is archived; a repeated call for an already retired agent changes nothing
        and says so.
        """
        return retire_child_payload(
            config,
            RetireChildRequest(
                task_document_ref=task_document_ref,
                role=role,
                reason=reason,
                agent_id=agent_id,
                interrupt_turn=interrupt_turn,
            ),
        )
