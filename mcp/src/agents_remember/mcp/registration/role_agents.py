"""Role start and role messaging: the two tools a role agent's instructions name."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.role_agents import role_message_payload, role_start_payload
from agents_remember.models.role_agents import (
    DEFAULT_WAIT_SECONDS,
    MAX_WAIT_SECONDS,
    RoleMessageCall,
    RoleStartCall,
)
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.task_document_ref import TaskDocumentRef


def register_role_agent_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register ``role_start`` and ``role_message``, in that order."""

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
    ) -> dict[str, object]:
        """Start one role agent for one canonical AR selection, with the caller as its parent.

        Only a role agent that AR launched can call this: the caller is the agent this tool
        server was started for. An architect may start every role except architect; an
        orchestrator may start manager, worker, reviewer, curator and investigator, and a manager
        worker, reviewer, curator and investigator, each only under its own sprint or master;
        worker, reviewer, curator and investigator may start none.

        An investigator accepts no task reference, a sprint, or a sprint and master; never a leaf.
        Give the other roles their required references: sprint for orchestrator, sprint and
        master for manager, sprint, master and leaf for worker, reviewer and curator. request_id is a UUID the caller chooses: the same id with
        the same selection reconciles the same agent and never creates a second; a new id is a
        new start. A selected investigator admits at most eight open request-addressed executions;
        the other task-bound roles refuse a new start while an execution is open. agent, model and
        effort override the role default and are checked against the host's catalog at the time
        of the call.

        One call resolves or creates the leaf enclosure, compiles the role's first message,
        records the launch and creates the agent in Paseo. It returns agentId, reportPath,
        handoverArtifactPath and status: running (the agent exists), rejected (the launch is
        closed; no usable agent exists) or unknown (no usable answer; repeat the same
        request_id). A call that is not carried out answers status refused with the reason in
        refusal and detail.

        Starts run one at a time. A call waits up to 60 seconds for another start of this tool
        server to end; after that it is refused as launch-refused and nextAction says to call
        again with the same arguments. Only the agent that started an execution repeats its
        request_id. A repeat whose agent is archived or gone is refused as launch-refused with
        the agentId: start again with a new request_id.
        """
        call = RoleStartCall(
            role=role,
            request_id=request_id,
            sprint_document_ref=sprint_document_ref,
            master_document_ref=master_document_ref,
            task_document_ref=task_document_ref,
            agent=agent,
            model=model,
            effort=effort,
        )
        return await asyncio.to_thread(role_start_payload, config, call)

    @server.tool()
    async def role_message(
        text: Annotated[str, Field(min_length=1)],
        agent_id: str | None = None,
        role: LauncherRole | None = None,
        *,
        sprint_document_ref: TaskDocumentRef | None = None,
        master_document_ref: TaskDocumentRef | None = None,
        task_document_ref: TaskDocumentRef | None = None,
        wait: bool = False,
        timeout_seconds: Annotated[int, Field(ge=1, le=MAX_WAIT_SECONDS)] = DEFAULT_WAIT_SECONDS,
    ) -> dict[str, object]:
        """Send one text message to one role agent; with wait, return its reply.

        Only a role agent that AR launched can call this. Name the recipient either by agent_id
        (the id a role start returned, or the id in the first line of a message you received), or
        by role plus the task references its class requires. A role that matches several live
        agents is refused with their ids in candidateAgentIds; the tool never picks one.

        The recipient reads the text behind one line that names the sender:
        "From <role> · <task id or Projects> · agent <sender agent id>". A recipient that is
        mid-turn is handed the message in its running turn; that turn is never cancelled, and
        when the host cannot hand a message to a running turn the call is refused as
        recipient-busy. Depending on its harness the recipient takes such a message up in the
        running turn or in a turn of its own right after it. A recipient that waits for a
        permission decision is refused as recipient-busy, because a message would answer the
        permission with a denial: the developer answers it in the recipient's chat, then send
        again. A recipient whose start has not finished is refused as recipient-busy as well.
        A recipient whose session is closed is resumed first; an archived or missing recipient
        is refused and stays as it is. A new turn on a completed task-bound Investigator
        needs one of that selection's eight open slots before delivery or resume; a full selection
        refuses with the eight request IDs and the close/archive remedy. An address by role
        never means the caller itself.

        Without wait the call returns status accepted once the host accepted the message. With
        wait it returns when the turn that consumed the message ends: turn-finished with its
        final text in text, turn-failed or turn-cancelled; or permission-pending when the
        recipient waits for a permission decision; or timeout after timeout_seconds (default
        300, at most 1800), with the message still delivered and the reply to be read later.
        When the host cannot say which turn consumed the message, the call returns accepted
        without a text and detail says that the reply must be read later. A reply to a message
        delivered during a turn can be the running turn's text; detail says so then.
        Refusals: recipient-busy, recipient-not-found, recipient-archived, recipient-ambiguous,
        recipient-cannot-be-resumed, investigator-capacity, scope-check-failed, caller-has-no-binding,
        no-paseo-runtime-configured, host-unreachable. A finished turn is a fact about the
        recipient's turn, never AR acceptance of a requirement.
        """
        call = RoleMessageCall(
            text=text,
            agent_id=agent_id,
            role=role,
            sprint_document_ref=sprint_document_ref,
            master_document_ref=master_document_ref,
            task_document_ref=task_document_ref,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )
        return await asyncio.to_thread(role_message_payload, config, call)


__all__ = ["register_role_agent_tools"]
