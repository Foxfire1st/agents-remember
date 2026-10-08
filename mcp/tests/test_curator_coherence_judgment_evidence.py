"""Judgment evidence custody: retaining admitted bytes at publication and reading them back later.

``retain_judgment_evidence`` copies the bytes a judgment cited into the task's content-addressed
``judgment-evidence`` folder and stamps the judgment with that custody; ``read_judgment_evidence``
is what the record reader uses afterwards, when the cited code or memory file may be long gone. The
fixture is a scratch leaf contract and real files under its task root; no Git repository is needed
because the evidence namespaces are plain files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRecordedJudgment
from agents_remember.models.lifecycles.review_assessment import AssessmentEvidenceByte
from agents_remember.worktrees.integration.closeout.curator_coherence_judgments import (
    read_judgment_evidence,
    retain_judgment_evidence,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_paths import (
    curator_coherence_paths,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

pytestmark = pytest.mark.evidence_unit

REPO = "agents-remember"
MASTER = "260101_evidence"
LEAF = "260101-EVD-L1"
CITED = "task:notes/reports/cited-evidence.md"
BYTES = b"The cited evidence, exactly as the curator read it.\n"


def _contract(root: Path) -> WorktreeContract:
    task_root = root / "coordination" / "tasks" / REPO / MASTER
    group = root / "group"
    for folder in (task_root, group / "code", group / "memory"):
        folder.mkdir(parents=True, exist_ok=True)
    path = task_root / "series-contract.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "schema: ar-series-contract/v1",
                "schemaVersion: 1.0",
                "kind: leaf",
                "task_id: 260101_EVIDENCE",
                f"task_name: {MASTER}",
                f"repo_name: {REPO}",
                "workflow_kind: light-task",
                "memory_mode: external",
                "",
                "coordination:",
                f"  root: {root / 'coordination'}",
                f"  task_root: {task_root}",
                f"  task_artifact: {task_root / 'task.md'}",
                f"  worktree_group: {group}",
                f"  leaf_id: {LEAF}",
                f"  parent_task_name: {MASTER}",
                "",
                "code:",
                f"  repo_path: {root / 'code'}",
                "  source_branch: main",
                "  work_branch: leaf",
                f"  base_commit: {'a' * 40}",
                f"  worktree: {group / 'code'}",
                "",
                "memory:",
                "  mode: external",
                f"  repo_path: {root / 'memory'}",
                "  source_branch: main",
                "  work_branch: leaf",
                f"  base_commit: {'b' * 40}",
                f"  worktree: {group / 'memory'}",
                f"  ledger: {group / 'memory' / 'memory.md'}",
                "---",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return load_contract(path)


def _judgment(
    reference: str = CITED, data: bytes = BYTES, **fields: Any
) -> CuratorCoherenceRecordedJudgment:
    return CuratorCoherenceRecordedJudgment(
        sourceFile="mcp/src/pkg/a.py",
        onboardingFile="onboarding/mcp/src/pkg/a.py.md",
        classification="verified-current",
        disposition="preserved",
        rationale="The card still describes the module.",
        evidenceRef=reference,
        evidenceSha256=hashlib.sha256(data).hexdigest(),
        **fields,
    )


def _cite(contract: WorktreeContract, data: bytes = BYTES) -> Path:
    cited = contract.task_root / "notes" / "reports" / "cited-evidence.md"
    cited.parent.mkdir(parents=True, exist_ok=True)
    cited.write_bytes(data)
    return cited


def _address(contract: WorktreeContract, data: bytes = BYTES) -> Path:
    return curator_coherence_paths(contract).judgment_evidence(hashlib.sha256(data).hexdigest())


def test_retained_evidence_survives_its_source_and_reads_back_from_custody(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    cited = _cite(contract)

    (stamped,) = retain_judgment_evidence(contract, [_judgment()])

    address = _address(contract)
    digest = hashlib.sha256(BYTES).hexdigest()
    assert address.read_bytes() == BYTES
    assert stamped.evidenceRef == CITED
    assert stamped.evidenceArtifact == AssessmentEvidenceByte(
        path=address.relative_to(contract.task_root).as_posix(), sha256=digest, size=len(BYTES)
    )
    # Retaining the same judgment again converges on the same custody.
    assert retain_judgment_evidence(contract, [_judgment()]) == [stamped]

    cited.unlink()
    fact = read_judgment_evidence(contract, stamped)
    assert (fact.path, fact.sha256) == (address.resolve().as_posix(), digest)


def test_evidence_that_moved_before_custody_is_refused_and_nothing_is_retained(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    _cite(contract, b"The bytes the judgment was not written against.\n")

    with pytest.raises(CuratorCoherenceError) as refused:
        retain_judgment_evidence(contract, [_judgment()])

    assert refused.value.status == "curator-coherence-evidence-raced"
    assert not _address(contract).exists()


def test_a_content_address_holding_other_bytes_is_never_overwritten(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    _cite(contract)
    address = _address(contract)
    address.parent.mkdir(parents=True)
    address.write_bytes(b"Different bytes already sit at this address.\n")

    with pytest.raises(CuratorCoherenceError) as refused:
        retain_judgment_evidence(contract, [_judgment()])

    assert refused.value.status == "curator-coherence-content-address-collision"
    assert address.read_bytes() == b"Different bytes already sit at this address.\n"


@pytest.mark.parametrize(
    ("damage", "status"),
    [
        ("truncate", "review-assessment-evidence-read-back-mismatch"),
        ("remove", "review-assessment-evidence-read-back-absent"),
    ],
)
def test_damaged_custody_is_refused_on_read_back(tmp_path: Path, damage: str, status: str) -> None:
    contract = _contract(tmp_path)
    _cite(contract)
    (stamped,) = retain_judgment_evidence(contract, [_judgment()])
    address = _address(contract)

    if damage == "truncate":
        address.write_bytes(BYTES[: len(BYTES) // 2])
    else:
        address.unlink()

    with pytest.raises(CuratorCoherenceError) as refused:
        read_judgment_evidence(contract, stamped)
    assert refused.value.status == status


def test_custody_that_names_another_artifact_is_refused(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    _cite(contract)
    (stamped,) = retain_judgment_evidence(contract, [_judgment()])
    other = b"Other retained bytes.\n"
    elsewhere = _address(contract, other)
    elsewhere.write_bytes(other)
    forged = stamped.model_copy(
        update={
            "evidenceArtifact": AssessmentEvidenceByte(
                path=elsewhere.relative_to(contract.task_root).as_posix(),
                sha256=stamped.evidenceSha256,
                size=len(other),
            )
        }
    )

    with pytest.raises(CuratorCoherenceError) as refused:
        read_judgment_evidence(contract, forged)
    assert refused.value.status == "curator-coherence-judgment-artifact-invalid"


def test_an_unretained_judgment_reads_only_a_current_task_citation(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    cited = _cite(contract)

    current = read_judgment_evidence(contract, _judgment())
    assert (current.path, current.sha256) == (
        cited.resolve().as_posix(),
        hashlib.sha256(BYTES).hexdigest(),
    )

    cited.write_bytes(b"Edited after the judgment was recorded.\n")
    with pytest.raises(CuratorCoherenceError) as stale:
        read_judgment_evidence(contract, _judgment())
    assert stale.value.status == "curator-coherence-evidence-stale"

    # A historical code or memory citation is never re-read from the live trees.
    (contract.code_worktree / "evidence.md").write_bytes(BYTES)
    with pytest.raises(CuratorCoherenceError) as unretained:
        read_judgment_evidence(contract, _judgment("code:evidence.md"))
    assert unretained.value.status == "curator-coherence-judgment-not-retained"
