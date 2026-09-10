"""The memory-scope task baseline is derived from the task document, not a stored door."""

from __future__ import annotations

import agents_remember.memory_quality.incremental_scope.candidate as candidate_module
from agents_remember.memory_quality.incremental_scope.models import TaskObservationPair
from agents_remember.models.task_document import CanonicalTaskObservation


def _observation() -> CanonicalTaskObservation:
    return CanonicalTaskObservation(
        taskRoot="/coordination/tasks/repo-a/leaf",
        taskDocumentRef={
            "repository": "repo-a",
            "path": "leaf.json",
        },
        sourceDigest="a" * 64,
        sourceAuthorityNamespace="agents-remember.task-document-source",
        sourceValidatorVersion="task-document-source-cas/v1",
        semanticTopologyDigest="b" * 64,
        taskIntent=None,
    )


def test_task_baseline_is_the_task_document_observation(monkeypatch) -> None:
    """Both pair sides come from the task document; no worktree-contract copy is read."""

    observed = _observation()
    monkeypatch.setattr(candidate_module, "observe_contract_task", lambda contract: observed)

    pair = candidate_module.observe_contract_task_pair(object())

    assert isinstance(pair, TaskObservationPair)
    assert pair.base is observed
    assert pair.candidate is observed
    assert pair.base.sourceAuthorityNamespace == "agents-remember.task-document-source"


def test_task_baseline_derivation_never_reads_a_worktree_contract_door() -> None:
    """The derivation cannot consult a contract door: the module names none."""

    source = candidate_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "closeout_door" not in text
    assert "CloseoutDoor" not in text
    assert "worktrees.integration.lifecycle" not in text
