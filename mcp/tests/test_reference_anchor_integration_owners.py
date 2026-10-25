"""R198 at actual integration owners, including a prior successful quality receipt.

The native checkpoint fixture supplies real enclosure and branch authority. Its tiny citation
records README's first line before only the containing blob changes. No publication gate is
patched: the stale operation must name only R198, and repairing that citation must publish.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
import test_closeout_queue as queue_fixture
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.reference_state import fix_references
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.modules import closeout
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import integrate_result
from agents_remember.worktrees.modules.record_landing import record_landing_result
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from checkpoint_landing_test_support import (
    branch_checkout,
    checkpoint,
    close_out_leaf,
    set_master_status,
)
from test_closeout_queue import (
    MASTER_B,
    QueueFixture,
    _publish_fixture_evidence,
    cover_fixture_code_changes,
)
from test_knowledge_closeout_gate import anchor_at, commit, git, write
from test_reference_anchor_owner_ledger import _effective
from test_reference_anchor_publication_owners import RULE, _rule_ids

SOURCE = "README.md"
SIDECAR = f"onboarding/{SOURCE}.json"


def _cite(leaf: WorktreeContract) -> None:
    assert leaf.memory_worktree is not None
    anchor = anchor_at(
        leaf.code_worktree,
        SOURCE,
        {"kind": "line_range", "start": 1, "end": 1},
        (leaf.code_worktree / SOURCE).read_bytes(),
    )
    write(
        leaf.memory_worktree,
        {
            f"onboarding/{SOURCE}.md": "# README\n\n## Evidence\n\n- The repository title. [1]\n",
            SIDECAR: canonical_text(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": SOURCE,
                    "references": {"1": {"targets": [{"kind": "code", "anchor": anchor}]}},
                    "realizes": [],
                }
            ),
        },
    )


def _integrate(leaf: WorktreeContract, *, dry_run: bool = False):
    return integrate_result(
        WorktreeArgs(
            contract_path=leaf.contract_path,
            approved=True,
            strategy="ff-only",
            dry_run=dry_run,
        ),
        leaf,
    )


def _refs(leaf: WorktreeContract) -> tuple[str, str, bytes]:
    assert leaf.memory_repo_path is not None
    return (
        git(leaf.code_repo_path, "for-each-ref"),
        git(leaf.memory_repo_path, "for-each-ref"),
        leaf.contract_path.read_bytes(),
    )


def test_leaf_integration_rejudges_the_pair_despite_an_old_full_quality_receipt(
    tmp_path: Path,
) -> None:
    """A persisted full-quality PASS predates the fault and cannot authorize this changed pair."""

    fixture = QueueFixture(tmp_path, atomic_b=True)
    leaf = fixture.contracts[MASTER_B]
    assert leaf.memory_worktree is not None
    _cite(leaf)
    quality = run_memory_quality_check(
        leaf.memory_worktree / "onboarding",
        drift_context=DriftCheckContext(leaf.code_worktree, context=None),
    )
    assert quality["ok"], quality
    assert "knowledge.converted" in quality["checks"], quality
    receipt = tmp_path / "old-full-quality-receipt.json"
    receipt.write_text(json.dumps(quality, sort_keys=True), encoding="utf-8")
    receipt_bytes = receipt.read_bytes()

    source = leaf.code_worktree / SOURCE
    source.write_text(
        source.read_text(encoding="utf-8") + "\nLater description.\n", encoding="utf-8"
    )
    cover_fixture_code_changes(leaf, SOURCE)
    closed = close_out_leaf(leaf)
    before = _refs(closed)

    for dry_run in (True, False):
        refused = _integrate(closed, dry_run=dry_run)
        assert refused.returncode == 2, refused.payload
        assert refused.payload["state"] == "knowledge-gate-refused", refused.payload
        assert _rule_ids(str(refused.payload["reason"])) == {RULE}, refused.payload
        assert _refs(closed) == before
        assert receipt.read_bytes() == receipt_bytes
        assert json.loads(receipt.read_text(encoding="utf-8"))["ok"] is True

    assert closed.memory_worktree is not None
    fixed = fix_references(closed.memory_worktree, closed.code_worktree)
    assert fixed["stale"] == [] and fixed["refreshedAnchors"] == 1, fixed
    repaired = commit(closed.memory_worktree, {}, trailer=closed.code_commit)
    closed = replace(closed, memory_content_commit=repaired)
    write_contract(closed.contract_path, closed)

    landed = _integrate(closed)

    assert (landed.returncode, landed.payload["state"]) == (0, "integrated"), landed.payload
    recorded = load_contract(closed.contract_path)
    assert recorded.integrated_memory_content_commit == repaired
    assert closed.memory_repo_path is not None
    assert git(closed.memory_repo_path, "rev-parse", closed.memory_source_branch) == repaired


def test_native_early_closeout_recovery_judges_the_immutable_output_without_preflight_shadowing(
    tmp_path: Path,
) -> None:
    """The entry's early recovery gate rejects the stale output even after the live tree is fixed."""

    fixture = QueueFixture(tmp_path, atomic_b=True)
    leaf = fixture.contracts[MASTER_B]
    _cite(leaf)
    source = leaf.code_worktree / SOURCE
    source.write_text(
        source.read_text(encoding="utf-8") + "\nLater description.\n", encoding="utf-8"
    )
    cover_fixture_code_changes(leaf, SOURCE)
    committed = close_out_leaf(leaf)
    assert committed.memory_worktree is not None
    # Fixture outputs exist, but their contract finalization has not happened: the state an
    # interrupted closeout resumes. All native enclosure/ref and recovery-output proofs stay live.
    recovering = replace(
        committed,
        approved_for_commit=False,
        closeout_status="not-started",
        code_commit="",
        memory_content_commit="",
    )
    write_contract(recovering.contract_path, recovering)

    def recover(memory: str):
        return closeout.closeout_result(
            WorktreeArgs(
                contract_path=recovering.contract_path,
                approved=True,
                approval_note="Resume the exact committed output",
                closeout_input=_effective(),
                recovery_commits=LifecycleOperationRecoveryCommits(
                    codeCommit=committed.code_commit,
                    memoryContentCommit=memory,
                ),
            ),
            recovering,
        )

    before = _refs(recovering)
    with (
        mock.patch.object(
            closeout, "require_gated_recovery", wraps=closeout.require_gated_recovery
        ) as exact_gate,
        mock.patch.object(
            closeout, "refuse_ungated_candidate", wraps=closeout.refuse_ungated_candidate
        ) as preflight,
    ):
        with pytest.raises(RuntimeError) as refused:
            recover(committed.memory_content_commit)
        assert _rule_ids(str(refused.value)) == {RULE}, str(refused.value)
        exact_gate.assert_called_once_with(
            recovering, committed.code_commit, committed.memory_content_commit
        )
        preflight.assert_not_called()
        assert _refs(recovering) == before

        fixed = fix_references(committed.memory_worktree, committed.code_worktree)
        assert fixed["stale"] == [] and fixed["refreshedAnchors"] == 1, fixed
        # The checkout now passes, but the old immutable recovered commit is still stale.
        with pytest.raises(RuntimeError) as still_refused:
            recover(committed.memory_content_commit)
        assert _rule_ids(str(still_refused.value)) == {RULE}, str(still_refused.value)
        preflight.assert_not_called()
        assert _refs(recovering) == before

        repaired = commit(committed.memory_worktree, {}, trailer=committed.code_commit)
        recovered = recover(repaired)
        preflight.assert_not_called()

    assert (recovered.returncode, recovered.payload["state"]) == (0, "closed"), recovered.payload
    assert recovered.payload["recovered"] is True, recovered.payload
    stored = load_contract(recovering.contract_path)
    assert stored.memory_content_commit == repaired
    assert stored.closeout_status == "completed"
    assert git(committed.memory_worktree, "rev-list", "--count", f"{repaired}..HEAD") == "0"


