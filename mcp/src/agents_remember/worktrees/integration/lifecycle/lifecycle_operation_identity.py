"""Stable lifecycle-operation identity derived from a worktree contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    contract_publication_text,
)


def operation_state_fingerprint(contract: WorktreeContract) -> str:
    """Hash lifecycle cells that change only when a sequential operation advances."""

    payload = {
        "codeBaseCommit": contract.code_base_commit,
        "memoryBaseCommit": contract.memory_base_commit,
        "closeoutStatus": contract.closeout_status,
        "codeCommit": contract.code_commit,
        "memoryContentCommit": contract.memory_content_commit,
        "integrationStatus": contract.integration_status,
        "integratedCodeCommit": contract.integrated_code_commit,
        "integratedMemoryContentCommit": contract.integrated_memory_content_commit,
        "cleanup": contract.cleanup,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def closeout_contract_sha256(contract: WorktreeContract) -> str:
    """Hash the exact canonical contract bytes that closeout would publish."""

    published = contract_publication_text(contract.contract_path, contract)
    return hashlib.sha256(published.encode("utf-8")).hexdigest()


def operation_key(contract_path: Path, kind: LifecycleOperationKind, fingerprint: str) -> str:
    identity = f"{contract_path.resolve().as_posix()}\0{kind}\0{fingerprint}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def direct_landing_identity(record: LifecycleOperationRecord) -> bool:
    """Whether this record carries a direct landing's own identity.

    A direct landing is admitted by its own request, not by a claimed closeout
    door, so it carries no door publication by design. The identity is the
    operation's kind with its fingerprint bound to its key through the same
    derivation the admission wrote. A record of the earlier shape, which
    carries a door publication, is not recognised. Never raises.
    """

    if record.operationKind != "direct-landing" or record.doorPublication is not None:
        return False
    return record.operationKey == operation_key(
        Path(record.contractPath), "direct-landing", record.fingerprint
    )


def completed_direct_owner(
    record: LifecycleOperationRecord, *, expected_generation: int | None = None
) -> bool:
    """Whether this stored generation is the exact owner of a completed direct
    landing (MIK-R143 rule 2, shared by the offer and the check under rule 3a).

    Ownership is decided on what ``direct_landing_record`` writes: the
    operation's kind, its fingerprint and key, its generation and its status.
    The generation counter is compared only where the caller names the
    generation it expects; the control verifies it upstream through
    ``_require_generation`` exactly as the closeout owner's branch does.
    """

    if not direct_landing_identity(record):
        return False
    observed = (record.generationDisposition, record.status, record.generation)
    expected = (
        "active",
        "completed",
        record.generation if expected_generation is None else expected_generation,
    )
    return observed == expected


def retired_direct_record(record: LifecycleOperationRecord) -> bool:
    """Whether this stored generation is a retired direct landing, for the
    idempotent re-answer of its retire (ruling e55c0093 point 6)."""

    return (
        direct_landing_identity(record)
        and record.generationDisposition == "retired"
        and record.status == "completed"
    )
