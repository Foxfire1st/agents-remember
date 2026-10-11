"""MIK-R50 rules 3-4: legacy contracts stay readable; all other routes refuse.

A contract on disk that carries the removed ``disabled`` mode stays readable
where status, attach and guarded abandon need it, while every other route —
admission, sync, integrate, direct landing, reopen, closeout and the lifecycle
layer — refuses it with the way out. Start refusals live in
``test_memory_opt_out_refused_start.py``. Shared worlds live once in
``memory_opt_out_test_support.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
    admit_configured_contract,
    project_configured_contract_refusal,
)
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.task_docs.task_reopen import task_reopen_tool
from agents_remember.application.worktree_status import worktree_status_packet
from agents_remember.application.worktree_tool_requests import CloseoutCommitMessages
from agents_remember.kernel.memory_mode import (
    LEAF_WAY_OUT,
    SERIES_WAY_OUT,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.closeout.input import (
    EffectiveCloseoutInput,
    NotApplicableCloseoutLeg,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.task_paths import series_contract_path
from agents_remember.worktrees.direct_landing import (
    DirectLandingRequest,
    _direct_landing_preview,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlCommand,
    LifecycleControlError,
    control_operation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.knowledge_gate import (
    direct_gate_verdict,
    leaf_cutover_refusal,
    leaf_gate_refusal,
    prepared_closeout_refusal,
)
from agents_remember.worktrees.modules import (
    cleanup,
    closeout,
    closeout_external,
    finalize,
    integrate,
    integration_gate,
    pause,
    record_landing,
    sync,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.finalize_task_documents import FinalizeArgs
from agents_remember.worktrees.modules.start import attach_result, start_result
from agents_remember.worktrees.queue.closeout_recovery import MemoryCloseoutOutcome
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.reopen_series import reopen_series
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from memory_opt_out_test_support import (
    _REMOVED_REASON,
    REPO,
    STATUS,
    _assert_opt_out,
    _disabled_leaf_file,
    _live_disabled_leaf,
    _World,
    _world,
)
from test_worktree_support import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    git,
)

# --------------------------------------------------------------------------------------
# Rule 3: a contract on disk that carries the mode
# --------------------------------------------------------------------------------------


def _published_disabled_leaf(
    tmp_path: Path, *, cell: str = "disabled", memory: str | None = "plain"
) -> tuple[_World, Path, Path]:
    """A published enclosure whose contract file carries the legacy mode."""

    root = tmp_path / f"leaf-{cell}"
    root.mkdir()
    world = _world(root, memory=memory)
    path = _disabled_leaf_file(world, cell=cell)
    contract = load_contract(path)
    publish_new_lifecycle_operation_location(
        contract, contract_text=path.read_text(encoding="utf-8")
    )
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO: {}},
                "providers": {},
                "timeoutCaps": {"toolSeconds": 30, "providerSetupSeconds": 1800},
                "benchmarksEnabled": False,
            }
        ),
        encoding="utf-8",
    )
    return world, path, settings


def _config(settings: Path, contract: WorktreeContract):
    del contract
    return load_config(settings)


@pytest.mark.integration
def test_status_answers_on_a_legacy_leaf_contract(tmp_path: Path) -> None:
    """Status shows the contract as it is; it never refuses the removed mode."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    summary = worktree_status_packet(load_config(settings), path)
    assert summary.memoryMode == "disabled"
    assert summary.status != STATUS


