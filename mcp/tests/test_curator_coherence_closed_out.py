"""MIK-R52 rule 10: status and validate after the closeout answer ``closed-out``.

A leaf whose closeout is completed and that published a coherence record answers state
``closed-out`` with the closeout's two commits and the record's digest. The answers compare
nothing with the live tree and say that the record was validated before the closeout. A leaf
without a record answers ``absent``, as today. Every case drives the production
``curator_coherence_action`` against a real external-memory leaf enclosure.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceAction,
    CuratorCoherenceRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    curator_coherence_paths,
    load_curator_coherence_authority,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from agents_remember.worktrees.route_review import build_route_review
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from curator_coherence_test_support import (
    write_curator_evidence,
    write_curator_task_topology,
)
from test_worktree_support import open_external_contract_fixture

pytestmark = pytest.mark.integration

REPO = "repo-a"


def _leaf_document(contract: WorktreeContract) -> TaskDocument:
    """Return the exact canonical leaf document the coherence route resolves for this contract."""

    slug = contract.leaf_id
    report = contract.task_root / "notes" / "reports" / f"{slug}-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Review\n\nPass.\n", encoding="utf-8")
    document = TaskDocument.model_validate(
        {
            "id": contract.leaf_id,
            "slug": slug,
            "title": slug,
            "kind": "subTask",
            "status": "inProgress",
            "repo": REPO,
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
    )
    review = build_route_review(
        contract,
        ResolvedTaskDocument(
            ref=TaskDocumentRef(repository=REPO, path=f"{contract.task_name}/{slug}.json"),
            path=contract.task_root / f"{slug}.json",
            document=document,
        ),
        {
            "verdict": "pass",
            "verdictRef": f"notes/reports/{slug}-review.md",
            "routes": [
                {
                    "route": slug,
                    "verdict": "pass",
                    "evidenceRef": f"notes/reports/{slug}-review.md",
                }
            ],
        },
        now=datetime.fromisoformat("2026-09-18T00:00:00+00:00"),
    )
    return document.model_copy(update={"routeReview": review})


def _published(contract: WorktreeContract) -> WorktreeContract:
    """One real enclosure with a published coherence authority."""

    sprint = write_curator_task_topology(contract)
    write_task_doc(contract.task_root, _leaf_document(contract))
    write_curator_evidence(contract, caller_ref=sprint)
    return contract


def _closed(contract: WorktreeContract) -> WorktreeContract:
    """The same leaf with its closeout cell completed at its two real heads."""

    code_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=contract.code_worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert contract.memory_worktree is not None
    memory_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=contract.memory_worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    closed = replace(
        contract,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
    )
    write_contract(closed.contract_path, closed)
    return load_contract(closed.contract_path)


def _action(contract: WorktreeContract, action: CuratorCoherenceAction) -> dict[str, Any]:
    result = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action=action, contract_path=contract.contract_path.as_posix()),
    )
    assert isinstance(result, dict)
    return result


def test_status_and_validate_answer_closed_out_after_the_closeout(tmp_path: Path) -> None:
    """Rule 10: completed closeout plus a record answers ``closed-out``, never ``stale``."""

    contract = _closed(_published(open_external_contract_fixture(tmp_path)))
    digest = load_curator_coherence_authority(contract).record_digest
    for action in ("status", "validate"):
        answer = _action(contract, action)
        assert answer["state"] == "closed-out"
        assert answer["codeCommit"] == contract.code_commit
        assert answer["memoryContentCommit"] == contract.memory_content_commit
        assert answer["recordDigest"] == digest
        assert "validated before the closeout" in answer["summary"]


def test_status_before_the_closeout_is_not_closed_out(tmp_path: Path) -> None:
    """Rule 10: the closed-out answer starts at the completed closeout, not at publication."""

    contract = _published(open_external_contract_fixture(tmp_path))
    assert _action(contract, "status")["state"] != "closed-out"


def test_a_completed_closeout_without_a_record_answers_absent(tmp_path: Path) -> None:
    """Rule 10: a leaf without a live record answers ``absent``, as today."""

    contract = _closed(_published(open_external_contract_fixture(tmp_path)))
    curator_coherence_paths(contract).canonical.unlink()
    assert _action(contract, "status")["state"] == "absent"
