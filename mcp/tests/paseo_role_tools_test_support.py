"""The fixture of both role-tool case modules (PNT-R06), in one place.

``test_paseo_role_tools.py`` holds the start rules, the other starters, the reused request
ids and the registered tools; ``test_paseo_role_tools_start.py`` holds the parentless chat
starts and the role-start cases. Both import this module, so the fake runtime's wiring, the
role tables and the request helpers have one home.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from agents_remember.application.agent_binding import (
    AgentBinding,
)
from agents_remember.cli import (
    paseo_role_caller,
    paseo_role_recipients,
    paseo_role_tools,
    paseo_role_wait,
    role_held_messages,
    role_retire,
)
from agents_remember.cli.paseo_role_tools import (
    send_role_message,
    start_role,
)
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleStartCall,
)
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentRefError
from test_paseo_launch import (
    LEAF_REF,
    MASTER_REF,
    ROLE_REFS,
    SERVER_ID,
    SPRINT_REF,
)
from test_paseo_status import StatusTestCase

ROLES = (
    "architect",
    "investigator",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
)
OTHER_SPRINT = TaskDocumentRef(repository="agents-remember", path="other-sprint/task.json")
OTHER_MASTER = TaskDocumentRef(repository="agents-remember", path="other-master/task.json")
OTHER_LEAF = TaskDocumentRef(repository="agents-remember", path="master/02_leaf.json")
ARCHIVED_AT = "2026-10-02T02:00:00.000Z"
REFS = {
    "sprintDocumentRef": "sprint_ref",
    "masterDocumentRef": "master_ref",
    "taskDocumentRef": "task_ref",
}
CALL_REFS = {
    "sprintDocumentRef": "sprint_document_ref",
    "masterDocumentRef": "master_document_ref",
    "taskDocumentRef": "task_document_ref",
}
TURN = "turn-7"


def binding(role: str, agent_id: str | None = None, **refs: TaskDocumentRef) -> AgentBinding:
    """The binding a launch gives the tool server of a role agent."""

    selected = {REFS[key]: ref for key, ref in ROLE_REFS[role].items()}
    return AgentBinding(
        agent_id=agent_id or str(uuid.uuid4()),
        role=role,
        request_id=str(uuid.uuid4()),
        report_path="/reports/caller.md",
        **cast(Any, {**selected, **refs}),
    )


def selection_of(role: str) -> dict[str, Any]:
    """The task references of a role's class, as the keyword arguments of a tool call."""

    return {CALL_REFS[key]: ref for key, ref in ROLE_REFS[role].items()}


