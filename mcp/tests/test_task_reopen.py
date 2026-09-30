from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.tasks import (
    ReviewState,
    TaskDocument,
    read_task_doc,
    write_task_doc,
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
    write_contract,
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

    def test_a_leaf_naming_no_master_resets_the_row_its_folder_master_lists(self) -> None:
        """MIK-R38: reopen resolves an unnamed leaf's master by the master sync's rule."""
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            _leaf_doc(contract.task_root, master=None)
            master_path = _master_doc(contract.task_root)

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0, result.payload)
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"], "reset"
            )
            self.assertEqual(read_task_doc(master_path).subTasks[0].status, "planning")

    def test_a_light_leaf_that_is_the_folder_task_json_is_not_its_own_master(self) -> None:
        """MIK-R38: only a ``subTask`` has a folder master, so a light ``task.json`` has none.

        The old fallback resolved this document to itself and refused it as "not a master". The
        reset planner is exercised directly because ``reopen_task``'s integration-branch preflight
        already refuses a leaf contract whose folder has no master, before this planner runs.
        """
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            light = TaskDocument.model_validate(
                {
                    "id": "260698-L1",
                    "slug": "task",
                    "title": "L1 — Demo leaf",
                    "kind": "light",
                    "status": "Completed",
                    "repo": "repo-a",
                    "createdAt": "2026-07-01T10:00",
                    "lifecycleId": "LC-OLD",
                    "steps": [{"id": "S1", "title": "do the thing", "status": "done"}],
                }
            )
            doc_path, _ = write_task_doc(contract.task_root, light)
            self.assertEqual(doc_path.name, "task.json")

            planned = reopen_module._plan_master_index_reset(contract, doc_path, light)

            self.assertEqual(planned, (None, "no-master"))

    def test_a_leaf_the_store_would_write_elsewhere_is_refused_before_any_write(self) -> None:
        """Review R1 finding 1: a hand-made ``light`` leaf would be written over ``task.json``.

        ``01_demo-leaf.json`` below is a ``light`` document listed by the folder master. The store
        writes a ``light`` document as ``task.json``, so resetting it would replace the master.
        """
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            master_path = _master_doc(contract.task_root)
            light = TaskDocument.model_validate(
                {
                    "id": "260698-L1",
                    "slug": "01_demo-leaf",
                    "title": "L1 — Demo leaf",
                    "kind": "light",
                    "status": "Completed",
                    "repo": "repo-a",
                    "createdAt": "2026-07-01T10:00",
                    "lifecycleId": "LC-OLD",
                    "steps": [{"id": "S1", "title": "do the thing", "status": "done"}],
                }
            )
            doc_path = contract.task_root / "01_demo-leaf.json"
            doc_path.write_text(light.model_dump_json(by_alias=True), encoding="utf-8")
            paths = (contract.contract_path, doc_path, master_path, master_path.with_suffix(".md"))
            before = {path: path.read_bytes() for path in paths}

            result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            blockers = cast("list[str]", result.payload["blockers"])
            self.assertIn(f"would be rewritten to {master_path}", blockers[0])
            self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_a_named_master_the_store_would_write_elsewhere_is_refused(self) -> None:
        """Parent guard: a hand-made master ``other.json`` is written as the folder's ``task.json``.

        Resetting its row would replace the series master, so reopen refuses before any write. A
        master at ``task.json`` reopens as ``test_resets_contract_doc_and_master_index`` shows.
        """
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            series_path = _master_doc(contract.task_root)
            data = read_task_doc(series_path).model_dump(by_alias=True)
            data.update(id="260698_OTHER", slug="other")
            other_path = contract.task_root / "other.json"
            other_path.write_text(
                TaskDocument.model_validate(data).model_dump_json(by_alias=True), encoding="utf-8"
            )
            doc_path = _leaf_doc(contract.task_root, master="other.md")
            paths = (
                contract.contract_path,
                doc_path,
                other_path,
                series_path,
                series_path.with_suffix(".md"),
            )
            before = {path: path.read_bytes() for path in paths}

            result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            blockers = cast("list[str]", result.payload["blockers"])
            self.assertIn(
                f"{other_path.resolve()} would be rewritten to {series_path.resolve()}", blockers[0]
            )
            self.assertEqual({path: path.read_bytes() for path in paths}, before)