@pytest.mark.integration
def test_status_answers_on_a_legacy_series_contract(tmp_path: Path) -> None:
    """A series contract that carries the mode is shown, not refused."""

    root = tmp_path / "series"
    root.mkdir()
    world = _world(root, memory="plain")
    coord = world.coord
    code = world.code
    series = default_series_contract(
        ContractTask(
            name="legacy-master",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/legacy-master",
            base_commit="a" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=coord / "memory-repos" / f"ar-{REPO}",
            source_branch="super",
            work_branch="ar/legacy-master",
            base_commit="b" * 40,
        ),
    )
    text = contract_publication_text(series.contract_path, series)
    series.contract_path.parent.mkdir(parents=True, exist_ok=True)
    series.contract_path.write_text(
        text.replace("memory_mode: external", "memory_mode: disabled", 1), encoding="utf-8"
    )
    assert load_contract(series.contract_path).memory_mode == "disabled"
    publish_new_lifecycle_operation_location(
        load_contract(series.contract_path),
        contract_text=series.contract_path.read_text(encoding="utf-8"),
    )
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coord.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO: {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    summary = worktree_status_packet(load_config(settings), series.contract_path)
    assert summary.memoryMode == "disabled"
    assert summary.status != STATUS


@pytest.mark.integration
def test_status_answers_on_a_contract_read_as_disabled_from_an_unknown_cell(
    tmp_path: Path,
) -> None:
    """The degraded read is shown with its quarantine record, not refused."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path, cell="bogus")
    contract = load_contract(path)
    assert contract.memory_mode == "disabled"
    assert any(entry.startswith("memory_mode=") for entry in contract.unknown_cells)
    summary = worktree_status_packet(load_config(settings), path)
    assert summary.memoryMode == "disabled"
    assert summary.status != STATUS


@pytest.mark.integration
def test_attach_answers_on_a_legacy_contract(tmp_path: Path) -> None:
    """Attach resumes the live worktrees; the removed mode is not its business."""

    world, path = _live_disabled_leaf(tmp_path / "attach")
    assert world.code.exists()
    result = attach_result(WorktreeArgs(contract_path=path))
    assert result.returncode == 0, result.payload


@pytest.mark.integration
def test_admission_refuses_a_legacy_contract_with_the_way_out(tmp_path: Path) -> None:
    """Admission refuses with status, reason and the abandon-first way out."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    refused = admit_configured_contract(load_config(settings), path.as_posix())
    assert isinstance(refused, ConfiguredContractRefused)
    assert refused.reason == "memory-opt-out"
    assert refused.status == STATUS
    assert path.as_posix() in refused.detail
    assert _REMOVED_REASON in refused.detail
    assert LEAF_WAY_OUT in refused.detail
    assert refused.expected["wayOut"] == "leaf"
    assert refused.expected["nextAction"] is not None
    assert "developer" not in refused.detail.lower() or "developer decision" not in refused.detail
    projected = project_configured_contract_refusal(refused, operation="worktree_sync")
    assert projected["state"] == STATUS
    assert projected["status"] == STATUS
    assert projected["nextTool"] == "worktree_abandon"
    assert projected["nextArgs"] == {"contract_path": path.as_posix(), "dry_run": True}
    assert projected.get("developerDecisionRequired") is not True


@pytest.mark.integration
def test_admission_names_the_raw_cell_read_from_an_unknown_cell(tmp_path: Path) -> None:
    """The degraded read names the raw token in the refusal."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path, cell="bogus")
    refused = admit_configured_contract(load_config(settings), path.as_posix())
    assert isinstance(refused, ConfiguredContractRefused)
    assert refused.status == STATUS
    assert "memory_mode='bogus'" in refused.detail
    observed = refused.observed["observed"]
    assert isinstance(observed, dict)
    assert observed["rawCell"].startswith("memory_mode=")


@pytest.mark.integration
def test_sync_tool_refuses_a_legacy_contract(tmp_path: Path) -> None:
    """The sync tool projects the refusal instead of the authority mismatch."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    payload = worktree_tools.worktree_sync_tool(
        load_config(settings), contract_path=path.as_posix(), dry_run=True
    )
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS
    assert LEAF_WAY_OUT in str(payload["detail"])


