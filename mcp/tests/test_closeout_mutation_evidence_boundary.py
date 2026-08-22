"""L1 mutation authority and Git reconciliation boundaries."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import lifecycle_operation_worker
from agents_remember.kernel.git_command import run_git
from agents_remember.models.lifecycles.mutation_evidence import CloseoutMutationLeg
from agents_remember.models.lifecycles.operation import CloseoutOperationInput
from agents_remember.worktrees.closeout_input import capture_closeout_candidate
from agents_remember.worktrees.integration import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    JOURNALED_CLOSEOUT_REQUIRED,
    begin_git_mutation,
    bind_expected_output_tree,
    closeout_cancellable,
    prove_git_commit,
    reconcile_closeout_mutations,
)
from agents_remember.worktrees.modules import cli as worktree_cli
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import load_contract
from closeout_fixture_test_support import selected_fixture
from closeout_input_test_support import (
    MutationEvidenceRecorder,
    closeout_operation_input,
    closeout_worktree_args,
    start_closeout_operation,
)
from test_closeout_queue import MASTER_A
from test_worktree_support import git


def test_direct_closeout_apply_without_journal_authority_refuses_before_route_or_git(
    tmp_path: Path,
) -> None:
    fixture = selected_fixture(tmp_path, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    args = closeout_worktree_args(
        contract,
        approved=True,
        approval_note="approved",
    )

    with (
        mock.patch.object(closeout_module, "_closeout_contract") as contract_route,
        pytest.raises(RuntimeError, match="journaled worktree_closeout_apply operation") as raised,
    ):
        closeout_module.closeout_result(args)

    assert str(raised.value) == JOURNALED_CLOSEOUT_REQUIRED
    contract_route.assert_not_called()


def test_generic_lifecycle_start_cannot_bypass_raw_closeout_admission(tmp_path: Path) -> None:
    fixture = selected_fixture(tmp_path, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    operation_input = closeout_operation_input(contract, config_path=fixture.config_path)
    launcher = mock.Mock()

    with pytest.raises(RuntimeError, match="lease-bound raw-input admission"):
        lifecycle_operations.start_or_observe_operation(
            operation_input,
            launcher=launcher,
        )

    launcher.assert_not_called()
    assert not operation_record_path(contract.worktree_group, "closeout").exists()


def test_lease_bound_closeout_start_supplies_its_resolved_candidate(tmp_path: Path) -> None:
    fixture = selected_fixture(tmp_path, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    operation_input = closeout_operation_input(contract, config_path=fixture.config_path)
    expected_tree = capture_closeout_candidate(contract).candidate_tree

    projection = start_closeout_operation(operation_input, launcher=lambda *_: None)

    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "closeout")
    ).read()
    assert projection.status == "queued"
    assert record is not None
    assert record.candidateTree == expected_tree


def test_stale_unchanged_intent_observes_attempt_one_without_relaunch(tmp_path: Path) -> None:
    contract, operation_input, store, runtime = _running_code_operation(tmp_path)
    begin_git_mutation(
        WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=operation_input.effectiveInput,
            operation_progress=runtime.progress,
        ),
        leg="code",
        repository=contract.code_worktree,
        expected_output_tree=None,
        use_current_candidate=True,
    )
    store.update(
        lambda record: record.model_copy(
            update={"heartbeatAt": "2026-08-22T00:00:00+00:00", "workerPid": None}
        )
    )
    launcher = mock.Mock()

    projection = start_closeout_operation(
        operation_input,
        launcher=launcher,
        now=datetime(2026, 8, 22, 1, 0, tzinfo=UTC),
    )

    reconciled = store.read()
    assert reconciled is not None
    assert reconciled.mutationEvidence["code"].state == "reconciled-unchanged"
    assert reconciled.attempt == 1
    launcher.assert_not_called()
    assert projection.cancellable is True


def test_git_mutation_status_failure_has_no_durable_progress(tmp_path: Path) -> None:
    contract, operation_input, store, runtime = _running_code_operation(tmp_path)
    git_marker = contract.code_worktree / ".git"
    git_marker.rename(contract.code_worktree / ".git-disabled")
    status = run_git(contract.code_worktree, ["status", "--porcelain=v1", "-z"])
    assert status.returncode != 0 and status.stderr.strip()
    before = store.path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        begin_git_mutation(
            WorktreeArgs(
                contract_path=contract.contract_path,
                closeout_input=operation_input.effectiveInput,
                operation_progress=runtime.progress,
            ),
            leg="code",
            repository=contract.code_worktree,
            expected_output_tree=None,
            use_current_candidate=True,
        )

    assert str(raised.value) == status.stderr.strip()
    assert store.path.read_bytes() == before
    current = store.read()
    assert current is not None
    assert current.mutationEvidence["code"].state == "pre-mutation"


def test_git_mutation_ref_log_failure_has_no_durable_progress(tmp_path: Path) -> None:
    contract, operation_input, store, runtime = _running_code_operation(tmp_path)
    head_ref = git(contract.code_worktree, "symbolic-ref", "--quiet", "HEAD")
    ref_log = Path(
        git(
            contract.code_worktree,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            f"logs/{head_ref}",
        )
    )
    ref_log.unlink()
    before = store.path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        begin_git_mutation(
            WorktreeArgs(
                contract_path=contract.contract_path,
                closeout_input=operation_input.effectiveInput,
                operation_progress=runtime.progress,
            ),
            leg="code",
            repository=contract.code_worktree,
            expected_output_tree=None,
            use_current_candidate=True,
        )

    assert str(raised.value) == "could not read Git ref-log evidence"
    assert store.path.read_bytes() == before
    current = store.read()
    assert current is not None
    assert current.mutationEvidence["code"].state == "pre-mutation"


def test_legacy_cli_apply_cannot_bypass_the_journaled_operation(tmp_path: Path) -> None:
    fixture = selected_fixture(tmp_path, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    args = Namespace(
        contract_path=contract.contract_path,
        approved=True,
        approval_note="approved",
        code_commit_message="code",
        memory_commit_message="memory",
        ledger_commit_message="ledger",
        dry_run=False,
        operation_progress=MutationEvidenceRecorder(),
    )

    with pytest.raises(RuntimeError) as raised:
        worktree_cli.command_closeout(args)

    assert str(raised.value) == JOURNALED_CLOSEOUT_REQUIRED


def test_git_mutation_helper_cannot_silently_run_without_evidence_authority() -> None:
    with pytest.raises(RuntimeError) as raised:
        begin_git_mutation(
            WorktreeArgs(contract_path=Path("/coordination/contract.md")),
            leg="code",
            repository=Path("/repository"),
            expected_output_tree="a" * 40,
        )

    assert str(raised.value) == JOURNALED_CLOSEOUT_REQUIRED


@pytest.mark.parametrize("case", ["disabled", "missing-contract", "foreign-repository"])
def test_git_mutation_authority_refuses_before_snapshot(
    tmp_path: Path,
    case: str,
) -> None:
    fixture = selected_fixture(
        tmp_path,
        memory_mode="internal" if case == "disabled" else "external",
    )
    contract = fixture.contracts[MASTER_A]
    if case != "disabled":
        (contract.code_worktree / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    operation_input = closeout_operation_input(contract, config_path=fixture.config_path)
    leg: CloseoutMutationLeg = "memory" if case == "disabled" else "code"
    repository = contract.code_worktree
    contract_path = contract.contract_path
    if case == "missing-contract":
        contract_path = None
    elif case == "foreign-repository":
        repository = tmp_path / "foreign"
    args = WorktreeArgs(
        contract_path=contract_path,
        closeout_input=operation_input.effectiveInput,
        operation_progress=MutationEvidenceRecorder(),
    )

    expected = {
        "disabled": "closeout memory mutation leg is not enabled",
        "missing-contract": "closeout mutation evidence requires a contract path",
        "foreign-repository": "closeout code mutation repository is outside contract authority",
    }[case]
    with pytest.raises(RuntimeError) as raised:
        begin_git_mutation(
            args,
            leg=leg,
            repository=repository,
            expected_output_tree=None,
        )

    assert str(raised.value) == expected


def test_bind_and_proof_refuse_changed_or_incomplete_intent(tmp_path: Path) -> None:
    fixture = selected_fixture(tmp_path, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    operation_input = closeout_operation_input(contract, config_path=fixture.config_path)
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=operation_input.effectiveInput,
        operation_progress=MutationEvidenceRecorder(),
    )
    intent = begin_git_mutation(
        args,
        leg="code",
        repository=contract.code_worktree,
        expected_output_tree=None,
        use_current_candidate=True,
    )
    with pytest.raises(RuntimeError, match="only fill pending intent"):
        bind_expected_output_tree(args, intent, repository=contract.code_worktree)
    with pytest.raises(RuntimeError, match="changed after intent"):
        bind_expected_output_tree(
            args,
            intent.model_copy(update={"repository": (tmp_path / "foreign").as_posix()}),
            repository=contract.code_worktree,
        )
    pre_mutation = intent.model_copy(
        update={"state": "pre-mutation", "before": None, "expectedOutputTree": None}
    )
    with pytest.raises(RuntimeError, match="pre-command Git evidence"):
        prove_git_commit(
            args,
            pre_mutation,
            repository=contract.code_worktree,
            commit=git(contract.code_worktree, "rev-parse", "HEAD"),
        )
    with pytest.raises(RuntimeError, match="does not match its mutation-intent"):
        prove_git_commit(
            args,
            intent,
            repository=contract.code_worktree,
            commit=git(contract.code_worktree, "rev-parse", "HEAD"),
        )


@pytest.mark.parametrize("leg", ["code", "memory", "ledger"])
def test_reconciliation_distinguishes_unchanged_ambiguous_and_proven_output(
    tmp_path: Path,
    leg: CloseoutMutationLeg,
) -> None:
    unchanged = _intent_record(tmp_path / f"{leg}-unchanged", leg=leg, prepare_output=False)
    unchanged_result = reconcile_closeout_mutations(unchanged)
    assert unchanged_result[leg].state == "reconciled-unchanged"
    assert (
        reconcile_closeout_mutations(
            unchanged.model_copy(update={"mutationEvidence": unchanged_result})
        )
        == unchanged_result
    )
    assert closeout_cancellable(unchanged.model_copy(update={"mutationEvidence": unchanged_result}))

    ambiguous = _intent_record(tmp_path / f"{leg}-ambiguous", leg=leg, prepare_output=True)
    ambiguous_repo = Path(ambiguous.mutationEvidence[leg].repository)
    git(ambiguous_repo, "add", "-A")
    ambiguous_result = reconcile_closeout_mutations(ambiguous)
    assert ambiguous_result[leg].state == "mutation-intent"
    assert ambiguous_result[leg].observed is not None
    assert not closeout_cancellable(ambiguous)

    proven = _intent_record(tmp_path / f"{leg}-proven", leg=leg, prepare_output=True)
    proven_repo = Path(proven.mutationEvidence[leg].repository)
    git(proven_repo, "add", "-A")
    git(proven_repo, "commit", "-m", "commit after published intent")
    proven_result = reconcile_closeout_mutations(proven)
    assert proven_result[leg].state == "commit-proven"
    assert proven_result[leg].commit == git(proven_repo, "rev-parse", "HEAD")
    assert not closeout_cancellable(proven.model_copy(update={"mutationEvidence": proven_result}))


def test_reconciliation_preserves_bound_output_after_exact_restore(tmp_path: Path) -> None:
    fixture = selected_fixture(tmp_path, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    assert contract.memory_worktree is not None
    assert contract.ledger_path is not None
    repository = contract.memory_worktree
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "prepare memory content before ledger")
    operation_input = closeout_operation_input(
        contract,
        config_path=fixture.config_path,
        approval_note="approved",
    )
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    runtime.start()
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=operation_input.effectiveInput,
        operation_progress=runtime.progress,
    )
    intent = begin_git_mutation(
        args,
        leg="ledger",
        repository=repository,
        expected_output_tree=None,
    )
    contract.ledger_path.write_text(
        contract.ledger_path.read_text(encoding="utf-8") + "\n# prepared ledger output\n",
        encoding="utf-8",
    )
    git(repository, "add", "memory.md")
    bound = bind_expected_output_tree(args, intent, repository=repository)
    record = store.read()
    assert record is not None
    evidence = record.mutationEvidence["ledger"]
    assert evidence.before is not None
    assert evidence.expectedOutputTree is not None
    assert evidence == bound
    assert evidence.expectedOutputTree != evidence.before.headTree

    git(repository, "restore", "--staged", "--worktree", "memory.md")
    reconciled = reconcile_closeout_mutations(record)
    restored = reconciled["ledger"]

    assert restored.state == "reconciled-unchanged"
    assert restored.observed is not None
    assert restored.observed == evidence.before
    assert restored.expectedOutputTree == evidence.expectedOutputTree
    assert restored.expectedOutputTree != restored.observed.headTree
    assert closeout_cancellable(record.model_copy(update={"mutationEvidence": reconciled}))


def test_reconciliation_does_not_claim_an_unexpected_ref_tree(tmp_path: Path) -> None:
    record = _intent_record(tmp_path, leg="code", prepare_output=True)
    repository = Path(record.mutationEvidence["code"].repository)
    (repository / "unexpected.txt").write_text("different output\n", encoding="utf-8")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "unexpected commit after intent")

    reconciled = reconcile_closeout_mutations(record)

    assert reconciled["code"].state == "mutation-intent"
    assert reconciled["code"].commit is None
    assert reconciled["code"].observed is not None


def test_reconciliation_detects_a_ref_that_moved_and_returned(tmp_path: Path) -> None:
    record = _intent_record(tmp_path, leg="code", prepare_output=True)
    evidence = record.mutationEvidence["code"]
    assert evidence.before is not None
    repository = Path(evidence.repository)
    candidate = repository / "feature.txt"
    candidate_bytes = candidate.read_bytes()
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "transient output")
    git(repository, "reset", "--hard", evidence.before.head)
    candidate.write_bytes(candidate_bytes)

    reconciled = reconcile_closeout_mutations(record)

    assert reconciled["code"].state == "mutation-intent"
    assert reconciled["code"].observed is not None


def test_reconciliation_records_an_unexpected_checked_out_ref(tmp_path: Path) -> None:
    record = _intent_record(tmp_path, leg="code", prepare_output=True)
    repository = Path(record.mutationEvidence["code"].repository)
    git(repository, "switch", "-c", "unexpected-recovery-ref")

    reconciled = reconcile_closeout_mutations(record)

    assert reconciled["code"].state == "mutation-intent"
    assert reconciled["code"].observed is not None
    assert reconciled["code"].observed.headRef == "refs/heads/unexpected-recovery-ref"


def test_one_unchanged_leg_cannot_hide_another_legs_proven_commit(tmp_path: Path) -> None:
    record = _intent_record(tmp_path, leg="memory", prepare_output=False)
    contract = load_contract(Path(record.contractPath))
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    accepted = record.input
    assert isinstance(accepted, CloseoutOperationInput)
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=accepted.effectiveInput,
        operation_progress=runtime.progress,
    )
    code_intent = begin_git_mutation(
        args,
        leg="code",
        repository=contract.code_worktree,
        expected_output_tree=None,
        use_current_candidate=True,
    )
    git(contract.code_worktree, "add", "-A")
    git(contract.code_worktree, "commit", "-m", accepted.effectiveInput.message_for("code"))
    prove_git_commit(
        args,
        code_intent,
        repository=contract.code_worktree,
        commit=git(contract.code_worktree, "rev-parse", "HEAD"),
    )
    current = store.read()
    assert current is not None
    reconciled = reconcile_closeout_mutations(current)
    runtime.progress(
        "memory-commit",
        {"mutation_evidence": reconciled["memory"].model_dump(mode="json")},
    )
    runtime.fail(RuntimeError("cut after mixed Git outcomes"))

    launches: list[int] = []
    observed = start_closeout_operation(
        accepted,
        launcher=lambda _contract, recovered: launches.append(recovered.attempt),
    )

    assert observed.status == "queued"
    assert launches == [2]


def test_reconciliation_refuses_a_repository_outside_contract_authority(
    tmp_path: Path,
) -> None:
    record = _intent_record(tmp_path, leg="code", prepare_output=False)
    evidence = dict(record.mutationEvidence)
    evidence["code"] = evidence["code"].model_copy(
        update={"repository": (tmp_path / "outside").as_posix()}
    )
    forged = record.model_copy(update={"mutationEvidence": evidence})

    with pytest.raises(RuntimeError, match="outside contract authority"):
        reconcile_closeout_mutations(forged)


def test_reconciliation_keeps_intent_when_authorized_repository_is_unreadable(
    tmp_path: Path,
) -> None:
    record = _intent_record(tmp_path, leg="code", prepare_output=False)
    repository = Path(record.mutationEvidence["code"].repository)
    repository.rename(tmp_path / "temporarily-moved-worktree")

    reconciled = reconcile_closeout_mutations(record)

    assert reconciled == record.mutationEvidence


def _running_code_operation(root: Path):
    fixture = selected_fixture(root, memory_mode="internal")
    contract = fixture.contracts[MASTER_A]
    (contract.code_worktree / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    operation_input = closeout_operation_input(contract, config_path=fixture.config_path)
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    runtime.start()
    return contract, operation_input, store, runtime


def _intent_record(root: Path, *, leg: CloseoutMutationLeg, prepare_output: bool):
    fixture = selected_fixture(
        root,
        memory_mode="internal" if leg == "code" else "external",
    )
    contract = fixture.contracts[MASTER_A]
    repository = contract.code_worktree
    if leg != "code":
        assert contract.memory_worktree is not None
        repository = contract.memory_worktree
    if leg == "ledger":
        git(repository, "add", "-A")
        git(repository, "commit", "-m", "prepare memory content before ledger")
    operation_input = closeout_operation_input(
        contract,
        config_path=fixture.config_path,
        code="commit intent",
        approval_note="approved",
    )
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    runtime.start()
    intent = begin_git_mutation(
        WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=operation_input.effectiveInput,
            operation_progress=runtime.progress,
        ),
        leg=leg,
        repository=repository,
        expected_output_tree=None,
        use_current_candidate=leg != "ledger",
    )
    if leg == "ledger" and prepare_output:
        assert contract.ledger_path is not None
        contract.ledger_path.write_text(
            contract.ledger_path.read_text(encoding="utf-8") + "\n# prepared ledger output\n",
            encoding="utf-8",
        )
        git(repository, "add", "memory.md")
        intent = bind_expected_output_tree(
            WorktreeArgs(
                contract_path=contract.contract_path,
                closeout_input=operation_input.effectiveInput,
                operation_progress=runtime.progress,
            ),
            intent,
            repository=repository,
        )
        assert intent is not None
    record = store.read()
    assert record is not None
    return record
