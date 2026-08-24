"""Canonical locator -> enclosure manifest -> lifecycle journal authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.lifecycles.enclosure import (
    EnclosurePublicationKind,
    EnclosurePublicationState,
    LifecycleEnclosureLocator,
    LifecycleEnclosureManifest,
    TerminalCleanupOperation,
    TerminalEnclosurePredecessor,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationKind
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    require_successor_generation,
    terminal_predecessor,
    validate_terminal_proof,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    location_error as _location_error,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    location_path_presence as _path_presence,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    read_location_bytes as _read_bytes,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
    operation_report_path,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    contract_publication_text,
)

LIFECYCLE_DIRECTORY = ".lifecycle"
ENCLOSURE_MANIFEST = "enclosure-manifest.json"

_LOCATION_OWNERSHIP = StoreOwnership(
    store="lifecycle-enclosure-locator",
    writers=("mcp",),
    compaction_owner=None,
    rationale=(
        "The MCP start and explicit adoption tools publish one address-only locator; "
        "operation workers own only journals beneath the proven enclosure root."
    ),
)


@dataclass(frozen=True)
class LifecycleOperationLocation:
    """One cross-checked addressable locator and immutable root manifest."""

    locator_path: Path
    locator: LifecycleEnclosureLocator
    manifest: LifecycleEnclosureManifest

    @property
    def contract_path(self) -> Path:
        return Path(self.manifest.contractPath)

    @property
    def worktree_group(self) -> Path:
        return Path(self.manifest.worktreeGroup)

    @property
    def lifecycle_directory(self) -> Path:
        return Path(self.manifest.lifecycleDirectory)

    @property
    def manifest_path(self) -> Path:
        return Path(self.locator.manifestPath)

    def journal_path(self, kind: LifecycleOperationKind) -> Path:
        return operation_record_path(self.worktree_group, kind)

    def report_path(self, kind: LifecycleOperationKind) -> Path:
        return operation_report_path(self.worktree_group, kind)

    def payload(self) -> dict[str, object]:
        return {
            "locatorPath": self.locator_path.as_posix(),
            "locator": self.locator.model_dump(mode="json"),
            "manifest": self.manifest.model_dump(mode="json"),
        }


@dataclass(frozen=True)
class EnclosurePublicationArtifacts:
    """Deterministic bytes and identities accepted before the first write."""

    coordination_root: Path
    locator_path: Path
    manifest_path: Path
    contract_path: Path
    manifest_text: str
    contract_text: str
    reserved_locator: LifecycleEnclosureLocator

    @property
    def manifest(self) -> LifecycleEnclosureManifest:
        return LifecycleEnclosureManifest.model_validate_json(self.manifest_text)


@dataclass(frozen=True)
class LifecycleLocatorObservation:
    """Typed exact-address observation used by start/discard serialization."""

    path: Path
    state: Literal[
        "missing",
        "unreadable",
        "reserved",
        "manifest-proven",
        "addressable",
        "terminal-archived",
    ]
    locator: LifecycleEnclosureLocator | None = None
    status: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class TerminalLifecyclePublication:
    """Exact external proof accepted when an addressable locator turns terminal."""

    operation: TerminalCleanupOperation
    archive_path: Path
    archive_sha256: str
    receipt_path: Path


@dataclass(frozen=True)
class _EnclosureBindingIdentity:
    locator_id: str
    repository: str
    contract_path: str
    worktree_group: str
    lifecycle_directory: str
    task_id: str
    task_name: str
    leaf_id: str
    lifecycle_id: str

    @classmethod
    def from_manifest(cls, manifest: LifecycleEnclosureManifest) -> _EnclosureBindingIdentity:
        return cls(
            locator_id=manifest.locatorId,
            repository=manifest.repository,
            contract_path=manifest.contractPath,
            worktree_group=manifest.worktreeGroup,
            lifecycle_directory=manifest.lifecycleDirectory,
            task_id=manifest.taskId,
            task_name=manifest.taskName,
            leaf_id=manifest.leafId,
            lifecycle_id=manifest.lifecycleId,
        )


def lifecycle_operation_locator_path(
    coordination_root: Path,
    contract_path: Path,
) -> Path:
    """Derive one pointer path from the exact stable public contract address."""

    confined = _confined_contract_path(coordination_root, contract_path)
    return (
        coordination_root.resolve(strict=False)
        / "controlplane"
        / "lifecycle-enclosures"
        / (f"{_locator_id(confined)}.json")
    )


def lifecycle_enclosure_manifest_path(worktree_group: Path) -> Path:
    return worktree_group.resolve(strict=False) / LIFECYCLE_DIRECTORY / ENCLOSURE_MANIFEST


def prepare_enclosure_publication(
    contract: WorktreeContract,
    *,
    contract_text: str,
    publication_kind: EnclosurePublicationKind,
    audit_intent: str,
    predecessor_terminal: TerminalEnclosurePredecessor | None = None,
) -> EnclosurePublicationArtifacts:
    """Build the immutable binding and expected digests before the first write."""

    intent = audit_intent.strip()
    if not intent:
        raise ValueError("enclosure publication requires a nonblank audit intent")
    root = contract.coordination_root.resolve(strict=False)
    contract_path = _confined_contract_path(root, contract.contract_path)
    worktree_group = contract.worktree_group.resolve(strict=False)
    _require_confined_worktree_group(root, contract.repo_name, worktree_group)
    manifest_path = lifecycle_enclosure_manifest_path(worktree_group)
    lifecycle_directory = manifest_path.parent
    locator_path = lifecycle_operation_locator_path(root, contract_path)
    locator_id = _locator_id(contract_path)
    contract_sha256 = _sha256_text(contract_text)
    binding = _enclosure_binding_payload(
        _EnclosureBindingIdentity(
            locator_id=locator_id,
            repository=contract.repo_name,
            contract_path=contract_path.as_posix(),
            worktree_group=worktree_group.as_posix(),
            lifecycle_directory=lifecycle_directory.as_posix(),
            task_id=contract.task_id,
            task_name=contract.task_name,
            leaf_id=contract.leaf_id,
            lifecycle_id=contract.lifecycle_id,
        ),
        predecessor_terminal=predecessor_terminal,
    )
    binding_fingerprint = _sha256_payload(binding)
    request_id = _sha256_payload(
        {
            "publicationKind": publication_kind,
            "auditIntent": intent,
            "initialContractSha256": contract_sha256,
            "bindingFingerprint": binding_fingerprint,
        }
    )
    manifest = LifecycleEnclosureManifest(
        locatorId=locator_id,
        publicationRequestId=request_id,
        publicationKind=publication_kind,
        auditIntent=intent,
        repository=contract.repo_name,
        contractPath=contract_path.as_posix(),
        initialContractSha256=contract_sha256,
        worktreeGroup=worktree_group.as_posix(),
        lifecycleDirectory=lifecycle_directory.as_posix(),
        taskId=contract.task_id,
        taskName=contract.task_name,
        leafId=contract.leaf_id,
        lifecycleId=contract.lifecycle_id,
        bindingFingerprint=binding_fingerprint,
        predecessorTerminal=predecessor_terminal,
    )
    manifest_text = _model_text(manifest)
    locator = LifecycleEnclosureLocator(
        locatorId=locator_id,
        publicationRequestId=request_id,
        publicationKind=publication_kind,
        stableAddress=contract_path.as_posix(),
        repository=contract.repo_name,
        worktreeGroup=worktree_group.as_posix(),
        manifestPath=manifest_path.as_posix(),
        lifecycleDirectory=lifecycle_directory.as_posix(),
        bindingFingerprint=binding_fingerprint,
        expectedManifestSha256=_sha256_text(manifest_text),
        expectedInitialContractSha256=contract_sha256,
        predecessorTerminal=predecessor_terminal,
    )
    return EnclosurePublicationArtifacts(
        coordination_root=root,
        locator_path=locator_path,
        manifest_path=manifest_path,
        contract_path=contract_path,
        manifest_text=manifest_text,
        contract_text=contract_text,
        reserved_locator=locator,
    )


def _prepare_start_generation(
    contract: WorktreeContract,
    *,
    contract_text: str,
    predecessor_terminal: TerminalEnclosurePredecessor | None = None,
) -> EnclosurePublicationArtifacts:
    successor = predecessor_terminal is not None
    return prepare_enclosure_publication(
        contract,
        contract_text=contract_text,
        publication_kind="successor-enclosure" if successor else "new-enclosure",
        audit_intent=(
            "worktree_start accepted this exact successor enclosure generation"
            if successor
            else "worktree_start accepted this exact enclosure binding"
        ),
        predecessor_terminal=predecessor_terminal,
    )


def publish_new_lifecycle_operation_location(
    contract: WorktreeContract,
    *,
    contract_text: str,
) -> LifecycleOperationLocation:
    """Publish reserve -> manifest proof -> contract proof -> addressable."""

    artifacts = _prepare_start_generation(
        contract,
        contract_text=contract_text,
    )
    return publish_enclosure_location(artifacts, contract_mode="publish")


def reserve_new_lifecycle_operation_location(
    contract: WorktreeContract,
    *,
    contract_text: str,
    predecessor_contract: WorktreeContract | None = None,
) -> LifecycleEnclosureLocator:
    """Reserve the exact initial or successor address before any long start work.

    The caller holds the repository task-publication CAS while proving the leaf still exists.
    A terminal locator advances only with its exact restartable predecessor contract. Other
    addressable or terminal authority cannot be silently reused as a fresh start.
    """

    locator_path = lifecycle_operation_locator_path(
        contract.coordination_root,
        contract.contract_path,
    )
    with exclusive_access(locator_path, _LOCATION_OWNERSHIP):
        presence = _path_presence(locator_path, contract.contract_path, "locator")
        predecessor: TerminalEnclosurePredecessor | None = None
        current: LifecycleEnclosureLocator | None = None
        if presence == "file":
            current = _read_locator(locator_path, contract.contract_path)
            _validate_locator(
                contract.coordination_root,
                contract.contract_path.resolve(strict=False),
                locator_path,
                current,
            )
            if current.state == "terminal-archived":
                predecessor = terminal_predecessor(current, contract.contract_path)
            elif current.publicationKind == "successor-enclosure":
                predecessor = current.predecessorTerminal
        if predecessor_contract is not None:
            if predecessor is None:
                raise _location_error(
                    "operation-location-terminal-predecessor-missing",
                    "successor start requires the exact prior terminal locator receipt",
                    contract_path=contract.contract_path,
                    observed={
                        "locatorPath": locator_path.as_posix(),
                        "state": current.state if current is not None else "missing",
                    },
                )
            require_successor_generation(
                contract,
                predecessor_contract,
                predecessor,
            )
        elif predecessor is not None:
            raise _location_error(
                "operation-location-successor-proof-required",
                "a terminal predecessor can advance only from a restartable predecessor contract",
                contract_path=contract.contract_path,
                observed={
                    "locatorPath": locator_path.as_posix(),
                    "publicationRequestId": predecessor.publicationRequestId,
                },
            )
        artifacts = _prepare_start_generation(
            contract,
            contract_text=contract_text,
            predecessor_terminal=predecessor,
        )
        if current is not None and current.state == "terminal-archived":
            if (
                _path_presence(
                    artifacts.manifest_path,
                    artifacts.contract_path,
                    "successor manifest",
                )
                != "missing"
            ):
                raise _location_error(
                    "operation-location-successor-root-occupied",
                    "successor reservation requires the prior enclosure root to be collected",
                    contract_path=artifacts.contract_path,
                    observed={"manifestPath": artifacts.manifest_path.as_posix()},
                )
            _write_locator(artifacts.locator_path, artifacts.reserved_locator)
            locator = artifacts.reserved_locator
        else:
            locator = _reserve_locator(artifacts)
        if locator.state not in {"reserved", "manifest-proven"}:
            raise _location_conflict(
                "enclosure reservation state",
                artifacts.reserved_locator,
                locator,
            )
        _publish_successor_contract(artifacts, predecessor_contract)
        return locator


def resume_new_lifecycle_operation_location(
    contract: WorktreeContract,
    *,
    contract_text: str,
) -> LifecycleOperationLocation:
    """Resume only the generation already recorded by the canonical reservation."""

    locator_path = lifecycle_operation_locator_path(
        contract.coordination_root,
        contract.contract_path,
    )
    if _path_presence(locator_path, contract.contract_path, "locator") == "missing":
        raise _location_error(
            "operation-location-adoption-required",
            "the readable pre-existing enclosure has no canonical locator reservation",
            contract_path=contract.contract_path,
            observed={
                "locatorPath": locator_path.as_posix(),
                "state": "missing",
            },
        )
    current = _read_locator(locator_path, contract.contract_path)
    _validate_locator(
        contract.coordination_root,
        contract.contract_path.resolve(strict=False),
        locator_path,
        current,
    )
    if current.state == "terminal-archived":
        raise _location_conflict(
            "terminal locator",
            _prepare_start_generation(contract, contract_text=contract_text).reserved_locator,
            current,
        )
    predecessor = (
        current.predecessorTerminal if current.publicationKind == "successor-enclosure" else None
    )
    artifacts = _prepare_start_generation(
        contract,
        contract_text=contract_text,
        predecessor_terminal=predecessor,
    )
    return publish_enclosure_location(artifacts, contract_mode="publish")


def publish_enclosure_location(
    artifacts: EnclosurePublicationArtifacts,
    *,
    contract_mode: Literal["publish", "prove-existing"],
    before_contract_proof: Callable[[], None] | None = None,
    after_addressable: Callable[[LifecycleOperationLocation], None] | None = None,
) -> LifecycleOperationLocation:
    """One locked publication transaction shared by new start and explicit adoption."""

    with exclusive_access(artifacts.locator_path, _LOCATION_OWNERSHIP):
        locator = _reserve_locator(artifacts)
        if locator.state == "terminal-archived":
            raise _location_conflict("terminal locator", artifacts.reserved_locator, locator)
        _publish_or_prove_manifest(artifacts)
        locator = _prove_manifest(artifacts, locator)
        if before_contract_proof is not None and locator.state != "addressable":
            before_contract_proof()
        _publish_or_prove_contract(artifacts, mode=contract_mode)
        locator = _prove_contract(artifacts, locator)
        manifest = _read_manifest(artifacts.manifest_path, artifacts.contract_path)
        _validate_manifest(
            artifacts.coordination_root,
            artifacts.contract_path,
            locator,
            artifacts.manifest_path,
            manifest,
        )
        location = LifecycleOperationLocation(artifacts.locator_path, locator, manifest)
        if after_addressable is not None:
            after_addressable(location)
        return location


def resolve_lifecycle_operation_location(
    coordination_root: Path,
    contract_path: Path,
) -> LifecycleOperationLocation:
    """Resolve locator -> root manifest -> journals without scans or inference."""

    root = coordination_root.resolve(strict=False)
    confined = _confined_contract_path(root, contract_path)
    locator_path = lifecycle_operation_locator_path(root, confined)
    if _path_presence(locator_path, confined, "locator") == "missing":
        raise _location_error(
            "operation-location-adoption-required",
            "the canonical enclosure locator is missing; normal readers cannot adopt it",
            contract_path=confined,
            observed={"locatorPath": locator_path.as_posix(), "state": "missing"},
        )
    locator = _read_locator(locator_path, confined)
    _validate_locator(root, confined, locator_path, locator)
    if locator.state in {"reserved", "manifest-proven"}:
        raise _location_error(
            "operation-location-publication-interrupted",
            "new enclosure addressability publication is incomplete",
            contract_path=confined,
            observed={
                "locatorPath": locator_path.as_posix(),
                "publicationState": locator.state,
                "publicationRequestId": locator.publicationRequestId,
            },
        )
    if locator.state == "terminal-archived":
        raise _location_error(
            "operation-location-terminal-archived",
            "the enclosure root was deliberately collected after terminal archive proof",
            contract_path=confined,
            observed={
                "locatorPath": locator_path.as_posix(),
                "terminalArchivePath": locator.terminalArchivePath or "",
                "terminalArchiveSha256": locator.terminalArchiveSha256 or "",
                "terminalReceiptPath": locator.terminalReceiptPath or "",
            },
        )
    manifest_path = Path(locator.manifestPath)
    manifest = _read_manifest(manifest_path, confined)
    _validate_manifest(root, confined, locator, manifest_path, manifest)
    return LifecycleOperationLocation(locator_path, locator, manifest)


def inspect_lifecycle_operation_locator(
    coordination_root: Path,
    contract_path: Path,
) -> LifecycleLocatorObservation:
    """Inspect only the canonical locator address; present-invalid fails closed and stays typed."""

    confined = _confined_contract_path(coordination_root, contract_path)
    path = lifecycle_operation_locator_path(coordination_root, confined)
    try:
        presence = _path_presence(path, confined, "locator")
    except LifecycleOperationLocationError as error:
        return LifecycleLocatorObservation(
            path,
            "unreadable",
            status=error.status,
            detail=error.detail,
        )
    if presence == "missing":
        return LifecycleLocatorObservation(path, "missing")
    try:
        locator = _read_locator(path, confined)
        _validate_locator(coordination_root, confined, path, locator)
    except LifecycleOperationLocationError as error:
        return LifecycleLocatorObservation(
            path,
            "unreadable",
            status=error.status,
            detail=error.detail,
        )
    state: EnclosurePublicationState = locator.state
    return LifecycleLocatorObservation(path, state, locator=locator)


def publish_terminal_lifecycle_operation_location(
    contract: WorktreeContract,
    expected_location: LifecycleOperationLocation,
    publication: TerminalLifecyclePublication,
) -> LifecycleEnclosureLocator:
    """Publish one verified external archive as the locator's terminal receipt.

    Archive and receipt bytes must already exist and read back successfully. The exact
    addressable locator is the only state allowed to cross this boundary; an identical
    retry observes the same terminal record, while a changed binding or cleanup request
    remains a typed conflict.
    """

    locator_path = lifecycle_operation_locator_path(
        contract.coordination_root,
        contract.contract_path,
    )
    archive = publication.archive_path.resolve(strict=False).as_posix()
    receipt = publication.receipt_path.resolve(strict=False).as_posix()
    with exclusive_access(locator_path, _LOCATION_OWNERSHIP):
        current = _read_locator(locator_path, contract.contract_path)
        _validate_locator(
            contract.coordination_root,
            contract.contract_path.resolve(strict=False),
            locator_path,
            current,
        )
        expected_terminal = {
            "state": "terminal-archived",
            "terminalArchivePath": archive,
            "terminalArchiveSha256": publication.archive_sha256,
            "terminalReceiptPath": receipt,
        }
        if current.state == "terminal-archived":
            observed_terminal = {
                "state": current.state,
                "terminalArchivePath": current.terminalArchivePath,
                "terminalArchiveSha256": current.terminalArchiveSha256,
                "terminalReceiptPath": current.terminalReceiptPath,
            }
            if observed_terminal != expected_terminal:
                raise LifecycleOperationLocationError(
                    "operation-location-terminal-conflict",
                    "the terminal locator belongs to a different "
                    f"{publication.operation} archive request",
                    expected=expected_terminal,
                    observed=observed_terminal,
                )
            validate_terminal_proof(
                contract.coordination_root,
                contract.contract_path,
                current,
            )
            return current
        if current != expected_location.locator or current.state != "addressable":
            raise _location_conflict(
                "terminal archive source locator",
                expected_location.locator,
                current,
            )
        require_contract_matches_lifecycle_operation_location(contract, expected_location)
        terminal = LifecycleEnclosureLocator.model_validate(
            {
                **current.model_dump(mode="json"),
                **expected_terminal,
            }
        )
        validate_terminal_proof(
            contract.coordination_root,
            contract.contract_path,
            terminal,
        )
        _write_locator(locator_path, terminal)
        observed = _read_locator(locator_path, contract.contract_path)
        if observed != terminal:
            raise LifecycleOperationLocationError(
                "operation-location-terminal-publication-failed",
                "the terminal locator did not read back as the exact published receipt",
                expected=terminal.model_dump(mode="json"),
                observed=observed.model_dump(mode="json"),
            )
        return observed


def require_terminal_lifecycle_predecessor(
    contract: WorktreeContract,
) -> TerminalEnclosurePredecessor:
    """Prove terminal archive authority before a contract becomes restartable."""

    locator_path = lifecycle_operation_locator_path(
        contract.coordination_root,
        contract.contract_path,
    )
    if _path_presence(locator_path, contract.contract_path, "locator") == "missing":
        raise _location_error(
            "operation-location-terminal-predecessor-missing",
            "task restart requires a canonical terminal locator receipt",
            contract_path=contract.contract_path,
            observed={"locatorPath": locator_path.as_posix(), "state": "missing"},
        )
    locator = _read_locator(locator_path, contract.contract_path)
    _validate_locator(
        contract.coordination_root,
        contract.contract_path.resolve(strict=False),
        locator_path,
        locator,
    )
    expected = {
        "state": "terminal-archived",
        "repository": contract.repo_name,
        "worktreeGroup": contract.worktree_group.resolve(strict=False).as_posix(),
    }
    observed = {
        "state": locator.state,
        "repository": locator.repository,
        "worktreeGroup": locator.worktreeGroup,
    }
    if expected != observed:
        raise LifecycleOperationLocationError(
            "operation-location-terminal-predecessor-mismatch",
            "task restart requires the exact prior enclosure's terminal locator receipt",
            expected=expected,
            observed=observed,
        )
    return terminal_predecessor(locator, contract.contract_path)


def require_matching_lifecycle_operation_location(
    contract: WorktreeContract,
) -> LifecycleOperationLocation:
    """Resolve the locator and cross-check every readable immutable contract cell."""

    location = resolve_lifecycle_operation_location(
        contract.coordination_root,
        contract.contract_path,
    )
    require_contract_matches_lifecycle_operation_location(contract, location)
    return location


def located_lifecycle_operation_store(
    contract: WorktreeContract,
    kind: LifecycleOperationKind,
) -> LifecycleOperationStore:
    """Open one journal only after locator and readable-contract cross-checks."""

    location = require_matching_lifecycle_operation_location(contract)
    return LifecycleOperationStore(location.journal_path(kind))


def located_lifecycle_operation_path(
    contract: WorktreeContract,
    kind: LifecycleOperationKind,
) -> Path:
    """Resolve one canonical journal path without invoking its strict record reader."""

    return require_matching_lifecycle_operation_location(contract).journal_path(kind)


def located_lifecycle_operation_report_path(
    contract: WorktreeContract,
    kind: LifecycleOperationKind,
) -> Path:
    """Resolve the canonical operation log beside its located journal."""

    return require_matching_lifecycle_operation_location(contract).report_path(kind)


def require_contract_matches_lifecycle_operation_location(
    contract: WorktreeContract,
    location: LifecycleOperationLocation,
) -> None:
    """Cross-check one already-resolved location without a second live read."""

    expected = _manifest_identity(contract)
    observed = _manifest_identity_payload(location.manifest)
    if observed != expected:
        raise LifecycleOperationLocationError(
            "operation-location-mismatch",
            "the readable contract contradicts its immutable enclosure manifest",
            expected=expected,
            observed=observed,
        )


def _reserve_locator(
    artifacts: EnclosurePublicationArtifacts,
) -> LifecycleEnclosureLocator:
    if (
        _path_presence(
            artifacts.locator_path,
            artifacts.contract_path,
            "locator",
        )
        == "file"
    ):
        current = _read_locator(artifacts.locator_path, artifacts.contract_path)
        if _locator_binding(current) != _locator_binding(artifacts.reserved_locator):
            raise _location_conflict("locator binding", artifacts.reserved_locator, current)
        return current
    _write_locator(artifacts.locator_path, artifacts.reserved_locator)
    return artifacts.reserved_locator


def _publish_or_prove_manifest(artifacts: EnclosurePublicationArtifacts) -> None:
    if (
        _path_presence(
            artifacts.manifest_path,
            artifacts.contract_path,
            "manifest",
        )
        == "missing"
    ):
        _publish_exact_text(
            artifacts.manifest_path,
            artifacts.manifest_text,
            owner="manifest",
            contract_path=artifacts.contract_path,
        )
    observed = _read_bytes(artifacts.manifest_path, "manifest", artifacts.contract_path)
    if observed != artifacts.manifest_text.encode("utf-8"):
        raise _byte_conflict("manifest", artifacts.manifest_text.encode(), observed)
    parsed = _read_manifest(artifacts.manifest_path, artifacts.contract_path)
    if parsed != artifacts.manifest:
        raise _location_conflict("manifest binding", artifacts.manifest, parsed)


def _publish_successor_contract(
    artifacts: EnclosurePublicationArtifacts,
    predecessor_contract: WorktreeContract | None,
) -> None:
    """Replace only the exact restartable tombstone with accepted successor bytes."""

    if predecessor_contract is None:
        return
    observed = _read_bytes(
        artifacts.contract_path,
        "successor predecessor contract",
        artifacts.contract_path,
    )
    successor = artifacts.contract_text.encode()
    if observed == successor:
        return
    predecessor = contract_publication_text(
        predecessor_contract.contract_path,
        predecessor_contract,
    ).encode()
    if observed != predecessor:
        raise LifecycleOperationLocationError(
            "operation-location-successor-contract-mismatch",
            "the stable contract address no longer contains the exact accepted predecessor",
            expected={"predecessorContractSha256": _sha256_bytes(predecessor)},
            observed={"contractSha256": _sha256_bytes(observed)},
        )
    atomic_write_text(artifacts.contract_path, artifacts.contract_text)
    published = _read_bytes(
        artifacts.contract_path,
        "successor contract",
        artifacts.contract_path,
    )
    if published != successor:
        raise _byte_conflict("successor contract", successor, published)


def _prove_manifest(
    artifacts: EnclosurePublicationArtifacts,
    locator: LifecycleEnclosureLocator,
) -> LifecycleEnclosureLocator:
    if locator.state != "reserved":
        return locator
    proven = locator.model_copy(
        update={
            "state": "manifest-proven",
            "provenManifestSha256": locator.expectedManifestSha256,
        }
    )
    _write_locator(artifacts.locator_path, proven)
    return proven


def _publish_or_prove_contract(
    artifacts: EnclosurePublicationArtifacts,
    *,
    mode: Literal["publish", "prove-existing"],
) -> None:
    if (
        _path_presence(
            artifacts.contract_path,
            artifacts.contract_path,
            "contract",
        )
        == "missing"
    ):
        if mode == "prove-existing":
            raise _location_error(
                "operation-location-adoption-conflict",
                "explicit enclosure adoption requires the accepted contract bytes to remain",
                contract_path=artifacts.contract_path,
                observed={"contractPath": artifacts.contract_path.as_posix(), "state": "missing"},
            )
        _publish_exact_text(
            artifacts.contract_path,
            artifacts.contract_text,
            owner="initial contract",
            contract_path=artifacts.contract_path,
        )
    observed = _read_bytes(artifacts.contract_path, "contract", artifacts.contract_path)
    expected = artifacts.contract_text.encode("utf-8")
    if observed != expected:
        raise _byte_conflict("initial contract", expected, observed)


def _prove_contract(
    artifacts: EnclosurePublicationArtifacts,
    locator: LifecycleEnclosureLocator,
) -> LifecycleEnclosureLocator:
    if locator.state == "addressable":
        return locator
    if locator.state != "manifest-proven":
        raise _location_conflict("publication state", artifacts.reserved_locator, locator)
    proven = locator.model_copy(
        update={
            "state": "addressable",
            "provenInitialContractSha256": locator.expectedInitialContractSha256,
        }
    )
    _write_locator(artifacts.locator_path, proven)
    return proven


def _write_locator(path: Path, locator: LifecycleEnclosureLocator) -> None:
    text = _model_text(locator)
    _publish_exact_text(
        path,
        text,
        owner="locator publication",
        contract_path=Path(locator.stableAddress),
    )
    observed = _read_bytes(path, "locator", Path(locator.stableAddress))
    if observed != text.encode("utf-8"):
        raise _byte_conflict("locator publication", text.encode(), observed)


def _publish_exact_text(
    path: Path,
    text: str,
    *,
    owner: str,
    contract_path: Path,
) -> None:
    """Publish exact bytes or return bounded interruption/conflict evidence."""

    expected = text.encode("utf-8")
    write_error: OSError | None = None
    try:
        atomic_write_text(path, text)
    except OSError as exc:
        write_error = exc
    try:
        presence = _path_presence(path, contract_path, owner)
    except LifecycleOperationLocationError:
        raise
    if presence == "missing":
        detail = f"the canonical {owner} publication was interrupted before readback"
        raise LifecycleOperationLocationError(
            "operation-location-publication-interrupted",
            detail,
            expected={
                "path": path.as_posix(),
                "sha256": hashlib.sha256(expected).hexdigest(),
                "size": len(expected),
            },
            observed={
                "state": "missing",
                "errorType": type(write_error).__name__ if write_error else "FileNotFoundError",
            },
        ) from write_error
    observed = _read_bytes(path, owner, contract_path)
    if observed != expected:
        raise _byte_conflict(owner, expected, observed)


def _read_locator(path: Path, contract_path: Path) -> LifecycleEnclosureLocator:
    try:
        return LifecycleEnclosureLocator.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise _location_error(
            "operation-location-invalid",
            "the control-plane enclosure locator is unreadable or invalid",
            contract_path=contract_path,
            observed={"locatorPath": path.as_posix(), "errorType": type(exc).__name__},
        ) from exc


def _read_manifest(path: Path, contract_path: Path) -> LifecycleEnclosureManifest:
    try:
        return LifecycleEnclosureManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        status = (
            "operation-enclosure-root-missing"
            if isinstance(exc, FileNotFoundError)
            else "operation-location-invalid"
        )
        raise _location_error(
            status,
            "the canonical enclosure-root manifest is missing, unreadable, or invalid",
            contract_path=contract_path,
            observed={"manifestPath": path.as_posix(), "errorType": type(exc).__name__},
        ) from exc


def _validate_locator(
    coordination_root: Path,
    contract_path: Path,
    locator_path: Path,
    locator: LifecycleEnclosureLocator,
) -> None:
    expected_path = lifecycle_operation_locator_path(coordination_root, contract_path)
    if (
        locator.locatorId != _locator_id(contract_path)
        or Path(locator.stableAddress) != contract_path
        or locator_path != expected_path
    ):
        raise _location_error(
            "operation-location-invalid",
            "the enclosure locator identity contradicts its canonical address",
            contract_path=contract_path,
            observed={
                "locatorPath": locator_path.as_posix(),
                "locatorId": locator.locatorId,
                "stableAddress": locator.stableAddress,
            },
        )
    validate_terminal_proof(coordination_root, contract_path, locator)


def _validate_manifest(
    coordination_root: Path,
    contract_path: Path,
    locator: LifecycleEnclosureLocator,
    manifest_path: Path,
    manifest: LifecycleEnclosureManifest,
) -> None:
    worktree_group = Path(manifest.worktreeGroup).resolve(strict=False)
    expected_manifest_path = lifecycle_enclosure_manifest_path(worktree_group)
    _require_confined_worktree_group(coordination_root, manifest.repository, worktree_group)
    observed_manifest_sha = hashlib.sha256(
        _read_bytes(manifest_path, "manifest", contract_path)
    ).hexdigest()
    derived_binding_fingerprint = _sha256_payload(
        _enclosure_binding_payload(
            _EnclosureBindingIdentity.from_manifest(manifest),
            predecessor_terminal=manifest.predecessorTerminal,
        )
    )
    derived_publication_request_id = _sha256_payload(
        {
            "publicationKind": manifest.publicationKind,
            "auditIntent": manifest.auditIntent,
            "initialContractSha256": manifest.initialContractSha256,
            "bindingFingerprint": derived_binding_fingerprint,
        }
    )
    expected = {
        "locatorId": locator.locatorId,
        "publicationRequestId": locator.publicationRequestId,
        "publicationKind": locator.publicationKind,
        "repository": locator.repository,
        "contractPath": locator.stableAddress,
        "worktreeGroup": locator.worktreeGroup,
        "manifestPath": locator.manifestPath,
        "lifecycleDirectory": locator.lifecycleDirectory,
        "bindingFingerprint": locator.bindingFingerprint,
        "derivedBindingFingerprint": locator.bindingFingerprint,
        "derivedPublicationRequestId": locator.publicationRequestId,
        "manifestSha256": locator.expectedManifestSha256,
        "initialContractSha256": locator.expectedInitialContractSha256,
        "predecessorTerminal": (
            locator.predecessorTerminal.model_dump(mode="json")
            if locator.predecessorTerminal is not None
            else None
        ),
    }
    observed = {
        "locatorId": manifest.locatorId,
        "publicationRequestId": manifest.publicationRequestId,
        "publicationKind": manifest.publicationKind,
        "repository": manifest.repository,
        "contractPath": manifest.contractPath,
        "worktreeGroup": manifest.worktreeGroup,
        "manifestPath": manifest_path.as_posix(),
        "lifecycleDirectory": manifest.lifecycleDirectory,
        "bindingFingerprint": manifest.bindingFingerprint,
        "derivedBindingFingerprint": derived_binding_fingerprint,
        "derivedPublicationRequestId": derived_publication_request_id,
        "manifestSha256": observed_manifest_sha,
        "initialContractSha256": manifest.initialContractSha256,
        "predecessorTerminal": (
            manifest.predecessorTerminal.model_dump(mode="json")
            if manifest.predecessorTerminal is not None
            else None
        ),
    }
    if (
        expected != observed
        or manifest_path != expected_manifest_path
        or Path(manifest.lifecycleDirectory) != expected_manifest_path.parent
        or locator.provenManifestSha256 != locator.expectedManifestSha256
    ):
        raise LifecycleOperationLocationError(
            "operation-location-mismatch",
            "the enclosure locator and immutable root manifest disagree",
            expected=expected,
            observed=observed,
        )


def _manifest_identity(contract: WorktreeContract) -> dict[str, str]:
    return {
        "repository": contract.repo_name,
        "contractPath": contract.contract_path.resolve(strict=False).as_posix(),
        "worktreeGroup": contract.worktree_group.resolve(strict=False).as_posix(),
        "lifecycleDirectory": lifecycle_enclosure_manifest_path(
            contract.worktree_group
        ).parent.as_posix(),
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "leafId": contract.leaf_id,
        "lifecycleId": contract.lifecycle_id,
    }


def _manifest_identity_payload(manifest: LifecycleEnclosureManifest) -> dict[str, str]:
    return {
        "repository": manifest.repository,
        "contractPath": manifest.contractPath,
        "worktreeGroup": manifest.worktreeGroup,
        "lifecycleDirectory": manifest.lifecycleDirectory,
        "taskId": manifest.taskId,
        "taskName": manifest.taskName,
        "leafId": manifest.leafId,
        "lifecycleId": manifest.lifecycleId,
    }


def _locator_binding(locator: LifecycleEnclosureLocator) -> dict[str, object]:
    excluded = {
        "state",
        "provenManifestSha256",
        "provenInitialContractSha256",
        "terminalArchivePath",
        "terminalArchiveSha256",
        "terminalReceiptPath",
    }
    if locator.predecessorTerminal is None:
        excluded.add("predecessorTerminal")
    return locator.model_dump(
        mode="json",
        exclude=excluded,
    )


def _enclosure_binding_payload(
    identity: _EnclosureBindingIdentity,
    *,
    predecessor_terminal: TerminalEnclosurePredecessor | None,
) -> dict[str, object]:
    """Build the sole canonical payload protected by the enclosure binding digest."""

    binding: dict[str, object] = {
        "locatorId": identity.locator_id,
        "repository": identity.repository,
        "contractPath": identity.contract_path,
        "worktreeGroup": identity.worktree_group,
        "lifecycleDirectory": identity.lifecycle_directory,
        "taskId": identity.task_id,
        "taskName": identity.task_name,
        "leafId": identity.leaf_id,
        "lifecycleId": identity.lifecycle_id,
    }
    if predecessor_terminal is not None:
        binding["predecessorTerminal"] = predecessor_terminal.model_dump(mode="json")
    return binding


def _confined_contract_path(coordination_root: Path, contract_path: Path) -> Path:
    root = coordination_root.resolve(strict=False)
    confined = contract_path.resolve(strict=False)
    tasks_root = (root / "tasks").resolve(strict=False)
    if not confined.is_relative_to(tasks_root):
        raise LifecycleOperationLocationError(
            "operation-location-invalid",
            "operation location requires an exact contract path inside coordination tasks",
            expected={"tasksRoot": tasks_root.as_posix()},
            observed={"contractPath": confined.as_posix()},
        )
    return confined


def _require_confined_worktree_group(
    coordination_root: Path,
    repository: str,
    worktree_group: Path,
) -> None:
    expected_root = (coordination_root / "worktrees" / repository).resolve(strict=False)
    if not worktree_group.is_relative_to(expected_root):
        raise LifecycleOperationLocationError(
            "operation-location-invalid",
            "enclosure root is outside the configured repository worktree boundary",
            expected={"worktreesRoot": expected_root.as_posix()},
            observed={"worktreeGroup": worktree_group.as_posix()},
        )


def _locator_id(contract_path: Path) -> str:
    return hashlib.sha256(contract_path.as_posix().encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _model_text(model: BaseModel) -> str:
    exclude = (
        {"predecessorTerminal"} if getattr(model, "predecessorTerminal", None) is None else None
    )
    return model.model_dump_json(indent=2, exclude=exclude) + "\n"


def _location_conflict(
    owner: str,
    expected: BaseModel,
    observed: BaseModel,
) -> LifecycleOperationLocationError:
    return LifecycleOperationLocationError(
        "operation-location-conflict",
        f"the immutable enclosure {owner} already contains different binding facts",
        expected=expected.model_dump(mode="json"),
        observed=observed.model_dump(mode="json"),
    )


def _byte_conflict(
    owner: str,
    expected: bytes,
    observed: bytes,
) -> LifecycleOperationLocationError:
    return LifecycleOperationLocationError(
        "operation-location-conflict",
        f"the canonical {owner} bytes differ from the accepted publication",
        expected={"sha256": hashlib.sha256(expected).hexdigest(), "size": len(expected)},
        observed={"sha256": hashlib.sha256(observed).hexdigest(), "size": len(observed)},
    )