@pytest.mark.integration
def test_direct_landing_tool_refuses_a_legacy_contract(tmp_path: Path) -> None:
    """Direct landing refuses at admission with the same status and way out."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    config = load_config(settings)
    settings.write_text(
        json.dumps(
            {
                **json.loads(settings.read_text(encoding="utf-8")),
                "directExecutionEnabled": True,
            }
        ),
        encoding="utf-8",
    )
    config = load_config(settings)
    payload = direct_landing_tool(
        config,
        DirectLandingRequest(contract_path=path.as_posix(), code_commit="a" * 40),
    )
    assert payload["ok"] is False
    # The tool wraps the read-time opt-out refusal in its generic error
    # envelope: the state is 'refused' while the status stays the opt-out one.
    assert payload["state"] == "refused"
    assert payload["status"] == STATUS
    assert payload["requested"] == "disabled"
    assert payload["supported"] == ["external"]
    assert payload["nextTool"] == "worktree_abandon"


@pytest.mark.integration
def test_abandon_admits_dry_and_real_then_restarts_external(tmp_path: Path) -> None:
    """The way out works: abandon dry, abandon real, restart in the default mode."""

    root = tmp_path / "abandon"
    root.mkdir()
    world = _world(root, memory="plain")
    coord = world.coord
    code = world.code
    memory = world.memory
    assert isinstance(memory, Path)
    git(code, "branch", "super", "main")
    git(memory, "branch", "super", "main")
    # Task authority for the leaf and for the restart below (mirrors attach).
    task_root = coord / "tasks" / REPO / "legacy-leaf"
    write_task_doc(
        coord / "tasks" / REPO / "legacy-sprint",
        TaskDocument.model_validate(
            {
                "id": "legacy-sprint",
                "slug": "task",
                "title": "Sprint",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "orchestrates": ["legacy-master"],
                "integrationBranch": "super",
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "legacy-master",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "executionNature": "atomic",
                "subTasks": [
                    {"number": "L50", "name": "Leaf", "file": "l50.md", "status": "inProgress"}
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "L50",
                "slug": "l50",
                "title": "Leaf",
                "kind": "subTask",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "master": "task.md",
            }
        ),
    )
    # A genuine legacy disabled leaf is code-only: the configured memory
    # repository stays available for the later default-mode start, but the
    # leaf carries no memory leg or branch of its own.
    contract = default_contract(
        ContractTask(
            name="legacy-leaf",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
            # The atomic leaf's parent is its own task series (same address the
            # restart derives); the successor check compares these names.
            parent_task_name="legacy-leaf",
            parent_contract_path=series_contract_path(task_root),
        ),
        leaf=LeafIdentity(
            worktree_name="legacy-leaf",
            leaf_id="L50",
            # Abandon reserves citation-cache authority by lifecycle id.
            lifecycle_id="l50-legacy-abandon-lifecycle",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/legacy-leaf-wt",
            base_commit=git(code, "rev-parse", "super"),
        ),
    )
    assert contract.memory_repo_path is None
    git(
        code,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "super",
    )
    # The legacy bytes are genuine serializer output with the legacy cells: the
    # request vocabulary refuses to create the removed mode, but the model
    # reads and rewrites it, so the fixture swaps the cells on the object and
    # publishes once. No post-publication rewrite.
    legacy = replace(contract, memory_mode="disabled", memory_worktree=None, ledger_path=None)
    write_contract(legacy.contract_path, legacy)
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coord.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO: {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(settings)
    # The locator records the legacy bytes as written above (single publish).
    publish_new_lifecycle_operation_location(
        load_contract(contract.contract_path),
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    preview = worktree_tools.worktree_abandon_tool(
        config, contract_path=contract.contract_path.as_posix(), dry_run=True
    )
    assert preview["state"] == "would-abandon", preview
    assert preview["blockers"] == []
    assert contract.code_worktree.exists()
    applied = worktree_tools.worktree_abandon_tool(
        config, contract_path=contract.contract_path.as_posix(), dry_run=False
    )
    assert applied["state"] == "abandoned", applied
    assert not contract.code_worktree.exists()
    assert load_contract(contract.contract_path).cleanup == "abandoned"
    # The restart the test name promises: a start without a mode at the same
    # address never inherits the removed mode; the configured memory
    # repository supplies the fresh external leg. A fresh worktree name starts
    # a fresh group: the abandoned generation's manifest still occupies the
    # old group until collection, and the successor check compares task
    # identity, not worktree names.
    restarted = start_result(
        WorktreeArgs(
            code_repository_name=REPO,
            code_repository_root=code,
            coordination_root=coord,
            task_name="legacy-leaf",
            worktree_name="restart-leaf",
            leaf_id="L50",
            dry_run=False,
        )
    )
    assert restarted.returncode == 0, restarted.payload
    assert load_contract(Path(str(restarted.payload["enclosure_path"]))).memory_mode == "external"


@pytest.mark.integration
def test_abandon_keeps_its_own_refusals_on_a_legacy_contract(tmp_path: Path) -> None:
    """A dirty worktree still blocks abandon, force still overrides, mode aside."""

    root = tmp_path / "abandon-dirty"
    root.mkdir()
    world = _world(root, memory="plain")
    coord = world.coord
    code = world.code
    memory = world.memory
    assert isinstance(memory, Path)
    git(code, "branch", "super", "main")
    git(memory, "branch", "super", "main")
    contract = default_contract(
        ContractTask(
            name="legacy-leaf",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(
            worktree_name="legacy-leaf",
            leaf_id="L50",
            # Abandon reserves citation-cache authority by lifecycle id.
            lifecycle_id="l50-legacy-abandon-lifecycle",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/legacy-leaf-wt",
            base_commit=git(code, "rev-parse", "super"),
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="super",
            work_branch="ar/legacy-leaf-wt",
            base_commit=git(memory, "rev-parse", "super"),
        ),
    )
    assert contract.memory_worktree is not None
    git(
        code,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "super",
    )
    git(
        memory,
        "worktree",
        "add",
        "-b",
        contract.memory_work_branch,
        str(contract.memory_worktree),
        "super",
    )
    write_contract(contract.contract_path, contract)
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coord.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO: {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(settings)
    text = contract.contract_path.read_text(encoding="utf-8")
    contract.contract_path.write_text(
        text.replace("memory_mode: external", "memory_mode: disabled", 1), encoding="utf-8"
    )
    # The locator must record the final legacy bytes (see the attach test).
    publish_new_lifecycle_operation_location(
        load_contract(contract.contract_path),
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    (contract.code_worktree / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    blocked = worktree_tools.worktree_abandon_tool(
        config, contract_path=contract.contract_path.as_posix(), dry_run=False
    )
    assert blocked["state"] == "abandon-blocked", blocked
    assert contract.code_worktree.exists()


@pytest.mark.integration
def test_leaf_start_under_a_disabled_series_names_the_new_master_escape(
    tmp_path: Path,
) -> None:
    """Under a disabled series the leaf start names the series, never the edge."""

    root = tmp_path / "series-start"
    root.mkdir()
    world = _world(root, memory="plain")
    coord = world.coord
    code = world.code
    memory = world.memory
    assert isinstance(memory, Path)
    git(code, "branch", "super", "main")
    git(memory, "branch", "super", "main")
    task_root = coord / "tasks" / REPO / "legacy-master"
    write_task_doc(
        coord / "tasks" / REPO / "legacy-sprint",
        TaskDocument.model_validate(
            {
                "id": "sprint",
                "slug": "task",
                "title": "Sprint",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "orchestrates": ["legacy-master"],
                "integrationBranch": "super",
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "master",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "executionNature": "atomic",
                "subTasks": [
                    {
                        "number": "L1",
                        "name": "Leaf",
                        "file": "l1.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "L1",
                "slug": "l1",
                "title": "Leaf",
                "kind": "subTask",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "master": "task.md",
            }
        ),
    )
    series = default_series_contract(
        ContractTask(
            name="legacy-master",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/legacy-master",
            base_commit=git(code, "rev-parse", "super"),
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="super",
            work_branch="ar/legacy-master",
            base_commit=git(memory, "rev-parse", "super"),
        ),
    )
    assert series.contract_path.parent == task_root
    write_contract(series.contract_path, series)
    text = series.contract_path.read_text(encoding="utf-8")
    series.contract_path.write_text(
        text.replace("memory_mode: external", "memory_mode: disabled", 1), encoding="utf-8"
    )
    base_arguments: dict[str, object] = {
        "code_repository_name": REPO,
        "code_repository_root": code,
        "coordination_root": coord,
        "task_name": "legacy-master",
        "worktree_name": "l1-leaf",
        "leaf_id": "L1",
        "dry_run": True,
    }
    refused = start_result(WorktreeArgs(**base_arguments))  # type: ignore[arg-type]
    assert refused.returncode == 2
    _assert_opt_out(refused.payload, operation="atomic leaf start")
    assert series.contract_path.as_posix() in str(refused.payload["summary"])
    assert SERIES_WAY_OUT in str(refused.payload["summary"])
    assert "correct the named edge" not in str(refused.payload["summary"])


# --------------------------------------------------------------------------------------
# Rule 4: the lifecycle layer refuses without the authority check
# --------------------------------------------------------------------------------------


def _bare_disabled_contract(coord: Path, code: Path, *, kind: str = "leaf") -> WorktreeContract:
    """A disabled contract object with no live worktrees, branches or memory repo."""

    return WorktreeContract(
        task_id="L50",
        task_name="some-task",
        repo_name=REPO,
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=coord,
        task_root=coord / "tasks" / REPO / "some-task",
        contract_path=coord / "tasks" / REPO / "some-task" / "series-contract.md",
        task_artifact=coord / "tasks" / REPO / "some-task" / "l1.md",
        worktree_group=coord / "tasks" / REPO / "some-task" / "worktrees",
        code_repo_path=code,
        code_source_branch="super",
        code_work_branch="ar/legacy-leaf",
        code_base_commit="a" * 40,
        code_worktree=coord / "tasks" / REPO / "some-task" / "worktrees" / "legacy-leaf",
        kind=kind,
        leaf_id="L50",
    )


def _route_args(path: Path, **overrides: object) -> WorktreeArgs:
    arguments: dict[str, object] = {"contract_path": path, "dry_run": True}
    arguments.update(overrides)
    return WorktreeArgs(**arguments)  # type: ignore[arg-type]


@pytest.mark.integration
@pytest.mark.parametrize("memory", ["plain", None])
def test_closeout_preview_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    """The preview refuses a published disabled contract, memory repo or not."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path, memory=memory)
    payload = worktree_tools.worktree_closeout_preview_tool(
        load_config(settings), path.as_posix(), CloseoutCommitMessages(code="a" * 40)
    )
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS


