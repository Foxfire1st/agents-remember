"""MIK-R50 rules 1-2: the start refuses the removed mode before anything exists.

``disabled`` is a removed memory mode. ``worktree_start`` refuses it by name
before anything exists — dry run and real run identically, for the mode, the
choice answer and both CLI options — and nothing in the product produces it any
more. Route refusals on legacy contracts live in
``test_memory_opt_out_refused_routes.py``; the vocabulary itself lives in
``test_memory_mode_removal.py``. Shared worlds live once in
``memory_opt_out_test_support.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.errors import MemoryOptOutRefusedError
from agents_remember.kernel.coordination_context.models import MissingMemoryError
from agents_remember.kernel.memory_mode import (
    SETUP_FIRST_ACTION,
    START_AGAIN_ACTION,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.task_paths import leaf_enclosure_path, series_contract_path
from agents_remember.worktrees.disabled_memory import (
    OptOutSubject,
    opt_out_result_fields,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cli import build_parser
from agents_remember.worktrees.modules.start import attach_result, start_result
from agents_remember.worktrees.modules.startup.start_memory import prepare_memory_for_start
from agents_remember.worktrees.reopen import _start_preview_args
from agents_remember.worktrees.reopen_reset import _reopened_contract
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)
from memory_opt_out_test_support import (
    _REMOVED_REASON,
    REPO,
    STATUS,
    _assert_opt_out,
    _live_disabled_leaf,
    _World,
    _world,
)
from test_worktree_support import (
    LeafIdentity,
    RepoBranchPlan,
    git,
)

_KNOWN_KNOWLEDGE = "invariant work is mandatory, and a task cannot opt out of memory (D5)"


def _start_args(world: _World, **overrides: object) -> WorktreeArgs:
    arguments: dict[str, object] = {
        "code_repository_name": REPO,
        "code_repository_root": world.code,
        "coordination_root": world.coord,
        "task_name": "some-task",
        "worktree_name": "some-leaf",
    }
    arguments.update(overrides)
    return WorktreeArgs(**arguments)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Rule 1: the start refuses the mode by name, before anything exists
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("dry_run", [True, False])
def test_start_refuses_disabled_where_knowledge_exists(tmp_path: Path, dry_run: bool) -> None:
    """Dry run and real run refuse identically, naming D5 and where it was found."""

    world = _world(tmp_path, memory="converted")
    before_branches = git(world.code, "branch", "--list")
    result = start_result(_start_args(world, memory_mode="disabled", dry_run=dry_run))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_start")
    assert _KNOWN_KNOWLEDGE in str(result.payload["summary"])
    assert "branch main" in str(result.payload["summary"])
    assert START_AGAIN_ACTION in str(result.payload["summary"])
    # Nothing was created: no contract, no branch, no worktree, no task binding.
    assert git(world.code, "branch", "--list") == before_branches
    assert not (world.coord / "tasks").exists()
    assert list(world.coord.glob("*.md")) == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_start_refuses_disabled_where_no_knowledge_exists(tmp_path: Path, dry_run: bool) -> None:
    """Without converted knowledge the refusal gives the removal reason instead."""

    world = _world(tmp_path, memory="plain")
    result = start_result(_start_args(world, memory_mode="disabled", dry_run=dry_run))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_start")
    assert _REMOVED_REASON in str(result.payload["summary"])
    assert "D5" not in str(result.payload["summary"])


def test_start_refuses_disabled_where_the_probe_cannot_answer(
    tmp_path: Path,
) -> None:
    """A memory root Git cannot use gets the truthful probe-failure refusal.

    A plain directory is not proof of successfully probed unconverted memory
    (the probe reads parent repositories too): the refusal names the probe
    failure, the mode, the repository and the action. The genuinely unreached
    setup-first shape has its own case below.
    """

    world = _world(tmp_path, memory="broken")
    result = start_result(_start_args(world, memory_mode="disabled", dry_run=True))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_start")
    assert "cutover probe cannot say" in str(result.payload["summary"])


def test_start_refuses_the_memory_choice_answer(tmp_path: Path) -> None:
    """The `disabled-memory` recovery answer is refused by name, never honored."""

    world = _world(tmp_path, memory="converted")
    for dry_run in (True, False):
        result = start_result(_start_args(world, memory_choice="disabled-memory", dry_run=dry_run))
        assert result.returncode == 2
        _assert_opt_out(result.payload, operation="worktree_start")
        assert _KNOWN_KNOWLEDGE in str(result.payload["summary"])


def test_start_refuses_both_module_command_line_options(tmp_path: Path) -> None:
    """The CLI parses both options and the start refuses each with the same status."""

    world = _world(tmp_path, memory="plain")
    for argv in (
        ["start", "--worktree-name", "some-leaf", "--memory-mode", "disabled"],
        ["start", "--worktree-name", "some-leaf", "--memory-choice", "disabled-memory"],
    ):
        parsed = build_parser().parse_args(argv)
        result = start_result(
            _start_args(
                world,
                worktree_name=parsed.worktree_name,
                memory_mode=parsed.memory_mode,
                memory_choice=parsed.memory_choice,
                dry_run=True,
            )
        )
        assert result.returncode == 2
        _assert_opt_out(result.payload, operation="worktree_start")


def test_start_refuses_an_unknown_memory_choice_as_an_invalid_request(
    tmp_path: Path,
) -> None:
    """An unknown choice answer is not a removal and never borrows the opt-out status."""

    world = _world(tmp_path, memory="plain")
    parsed = build_parser().parse_args(
        ["start", "--worktree-name", "some-leaf", "--memory-choice", "bogus"]
    )
    assert parsed.memory_choice == "bogus"
    result = start_result(_start_args(world, memory_choice="bogus", dry_run=True))
    assert result.returncode == 2
    assert result.payload["state"] == "invalid-request"
    assert result.payload["state"] != STATUS


def test_start_names_the_probe_failure(tmp_path: Path) -> None:
    """When Git cannot say whether knowledge exists, the refusal names the failure."""

    world = _world(tmp_path, memory="broken")
    result = start_result(_start_args(world, memory_mode="disabled", dry_run=True))
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_start")
    assert "cutover probe cannot say" in str(result.payload["summary"])


def test_start_with_no_memory_root_keeps_the_missing_memory_error(tmp_path: Path) -> None:
    """Where the memory root does not exist at all, the existing error stands."""

    world = _world(tmp_path, memory=None)
    with pytest.raises(MissingMemoryError, match="memory is missing"):
        start_result(_start_args(world, memory_mode="disabled", dry_run=True))


def test_absent_memory_root_names_the_setup_first_action(tmp_path: Path) -> None:
    """Where the memory root is absent, the refusal names the setup action.

    The distinct genuinely setup-first shape: no probe runs against a missing
    root, so the reason is the removal plus "not set up" and the action starts
    with setting the repository up first.
    """

    world = _world(tmp_path, memory=None)
    contract_path = world.coord / "tasks" / REPO / "some-task" / "series-contract.md"
    fields = opt_out_result_fields(
        OptOutSubject(
            contract_path=contract_path,
            coordination_root=world.coord,
            repo_name=REPO,
        ),
        "worktree_start",
        "start",
    )
    assert fields["status"] == STATUS
    assert fields["requested"] == "disabled"
    assert fields["supported"] == ["external"]
    assert "not set up" in str(fields["detail"])
    assert SETUP_FIRST_ACTION in str(fields["detail"])


def test_start_with_an_unknown_token_stays_an_invalid_request(tmp_path: Path) -> None:
    """An unknown token is not a removal and never borrows the opt-out status."""

    world = _world(tmp_path, memory="plain")
    result = start_result(_start_args(world, memory_mode="bogus", dry_run=True))
    assert result.returncode == 2
    assert result.payload["state"] != STATUS


def test_first_leaf_of_a_master_is_refused_and_creates_no_series_contract(
    tmp_path: Path,
) -> None:
    """The master's first leaf is refused before the series contract can exist."""

    world = _world(tmp_path, memory="converted")
    coord = world.coord
    assert isinstance(coord, Path)
    write_task_doc(
        coord / "tasks" / REPO / "some-master",
        TaskDocument.model_validate(
            {
                "id": "some-master",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "executionNature": "atomic",
                "subTasks": [
                    {"number": "L1", "name": "Leaf", "file": "l1.md", "status": "planning"}
                ],
            }
        ),
    )
    result = start_result(
        _start_args(
            world,
            task_name="some-master",
            worktree_name="l1-leaf",
            leaf_id="L1",
            memory_mode="disabled",
            dry_run=False,
        )
    )
    assert result.returncode == 2
    _assert_opt_out(result.payload, operation="worktree_start")
    assert not (coord / "tasks" / REPO / "some-master" / "series-contract.md").exists()


