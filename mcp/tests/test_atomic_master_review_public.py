"""Public MCP proofs for the atomic-master review boundary (CCR-R26)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.models.lifecycles.operation import IntegrateOperationInput
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    publish_atomic_series_selection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    start_or_observe_operation,
)
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from closeout_input_test_support import (
    closeout_operation_input,
    finish_operation_record,
    publish_closeout_finalization,
    start_closeout_operation,
    start_operation_record,
)
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from test_closeout_queue import MASTER_A, QueueFixture, git

from mcp import ClientSession, StdioServerParameters

pytestmark = pytest.mark.integration


async def _call_registered(
    settings_path: Path,
    tool: str,
    arguments: dict[str, Any],
) -> CallToolResult:
    """Call the real MCP stdio server, retaining both wire result surfaces."""

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", settings_path.as_posix()],
        env=dict(os.environ),
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=60)
        return await asyncio.wait_for(
            session.call_tool(tool, arguments),
            timeout=60,
        )


def _call(
    settings_path: Path,
    tool: str,
    arguments: dict[str, Any],
) -> CallToolResult:
    return asyncio.run(_call_registered(settings_path, tool, arguments))


def _assert_wire_payload(result: CallToolResult) -> dict[str, Any]:
    """Require the registered server's readable text and structured result to agree."""

    assert result.isError is False
    assert result.structuredContent is not None
    payload = result.structuredContent
    assert isinstance(payload, dict)
    text_blocks = [block for block in result.content if isinstance(block, TextContent)]
    assert text_blocks
    assert json.loads(text_blocks[0].text) == payload
    return payload


def _remove_leaf_review(contract: WorktreeContract) -> Path:
    path = contract.task_root / "leaf-a.json"
    document = read_task_doc(path)
    write_task_doc(contract.task_root, document.model_copy(update={"routeReview": None}))
    return path


def _integrate_exact_leaf(
    config_path: Path,
    leaf: WorktreeContract,
    candidate_commit: str,
) -> dict[str, Any]:
    """Run the production leaf landing after public dry-run admission."""

    operation_input = IntegrateOperationInput(
        configPath=config_path.as_posix(),
        contractPath=leaf.contract_path.as_posix(),
    )
    start_or_observe_operation(operation_input, leaf, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(leaf.worktree_group, "integrate"))
    running = store.read()
    assert running is not None
    args = WorktreeArgs(
        contract_path=leaf.contract_path,
        certification_profile=Path("mcp/certification-profile-v1.json"),
        strategy="ff-only",
        approved=True,
        operation_key=running.operationKey,
        operation_generation=running.generation,
    )
    result = integrate_module.integrate_result(args, load_contract(leaf.contract_path))
    assert result.returncode == 0
    assert result.payload["state"] == "integrated"
    assert git(leaf.code_repo_path, "rev-parse", leaf.code_source_branch) == candidate_commit
    return result.payload


def _prepare_atomic_leaf_landing(
    fixture: QueueFixture,
    contract: WorktreeContract,
) -> tuple[WorktreeContract, str]:
    """Create one review-free atomic leaf closeout source for the landing proof."""

    assert contract.parent_contract_path is not None
    publish_atomic_series_selection(
        load_contract(contract.parent_contract_path),
        "active",
        timestamp="2026-08-15T00:00:00+00:00",
    )
    git(contract.code_worktree, "add", "-A")
    git(contract.code_worktree, "commit", "-m", "atomic child candidate")
    candidate_commit = git(contract.code_worktree, "rev-parse", "HEAD")
    fixture.declare(MASTER_A)
    contract = load_contract(contract.contract_path)
    start_closeout_operation(
        closeout_operation_input(
            contract,
            config_path=fixture.config_path,
            approval_note="approved atomic child candidate",
        ),
        launcher=lambda *_: None,
    )
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    assert start_operation_record(store).status == "running"
    finalized = replace(
        load_contract(contract.contract_path),
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=candidate_commit,
    )
    write_contract(finalized.contract_path, finalized)
    publish_closeout_finalization(store, finalized)
    finish_operation_record(store, {"state": "closed"}, ok=True)
    return load_contract(finalized.contract_path), candidate_commit