@pytest.mark.integration
@pytest.mark.parametrize("memory", ["plain", None])
def test_closeout_apply_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    """The apply refuses before any worktree or branch read, memory repo or not."""

    _fixture_world, path, settings = _published_disabled_leaf(tmp_path, memory=memory)
    payload = worktree_tools.worktree_closeout_apply_tool(
        load_config(settings),
        path.as_posix(),
        CloseoutCommitMessages(code="a" * 40),
        worktree_tools.CloseoutApproval(intent_note="opt-out probe"),
    )
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS


@pytest.mark.parametrize("memory", ["plain", None])
def test_sync_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    result = sync.sync_result(_route_args(path))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_sync")


@pytest.mark.parametrize("memory", ["plain", None])
def test_integrate_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    contract = load_contract(path)
    result = integrate.integrate_result(_route_args(path, approved=True, dry_run=False), contract)
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_integrate")


@pytest.mark.parametrize("memory", ["plain", None])
def test_checkpoint_landing_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    contract = load_contract(path)
    result = integrate.checkpoint_landing_result(
        _route_args(path, approved=True, dry_run=False), contract
    )
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_checkpoint_landing")


@pytest.mark.parametrize("memory", ["plain", None])
def test_record_landing_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    result = record_landing.record_landing_result(_route_args(path))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_record_landing")


