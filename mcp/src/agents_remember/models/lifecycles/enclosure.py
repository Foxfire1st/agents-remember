"""Strict immutable records for lifecycle enclosure addressability."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from agents_remember.models.base import StrictResponseModel

Sha256 = str
EnclosurePublicationKind = Literal["new-enclosure", "legacy-adoption"]
EnclosurePublicationState = Literal[
    "reserved",
    "manifest-proven",
    "addressable",
    "terminal-archived",
]


class LifecycleEnclosureManifest(StrictResponseModel):
    """Immutable identity and provenance stored beside canonical live journals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    locatorId: str = Field(pattern=r"^[0-9a-f]{64}$")
    publicationRequestId: str = Field(pattern=r"^[0-9a-f]{64}$")
    publicationKind: EnclosurePublicationKind
    auditIntent: str = Field(min_length=1, max_length=4096)
    repository: str = Field(min_length=1, max_length=512)
    contractPath: str = Field(min_length=1, max_length=4096)
    initialContractSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    worktreeGroup: str = Field(min_length=1, max_length=4096)
    lifecycleDirectory: str = Field(min_length=1, max_length=4096)
    taskId: str = Field(min_length=1, max_length=512)
    taskName: str = Field(min_length=1, max_length=1024)
    leafId: str = Field(default="", max_length=512)
    lifecycleId: str = Field(default="", max_length=512)
    bindingFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class LifecycleEnclosureLocator(StrictResponseModel):
    """Address-only control-plane record; never a second operation journal."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"] = "1.0"
    locatorId: str = Field(pattern=r"^[0-9a-f]{64}$")
    publicationRequestId: str = Field(pattern=r"^[0-9a-f]{64}$")
    publicationKind: EnclosurePublicationKind
    stableAddress: str = Field(min_length=1, max_length=4096)
    repository: str = Field(min_length=1, max_length=512)
    worktreeGroup: str = Field(min_length=1, max_length=4096)
    manifestPath: str = Field(min_length=1, max_length=4096)
    lifecycleDirectory: str = Field(min_length=1, max_length=4096)
    bindingFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectedManifestSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    expectedInitialContractSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    provenManifestSha256: Sha256 | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    provenInitialContractSha256: Sha256 | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    state: EnclosurePublicationState = "reserved"
    terminalArchivePath: str | None = Field(default=None, max_length=4096)
    terminalArchiveSha256: Sha256 | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    terminalReceiptPath: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def _publication_evidence_matches_state(self) -> LifecycleEnclosureLocator:
        manifest_proven = self.provenManifestSha256 == self.expectedManifestSha256
        contract_proven = self.provenInitialContractSha256 == self.expectedInitialContractSha256
        terminal_fields = (
            self.terminalArchivePath,
            self.terminalArchiveSha256,
            self.terminalReceiptPath,
        )
        if self.state == "reserved" and (
            self.provenManifestSha256 is not None
            or self.provenInitialContractSha256 is not None
            or any(terminal_fields)
        ):
            raise ValueError("reserved locator cannot carry publication proof")
        if self.state == "manifest-proven" and (
            not manifest_proven
            or self.provenInitialContractSha256 is not None
            or any(terminal_fields)
        ):
            raise ValueError("manifest-proven locator requires only exact manifest proof")
        if self.state == "addressable" and (
            not manifest_proven or not contract_proven or any(terminal_fields)
        ):
            raise ValueError("addressable locator requires exact live publication proof")
        if self.state == "terminal-archived" and (
            not manifest_proven or not contract_proven or not all(terminal_fields)
        ):
            raise ValueError("terminal locator requires exact archive and receipt proof")
        return self