class RoleToolsTestCase(StatusTestCase):
    """Both tools against the fake runtime, which here also takes messages and waits."""

    def setUp(self) -> None:
        super().setUp()
        self.replace(paseo_role_tools, "bridge_call", self.runtime)
        self.replace(paseo_role_wait, "bridge_call", self.runtime)
        # The held-message store and native retirement call the bridge from their own
        # modules (MIK-R100 rules 4/8): the fake runtime answers there too.
        self.replace(role_held_messages, "bridge_call", self.runtime)
        self.replace(role_retire, "bridge_call", self.runtime)
        self.replace(paseo_role_tools, "resolve_role_launch_context", side_effect=self.context)
        self.replace(paseo_role_caller, "TaskDocumentTopology", self.topology)
        self.replace(paseo_role_recipients, "resolve_role_launch_context", side_effect=self.context)
        self.runtime._agent_send = self.agent_send
        self.runtime._agent_wait = self.agent_wait
        # What each wait call answers, in order; the clock moves by `waitMs` with every call.
        self.waits: list[dict[str, Any]] = []
        self.clock = 0.0
        self.replace(paseo_role_wait, "_monotonic", lambda: self.clock)
        self.architect = binding("architect")

    def topology(self, _root: Any) -> Any:
        documents = {SPRINT_REF: self.sprint, MASTER_REF: self.master, LEAF_REF: self.leaf}

        class Topology:
            @staticmethod
            def resolve(reference: TaskDocumentRef) -> ResolvedTaskDocument:
                if reference not in documents:
                    raise TaskDocumentRefError("task-document-not-found", reference.key)
                return documents[reference]

        return Topology()

    def agent_send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The bridge's delivery rules: only a live agent with an open session is sent to."""

        agent = self.runtime.agents.get(payload["agentId"])
        refusal = (
            ("not-found", "the host has no such agent")
            if agent is None
            else ("archived", "the agent is archived")
            if agent["archivedAt"]
            else ("closed", "the session of the agent is closed")
            if agent["status"] == "closed"
            else ("busy", "the agent waits for a permission decision")
            if agent.get("pendingPermissions")
            # MIK-R171@v2 rule 2: a steer goes into a running turn only for a provider the
            # product knows takes one; every other message to such a recipient is held.
            else ("busy", "the agent is in a turn and its provider is not one that takes a steer")
            if (agent["status"] == "running" or payload.get("turnReportedByHost") is True)
            and agent.get("steers") is not True
            else None
        )
        if refusal is not None:
            delivery: dict[str, Any] = {
                "delivered": False,
                "refused": refusal[0],
                "detail": refusal[1],
            }
            if refusal[1].endswith("permission decision"):
                name = agent["pendingPermissions"][0].get("name") if agent else None
                delivery.update(permissionPending=True, **({"permission": name} if name else {}))
            return {"serverId": SERVER_ID, "delivery": delivery}
        assert agent is not None
        in_turn = agent["status"] == "running" or payload.get("turnReportedByHost") is True
        agent.setdefault("received", []).append((payload["messageId"], payload["text"]))
        # The host records the message id in the recipient's timeline; it does not
        # deduplicate by it, so a resend under the same id would be a second copy.
        agent.setdefault("timeline", []).append(payload["messageId"])
        agent["status"] = "running"
        # A host-reported replacement models the read/send race or a failed steer (v2 rule 2).
        taken = agent.get("taken") or ("steered" if in_turn else "started")
        return {
            "serverId": SERVER_ID,
            "delivery": {
                "delivered": True,
                "taken": taken,
                "turnId": TURN,
            },
        }

    def agent_wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.clock += payload["waitMs"] / 1000
        # Without a queued answer the time is up and the turn the caller named still runs.
        still = {"state": "running", "turnId": payload.get("turnId")}
        return {"serverId": SERVER_ID, "wait": self.waits.pop(0) if self.waits else still}

    def waited(self) -> list[dict[str, Any]]:
        return [payload for command, payload in self.runtime.calls if command == "agent-wait"]

    def message_ids(self, agent_id: str) -> list[str]:
        return [message_id for message_id, _text in self.runtime.agents[agent_id]["received"]]

    def start(self, caller: AgentBinding | None, role: str, **fields: Any) -> dict[str, Any]:
        call = RoleStartCall(
            role=role,
            request_id=fields.pop("request_id", None) or uuid.uuid4(),
            **{**selection_of(role), **fields},
        )
        environment = caller.environment() if caller is not None else {}
        return start_role(self.config, call, environment=environment)

    def message(
        self, caller: AgentBinding | None, text: str = "report your plan", **to: Any
    ) -> dict[str, Any]:
        environment = caller.environment() if caller is not None else {}
        return send_role_message(
            self.config, RoleMessageCall(text=text, **to), environment=environment
        )

    def started(self, role: str = "worker", **state: Any) -> tuple[RoleDispatchRequest, str]:
        """A role agent the dashboard started, in the given state; its request and agent id."""

        request = self.launched(role, **state)
        return request, self.receipt(request)["agentId"]

    def received(self, agent_id: str) -> list[str]:
        return [text for _id, text in self.runtime.agents[agent_id].get("received", [])]

    def refusal(self, result: dict[str, Any], name: str) -> dict[str, Any]:
        self.assertEqual(
            (result["ok"], result["status"], result["refusal"]), (False, "refused", name)
        )
        self.assertTrue(result["detail"].strip() and result["nextAction"].strip())
        return result