@pytest.mark.parametrize("memory", ["plain", None])
def test_pause_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    contract = load_contract(path)
    result = pause.pause_result(_route_args(path), contract)
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_pause")


@pytest.mark.parametrize("memory", ["plain", None])
def test_cleanup_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    result = cleanup.cleanup_result(_route_args(path))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_cleanup")


@pytest.mark.parametrize("memory", ["plain", None])
def test_finalize_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    world = _world(tmp_path, memory=memory)
    path = _disabled_leaf_file(world)
    result = finalize.finalize_result(FinalizeArgs(contract_path=path))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="lifecycle_finalize_task")


def test_reopen_refuses_a_legacy_contract(tmp_path: Path) -> None:
    world = _world(tmp_path, memory="plain")
    path = _disabled_leaf_file(world)
    result = reopen_task(path, dry_run=True)
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="task_reopen")


def test_reopen_series_refuses_a_legacy_contract(tmp_path: Path) -> None:
    world = _world(tmp_path, memory="plain")
    contract = _bare_disabled_contract(
        world.coord,
        world.code,
        kind="series",
    )
    result = reopen_series(contract, dry_run=True)
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="task_reopen")


@pytest.mark.integration
@pytest.mark.parametrize("memory", ["plain", None])
def test_operation_control_refuses_without_authority(tmp_path: Path, memory: str | None) -> None:
    """The control entry raises the opt-out status before reloading anything."""

    _fixture_world, path, _settings = _published_disabled_leaf(tmp_path, memory=memory)
    contract = load_contract(path)
    location = require_matching_lifecycle_operation_location(contract)
    with pytest.raises(LifecycleControlError) as raised:
        control_operation(
            LifecycleControlCommand(
                admitted_contract=contract,
                admitted_location=location,
                configured_authority="test",
                kind="closeout",
                action="cancel",
                expected_generation=1,
                intent_note="opt-out probe",
            )
        )
    assert raised.value.status == STATUS
    assert "disabled" in raised.value.detail


