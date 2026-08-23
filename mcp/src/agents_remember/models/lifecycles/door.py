"""Contract-owned closeout-door generation and publication evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind

CloseoutDoorDisposition = Literal[
    "waiting",
    "claimed",
    "cancelled",
    "withdrawn",
    "retired",
    "superseded",
]
DoorPublicationState = Literal["intent", "proven"]


class CloseoutDoorGeneration(BaseModel):
    """One task-local declaration/disposition generation, never a queue row."""

    model_config = ConfigDict(extra="forbid")

    generationId: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessorGenerationId: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    successorGenerationId: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    disposition: CloseoutDoorDisposition
    taskId: str = Field(min_length=1, max_length=4096)
    taskName: str = Field(min_length=1, max_length=4096)
    contractPath: str = Field(min_length=1, max_length=4096)
    codeBaseCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryBaseCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    taskStateFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKind: LifecycleOperationKind | None = None
    operationFingerprint: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    claimedOperationKey: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _claimed_generation_has_one_operation(self) -> CloseoutDoorGeneration:
        claimed = self.disposition == "claimed"
        identity_cells = (
            self.operationKind is not None,
            bool(self.operationFingerprint),
            bool(self.claimedOperationKey),
        )
        if (claimed and not all(identity_cells)) or (not claimed and any(identity_cells)):
            raise ValueError(
                "claimed closeout-door generation requires one exact lifecycle operation; "
                "non-claimed generations require every operation identity cell cleared"
            )
        return self


class DoorPublicationEvidence(BaseModel):
    """Write-once intent/proof for one exact contract publication."""

    model_config = ConfigDict(extra="forbid")

    state: DoorPublicationState
    generation: CloseoutDoorGeneration
    expectedBeforeContractSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectedPublishedContractSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observedPublishedContractSha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _proof_is_exact(self) -> DoorPublicationEvidence:
        if self.state == "intent" and self.observedPublishedContractSha256 is not None:
            raise ValueError("door publication intent cannot claim observed publication")
        if self.state == "proven" and (
            self.observedPublishedContractSha256 != self.expectedPublishedContractSha256
        ):
            raise ValueError("door publication proof must match the intended contract bytes")
        return self
