"""Role start, set start, and role messaging: the tools a role agent's instructions name."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StrictBool, StrictStr

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.role_agents import (
    role_message_payload,
    role_start_payload,
    role_start_set_payload,
)
from agents_remember.models.role_agents import (
    DEFAULT_WAIT_SECONDS,
    MAX_WAIT_SECONDS,
    RoleMessageCall,
    RoleStartCall,
    RoleStartSetCall,
    SeatLaunchOverride,
)
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.task_document_ref import TaskDocumentRef

_SEAT_OVERRIDE_KEYS = frozenset({"agent", "model", "effort", "featureValues", "feature_values"})


def seat_override(role: str, mapping: dict[str, Any] | None) -> SeatLaunchOverride:
    """One seat's launch choices from a tool argument; unknown keys are refused by name."""

    if not mapping:
        return SeatLaunchOverride()
    unknown = sorted(set(mapping) - _SEAT_OVERRIDE_KEYS)
    if unknown:
        raise ValueError(
            f"The {role} seat override names unknown choices: {', '.join(unknown)}. "
            "A seat takes agent, model, effort and featureValues."
        )
    features = mapping.get("featureValues", mapping.get("feature_values"))
    if features is not None and (
        not isinstance(features, dict)
        or not all(
            isinstance(key, str) and isinstance(value, (bool, str))
            for key, value in features.items()
        )
    ):
        raise ValueError(
            f"The {role} seat override featureValues must map option ids to booleans or strings."
        )
    for key in ("agent", "model", "effort"):
        value = mapping.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"The {role} seat override {key!r} must be a nonempty string.")
    return SeatLaunchOverride(
        agent=mapping.get("agent"),
        model=mapping.get("model"),
        effort=mapping.get("effort"),
        feature_values=dict(features) if features else {},
    )


def register_role_agent_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register ``role_start``, ``role_start_set`` and ``role_message``, in that order."""

    @server.tool()
    async def role_start(
        role: LauncherRole,
        request_id: uuid.UUID,
        sprint_document_ref: TaskDocumentRef | None = None,
        master_document_ref: TaskDocumentRef | None = None,
        task_document_ref: TaskDocumentRef | None = None,
        *,
        agent: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        feature_values: dict[str, StrictBool | StrictStr] | None = None,
        replaces_agent_id: str | None = None,
        replaces_interrupt_turn: bool = False,
    ) -> dict[str, object]:
        """Start one role agent for one canonical selection with the caller as parent, starting in Paseo. Only a role agent that AR launched can call this. A request id reconciles the same agent, never a second. Only the agent that started an execution repeats its request_id. Answers ok, the agent id and the handover location; rejected (the launch is closed; no usable agent exists). Starts run one at a time. A call waits up to 60 seconds for another start to end, then is refused; nextAction says to call again with the same arguments. Reference: docs/reference/mcp-tools.md."""
        call = RoleStartCall(
            role=role,
            request_id=request_id,
            sprint_document_ref=sprint_document_ref,
            master_document_ref=master_document_ref,
            task_document_ref=task_document_ref,
            agent=agent,
            model=model,
            effort=effort,
            feature_values=feature_values or {},
            replaces_agent_id=replaces_agent_id,
            replaces_interrupt_turn=replaces_interrupt_turn,
        )
        return await asyncio.to_thread(role_start_payload, config, call)

    @server.tool()
    async def role_start_set(
        request_id: uuid.UUID,
        sprint_document_ref: TaskDocumentRef | None = None,
        master_document_ref: TaskDocumentRef | None = None,
        task_document_ref: TaskDocumentRef | None = None,
        *,
        reviewer_message: str | None = None,
        worker: dict[str, Any] | None = None,
        reviewer: dict[str, Any] | None = None,
        curator: dict[str, Any] | None = None,
        replaces: dict[str, str] | None = None,
        replaces_interrupt_turn: bool = False,
    ) -> dict[str, object]:
        """Start a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1). Only a role agent that AR launched can call this. Only the leaf's owning Manager may call it. Pass selection, request_id and per-seat launch choices. Worker opens; Reviewer and Curator are held unless reviewer_message opens the Reviewer. A repeat with the same request id reconciles the same three agents: it creates no fourth and opens no held seat. More: docs/tool-details.md."""
        try:
            call = RoleStartSetCall(
                request_id=request_id,
                sprint_document_ref=sprint_document_ref,
                master_document_ref=master_document_ref,
                task_document_ref=task_document_ref,
                reviewer_message=reviewer_message,
                worker=seat_override("worker", worker),
                reviewer=seat_override("reviewer", reviewer),
                curator=seat_override("curator", curator),
                replaces=replaces or {},
                replaces_interrupt_turn=replaces_interrupt_turn,
            )
        except ValueError as error:
            return {
                "ok": False,
                "operation": "role_start_set",
                "status": "refused",
                "refusal": "launch-refused",
                "detail": str(error),
                "nextAction": "Fix the seat override named in detail and call again.",
                "requestId": str(request_id),
            }
        return await asyncio.to_thread(role_start_set_payload, config, call)

    @server.tool()
    async def role_message(
        text: Annotated[str, Field(min_length=1)],
        agent_id: str | None = None,
        role: LauncherRole | None = None,
        *,
        sprint_document_ref: TaskDocumentRef | None = None,
        master_document_ref: TaskDocumentRef | None = None,
        task_document_ref: TaskDocumentRef | None = None,
        body: str | None = None,
        attach: list[str] | None = None,
        wait: bool = False,
        timeout_seconds: Annotated[int, Field(ge=1, le=MAX_WAIT_SECONDS)] = DEFAULT_WAIT_SECONDS,
        withdraw_held_message_id: str | None = None,
    ) -> dict[str, object]:
        """Send one text message to one role agent. Name the recipient by agent_id or by role plus task references. Only the host request is promised: without wait the call answers once the host accepted the message; with wait it answers when the consuming turn ends (finished, failed, cancelled, permission, timeout). A mid-turn recipient gets the message in its turn, which the host may cancel; a waiting, unstarted or unwritable recipient is answered `held` with reason, permission, counts and id. Only a full hold store (twenty) is refused as `recipient-busy`. Reference: docs/reference/mcp-tools.md."""
        call = RoleMessageCall(
            text=text,
            agent_id=agent_id,
            role=role,
            sprint_document_ref=sprint_document_ref,
            master_document_ref=master_document_ref,
            task_document_ref=task_document_ref,
            body=body,
            attach=attach,
            wait=wait,
            timeout_seconds=timeout_seconds,
            withdraw_held_message_id=withdraw_held_message_id,
        )
        return await asyncio.to_thread(role_message_payload, config, call)


__all__ = ["register_role_agent_tools"]
