"""Strict immutable records for lifecycle enclosure addressability."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from agents_remember.models.base import StrictResponseModel

Sha256 = str
TerminalCleanupOperation = Literal["worktree_cleanup", "worktree_abandon"]
EnclosurePublicationKind = Literal[
    "new-enclosure",
    "successor-enclosure",
    "legacy-adoption",
]
EnclosurePublicationState = Literal[
    "reserved",
    "manifest-proven",
    "addressable",
    "terminal-archived",
]


class TerminalEnclosurePredecessor(StrictResponseModel):
    """Typed address/archive link to the immediately preceding terminal generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    publicationRequestId: str = Field(pattern=r"^[0-9a-f]{64}$")
    bindingFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    worktreeGroup: str = Field(min_length=1, max_length=4096)
    manifestPath: str = Field(min_length=1, max_length=4096)
    expectedManifestSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    expectedInitialContractSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    terminalArchivePath: str = Field(min_length=1, max_length=4096)
    terminalArchiveSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    terminalReceiptPath: str = Field(min_length=1, max_length=4096)


class TerminalEnclosureReceipt(StrictResponseModel):
    """External readback receipt binding one collected enclosure generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    state: Literal["terminal-archived"] = "terminal-archived"
    locatorId: str = Field(pattern=r"^[0-9a-f]{64}$")
    publicationRequestId: str = Field(pattern=r"^[0-9a-f]{64}$")
    bindingFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository: str = Field(min_length=1, max_length=512)
    contractPath: str = Field(min_length=1, max_length=4096)
    worktreeGroup: str = Field(min_length=1, max_length=4096)
    manifestPath: str = Field(min_length=1, max_length=4096)
    expectedManifestSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    expectedInitialContractSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    terminalArchivePath: str = Field(min_length=1, max_length=4096)
    terminalArchiveSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    terminalReceiptPath: str = Field(min_length=1, max_length=4096)


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
    predecessorTerminal: TerminalEnclosurePredecessor | None = None

    @model_validator(mode="after")
    def _successor_generation_has_predecessor(self) -> LifecycleEnclosureManifest:
        is_successor = self.publicationKind == "successor-enclosure"
        if is_successor != (self.predecessorTerminal is not None):
            raise ValueError("only a successor enclosure may carry one terminal predecessor")
        return self


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
    predecessorTerminal: TerminalEnclosurePredecessor | None = None

    @model_validator(mode="after")
    def _publication_evidence_matches_state(self) -> LifecycleEnclosureLocator:
        is_successor = self.publicationKind == "successor-enclosure"
        if is_successor != (self.predecessorTerminal is not None):
            raise ValueError("only a successor enclosure may carry one terminal predecessor")
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


class TerminalEnclosureArchiveEntry(StrictResponseModel):
    """One exact canonical enclosure-root artifact copied into terminal proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relativePath: str = Field(min_length=1, max_length=1024)
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    sizeBytes: int = Field(ge=1, le=16 * 1024 * 1024)
    content: str = Field(min_length=1, max_length=16 * 1024 * 1024)

    @model_validator(mode="after")
    def _path_and_bytes_are_exact(self) -> TerminalEnclosureArchiveEntry:
        path = PurePosixPath(self.relativePath)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("terminal archive entry must use one confined relative path")
        payload = self.content.encode("utf-8")
        if len(payload) != self.sizeBytes:
            raise ValueError("terminal archive entry size does not match its UTF-8 bytes")
        if hashlib.sha256(payload).hexdigest() != self.sha256:
            raise ValueError("terminal archive entry digest does not match its content")
        return self


class TerminalWorktreeCleanupArguments(StrictResponseModel):
    """Exact accepted public arguments for terminal cleanup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    teardown_providers: bool


class TerminalWorktreeAbandonArguments(StrictResponseModel):
    """Exact accepted public arguments for terminal abandon."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    force: bool


