"""Public L2 controls for intended and conflicting protected integration refs."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

import pytest
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import worktree_status_tool
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.integration import integration_ref_state
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_controls as controls_module,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlError,
    control_operation,
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    start_or_observe_operation,
)
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_lifecycle_operation_controls_l2 import _command, _public_control
from test_worktree_support import git


def _recoverable_integration(tmp_path: Path):
    fixture = _authority_fixture(tmp_path, external_memory=True)
    contract = _closed_external_leaf_worktrees(fixture, tmp_path)
    start_or_observe_operation(
        IntegrateOperationInput(
            configPath=fixture.config_path.as_posix(),
            contractPath=contract.contract_path.as_posix(),
        ),
        contract,
        launcher=lambda *_: None,
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
    record = store.read()
    assert record is not None and record.integrationAuthority is not None
    store.update(
        lambda current: current.model_copy(
            update={
                "irreversibleBoundaryEntered": True,
                "recoveryCommits": LifecycleOperationRecoveryCommits(
                    codeCommit=contract.code_commit,
                    memoryContentCommit=contract.memory_content_commit,
                    ledgerCommit=contract.ledger_commit,
                ),
            }
        )
    )
    current = store.read()
    assert current is not None
    return contract, store, record, current


def test_integrate_ref_movement_requires_same_generation_recover(tmp_path: Path) -> None:
    contract, _store, record, moved = _recoverable_integration(tmp_path)
    authority = record.integrationAuthority
    assert authority is not None
    require_git(
        contract.code_repo_path,
        [
            "update-ref",
            f"refs/heads/{authority.codeSourceBranch}",
            contract.code_commit,
        ],
    )
    with pytest.raises(
        LifecycleControlError,
        match=(
            r"^lifecycle-immutable-output-recovery-required: this generation has immutable "
            "output intent or proof and can only recover$"
        ),
    ):
        control_operation(_command(contract, moved, "retry"))
    recover = next(
        row for row in legal_operation_controls(contract, moved) if row["action"] == "recover"
    )
    with mock.patch.object(controls_module, "launch_detached_worker") as launch:
        recovered = _public_control(load_config(Path(moved.input.configPath)), recover)
    assert recovered["ok"] is True
    assert recovered["lifecycleOperation"]["generation"] == 1
    launch.assert_called_once()


def test_third_protected_ref_status_and_public_handler_require_developer_decision(
    tmp_path: Path,
) -> None:
    contract, store, record, recoverable = _recoverable_integration(tmp_path)
    authority = record.integrationAuthority
    assert authority is not None
    advertised = next(
        row for row in legal_operation_controls(contract, recoverable) if row["action"] == "recover"
    )
    before = authority.codeSourceCommit
    tree = git(contract.code_repo_path, "rev-parse", f"{before}^{{tree}}")
    third = git(
        contract.code_repo_path,
        "commit-tree",
        tree,
        "-p",
        before,
        "-m",
        "unexpected third protected ref",
    )
    require_git(
        contract.code_repo_path,
        ["update-ref", f"refs/heads/{authority.codeSourceBranch}", third, before],
    )
    journal_before = store.path.read_bytes()
    contract_before = contract.contract_path.read_bytes()
    config = load_config(Path(record.input.configPath))
    with mock.patch(
        "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
        return_value=WorktreeCommandResult(
            0,
            {
                "contract_path": contract.contract_path.as_posix(),
                "task_name": contract.task_name,
            },
        ),
    ):
        status = worktree_status_tool(
            config,
            TaskRef(repo_id=contract.repo_name, contract_path=contract.contract_path.as_posix()),
        )
    projected = next(row for row in status["lifecycleOperations"] if row["kind"] == "integrate")
    assert projected["legalControls"] == []
    expected = {
        "before": {"codeRef": before, "memoryRef": authority.memorySourceCommit},
        "intended": {
            "codeRef": contract.code_commit,
            "memoryRef": contract.ledger_commit,
        },
    }
    observed = {
        "codeRef": {
            "side": "code",
            "ref": f"refs/heads/{authority.codeSourceBranch}",
            "objectId": third,
        },
        "memoryRef": {
            "side": "memory",
            "ref": f"refs/heads/{authority.memorySourceBranch}",
            "objectId": authority.memorySourceCommit,
        },
    }
    decision = {
        "state": "integration-ref-conflict",
        "reason": "a protected source ref has an unexpected third object",
        "summary": "a protected source ref has an unexpected third object",
        "developerDecisionRequired": True,
        "decisionSurface": "a protected source ref has an unexpected third object",
        "nextAction": "developer-decision",
        "expected": expected,
        "observed": observed,
    }
    assert projected["result"] == decision
    refused = _public_control(config, advertised)
    assert refused["ok"] is False
    assert refused["status"] == "integration-ref-conflict"
    assert refused["nextAction"] == "developer-decision"
    assert refused["expected"] == expected
    assert refused["observed"] == observed
    assert {
        "status": decision["state"],
        "detail": decision["decisionSurface"],
        "developerDecisionRequired": decision["developerDecisionRequired"],
        "nextAction": decision["nextAction"],
        "expected": decision["expected"],
        "observed": decision["observed"],
    } == {
        key: refused[key]
        for key in (
            "status",
            "detail",
            "developerDecisionRequired",
            "nextAction",
            "expected",
            "observed",
        )
    }
    assert store.path.read_bytes() == journal_before
    assert contract.contract_path.read_bytes() == contract_before
    assert (
        require_git(
            contract.code_repo_path,
            ["rev-parse", f"refs/heads/{authority.codeSourceBranch}"],
        )
        == third
    )


@pytest.mark.parametrize(
    ("side", "failure", "returncode"),
    [
        ("code", "ref-missing", 1),
        ("code", "ref-unreadable", 128),
        ("memory", "ref-missing", 1),
        ("memory", "ref-unreadable", 128),
    ],
)
def test_missing_or_unreadable_protected_ref_is_one_public_decision(
    tmp_path: Path,
    side: str,
    failure: str,
    returncode: int,
) -> None:
    contract, store, record, recoverable = _recoverable_integration(tmp_path)
    authority = record.integrationAuthority
    assert authority is not None and contract.memory_repo_path is not None
    advertised = next(
        row for row in legal_operation_controls(contract, recoverable) if row["action"] == "recover"
    )
    repository = Path(authority.codeRepository if side == "code" else authority.memoryRepository)
    real_run_git = integration_ref_state.run_git

    def unreadable_ref(repo: Path, args: list[str]):
        if repo == repository and args[:3] == ["show-ref", "--verify", "--quiet"]:
            return CompletedProcess(args, returncode, stdout="", stderr="not public")
        return real_run_git(repo, args)

    before = {
        "journal": store.path.read_bytes(),
        "contract": contract.contract_path.read_bytes(),
        "codeRef": require_git(
            contract.code_repo_path,
            ["rev-parse", f"refs/heads/{authority.codeSourceBranch}"],
        ),
        "memoryRef": require_git(
            contract.memory_repo_path,
            ["rev-parse", f"refs/heads/{authority.memorySourceBranch}"],
        ),
    }
    config = load_config(Path(record.input.configPath))
    with (
        mock.patch.object(integration_ref_state, "run_git", side_effect=unreadable_ref),
        mock.patch.object(controls_module, "launch_detached_worker") as launch,
        mock.patch(
            "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
            return_value=WorktreeCommandResult(
                0,
                {
                    "contract_path": contract.contract_path.as_posix(),
                    "task_name": contract.task_name,
                },
            ),
        ),
    ):
        status = worktree_status_tool(
            config,
            TaskRef(
                repo_id=contract.repo_name,
                contract_path=contract.contract_path.as_posix(),
            ),
        )
        projected = next(row for row in status["lifecycleOperations"] if row["kind"] == "integrate")
        refused = _public_control(config, advertised)

    launch.assert_not_called()

    assert projected["legalControls"] == []
    decision = projected["result"]
    assert decision["state"] == "integration-ref-conflict"
    assert decision["nextAction"] == "developer-decision"
    observed_key = f"{side}Ref"
    assert decision["observed"][observed_key] == {
        "side": side,
        "ref": (
            f"refs/heads/{authority.codeSourceBranch}"
            if side == "code"
            else f"refs/heads/{authority.memorySourceBranch}"
        ),
        "errorType": failure,
    }
    assert "not public" not in repr(decision)
    assert refused["ok"] is False
    assert {
        "status": decision["state"],
        "detail": decision["decisionSurface"],
        "developerDecisionRequired": decision["developerDecisionRequired"],
        "nextAction": decision["nextAction"],
        "expected": decision["expected"],
        "observed": decision["observed"],
    } == {
        key: refused[key]
        for key in (
            "status",
            "detail",
            "developerDecisionRequired",
            "nextAction",
            "expected",
            "observed",
        )
    }
    after = {
        "journal": store.path.read_bytes(),
        "contract": contract.contract_path.read_bytes(),
        "codeRef": require_git(
            contract.code_repo_path,
            ["rev-parse", f"refs/heads/{authority.codeSourceBranch}"],
        ),
        "memoryRef": require_git(
            contract.memory_repo_path,
            ["rev-parse", f"refs/heads/{authority.memorySourceBranch}"],
        ),
    }
    assert after == before
