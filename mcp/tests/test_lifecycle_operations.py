from __future__ import annotations

import json
import runpy
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from _global_state import preserve_owned_mutable_state
from agents_remember.application import lifecycle_operation_worker, worktree_tools
from agents_remember.application.task_ref import TaskRef
from agents_remember.controlplane.records import (
    GateVerdict,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees import lifecycle_operations
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.lifecycle_operations import (
    cancel_operation,
    latest_operation_projection,
    observe_operation,
    operation_fingerprint,
    operation_key,
    start_or_observe_operation,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from pydantic import ValidationError


def _contract(tmp_path: Path):
    coordination = tmp_path / "ar-coordination"
    contract = default_contract(
        ContractTask(
            name="durable-lifecycle",
            repo_name="repo",
            coordination_root=coordination,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="durable-lifecycle", leaf_id="L23"),
        code=RepoBranchPlan(
            repo_path=tmp_path / "repo",
            source_branch="main",
            work_branch="feature/l23",
            base_commit="a" * 40,
        ),
    )
    write_contract(contract.contract_path, contract)
    contract.code_worktree.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=contract.code_worktree,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "lifecycle-tests@agents-remember.invalid"],
        cwd=contract.code_worktree,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Lifecycle Tests"],
        cwd=contract.code_worktree,
        check=True,
    )
    (contract.code_worktree / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=contract.code_worktree, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=contract.code_worktree,
        check=True,
        capture_output=True,
    )
    return contract


def _input(contract, *, message: str = "close L23") -> CloseoutOperationInput:
    return CloseoutOperationInput(
        configPath=(contract.coordination_root / "settings.json").as_posix(),
        contractPath=contract.contract_path.as_posix(),
        codeCommitMessage=message,
        approvalNote="developer approved this exact candidate",
    )


