"""Focused proof for deterministic recovery routing of already-started tasks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.application.task_docs import task_unstarted_evidence as evidence_module
from agents_remember.models.task_document_ref import TaskDocumentRef


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _evidence(*, contract: Any | None = None) -> evidence_module._StartedRouteEvidence:
    binding = _value(
        repo_id="agents-remember",
        task_name="sprint",
        contract_path=Path("/tmp/contract.json"),
        row=_value(number="L1"),
        task_ref=TaskDocumentRef(repository="agents-remember", path="sprint/leaf.json"),
    )
    return evidence_module._StartedRouteEvidence(
        binding=binding,
        contract=contract,
        locator=_value(state="absent", locator=None),
        projections=(),
        seat_ids=(),
        report_ids=(),
        task_registrations=(),
    )


def test_projection_control_uses_declared_values_and_safe_defaults() -> None:
    projection = _value(
        kind="closeout",
        legalControls=[
            "not-a-control",
            {
                "action": "retry-closeout",
                "tool": "worktree_operation_control",
                "arguments": {"expected_generation": 2},
            },
        ],
    )
    assert evidence_module._projection_control_route(projection) == (
        "retry-closeout",
        "worktree_operation_control",
        {"expected_generation": 2},
    )

    projection.legalControls = [{}]
    assert evidence_module._projection_control_route(projection) == (
        "recover-closeout",
        "worktree_operation_control",
        {},
    )
    projection.legalControls = []
    assert evidence_module._projection_control_route(projection) is None


def test_projection_recovery_distinguishes_controls_completion_and_authority() -> None:
    contract = _value(contract_path=Path("/tmp/live-contract.json"))
    evidence = _evidence(contract=contract)
    controlled = _value(
        kind="integrate",
        status="failed",
        legalControls=[{"action": "recover", "tool": "control", "arguments": {}}],
    )
    with mock.patch.object(
        evidence_module, "primary_operation_projection", return_value=controlled
    ):
        assert evidence_module._projection_recovery_route(evidence) == (
            "recover",
            "control",
            {},
        )

    completed_closeout = _value(kind="closeout", status="completed", legalControls=[])
    assert evidence_module._projection_without_control(evidence, completed_closeout) == (
        "complete-integration",
        "worktree_integrate",
        {"contract_path": "/tmp/live-contract.json"},
    )

    completed_integrate = _value(kind="integrate", status="completed", legalControls=[])
    action, tool, arguments = evidence_module._projection_without_control(
        evidence, completed_integrate
    )
    assert (action, tool) == ("complete-started-task", "task_doc")
    assert arguments == {
        "repo_id": "agents-remember",
        "task_name": "sprint",
        "task_document_ref": {
            "repository": "agents-remember",
            "path": "sprint/leaf.json",
        },
    }

    running = _value(kind="integrate", status="running", legalControls=[])
    assert evidence_module._projection_without_control(evidence, running) == (
        "recover-integrate-authority",
        "worktree_status",
        {"contract_path": "/tmp/contract.json"},
    )
