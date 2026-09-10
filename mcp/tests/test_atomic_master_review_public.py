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
from agents_remember.application.lifecycle.lifecycle_operation_worker import OperationRuntime
from agents_remember.kernel.memory_ledger import load_ledger
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
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
from agents_remember.worktrees.route_review import route_review_refusal_projection
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from closeout_input_test_support import (
    closeout_operation_input,
    publish_closeout_finalization,
    start_closeout_operation,
)
from curator_coherence_test_support import write_curator_evidence
from integration_branch_authority_test_support import (
    _complete_atomic_master,
    _record_atomic_leaf_landing,
)
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from test_closeout_queue import LEAF_A, MASTER_A, SPRINT, QueueFixture, _grade, git
from test_source_lineage import _commit_on, _fixture

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


def _complete_series_fixture(fixture: QueueFixture) -> WorktreeContract:
    """Make a disposable series contract eligible so review is the next admission gate."""

    master_path = fixture.tasks / "master-a" / "task.json"
    master = read_task_doc(master_path)
    write_task_doc(
        master_path.parent,
        master.model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                ],
            }
        ),
    )
    series_path = fixture.tasks / "master-a" / "series-contract.md"
    series = load_contract(series_path)
    tip = git(series.code_repo_path, "rev-parse", series.code_work_branch)
    completed = replace(
        series,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=tip,
    )
    write_contract(series_path, completed)
    return load_contract(series_path)


def _master_review_payload() -> dict[str, Any]:
    return {
        "verdict": "pass",
        "verdictRef": "notes/reports/master-review.md",
        "routes": [
            {
                "route": "atomic-master",
                "verdict": "pass",
                "evidenceRef": "notes/reports/master-review.md",
            }
        ],
    }


def _write_master_review_evidence(series: WorktreeContract) -> None:
    evidence = series.task_root / "notes" / "reports" / "master-review.md"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        "Independent review of the accumulated atomic candidate.\n", encoding="utf-8"
    )