def _stale_series(tmp_path: Path) -> tuple[QueueFixture, WorktreeContract]:
    """A native leaf lands current; a real source sync then makes its preserved citation stale."""

    fixture = QueueFixture(tmp_path, atomic_b=True)
    leaf = fixture.contracts[MASTER_B]
    _cite(leaf)
    closed = close_out_leaf(leaf)
    landed = _integrate(closed)
    assert landed.payload["state"] == "integrated", landed.payload
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    with branch_checkout(
        series.code_repo_path, series.code_source_branch, tmp_path / "source-code"
    ) as code:
        source = code / SOURCE
        changed_code = commit(
            code, {SOURCE: source.read_text(encoding="utf-8") + "\nLater description.\n"}
        )
    with branch_checkout(
        series.memory_repo_path, series.memory_source_branch, tmp_path / "source-memory"
    ) as memory:
        commit(memory, {"source.md": "# Source line\n"}, trailer=changed_code)
    synced = sync_result(
        WorktreeArgs(contract_path=series.contract_path, memory_sync_choice="merge-memory")
    )
    assert synced.payload["state"] == "synced", synced.payload
    debt = synced.payload.get("memoryRepairDebt")
    assert isinstance(debt, dict), synced.payload
    assert RULE in debt["rules"], synced.payload
    return fixture, load_contract(series.contract_path)


