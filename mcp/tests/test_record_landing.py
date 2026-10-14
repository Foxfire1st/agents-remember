"""The pull-request landing route records what the remote already did.

``worktree_integrate`` moves refs locally and then records what it moved. A pull request moves
nothing locally, so its route records the commit the caller names. These cases lock that recording
and the three ways it must refuse: an unapproved record, a missing commit, and a commit that landed
nowhere.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.application.worktree_tools import worktree_sync_tool
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.record_landing import PR_STRATEGY, record_landing_result
from agents_remember.worktrees.services import bind_worktree_services
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_series_contract,
    load_contract,
    write_contract,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=ar@example.invalid", "-c", "user.name=AR", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fixture(root: Path) -> WorktreeContract:
    """A series contract over a repository whose work branch has landed nowhere yet."""

    bind_worktree_services(build_default_worktree_services())
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    for branch in ("super", "ar/master"):
        _git(repo, "branch", branch)
    code_base = _git(repo, "rev-parse", "super")
    _git(repo, "update-ref", "refs/remotes/origin/super", code_base)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/super")
    memory_repo = root / "memory"
    memory_repo.mkdir()
    _git(memory_repo, "init", "-b", "main")
    (memory_repo / "memory.md").write_text("# memory\n", encoding="utf-8")
    # Converted memory: the marker rides the base commit and the base pairs with the code
    # base, so the recording runs the converted routes under the gate.
    (memory_repo / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory_repo / "knowledge" / "layout.json").write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )
    _git(memory_repo, "add", "-A")
    _git(memory_repo, "commit", "-m", f"base\n\nCode-Commit: {code_base}")
    for branch in ("super", "ar/master"):
        _git(memory_repo, "branch", branch)
    memory_base = _git(memory_repo, "rev-parse", "super")
    _git(memory_repo, "update-ref", "refs/remotes/origin/super", memory_base)
    _git(memory_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/super")
    coordination = root / "coordination"
    task_root = coordination / "tasks" / "repo" / "master"
    task_root.mkdir(parents=True)
    # Converted recording resolves the master task document through the gate.
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "master",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo",
                "createdAt": "2026-08-22T00:00:00+00:00",
                "subTasks": [
                    SubTaskRef(
                        number="1",
                        name="Leaf",
                        file="leaf.json",
                        status="planning",
                    ).model_dump(mode="json"),
                ],
            }
        ),
    )
    # Converted recording resolves the leaf task document through the gate.
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "1",
                "slug": "leaf",
                "title": "LEAF-1",
                "kind": "subTask",
                "status": "inProgress",
                "repo": "repo",
                "createdAt": "2026-08-15T00:00:00+00:00",
                "objective": "Exercise the exact fixture task intent.",
                "requirements": ["The fixture publishes canonical task intent."],
            }
        ),
    )
    contract = default_series_contract(
        ContractTask("master", "repo", coordination, "light-task", "external"),
        code=RepoBranchPlan(repo, "super", "ar/master", _git(repo, "rev-parse", "super")),
        memory=RepoBranchPlan(
            memory_repo, "super", "ar/master", _git(memory_repo, "rev-parse", "super")
        ),
        task_root=task_root,
    )
    write_contract(contract.contract_path, contract)
    # The sync reconciles through the published operation location.
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract_publication_text(contract.contract_path, contract),
    )
    return contract


def _commit_on(repo: Path, branch: str, name: str) -> str:
    _git(repo, "switch", branch)
    (repo / name).write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)
    return _git(repo, "rev-parse", branch)


def _record(
    contract: WorktreeContract,
    *,
    landed_code_commit: str = "",
    dry_run: bool = False,
):
    # Converted memory: the recording names the landed memory commit for the gate.
    # Nothing changed memory-side, so the landed memory commit is the line head.
    assert contract.memory_repo_path is not None
    landed_memory = _git(contract.memory_repo_path, "rev-parse", "super")
    return record_landing_result(
        WorktreeArgs(
            contract_path=contract.contract_path,
            approved=True,
            landed_code_commit=landed_code_commit,
            landed_memory_content_commit=landed_memory,
            dry_run=dry_run,
        )
    )


def _synced(root: Path, contract: WorktreeContract) -> WorktreeContract:
    """Reconcile the externally landed commits as the chain's own sync position.

    Converted lines prove the landing chain (MIK-R52 rule 4): a recorded pull-request
    landing is admitted through the sync the master reconciled, not as a leaf landing.
    The sync's branch authority wants the source at the repository default, so the
    landed source heads are published to the remote default first, the way a merged
    pull request advances them.
    """

    assert contract.memory_repo_path is not None
    for repo, source in (
        (contract.code_repo_path, contract.code_source_branch),
        (contract.memory_repo_path, contract.memory_source_branch),
    ):
        head = _git(repo, "rev-parse", source)
        _git(repo, "update-ref", f"refs/remotes/origin/{source}", head)
    code_link = root / "repo"
    if not code_link.exists():
        code_link.symlink_to(contract.code_repo_path, target_is_directory=True)
    if contract.memory_repo_path is not None:
        memory_link = contract.coordination_root / "memory-repos" / f"ar-{contract.repo_name}"
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
    config = load_config(config_path)
    synced = worktree_sync_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
        dry_run=False,
    )
    assert synced["ok"], synced
    return load_contract(contract.contract_path)


class RecordLandingTests(unittest.TestCase):
    def test_landed_commit_sets_the_terminal_integration_cell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")
            contract = _synced(Path(tmp), contract)

            result = _record(contract, landed_code_commit=landed)

            self.assertEqual((result.returncode, result.payload["state"]), (0, "recorded"))
            stored = load_contract(contract.contract_path)
            self.assertEqual(stored.integration_status, "completed")
            self.assertEqual(stored.integration_strategy, PR_STRATEGY)
            self.assertEqual(stored.integrated_code_commit, landed)

    def test_commit_that_landed_nowhere_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _commit_on(contract.code_repo_path, "super", "landed.txt")
            _git(contract.code_repo_path, "switch", "-c", "side", "super")
            (contract.code_repo_path / "unlanded.txt").write_text("x\n", encoding="utf-8")
            _git(contract.code_repo_path, "add", "-A")
            _git(contract.code_repo_path, "commit", "-m", "never merged")
            unlanded = _git(contract.code_repo_path, "rev-parse", "side")

            result = _record(contract, landed_code_commit=unlanded)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "refused")
            self.assertEqual(result.payload["code"], "commit-not-on-landing-target")
            self.assertIn("not reachable from any landing target", str(result.payload["error"]))
            self.assertEqual(
                load_contract(contract.contract_path).integration_status, "not-started"
            )

    def test_recording_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")

            with self.assertRaises(RuntimeError) as raised:
                record_landing_result(
                    WorktreeArgs(contract_path=contract.contract_path, landed_code_commit=landed)
                )

            self.assertIn("explicit developer approval", str(raised.exception))

    def test_a_missing_commit_argument_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))

            result = _record(contract)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "refused")
            self.assertEqual(result.payload["code"], "commit-missing")
            self.assertIn("requires landed_code_commit", str(result.payload["error"]))

    def test_dry_run_records_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")
            contract = _synced(Path(tmp), contract)
            assert contract.memory_repo_path is not None
            landed_memory = _git(contract.memory_repo_path, "rev-parse", "super")

            result = record_landing_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    landed_code_commit=landed,
                    landed_memory_content_commit=landed_memory,
                    dry_run=True,
                )
            )

            self.assertEqual(result.payload["state"], "would-record")
            self.assertEqual(
                load_contract(contract.contract_path).integration_status, "not-started"
            )

    def test_recording_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")
            contract = _synced(Path(tmp), contract)

            _record(contract, landed_code_commit=landed)
            again = _record(contract, landed_code_commit=landed)

            self.assertEqual(again.payload["state"], "already-recorded")

    def test_a_checkpointed_series_is_not_upgraded_into_a_reclaimable_integration(self) -> None:
        """A checkpoint landed a series that is still open, so this route must not close it.

        The full-record path writes ``completed`` plus ``cleanup="pending"``, and that pending
        cleanup is exactly what ``worktree_cleanup`` requires before it retires a branch. Taking
        it here would revoke the checkpoint's guarantee and make an open series reclaimable, so a
        checkpoint reads as already recorded and both cells keep the values the checkpoint wrote.
        """

        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            landed = _commit_on(contract.code_repo_path, "super", "landed.txt")
            write_contract(
                contract.contract_path,
                replace(load_contract(contract.contract_path), integration_status="checkpointed"),
            )

            result = _record(contract, landed_code_commit=landed)

            self.assertEqual(result.payload["state"], "already-recorded")
            stored = load_contract(contract.contract_path)
            self.assertEqual(stored.integration_status, "checkpointed")
            self.assertEqual(stored.cleanup, "pending")
