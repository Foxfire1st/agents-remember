"""Real Git forcing for created and resumed external ledger outputs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.kernel.memory_ledger import find_mapping, load_ledger
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.queue.closeout_recovery import resume_external_commits
from closeout_input_test_support import closeout_operation_input, start_closeout_operation
from test_worktree_support import git, open_external_contract_fixture


def _journaled_ledger_fixture(root: Path):
    contract = open_external_contract_fixture(root)
    assert contract.memory_worktree is not None
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo-a": {}},
            }
        ),
        encoding="utf-8",
    )
    (contract.code_worktree / "accepted.txt").write_text("accepted\n", encoding="utf-8")
    git(contract.code_worktree, "add", "accepted.txt")
    git(contract.code_worktree, "commit", "-m", "accepted code output")
    code_commit = git(contract.code_worktree, "rev-parse", "HEAD")
    memory_commit = git(contract.memory_worktree, "rev-parse", "HEAD")
    operation_input = closeout_operation_input(
        contract,
        config_path=config_path,
        memory="record memory output",
        ledger="record ledger mapping",
    )
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = OperationRuntime(store)
    runtime.start()
    runtime.progress(
        "memory-commit",
        {
            "recovery_commits": {
                "codeCommit": code_commit,
                "memoryContentCommit": memory_commit,
                "ledgerCommit": "",
            }
        },
    )
    return contract, operation_input, store, runtime, code_commit, memory_commit


def test_created_ledger_output_publishes_intent_bind_commit_and_proof(tmp_path: Path) -> None:
    contract, operation_input, store, runtime, code_commit, memory_commit = (
        _journaled_ledger_fixture(tmp_path)
    )
    events: list[tuple[str, dict[str, object]]] = []

    def progress(phase: str, evidence: Mapping[str, object]) -> None:
        events.append((phase, dict(evidence)))
        runtime.progress(phase, evidence)

    memory_result, ledger_commit = resume_external_commits(
        contract,
        WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=operation_input.effectiveInput,
            operation_progress=progress,
        ),
        operation_input.effectiveInput,
        code_commit=code_commit,
        memory_commit=memory_commit,
    )

    assert memory_result == memory_commit
    assert contract.memory_worktree is not None and contract.ledger_path is not None
    assert ledger_commit == git(contract.memory_worktree, "rev-parse", "HEAD")
    assert git(contract.memory_worktree, "log", "-1", "--format=%s") == (
        operation_input.effectiveInput.message_for("ledger")
    )
    mapping = find_mapping(load_ledger(contract.ledger_path), code_commit)
    assert mapping is not None and mapping.memory_commit == memory_commit
    mutation_events = [
        cast(dict[str, object], evidence["mutation_evidence"])
        for phase, evidence in events
        if phase == "ledger-commit" and "mutation_evidence" in evidence
    ]
    assert [event["state"] for event in mutation_events] == [
        "mutation-intent",
        "mutation-intent",
        "commit-proven",
    ]
    assert mutation_events[0]["expectedOutputTree"] is None
    assert mutation_events[1]["expectedOutputTree"]
    assert mutation_events[2]["commit"] == ledger_commit
    before = cast(dict[str, object], mutation_events[0]["before"])
    observed = cast(dict[str, object], mutation_events[2]["observed"])
    assert before["head"] == memory_commit
    assert mutation_events[1]["before"] == mutation_events[0]["before"]
    assert mutation_events[1]["expectedOutputTree"] == git(
        contract.memory_worktree, "rev-parse", f"{ledger_commit}^{{tree}}"
    )
    assert mutation_events[2]["expectedOutputTree"] == mutation_events[1]["expectedOutputTree"]
    assert observed["head"] == ledger_commit
    assert observed["headTree"] == mutation_events[1]["expectedOutputTree"]
    durable = store.read()
    assert durable is not None and durable.recoveryCommits is not None
    assert durable.recoveryCommits.model_dump() == {
        "codeCommit": code_commit,
        "memoryContentCommit": memory_commit,
        "ledgerCommit": ledger_commit,
    }
    assert durable.mutationEvidence["ledger"].state == "commit-proven"


def test_resumed_external_output_uses_exact_recovery_tuple_without_refresh(
    tmp_path: Path,
) -> None:
    contract, operation_input, store, runtime, code_commit, memory_commit = (
        _journaled_ledger_fixture(tmp_path)
    )
    resume_external_commits(
        contract,
        WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=operation_input.effectiveInput,
            operation_progress=runtime.progress,
        ),
        operation_input.effectiveInput,
        code_commit=code_commit,
        memory_commit=memory_commit,
    )
    durable = store.read()
    assert durable is not None and durable.recoveryCommits is not None
    events: list[tuple[str, dict[str, object]]] = []

    def progress(phase: str, evidence: Mapping[str, object]) -> None:
        events.append((phase, dict(evidence)))
        runtime.progress(phase, evidence)

    with mock.patch.object(closeout_external, "_refresh_external_memory") as refresh:
        outcome = closeout_external.external_closeout_commits(
            contract,
            WorktreeArgs(
                contract_path=contract.contract_path,
                closeout_input=operation_input.effectiveInput,
                recovery_commits=durable.recoveryCommits,
                operation_progress=progress,
            ),
            operation_input.effectiveInput,
            VerifiedChange(code_commit, "2026-08-22", []),
            {},
        )
    refresh.assert_not_called()
    assert outcome.memory_commit == memory_commit
    assert outcome.ledger_commit == durable.recoveryCommits.ledgerCommit
    assert events == [
        (
            "ledger-commit",
            {
                "current_command": "verified-existing ledger commit recorded for recovery",
                "recovery_commits": durable.recoveryCommits.model_dump(),
            },
        )
    ]
