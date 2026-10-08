"""Wire models for the role launcher."""

from __future__ import annotations

import uuid
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    field_validator,
    model_validator,
)

from agents_remember.models.role_identity import canonical_role
from agents_remember.models.task_document_ref import TaskDocumentRef

LauncherRole = Literal[
    "architect",
    "investigator",
    "system-specialist",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
]


class RoleAgentOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    agent_id: str = Field(alias="agentId", min_length=1, max_length=80)
    model_id: str | None = Field(default=None, alias="modelId", max_length=200)
    effort_id: str | None = Field(default=None, alias="effortId", max_length=80)


class RoleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role: LauncherRole
    _used_role_alias: bool = PrivateAttr(default=False)

    @field_validator("role", mode="before")
    @classmethod
    def canonical_role_id(cls, value: object) -> object:
        return canonical_role(value) if isinstance(value, str) else value

    @model_validator(mode="wrap")
    @classmethod
    def retain_alias_notice(cls, value: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        result = handler(value)
        if isinstance(value, dict):
            result._used_role_alias = value.get("role") == "system-specialist"
        return result

    @property
    def used_role_alias(self) -> bool:
        return self._used_role_alias

    sprint_document_ref: TaskDocumentRef | None = Field(default=None, alias="sprintDocumentRef")
    master_document_ref: TaskDocumentRef | None = Field(default=None, alias="masterDocumentRef")
    task_document_ref: TaskDocumentRef | None = Field(default=None, alias="taskDocumentRef")


class RoleLauncherOptionsRequest(RoleSelection):
    agent_id: str | None = Field(default=None, alias="agentId", max_length=80)
    refresh_catalog: bool = Field(default=False, alias="refreshCatalog")


class RoleDispatchRequest(RoleSelection):
    request_id: uuid.UUID = Field(alias="requestId")
    action: Literal["start", "revive"] = "start"
    agent_override: RoleAgentOverride | None = Field(default=None, alias="agentOverride")


class RoleResultRequest(RoleSelection):
    request_id: uuid.UUID | None = Field(default=None, alias="requestId")