@pytest.mark.parametrize("memory", ["plain", None])
def test_direct_landing_preview_equals_its_apply(tmp_path: Path, memory: str | None) -> None:
    """Preview and apply raise the same status with the same detail."""

    from agents_remember.worktrees.direct_landing import (  # noqa: PLC0415 - one local import keeps the collector light
        DirectLandingError,
    )

    world = _world(tmp_path, memory=memory)
    contract = _bare_disabled_contract(world.coord, world.code)
    request = DirectLandingRequest(contract_path="/nonexistent/contract.md", code_commit="c" * 40)
    effective_input = EffectiveCloseoutInput(
        route="direct-landing",
        contractKind="leaf",
        memoryMode="disabled",
        code=NotApplicableCloseoutLeg(reason="opt-out probe"),
        memory=NotApplicableCloseoutLeg(reason="opt-out probe"),
    )
    with pytest.raises(DirectLandingError) as preview_raised:
        _direct_landing_preview(contract, request, effective_input, "c" * 40)
    with pytest.raises(DirectLandingError) as apply_raised:
        direct_landing_apply(contract)
    assert preview_raised.value.status == STATUS
    assert apply_raised.value.status == STATUS
    assert preview_raised.value.detail == apply_raised.value.detail


def direct_landing_apply(contract: WorktreeContract) -> None:
    """The apply-side gate over the same contract, reached without a server."""

    from agents_remember.worktrees.direct_landing import (  # noqa: PLC0415 - one local import keeps the collector light
        direct_landing,
    )

    direct_landing(
        _direct_config(contract),
        DirectLandingRequest(contract_path="/nonexistent/contract.md", code_commit="c" * 40),
        contract,
    )


def _direct_config(contract: WorktreeContract):
    from agents_remember.kernel.primitives.runtime_config import (  # noqa: PLC0415 - one local import keeps the collector light
        McpRuntimeConfig,
    )

    return McpRuntimeConfig(
        config_path=contract.coordination_root / "settings.json",
        coordination_root=contract.coordination_root,
        direct_execution_enabled=True,
        workspace_root=contract.coordination_root.parent,
        transcript_root=contract.coordination_root / "logs" / "mcp",
        repositories={},
    )


@pytest.mark.parametrize("memory", ["plain", None])
def test_knowledge_gates_refuse_instead_of_nothing_to_judge(
    tmp_path: Path, memory: str | None
) -> None:
    """All five gate sites answer the refusal for a disabled contract."""

    world = _world(tmp_path, memory=memory)
    contract = _bare_disabled_contract(world.coord, world.code)
    cutover = leaf_cutover_refusal(contract, "the closeout validator")
    assert (cutover is not None and STATUS in cutover) or "disabled" in (cutover or "")
    gate = leaf_gate_refusal(contract, code_tree="a" * 40, memory_tree="b" * 40)
    assert gate is not None and "disabled" in gate
    prepared = prepared_closeout_refusal(contract)
    assert prepared is not None and "disabled" in prepared
    verdict = direct_gate_verdict(contract, code_commit="a" * 40, memory_tree="b" * 40)
    assert verdict.applies is True
    assert verdict.refusal is not None and "disabled" in verdict.refusal


