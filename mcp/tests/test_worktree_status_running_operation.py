"""Status keeps complete operations in its saved result and progress in its compact answer.

The closeout tool's own description tells its caller to "Poll worktree_status for progress"
(`mcp/registration/closeout.py`). A complete operation projection is larger than the reply's bound,
so the saved result retains the list while the reply carries its count and one running sentence.
That sentence survives the size guard even when another part of the answer forces a cut.

This test enters through the tool itself, over a real server session, with a closeout the product's
own admission claimed (`start_closeout_operation`), and reads the delivered answer and saved file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import anyio
import pytest
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.server import create_server
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operation_projection
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle.observation.projection import (
    current_operation_projections,
)
from closeout_input_test_support import (
    closeout_operation_input,
    finish_operation_record,
    start_closeout_operation,
)
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from selected_lifecycle_test_support import selected_contract

pytestmark = pytest.mark.integration


def _world(tmp_path: Path):
    contract = selected_contract(tmp_path, candidate_file=("candidate.py", "VALUE = 1\n"))
    config = load_config(contract.code_repo_path.parent / "settings.json")
    server = create_server(config)
    arguments: dict[str, object] = {
        "repo_id": contract.repo_name,
        "contract_path": contract.contract_path.as_posix(),
    }

    async def status() -> dict[str, object]:
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            result = await client.call_tool("worktree_status", arguments)
        text = next(block.text for block in result.content if isinstance(block, TextContent))
        return json.loads(text)

    return contract, status


def test_a_claimed_closeout_is_named_in_the_answer_the_guard_trims(tmp_path: Path) -> None:
    contract, status = _world(tmp_path)

    before = anyio.run(status)
    assert "runningOperation" not in before
    # The reply carries the count; the saved result carries the complete operation list.
    assert before["lifecycleOperationCount"] == 0
    assert "lifecycleOperations" not in before

    started = start_closeout_operation(
        closeout_operation_input(contract, code="close the candidate"), launcher=lambda *_: None
    )
    trimmed = anyio.run(status)

    assert trimmed["lifecycleOperationCount"] == 1
    assert "lifecycleOperations" not in trimmed, trimmed
    line = trimmed["runningOperation"]
    assert isinstance(line, str)
    assert line.startswith(f"closeout generation {started.generation} is ")
    assert "worktree_operation_control" in line
    # It says the operation is alive, and it stays inside the guard's short-string limit.
    assert "queued" in line or "running" in line
    assert len(line) <= 200


def test_a_status_answer_with_one_running_closeout_is_not_cut(tmp_path: Path) -> None:
    """A single running closeout fits once its full projection stays in the saved result."""

    contract, status = _world(tmp_path)
    start_closeout_operation(
        closeout_operation_input(contract, code="close the candidate"), launcher=lambda *_: None
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(update={"status": "running", "phase": "preflight"})
    )

    answer = anyio.run(status)

    assert not answer.get("overflow"), answer
    assert answer["lifecycleOperationCount"] == 1
    assert "runningOperation" in answer
    assert "lifecycleOperations" not in answer


def test_the_saved_status_result_keeps_every_operation_projection(tmp_path: Path) -> None:
    contract, status = _world(tmp_path)
    now = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)
    start_closeout_operation(
        closeout_operation_input(contract, code="close the candidate"),
        launcher=lambda *_: None,
        now=now,
    )
    integrate = LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        operationKind="integrate",
        candidateState="a" * 64,
        fingerprint="b" * 64,
        operationKey="c" * 64,
        input=IntegrateOperationInput(
            configPath=(contract.code_repo_path.parent / "settings.json").as_posix(),
            contractPath=contract.contract_path.as_posix(),
        ),
        integrationAuthority=IntegrationOperationAuthority(
            targetKind="atomic-integration",
            codeRepository=contract.code_repo_path.as_posix(),
            codeSourceBranch=contract.code_source_branch,
            codeSourceRef=f"refs/heads/{contract.code_source_branch}",
            codeSourceCommit=contract.code_base_commit,
            codeCandidateCommit=contract.code_base_commit,
        ),
        status="completed",
        phase="completed",
        queuedAt=now.isoformat(),
        finishedAt=now.isoformat(),
        reportPath=(contract.worktree_group / "integrate-report.md").as_posix(),
        result={"state": "integrated", "commits": {"code": contract.code_base_commit}},
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
    store.create(integrate)

    # Freeze only projection time so the entire nested observation can be compared exactly.
    with mock.patch.object(lifecycle_operation_projection, "datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        expected = [
            operation.model_dump(mode="json", exclude_none=True)
            for operation in current_operation_projections(
                contract.contract_path, contract=contract
            )
        ]
        answer = anyio.run(status)

    assert [operation["kind"] for operation in expected] == ["closeout", "integrate"]
    assert answer["lifecycleOperationCount"] == len(expected)
    assert "lifecycleOperations" not in answer
    assert isinstance(answer["runningOperation"], str)
    assert "closeout generation 1 is queued" in answer["runningOperation"]
    assert not answer.get("overflow"), answer
    assert not answer.get("cut"), answer
    saved = json.loads(Path(str(answer["log"])).read_text(encoding="utf-8"))
    assert saved["lifecycleOperations"] == expected


def test_a_running_closeout_is_named_with_its_phase(tmp_path: Path) -> None:
    contract, status = _world(tmp_path)
    start_closeout_operation(
        closeout_operation_input(contract, code="close the candidate"), launcher=lambda *_: None
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(update={"status": "running", "phase": "preflight"})
    )

    trimmed = anyio.run(status)

    assert trimmed["lifecycleOperationCount"] == 1
    line = trimmed["runningOperation"]
    assert isinstance(line, str)
    assert " is running" in line
    assert "phase preflight" in line


def test_a_finished_closeout_is_not_named_as_running(tmp_path: Path) -> None:
    contract, status = _world(tmp_path)
    start_closeout_operation(
        closeout_operation_input(contract, code="close the candidate"), launcher=lambda *_: None
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    store.update(
        lambda record: record.model_copy(update={"status": "running", "phase": "preflight"})
    )
    finish_operation_record(store, {"state": "completed", "summary": "closeout completed"}, ok=True)

    answer = anyio.run(status)

    assert "runningOperation" not in answer