TerminalCleanupArguments = (
    TerminalWorktreeCleanupArguments | TerminalWorktreeAbandonArguments
)


def _require_cleanup_arguments(
    operation: TerminalCleanupOperation,
    arguments: TerminalCleanupArguments,
) -> None:
    if operation == "worktree_cleanup" and not isinstance(
        arguments,
        TerminalWorktreeCleanupArguments,
    ):
        raise ValueError("worktree_cleanup requires exact teardown_providers authority")
    if operation == "worktree_abandon" and not isinstance(
        arguments,
        TerminalWorktreeAbandonArguments,
    ):
        raise ValueError("worktree_abandon requires exact force authority")


class TerminalEnclosureArchive(StrictResponseModel):
    """Bounded external copy of the canonical evidence needed after root deletion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    state: Literal["terminal-archive-proven"] = "terminal-archive-proven"
    cleanupOperation: TerminalCleanupOperation
    cleanupArguments: TerminalCleanupArguments
    cleanupRequestId: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: LifecycleEnclosureLocator
    manifest: LifecycleEnclosureManifest
    contractPath: str = Field(min_length=1, max_length=4096)
    contractSha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    contractText: str = Field(min_length=1, max_length=16 * 1024 * 1024)
    canonicalEntries: list[TerminalEnclosureArchiveEntry] = Field(
        min_length=1,
        max_length=1024,
    )

    @model_validator(mode="after")
    def _archive_identity_and_bytes_are_exact(self) -> TerminalEnclosureArchive:
        _require_cleanup_arguments(self.cleanupOperation, self.cleanupArguments)
        if self.locator.state != "addressable":
            raise ValueError("terminal archive must preserve the last addressable locator")
        if (
            self.locator.publicationRequestId != self.manifest.publicationRequestId
            or self.locator.bindingFingerprint != self.manifest.bindingFingerprint
            or self.locator.manifestPath != self.manifest.lifecycleDirectory
            + "/enclosure-manifest.json"
            or self.contractPath != self.locator.stableAddress
            or self.contractPath != self.manifest.contractPath
        ):
            raise ValueError("terminal archive locator, manifest, and contract identity disagree")
        contract_bytes = self.contractText.encode("utf-8")
        if hashlib.sha256(contract_bytes).hexdigest() != self.contractSha256:
            raise ValueError("terminal archive contract digest does not match its bytes")
        request_payload = (
            f"{self.locator.publicationRequestId}\n"
            f"{self.cleanupOperation}\n"
            f"{self.cleanupArguments.model_dump_json()}\n"
            f"{self.contractSha256}\n"
        ).encode()
        if hashlib.sha256(request_payload).hexdigest() != self.cleanupRequestId:
            raise ValueError("terminal archive cleanup request identity is inconsistent")
        paths = [entry.relativePath for entry in self.canonicalEntries]
        if len(paths) != len(set(paths)):
            raise ValueError("terminal archive canonical entry paths must be unique")
        manifest_entries = [
            entry for entry in self.canonicalEntries if entry.relativePath == "enclosure-manifest.json"
        ]
        if len(manifest_entries) != 1:
            raise ValueError("terminal archive requires the exact enclosure manifest entry")
        manifest_exclude = (
            {"predecessorTerminal"} if self.manifest.predecessorTerminal is None else None
        )
        expected_manifest = self.manifest.model_dump_json(
            indent=2,
            exclude=manifest_exclude,
        ) + "\n"
        if manifest_entries[0].content != expected_manifest:
            raise ValueError("terminal archive manifest entry contradicts the typed manifest")
        manifest_sha256 = hashlib.sha256(expected_manifest.encode("utf-8")).hexdigest()
        if (
            self.locator.expectedManifestSha256 != manifest_sha256
            or self.locator.provenManifestSha256 != manifest_sha256
        ):
            raise ValueError("terminal archive manifest bytes contradict locator proof")
        return self
