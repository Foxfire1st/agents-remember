"""Focused proof for queue authorization and exact quality/ledger evidence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.integration import integration_quality
from agents_remember.worktrees.integration.closeout import ledger_recovery
from agents_remember.worktrees.queue import closeout_queue
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _ref(path: str) -> TaskDocumentRef:
    return TaskDocumentRef(repository="repo", path=path)


def test_projection_access_accepts_only_commanded_manager_documents() -> None:
    sprint = _ref("sprint/task.json")
    master = _ref("sprint/master.json")
    config = _value(coordination_root=Path("/tmp/coordination"))
    manager = _value(role="manager", task_document_ref=master)
    with mock.patch.object(closeout_queue, "_commanded_manager_refs", return_value={master}):
        closeout_queue._authorize_projection_access(config, sprint, manager)

    manager.task_document_ref = _ref("other/master.json")
    with (
        mock.patch.object(closeout_queue, "_commanded_manager_refs", return_value={master}),
        pytest.raises(CloseoutQueueError, match="exact commanded master"),
    ):
        closeout_queue._authorize_projection_access(config, sprint, manager)

    topology = _value(resolve=lambda _ref: _value())
    with (
        mock.patch.object(closeout_queue, "TaskDocumentTopology", return_value=topology),
        mock.patch.object(
            closeout_queue, "commanded_sprint_masters", return_value=(_value(ref=master),)
        ),
    ):
        assert closeout_queue._commanded_manager_refs(config, sprint) == {master}

    topology.resolve = mock.Mock(side_effect=TaskDocumentRefError("bad-ref", "bad"))
    with (
        mock.patch.object(closeout_queue, "TaskDocumentTopology", return_value=topology),
        pytest.raises(CloseoutQueueError, match="bad"),
    ):
        closeout_queue._commanded_manager_refs(config, sprint)


def test_ledger_child_proof_requires_bound_inputs_and_readable_parent() -> None:
    repository = Path("/tmp/memory")
    live = _value(head="after")
    assert not ledger_recovery._exact_intended_child(
        repository,
        "ledger.md",
        _value(before=None, expectedOutputTree=None),
        live,
        "intended",
    )

    evidence = _value(before=_value(), expectedOutputTree="tree")
    with mock.patch.object(ledger_recovery, "_intended_child_observation", return_value=None):
        assert not ledger_recovery._exact_intended_child(
            repository, "ledger.md", evidence, live, "intended"
        )

    with mock.patch.object(ledger_recovery, "require_git", side_effect=RuntimeError("missing")):
        assert ledger_recovery._intended_child_observation(repository, "ledger.md", live) is None


def test_quality_certification_matches_the_exact_candidate_and_plan() -> None:
    completion = _value(fingerprint="fingerprint", code_tree="tree")
    contract = _value(code_commit="commit")
    plan = _value()
    certification = _value(model_dump=lambda **_kwargs: {"certification": True})
    validated = _value(
        completionFingerprint="fingerprint",
        codeCommit="commit",
        candidateTree="tree",
        attestation={"mode": "full"},
    )
    with (
        mock.patch.object(
            integration_quality.IntegrationQualityCertification,
            "model_validate",
            return_value=validated,
        ),
        mock.patch.object(
            integration_quality, "_quality_attestation", return_value={"mode": "full"}
        ),
    ):
        integration_quality._require_matching_certification(
            contract, completion, certification, plan=plan
        )
        validated.codeCommit = "other"
        with pytest.raises(RuntimeError, match="another candidate"):
            integration_quality._require_matching_certification(
                contract, completion, certification, plan=plan
            )
