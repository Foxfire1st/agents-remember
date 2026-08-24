"""Pure identity, serialization, and conflict helpers for enclosure publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    LifecycleEnclosureManifest,
    TerminalEnclosurePredecessor,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class EnclosureBindingIdentity:
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
    def from_manifest(cls, manifest: LifecycleEnclosureManifest) -> EnclosureBindingIdentity:
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


def manifest_identity(
    contract: WorktreeContract,
    *,
    lifecycle_directory: Path,
) -> dict[str, str]:
    return {
        "repository": contract.repo_name,
        "contractPath": contract.contract_path.resolve(strict=False).as_posix(),
        "worktreeGroup": contract.worktree_group.resolve(strict=False).as_posix(),
        "lifecycleDirectory": lifecycle_directory.resolve(strict=False).as_posix(),
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "leafId": contract.leaf_id,
        "lifecycleId": contract.lifecycle_id,
    }


def manifest_identity_payload(manifest: LifecycleEnclosureManifest) -> dict[str, str]:
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


def locator_binding(locator: LifecycleEnclosureLocator) -> dict[str, object]:
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
    return locator.model_dump(mode="json", exclude=excluded)


def enclosure_binding_payload(
    identity: EnclosureBindingIdentity,
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


def locator_id(contract_path: Path) -> str:
    return hashlib.sha256(contract_path.as_posix().encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def model_text(model: BaseModel) -> str:
    exclude = (
        {"predecessorTerminal"} if getattr(model, "predecessorTerminal", None) is None else None
    )
    return model.model_dump_json(indent=2, exclude=exclude) + "\n"


def location_conflict(
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


def byte_conflict(
    owner: str,
    expected: bytes,
    observed: bytes,
) -> LifecycleOperationLocationError:
    return LifecycleOperationLocationError(
        "operation-location-conflict",
        f"the canonical {owner} bytes differ from the accepted publication",
        expected={"sha256": sha256_bytes(expected), "size": len(expected)},
        observed={"sha256": sha256_bytes(observed), "size": len(observed)},
    )
