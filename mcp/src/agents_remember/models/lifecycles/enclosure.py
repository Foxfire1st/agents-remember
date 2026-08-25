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
        _require_successor_predecessor(self.publicationKind, self.predecessorTerminal)
        terminal_fields = (
            self.terminalArchivePath,
            self.terminalArchiveSha256,
            self.terminalReceiptPath,
        )
        observed = (
            self.provenManifestSha256 == self.expectedManifestSha256,
            self.provenInitialContractSha256 == self.expectedInitialContractSha256,
            self.provenManifestSha256 is None,
            self.provenInitialContractSha256 is None,
            any(terminal_fields),
            all(terminal_fields),
        )
        expected = {
            "reserved": (False, False, True, True, False, False),
            "manifest-proven": (True, False, False, True, False, False),
            "addressable": (True, True, False, False, False, False),
            "terminal-archived": (True, True, False, False, True, True),
        }[self.state]
        if observed != expected:
            raise ValueError(f"{self.state} locator carries inconsistent publication proof")
        return self


def _require_successor_predecessor(
    publication_kind: EnclosurePublicationKind,
    predecessor: TerminalEnclosurePredecessor | None,
) -> None:
    if (publication_kind == "successor-enclosure") != (predecessor is not None):
        raise ValueError("only a successor enclosure may carry one terminal predecessor")


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


TerminalCleanupArguments = TerminalWorktreeCleanupArguments | TerminalWorktreeAbandonArguments


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
        _require_archive_identity(self)
        _require_contract_digest(self.contractText, self.contractSha256)
        _require_cleanup_request_identity(self)
        _require_unique_archive_paths(self.canonicalEntries)
        manifest_entry = _required_manifest_entry(self.canonicalEntries)
        expected_manifest = _expected_manifest_text(self.manifest)
        if manifest_entry.content != expected_manifest:
            raise ValueError("terminal archive manifest entry contradicts the typed manifest")
        _require_manifest_proof(self.locator, expected_manifest)
        return self


def _require_archive_identity(archive: TerminalEnclosureArchive) -> None:
    observed = (
        archive.locator.publicationRequestId,
        archive.locator.bindingFingerprint,
        archive.locator.manifestPath,
        archive.contractPath,
        archive.contractPath,
    )
    expected = (
        archive.manifest.publicationRequestId,
        archive.manifest.bindingFingerprint,
        archive.manifest.lifecycleDirectory + "/enclosure-manifest.json",
        archive.locator.stableAddress,
        archive.manifest.contractPath,
    )
    if observed != expected:
        raise ValueError("terminal archive locator, manifest, and contract identity disagree")


def _require_contract_digest(contract_text: str, expected_sha256: str) -> None:
    if hashlib.sha256(contract_text.encode("utf-8")).hexdigest() != expected_sha256:
        raise ValueError("terminal archive contract digest does not match its bytes")


def _require_cleanup_request_identity(archive: TerminalEnclosureArchive) -> None:
    request_payload = (
        f"{archive.locator.publicationRequestId}\n"
        f"{archive.cleanupOperation}\n"
        f"{archive.cleanupArguments.model_dump_json()}\n"
        f"{archive.contractSha256}\n"
    ).encode()
    if hashlib.sha256(request_payload).hexdigest() != archive.cleanupRequestId:
        raise ValueError("terminal archive cleanup request identity is inconsistent")


def _require_unique_archive_paths(entries: list[TerminalEnclosureArchiveEntry]) -> None:
    paths = [entry.relativePath for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("terminal archive canonical entry paths must be unique")


def _manifest_entries(
    entries: list[TerminalEnclosureArchiveEntry],
) -> list[TerminalEnclosureArchiveEntry]:
    return [entry for entry in entries if entry.relativePath == "enclosure-manifest.json"]


def _required_manifest_entry(
    entries: list[TerminalEnclosureArchiveEntry],
) -> TerminalEnclosureArchiveEntry:
    manifests = _manifest_entries(entries)
    if len(manifests) != 1:
        raise ValueError("terminal archive requires the exact enclosure manifest entry")
    return manifests[0]


def _expected_manifest_text(manifest: LifecycleEnclosureManifest) -> str:
    exclude = {"predecessorTerminal"} if manifest.predecessorTerminal is None else None
    return manifest.model_dump_json(indent=2, exclude=exclude) + "\n"


def _require_manifest_proof(locator: LifecycleEnclosureLocator, manifest_text: str) -> None:
    manifest_sha256 = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    if (locator.expectedManifestSha256, locator.provenManifestSha256) != (
        manifest_sha256,
        manifest_sha256,
    ):
        raise ValueError("terminal archive manifest bytes contradict locator proof")
