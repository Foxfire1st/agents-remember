"""Shared contract and task-document fixtures for task reopen tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.tasks import SprintExecutionGraph, TaskDocument, write_task_doc
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import (
    publish_test_enclosure,
    terminalize_test_enclosure,
)
from test_worktree_support import git, init_repo


def _publish_restamp(task_root: Path, document: TaskDocument) -> object:
    return write_task_doc(task_root, document)


def _external_memory_repo(
    coordination_root: Path,
    repo_name: str,
    branch: str,
    *,
    code_commit: str,
) -> tuple[RepoBranchPlan, Path]:
    """One external memory repository with a real ledger, and the branch plan addressing it.

    The ledger is not decoration: a series reopen proves its *memory* landing is still an
    ancestor of the memory source tip, and a repository whose ledger pair was never recorded
    has no memory landing to prove. The plan's base commit is the ledger's memory commit, so
    the fixture's contract cells and the repository agree about where the memory side landed.

    ``branch`` is the repository's *own* default branch. It must not be the code repository's
    default branch when a sprint is in play: a sprint-super surface owns one branch name on both
    sides, and a memory repository whose default shares it collides with the repository-default
    surface (``integration-branch authority collision``). The recorded default therefore stays
    ``main`` even when the series source is a sprint super.
    """

    memory_repo = coordination_root / "memory-repos" / f"ar-{repo_name}"
    memory_base = init_repo(memory_repo, "main")
    # The memory default branch is a *recorded* fact, not a fixed name: the branch authority
    # reads `agents-remember.defaultBranch` and refuses when it is absent or unresolvable
    # (`memory_init` is its writer), so a fixture that omits it leaves the authority to fall
    # back to the remote default and disagree with the branch it actually created. It stays
    # `main` while the *series source* may be a sprint super: a sprint-super surface owns one
    # branch name on both sides, and a memory repository whose own default shares that name
    # collides with it in `_deduplicated`.
    git(memory_repo, "config", "agents-remember.defaultBranch", "main")
    if branch != "main":
        git(memory_repo, "branch", branch, memory_base)
    for name in ("system", "onboarding"):
        (memory_repo / name).mkdir(parents=True, exist_ok=True)
        (memory_repo / name / ".gitkeep").write_text("", encoding="utf-8")
    write_ledger(
        memory_repo / "memory.md",
        create_initial_ledger(repo_name, code_commit, memory_base),
    )
    git(memory_repo, "add", "-A")
    git(memory_repo, "commit", "-m", "Add ledger")
    memory_base = git(memory_repo, "rev-parse", "HEAD")
    # One unique content commit per world. Git hashes content, so two fixtures that build the same
    # tree produce the *same* commit -- and the reopen's reachability proof would then be satisfied
    # by an object that belongs to a different world's line. A fixture whose memory landing is not
    # uniquely its own cannot pin a guard about that landing.
    (memory_repo / "fixture-world.txt").write_text(f"{coordination_root.name}\n", encoding="utf-8")
    # The ledger pair must name the commit that *becomes* the memory landing this contract
    # records, and that landing is the branch tip a leaf starts from -- this commit, made after
    # the seed ledger was written. Recording the pre-commit value instead leaves the reopen's
    # reachability guard holding a landing no branch contains, which is exactly the refusal it
    # exists to make: a record that cannot be proven must be repaired, not trusted.
    write_ledger(
        memory_repo / "memory.md",
        create_initial_ledger(repo_name, code_commit, memory_base),
    )
    git(memory_repo, "add", "-A")
    git(memory_repo, "commit", "-m", "Record the ledger pair and this world's identity")
    memory_base = git(memory_repo, "rev-parse", "HEAD")
    if branch != "main":
        git(memory_repo, "branch", "-f", branch, memory_base)
    return (
        RepoBranchPlan(
            repo_path=memory_repo,
            source_branch=branch,
            work_branch="ar/260698_demo-series",
            base_commit=memory_base,
        ),
        memory_repo,
    )


def _completed_series_contract(
    workspace: Path,
    *,
    repo_name: str = "repo-a",
    memory_mode: str = "disabled",
    sprint: bool = False,
) -> WorktreeContract:
    """A terminal atomic series: landed, cleaned up, its enclosure root collected.

    The fixture reproduces the state a completed master is left in -- the integration branch is
    retired by cleanup, the series contract reads ``cleanup: completed``, the enclosure locator
    is ``terminal-archived`` and its root is gone -- instead of a synthetic variant of it, because
    the defect this fixture exists for is precisely that no tool could leave that state behind.

    ``memory_mode`` ``external`` additionally builds the memory repository and its ledger, which
    is what a reopened series needs before it can start a child leaf at all: a start resolves the
    repository's configured memory topology, and a series whose recorded memory landing is absent
    is refused by the reopen's own reachability precondition.

    ``sprint`` commands this master from a sprint whose ``integrationBranch`` is ``super``, which
    is what makes the master *landable*: generic integration refuses a standalone atomic series
    onto a repository-default branch by design, because only the PR landing plane may move that
    root. The default is the standalone shape the terminal-state case pins.
    """

    coordination_root = workspace / "ar-coordination"
    code_repo = workspace / repo_name
    base = init_repo(code_repo, "main")
    git(code_repo, "branch", "super", "main")
    git(code_repo, "update-ref", "refs/remotes/origin/super", base)
    task = ContractTask(
        name="260698_demo-series",
        repo_name=repo_name,
        coordination_root=coordination_root,
        workflow_kind="light-task",
        memory_mode=memory_mode,
    )
    memory_source = "super" if sprint else "main"
    memory_plan = (
        _external_memory_repo(
            coordination_root,
            repo_name,
            memory_source,
            code_commit=base,
        )[0]
        if memory_mode == "external"
        else None
    )
    contract = default_series_contract(
        task,
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super" if sprint else "main",
            work_branch="ar/260698_demo-series",
            base_commit=base,
        ),
        memory=memory_plan,
    )
    memory_commit = memory_plan.base_commit if memory_plan is not None else ""
    master_fields: dict[str, object] = {
        "id": "260698_DEMO-SERIES",
        "slug": "task",
        "title": "Demo Series",
        "kind": "master",
        "status": "Completed",
        "repo": repo_name,
        "createdAt": "2026-07-01T09:00",
        "executionNature": "atomic",
        "subTasks": [],
    }
    if sprint:
        master_fields["master"] = "../260698_demo-sprint/task.md"
        write_task_doc(
            contract.task_root.parent / "260698_demo-sprint",
            TaskDocument.model_validate(
                {
                    "id": "260698_DEMO-SPRINT",
                    "slug": "task",
                    "title": "Demo Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": repo_name,
                    "createdAt": "2026-07-01T08:00",
                    "orchestrates": [contract.task_root.name],
                    "integrationBranch": "super",
                    "executionGraph": SprintExecutionGraph.model_validate(
                        {
                            "nodes": [
                                {
                                    "repository": repo_name,
                                    "path": f"{contract.task_root.name}/task.json",
                                }
                            ],
                            "edges": [],
                        }
                    ),
                }
            ),
        )
    write_task_doc(contract.task_root, TaskDocument.model_validate(master_fields))
    contract = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=base,
        memory_content_commit=memory_commit,
        integration_status="completed",
        integrated_code_commit=base,
        integrated_memory_content_commit=memory_commit,
        cleanup="completed",
    )
    write_contract(contract.contract_path, contract)
    location = publish_test_enclosure(
        contract,
        contract.contract_path.read_text(encoding="utf-8"),
    )
    terminalize_test_enclosure(location)
    return contract


def _publish_terminal_reopen_predecessor(contract: WorktreeContract) -> None:
    """Publish the completed generation that authorizes one reopened successor."""

    terminal = replace(
        contract,
        lifecycle_id="LC-OLD",
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=contract.code_base_commit,
        integration_status="completed",
        integrated_code_commit=contract.code_base_commit,
        cleanup="completed",
    )
    location = publish_test_enclosure(
        terminal,
        contract_publication_text(terminal.contract_path, terminal),
    )
    terminalize_test_enclosure(location)


def _completed_leaf_contract(workspace: Path) -> WorktreeContract:
    coordination_root = workspace / "ar-coordination"
    code_repo = workspace / "repo-a"
    base = init_repo(code_repo, "main")
    git(code_repo, "branch", "super", "main")
    git(code_repo, "branch", "ar/01-demo-leaf", "super")
    task = ContractTask(
        name="260698_demo-series",
        repo_name="repo-a",
        coordination_root=coordination_root,
        workflow_kind="light-task",
        memory_mode="disabled",
    )
    contract = default_contract(
        task,
        leaf=LeafIdentity(
            worktree_name="01-demo-leaf",
            leaf_id="260698-l1",
            lifecycle_id="LC-OLD",
        ),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super",
            work_branch="ar/01-demo-leaf",
            base_commit=base,
        ),
    )
    contract = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=base,
        integration_status="completed",
        integrated_code_commit=base,
        cleanup="completed",
    )
    write_contract(contract.contract_path, contract)
    location = publish_test_enclosure(
        contract,
        contract.contract_path.read_text(encoding="utf-8"),
    )
    terminalize_test_enclosure(location)
    return contract


def _runtime_config(root: Path, contract: WorktreeContract) -> McpRuntimeConfig:
    """The runtime config a real operation loads, written to disk rather than only in memory.

    The closeout and integration admission paths reload the authority settings file from the
    coordination root, so a config object whose ``config_path`` does not exist refuses before
    any work happens. The repository is declared at the same absolute path the contract carries,
    which is what the configured-repository authority compares identity against.
    """

    config_path = root / "mcp.settings.json"
    if not config_path.exists():
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": contract.coordination_root.as_posix(),
                    "workspaceRoot": root.as_posix(),
                    "directExecutionEnabled": False,
                    "repositories": {
                        contract.repo_name: {"path": contract.code_repo_path.as_posix()}
                    },
                }
            ),
            encoding="utf-8",
        )
    return load_config(config_path)


def _external_memory_dirs(coordination_root: Path) -> None:
    for name in ("system", "onboarding"):
        (coordination_root / "memory-repos" / "ar-repo-a" / name).mkdir(parents=True)


def _leaf_doc(
    task_root: Path,
    *,
    lifecycle_id: str | None = "LC-OLD",
    master: str | None = "task.md",
    status: str = "Completed",
    step: dict[str, object] | None = None,
) -> Path:
    doc = TaskDocument.model_validate(
        {
            "id": "260698-L1",
            "slug": "01_demo-leaf",
            "title": "L1 — Demo leaf",
            "kind": "subTask",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T10:00",
            "lifecycleId": lifecycle_id,
            "master": master,
            "steps": [step or {"id": "S1", "title": "do the thing", "status": "done"}],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


def _master_doc(
    task_root: Path,
    *,
    duplicate_row: bool = False,
    row_number: str = "260698-L1",
    row_file: str = "01_demo-leaf.md",
    statuses: tuple[str, str] = ("Completed", "Completed"),
) -> Path:
    write_task_doc(
        task_root.parent / "260698_demo-sprint",
        TaskDocument(
            id="260698_DEMO-SPRINT",
            slug="task",
            title="Demo Sprint",
            kind="master",
            status="inProgress",
            repo="repo-a",
            createdAt="2026-07-01T08:00",
            orchestrates=[task_root.name],
            integrationBranch="super",
            executionGraph=SprintExecutionGraph.model_validate(
                {
                    "nodes": [
                        {
                            "repository": "repo-a",
                            "path": f"{task_root.name}/task.json",
                        }
                    ],
                    "edges": [],
                }
            ),
        ),
    )
    status, row_status = statuses
    row = {
        "number": row_number,
        "name": "L1 — Demo leaf",
        "file": row_file,
        "status": row_status,
    }
    doc = TaskDocument.model_validate(
        {
            "id": "260698_DEMO-SERIES",
            "slug": "task",
            "title": "Demo Series",
            "kind": "master",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T09:00",
            "executionNature": "organizational",
            "subTasks": [row, dict(row)] if duplicate_row else [row],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path
