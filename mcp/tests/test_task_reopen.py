from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.tasks import (
    read_task_doc,
)
from agents_remember.worktrees import reopen as reopen_module
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
    resolve_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.git import branch_commit, branch_exists
from agents_remember.worktrees.modules.startup.series_attach import series_attach_result
from agents_remember.worktrees.reopen import SERIES_REOPEN_AUDIT_INTENT, reopen_task
from agents_remember.worktrees.scheduling_mode import TERMINAL_SERIES_CLEANUP
from agents_remember.worktrees.worktree_contract import (
    load_contract,
)
from task_reopen_test_support import (
    _completed_leaf_contract,
    _completed_series_contract,
    _leaf_doc,
    _master_doc,
)
from test_worktree_support import commit_file, git


class ReopenResetTests(unittest.TestCase):
    def test_resets_contract_doc_and_master_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "reopened")
            self.assertEqual(result.payload["nextOperation"], "start_reopened_task")
            self.assertEqual(result.payload["nextTool"], "worktree_start")
            next_step = cast("dict[str, object]", result.payload["nextStep"])
            self.assertEqual(next_step["nextTool"], "worktree_start")
            self.assertEqual(next_step["nextArgs"], result.payload["nextArgs"])

            reopened = load_contract(contract.contract_path)
            self.assertEqual(
                (
                    reopened.human_review_status,
                    reopened.approved_for_commit,
                    reopened.closeout_status,
                    reopened.integration_status,
                    reopened.cleanup,
                    reopened.lifecycle_id,
                    reopened.code_commit,
                    reopened.integrated_code_commit,
                ),
                ("pending-review", False, "not-started", "not-started", "reopened", "", "", ""),
            )
            self.assertEqual(reopened.leaf_id, "260698-L1")

            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("planning", None))
            self.assertTrue(any("reopened" in d.decision for d in doc.decisions))

            master = read_task_doc(master_path)
            self.assertEqual(master.subTasks[0].status, "planning")
            self.assertEqual(master.status, "inProgress")
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"], "reset"
            )

    def test_contract_publish_failure_rolls_back_docs_and_landing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)
            landing = contract.contract_path.parent / "landing-final.json"
            landing.write_text('{"finished": true}\n', encoding="utf-8")
            paths = (
                contract.contract_path,
                doc_path,
                doc_path.with_suffix(".md"),
                master_path,
                master_path.with_suffix(".md"),
                landing,
            )
            before = {path: path.read_bytes() for path in paths}

            with mock.patch.object(
                reopen_module, "write_contract", side_effect=OSError("contract locked")
            ):
                result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            self.assertIn("contract locked", str(result.payload["summary"]))
            self.assertEqual({path: path.read_bytes() for path in paths}, before)


class SeriesReopenTests(unittest.TestCase):
    """D-49: a terminal atomic series is reopened by tooling, as one journaled CAS-guarded move.

    Before this, ``_reopen_blockers`` returned at its leaf-only kind gate before any other check, so
    ``task_reopen`` could never act on a series while ``worktree_attach`` told the reader to run it
    (D-48). The only way to start a repair leaf in a completed master was to hand-edit the contract,
    which is the transition this case exists to make impossible to need.
    """

    def test_a_terminal_series_is_reopened_without_ever_moving_a_live_ref(self) -> None:
        """One case, two facts: a live ref is never moved, and the reset is otherwise complete.

        They are one subject because they are the two halves of the same promise -- the reopen
        re-cuts only the ref the series' own cleanup retired, and it performs the whole sanctioned
        transition (contract cells, integration ref, master document, enclosure generation) when
        that ref is genuinely absent -- and splitting them would have cost a budget slot the
        integration lane did not have.
        """

        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_series_contract(Path(tmp))
            self.assertEqual(contract.cleanup, "completed")
            self.assertFalse(
                branch_exists(contract.code_repo_path, contract.code_work_branch),
                "cleanup must have retired the integration branch for this fixture to be real",
            )
            terminal = inspect_lifecycle_operation_locator(
                contract.coordination_root, contract.contract_path
            )
            self.assertEqual(terminal.state, "terminal-archived")
            archived = terminal.locator
            assert archived is not None and archived.publicationRequestId

            # A line that has advanced past its source is refused, and nothing at all is written.
            git(contract.code_repo_path, "branch", contract.code_work_branch)
            git(contract.code_repo_path, "checkout", "-q", contract.code_work_branch)
            advanced = commit_file(contract.code_repo_path, "late.txt", "late\n", "Advance the line")
            git(contract.code_repo_path, "checkout", "-q", contract.code_source_branch)
            before = contract.contract_path.read_bytes()

            refused = reopen_task(contract.contract_path)

            self.assertEqual((refused.returncode, refused.payload["state"]), (2, "blocked"))
            self.assertIn(
                "never moves an existing ref",
                " ".join(cast("list[str]", refused.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), before)
            self.assertEqual(load_contract(contract.contract_path).cleanup, "completed")
            self.assertEqual(
                branch_commit(contract.code_repo_path, contract.code_work_branch), advanced
            )
            # Restore the state the series' own cleanup left, so the reopen has its real premise.
            git(contract.code_repo_path, "branch", "-D", contract.code_work_branch)

            result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (0, "reopened"))
            reopened = load_contract(contract.contract_path)
            self.assertEqual(
                (
                    reopened.cleanup,
                    reopened.closeout_status,
                    reopened.integration_status,
                    reopened.human_review_status,
                    reopened.approved_for_commit,
                    reopened.lifecycle_id,
                    reopened.code_commit,
                    reopened.integrated_code_commit,
                ),
                ("pending", "not-started", "not-started", "pending-review", False, "", "", ""),
            )
            self.assertNotIn(reopened.cleanup, TERMINAL_SERIES_CLEANUP)
            # The integration ref the cleanup retired is re-cut at the recorded source tip, and the
            # transition is durable: a second call refuses because the series is live again.
            self.assertTrue(branch_exists(contract.code_repo_path, contract.code_work_branch))
            self.assertEqual(
                branch_commit(contract.code_repo_path, contract.code_work_branch),
                branch_commit(contract.code_repo_path, reopened.code_source_branch),
            )
            second = reopen_task(contract.contract_path)
            self.assertEqual(second.returncode, 2)
            self.assertIn(
                "not a terminal series state",
                " ".join(cast("list[str]", second.payload["blockers"])),
            )

            # The enclosure root, manifest and journal are re-published as the successor of the
            # archived generation, which is what tells the archive this transition was sanctioned
            # rather than tampering with a collected record.
            location = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertEqual(location.locator.publicationKind, "successor-enclosure")
            predecessor = location.locator.predecessorTerminal
            assert predecessor is not None, "a successor generation must cite its predecessor"
            self.assertEqual(predecessor.publicationRequestId, archived.publicationRequestId)
            self.assertEqual(location.manifest.auditIntent, SERIES_REOPEN_AUDIT_INTENT)
            self.assertIsNone(
                location.locator.terminalArchivePath,
                "the successor generation is addressable, not terminal",
            )
            # The series is attachable again, which is the remedy series_attach_result names.
            attached = series_attach_result(reopened)
            self.assertEqual((attached.returncode, attached.payload["state"]), (0, "attached"))
            # And the master document leaves Completed, with the reopen as its audit record.
            master = read_task_doc(contract.task_root / "task.json")
            self.assertEqual(master.status, "inProgress")
            self.assertTrue(any("reopened" in d.decision for d in master.decisions))


if __name__ == "__main__":
    unittest.main()
