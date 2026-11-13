"""The repaired route: a standalone master's task prepares, publishes and reads current.

Every case drives the production ``curator_coherence_action`` or the production readiness join
against a real external-memory leaf whose master no sprint commands. On the base tree the same
cases refuse with ``task-document-parent-missing``; on the candidate they pass.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from agents_remember.application.memory_quality.controller import _attach_coherence_readiness
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from curator_coherence_test_support import write_curator_evidence, write_curator_task_topology
from test_curator_coherence_closed_out import _leaf_document
from test_memory_quality_coherence_takeover import _ready_response, _scope
from test_worktree_support import open_external_contract_fixture

pytestmark = [pytest.mark.evidence_unit, pytest.mark.usefixtures("worktree_services")]


def _standalone(contract):  # type: ignore[no-untyped-def]
    """Write the fixture lineage, then remove the sprint so the master stands alone.

    The master is written as an atomic standalone master: ``TaskDocumentTopology.parent`` treats
    an explicit atomic master with no sprint as standalone (L13-R5e), while an explicit
    organizational master without a sprint stays the older dead-end.
    """

    master_ref = write_curator_task_topology(contract)

    standalone_ref = TaskDocumentRef(
        repository=master_ref.repository,
        path=f"{contract.task_root.name}/task.json",
    )
    master_path = contract.task_root / "task.json"
    master = TaskDocument.model_validate_json(master_path.read_text())
    write_task_doc(
        contract.task_root,
        master.model_copy(update={"executionNature": "atomic"}),
    )
    shutil.rmtree(contract.coordination_root / "tasks" / contract.repo_name / "sprint")
    write_task_doc(contract.task_root, _leaf_document(contract))
    return standalone_ref


def test_status_prepare_publish_and_validate_a_standalone_masters_task(tmp_path: Path) -> None:
    contract = open_external_contract_fixture(tmp_path)
    master_ref = _standalone(contract)

    # The publication's declared caller is the master: on this shape the publisher check must
    # accept the master in place of the absent sprint.
    write_curator_evidence(contract, caller_ref=master_ref)

    status = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="status", contract_path=contract.contract_path.as_posix()),
    )
    assert status["state"] in {"published", "already-current", "current"}

    validated = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="validate", contract_path=contract.contract_path.as_posix()),
    )
    assert validated["ok"] is True


def test_the_quality_readiness_join_reaches_closeout_ready(tmp_path: Path) -> None:
    contract = open_external_contract_fixture(tmp_path)
    master_ref = _standalone(contract)
    write_curator_evidence(contract, caller_ref=master_ref)

    response = _ready_response(contract)
    _attach_coherence_readiness(_scope(contract), response)

    assert response.get("closeoutReady") is True, response
    assert response.get("coherenceStatus") == "current", response
