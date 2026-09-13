"""The checkpoint landing route pauses an unfinished atomic master without retiring it.

``worktree_integrate`` proves an atomic master is a finished unit: its task document is
``Completed`` and every canonical leaf has its own landed enclosure. A master being paused has
neither, so before this route existed a partial master had no way to land its accumulated line at
all. These cases lock exactly the two differences -- the recorded integration state and the dropped
completion assumptions -- plus the abandon guard that must keep refusing to retire a master whose
line is already upstream.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_terminal_worktree,
)
from agents_remember.worktrees.integration.integration_ref_transaction import IntegratedCommits
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import _checkpoint_result
from agents_remember.worktrees.modules.landing_record import (
    LandedIntegration,
    record_landed_integration,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.series_closeout import (
    SeriesCheckpointRefs,
    capture_series_checkpoint_refs,
    publish_series_checkpoint_under_authority,
    publish_series_integration_under_authority,
)
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    amend_contract,
    default_series_contract,
    load_contract,
    write_contract,
)

_ANY_COMMIT = "a" * 40


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=ar@example.invalid", "-c", "user.name=AR", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "repo": "repo",
            "createdAt": "2026-08-12T00:00",
            **values,
        }
    )


def _write_task_tree(coordination: Path) -> None:
    """One sprint commanding one atomic master with one still-open leaf."""

    task_root = coordination / "tasks" / "repo"
    write_task_doc(
        task_root / "sprint",
        _doc(
            id="SPRINT",
            slug="sprint",
            title="Sprint",
            kind="master",
            orchestrates=["master"],
            integrationBranch="super",
            executionGraph={
                "nodes": [{"repository": "repo", "path": "master/task.json"}],
                "edges": [],
            },
        ),
    )
    write_task_doc(
        task_root / "master",
        _doc(
            id="MASTER",
            slug="master",
            title="Master",
            kind="master",
            executionNature="atomic",
            status="inProgress",
            subTasks=[
                {"number": "leaf-1", "name": "Leaf", "file": "leaf-1.md", "status": "inProgress"}
            ],
        ),
    )
    write_task_doc(
        task_root / "master",
        _doc(id="leaf-1", slug="leaf-1", title="Leaf", kind="subTask", master="task.md"),
    )


def _code_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "super")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-m", "base")
    _git(path, "branch", "ar/master")
    # The terminal guard reads the remote default branch before it will retire anything, so the
    # repository must name its own default the way a real clone does.
    _git(path, "update-ref", "refs/remotes/origin/super", _git(path, "rev-parse", "super"))
    _git(path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/super")
    return path


def _fixture(root: Path) -> WorktreeContract:
    """An atomic series contract whose master is open and owns no leaf enclosure yet."""

    coordination = root / "coordination"
    code_repo = _code_repo(root / "code")
    task_root = coordination / "tasks" / "repo" / "master"
    _write_task_tree(coordination)
    contract = default_series_contract(
        ContractTask("master", "repo", coordination, "light-task", "disabled"),
        code=RepoBranchPlan(code_repo, "super", "ar/master", _git(code_repo, "rev-parse", "super")),
        memory=None,
        task_root=task_root,
    )
    write_contract(contract.contract_path, contract)
    return contract


def _set_master(contract: WorktreeContract, *, status: str, row_status: str) -> None:
    """Rewrite the master's own status and its single sub-task row status."""

    master = TaskDocument.model_validate_json(
        (contract.task_root / "task.json").read_text(encoding="utf-8")
    )
    write_task_doc(
        contract.task_root,
        master.model_copy(
            update={
                "status": status,
                "subTasks": [
                    row.model_copy(update={"status": row_status}) for row in master.subTasks
                ],
            }
        ),
    )


class IntegrationCellRecordingTests(unittest.TestCase):
    def test_checkpoint_records_the_state_and_leaves_cleanup_untouched(self) -> None:
        # ``cleanup`` is deliberately moved off its default before the checkpoint call: with the
        # default ``pending`` a route that rewrote the cell and one that left it alone would be
        # indistinguishable, and leaving it alone is the whole reason nothing is retired here.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            reopened = amend_contract(contract, ContractCells(cleanup="reopened"))
            write_contract(contract.contract_path, reopened)

            updated = record_landed_integration(
                reopened,
                landed=LandedIntegration(strategy="ff-only", code_commit=_ANY_COMMIT),
                checkpoint=True,
            )

            self.assertEqual(updated.integration_status, "checkpointed")
            self.assertEqual(updated.cleanup, "reopened")
            stored = load_contract(contract.contract_path)
            self.assertEqual(stored.integration_status, "checkpointed")
            self.assertEqual(stored.cleanup, "reopened")

    def test_final_landing_still_records_completed_and_marks_cleanup_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            reopened = amend_contract(contract, ContractCells(cleanup="reopened"))
            write_contract(contract.contract_path, reopened)

            updated = record_landed_integration(
                reopened,
                landed=LandedIntegration(strategy="ff-only", code_commit=_ANY_COMMIT),
            )

            self.assertEqual(updated.integration_status, "completed")
            self.assertEqual(updated.cleanup, "pending")
            self.assertEqual(load_contract(contract.contract_path).integration_status, "completed")


