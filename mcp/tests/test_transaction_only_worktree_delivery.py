"""Focused CCR-R12 transaction-only closeout and integration regressions."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.lifecycle.lifecycle_operation_worker import (
    OperationRuntime,
    execute_operation,
)
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    load_ledger,
    write_ledger,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskEnclosureRef, read_task_doc, write_task_doc
from agents_remember.worktrees.integration.closeout import curator_coherence as coherence
from agents_remember.worktrees.integration.closeout.certification import execution as selected
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.quality import closeout_memory as memory_quality
from agents_remember.worktrees.modules.quality import gate as quality_gate
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from closeout_input_test_support import (
    closeout_operation_input,
    ensure_fixture_waiting_door,
    publish_closeout_finalization,
    start_closeout_operation,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _fixture, _git

MESSAGES = CloseoutCommitMessages(
    code="Add transaction feature",
    memory="Document transaction feature",
    ledger="Record transaction pair",
)


def _public_config(root: Path, contract) -> object:
    """Bind an existing temp Git fixture to MCP authority without a profile."""

    code_link = root / contract.repo_name
    if not code_link.exists():
        code_link.symlink_to(contract.code_repo_path, target_is_directory=True)
    if contract.memory_repo_path is not None:
        memory_link = (
            contract.coordination_root / "memory-repos" / f"ar-{contract.repo_name}"
        )
        memory_link.parent.mkdir(parents=True, exist_ok=True)
        if not memory_link.exists():
            memory_link.symlink_to(contract.memory_repo_path, target_is_directory=True)
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "retirement": {"autoLandOnIntegration": False},
                "repositories": {contract.repo_name: {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _run_queued_operation(contract, operation: str):
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, operation))
    running = OperationRuntime(store).start()
    execute_operation(running, OperationRuntime(store))
    return load_contract(contract.contract_path), store.read()


def _bind_task_without_review(contract) -> None:
    """Add only the canonical enclosure binding; leave review evidence absent."""

    task_path = contract.task_root / f"{contract.leaf_id.lower()}.json"
    document = read_task_doc(task_path)
    write_task_doc(
        task_path.parent,
        document.model_copy(
            update={
                "enclosures": [
                    TaskEnclosureRef(
                        leafId=contract.leaf_id,
                        enclosurePath=contract.contract_path.as_posix(),
                    )
                ],
            }
        ),
    )


def _assert_no_profile_or_review(config, contract) -> None:
    """Make the transaction test's absent acceptance authorities explicit."""

    assert config.repositories[contract.repo_name].certification_profile is None
    document = read_task_doc(contract.task_root / f"{contract.leaf_id.lower()}.json")
    assert document.routeReview is None


def _publish_synthetic_closeout_source(contract, config_path: Path):
    """Create only the durable source journal that integration's CAS owner consumes."""

    # Reuse the real fixture's sprint -> master -> leaf topology.  The lifecycle helper's
    # fallback sprint is intentionally disposable and cannot support integration completion.
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    current = load_contract(contract.contract_path)
    assert current.closeout_door is not None
    current = replace(
        current,
        closeout_door=current.closeout_door.model_copy(
            update={
                "sprintTaskDocumentRef": TaskDocumentRef(
                    repository=current.repo_name,
                    path="sprint/task.json",
                )
            }
        ),
    )
    write_contract(current.contract_path, current)
    contract = current
    operation_input = closeout_operation_input(
        contract,
        config_path=config_path,
        approval_note="fixture records the already prepared transaction",
    )
    start_closeout_operation(
        operation_input,
        launcher=lambda *_: None,
    )
    current = load_contract(contract.contract_path)
    store = LifecycleOperationStore(operation_record_path(current.worktree_group, "closeout"))
    runtime = OperationRuntime(store)
    runtime.start()
    publish_closeout_finalization(runtime, current)
    runtime.finish({"state": "closed"}, ok=True)
    return load_contract(contract.contract_path)


def _forbid_acceptance_tools():
    """Patch historical acceptance entry points so an accidental call fails loudly."""

    return mock.patch.multiple(
        quality_gate,
        run_strict_code_quality_gate=mock.Mock(
            side_effect=AssertionError("transaction called strict code quality")
        ),
        create=True,
    ), mock.patch.object(
        memory_quality,
        "run_memory_quality_phase",
        side_effect=AssertionError("transaction called memory quality"),
    ), mock.patch.object(
        selected,
        "execute_selected_closeout",
        side_effect=AssertionError("transaction called selected certification"),
    ), mock.patch.object(
        coherence,
        "require_current_curator_coherence",
        side_effect=AssertionError("transaction called curator certification"),
    )