def _repair_series(fixture: QueueFixture) -> WorktreeContract:
    """A new leaf fixes the inherited citation and lands through the unchanged native owner."""

    fixture.author_unstarted_leaf("master-b", "LEAF-REPAIR")

    def repair_then_attest(repair: WorktreeContract) -> None:
        # start_leaf is a test-world builder that immediately attests its candidate. This repair
        # leaf inherits deliberate debt, so author the real fix before its real attestation.
        assert repair.memory_worktree is not None
        fixed = fix_references(repair.memory_worktree, repair.code_worktree)
        assert fixed["stale"] == [] and fixed["refreshedAnchors"] == 1, fixed
        _publish_fixture_evidence(repair)

    with mock.patch.object(
        queue_fixture, "_publish_fixture_evidence", side_effect=repair_then_attest
    ):
        repair = fixture.start_leaf("LEAF-REPAIR", master="master-b")
    closed = close_out_leaf(repair)
    landed = _integrate(closed)
    assert (landed.returncode, landed.payload["state"]) == (0, "integrated"), landed.payload
    return load_contract(fixture.tasks / "master-b" / "series-contract.md")


def _final_contract(series: WorktreeContract) -> WorktreeContract:
    """The fixture's completed master with its real current code/memory output refs."""

    assert series.memory_repo_path is not None
    set_master_status(series, status="Completed", row_status="Completed")
    closed = replace(
        series,
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=git(series.code_repo_path, "rev-parse", series.code_work_branch),
        memory_content_commit=git(series.memory_repo_path, "rev-parse", series.memory_work_branch),
    )
    write_contract(closed.contract_path, closed)
    return closed


@pytest.mark.parametrize("owner", ["checkpoint", "final", "recorded-series"])
def test_series_publication_refuses_synced_anchor_debt_and_publishes_a_real_leaf_repair(
    tmp_path: Path, owner: str
) -> None:
    """Each series entry sees the actual sync-created stale pair and its native leaf repair."""

    fixture, series = _stale_series(tmp_path)
    if owner == "final":
        series = _final_contract(series)
    before = _refs(series)

    def publish(contract: WorktreeContract, *, dry_run: bool):
        if owner == "checkpoint":
            return checkpoint(fixture, contract, dry_run=dry_run)
        if owner == "final":
            return _integrate(contract, dry_run=dry_run).payload
        assert contract.memory_repo_path is not None
        code = git(contract.code_repo_path, "rev-parse", contract.code_work_branch)
        memory = git(contract.memory_repo_path, "rev-parse", contract.memory_work_branch)
        # The pull-request operation receives immutable landed commits. The source branch has
        # already landed only the source side of the sync; main is its legitimate landing target.
        git(contract.code_repo_path, "update-ref", "refs/heads/main", code)
        return record_landing_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=True,
                dry_run=dry_run,
                landed_code_commit=code,
                landed_memory_content_commit=memory,
            )
        ).payload

    if owner == "recorded-series":
        assert series.memory_repo_path is not None
        git(
            series.code_repo_path,
            "update-ref",
            "refs/heads/main",
            git(series.code_repo_path, "rev-parse", series.code_work_branch),
        )
        before = _refs(series)
    for dry_run in (True, False):
        refused = publish(series, dry_run=dry_run)
        assert refused["state"] in {"knowledge-gate-refused", "refused"}, refused
        detail = str(refused.get("reason") or refused.get("detail"))
        assert _rule_ids(detail) == {RULE}, detail
        assert _refs(series) == before

    # Final completion is a fixture input; the repair is a fresh child while the master is open.
    if owner == "final":
        set_master_status(series, status="inProgress", row_status="inProgress")
    repaired = _repair_series(fixture)
    if owner == "final":
        repaired = _final_contract(repaired)
    result = publish(repaired, dry_run=False)

    expected = {"checkpoint": "checkpointed", "final": "integrated", "recorded-series": "recorded"}
    assert result["state"] == expected[owner], result
    stored = load_contract(repaired.contract_path)
    assert stored.integrated_code_commit == git(
        repaired.code_repo_path, "rev-parse", repaired.code_work_branch
    )
    assert repaired.memory_repo_path is not None
    assert stored.integrated_memory_content_commit == git(
        repaired.memory_repo_path, "rev-parse", repaired.memory_work_branch
    )
