"""End-to-end and refusal tests for PR-landed standalone master finalization."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.application.worktree_tool_requests import LandedCommits
from agents_remember.application.worktree_tools import (
    lifecycle_finalize_task_tool,
    worktree_cleanup_tool,
    worktree_record_landing_tool,
    worktree_sync_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.tool_answer import is_valid_tool_code
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    observe_atomic_series,
    publish_atomic_series_selection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.standalone_pr_landing import (
    CONTRACT_ABANDONED_CODE,
    PR_STRATEGY,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.modules.record_landing import record_landing_result
from agents_remember.worktrees.services import bind_worktree_services
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from tool_answer_l111_test_support import RECORD_LANDING_REFUSAL_CODES

pytestmark = pytest.mark.integration

REPO = "agents-remember"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=ar@example.invalid", "-c", "user.name=AR", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class StandalonePrWorld:
    """Test world starting from ``cleanup: pending`` and non-Completed masters."""

    def __init__(self, root: Path) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.root = root
        self.coord = root / "coord"
        self.tasks = self.coord / "tasks" / REPO
        self.tasks.mkdir(parents=True, exist_ok=True)
        self.code = root / REPO
        self.code.mkdir()
        _git(self.code, "init", "-b", "main")
        (self.code / "base.txt").write_text("base\n", encoding="utf-8")
        _git(self.code, "add", "-A")
        _git(self.code, "commit", "-m", "base")
        self.code_base = _git(self.code, "rev-parse", "main")
        _git(self.code, "update-ref", "refs/remotes/origin/main", self.code_base)
        _git(self.code, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

        self.memory = self.coord / "memory-repos" / f"ar-{REPO}"
        self.memory.mkdir(parents=True)
        _git(self.memory, "init", "-b", "main")
        (self.memory / "memory.md").write_text("# memory\n", encoding="utf-8")
        (self.memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (self.memory / "knowledge" / "layout.json").write_text(
            canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        _git(self.memory, "add", "-A")
        _git(self.memory, "commit", "-m", f"base\n\nCode-Commit: {self.code_base}")
        self.memory_base = _git(self.memory, "rev-parse", "main")
        _git(self.memory, "update-ref", "refs/remotes/origin/main", self.memory_base)
        _git(self.memory, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

        settings_path = root / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coord.as_posix(),
                    "workspaceRoot": root.as_posix(),
                    "retirement": {"autoLandOnIntegration": False},
                    "repositories": {REPO: {}},
                }
            ),
            encoding="utf-8",
        )
        self.cfg: McpRuntimeConfig = load_config(settings_path)

    def make_master(
        self,
        name: str = "standalone-pr",
        *,
        status: str = "inProgress",
        source_branch: str = "main",
        merge_pr: bool = True,
        activate: bool = True,
    ) -> tuple[WorktreeContract, str, str]:
        """Create an atomic series master with ``cleanup: pending`` and optional PR merge."""
        work_branch = f"ar/{name}"
        _git(self.code, "branch", work_branch, source_branch)
        _git(self.memory, "branch", work_branch, source_branch)
        code_base = _git(self.code, "rev-parse", source_branch)
        memory_base = _git(self.memory, "rev-parse", source_branch)

        task_root = self.tasks / name
        task_root.mkdir(parents=True, exist_ok=True)
        leaf_doc = TaskDocument.model_validate(
            {
                "id": "1",
                "slug": "1_leaf",
                "title": f"Leaf 1 for {name}",
                "kind": "subTask",
                "status": "Completed",
                "repo": REPO,
                "createdAt": "2026-11-26T00:00:00+00:00",
                "master": "task.md",
                "objective": "Deliver the standalone master change.",
                "requirements": ["Deliver the standalone master change."],
            }
        )
        write_task_doc(task_root, leaf_doc)
        master_doc = TaskDocument.model_validate(
            {
                "id": name.upper(),
                "slug": name,
                "title": f"Master {name}",
                "kind": "master",
                "status": status,
                "repo": REPO,
                "createdAt": "2026-11-26T00:00:00+00:00",
                "executionNature": "atomic",
                "subTasks": [
                    SubTaskRef(
                        number="1",
                        name=f"Leaf 1 for {name}",
                        file="1_leaf.json",
                        status="Completed",
                    ).model_dump(mode="json")
                ],
            }
        )
        write_task_doc(task_root, master_doc)

        contract = default_series_contract(
            ContractTask(name, REPO, self.coord, "light-task", "external"),
            code=RepoBranchPlan(self.code, source_branch, work_branch, code_base),
            memory=RepoBranchPlan(self.memory, source_branch, work_branch, memory_base),
            task_root=task_root,
        )
        write_contract(contract.contract_path, contract)
        publish_new_lifecycle_operation_location(
            contract,
            contract_text=contract_publication_text(contract.contract_path, contract),
        )
        if activate and status != "abandoned":
            publish_atomic_series_selection(contract, "active")

        landed_code = code_base
        landed_memory = memory_base
        if merge_pr:
            _git(self.code, "switch", source_branch)
            (self.code / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
            _git(self.code, "add", f"{name}.txt")
            _git(self.code, "commit", "-m", f"Merge PR {name}")
            landed_code = _git(self.code, "rev-parse", source_branch)
            _git(self.code, "update-ref", f"refs/remotes/origin/{source_branch}", landed_code)
            _git(self.memory, "update-ref", f"refs/remotes/origin/{source_branch}", landed_memory)
            if status != "abandoned":
                synced = worktree_sync_tool(
                    self.cfg,
                    contract_path=contract.contract_path.as_posix(),
                    memory_sync_choice="merge-memory",
                    dry_run=False,
                )
                assert synced["ok"], synced

        return load_contract(contract.contract_path), landed_code, landed_memory

    def attach_sprint(self, master_name: str, *, sprint_branch: str = "super") -> None:
        """Command ``master_name`` from a sprint master so ``sprint_ref`` is present."""
        for repo in (self.code, self.memory):
            if (
                subprocess.run(
                    ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{sprint_branch}"],
                    cwd=repo,
                    check=False,
                ).returncode
                != 0
            ):
                _git(repo, "branch", sprint_branch, "main")
        sprint_root = self.tasks / "sprint"
        sprint_root.mkdir(parents=True, exist_ok=True)
        master_ref = TaskDocumentRef(repository=REPO, path=f"{master_name}/task.json")
        sprint_doc = TaskDocument.model_validate(
            {
                "id": "SPRINT",
                "slug": "sprint",
                "title": "Sprint",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-11-26T00:00:00+00:00",
                "integrationBranch": sprint_branch,
                "orchestrates": [master_name],
                "subTasks": [
                    SubTaskRef(
                        number="0",
                        name=f"Master {master_name}",
                        status="inProgress",
                        masterRef=master_ref,
                    ).model_dump(mode="json")
                ],
            }
        )
        write_task_doc(sprint_root, sprint_doc)

    def make_leaf(
        self,
        parent: WorktreeContract,
        leaf_id: str = "L1",
        *,
        leaf_status: str = "inProgress",
    ) -> WorktreeContract:
        """Create a leaf contract and leaf task document under ``parent``."""
        leaf_doc = TaskDocument.model_validate(
            {
                "id": leaf_id,
                "slug": f"1_{leaf_id.lower()}",
                "title": f"Leaf {leaf_id}",
                "kind": "subTask",
                "status": leaf_status,
                "repo": REPO,
                "createdAt": "2026-11-26T00:00:00+00:00",
                "master": "task.md",
            }
        )
        write_task_doc(parent.task_root, leaf_doc)
        leaf = default_contract(
            ContractTask(
                name=parent.task_name,
                repo_name=REPO,
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="external",
                parent_task_name=parent.task_name,
                parent_contract_path=parent.contract_path,
            ),
            leaf=LeafIdentity(worktree_name=f"wt-{leaf_id.lower()}", leaf_id=leaf_id),
            code=RepoBranchPlan(
                self.code,
                parent.code_work_branch,
                f"ar/{leaf_id.lower()}",
                self.code_base,
            ),
            memory=RepoBranchPlan(
                self.memory,
                parent.memory_work_branch,
                f"ar/{leaf_id.lower()}",
                self.memory_base,
            ),
        )
        write_contract(leaf.contract_path, leaf)
        return load_contract(leaf.contract_path)


def _branch_exists(repo: Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo,
            check=False,
        ).returncode
        == 0
    )


def _finalize_blockers(contract_path: Path) -> tuple[str, list[str]]:
    payload = finalize_result(FinalizeArgs(contract_path=contract_path)).payload
    blockers = payload["blockers"]
    assert isinstance(blockers, list)
    return str(payload["state"]), [str(item) for item in blockers]


def _record_pr(
    world: StandalonePrWorld,
    contract: WorktreeContract,
    code_commit: str,
    memory_commit: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return worktree_record_landing_tool(
        world.cfg,
        contract_path=contract.contract_path.as_posix(),
        landed=LandedCommits(code=code_commit, memory_content=memory_commit),
        dry_run=dry_run,
    )


def test_pr_landed_standalone_master_end_to_end_records_finalizes_and_retires(tmp_path: Path):
    """Evidence 1 + 2: record_landing -> lifecycle_finalize_task -> retire_master end to end."""
    world = StandalonePrWorld(tmp_path)
    contract, landed_code, landed_memory = world.make_master("e2e-standalone")
    assert contract.cleanup == "pending"
    assert read_task_doc(contract.task_root / "task.json").status == "inProgress"
    assert observe_atomic_series(contract).state == "active"

    dry_record = _record_pr(world, contract, landed_code, landed_memory, dry_run=True)
    assert dry_record["ok"] and dry_record["state"] == "would-record", dry_record

    recorded = _record_pr(world, contract, landed_code, landed_memory)
    assert recorded["ok"] and recorded["state"] == "recorded", recorded
    after_record = load_contract(contract.contract_path)
    assert after_record.integration_strategy == PR_STRATEGY
    assert after_record.integration_status == "completed"
    assert after_record.integrated_code_commit == landed_code
    assert after_record.integrated_memory_content_commit == landed_memory
    assert after_record.cleanup == "pending"
    assert after_record.closeout_status == "not-started"
    assert after_record.code_commit == ""

    dry_finalize = lifecycle_finalize_task_tool(
        world.cfg, contract.contract_path.as_posix(), dry_run=True
    )
    assert dry_finalize["ok"] and dry_finalize["state"] == "would-finalize", dry_finalize
    assert read_task_doc(contract.task_root / "task.json").status == "inProgress"
    assert _branch_exists(world.code, contract.code_work_branch)

    finalized = lifecycle_finalize_task_tool(world.cfg, contract.contract_path.as_posix())
    assert finalized["ok"] and finalized["state"] == "finalized", finalized
    assert finalized["landedCommit"] == landed_code
    assert finalized["taskArchive"]["state"] == "skipped"
    assert finalized["taskArchive"]["reason"] == "master-archived-only-by-retire"

    after_finalize = load_contract(contract.contract_path)
    assert after_finalize.cleanup == "completed"
    assert not _branch_exists(world.code, contract.code_work_branch)
    assert not _branch_exists(world.memory, contract.memory_work_branch)
    master_doc = read_task_doc(contract.task_root / "task.json")
    assert master_doc.status == "Completed" and master_doc.completedAt is not None
    assert observe_atomic_series(after_finalize).state == "vacant"
    assert contract.task_root.is_dir()

    master_ref = TaskDocumentRef(repository=REPO, path="e2e-standalone/task.json")
    retire_fields = {
        "masterRef": master_ref.model_dump(),
        "reason": "Standalone master landed by PR and finalized.",
    }
    dry_retire = task_doc_tool(
        world.cfg,
        TaskDocTarget(repo_id=REPO, task_name="e2e-standalone"),
        operation="retire_master",
        edit=TaskDocEdit(fields=retire_fields),
        call=TaskDocCall(dry_run=True),
    )
    assert dry_retire["ok"] and dry_retire["state"] == "would-retire", dry_retire
    assert dry_retire["taskArchive"]["state"] == "would-archive"

    retired = task_doc_tool(
        world.cfg,
        TaskDocTarget(repo_id=REPO, task_name="e2e-standalone"),
        operation="retire_master",
        edit=TaskDocEdit(fields=retire_fields),
        call=TaskDocCall(dry_run=False),
    )
    assert retired["ok"] and retired["state"] == "retired", retired
    archived_root = world.tasks / "0_archive" / "e2e-standalone"
    assert not contract.task_root.exists()
    assert (archived_root / "task.json").is_file()
    assert read_task_doc(archived_root / "task.json").status == "Completed"
    assert (archived_root / "notes" / "reports" / "master-retirement.json").is_file()


def test_pr_landed_standalone_master_is_directly_cleanable_before_completed(tmp_path: Path):
    """Rule 3: direct worktree_cleanup accepts a PR-landed standalone master before Completed."""
    world = StandalonePrWorld(tmp_path)
    contract, landed_code, landed_memory = world.make_master("direct-clean")
    assert _record_pr(world, contract, landed_code, landed_memory)["state"] == "recorded"

    dry_clean = worktree_cleanup_tool(
        world.cfg, contract_path=contract.contract_path.as_posix(), dry_run=True
    )
    assert dry_clean["ok"] and dry_clean["state"] == "would-cleanup", dry_clean

    cleaned = worktree_cleanup_tool(
        world.cfg, contract_path=contract.contract_path.as_posix(), dry_run=False
    )
    assert cleaned["ok"] and cleaned["state"] == "cleanup-completed", cleaned
    assert load_contract(contract.contract_path).cleanup == "completed"
    assert not _branch_exists(world.code, contract.code_work_branch)
    assert observe_atomic_series(load_contract(contract.contract_path)).state == "vacant"
    assert read_task_doc(contract.task_root / "task.json").status == "inProgress"

    finalized = lifecycle_finalize_task_tool(world.cfg, contract.contract_path.as_posix())
    assert finalized["ok"] and finalized["state"] == "finalized", finalized
    assert read_task_doc(contract.task_root / "task.json").status == "Completed"


def test_finalize_cleanup_failure_leaves_master_document_unchanged(tmp_path: Path):
    """Rule 3: finalize runs cleanup before task documents; a failed cleanup changes neither."""
    world = StandalonePrWorld(tmp_path)
    contract, landed_code, landed_memory = world.make_master("blocked-clean")
    assert _record_pr(world, contract, landed_code, landed_memory)["state"] == "recorded"

    # Add an unmerged commit on the work branch so cleanup refuses to delete it.
    _git(world.code, "switch", contract.code_work_branch)
    (world.code / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
    _git(world.code, "add", "unmerged.txt")
    _git(world.code, "commit", "-m", "unmerged work")
    _git(world.code, "switch", "main")

    blocked = lifecycle_finalize_task_tool(world.cfg, contract.contract_path.as_posix())
    assert blocked["state"] == "cleanup-blocked", blocked
    assert blocked["summary"] == "Cleanup did not complete; task documents were not changed."
    assert read_task_doc(contract.task_root / "task.json").status == "inProgress"


def test_finalize_refusals_for_missing_unlanded_and_abandoned_standalone_master(tmp_path: Path):
    """Rule 2 + Evidence 1: landed-commit-missing, not-landed-on-target-branch, master-abandoned."""
    world = StandalonePrWorld(tmp_path)
    contract, landed_code, landed_memory = world.make_master("refusal-cases")

    # 1. Missing integrated_code_commit.
    write_contract(
        contract.contract_path,
        replace(
            contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit="",
            integrated_memory_content_commit=landed_memory,
        ),
    )
    state, blockers = _finalize_blockers(contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "landed-commit-missing" in blockers
    assert "closeout-not-complete" not in blockers

    # 2. Missing integrated_memory_content_commit with external memory.
    write_contract(
        contract.contract_path,
        replace(
            contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit="",
        ),
    )
    state, blockers = _finalize_blockers(contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "landed-commit-missing" in blockers
    assert "memory-carryover-incomplete" in blockers

    # 3. Unreachable integrated_memory_content_commit (not on memory_source_branch).
    _git(world.memory, "switch", "-c", "mem-side", "main")
    (world.memory / "side.txt").write_text("side\n", encoding="utf-8")
    _git(world.memory, "add", "side.txt")
    _git(world.memory, "commit", "-m", "unlanded memory")
    unlanded_mem = _git(world.memory, "rev-parse", "mem-side")
    _git(world.memory, "switch", "main")
    write_contract(
        contract.contract_path,
        replace(
            contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit=unlanded_mem,
        ),
    )
    state, blockers = _finalize_blockers(contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "landed-commit-missing" in blockers

    # 4. integrated_code_commit not on code_source_branch.
    _git(world.code, "switch", "-c", "code-side", "main")
    (world.code / "side.txt").write_text("side\n", encoding="utf-8")
    _git(world.code, "add", "side.txt")
    _git(world.code, "commit", "-m", "unlanded code")
    unlanded_code = _git(world.code, "rev-parse", "code-side")
    _git(world.code, "switch", "main")
    write_contract(
        contract.contract_path,
        replace(
            contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=unlanded_code,
            integrated_memory_content_commit=landed_memory,
        ),
    )
    state, blockers = _finalize_blockers(contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "not-landed-on-target-branch" in blockers

    # 5. Contract cleanup reads abandoned.
    write_contract(
        contract.contract_path,
        replace(
            contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit=landed_memory,
            cleanup="abandoned",
        ),
    )
    state, blockers = _finalize_blockers(contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "master-abandoned" in blockers

    # 6. Master task document reads abandoned (with cleanup pending): finalize & cleanup refuse.
    write_contract(
        contract.contract_path,
        replace(
            contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit=landed_memory,
            cleanup="pending",
        ),
    )
    master_doc = read_task_doc(contract.task_root / "task.json")
    write_task_doc(contract.task_root, master_doc.model_copy(update={"status": "abandoned"}))
    state, blockers = _finalize_blockers(contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "master-abandoned" in blockers

    cleanup_abandoned = worktree_cleanup_tool(
        world.cfg, contract_path=contract.contract_path.as_posix(), dry_run=True
    )
    assert not cleanup_abandoned["ok"]
    assert cleanup_abandoned["code"] == "master-not-completed"


def test_checkpoint_sprint_master_and_pr_leaf_keep_todays_refusals(tmp_path: Path):
    """Evidence 1: checkpoint, sprint-attached master, and PR-landed leaf keep existing answers."""
    world = StandalonePrWorld(tmp_path)

    # 1. Checkpoint stays blocked by integration-not-complete.
    ckpt, landed_code, landed_memory = world.make_master("ckpt-master", activate=False)
    write_contract(
        ckpt.contract_path,
        replace(
            ckpt,
            integration_strategy=PR_STRATEGY,
            integration_status="checkpointed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit=landed_memory,
        ),
    )
    state, blockers = _finalize_blockers(ckpt.contract_path)
    assert state == "not-finalizable-yet"
    assert "integration-not-complete" in blockers

    # 2. Sprint-attached master keeps today's answer (finalize + direct cleanup).
    world.attach_sprint("sprint-master", sprint_branch="super")
    sprint_contract, sprint_code, sprint_mem = world.make_master(
        "sprint-master", source_branch="super", activate=False
    )
    write_contract(
        sprint_contract.contract_path,
        replace(
            sprint_contract,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=sprint_code,
            integrated_memory_content_commit=sprint_mem,
        ),
    )
    state, blockers = _finalize_blockers(sprint_contract.contract_path)
    assert state == "not-finalizable-yet"
    assert "closeout-not-complete" in blockers
    assert "code-commit-missing" in blockers

    sprint_clean = worktree_cleanup_tool(
        world.cfg, contract_path=sprint_contract.contract_path.as_posix(), dry_run=True
    )
    assert not sprint_clean["ok"]
    assert sprint_clean["code"] == "master-not-completed"

    # 3. Leaf that recorded a PR landing without a closeout keeps today's blockers.
    parent, _, _ = world.make_master("leaf-parent", merge_pr=False, activate=False)
    leaf = world.make_leaf(parent, "L1")
    write_contract(
        leaf.contract_path,
        replace(
            leaf,
            integration_strategy=PR_STRATEGY,
            integration_status="completed",
            integrated_code_commit=world.code_base,
            integrated_memory_content_commit=world.memory_base,
        ),
    )
    state, blockers = _finalize_blockers(leaf.contract_path)
    assert state == "not-finalizable-yet"
    assert "closeout-not-complete" in blockers
    assert "code-commit-missing" in blockers


def test_record_landing_refuses_abandoned_contract_and_preserves_missing_doc(tmp_path: Path):
    """Rule 4 + Evidence 1: worktree_record_landing refuses abandoned contracts without writing."""
    assert CONTRACT_ABANDONED_CODE in RECORD_LANDING_REFUSAL_CODES
    assert all(is_valid_tool_code(code) for code in RECORD_LANDING_REFUSAL_CODES)

    world = StandalonePrWorld(tmp_path)
    contract, landed_code, landed_memory = world.make_master("abandoned-rec", activate=False)

    # 1. Contract cleanup == abandoned refuses (even if integration_status was completed) and keeps cleanup.
    for integration_status in ("not-started", "completed"):
        write_contract(
            contract.contract_path,
            replace(contract, cleanup="abandoned", integration_status=integration_status),
        )
        for dry_run in (True, False):
            refused = _record_pr(world, contract, landed_code, landed_memory, dry_run=dry_run)
            assert not refused["ok"]
            assert refused["state"] == "refused"
            assert refused["code"] == CONTRACT_ABANDONED_CODE
            stored = load_contract(contract.contract_path)
            assert stored.cleanup == "abandoned"
            assert stored.integration_status == integration_status

    # 2. Master task document status == abandoned refuses before recording.
    write_contract(contract.contract_path, replace(contract, cleanup="pending"))
    master_doc = read_task_doc(contract.task_root / "task.json")
    write_task_doc(contract.task_root, master_doc.model_copy(update={"status": "abandoned"}))
    refused_doc = _record_pr(world, contract, landed_code, landed_memory)
    assert not refused_doc["ok"]
    assert refused_doc["state"] == "refused"
    assert refused_doc["code"] == CONTRACT_ABANDONED_CODE
    assert load_contract(contract.contract_path).integration_status == "not-started"

    # 3. Leaf task document status == abandoned refuses before recording.
    write_task_doc(contract.task_root, master_doc.model_copy(update={"status": "inProgress"}))
    leaf = world.make_leaf(contract, "L9", leaf_status="abandoned")
    refused_leaf = record_landing_result(
        WorktreeArgs(
            contract_path=leaf.contract_path,
            approved=True,
            landed_code_commit=landed_code,
            landed_memory_content_commit=landed_memory,
        )
    )
    assert refused_leaf.returncode != 0
    assert refused_leaf.payload["state"] == "refused"
    assert refused_leaf.payload["code"] == CONTRACT_ABANDONED_CODE
    assert load_contract(leaf.contract_path).integration_status == "not-started"

    # 4. Missing task document is not a reason by itself; existing checks run as today.
    (contract.task_root / "task.json").unlink()
    missing_doc_res = record_landing_result(
        WorktreeArgs(
            contract_path=contract.contract_path,
            approved=True,
            landed_code_commit="",
            landed_memory_content_commit=landed_memory,
        )
    )
    assert missing_doc_res.returncode != 0
    assert missing_doc_res.payload["code"] == "commit-missing"