def test_closeout_never_gates_on_a_route_review_record_at_either_altitude(
    tmp_path: Path,
) -> None:
    """Closeout is the trifecta, the commit messages and ancestry -- never a review record.

    The route-review *gate* at closeout was removed by design: quality is checked focused
    within the leaves, and the adversarial review that precedes integration is a process
    step owned by the reviewer role rather than a code gate. Closeout therefore consults no
    route-review record at either altitude -- an atomic child is not deferred into one, and
    an organizational leaf is not refused for the lack of one. The review *record* survives
    as task shape (``task_doc.record_route_review``); only the gate is gone. This pins that
    removal at both altitudes so the gate cannot return unnoticed, and it keeps the real
    atomic leaf-to-master landing proven with both task documents review-free.
    """

    atomic_fixture = QueueFixture(tmp_path / "atomic", atomic_a=True, memory_mode="internal")
    atomic_contract = atomic_fixture.contracts[MASTER_A]
    atomic_doc_path = _remove_leaf_review(atomic_contract)
    before_doc = atomic_doc_path.read_bytes()
    before_contract = atomic_contract.contract_path.read_bytes()
    before_head = git(atomic_contract.code_worktree, "rev-parse", "HEAD")

    atomic_payload = _assert_wire_payload(
        _call(
            atomic_fixture.config_path,
            "worktree_closeout_preview",
            {
                "contract_path": atomic_contract.contract_path.as_posix(),
                "code_commit_message": "atomic child candidate",
            },
        )
    )
    assert atomic_payload["ok"] is True
    assert "route_review" not in atomic_payload
    assert atomic_doc_path.read_bytes() == before_doc
    assert atomic_contract.contract_path.read_bytes() == before_contract
    assert git(atomic_contract.code_worktree, "rev-parse", "HEAD") == before_head

    # The preview above is followed by the real leaf-to-master landing path.  The disposable
    # fixture activates the atomic master and records the closeout journal without
    # manufacturing a route-review record; integration must still move the master's protected
    # branch while both the child and master task documents remain review-free.
    atomic_contract, leaf_commit = _prepare_atomic_leaf_landing(atomic_fixture, atomic_contract)
    assert read_task_doc(atomic_doc_path).routeReview is None
    assert read_task_doc(atomic_contract.task_root / "task.json").routeReview is None

    leaf_preview = _assert_wire_payload(
        _call(
            atomic_fixture.config_path,
            "worktree_integrate",
            {
                "contract_path": atomic_contract.contract_path.as_posix(),
                "strategy": "ff-only",
                "dry_run": True,
            },
        )
    )
    assert leaf_preview["ok"] is True
    assert leaf_preview["state"] == "would-integrate"
    assert git(atomic_contract.code_repo_path, "rev-parse", atomic_contract.code_source_branch) != (
        leaf_commit
    )
    landed = _integrate_exact_leaf(
        atomic_fixture.config_path,
        atomic_contract,
        leaf_commit,
    )
    assert landed["state"] == "integrated"
    assert git(atomic_contract.code_repo_path, "rev-parse", atomic_contract.code_source_branch) == (
        leaf_commit
    )
    assert read_task_doc(atomic_doc_path).routeReview is None
    assert read_task_doc(atomic_contract.task_root / "task.json").routeReview is None

    organizational_fixture = QueueFixture(tmp_path / "organizational", memory_mode="internal")
    organizational_contract = organizational_fixture.contracts[MASTER_A]
    _remove_leaf_review(organizational_contract)
    organizational_payload = _assert_wire_payload(
        _call(
            organizational_fixture.config_path,
            "worktree_closeout_preview",
            {
                "contract_path": organizational_contract.contract_path.as_posix(),
                "code_commit_message": "organizational candidate",
            },
        )
    )
    assert organizational_payload["ok"] is True
    assert "route_review" not in organizational_payload
