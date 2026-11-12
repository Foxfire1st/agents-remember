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
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from agents_remember.models.role_identity import canonical_role
from agents_remember.models.task_document_ref import TaskDocumentRef

# Every role a launch may select (MIK-R196). ``bootstrap`` is the free agent a new user
# reaches through the one start call: identity-free, parentless and taskless, it sets one
# repository up and hands over. ``system-specialist`` is the documented alias of
# ``investigator`` and keeps the same value here.
LauncherRole = Literal[
    "architect",
    "investigator",
    "system-specialist",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
    "bootstrap",
]


class RoleAgentOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    agent_id: str | None = Field(default=None, alias="agentId", min_length=1, max_length=80)
    model_id: str | None = Field(default=None, alias="modelId", max_length=200)
    effort_id: str | None = Field(default=None, alias="effortId", max_length=80)
    feature_values: dict[str, StrictBool | StrictStr] | None = Field(
        default=None, alias="featureValues"
    )


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
    step_up: str | None = Field(default=None, alias="stepUp", min_length=1, max_length=1000)
    action: Literal["start", "revive"] = "start"
    agent_override: RoleAgentOverride | None = Field(default=None, alias="agentOverride")
    # The holder this start replaces on its leaf seat (MIK-R100 rule 9): the start
    # retires that agent first, in the same call, by rule 8. Absent for an ordinary start.
    replaces_agent_id: str | None = Field(default=None, alias="replacesAgentId")
    # The word that the holder's turn, if it is in one, may be interrupted by the
    # replacement (rule 8). Without it a running holder is refused, as in retirement.
    replaces_interrupt_turn: bool = Field(default=False, alias="replacesInterruptTurn")
    # A chat without a role started this through role_start (MIK-R113 rule 6): no
    # parent, the parentless table applies, the launch record says so. Absent for
    # every dashboard start and every role agent's start.
    chat_started: bool = Field(default=False, alias="chatStarted")
    # The harness the calling chat itself runs, as the MCP client named itself (MIK-R196
    # rule 1). It is the honest signal the free seat's harness is chosen from; absent means
    # the caller did not name one, and nothing substitutes a provider for it.
    caller_harness: str | None = Field(default=None, alias="callerHarness", max_length=120)


class RoleResultRequest(RoleSelection):
    request_id: uuid.UUID | None = Field(default=None, alias="requestId")