def _direct_leaf_review_payload(
    verdict: str,
    *,
    findings: list[dict[str, str]] | None = None,
    remaining_finding_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the public branch-addressed leaf review payload for direct execution."""

    payload: dict[str, Any] = {
        "verdict": verdict,
        "verdictRef": "notes/reports/direct-review.md",
        "routes": [
            {
                "route": "direct-organizational",
                "verdict": verdict,
                "evidenceRef": "notes/reports/direct-review.md",
            }
        ],
    }
    if findings is not None:
        payload["findings"] = findings
    if remaining_finding_ids is not None:
        payload["remainingFindingIds"] = remaining_finding_ids
    return payload


def _series_config(root: Path, fixture: Any) -> Path:
    """Configure the source-lineage fixture for the real MCP authority reader."""

    configured_repo = root / "repo"
    if not configured_repo.exists():
        configured_repo.symlink_to(fixture.code_repo, target_is_directory=True)
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": fixture.coordination.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {
                    "repo": {"certificationProfile": "mcp/certification-profile-v1.json"}
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _direct_organizational_fixture(
    root: Path,
) -> tuple[QueueFixture, WorktreeContract]:
    """Build a sanctioned direct series candidate with a changed named branch."""

    fixture = QueueFixture(root, memory_mode="internal")
    fixture.enable_direct_execution()
    leaf = fixture.contracts[MASTER_A]
    series = replace(
        load_contract(leaf.task_root / "series-contract.md"),
        cleanup="pending",
    )
    write_contract(series.contract_path, series)
    task_path = leaf.task_root / "leaf-a.json"
    document = read_task_doc(task_path)
    write_task_doc(leaf.task_root, document.model_copy(update={"routeReview": None}))
    git(fixture.code, "checkout", "super")
    (fixture.code / "direct-change.txt").write_text(
        "direct organizational candidate\n", encoding="utf-8"
    )
    git(fixture.code, "add", "direct-change.txt")
    git(fixture.code, "commit", "-m", "direct candidate")
    git(fixture.code, "checkout", "main")
    return fixture, series


def _call_direct_door(
    fixture: QueueFixture,
    series: WorktreeContract,
    *,
    expected_generation_id: str | None = None,
) -> CallToolResult:
    request: dict[str, Any] = {
        "action": "update-provenance" if expected_generation_id else "declare",
        "contract_path": series.contract_path.as_posix(),
        "candidate_task_document_ref": LEAF_A.model_dump(mode="json"),
        "grade": _grade("normal", LEAF_A),
        "admission": {},
        "caller": {
            "role": "manager",
            "task_document_ref": MASTER_A.model_dump(mode="json"),
        },
    }
    if expected_generation_id is not None:
        request["expected_generation_id"] = expected_generation_id
    return _call(fixture.config_path, "closeout_door", {"request": request})


def _exercise_blocked_direct_review(root: Path) -> None:
    """Keep blocked and fixed route-gate coverage on a finite finding sequence."""

    fixture, series = _direct_organizational_fixture(root)
    source_before = git(series.code_repo_path, "rev-parse", series.code_source_branch)
    evidence = series.task_root / "notes" / "reports" / "direct-review.md"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("Direct blocked review evidence.\n", encoding="utf-8")
    _assert_wire_payload(
        _call(
            fixture.config_path,
            "task_doc",
            {
                "repo_id": "repo-a",
                "operation": "begin_review",
                "contract_path": series.contract_path.as_posix(),
                "slug": "leaf-a",
                "review": {},
            },
        )
    )
    blocked_publication = _assert_wire_payload(
        _call(
            fixture.config_path,
            "task_doc",
            {
                "repo_id": "repo-a",
                "operation": "record_route_review",
                "contract_path": series.contract_path.as_posix(),
                "slug": "leaf-a",
                "review": _direct_leaf_review_payload(
                    "block",
                    findings=[{"findingId": "DIRECT-BLOCK", "description": "verify the fix"}],
                ),
                "branch_addressed": True,
            },
        )
    )
    assert blocked_publication["ok"] is True
    blocked = _call_direct_door(fixture, series)
    assert blocked.isError is True
    blocked_text = [block.text for block in blocked.content if isinstance(block, TextContent)]
    assert blocked_text
    assert "closeout-door-route-review-blocked" in blocked_text[0]
    assert load_contract(series.contract_path).closeout_door is None
    assert git(series.code_repo_path, "rev-parse", series.code_source_branch) == source_before
    _assert_wire_payload(
        _call(
            fixture.config_path,
            "task_doc",
            {
                "repo_id": "repo-a",
                "operation": "begin_review",
                "contract_path": series.contract_path.as_posix(),
                "slug": "leaf-a",
                "review": {},
            },
        )
    )
    current = _assert_wire_payload(
        _call(
            fixture.config_path,
            "task_doc",
            {
                "repo_id": "repo-a",
                "operation": "record_route_review",
                "contract_path": series.contract_path.as_posix(),
                "slug": "leaf-a",
                "review": _direct_leaf_review_payload("pass", remaining_finding_ids=[]),
                "branch_addressed": True,
            },
        )
    )
    assert current["reviewState"]["remainingFindingIds"] == []
    declared = _assert_wire_payload(_call_direct_door(fixture, series))
    assert declared["generation"]["reviewProvenance"]["state"] == "proven"


def _ambiguous_memory_commit_prefix(repository: Path, parent: str) -> str:
    """Create disposable commit objects until Git has an ambiguous four-digit prefix."""

    commits: set[str] = set(git(repository, "rev-list", "--all").splitlines())
    tree = git(repository, "rev-parse", f"{parent}^{{tree}}")
    for index in range(2048):
        commit = git(
            repository,
            "commit-tree",
            tree,
            "-p",
            parent,
            "-m",
            f"CCR-CQ07 ambiguous identity {index}",
        )
        commits.add(commit)
        prefix = commit[:4]
        if sum(candidate.startswith(prefix) for candidate in commits) > 1:
            return prefix
    raise AssertionError("fixture could not create an ambiguous four-digit commit prefix")


def _external_atomic_ledger_door_case(
    root: Path,
    replacement: str | None,
    *,
    abbreviated: bool = False,
) -> tuple[CallToolResult, WorktreeContract, bytes, bytes, str, str]:
    """Prepare one ready atomic leaf with the requested ledger identity spelling."""

    fixture = QueueFixture(root, atomic_a=True, memory_mode="external")
    leaf = fixture.contracts[MASTER_A]
    master_path = leaf.task_root / "task.json"
    master = read_task_doc(master_path)
    write_task_doc(
        leaf.task_root,
        master.model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                ],
            }
        ),
    )
    leaf_path = leaf.task_root / "leaf-a.json"
    document = read_task_doc(leaf_path)
    write_task_doc(
        leaf.task_root,
        document.model_copy(update={"status": "Completed", "routeReview": None}),
    )
    leaf = load_contract(leaf.contract_path)
    assert leaf.ledger_path is not None
    ledger_bytes = leaf.ledger_path.read_bytes()
    ledger = load_ledger(leaf.ledger_path)
    mapped = next(
        row.memory_commit for row in ledger.rows if row.code_commit == leaf.code_base_commit
    )
    assert leaf.memory_repo_path is not None
    if replacement == "noncommit":
        replacement = git(leaf.memory_repo_path, "rev-parse", f"{mapped}^{{tree}}")
    elif replacement == "ambiguous":
        replacement = _ambiguous_memory_commit_prefix(leaf.memory_repo_path, mapped)
        assert (
            len(
                git(
                    leaf.memory_repo_path, "rev-parse", f"--disambiguate={replacement}"
                ).splitlines()
            )
            > 1
        )
    else:
        replacement = mapped[:8] if abbreviated else mapped if replacement is None else replacement
    leaf.ledger_path.write_text(
        ledger_bytes.decode("utf-8").replace(mapped, replacement),
        encoding="utf-8",
    )
    write_curator_evidence(leaf, caller_ref=SPRINT)
    request = {
        "action": "declare",
        "contract_path": leaf.contract_path.as_posix(),
        "grade": _grade("normal", LEAF_A),
        "admission": {},
        "caller": {
            "role": "manager",
            "task_document_ref": MASTER_A.model_dump(mode="json"),
        },
    }
    before_contract = leaf.contract_path.read_bytes()
    before_source = git(leaf.code_repo_path, "rev-parse", leaf.code_source_branch)
    before_ledger = leaf.ledger_path.read_bytes()
    result = _call(fixture.config_path, "closeout_door", {"request": request})
    return result, leaf, before_contract, before_ledger, before_source, mapped


def _assert_external_ledger_identity_cases(root: Path) -> None:
    """Exercise valid and invalid ledger identities through the registered door."""

    full_result, full_leaf, _, full_ledger, full_source, full_commit = (
        _external_atomic_ledger_door_case(root / "full", None)
    )
    full_payload = _assert_wire_payload(full_result)
    assert full_payload["generation"]["ledgerMemoryCommit"] == full_commit
    assert full_payload["generation"]["reviewProvenance"]["state"] == "not-applicable"
    assert full_leaf.ledger_path is not None
    assert full_leaf.ledger_path.read_bytes() == full_ledger
    assert git(full_leaf.code_repo_path, "rev-parse", full_leaf.code_source_branch) == full_source

    short_result, short_leaf, _, short_ledger, short_source, short_commit = (
        _external_atomic_ledger_door_case(root / "short", None, abbreviated=True)
    )
    short_payload = _assert_wire_payload(short_result)
    assert short_payload["generation"]["ledgerMemoryCommit"] == short_commit
    assert short_payload["generation"]["reviewProvenance"]["state"] == "not-applicable"
    assert short_leaf.ledger_path is not None
    assert short_leaf.ledger_path.read_bytes() == short_ledger
    assert (
        git(short_leaf.code_repo_path, "rev-parse", short_leaf.code_source_branch) == short_source
    )

    missing_result, missing_leaf, missing_contract, missing_ledger, missing_source, _ = (
        _external_atomic_ledger_door_case(root / "missing", "0" * 40)
    )
    assert missing_result.isError is True
    missing_text = [
        block.text for block in missing_result.content if isinstance(block, TextContent)
    ]
    assert missing_text
    assert "closeout-door-ledger-incompatible" in missing_text[0]
    assert "0000000000000000000000000000000000000000" in missing_text[0]
    assert missing_leaf.contract_path.read_bytes() == missing_contract
    assert missing_leaf.ledger_path is not None
    assert missing_leaf.ledger_path.read_bytes() == missing_ledger
    assert git(missing_leaf.code_repo_path, "rev-parse", missing_leaf.code_source_branch) == (
        missing_source
    )

    for label, identity in (("noncommit", "noncommit"), ("ambiguous", "ambiguous")):
        invalid_result, invalid_leaf, invalid_contract, invalid_ledger, invalid_source, _ = (
            _external_atomic_ledger_door_case(root / label, identity)
        )
        assert invalid_result.isError is True
        invalid_text = [
            block.text for block in invalid_result.content if isinstance(block, TextContent)
        ]
        assert invalid_text
        assert "closeout-door-ledger-incompatible" in invalid_text[0]
        assert "exact Git commit cannot be resolved" in invalid_text[0]
        assert invalid_leaf.contract_path.read_bytes() == invalid_contract
        assert invalid_leaf.ledger_path is not None
        assert invalid_leaf.ledger_path.read_bytes() == invalid_ledger
        assert git(invalid_leaf.code_repo_path, "rev-parse", invalid_leaf.code_source_branch) == (
            invalid_source
        )

    long_result, long_leaf, long_contract, long_ledger, long_source, _ = (
        _external_atomic_ledger_door_case(root / "long", "x" * 10_000)
    )
    assert long_result.isError is True
    long_text = [block.text for block in long_result.content if isinstance(block, TextContent)]
    assert long_text
    assert len(long_text[0]) < 2_048
    assert "closeout-door-ledger-incompatible" in long_text[0]
    assert "exact Git commit cannot be resolved" in long_text[0]
    assert "[truncated]" in long_text[0]
    assert long_leaf.contract_path.read_bytes() == long_contract
    assert long_leaf.ledger_path is not None
    assert long_leaf.ledger_path.read_bytes() == long_ledger
    assert git(long_leaf.code_repo_path, "rev-parse", long_leaf.code_source_branch) == long_source


def _real_atomic_series_fixture(root: Path) -> tuple[Any, Path, WorktreeContract, str]:
    """Build one child landing and a completed master in disposable Git repositories."""

    fixture = _fixture(root, external_memory=False, selected_profile=True)
    (fixture.code_repo / "ar-memory").mkdir()
    # The source-lineage helper defaults to disabled memory; the public authority derives
    # internal memory from this repository-owned directory, so align both contracts before
    # any mutation path rereads them.
    write_contract(
        fixture.master_contract.contract_path,
        replace(fixture.master_contract, memory_mode="internal"),
    )
    fixture.master_contract = replace(fixture.master_contract, memory_mode="internal")
    write_contract(
        fixture.leaf_contract.contract_path,
        replace(fixture.leaf_contract, memory_mode="internal", memory_state=""),
    )
    fixture.leaf_contract = replace(fixture.leaf_contract, memory_mode="internal", memory_state="")
    _commit_on(fixture.code_repo, "ar/master", "atomic-candidate.txt")
    candidate_commit = git(fixture.code_repo, "rev-parse", "ar/master")
    _record_atomic_leaf_landing(fixture, candidate_commit)
    _complete_atomic_master(fixture)
    series = load_contract(fixture.master_contract.contract_path)
    series = replace(
        series,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=candidate_commit,
    )
    write_contract(series.contract_path, series)
    fixture.master_contract = series
    config_path = _series_config(root, fixture)
    return fixture, config_path, series, candidate_commit


def _run_exact_candidate(
    config_path: Path,
    series: WorktreeContract,
    candidate_commit: str,
) -> Any:
    """Run the production mutation path with quality isolated in the fixture."""

    del candidate_commit
    operation_input = IntegrateOperationInput(
        configPath=config_path.as_posix(),
        contractPath=series.contract_path.as_posix(),
    )
    start_or_observe_operation(operation_input, series, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(series.worktree_group, "integrate"))
    running = OperationRuntime(store).start()
    args = WorktreeArgs(
        contract_path=series.contract_path,
        certification_profile=Path("mcp/certification-profile-v1.json"),
        strategy="ff-only",
        approved=True,
        operation_key=running.operationKey,
        operation_generation=running.generation,
    )

    def quality_stub(
        contract: WorktreeContract,
        *,
        completion: object | None = None,
        args: WorktreeArgs,
    ) -> tuple[dict[str, object], None]:
        del contract, completion, args
        return {"status": "test-pass", "passed": True}, None

    original_quality_gate = integrate_module._run_integration_quality_gate
    integrate_module._run_integration_quality_gate = quality_stub
    try:
        return integrate_module.integrate_result(args, load_contract(series.contract_path))
    finally:
        integrate_module._run_integration_quality_gate = original_quality_gate


def _integrate_exact_candidate(
    config_path: Path,
    series: WorktreeContract,
    candidate_commit: str,
) -> dict[str, Any]:
    """Run the production mutation path after public admission, with quality isolated."""

    result = _run_exact_candidate(config_path, series, candidate_commit)
    assert result.returncode == 0
    assert result.payload["state"] == "integrated"
    assert git(series.code_repo_path, "rev-parse", series.code_source_branch) == candidate_commit
    return result.payload


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
    running = OperationRuntime(store).start()
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
    runtime = OperationRuntime(store)
    assert runtime.start().status == "running"
    finalized = replace(
        load_contract(contract.contract_path),
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=candidate_commit,
    )
    write_contract(finalized.contract_path, finalized)
    publish_closeout_finalization(runtime, finalized)
    runtime.finish({"state": "closed"}, ok=True)
    return load_contract(finalized.contract_path), candidate_commit


def test_atomic_leaf_public_closeout_defers_review_and_organizational_leaf_keeps_gate(
    tmp_path: Path,
) -> None:
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
    assert atomic_payload["route_review"] == {
        "required": False,
        "status": "deferred-atomic-child",
        "masterRef": {"repository": "repo-a", "path": "master-a/task.json"},
        "childCount": 1,
    }
    assert atomic_doc_path.read_bytes() == before_doc
    assert atomic_contract.contract_path.read_bytes() == before_contract
    assert git(atomic_contract.code_worktree, "rev-parse", "HEAD") == before_head

    # The public deferral above is followed by the real leaf-to-master landing path.  The
    # disposable fixture activates the atomic master and records the closeout journal without
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
    assert organizational_payload["ok"] is False
    assert organizational_payload["status"] == "route-review-required"
    assert (
        organizational_payload["detail"]
        == "the current code change has no independent route-review record"
    )
    assert organizational_payload["expected"] == {
        "routeReview": "current passing review bound to this candidate"
    }
    assert organizational_payload["observed"] == {"routeReviewStatus": "route-review-required"}
    assert organizational_payload["nextAction"] == "record_route_review"
    assert organizational_payload["nextTool"] == "task_doc"
    assert organizational_payload["nextArgs"] == {
        "repo_id": "repo-a",
        "operation": "record_route_review",
        "contract_path": organizational_contract.contract_path.as_posix(),
    }
    assert organizational_payload["nextRequiredArgs"] == ["review"]
    assert organizational_payload["nextStep"] == {
        "summary": "Record or refresh the route review for the exact current candidate, then retry closeout.",
        "nextOperation": "record_route_review",
        "nextTool": "task_doc",
        "nextArgs": organizational_payload["nextArgs"],
        "nextRequiredArgs": ["review"],
    }

    _assert_external_ledger_identity_cases(tmp_path / "external")



def test_atomic_master_public_integration_refuses_without_review_before_ref_move(
    tmp_path: Path,
) -> None:
    fixture = QueueFixture(tmp_path, atomic_a=True, memory_mode="internal")
    series = _complete_series_fixture(fixture)
    before_ref = git(series.code_repo_path, "rev-parse", series.code_source_branch)
    before_contract = series.contract_path.read_bytes()

    payload = _assert_wire_payload(
        _call(
            fixture.config_path,
            "worktree_integrate",
            {
                "contract_path": series.contract_path.as_posix(),
                "strategy": "ff-only",
                "dry_run": True,
            },
        )
    )
    detail = (
        "atomic master integration has no independent review of the accumulated candidate; "
        "publish task_doc.record_route_review on the canonical master"
    )
    assert payload["ok"] is False
    assert payload["state"] == "route-review-master-required"
    assert payload["reason"] == detail
    assert payload["reviewRefusal"] == {"status": "route-review-master-required", "detail": detail}
    assert payload["expected"] == {
        "routeReview": "current passing review of the accumulated master candidate"
    }
    assert payload["observed"] == {"routeReview": "missing"}
    assert payload["nextAction"] == "record_route_review"
    assert payload["developerDecisionRequired"] is False
    assert payload["nextTool"] == "task_doc"
    assert payload["nextArgs"] == {
        "repo_id": "repo-a",
        "operation": "record_route_review",
        "contract_path": series.contract_path.as_posix(),
    }
    assert payload["nextRequiredArgs"] == ["review"]
    assert payload["statusAction"] == {
        "tool": "worktree_status",
        "args": {
            "repo_id": "repo-a",
            "contract_path": series.contract_path.as_posix(),
        },
    }
    assert payload["nextStep"] == {
        "summary": "Record or refresh the independent master route review for the exact accumulated candidate, then retry integration.",
        "nextOperation": "record_route_review",
        "nextTool": "task_doc",
        "nextArgs": payload["nextArgs"],
        "nextRequiredArgs": ["review"],
    }

    candidate_detail = "journaled candidate commit deadbeef cannot be resolved"
    candidate_projection = route_review_refusal_projection(
        "route-review-master-candidate-unreadable",
        candidate_detail,
        contract=series,
        boundary="integration",
    )
    assert candidate_projection["detail"] == candidate_detail
    assert candidate_projection["expected"] == {
        "candidate": "readable journaled Git candidate commit"
    }
    assert candidate_projection["observed"] == {"candidateStatus": "unreadable"}
    assert candidate_projection["nextAction"] == "inspect_task_document"
    assert candidate_projection["nextTool"] == "worktree_status"
    assert candidate_projection["nextArgs"] == {
        "repo_id": series.repo_name,
        "contract_path": series.contract_path.as_posix(),
    }
    assert candidate_projection["statusAction"] == {
        "tool": "worktree_status",
        "args": candidate_projection["nextArgs"],
    }
    assert "nextRequiredArgs" not in candidate_projection
    candidate_next_step = candidate_projection["nextStep"]
    assert isinstance(candidate_next_step, dict)
    assert candidate_next_step["nextTool"] == "worktree_status"
    assert candidate_next_step["nextArgs"] == candidate_projection["nextArgs"]

    assert git(series.code_repo_path, "rev-parse", series.code_source_branch) == before_ref
    assert series.contract_path.read_bytes() == before_contract


def test_public_master_review_stamps_branch_candidate_and_allows_exact_protected_landing(
    tmp_path: Path,
) -> None:
    _fixture_value, config_path, series, candidate_commit = _real_atomic_series_fixture(tmp_path)
    _write_master_review_evidence(series)
    review_args = {
        "repo_id": "repo",
        "operation": "record_route_review",
        "contract_path": series.contract_path.as_posix(),
    }
    begun = _assert_wire_payload(
        _call(
            config_path,
            "task_doc",
            {
                "repo_id": "repo",
                "operation": "begin_review",
                "contract_path": series.contract_path.as_posix(),
                "review": {},
            },
        )
    )
    assert begun["reviewState"]["pending"] is True
    invalid = _call(
        config_path,
        "task_doc",
        {
            **review_args,
            "review": {**_master_review_payload(), "candidateTree": "0" * 40},
        },
    )
    assert invalid.isError is True
    assert invalid.structuredContent is None
    invalid_text = [block.text for block in invalid.content if isinstance(block, TextContent)]
    assert invalid_text
    assert "the plane owns candidateTree" in invalid_text[0]
    assert read_task_doc(series.task_root / "task.json").routeReview is None

    published = _assert_wire_payload(
        _call(
            config_path,
            "task_doc",
            {**review_args, "review": _master_review_payload()},
        )
    )
    assert published["ok"] is True
    master = read_task_doc(series.task_root / "task.json")
    assert master.routeReview is not None
    assert master.routeReview.candidateTree == git(
        series.code_repo_path, "rev-parse", f"{series.code_work_branch}^{{tree}}"
    )
    assert master.routeReview.candidateTree == git(
        series.code_repo_path, "rev-parse", f"{candidate_commit}^{{tree}}"
    )
    assert master.routeReview.scope is not None
    assert len(master.routeReview.scope.childIntents) == 1

    admitted = _assert_wire_payload(
        _call(
            config_path,
            "worktree_integrate",
            {
                "contract_path": series.contract_path.as_posix(),
                "strategy": "ff-only",
                "dry_run": True,
            },
        )
    )
    assert admitted["ok"] is True
    assert admitted["state"] == "would-integrate"
    assert git(series.code_repo_path, "rev-parse", series.code_source_branch) != candidate_commit

    integrated = _integrate_exact_candidate(config_path, series, candidate_commit)
    assert integrated["state"] == "integrated"
    assert load_contract(series.contract_path).integration_status == "completed"


def test_public_master_integration_refuses_after_admission_evidence_changes(
    tmp_path: Path,
) -> None:
    _fixture_value, config_path, series, candidate_commit = _real_atomic_series_fixture(tmp_path)
    _write_master_review_evidence(series)
    begun = _assert_wire_payload(
        _call(
            config_path,
            "task_doc",
            {
                "repo_id": "repo",
                "operation": "begin_review",
                "contract_path": series.contract_path.as_posix(),
                "review": {},
            },
        )
    )
    assert begun["reviewState"]["pending"] is True
    published = _assert_wire_payload(
        _call(
            config_path,
            "task_doc",
            {
                "repo_id": "repo",
                "operation": "record_route_review",
                "contract_path": series.contract_path.as_posix(),
                "review": _master_review_payload(),
            },
        )
    )
    assert published["ok"] is True
    review = read_task_doc(series.task_root / "task.json").routeReview
    assert review is not None
    admitted = _assert_wire_payload(
        _call(
            config_path,
            "worktree_integrate",
            {
                "contract_path": series.contract_path.as_posix(),
                "strategy": "ff-only",
                "dry_run": True,
            },
        )
    )
    assert admitted["ok"] is True
    assert admitted["state"] == "would-integrate"

    before_ref = git(series.code_repo_path, "rev-parse", series.code_source_branch)
    original_gate = integrate_module.master_route_review_block
    mutation_seen = False

    def mutate_review_evidence_after_admission(
        contract: WorktreeContract,
        args: WorktreeArgs,
        *,
        expected_candidate_commit: str | None = None,
    ) -> Any:
        nonlocal mutation_seen
        result = original_gate(
            contract,
            args,
            expected_candidate_commit=expected_candidate_commit,
        )
        if result is None and not mutation_seen:
            _write_master_review_evidence(series)
            evidence = series.task_root / "notes" / "reports" / "master-review.md"
            evidence.write_text(
                "Review evidence changed after integration admission.\n", encoding="utf-8"
            )
            mutation_seen = True
        return result

    integrate_module.master_route_review_block = mutate_review_evidence_after_admission
    try:
        result = _run_exact_candidate(config_path, series, candidate_commit)
    finally:
        integrate_module.master_route_review_block = original_gate

    assert mutation_seen is True
    assert result.returncode == 2
    payload = result.payload
    assert payload["state"] == "route-review-evidence-stale"
    assert payload["reviewRefusal"]["status"] == "route-review-evidence-stale"
    assert payload["developerDecisionRequired"] is False
    assert payload["expected"] == {
        "routeReview": "current passing review of the accumulated master candidate"
    }
    assert payload["observed"] == {"routeReviewStatus": "route-review-evidence-stale"}
    assert payload["nextAction"] == "record_route_review"
    assert payload["nextTool"] == "task_doc"
    assert payload["nextArgs"] == {
        "repo_id": series.repo_name,
        "operation": "record_route_review",
        "contract_path": series.contract_path.as_posix(),
    }
    assert payload["nextRequiredArgs"] == ["review"]
    assert payload["statusAction"] == {
        "tool": "worktree_status",
        "args": {
            "repo_id": series.repo_name,
            "contract_path": series.contract_path.as_posix(),
        },
    }
    assert payload["nextStep"]["nextArgs"] == payload["nextArgs"]
    assert payload["nextStep"]["nextRequiredArgs"] == ["review"]
    assert git(series.code_repo_path, "rev-parse", series.code_source_branch) == before_ref