def test_start_returns_immediately_and_duplicate_observes_one_launch(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    launches = []

    def launcher(loaded, record) -> None:
        launches.append((loaded.task_name, record.fingerprint))

    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    first = start_or_observe_operation(_input(contract), launcher=launcher, now=now)
    second = start_or_observe_operation(_input(contract), launcher=launcher, now=now)

    assert first.status == second.status == "queued"
    assert len(launches) == 1
    assert "job" not in first.model_dump_json().lower()
    assert "pid" not in first.model_dump_json().lower()


def test_conflicting_commit_message_refuses_while_task_operation_exists(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)

    with pytest.raises(RuntimeError, match="conflicting closeout operation"):
        start_or_observe_operation(
            _input(contract, message="different mutation"), launcher=lambda *_: None
        )


def test_changed_worktree_is_a_different_closeout_candidate(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    first = store.read()
    assert first is not None and first.candidateTree is not None
    (contract.code_worktree / "later.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="conflicting closeout operation"):
        start_or_observe_operation(_input(contract), launcher=lambda *_: None)

    current = store.read()
    assert current is not None
    assert current.candidateTree == first.candidateTree


def test_status_projects_the_latest_task_operation() -> None:
    projection = Mock()
    projection.model_dump.return_value = {"kind": "closeout", "status": "running"}
    result = WorktreeCommandResult(
        0,
        {"contract_path": "/tmp/contract.yaml", "task_name": "durable-lifecycle"},
    )
    with (
        patch.object(worktree_tools, "_task_ref_namespace", return_value=Mock()),
        patch.object(worktree_tools.git_worktree_manager, "status_result", return_value=result),
        patch.object(worktree_tools, "latest_operation_projection", return_value=projection),
    ):
        payload = worktree_tools.worktree_status_tool(
            cast(McpRuntimeConfig, Mock()), TaskRef(repo_id="repo")
        )

    assert payload["lifecycleOperation"] == {"kind": "closeout", "status": "running"}
    projection.model_dump.assert_called_once_with(mode="json", exclude_none=True)

    with (
        patch.object(worktree_tools, "_task_ref_namespace", return_value=Mock()),
        patch.object(
            worktree_tools.git_worktree_manager,
            "status_result",
            return_value=WorktreeCommandResult(0, {"task_name": "unattached"}),
        ),
        patch.object(worktree_tools, "latest_operation_projection") as latest,
    ):
        unattached = worktree_tools.worktree_status_tool(
            cast(McpRuntimeConfig, Mock()), TaskRef(repo_id="repo")
        )
    assert "lifecycleOperation" not in unattached
    latest.assert_not_called()


def test_closeout_preview_path_and_cancel_validation_are_task_addressed() -> None:
    config = cast(McpRuntimeConfig, Mock())
    messages = worktree_tools.CloseoutCommitMessages(code="close L23")
    approval = worktree_tools.CloseoutApproval(dry_run=True)
    with patch.object(worktree_tools, "_worktree_closeout", return_value={"ok": True}) as close:
        assert worktree_tools.worktree_closeout_apply_tool(
            config, "/tmp/contract.yaml", messages, approval
        ) == {"ok": True}
    close.assert_called_once()

    with pytest.raises(ValueError, match="operation_kind"):
        worktree_tools.worktree_operation_cancel_tool(
            config,
            contract_path="/tmp/contract.yaml",
            operation_kind="cleanup",
            intent_note="stop",
        )
    with pytest.raises(ValueError, match="non-empty intent_note"):
        worktree_tools.worktree_operation_cancel_tool(
            config,
            contract_path="/tmp/contract.yaml",
            operation_kind="closeout",
            intent_note=" \n ",
        )
    with (
        patch.object(worktree_tools, "require_within_coordination", return_value=Path("/tmp/c")),
        patch.object(worktree_tools, "observe_operation", return_value=None),
        pytest.raises(RuntimeError, match="no closeout operation"),
    ):
        worktree_tools.worktree_operation_cancel_tool(
            config,
            contract_path="/tmp/contract.yaml",
            operation_kind="closeout",
            intent_note="stop before boundary",
            dry_run=True,
        )


def test_stale_worker_is_relaunched_with_same_input_and_attempt(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    old = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None, now=old)
    launches = []

    projection = start_or_observe_operation(
        _input(contract),
        launcher=lambda _, record: launches.append(record.attempt),
        now=old + timedelta(seconds=31),
    )

    assert launches == [2]
    assert projection.status == "queued"


def test_preboundary_failure_restarts_the_same_task_operation(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(
            update={
                "status": "failed",
                "phase": "failed",
                "finishedAt": "2026-08-12T12:01:00+00:00",
            }
        )
    )
    attempts: list[int] = []

    projection = start_or_observe_operation(
        _input(contract), launcher=lambda _, record: attempts.append(record.attempt)
    )

    assert projection.status == "queued"
    assert attempts == [2]


def test_stale_recovery_attempt_can_be_claimed_only_once(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(
            update={
                "status": "failed",
                "phase": "failed",
                "finishedAt": "2026-08-12T12:01:00+00:00",
            }
        )
    )

    def requeue(record):
        return record.model_copy(
            update={"status": "queued", "phase": "queued", "attempt": record.attempt + 1}
        )

    first, first_claimed = store.replace_for_recovery(requeue, expected_attempt=1)
    second, second_claimed = store.replace_for_recovery(requeue, expected_attempt=1)

    assert first.attempt == second.attempt == 2
    assert first_claimed is True
    assert second_claimed is False


def test_completed_attempt_observes_until_contract_state_advances(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    operation_input = _input(contract)
    start_or_observe_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(update={"status": "running", "phase": "preflight"})
    )
    store.update(
        lambda record: record.model_copy(
            update={
                "status": "completed",
                "phase": "completed",
                "finishedAt": "2026-08-12T12:02:00+00:00",
            }
        )
    )
    assert (
        start_or_observe_operation(operation_input, launcher=lambda *_: None).status == "completed"
    )
    with pytest.raises(RuntimeError, match="already completed task state"):
        start_or_observe_operation(
            _input(contract, message="different message"), launcher=lambda *_: None
        )

    advanced = replace(contract, code_commit="b" * 40, closeout_status="completed")
    write_contract(advanced.contract_path, advanced)
    attempts: list[int] = []
    projection = start_or_observe_operation(
        _input(advanced), launcher=lambda _, record: attempts.append(record.attempt)
    )

    assert projection.status == "queued"
    assert attempts == [2]


def test_corrupt_or_extra_store_fields_fail_closed(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    path = operation_record_path(contract.worktree_group, "closeout")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["agentSelectedJobId"] = "forbidden"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid lifecycle operation record"):
        LifecycleOperationStore(path).read()


def test_store_revalidates_model_copy_before_write(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))

    with pytest.raises(ValidationError):
        store.update(lambda record: record.model_copy(update={"status": "willy-nilly"}))


def test_cancel_before_boundary_is_task_addressed_and_kills_private_worker_group(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(
            update={"status": "running", "phase": "quality", "workerPid": 4321}
        )
    )

    with (
        patch("agents_remember.worktrees.lifecycle_operations.os.killpg") as kill,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        projections = list(
            pool.map(
                lambda _: cancel_operation(contract.contract_path, "closeout"),
                range(2),
            )
        )

    assert {projection.status for projection in projections} == {"cancelled"}
    assert all(not projection.cancellable for projection in projections)
    assert store.read().workerPid is None  # type: ignore[union-attr]
    kill.assert_called_once()


def test_cancel_after_boundary_refuses_without_making_approval_reusable(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    operation_input = _input(contract)
    start_or_observe_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(
            update={
                "status": "running",
                "phase": "approval-claim",
                "irreversibleBoundaryEntered": True,
                "approvalClaimed": True,
            }
        )
    )

    with pytest.raises(RuntimeError, match="cancellation is refused"):
        cancel_operation(contract.contract_path, "closeout")
    assert store.read().approvalClaimed is True  # type: ignore[union-attr]


def test_internal_operation_key_is_stable_but_not_part_of_projection(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    operation_input = _input(contract)
    fingerprint = operation_fingerprint(operation_input)
    key = operation_key(contract.contract_path, "closeout", fingerprint)

    projection = start_or_observe_operation(operation_input, launcher=lambda *_: None)

    assert len(key) == 64
    assert key not in projection.model_dump_json()


def test_consumed_gate_recovers_only_the_same_internal_operation(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "observer")
    opened = create_gate(
        "closeout-approval", gate_id="01KZTTESTGATE0000000000000", now="2026-08-12T10:00:00+00:00"
    )
    approved = decide_gate(
        opened,
        GateVerdict(decision="approve", via="chat", by="developer"),
        now="2026-08-12T10:01:00+00:00",
    )
    store.append(opened)
    store.append(approved)

    claimed = store.claim_approval(
        None,
        kind="closeout-approval",
        now="2026-08-12T10:02:00+00:00",
        operation_key="a" * 64,
    )
    recovered = store.claim_approval(
        None,
        kind="closeout-approval",
        now="2026-08-12T10:03:00+00:00",
        operation_key="a" * 64,
    )
    conflicting = store.claim_approval(
        None,
        kind="closeout-approval",
        now="2026-08-12T10:04:00+00:00",
        operation_key="b" * 64,
    )

    assert claimed.permitted and recovered.permitted
    assert not conflicting.permitted
    assert len(store.read(None)) == 3


def test_store_recovery_and_terminal_replacement_guards_every_identity_edge(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    missing = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    with pytest.raises(RuntimeError, match="does not exist"):
        missing.update(lambda record: record)
    with pytest.raises(RuntimeError, match="completed lifecycle operation"):
        missing.replace_for_recovery(lambda record: record, expected_attempt=1)
    with pytest.raises(RuntimeError, match="active lifecycle operation"):
        missing.replace_terminal(
            lifecycle_operations._queued_record(
                contract,
                _input(contract),
                lifecycle_operations._CandidateIdentity(
                    state="a" * 64,
                    tree="c" * 40,
                    fingerprint="b" * 64,
                ),
                datetime(2026, 8, 12, tzinfo=UTC),
            )
        )

    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    current = store.read()
    assert current is not None
    with pytest.raises(RuntimeError, match="cannot change its fingerprint"):
        store.replace_for_recovery(
            lambda record: record.model_copy(update={"fingerprint": "c" * 64}),
            expected_attempt=1,
        )
    terminal_past_boundary = current.model_copy(
        update={
            "status": "failed",
            "phase": "failed",
            "irreversibleBoundaryEntered": True,
        }
    )
    store._write(terminal_past_boundary)
    with pytest.raises(RuntimeError, match="past its boundary"):
        store.replace_for_recovery(lambda record: record, expected_attempt=1)

    candidate = current.model_copy(update={"fingerprint": "d" * 64, "operationKey": "e" * 64})
    with pytest.raises(RuntimeError, match="cannot change taskId"):
        store.replace_terminal(candidate.model_copy(update={"taskId": "different"}))
    replaced = store.replace_terminal(candidate)
    assert replaced.attempt == 2
    assert replaced.fingerprint == "d" * 64


def test_store_transition_guards_immutable_input_claim_boundary_and_state(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    current = store.read()
    assert current is not None
    validate = store._validate_transition

    with pytest.raises(RuntimeError, match="cannot change taskName"):
        validate(current, current.model_copy(update={"taskName": "other"}))
    with pytest.raises(RuntimeError, match="cannot change its durable input"):
        validate(current, current.model_copy(update={"operationKey": "f" * 64}))
    with pytest.raises(RuntimeError, match="invalid lifecycle operation transition"):
        validate(current, current.model_copy(update={"status": "input-required"}))

    claimed = current.model_copy(update={"approvalClaimed": True})
    with pytest.raises(RuntimeError, match="cannot become unclaimed"):
        validate(claimed, claimed.model_copy(update={"approvalClaimed": False}))
    entered = current.model_copy(update={"irreversibleBoundaryEntered": True})
    with pytest.raises(RuntimeError, match="cannot be cleared"):
        validate(entered, entered.model_copy(update={"irreversibleBoundaryEntered": False}))
    with pytest.raises(RuntimeError, match="cannot cancel after"):
        validate(
            entered,
            entered.model_copy(update={"status": "cancelled", "phase": "cancelled"}),
        )


def test_observe_latest_terminal_cancel_and_launch_failure_are_task_addressed(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    assert observe_operation(contract.contract_path, "closeout") is None
    assert latest_operation_projection(contract.contract_path) is None

    with pytest.raises(RuntimeError, match="no native runner"):
        start_or_observe_operation(
            _input(contract),
            launcher=lambda *_: (_ for _ in ()).throw(RuntimeError("no native runner")),
        )
    failed = observe_operation(contract.contract_path, "closeout")
    assert failed is not None and failed.status == "failed"
    assert cancel_operation(contract.contract_path, "closeout").status == "failed"

    integration = IntegrateOperationInput(
        configPath=(contract.coordination_root / "settings.json").as_posix(),
        contractPath=contract.contract_path.as_posix(),
    )
    start_or_observe_operation(integration, launcher=lambda *_: None)
    latest = latest_operation_projection(contract.contract_path)
    assert latest is not None and latest.kind == "integrate" and latest.status == "queued"


def test_detached_launcher_uses_native_environment_and_private_process_group(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    record = store.read()
    assert record is not None
    process = SimpleNamespace(pid=9876)
    environment = {"PATH": "/usr/bin", "PYTHONPATH": "/existing"}

    with (
        patch.object(lifecycle_operations, "git_environment", return_value=environment),
        patch.object(
            lifecycle_operations,
            "native_subprocess_environment",
            return_value=dict(environment),
        ) as native_env,
        patch.object(
            lifecycle_operations,
            "native_command",
            side_effect=lambda command, _env: command,
        ),
        patch.object(lifecycle_operations.subprocess, "Popen", return_value=process) as popen,
    ):
        lifecycle_operations.launch_detached_worker(contract, record)

    native_env.assert_called_once()
    command = popen.call_args.args[0]
    assert "agents_remember.application.lifecycle_operation_worker" in command
    assert popen.call_args.kwargs["env"]["PYTHONPATH"] == "/existing"
    assert (contract.code_worktree / "mcp" / "src").as_posix() not in str(
        popen.call_args.kwargs["env"]
    )
    assert popen.call_args.kwargs["start_new_session"] is True
    assert store.read().workerPid == 9876  # type: ignore[union-attr]


def test_private_worker_termination_tolerates_an_already_exited_process() -> None:
    with patch.object(lifecycle_operations.os, "killpg", side_effect=ProcessLookupError):
        lifecycle_operations._terminate_worker_group(1234)


def test_input_identity_and_closeout_approval_are_validated_before_launch(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with pytest.raises(RuntimeError, match="does not resolve"):
        lifecycle_operations._validate_input_identity(
            contract,
            _input(contract).model_copy(update={"contractPath": (tmp_path / "other").as_posix()}),
        )
    with pytest.raises(RuntimeError, match="non-empty approval intent"):
        lifecycle_operations._validate_input_identity(
            contract,
            _input(contract).model_copy(update={"approvalNote": "  "}),
        )


def test_operation_runtime_tracks_progress_reports_and_terminal_outcomes(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    running = runtime.start()
    assert running.status == "running"
    assert runtime.start().status == "running"

    runtime.progress(
        "approval-claim",
        {
            "current_command": "claim exact approval",
            "approval_claimed": True,
            "irreversible_boundary": True,
        },
    )
    assert store.read().approvalClaimed is True  # type: ignore[union-attr]
    runtime.progress(
        "code-commit",
        {
            "recovery_commits": {
                "codeCommit": "a" * 40,
                "memoryContentCommit": "",
                "ledgerCommit": "",
            }
        },
    )
    runtime.progress(
        "ledger-commit",
        {
            "recovery_commits": {
                "codeCommit": "a" * 40,
                "memoryContentCommit": "b" * 40,
                "ledgerCommit": "c" * 40,
            }
        },
    )
    recorded = store.read()
    assert recorded is not None and recorded.recoveryCommits is not None
    assert recorded.recoveryCommits.codeCommit == "a" * 40
    assert recorded.recoveryCommits.ledgerCommit == "c" * 40
    with pytest.raises(RuntimeError, match="cannot be cleared"):
        store.update(lambda record: record.model_copy(update={"recoveryCommits": None}))
    with pytest.raises(RuntimeError, match="can only fill empty cells"):
        store.update(
            lambda record: record.model_copy(
                update={
                    "recoveryCommits": LifecycleOperationRecoveryCommits(
                        codeCommit="d" * 40,
                        memoryContentCommit="b" * 40,
                        ledgerCommit="c" * 40,
                    )
                }
            )
        )

    quality = store.path.parent / lifecycle_operation_worker.QUALITY_PROGRESS_REPORT
    quality.write_text(
        json.dumps({"status": "running", "step": "pytest", "detail": "20 workers"}),
        encoding="utf-8",
    )
    assert runtime._quality_command() == "quality: pytest — 20 workers"
    quality.write_text("not-json", encoding="utf-8")
    dagger = store.path.parent / "dagger-progress.log"
    dagger.write_text("first\nlatest graph vertex\n", encoding="utf-8")
    assert runtime._quality_command() == "Dagger quality: latest graph vertex"
    dagger.unlink()
    assert runtime._quality_command() is None

    quality.write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    assert runtime._quality_command() is None
    quality.write_text(
        json.dumps({"status": "running", "step": 3, "detail": "bad"}), encoding="utf-8"
    )
    assert runtime._quality_command() is None

    runtime.finish({"reason": "merge needs reconciliation"}, ok=False)
    recovery = store.read()
    assert recovery is not None and recovery.status == "input-required"
    assert recovery.phase == "contract-finalization"
    assert recovery.workerPid is None


def test_operation_runtime_heartbeat_updates_running_and_ignores_terminal_record(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    runtime.start()
    runtime.stop = Mock()
    runtime.stop.wait.side_effect = [False, True]
    with patch.object(runtime, "_quality_command", return_value="quality: pytest"):
        runtime.heartbeat()
    assert store.read().currentCommand == "quality: pytest"  # type: ignore[union-attr]

    store.update(
        lambda record: record.model_copy(update={"status": "completed", "phase": "completed"})
    )
    runtime.stop.wait.side_effect = [False, True]
    runtime.heartbeat()
    assert store.read().status == "completed"  # type: ignore[union-attr]


def test_operation_runtime_failure_modes_and_cancelled_progress(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = lifecycle_operation_worker.OperationRuntime(store)
    runtime.start()
    runtime.finish({"developer_decision_required": True, "summary": "choose"}, ok=False)
    assert store.read().status == "input-required"  # type: ignore[union-attr]
    assert store.read().workerPid is None  # type: ignore[union-attr]
    with patch.object(lifecycle_operations, "_terminate_worker_group") as terminate:
        cancelled = lifecycle_operations.cancel_operation(contract.contract_path, "closeout")
    assert cancelled.status == "cancelled"
    terminate.assert_not_called()
    with pytest.raises(lifecycle_operation_worker.OperationCancelled):
        runtime.progress("quality", {})
    runtime.finish({"ok": True}, ok=True)
    assert store.read().status == "cancelled"  # type: ignore[union-attr]

    second_contract = _contract(tmp_path / "second")
    start_or_observe_operation(_input(second_contract), launcher=lambda *_: None)
    second_store = LifecycleOperationStore(
        operation_record_path(second_contract.worktree_group, "closeout")
    )
    second_runtime = lifecycle_operation_worker.OperationRuntime(second_store)
    second_runtime.start()
    second_runtime.finish({"commit": "a" * 40}, ok=True)
    assert second_store.read().status == "completed"  # type: ignore[union-attr]


def test_execute_operation_dispatches_closeout_and_integration_payloads(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    runtime = Mock()
    config = SimpleNamespace()
    with patch.object(lifecycle_operation_worker, "load_config", return_value=config):
        start_or_observe_operation(_input(contract), launcher=lambda *_: None)
        closeout = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        ).read()
        assert closeout is not None
        with patch.object(
            lifecycle_operation_worker,
            "closeout_result",
            return_value=WorktreeCommandResult(0, {"state": "closed"}),
        ):
            lifecycle_operation_worker.execute_operation(closeout, runtime)
        runtime.finish.assert_called_with(
            {"state": "closed", "ok": True, "operation": "worktree_closeout_apply"}, ok=True
        )

        integration_input = IntegrateOperationInput(
            configPath="settings.json",
            contractPath=contract.contract_path.as_posix(),
            autoCompleteSeats=False,
        )
        start_or_observe_operation(integration_input, launcher=lambda *_: None)
        integration = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "integrate")
        ).read()
        assert integration is not None
        with patch.object(
            lifecycle_operation_worker,
            "integrate_result",
            return_value=WorktreeCommandResult(2, {"reason": "blocked"}),
        ):
            lifecycle_operation_worker.execute_operation(integration, runtime)
        runtime.finish.assert_called_with(
            {"reason": "blocked", "ok": False, "operation": "worktree_integrate"}, ok=False
        )


def test_integration_completion_auto_closes_seats_only_for_a_green_edge(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    operation_input = IntegrateOperationInput(
        configPath="settings.json",
        contractPath=contract.contract_path.as_posix(),
        autoCompleteSeats=True,
    )
    with patch.object(
        lifecycle_operation_worker,
        "auto_complete_seats",
        return_value={"completedSeatCount": 3},
    ) as complete:
        payload = lifecycle_operation_worker.integration_completion_payload(
            cast(McpRuntimeConfig, SimpleNamespace()),
            operation_input,
            WorktreeCommandResult(0, {"state": "integrated"}),
        )
    assert payload["completedSeatCount"] == 3
    complete.assert_called_once()

    with patch.object(lifecycle_operation_worker, "auto_complete_seats") as complete:
        lifecycle_operation_worker.integration_completion_payload(
            cast(McpRuntimeConfig, SimpleNamespace()),
            operation_input,
            WorktreeCommandResult(2, {"state": "blocked"}),
        )
    complete.assert_not_called()


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (lifecycle_operation_worker.OperationCancelled(), 0),
        (RuntimeError("worker failed"), 1),
        (None, 0),
    ],
)
def test_run_worker_records_execution_outcome(
    tmp_path: Path, side_effect: Exception | None, expected: int
) -> None:
    contract = _contract(tmp_path)
    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    with patch.object(lifecycle_operation_worker, "execute_operation", side_effect=side_effect):
        assert lifecycle_operation_worker.run_worker(contract.contract_path, "closeout") == expected
    current = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "closeout")
    ).read()
    if expected == 1:
        assert current is not None and current.status == "failed"


def test_run_worker_refuses_missing_or_non_startable_durable_state(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with pytest.raises(RuntimeError, match="no closeout operation is queued"):
        lifecycle_operation_worker.run_worker(contract.contract_path, "closeout")

    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store._write(
        store.read().model_copy(update={"status": "cancelled", "phase": "cancelled"})  # type: ignore[union-attr]
    )
    assert lifecycle_operation_worker.run_worker(contract.contract_path, "closeout") == 0

    store._write(
        store.read().model_copy(  # type: ignore[union-attr]
            update={"status": "input-required", "phase": "failed"}
        )
    )
    with pytest.raises(RuntimeError, match="cannot start from durable state"):
        lifecycle_operation_worker.run_worker(contract.contract_path, "closeout")


def test_worker_parser_main_and_script_entry_use_task_addressing(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    parser = lifecycle_operation_worker.build_parser()
    parsed = parser.parse_args(
        ["--contract-path", contract.contract_path.as_posix(), "--kind", "closeout"]
    )
    assert parsed.contract_path == contract.contract_path

    services = Mock()
    entry_order: list[str] = []
    with (
        patch.object(
            lifecycle_operation_worker,
            "declare_lifecycle_operation_process",
            side_effect=lambda: entry_order.append("declare"),
        ) as declare_operation,
        patch.object(
            lifecycle_operation_worker,
            "build_default_worktree_services",
            side_effect=lambda: (entry_order.append("build"), services)[1],
        ) as build_services,
        patch.object(lifecycle_operation_worker, "bind_worktree_services") as bind_services,
        patch.object(lifecycle_operation_worker, "run_worker", return_value=7) as run,
    ):
        assert (
            lifecycle_operation_worker.main(
                ["--contract-path", contract.contract_path.as_posix(), "--kind", "closeout"]
            )
            == 7
        )
    declare_operation.assert_called_once_with()
    assert entry_order == ["declare", "build"]
    build_services.assert_called_once_with()
    bind_services.assert_called_once_with(services)
    run.assert_called_once_with(contract.contract_path, "closeout")

    start_or_observe_operation(_input(contract), launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store._write(
        store.read().model_copy(update={"status": "cancelled", "phase": "cancelled"})  # type: ignore[union-attr]
    )
    argv = [
        "lifecycle_operation_worker.py",
        "--contract-path",
        contract.contract_path.as_posix(),
        "--kind",
        "closeout",
    ]
    with (
        preserve_owned_mutable_state(),
        patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as exited,
    ):
        runpy.run_path(
            Path(lifecycle_operation_worker.__file__).as_posix(),
            run_name="__main__",
        )
    assert exited.value.code == 0
