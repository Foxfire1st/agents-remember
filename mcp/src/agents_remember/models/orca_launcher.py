"""Wire models for the native Orca role launcher."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.base import StrictResponseModel, ToolResponse
from agents_remember.models.task_document_ref import TaskDocumentRef

OrcaRole = Literal[
    "architect",
    "system-specialist",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
]


class OrcaAgentOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    agent_id: str = Field(alias="agentId", min_length=1, max_length=80)
    model_id: str | None = Field(default=None, alias="modelId", max_length=200)
    effort_id: str | None = Field(default=None, alias="effortId", max_length=80)


class OrcaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role: OrcaRole
    sprint_document_ref: TaskDocumentRef | None = Field(default=None, alias="sprintDocumentRef")
    master_document_ref: TaskDocumentRef | None = Field(default=None, alias="masterDocumentRef")
    task_document_ref: TaskDocumentRef | None = Field(default=None, alias="taskDocumentRef")


class OrcaLauncherOptionsRequest(OrcaSelection):
    agent_id: str | None = Field(default=None, alias="agentId", max_length=80)
    refresh_catalog: bool = Field(default=False, alias="refreshCatalog")


class OrcaDispatchRequest(OrcaSelection):
    request_id: uuid.UUID = Field(alias="requestId")
    action: Literal["start", "revive"] = "start"
    agent_override: OrcaAgentOverride | None = Field(default=None, alias="agentOverride")


class OrcaResultRequest(OrcaSelection):
    request_id: uuid.UUID | None = Field(default=None, alias="requestId")


class OrcaRoleHandoverReference(StrictResponseModel):
    path: str
    sha256: str


class OrcaNativeSessionIdentity(StrictResponseModel):
    handle: str | None = None
    sessionId: str | None = None
    worktreeId: str | None = None


class OrcaNativeWorkerStart(StrictResponseModel):
    operation: Literal["orca orchestration worker-start"] = "orca orchestration worker-start"
    workspaceSelector: str
    terminalHandle: str
    spec: str


class OrcaNativeNextAction(StrictResponseModel):
    runCreateOperation: Literal["orca orchestration run-create"] = "orca orchestration run-create"
    runObjective: str
    workerStart: OrcaNativeWorkerStart


class OrcaRolePrepareResponse(ToolResponse):
    """An idle native session and the compact inputs to its native Orca task start."""

    operation: Literal["orca_role_prepare"] = "orca_role_prepare"
    status: Literal["idle-session-ready", "unknown", "rejected"]
    detail: str
    requestId: str
    role: Literal["worker", "reviewer", "curator"]
    taskReference: str
    taskDocumentDigest: str
    capsuleDigest: str
    candidateClass: Literal["working-tree-diff"] = "working-tree-diff"
    baselineSourcePath: str
    handover: OrcaRoleHandoverReference
    taskReportPath: str
    nativeIdentity: OrcaNativeSessionIdentity = Field(default_factory=OrcaNativeSessionIdentity)
    nativeNextAction: OrcaNativeNextAction | None = None
    workStarted: Literal[False] = False