# --------------------------------------------------------------------------------------
# Rule 2: no producer is left
# --------------------------------------------------------------------------------------


@pytest.mark.integration
def test_no_mode_start_after_an_abandoned_disabled_contract_is_external(
    tmp_path: Path,
) -> None:
    """The next start at the address never inherits the removed mode."""

    world = _started_abandoned_world(tmp_path, cleanup="abandoned")
    result = start_result(
        _start_args(
            world,
            task_name="restart-master",
            worktree_name="restart-leaf",
            leaf_id="L1",
            dry_run=False,
        )
    )
    assert result.returncode == 0, result.payload
    restarted = load_contract(Path(str(result.payload["enclosure_path"])))
    assert restarted.memory_mode == "external"


@pytest.mark.integration
def test_no_mode_start_after_a_reopened_disabled_contract_is_external(
    tmp_path: Path,
) -> None:
    """A reopened tombstone restarts in the default mode, not the removed one."""

    world = _started_abandoned_world(tmp_path, cleanup="reopened")
    result = start_result(
        _start_args(
            world,
            task_name="restart-master",
            worktree_name="restart-leaf",
            leaf_id="L1",
            dry_run=False,
        )
    )
    assert result.returncode == 0, result.payload
    restarted = load_contract(Path(str(result.payload["enclosure_path"])))
    assert restarted.memory_mode == "external"


