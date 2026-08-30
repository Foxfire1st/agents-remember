"""Exact contract-owned code/memory pairing for acceptance-grade memory quality."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import memory_scope as memory_scope_module
from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
)
from agents_remember.application.memory_quality import controller
from agents_remember.errors import (
    CuratorCoherencePairError,
    MemoryCandidatePairError,
    MemoryCandidatePairFailure,
)
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.worktrees.integration.closeout import memory_candidate_pair as pair_module
from agents_remember.worktrees.integration.closeout.memory_candidate_pair import (
    resolve_memory_candidate_pair,
)
from agents_remember.worktrees.integration.closeout.memory_candidate_pairing import (
    resolve_closeout_memory_pair,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_closeout_queue import MASTER_A, MASTER_B, QueueFixture
from test_worktree_support import git


def _resolve(contract: WorktreeContract) -> MemoryCandidatePairIdentity:
    return resolve_memory_candidate_pair(
        contract,
        requested_contract_path=contract.contract_path,
        requested_repo_id=contract.repo_name,
    )


def test_two_leaf_checkouts_produce_distinct_exact_pair_identities(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract_a = load_contract(fixture.contracts[MASTER_A].contract_path)
    contract_b = load_contract(fixture.contracts[MASTER_B].contract_path)

    pair_a = _resolve(contract_a)
    pair_b = _resolve(contract_b)

    assert contract_a.memory_worktree is not None
    assert pair_a.codeRoot == contract_a.code_worktree.resolve().as_posix()
    assert pair_a.memoryRoot == contract_a.memory_worktree.resolve().as_posix()
    assert pair_a.codeWorkBranch == contract_a.code_work_branch
    assert pair_a.memoryWorkBranch == contract_a.memory_work_branch
    assert pair_a.codeBaseCommit == contract_a.code_base_commit
    assert pair_a.memoryBaseCommit == contract_a.memory_base_commit
    assert pair_a != pair_b
    assert pair_a.contractDigest != pair_b.contractDigest


def test_valid_but_wrong_checkout_is_refused_before_it_can_be_a_candidate(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract_a = load_contract(fixture.contracts[MASTER_A].contract_path)
    contract_b = load_contract(fixture.contracts[MASTER_B].contract_path)
    wrong = replace(contract_a, code_worktree=contract_b.code_worktree)
    write_contract(wrong.contract_path, wrong)
    before_a = git(contract_a.code_worktree, "rev-parse", "HEAD")
    before_b = git(contract_b.code_worktree, "rev-parse", "HEAD")

    with pytest.raises(MemoryCandidatePairError) as raised:
        _resolve(wrong)

    assert raised.value.status == "memory-candidate-pair-branch-mismatch"
    assert raised.value.field == "codeWorkBranch"
    assert raised.value.expected == {"branch": contract_a.code_work_branch}
    assert raised.value.observed == {"branch": contract_b.code_work_branch}
    assert git(contract_a.code_worktree, "rev-parse", "HEAD") == before_a
    assert git(contract_b.code_worktree, "rev-parse", "HEAD") == before_b

    with (
        mock.patch.object(
            controller,
            "resolve_memory_candidate_scope",
            side_effect=lambda *_args, **_kwargs: _resolve(wrong),
        ),
        mock.patch.object(controller, "run_memory_quality_check") as scan,
    ):
        response = controller.run_memory_quality_request(
            mock.Mock(),
            MemoryQualitySyncRequest(
                mode="sync",
                repo_id=wrong.repo_name,
                contract_path=wrong.contract_path.as_posix(),
            ),
        )

    assert response["status"] == "scope-refused"
    assert response["pairField"] == "codeWorkBranch"
    scan.assert_not_called()


def test_source_branch_advance_requires_sync_instead_of_reusing_old_base(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)
    source_worktree = tmp_path / "advanced-super"
    git(fixture.code, "worktree", "add", str(source_worktree), contract.code_source_branch)
    (source_worktree / "advanced.txt").write_text("moved\n", encoding="utf-8")
    git(source_worktree, "add", "advanced.txt")
    git(source_worktree, "commit", "-m", "advance source")

    with pytest.raises(MemoryCandidatePairError) as raised:
        _resolve(contract)

    assert raised.value.status == "memory-candidate-pair-base-stale"
    assert raised.value.field == "codeBaseCommit"
    assert raised.value.next_action == "worktree_sync"
    assert raised.value.next_args == {
        "contract_path": contract.contract_path.resolve().as_posix(),
        "dry_run": True,
    }


def test_unrelated_lifecycle_cells_do_not_change_the_pair_digest(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)
    initial = _resolve(contract)
    approved = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        commit_approval_note="approved without changing pair authority",
    )
    write_contract(approved.contract_path, approved)

    current = _resolve(approved)

    assert current == initial


def test_closeout_recovery_rereads_the_exact_contract_pair(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)

    recovered = resolve_closeout_memory_pair(contract)

    assert recovered == _resolve(contract)
    assert recovered is not None
    assert recovered.contractPath == contract.contract_path.resolve().as_posix()


def test_pair_refusal_is_typed_and_prevents_memory_scanning() -> None:
    error = MemoryCandidatePairError(
        "memory-candidate-pair-contract-stale",
        "the leaf contract changed before scanning",
        failure=MemoryCandidatePairFailure(
            field="contractPath",
            contract_path="/coordination/contract.md",
            expected={"state": "unchanged-since-admission"},
            observed={"state": "changed"},
            next_action="worktree_sync",
            next_args={"contract_path": "/coordination/contract.md", "dry_run": True},
        ),
    )
    with (
        mock.patch.object(
            controller,
            "resolve_memory_candidate_scope",
            side_effect=error,
        ),
        mock.patch.object(controller, "run_memory_quality_check") as scan,
    ):
        response = controller.run_memory_quality_request(
            mock.Mock(),
            MemoryQualitySyncRequest(
                mode="sync",
                repo_id="repo",
                contract_path="/coordination/contract.md",
            ),
        )

    assert response == {
        "ok": False,
        "operation": "memory_quality_check",
        "repoId": "repo",
        "status": "scope-refused",
        "pairStatus": "memory-candidate-pair-contract-stale",
        "pairField": "contractPath",
        "contractPath": "/coordination/contract.md",
        "detail": "the leaf contract changed before scanning",
        "nextAction": "worktree_sync",
        "expected": {"state": "unchanged-since-admission"},
        "observed": {"state": "changed"},
        "nextArgs": {"contract_path": "/coordination/contract.md", "dry_run": True},
    }
    scan.assert_not_called()


def test_pair_refusal_optional_evidence_and_coherence_translation_are_total() -> None:
    minimal = MemoryCandidatePairError(
        "memory-candidate-pair-field-missing",
        "field missing",
        failure=MemoryCandidatePairFailure(
            field="memoryRoot",
            contract_path="/contract",
        ),
    )
    assert minimal.response_fields() == {
        "pairStatus": "memory-candidate-pair-field-missing",
        "pairField": "memoryRoot",
        "contractPath": "/contract",
        "detail": "field missing",
        "nextAction": "developer-decision",
    }

    detailed = MemoryCandidatePairError(
        "memory-candidate-pair-base-stale",
        "sync required",
        failure=MemoryCandidatePairFailure(
            field="codeBaseCommit",
            contract_path="/contract",
            next_args={"contract_path": "/contract", "dry_run": True},
        ),
    )
    coherence = CuratorCoherencePairError(detailed)
    assert coherence.response_fields()["nextArgs"] == {
        "contract_path": "/contract",
        "dry_run": True,
    }
    assert "nextArgs" not in CuratorCoherencePairError(minimal).response_fields()


def test_configured_refusal_is_translated_to_one_strict_pair_refusal() -> None:
    refusal = ConfiguredContractRefused(
        reason="location-invalid",
        status="operation-location-mismatch",
        detail="configured locator and enclosure manifest disagree",
        expected={"state": "addressable"},
        observed={"state": "mismatch"},
    )
    with (
        mock.patch.object(memory_scope_module, "require_repo", return_value=mock.Mock()),
        mock.patch.object(
            memory_scope_module,
            "admit_configured_contract",
            return_value=refusal,
        ),
        pytest.raises(MemoryCandidatePairError) as raised,
    ):
        memory_scope_module.resolve_memory_candidate_scope(
            mock.Mock(),
            repo_id="repo",
            contract_path="/coordination/contract.md",
        )

    assert raised.value.status == "operation-location-mismatch"
    assert raised.value.detail == refusal.detail
    assert raised.value.expected == refusal.expected
    assert raised.value.observed == refusal.observed
    assert raised.value.next_action == "developer-decision"


def test_requested_address_and_repository_must_match_the_contract(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)

    with pytest.raises(MemoryCandidatePairError) as wrong_path:
        resolve_memory_candidate_pair(
            contract,
            requested_contract_path=contract.contract_path.parent / "other.md",
        )
    assert wrong_path.value.status == "memory-candidate-pair-contract-mismatch"

    with pytest.raises(MemoryCandidatePairError) as wrong_repo:
        resolve_memory_candidate_pair(contract, requested_repo_id="other-repo")
    assert wrong_repo.value.status == "memory-candidate-pair-repository-mismatch"


def test_unreadable_and_changed_contracts_are_typed_pair_refusals(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)
    with (
        mock.patch.object(pair_module, "load_contract", side_effect=OSError("unreadable")),
        pytest.raises(MemoryCandidatePairError) as unreadable,
    ):
        _resolve(contract)
    assert unreadable.value.status == "memory-candidate-pair-contract-unreadable"
    assert unreadable.value.observed == {"errorType": "OSError"}

    changed = replace(contract, commit_approval_note="changed after admission")
    write_contract(changed.contract_path, changed)
    with pytest.raises(MemoryCandidatePairError) as stale:
        _resolve(contract)
    assert stale.value.status == "memory-candidate-pair-contract-stale"


@pytest.mark.parametrize(
    ("changes", "status", "field"),
    [
        ({"kind": "series"}, "memory-candidate-pair-contract-kind-invalid", "kind"),
        ({"memory_mode": "internal"}, "memory-candidate-pair-memory-mode-invalid", "memoryMode"),
        ({"memory_worktree": None}, "memory-candidate-pair-field-missing", "memoryRoot"),
        ({"ledger_path": None}, "memory-candidate-pair-field-missing", "ledgerPath"),
    ],
)
def test_contract_shape_failures_name_the_exact_field(
    tmp_path: Path,
    changes: dict[str, object],
    status: str,
    field: str,
) -> None:
    fixture = QueueFixture(tmp_path)
    contract = replace(load_contract(fixture.contracts[MASTER_A].contract_path), **changes)

    with pytest.raises(MemoryCandidatePairError) as raised:
        _resolve(contract)

    assert raised.value.status == status
    assert raised.value.field == field


def test_paths_and_repository_membership_are_reproved_before_scanning(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)
    assert contract.memory_worktree is not None

    wrong_ledger = contract.memory_worktree / "other-ledger.md"
    wrong_ledger.write_text("# wrong ledger\n", encoding="utf-8")
    mismatched = replace(contract, ledger_path=wrong_ledger)
    write_contract(mismatched.contract_path, mismatched)
    with pytest.raises(MemoryCandidatePairError) as ledger:
        _resolve(mismatched)
    assert ledger.value.status == "memory-candidate-pair-path-mismatch"
    assert ledger.value.field == "ledgerPath"

    write_contract(contract.contract_path, contract)
    with (
        mock.patch.object(pair_module, "repository_identity", side_effect=[None, None, "m", "m"]),
        pytest.raises(MemoryCandidatePairError) as code_repo,
    ):
        _resolve(contract)
    assert code_repo.value.field == "codeRoot"

    with (
        mock.patch.object(pair_module, "repository_identity", side_effect=["c", "c", "m", "x"]),
        pytest.raises(MemoryCandidatePairError) as memory_repo,
    ):
        _resolve(contract)
    assert memory_repo.value.field == "memoryRoot"

    with (
        mock.patch.object(pair_module, "repository_identity", side_effect=["same"] * 4),
        pytest.raises(MemoryCandidatePairError) as separation,
    ):
        _resolve(contract)
    assert "distinct repositories" in separation.value.detail


def test_path_kind_branch_read_and_ancestry_failures_are_typed(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.md"
    missing = tmp_path / "missing"
    with pytest.raises(MemoryCandidatePairError) as unavailable:
        pair_module._require_path(
            missing,
            "memoryRoot",
            kind="directory",
            contract_path=contract_path,
        )
    assert unavailable.value.status == "memory-candidate-pair-path-unavailable"

    plan = pair_module._BranchPlan(
        side="code",
        repository=tmp_path,
        worktree=tmp_path,
        source_branch="",
        work_branch="leaf",
        base_commit="a" * 40,
        accepted_source_heads=frozenset({"a" * 40}),
    )
    with pytest.raises(MemoryCandidatePairError) as missing_branch:
        pair_module._require_branch_plan(plan, contract_path=contract_path)
    assert missing_branch.value.field == "codeSourceBranch"

    readable = replace(plan, source_branch="super")
    with (
        mock.patch.object(pair_module, "current_branch", side_effect=RuntimeError("git failed")),
        pytest.raises(MemoryCandidatePairError) as unreadable,
    ):
        pair_module._require_branch_plan(readable, contract_path=contract_path)
    assert unreadable.value.status == "memory-candidate-pair-branch-unreadable"

    with (
        mock.patch.object(pair_module, "current_branch", return_value="leaf"),
        mock.patch.object(pair_module, "branch_commit", side_effect=["a" * 40, "b" * 40]),
        mock.patch.object(pair_module, "is_ancestor", return_value=False),
        pytest.raises(MemoryCandidatePairError) as contradictory,
    ):
        pair_module._require_branch_plan(readable, contract_path=contract_path)
    assert contradictory.value.status == "memory-candidate-pair-base-contradictory"


def test_completed_integration_head_is_valid_for_memory_only_recloseout(tmp_path: Path) -> None:
    fixture = QueueFixture(tmp_path)
    contract = load_contract(fixture.contracts[MASTER_A].contract_path)
    source_worktree = tmp_path / "landed-super"
    git(fixture.code, "worktree", "add", str(source_worktree), contract.code_source_branch)
    (source_worktree / "landed.txt").write_text("landed\n", encoding="utf-8")
    git(source_worktree, "add", "landed.txt")
    git(source_worktree, "commit", "-m", "land exact code candidate")
    integrated_code = git(source_worktree, "rev-parse", "HEAD")
    landed = replace(
        contract,
        integration_status="completed",
        integrated_code_commit=integrated_code,
    )
    write_contract(landed.contract_path, landed)

    resolved = _resolve(landed)

    assert resolved.codeBaseCommit == contract.code_base_commit
    accepted = pair_module._accepted_source_heads(
        contract.code_base_commit,
        integrated_code,
        integration_completed=True,
    )
    assert accepted == frozenset({contract.code_base_commit, integrated_code})
    assert pair_module._expected_source_head(
        pair_module._BranchPlan(
            side="code",
            repository=fixture.code,
            worktree=contract.code_worktree,
            source_branch=contract.code_source_branch,
            work_branch=contract.code_work_branch,
            base_commit=contract.code_base_commit,
            accepted_source_heads=accepted,
        )
    ) == {"sourceCommitOneOf": sorted(accepted)}