class CheckpointResultTests(unittest.TestCase):
    def setUp(self) -> None:
        # ``_checkpoint_result`` builds its payload through the ordinary status projection, which
        # reads the provider lifecycle. The unit population binds no services, so this one case
        # binds the default composition and releases it again.
        bind_worktree_services(build_default_worktree_services())

    def tearDown(self) -> None:
        reset_worktree_services()

    def test_checkpoint_result_publishes_without_running_cleanup(self) -> None:
        # "Retires nothing" is the property of every landing route now that reclamation belongs
        # to ``lifecycle_finalize_task``, and the checkpoint is where it is easiest to lose: its
        # payload goes through the ordinary status projection. ``cleanup`` is deliberately moved
        # off its default first, so a route that reclaimed -- or merely rewrote the cell -- stays
        # distinguishable from one that left the enclosure alone.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            commits = IntegratedCommits(code=_ANY_COMMIT, memory_content="", ledger="")
            reopened = amend_contract(contract, ContractCells(cleanup="reopened"))
            write_contract(contract.contract_path, reopened)

            result = _checkpoint_result(reopened, WorktreeArgs(strategy="ff-only"), commits)

            self.assertEqual((result.returncode, result.payload["state"]), (0, "checkpointed"))
            # The ``cleanup`` key is the untouched contract cell, and no reclamation report was
            # published beside it.
            self.assertEqual(result.payload["cleanup"], "reopened")
            self.assertNotIn("removed", result.payload)
            stored = load_contract(contract.contract_path)
            self.assertEqual(stored.integration_status, "checkpointed")
            self.assertEqual(stored.cleanup, "reopened")


class SeriesCheckpointAuthorityTests(unittest.TestCase):
    def test_checkpoint_refuses_a_completed_master(self) -> None:
        # A finished integration must never be downgraded to the weaker ``checkpointed`` claim.
        # The captured candidate is supplied honestly (``capture_series_checkpoint_refs`` is the
        # route's own live capture) because publication now requires it: the master-complete
        # refusal must be the reason this refuses, not a missing argument.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _set_master(contract, status="Completed", row_status="Completed")
            published: list[bool] = []

            with self.assertRaises(CloseoutQueueError) as raised:
                publish_series_checkpoint_under_authority(
                    contract,
                    lambda: published.append(True),
                    capture_series_checkpoint_refs(contract),
                )

            self.assertEqual(raised.exception.status, "atomic-series-checkpoint-master-complete")
            self.assertEqual(published, [])

    def test_checkpoint_publishes_for_a_master_that_is_not_completed(self) -> None:
        # The fixture's master is ``inProgress`` with one unresolved sub-task row and NO
        # ``task_root/enclosures`` directory at all, so this also proves the checkpoint route
        # consults neither completion blockers nor the leaf-enclosure census: the final route
        # refuses this very contract for exactly those reasons.

        # The candidate is the route's own live capture, which is what makes this a real
        # publication rather than a call that skipped revalidation.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            self.assertFalse((contract.task_root / "enclosures").exists())
            with self.assertRaises(CloseoutQueueError) as final:
                publish_series_integration_under_authority(contract, lambda: "never")
            self.assertEqual(final.exception.status, "atomic-series-closeout-master-incomplete")

            published: list[str] = []

            def publish() -> str:
                published.append("called")
                return "published"

            result = publish_series_checkpoint_under_authority(
                contract, publish, capture_series_checkpoint_refs(contract)
            )

            self.assertEqual(published, ["called"])
            self.assertEqual(result, "published")

    def test_publication_refuses_a_candidate_that_moved_after_its_capture(self) -> None:
        # The preflight captures the live refs once; publication re-reads them and refuses when
        # they are no longer the ones it admitted. Without this the route would land a pair the
        # preview never showed, which is the one thing a preview/apply pair must never do.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            stale = SeriesCheckpointRefs(code_commit=_ANY_COMMIT)
            published: list[str] = []

            with self.assertRaises(CloseoutQueueError) as raised:
                publish_series_checkpoint_under_authority(
                    contract, lambda: published.append("called"), stale
                )

            self.assertEqual(raised.exception.status, "atomic-series-checkpoint-candidate-moved")
            self.assertEqual(published, [])


class SeriesAbandonGuardTests(unittest.TestCase):
    def test_abandon_guard_refuses_a_checkpointed_master(self) -> None:
        # ``checkpointed`` is the state a partial landing leaves behind: the line is already
        # upstream, so abandoning the master as a whole would assert none of its work was taken.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _set_master(contract, status="abandoned", row_status="planning")
            checkpointed = amend_contract(
                contract, ContractCells(integration_status="checkpointed")
            )
            write_contract(contract.contract_path, checkpointed)

            with self.assertRaises(RuntimeError) as raised:
                require_terminal_worktree(checkpointed, operation="worktree_abandon")

            self.assertIn("already landed work into its source branch", str(raised.exception))

    def test_abandon_guard_still_refuses_a_completed_master(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _set_master(contract, status="abandoned", row_status="planning")
            completed = amend_contract(contract, ContractCells(integration_status="completed"))
            write_contract(contract.contract_path, completed)

            with self.assertRaises(RuntimeError) as raised:
                require_terminal_worktree(completed, operation="worktree_abandon")

            self.assertIn("already landed work into its source branch", str(raised.exception))

    def test_abandon_guard_allows_a_never_integrated_master(self) -> None:
        # ``not-started`` means nothing was taken, so wholesale abandonment is still honest and
        # must keep passing the whole terminal guard, branch spelling included.
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _set_master(contract, status="abandoned", row_status="planning")
            self.assertEqual(
                load_contract(contract.contract_path).integration_status, "not-started"
            )

            require_terminal_worktree(contract, operation="worktree_abandon")


if __name__ == "__main__":
    unittest.main()