class SeriesReopenTests(unittest.TestCase):
    """D-49: a terminal atomic series is reopened by tooling, as one journaled CAS-guarded move.

    Before this, ``_reopen_blockers`` returned at its leaf-only kind gate before any other check, so
    ``task_reopen`` could never act on a series while ``worktree_attach`` told the reader to run it
    (D-48). The only way to start a repair leaf in a completed master was to hand-edit the contract,
    which is the transition this case exists to make impossible to need.
    """

    def test_a_terminal_series_is_reopened_without_ever_moving_a_live_ref(self) -> None:
        """One case, four facts, because they are one promise told from both of its arrivals.

        A live ref is never moved, the reset is otherwise complete, a series that is *already* live
        at a collected address is re-addressed instead of refused, and the review counter the
        completion spent is cleared. Splitting them would have cost budget slots the lanes did not
        have, and none of them stands alone: the ref rule and the reset are the two halves of "a
        reopen never moves an existing ref"; the live arrival is the same publication entered
        without a reset, which is why it must leave the branch exactly where it is; and the counter
        is the same reset applied to the document instead of the contract (D-58).
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
            advanced = commit_file(
                contract.code_repo_path, "late.txt", "late\n", "Advance the line"
            )
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

        # The other way to arrive (D-58) is the same publication entered without a reset.
        with tempfile.TemporaryDirectory() as tmp:
            self._assert_a_live_unaddressed_series_is_re_addressed(Path(tmp))

        # And the state the first attempt of that publication leaves behind.
        with tempfile.TemporaryDirectory() as tmp:
            self._assert_an_interrupted_series_reset_is_resumed(Path(tmp))

    def _assert_an_interrupted_series_reset_is_resumed(self, workspace: Path) -> None:
        """The reset is durable on disk while the locator is still the collected generation.

        That is the state a publication which was refused or interrupted leaves behind, and the
        state the 260915-KS master itself was found in. A resume must finish the transition rather
        than refuse the branches the series is standing on, which is the whole difference between
        an interrupted reopen being recoverable and being stranded. A plain method for the same
        reason as its sibling: a second collected subject would have cost a slot no lane had.
        """

        contract = _completed_series_contract(workspace)
        git(contract.code_repo_path, "branch", contract.code_work_branch)
        git(contract.code_repo_path, "checkout", "-q", contract.code_work_branch)
        landed = commit_file(contract.code_repo_path, "landed.txt", "landed\n", "Land the work")
        git(contract.code_repo_path, "checkout", "-q", contract.code_source_branch)
        write_contract(contract.contract_path, reopen_module._reopened_contract(contract))
        self.assertEqual(load_contract(contract.contract_path).cleanup, "reopened")

        resumed = reopen_task(contract.contract_path)

        self.assertEqual((resumed.returncode, resumed.payload["state"]), (0, "reopened"))
        self.assertEqual(resumed.payload["mode"], "publish")
        self.assertEqual(
            branch_commit(contract.code_repo_path, contract.code_work_branch),
            landed,
            "a resumed reopen finishes the publication without moving the branch",
        )
        resumed_location = resolve_lifecycle_operation_location(
            contract.coordination_root, contract.contract_path
        )
        self.assertEqual(resumed_location.locator.publicationKind, "successor-enclosure")
        self.assertIsNone(resumed_location.locator.terminalArchivePath)

    def _assert_a_live_unaddressed_series_is_re_addressed(self, workspace: Path) -> None:
        """D-58: the series is ALREADY live, and only its enclosure generation is missing.

        Reopened by hand before this route existed, or by a reopen whose successor publication
        never ran, the series is live on disk and its integration branch carries the series' own
        landed work. Nothing here is a reset -- there is nothing terminal to cut -- so the same
        call must recognise the state, leave the branch exactly where the work put it, publish the
        successor generation, and clear the review counter the completion spent. It is a plain
        method because the case above is the one collected subject; a second collected subject
        would have cost a budget slot neither lane had.
        """

        contract = _completed_series_contract(workspace)
        git(contract.code_repo_path, "branch", contract.code_work_branch)
        git(contract.code_repo_path, "checkout", "-q", contract.code_work_branch)
        landed = commit_file(contract.code_repo_path, "landed.txt", "landed\n", "Land the work")
        git(contract.code_repo_path, "checkout", "-q", contract.code_source_branch)
        terminal = inspect_lifecycle_operation_locator(
            contract.coordination_root, contract.contract_path
        )
        archived = terminal.locator
        assert archived is not None and archived.publicationRequestId
        # The state the hand reopen left: live, every progress cell untouched, and an approval
        # note that belonged to the completion rather than to the landing still to come.
        write_contract(
            contract.contract_path,
            replace(
                contract,
                cleanup="pending",
                closeout_status="not-started",
                integration_status="not-started",
                code_commit="",
                integrated_code_commit="",
                commit_approval_note="the completion's own approval note",
                lifecycle_id="",
            ),
        )
        # A completion that spent three review rounds, so the counter has something to clear.
        master_path = contract.task_root / "task.json"
        write_task_doc(
            contract.task_root,
            read_task_doc(master_path).model_copy(
                update={"status": "Completed", "reviewState": ReviewState(round=3)}
            ),
        )

        result = reopen_task(contract.contract_path)

        self.assertEqual((result.returncode, result.payload["state"]), (0, "reopened"))
        self.assertEqual(result.payload["mode"], "publish")
        self.assertEqual(
            [
                recut["action"]
                for recut in cast("list[dict[str, object]]", result.payload["seriesRefs"])
            ],
            ["advance"],
        )
        self.assertEqual(
            branch_commit(contract.code_repo_path, contract.code_work_branch),
            landed,
            "a series that is already live keeps the work it landed",
        )
        re_addressed = load_contract(contract.contract_path)
        self.assertEqual(re_addressed.cleanup, "pending")
        self.assertNotIn(re_addressed.cleanup, TERMINAL_SERIES_CLEANUP)
        location = resolve_lifecycle_operation_location(
            contract.coordination_root, contract.contract_path
        )
        self.assertEqual(location.locator.publicationKind, "successor-enclosure")
        successor_predecessor = location.locator.predecessorTerminal
        assert successor_predecessor is not None, "a successor must cite its predecessor"
        self.assertEqual(
            successor_predecessor.publicationRequestId,
            archived.publicationRequestId,
        )
        self.assertEqual(location.manifest.auditIntent, SERIES_REOPEN_AUDIT_INTENT)
        self.assertIsNone(location.locator.terminalArchivePath)
        reattached = series_attach_result(re_addressed)
        self.assertEqual((reattached.returncode, reattached.payload["state"]), (0, "attached"))
        # The reopen clears the counter the completion spent, so the next round is the first.
        cleared = read_task_doc(master_path)
        self.assertEqual(cleared.status, "inProgress")
        assert cleared.reviewState is not None
        self.assertEqual(
            (
                cleared.reviewState.round,
                cleared.reviewState.pending,
                cleared.reviewState.additionalRounds,
                cleared.reviewState.developerApproval,
                cleared.reviewState.baselineFindings,
                cleared.reviewState.remainingFindingIds,
            ),
            (0, False, 0, None, [], []),
        )


if __name__ == "__main__":
    unittest.main()
