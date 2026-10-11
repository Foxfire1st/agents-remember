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
        of the call. feature_values supplies speed features (service_tier or fast_mode), using the
        host's exact option IDs/booleans. A start call cannot state a mode or a permission-bearing
        feature; configure those in the user's coordination settings or host profile.

        One call resolves or creates the leaf enclosure, compiles the role's first message,
        records the launch and creates the agent in Paseo. It returns agentId, reportPath,
        handoverArtifactPath and status: running (the agent exists), rejected (the launch is
        closed; no usable agent exists) or unknown (no usable answer; repeat the same
        request_id). A call that is not carried out answers status refused with the reason in
        refusal and detail.

        A leaf seat is held by its agent while that agent is not archived (MIK-R100 rule 9):
        a new request id for an occupied seat is refused naming the holder, and a start with
        the holder's own request id reconciles it. A start that says it replaces the holder
        in replaces_agent_id retires the holder first, in the same call; only the agent that
        started the seat, or the launcher, may replace it, and the successor's handover names
        the earlier report paths. A holder in a turn is not replaced without
        replaces_interrupt_turn saying its turn may be interrupted, as in retirement.

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
        """Start a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1).

        Only a role agent that AR launched can call this. Only the leaf's owning Manager can call this: any other role is refused
        before anything is prepared. Each seat is then checked as a start of its
        role is. The three have the calling Manager as their parent and the same
        leaf as their selection, and each takes its agent, model, effort and
        feature values as any start of its role does: pass them
        per seat in worker, reviewer and curator (agent, model, effort, featureValues).

        The Worker opens at once. The Reviewer and the Curator are held: each exists on
        the host, is reached by its role and the leaf's task reference, and has taken no
        turn, with its handover written when the seat is prepared. reviewer_message is
        the Reviewer's first work: carried, the Reviewer opens at once with its handover
        and that message. replaces names, per seat role, the holder a replacing start
        retires first, by the rule of role_start; replaces_interrupt_turn is the word,
        for the whole set, that a replaced holder's turn may be interrupted.

        The answer names the three agent ids and says for each whether it has taken a
        turn. A repeat with the same request id reconciles the same three agents: it
        creates no fourth and opens no held seat. If one of the three cannot be created,
        the call says which, leaves the others as they are, and a repeat completes the
        set.
        """
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
        """Send one text message to one role agent; with wait, return its reply.

        Only a role agent that AR launched can call this. Name the recipient either by agent_id
        (the id a role start returned, or the id in the first line of a message you received), or
        by role plus the task references its class requires. A role that matches several live
        agents is refused with their ids in candidateAgentIds; the tool never picks one. On a
        leaf's seat two live agents refuse the same way: one seat holds one agent.

        The text is the wake-up: at most the limit the settings name (1,000 where they name
        none). A longer text is refused as message-too-long with the count and the limit; put
        the full text in body (any length) or attach its files with attach instead. body goes
        into the message file under the message and nowhere else. attach takes the paths of
        files that exist: each is written into the message file with its SHA-256, copied into
        the folder first where it lies outside the task's record and the leaf workspaces. A
        verdict or report that is a file already is attached, not repeated in body.

        The tool writes one file per message before handing it to the host, in the folder of
        the narrowest task sender and recipient share (the leaf, else the master, else the
        sprint; beside the start briefs of the sender's class for agents without a shared
        task). The name carries the time in UTC, the sender and the recipient. The recipient
        reads the sender line and the message, and where the message has a body or an
        attachment, one last line with the path of the message file and its SHA-256. A
        delivery the host refuses keeps its file under a name ending in .undelivered.md, and
        the refusal names the file.

        The recipient reads the text behind one line that names the sender:
        "From <role> · <task id or Projects> · agent <sender agent id>". A message to a held
        seat delivers the seat's handover and the message in one turn, and the answer says
        that the seat was opened; a held seat is refused neither as busy nor as unfinished.

        Every message the host cannot take now is held by the product instead of refused:
        a recipient whose start has not finished, that waits for a permission decision, or
        that is in a turn the host cannot write into. The answer says that the message is
        held and why, and names the permission as the host names it, or "not supplied"; it
        does not say accepted. When the recipient can be written to, the product hands over
        everything it holds for that recipient in one delivery, in the order taken. At most
        twenty held messages wait for one recipient; a message beyond that is refused as
        busy and none is dropped. The answer names how many messages of the sender wait for
        the recipient, and a sender withdraws an earlier one with withdraw_held_message_id.
        A recipient whose session is closed is resumed first; an archived or missing recipient
        is refused and stays as it is. Where an archived recipient named by id has a live
        successor on the same selection, the refusal names that agent: send the message again
        to it. A new turn on a completed task-bound Investigator
        needs one of that selection's eight open slots before delivery or resume; a full selection
        refuses with the eight request IDs and the close/archive remedy. An address by role
        never means the caller itself. A return goes to the parent's role with its task
        references, never to an id read from an earlier message.

        Without wait the call returns status accepted once the host accepted the message, or
        status held once the product held it. With wait it returns when the turn that consumed
        the message ends: turn-finished with its final text in text (cut at the limit, the
        whole reply filed beside the message and named by the answer), turn-failed or
        turn-cancelled; or permission-pending when the recipient waits for a permission
        decision; or timeout after timeout_seconds (default 300, at most 1800), with the
        message still delivered and the reply to be read later. A wait on a held message at
        a permission answers permission-pending as well. When the host cannot say which turn
        consumed the message, the call returns accepted
        without a text and detail says that the reply must be read later. A reply to a message
        delivered during a turn can be that running turn's text; detail says so then.
        Refusals: recipient-busy, recipient-not-found, recipient-archived, recipient-ambiguous,
        recipient-cannot-be-resumed, investigator-capacity, scope-check-failed, caller-has-no-binding,
        no-paseo-runtime-configured, host-unreachable, message-too-long, message-file-failed,
        settings-invalid. A finished turn is a fact about the
        recipient's turn, never AR acceptance of a requirement.
        """
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
