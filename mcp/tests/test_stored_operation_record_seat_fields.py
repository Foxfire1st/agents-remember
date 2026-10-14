"""A stored operation record that names the four seat fields still loads (MIK-R113).

``autoClosedSeats``, ``autoCloseDeferredSeats``, ``autoCloseFailedSeats`` and
``autoLandedSeats`` were added to the answers of ``worktree_integrate`` and
``lifecycle_finalize_task`` by the part that ended and landed seats after a landing. That
part is removed and the two answers no longer declare the fields. An operation record
already written to disk is not rewritten: its ``result`` is a free mapping the store never
validates against an answer model, so a record of today's shape that holds the four fields
loads unchanged.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.lifecycles.finalize import LifecycleFinalizeTaskResponse
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.worktree import WorktreeIntegrateResponse
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)

SEAT_FIELDS = {
    "autoClosedSeats": ["seat-closed"],
    "autoCloseDeferredSeats": ["seat-deferred"],
    "autoCloseFailedSeats": ["seat-failed"],
    "autoLandedSeats": ["seat-landed"],
}


def _integrate_record(group: Path) -> LifecycleOperationRecord:
    contract = (group / "contract.json").as_posix()
    return LifecycleOperationRecord.model_validate(
        {
            "taskId": "TASK-1",
            "taskName": "task",
            "contractPath": contract,
            "operationKind": "integrate",
            "candidateState": "f" * 64,
            "fingerprint": "a" * 64,
            "operationKey": "e" * 64,
            "input": {
                "kind": "integrate",
                "configPath": (group / "settings.json").as_posix(),
                "contractPath": contract,
            },
            "status": "completed",
            "phase": "completed",
            "queuedAt": "2026-10-11T00:00:00+00:00",
            "startedAt": "2026-10-11T00:00:00+00:00",
            "finishedAt": "2026-10-11T00:01:00+00:00",
            "reportPath": (group / ".lifecycle" / "integrate-operation.log").as_posix(),
            "integrationAuthority": {
                "targetKind": "atomic-integration",
                "codeRepository": group.as_posix(),
                "codeSourceBranch": "work",
                "codeSourceRef": "refs/heads/work",
                "codeSourceCommit": "1" * 40,
                "codeCandidateCommit": "1" * 40,
            },
            "result": {"ok": True, "state": "integrated", **SEAT_FIELDS},
        }
    )


def test_a_stored_record_of_today_s_shape_that_holds_the_four_fields_still_loads(
    tmp_path: Path,
) -> None:
    group = tmp_path / "group"
    path = operation_record_path(group, "integrate")
    path.parent.mkdir(parents=True)
    stored = _integrate_record(group).model_dump_json()
    path.write_text(stored, encoding="utf-8")

    record = LifecycleOperationStore(path).read()

    assert record is not None
    assert record.schemaVersion == "3.0"
    assert record.result is not None
    assert {name: record.result[name] for name in SEAT_FIELDS} == SEAT_FIELDS
    # Loading rewrites nothing: the stored bytes are the ones written.
    assert path.read_text(encoding="utf-8") == stored


def test_the_answers_of_integrate_and_finalize_no_longer_declare_the_four_fields() -> None:
    for answer in (WorktreeIntegrateResponse, LifecycleFinalizeTaskResponse):
        assert not set(SEAT_FIELDS) & set(answer.model_fields), answer.__name__