def test_public_closeout_commits_code_memory_and_ledger_without_acceptance_tools(
    tmp_path, worktree_services
):
    """Closeout is a Git transaction even when quality/review authorities are absent."""

    fixture = _fixture(tmp_path, external_memory=True, selected_profile=False)
    contract = fixture.leaf_contract
    assert contract.memory_repo_path is not None and contract.memory_worktree is not None
    contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
    contract.memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(
        fixture.code_repo,
        "worktree",
        "add",
        contract.code_worktree.as_posix(),
        contract.code_work_branch,
    )
    _git(
        contract.memory_repo_path,
        "worktree",
        "add",
        contract.memory_worktree.as_posix(),
        contract.memory_work_branch,
    )
    memory_seed = _git(contract.memory_worktree, "rev-parse", "HEAD")
    write_ledger(
        contract.memory_worktree / "memory.md",
        create_initial_ledger("repo", contract.code_base_commit, memory_seed),
    )
    _git(contract.memory_worktree, "add", "memory.md")
    _git(contract.memory_worktree, "commit", "-m", "Seed transaction ledger")
    (contract.code_worktree / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (contract.memory_worktree / "onboarding").mkdir()
    (contract.memory_worktree / "feature.md").write_text("# Feature\n", encoding="utf-8")
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    contract = load_contract(contract.contract_path)
    _bind_task_without_review(contract)
    config = _public_config(tmp_path, contract)
    _assert_no_profile_or_review(config, contract)

    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        # The existing helper publishes a deliberately non-applicable waiting door.  Its
        # disposable sprint has no queue projection, so bypass only that fixture fence while
        # retaining the public apply admission and worker transaction.
        stack.enter_context(
            mock.patch.object(lifecycle_operations, "require_first_ready_generation")
        )
        stack.enter_context(mock.patch.object(lifecycle_operations, "launch_detached_worker"))
        preview = worktree_tools.worktree_closeout_preview_tool(
            config, contract.contract_path.as_posix(), MESSAGES
        )
        assert preview["ok"] is True, preview
        assert preview["state"] == "would-closeout"
        queued = worktree_tools.worktree_closeout_apply_tool(
            config,
            contract.contract_path.as_posix(),
            MESSAGES,
            CloseoutApproval(intent_note="developer approved transaction"),
        )
        assert queued["ok"] is True and queued["state"] == "queued", queued
        closed, operation = _run_queued_operation(contract, "closeout")

    assert operation is not None and operation.status == "completed", operation
    assert closed.closeout_status == "completed"
    assert closed.code_commit and closed.memory_content_commit and closed.ledger_commit
    assert _git(contract.code_worktree, "rev-parse", "HEAD") == closed.code_commit
    assert _git(contract.memory_worktree, "rev-parse", "HEAD") == closed.ledger_commit
    mapping = load_ledger(contract.ledger_path).rows[0]
    assert mapping.code_commit == closed.code_commit
    assert mapping.memory_commit == closed.memory_content_commit


def test_public_integration_merges_prepared_pair_without_acceptance_tools(
    tmp_path, worktree_services
):
    """Integration merges the prepared code and memory refs without rerunning acceptance."""

    fixture = _authority_fixture(tmp_path, external_memory=True)
    closed = _closed_external_leaf_worktrees(
        fixture, tmp_path, publish_closeout_evidence=False
    )
    config = _public_config(tmp_path, closed)
    closed = _publish_synthetic_closeout_source(closed, config.config_path)
    _assert_no_profile_or_review(config, closed)

    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        stack.enter_context(mock.patch.object(lifecycle_operations, "launch_detached_worker"))
        preview = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=True,
        )
        assert preview["ok"] is True, preview
        queued = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=False,
        )
        assert queued["ok"] is True and queued["state"] == "queued", queued
        integrated, operation = _run_queued_operation(closed, "integrate")

    assert operation is not None and operation.status == "completed", operation
    assert integrated.integration_status == "completed"
    assert integrated.integrated_code_commit == integrated.code_commit
    assert integrated.integrated_memory_content_commit == integrated.memory_content_commit
    assert integrated.integrated_ledger_commit == integrated.ledger_commit
    assert _git(fixture.code_repo, "rev-parse", "ar/master") == integrated.code_commit
    assert _git(closed.memory_repo_path, "rev-parse", "ar/master") == integrated.ledger_commit


def test_public_integration_ref_movement_refuses_before_pair_merge(tmp_path, worktree_services):
    """A source-tip race remains a concrete refusal and cannot publish a torn pair."""

    fixture = _authority_fixture(tmp_path, external_memory=True)
    closed = _closed_external_leaf_worktrees(
        fixture, tmp_path, publish_closeout_evidence=False
    )
    config = _public_config(tmp_path, closed)
    closed = _publish_synthetic_closeout_source(closed, config.config_path)
    _assert_no_profile_or_review(config, closed)
    source_before = _git(fixture.code_repo, "rev-parse", "ar/master")
    memory_before = _git(closed.memory_repo_path, "rev-parse", "ar/master")

    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        stack.enter_context(mock.patch.object(lifecycle_operations, "launch_detached_worker"))
        queued = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=False,
        )
        assert queued["ok"] is True and queued["state"] == "queued", queued

        _git(fixture.code_repo, "branch", "race", source_before)
        _git(fixture.code_repo, "switch", "race")
        (fixture.code_repo / "parallel.txt").write_text("parallel\n", encoding="utf-8")
        _git(fixture.code_repo, "add", "parallel.txt")
        _git(fixture.code_repo, "commit", "-m", "Parallel source change")
        raced = _git(fixture.code_repo, "rev-parse", "HEAD")
        _git(fixture.code_repo, "update-ref", "refs/heads/ar/master", raced, source_before)

        store = LifecycleOperationStore(operation_record_path(closed.worktree_group, "integrate"))
        running = OperationRuntime(store).start()
        runtime = OperationRuntime(store)
        with pytest.raises(RuntimeError, match="code integration source moved") as raised:
            execute_operation(running, runtime)
        runtime.fail(raised.value)
        operation = store.read()

    assert operation is not None and operation.status == "failed", operation
    assert operation.result is not None, operation
    assert "code integration source moved" in repr(operation.result)
    assert _git(fixture.code_repo, "rev-parse", "ar/master") == raced
    assert _git(closed.memory_repo_path, "rev-parse", "ar/master") == memory_before
    current = load_contract(closed.contract_path)
    assert current.integration_status != "completed"
