"""MIK-R110 rule 4: a taken-over coherence refusal carries its code and next action.

``memory_quality_check`` on a task whose document has no parent used to answer the
coherence-required outcome with neither a refusal identity nor a next action (the
emptied ``UNMARKED_NOT_OK`` pin). The readiness join now keeps the coherence check's
code and next action -- ``task_doc`` for the parentless document, exactly as
``curator_coherence`` names for the same state.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application.memory_quality.controller import _attach_coherence_readiness
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import WorktreeContract
from curator_coherence_test_support import write_curator_attestation
from test_worktree_support import open_external_contract_fixture

pytestmark = [pytest.mark.evidence_unit, pytest.mark.usefixtures("worktree_services")]


def _parentless_leaf_document(contract: WorktreeContract) -> None:
    """Author the leaf's document under an organizational master with no sprint above it.

    The master exists but no sprint document commands it, so the parent lookup raises
    ``task-document-parent-missing`` -- the state ``curator_coherence`` names with that
    code and ``task_doc``.
    """

    slug = contract.leaf_id
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": contract.leaf_id,
                "slug": slug,
                "title": slug,
                "kind": "subTask",
                "status": "inProgress",
                "repo": contract.repo_name,
                "createdAt": "2026-09-18T00:00:00+00:00",
                "seriesContractPath": series_contract_path(contract.task_root).as_posix(),
                "enclosures": [
                    {
                        "leafId": contract.leaf_id,
                        "enclosurePath": contract.contract_path.as_posix(),
                    }
                ],
                "steps": [{"id": "S1", "title": "Ready", "status": "done"}],
            }
        ),
    )
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": f"{contract.leaf_id}-MASTER",
                "slug": "task",
                "title": f"{contract.task_name} master",
                "kind": "master",
                "status": "inProgress",
                "repo": contract.repo_name,
                "createdAt": "2026-08-13T00:00:00+00:00",
                "executionNature": "organizational",
                "subTasks": [
                    {
                        "number": contract.leaf_id,
                        "name": contract.task_name,
                        "file": f"{contract.leaf_id}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )


def _scope(contract: WorktreeContract) -> Any:
    """The scope the readiness join reads: only its contract."""

    return SimpleNamespace(contract=contract)


def _ready_response(contract: WorktreeContract) -> dict[str, Any]:
    return {"ok": True, "checklistStatus": "ready-for-closeout"}


def test_parentless_task_document_answers_with_the_code_and_task_doc(tmp_path: Path) -> None:
    """The pinned case: no parent names ``task-document-parent-missing`` and ``task_doc``.

    Fails without the rule-4 repair: the answer then holds neither ``code`` nor
    ``nextAction``.
    """

    contract = open_external_contract_fixture(tmp_path)
    _parentless_leaf_document(contract)
    write_curator_attestation(contract)

    response = _ready_response(contract)
    _attach_coherence_readiness(_scope(contract), response)

    assert response["ok"] is False
    assert response["checklistStatus"] == "coherence-required"
    assert response["code"] == "task-document-parent-missing"
    assert response["nextAction"] == "task_doc"


def test_stale_authority_answers_with_its_status_and_publish(tmp_path: Path) -> None:
    """The same join on a stale source keeps that check's own code and next action."""

    contract = open_external_contract_fixture(tmp_path)
    _parentless_leaf_document(contract)
    write_curator_attestation(
        contract,
        source_candidates=[
            {
                "sourceFile": "pkg/a.py",
                "onboardingFile": "onboarding/pkg/a.py.md",
                "classification": "source-change",
            }
        ],
    )
    write_curator_attestation(
        contract,
        source_candidates=[
            {
                "sourceFile": "pkg/b.py",
                "onboardingFile": "onboarding/pkg/b.py.md",
                "classification": "source-change",
            }
        ],
    )

    response = _ready_response(contract)
    _attach_coherence_readiness(_scope(contract), response)

    assert response["ok"] is False
    assert response["checklistStatus"] == "coherence-required"
    assert response["code"] == response["coherenceStatus"]
    assert isinstance(response["code"], str) and response["code"]
    assert response["nextAction"]