@pytest.mark.integration
@pytest.mark.parametrize("dry_run", [True, False])
def test_no_mode_start_attaches_to_a_live_disabled_leaf(tmp_path: Path, dry_run: bool) -> None:
    """A no-mode start attaches to a live legacy leaf; admission never preempts (F01)."""

    world, path = _live_disabled_leaf(tmp_path / "live")
    contract = load_contract(path)
    attached = attach_result(WorktreeArgs(contract_path=path))
    assert attached.returncode == 0, attached.payload
    before_text = path.read_text(encoding="utf-8")
    before_refs = git(world.code, "show-ref")
    result = start_result(
        _start_args(
            world,
            task_name=contract.task_name,
            worktree_name=contract.code_worktree.name,
            leaf_id=contract.leaf_id,
            dry_run=dry_run,
        )
    )
    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "attached-existing-contract", result.payload
    assert load_contract(path).memory_mode == "disabled"
    assert path.read_text(encoding="utf-8") == before_text
    assert git(world.code, "show-ref") == before_refs


@pytest.mark.integration
@pytest.mark.parametrize("dry_run", [True, False])
def test_cleanup_of_a_terminal_archived_disabled_contract_is_refused(
    tmp_path: Path, dry_run: bool
) -> None:
    """A genuine terminal archive cleans up with the opt-out refusal (F03).

    The surviving contract still carries the removed mode, so cleanup answers
    with the known remedy -- never a developer decision -- dry run and real
    run identically.
    """

    world = _started_abandoned_world(tmp_path, cleanup="abandoned")
    path = leaf_enclosure_path(world.coord / "tasks" / REPO / "restart-master", "L1")
    assert load_contract(path).memory_mode == "disabled"
    payload = worktree_tools.worktree_cleanup_tool(
        load_config(tmp_path / "settings.json"),
        contract_path=path.as_posix(),
        dry_run=dry_run,
    )
    assert payload["ok"] is False
    assert payload["state"] == STATUS
    assert payload["status"] == STATUS
    assert payload.get("developerDecisionRequired") is not True
    assert "developer decision" not in str(payload.get("detail", "")).lower()