@pytest.mark.parametrize("memory", ["plain", None])
def test_behind_entry_gate_sites_refuse_a_disabled_contract(
    tmp_path: Path, memory: str | None
) -> None:
    """The behind-entry gate realizations refuse by name (L50-CODE-R1-F02)."""

    world = _world(tmp_path, memory=memory)
    contract = _bare_disabled_contract(world.coord, world.code)
    verdict = closeout_external.candidate_gate_verdict(contract, code_tree="a" * 40)
    assert verdict is not None
    assert verdict != closeout_external.GATE_NOT_APPLICABLE
    assert "disabled" in verdict
    blocked = integration_gate.integration_gate_block(
        contract,
        _route_args(world.coord / "tasks" / REPO / "some-task" / "series-contract.md"),
        IntegratedCommits(code="a" * 40, memory_content="b" * 40),
        IntegrationSources(
            current_code_source="a" * 40,
            current_memory_source="b" * 40,
            code_replay_required=False,
            memory_replay_required=False,
        ),
        operation="worktree_integrate",
    )
    assert blocked is not None
    _assert_opt_out(blocked.payload, operation="worktree_integrate")
    moved = integrate._integration_sources_moved_block(
        contract,
        IntegrationSources(
            current_code_source="a" * 40,
            current_memory_source="b" * 40,
            code_replay_required=False,
            memory_replay_required=False,
        ),
        operation="worktree_integrate",
    )
    assert moved is not None
    _assert_opt_out(moved.payload, operation="worktree_integrate")
    with pytest.raises(RuntimeError, match="disabled"):
        integrate._replay_requirements(contract, "a" * 40, "b" * 40, operation="worktree_integrate")
    series = _bare_disabled_contract(world.coord, world.code, kind="series")
    with pytest.raises(RuntimeError, match="disabled"):
        integrate.checkpoint_landing_eligibility(series)
    with pytest.raises(RuntimeError, match="disabled"):
        closeout._recover_closeout_finalization(
            contract,
            _route_args(world.coord / "tasks" / REPO / "some-task" / "series-contract.md"),
        )
    refusal = record_landing._knowledge_gate_refusal(contract, "a" * 40, "b" * 40)
    assert refusal is not None and "disabled" in refusal
    with pytest.raises(RuntimeError, match="disabled"):
        closeout._memory_refresh_preview(contract, {"all": [], "working": []})
    with pytest.raises(RuntimeError, match="disabled"):
        closeout._memory_ledger_repair(
            contract, MemoryCloseoutOutcome(), operation="worktree_closeout_apply"
        )
    with pytest.raises(RuntimeError, match="disabled"):
        closeout._memory_ledger_repair(
            contract,
            MemoryCloseoutOutcome(ledger_repair={"repaired": ["memory.md"]}),
            operation="worktree_closeout_apply",
        )
    with pytest.raises(RuntimeError, match="disabled"):
        integrate._integrated_memory_commit(contract, "b" * 40, operation="worktree_integrate")


@pytest.mark.integration
def test_reopen_tool_refuses_a_legacy_contract(tmp_path: Path) -> None:
    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    payload = task_reopen_tool(load_config(settings), contract_path=path.as_posix(), dry_run=True)
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS


@pytest.mark.integration
def test_cleanup_tool_refuses_a_legacy_contract(tmp_path: Path) -> None:
    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    payload = worktree_tools.worktree_cleanup_tool(
        load_config(settings), contract_path=path.as_posix(), dry_run=True
    )
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS


@pytest.mark.integration
def test_finalize_tool_refuses_a_legacy_contract(tmp_path: Path) -> None:
    _fixture_world, path, settings = _published_disabled_leaf(tmp_path)
    payload = worktree_tools.lifecycle_finalize_task_tool(
        load_config(settings), contract_path=path.as_posix(), dry_run=True
    )
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS
