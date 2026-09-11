"""A persisted door publication that predates the contract-byte cut still reads.

Commit ``f02598c0`` took ``closeout_door`` out of the worktree contract and, with it, the
three contract-byte digests ``DoorPublicationEvidence`` used to carry. The narrower model is
correct; the operation records already written to disk are not. Every read of a persisted
record -- the operation store and the terminal archive cleanup publishes before deleting an
enclosure -- validates one, so a narrowed model without tolerance made cleanup refuse its own
canonical evidence with ``terminal-archive-evidence-invalid``.

The tolerance is the contract parser's, applied at the one model that narrowed: the three
retired names are dropped on the way in, never read, and the next rewrite heals them away.
Every other unknown key stays a hard refusal, which the last case here pins.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from agents_remember.models.lifecycles.door import (
    CloseoutDoorGeneration,
    DoorAdmissionProvenance,
    DoorProvenance,
    DoorPublicationEvidence,
    DoorSchedulingProvenance,
)
from agents_remember.models.lifecycles.enclosure import TerminalWorktreeCleanupArguments
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import MissingTaskIntent
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from pydantic import ValidationError

RETIRED_DIGEST_FIELDS = (
    "expectedBeforeContractSha256",
    "expectedPublishedContractSha256",
    "observedPublishedContractSha256",
)


def _digest(seed: bytes) -> str:
    return hashlib.sha256(seed).hexdigest()


def _door_generation() -> CloseoutDoorGeneration:
    """One structurally valid source generation, with no lifecycle fixture attached."""

    not_applicable = DoorProvenance(
        state="not-applicable",
        fingerprint=_digest(b"retired-door-provenance"),
    )
    return CloseoutDoorGeneration(
        generationId=_digest(b"retired-door-generation"),
        disposition="waiting",
        taskId="RETIRED-DOOR",
        taskName="retired-door",
        taskDocumentRef=TaskDocumentRef(repository="repo", path="retired-door/leaf.json"),
        owningMasterTaskDocumentRef=TaskDocumentRef(
            repository="repo", path="retired-door/task.json"
        ),
        sprintTaskDocumentRef=TaskDocumentRef(repository="repo", path="sprint/task.json"),
        contractPath="/coordination/tasks/repo/retired-door/enclosures/leaf/series-contract.md",
        candidateTree="a" * 40,
        codeBaseCommit="b" * 40,
        taskTopologyFingerprint=_digest(b"retired-door-topology"),
        taskIntent=MissingTaskIntent(),
        reviewProvenance=not_applicable,
        memoryProvenance=not_applicable,
        ledgerProvenance=not_applicable,
        admissionProvenance=DoorAdmissionProvenance(
            fingerprint=_digest(b"retired-door-admission"),
        ),
        schedulingProvenance=DoorSchedulingProvenance(
            priority="normal",
            judgmentId="RETIRED-DOOR-FIXTURE",
            fingerprint=_digest(b"retired-door-scheduling"),
        ),
        declaredBy="test-fixture:retired-door",
        declaredAt="2026-08-15T00:00:00+00:00",
    )


def _legacy_door_publication() -> dict[str, object]:
    published = DoorPublicationEvidence(state="proven", generation=_door_generation())
    return {
        **published.model_dump(mode="json"),
        **{name: "c" * 64 for name in RETIRED_DIGEST_FIELDS},
    }


def _inject_retired_digests(record_path: Path) -> None:
    """Rewrite one already-persisted record with the retired fields it used to carry."""

    payload = json.loads(record_path.read_text(encoding="utf-8"))
    publication = payload["doorPublication"]
    assert isinstance(publication, dict)
    for name in RETIRED_DIGEST_FIELDS:
        assert name not in publication
        publication[name] = "c" * 64
    record_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_door_publication_drops_retired_digests_but_refuses_other_unknown_keys() -> None:
    """The three retired names read; every other unknown name is still a hard refusal."""

    legacy = _legacy_door_publication()
    evidence = DoorPublicationEvidence.model_validate(legacy)
    assert evidence.state == "proven"
    assert evidence.generation.disposition == "waiting"
    assert not set(RETIRED_DIGEST_FIELDS) & set(evidence.model_dump(mode="json"))

    with pytest.raises(ValidationError) as refused:
        DoorPublicationEvidence.model_validate({**legacy, "unexpectedField": "x"})
    assert {error["type"] for error in refused.value.errors()} == {"extra_forbidden"}


def test_operation_store_and_cleanup_terminal_archive_read_a_legacy_record(tmp_path: Path) -> None:
    """The store read and cleanup's canonical-evidence read both accept the legacy record."""

    fixture = _authority_fixture(tmp_path, external_memory=True)
    closed = _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=True)
    record_path = operation_record_path(closed.worktree_group, "closeout")
    _inject_retired_digests(record_path)

    # The strict store reader (the one that raised the three extra_forbidden errors).
    record = LifecycleOperationStore(record_path).read()
    assert isinstance(record, LifecycleOperationRecord)
    assert record.doorPublication is not None
    assert record.doorPublication.state == "proven"

    # Cleanup archives exactly these records and reads the archive back before deleting.
    result = terminal_archive_required_result(
        closed,
        operation="worktree_cleanup",
        arguments=TerminalWorktreeCleanupArguments(teardown_providers=True),
        dry_run=False,
    )
    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "terminal-archive-proven"
    entries = cast(list[dict[str, object]], result.payload["canonicalEntries"])
    assert record_path.name in {str(entry["relativePath"]) for entry in entries}