def _started_abandoned_world(tmp_path: Path, *, cleanup: str) -> _World:
    """A genuine legacy tombstone: code-only disabled bytes, one locator, real abandon.

    The leaf carries no memory leg or branch (genuine legacy population); the
    configured memory repository stays available for the later default-mode
    start. The tombstone is published once, then the guarded abandon tool
    produces the terminal archive genuinely. The restart below uses a fresh
    worktree name: the abandoned generation's manifest still occupies the old
    group until collection, and the successor check compares task identity.
    """

    world = _world(tmp_path, memory="plain")
    coord = world.coord
    code = world.code
    git(code, "branch", "super", "main")
    memory = world.memory
    assert isinstance(memory, Path)
    git(memory, "branch", "super", "main")
    task_root = coord / "tasks" / REPO / "restart-master"
    write_task_doc(
        coord / "tasks" / REPO / "restart-sprint",
        TaskDocument.model_validate(
            {
                "id": "sprint",
                "slug": "task",
                "title": "Sprint",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "orchestrates": ["restart-master"],
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
                    {"number": "L1", "name": "Leaf", "file": "l1.md", "status": "inProgress"}
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
    started = default_contract(
        ContractTask(
            name="restart-master",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
            # The atomic leaf's parent is its own task series (same address the
            # restart derives); the successor check compares these names.
            parent_task_name="restart-master",
            parent_contract_path=series_contract_path(task_root),
        ),
        leaf=LeafIdentity(
            worktree_name="first-leaf",
            leaf_id="L1",
            lifecycle_id="l50-no-mode-restart-lifecycle",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/first-leaf",
            base_commit=git(code, "rev-parse", "super"),
        ),
    )
    assert started.memory_repo_path is None
    git(
        code,
        "worktree",
        "add",
        "-b",
        started.code_work_branch,
        str(started.code_worktree),
        "super",
    )
    # Genuine serializer bytes with the legacy cells (same discipline as the
    # abandon route test): the request vocabulary refuses to create the
    # removed mode, but the model reads and rewrites it.
    legacy = replace(started, memory_mode="disabled", memory_worktree=None, ledger_path=None)
    write_contract(legacy.contract_path, legacy)
    tombstone = load_contract(legacy.contract_path)
    assert tombstone.memory_mode == "disabled"
    publish_new_lifecycle_operation_location(
        tombstone,
        contract_text=legacy.contract_path.read_text(encoding="utf-8"),
    )
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coord.as_posix(),
                "workspaceRoot": tmp_path.as_posix(),
                "repositories": {REPO: {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    abandoned = worktree_tools.worktree_abandon_tool(
        load_config(settings),
        contract_path=legacy.contract_path.as_posix(),
        dry_run=False,
    )
    assert abandoned["state"] == "abandoned", abandoned
    if cleanup != "abandoned":
        # An already-reopened legacy tombstone is produced exactly as the
        # pre-cutover reopen produced it: the product's own reset transform
        # over the genuinely abandoned object, written with the product's own
        # write. No hand edit, no locator rewrite.
        reopened = _reopened_contract(load_contract(legacy.contract_path))
        assert reopened.cleanup == "reopened"
        assert reopened.memory_mode == "disabled"
        write_contract(legacy.contract_path, reopened)
    return world


def test_reopen_restart_arguments_never_carry_the_removed_mode(tmp_path: Path) -> None:
    """Restart arguments name no mode for a legacy contract, the mode for external."""

    world = _world(tmp_path, memory="plain")
    coord = world.coord
    assert isinstance(coord, Path)
    task_root = coord / "tasks" / REPO / "some-task"
    legacy = WorktreeContract(
        task_id="L50",
        task_name="some-task",
        repo_name=REPO,
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=coord,
        task_root=task_root,
        contract_path=task_root / "enclosures" / "leaf" / "series-contract.md",
        task_artifact=task_root / "l1.md",
        worktree_group=task_root / "worktrees",
        code_repo_path=world.code,
        code_source_branch="super",
        code_work_branch="ar/legacy-leaf",
        code_base_commit="a" * 40,
        code_worktree=task_root / "worktrees" / "legacy-leaf",
    )
    assert "memory_mode" not in _start_preview_args(legacy)
    external = default_contract(
        ContractTask(
            name="some-task",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="legacy-leaf", leaf_id="L50"),
        code=RepoBranchPlan(
            repo_path=world.code,
            source_branch="super",
            work_branch="ar/legacy-leaf",
            base_commit="a" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=coord / "memory-repos" / f"ar-{REPO}",
            source_branch="super",
            work_branch="ar/legacy-leaf",
            base_commit="b" * 40,
        ),
    )
    assert _start_preview_args(external)["memory_mode"] == "external"


def test_missing_memory_repository_offers_no_disabled_choice(tmp_path: Path) -> None:
    """The blocked start offers the setup choice only."""

    world = _world(tmp_path, memory=None)
    coord = world.coord
    assert isinstance(coord, Path)
    contract = default_contract(
        ContractTask(
            name="some-task",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="some-leaf", leaf_id="L1"),
        code=RepoBranchPlan(
            repo_path=world.code,
            source_branch="super",
            work_branch="ar/some-leaf",
            base_commit="a" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=coord / "memory-repos" / f"ar-{REPO}",
            source_branch="super",
            work_branch="ar/some-leaf",
            base_commit="b" * 40,
        ),
    )
    state = prepare_memory_for_start(
        contract, _start_args(world, dry_run=True), preview_parent=None
    )
    assert state["state"] == "blocked"
    assert state["choices"] == ["initialize-memory-repo"]


def test_missing_memory_repository_refuses_the_choice_answer(tmp_path: Path) -> None:
    """Answering the missing repository with `disabled-memory` is refused by name."""

    world = _world(tmp_path, memory=None)
    coord = world.coord
    assert isinstance(coord, Path)
    contract = default_contract(
        ContractTask(
            name="some-task",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="some-leaf", leaf_id="L1"),
        code=RepoBranchPlan(
            repo_path=world.code,
            source_branch="super",
            work_branch="ar/some-leaf",
            base_commit="a" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=coord / "memory-repos" / f"ar-{REPO}",
            source_branch="super",
            work_branch="ar/some-leaf",
            base_commit="b" * 40,
        ),
    )
    state = prepare_memory_for_start(
        contract,
        _start_args(world, memory_choice="disabled-memory", dry_run=True),
        preview_parent=None,
    )
    assert state["state"] == STATUS
    assert state["status"] == STATUS


def test_opt_out_error_carries_its_own_status() -> None:
    """The raised error and the published fields agree on the one status."""

    fields = opt_out_result_fields(OptOutSubject(), "worktree_sync", "leaf")
    assert fields["status"] == STATUS
    assert fields["requested"] == "disabled"
    assert fields["supported"] == ["external"]
    error = MemoryOptOutRefusedError("detail")
    assert error.status == STATUS
