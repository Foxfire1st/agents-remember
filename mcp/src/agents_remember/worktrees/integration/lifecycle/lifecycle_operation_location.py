"""Canonical locator -> enclosure manifest -> lifecycle journal authority."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.lifecycles.enclosure import (
    EnclosurePublicationKind,
    LifecycleEnclosureLocator,
    LifecycleEnclosureManifest,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationKind
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
    operation_report_path,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

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


class LifecycleOperationLocationError(RuntimeError):
    """The sole locator-to-root authority could not be proven."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object],
        observed: Mapping[str, object],
    ) -> None:
        self.status = status
        self.detail = detail
        self.expected = dict(expected)
        self.observed = dict(observed)
        super().__init__(detail)


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
    binding = {
        "locatorId": locator_id,
        "repository": contract.repo_name,
        "contractPath": contract_path.as_posix(),
        "worktreeGroup": worktree_group.as_posix(),
        "lifecycleDirectory": lifecycle_directory.as_posix(),
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "leafId": contract.leaf_id,
        "lifecycleId": contract.lifecycle_id,
    }
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


def publish_new_lifecycle_operation_location(
    contract: WorktreeContract,
    *,
    contract_text: str,
) -> LifecycleOperationLocation:
    """Publish reserve -> manifest proof -> contract proof -> addressable."""

    artifacts = prepare_enclosure_publication(
        contract,
        contract_text=contract_text,
        publication_kind="new-enclosure",
        audit_intent="worktree_start accepted this exact enclosure binding",
    )
    return publish_enclosure_location(artifacts, contract_mode="publish")


def resume_new_lifecycle_operation_location(
    contract: WorktreeContract,
    *,
    contract_text: str,
) -> LifecycleOperationLocation:
    """Resume only an already-reserved new-enclosure publication."""

    artifacts = prepare_enclosure_publication(
        contract,
        contract_text=contract_text,
        publication_kind="new-enclosure",
        audit_intent="worktree_start accepted this exact enclosure binding",
    )
    if _path_presence(artifacts.locator_path, artifacts.contract_path, "locator") == "missing":
        raise _location_error(
            "operation-location-adoption-required",
            "the readable pre-existing enclosure has no canonical locator reservation",
            contract_path=artifacts.contract_path,
            observed={
                "locatorPath": artifacts.locator_path.as_posix(),
                "state": "missing",
            },
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
    expected = {
        "locatorId": locator.locatorId,
        "publicationRequestId": locator.publicationRequestId,
        "repository": locator.repository,
        "contractPath": locator.stableAddress,
        "worktreeGroup": locator.worktreeGroup,
        "manifestPath": locator.manifestPath,
        "lifecycleDirectory": locator.lifecycleDirectory,
        "bindingFingerprint": locator.bindingFingerprint,
        "manifestSha256": locator.expectedManifestSha256,
        "initialContractSha256": locator.expectedInitialContractSha256,
    }
    observed = {
        "locatorId": manifest.locatorId,
        "publicationRequestId": manifest.publicationRequestId,
        "repository": manifest.repository,
        "contractPath": manifest.contractPath,
        "worktreeGroup": manifest.worktreeGroup,
        "manifestPath": manifest_path.as_posix(),
        "lifecycleDirectory": manifest.lifecycleDirectory,
        "bindingFingerprint": manifest.bindingFingerprint,
        "manifestSha256": observed_manifest_sha,
        "initialContractSha256": manifest.initialContractSha256,
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
    return locator.model_dump(
        mode="json",
        exclude={
            "state",
            "provenManifestSha256",
            "provenInitialContractSha256",
            "terminalArchivePath",
            "terminalArchiveSha256",
            "terminalReceiptPath",
        },
    )


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


def _read_bytes(path: Path, owner: str, contract_path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise _location_error(
            "operation-location-invalid",
            f"the canonical {owner} bytes are unreadable",
            contract_path=contract_path,
            observed={
                "path": path.as_posix(),
                "owner": owner,
                "errorType": type(exc).__name__,
            },
        ) from exc


def _path_presence(
    path: Path,
    contract_path: Path,
    owner: str,
) -> Literal["missing", "file"]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise _location_error(
            "operation-location-invalid",
            f"the canonical {owner} path cannot be inspected",
            contract_path=contract_path,
            observed={
                "path": path.as_posix(),
                "owner": owner,
                "errorType": type(exc).__name__,
            },
        ) from exc
    if not stat.S_ISREG(mode):
        raise _location_error(
            "operation-location-invalid",
            f"the canonical {owner} path is present but is not a regular file",
            contract_path=contract_path,
            observed={
                "path": path.as_posix(),
                "owner": owner,
                "fileType": "non-regular",
            },
        )
    return "file"


def _locator_id(contract_path: Path) -> str:
    return hashlib.sha256(contract_path.as_posix().encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _model_text(model: BaseModel) -> str:
    return model.model_dump_json(indent=2) + "\n"


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


def _location_error(
    status: str,
    detail: str,
    *,
    contract_path: Path,
    observed: dict[str, object],
) -> LifecycleOperationLocationError:
    return LifecycleOperationLocationError(
        status,
        detail,
        expected={
            "contractPath": contract_path.as_posix(),
            "route": "locator -> root manifest -> root journal",
        },
        observed=observed,
    )
