"""MIK-R52 F02: a refused integration alters neither the contract nor any file.

Preview and apply share the refusal boundary: the gate refusal carries
``knowledge-gate-refused`` in memory while the contract bytes, the recomputed worklist
file, and both ref inventories stay exactly as they were. Each test snapshots all four
before the call and compares after.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.knowledge_worklist.leaf import worklist_path
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.modules import record_landing
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integration_gate import integration_gate_block
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    A,
    Gated,
    _series,
    build_gated,
    commit,
)


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _snapshot(world: Gated, contract: Any) -> tuple[bytes | None, bytes | None, str]:
    path = Path(contract.contract_path)
    contract_bytes = path.read_bytes() if path.is_file() else None
    list_path = worklist_path(contract)
    list_bytes = list_path.read_bytes() if list_path is not None and list_path.is_file() else None
    refs = (
        _git(world.code, "for-each-ref")
        + "\n"
        + _git(world.memory, "for-each-ref")
    )
    return contract_bytes, list_bytes, refs


def _assert_untouched(
    world: Gated, contract: Any, before: tuple[bytes | None, bytes | None, str]
) -> None:
    path = Path(contract.contract_path)
    if before[0] is None:
        assert not path.is_file()
    else:
        assert path.read_bytes() == before[0]
    list_path = worklist_path(contract)
    if before[1] is None:
        assert list_path is None or not list_path.is_file()
    else:
        assert list_path is not None and list_path.read_bytes() == before[1]
    assert (
        _git(world.code, "for-each-ref") + "\n" + _git(world.memory, "for-each-ref")
    ) == before[2]


def _refusing_leaf(world: Gated) -> tuple[Any, IntegratedCommits, IntegrationSources]:
    """A leaf pair the gate genuinely refuses: a code change with no answering rows."""

    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    memory_commit = commit(world.memory, {}, trailer=code_commit)
    commits = IntegratedCommits(code=code_commit, memory_content=memory_commit)
    sources = IntegrationSources(world.code_base, world.memory_base, False, False)
    return world.contract, commits, sources


def test_refused_leaf_integration_preview_writes_nothing(world: Gated, ports: None) -> None:
    """F02 preview: the refusal payload arrives, the contract/worklist/refs do not move."""

    contract, commits, sources = _refusing_leaf(world)
    before = _snapshot(world, contract)
    blocked = integration_gate_block(
        contract,
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        commits,
        sources,
        operation="worktree_integrate",
    )
    assert blocked is not None and blocked.returncode == 2
    assert blocked.payload["state"] == "knowledge-gate-refused"
    _assert_untouched(world, contract, before)


def test_refused_leaf_integration_apply_writes_nothing(world: Gated, ports: None) -> None:
    """F02 apply: the reviewer's counterexample shape — dry_run=False still writes nothing."""

    contract, commits, sources = _refusing_leaf(world)
    before = _snapshot(world, contract)
    blocked = integration_gate_block(
        contract,
        WorktreeArgs(contract_path=contract.contract_path, dry_run=False),
        commits,
        sources,
        operation="worktree_integrate",
    )
    assert blocked is not None and blocked.returncode == 2
    assert blocked.payload["state"] == "knowledge-gate-refused"
    # The refusal still reports the blocked decision in memory ...
    assert blocked.payload["integration_status"] == "blocked"
    # ... while the contract file on disk is untouched.
    _assert_untouched(world, contract, before)


def test_refused_series_integration_writes_nothing(world: Gated, ports: None) -> None:
    """F02 series: a refusing master pair leaves the series contract and refs alone."""

    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    memory_commit = commit(world.memory, {}, trailer=code_commit)  # nothing re-anchored: stale
    series = replace(_series(world), code_commit=code_commit, memory_content_commit=memory_commit)
    sources = IntegrationSources(world.code_base, world.memory_base, False, False)
    commits = IntegratedCommits(code=code_commit, memory_content=memory_commit)
    before = _snapshot(world, series)
    blocked = integration_gate_block(
        series,
        WorktreeArgs(contract_path=series.contract_path, dry_run=False),
        commits,
        sources,
        operation="worktree_integrate",
    )
    assert blocked is not None and blocked.returncode == 2
    assert blocked.payload["state"] == "knowledge-gate-refused"
    _assert_untouched(world, series, before)


def test_refused_record_landing_writes_nothing(world: Gated, ports: None) -> None:
    """F02 record route: a refused recorded landing changes no contract, ref, or file."""

    broken = commit(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"}, trailer=world.code_base)
    candidate = Path(world.contract.contract_path)
    before = _snapshot(world, world.contract)
    with pytest.raises(RuntimeError, match=r"R22\.3-markers"):
        record_landing.record_landing_result(
            WorktreeArgs(
                contract_path=candidate,
                dry_run=False,
                approved=True,
                landed_code_commit=world.code_base,
                landed_memory_content_commit=broken,
            )
        )
    _assert_untouched(world, world.contract, before)


def test_edited_closeout_cell_is_judged_as_edited(world: Gated, ports: None) -> None:
    """F04: the integration judges the contract's current cell, not a stale recording."""

    contract, commits, sources = _refusing_leaf(world)
    first = integration_gate_block(
        contract,
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        commits,
        sources,
        operation="worktree_integrate",
    )
    assert first is not None and first.payload["state"] == "knowledge-gate-refused"
    first_reason = str(first.payload["reason"])
    assert "R27.4-record-kept" not in first_reason
    # A different pair gets a fresh judgment: deleting a record the base holds refuses by
    # name, proving the current cell was judged and never the stale refusal.
    deleted = "knowledge/invariants/INV-BBBBBB-sibling.json"
    (world.memory / deleted).unlink()
    _git(world.memory, "add", "-A")
    other_memory = commit(world.memory, {}, trailer=commits.code)
    other = IntegratedCommits(code=commits.code, memory_content=other_memory)
    second = integration_gate_block(
        contract,
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        other,
        sources,
        operation="worktree_integrate",
    )
    assert second is not None and second.payload["state"] == "knowledge-gate-refused"
    second_reason = str(second.payload["reason"])
    assert "R27.4-record-kept" in second_reason
    assert "INV-BBBBBB" in second_reason
    _assert_untouched(world, contract, _snapshot(world, contract))
