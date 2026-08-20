"""Atomic child admission and pre-closeout landing completeness."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest import mock

from agents_remember.kernel.memory_ledger import (
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees import reopen as reopen_module
from agents_remember.worktrees import series_closeout
from agents_remember.worktrees.atomic_series_seal import require_series_accepting_leaves
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_terminal_worktree,
)
from agents_remember.worktrees.modules import abandon as abandon_module
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules.abandon import _abandon_directories, abandon_result
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    _removed_directories,
    _terminal_mutation_authority,
    cleanup_result,
)
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalMode,
    require_series_children_retired,
    terminal_preflight,
    terminal_result_blockers,
)
from agents_remember.worktrees.queue import closeout_queue_lifecycle as queue_lifecycle
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_closeout_queue import LEAF_B, MASTER_B, SPRINT, QueueFixture
from test_worktree_support import git


class AtomicSeriesSealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        self.series = load_contract(self.fixture.tasks / "master-b" / "series-contract.md")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_series_terminal_capability_requires_live_queue_publication(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "live queue-owned publication authority",
        ):
            _terminal_mutation_authority(self.series, operation="worktree_abandon")
        with self.assertRaisesRegex(
            RuntimeError,
            "live queue-owned publication authority",
        ):
            _terminal_mutation_authority(self.series, operation="worktree_cleanup")

    def test_series_terminal_capability_is_exact_and_cannot_escape_publication(self) -> None:
        escaped = []

        def capture(permit):
            escaped.append(permit)
            authority = _terminal_mutation_authority(
                self.series,
                operation="worktree_abandon",
                series_permit=permit,
            )
            self.assertEqual(authority.operation, "worktree_abandon")
            with self.assertRaisesRegex(RuntimeError, "live queue-owned"):
                _terminal_mutation_authority(
                    self.series,
                    operation="worktree_cleanup",
                    series_permit=permit,
                )
            moved = replace(
                self.series,
                contract_path=self.series.contract_path.with_name("copied-series-contract.md"),
            )
            with self.assertRaisesRegex(RuntimeError, "live queue-owned"):
                _terminal_mutation_authority(
                    moved,
                    operation="worktree_abandon",
                    series_permit=permit,
                )
            return authority

        with mock.patch.object(queue_lifecycle, "require_series_children_retired"):
            authority = queue_lifecycle.publish_atomic_series_terminal_under_authority(
                self.series,
                "worktree_abandon",
                capture,
            )
        self.assertEqual(authority.operation, "worktree_abandon")
        with self.assertRaisesRegex(RuntimeError, "live queue-owned"):
            _terminal_mutation_authority(
                self.series,
                operation="worktree_abandon",
                series_permit=escaped[0],
            )

    def test_series_terminal_capability_expires_when_publication_raises(self) -> None:
        escaped = []

        def fail(permit):
            escaped.append(permit)
            raise RuntimeError("publication failed")

        with (
            mock.patch.object(queue_lifecycle, "require_series_children_retired"),
            self.assertRaisesRegex(RuntimeError, "publication failed"),
        ):
            queue_lifecycle.publish_atomic_series_terminal_under_authority(
                self.series,
                "worktree_abandon",
                fail,
            )
        with self.assertRaisesRegex(RuntimeError, "live queue-owned"):
            _terminal_mutation_authority(
                self.series,
                operation="worktree_abandon",
                series_permit=escaped[0],
            )

    def test_series_terminal_capability_cannot_replay_from_copied_context(self) -> None:
        escaped = []

        def capture(permit):
            escaped.append(
                (
                    copy_context(),
                    permit,
                )
            )
            return _terminal_mutation_authority(
                self.series,
                operation="worktree_abandon",
                series_permit=permit,
            )

        with mock.patch.object(queue_lifecycle, "require_series_children_retired"):
            queue_lifecycle.publish_atomic_series_terminal_under_authority(
                self.series,
                "worktree_abandon",
                capture,
            )
        copied_context, permit = escaped[0]

        def replay():
            return _terminal_mutation_authority(
                self.series,
                operation="worktree_abandon",
                series_permit=permit,
            )

        with self.assertRaisesRegex(RuntimeError, "live queue-owned"):
            copied_context.run(replay)

    def _land_leaf(self):
        leaf = self.fixture.contracts[MASTER_B]
        git(leaf.code_worktree, "add", "-A")
        git(leaf.code_worktree, "commit", "-m", "Land atomic leaf code")
        code_commit = git(leaf.code_worktree, "rev-parse", "HEAD")
        assert leaf.memory_worktree is not None
        assert leaf.ledger_path is not None
        git(leaf.memory_worktree, "add", "-A")
        git(leaf.memory_worktree, "commit", "-m", "Land atomic leaf memory")
        memory_content = git(leaf.memory_worktree, "rev-parse", "HEAD")
        ledger = prepend_mapping(
            load_ledger(leaf.ledger_path),
            code_commit,
            memory_content,
        )
        write_ledger(leaf.ledger_path, ledger)
        git(leaf.memory_worktree, "add", "memory.md")
        git(leaf.memory_worktree, "commit", "-m", "Map atomic leaf landing")
        ledger_commit = git(leaf.memory_worktree, "rev-parse", "HEAD")
        git(self.fixture.code, "branch", "-f", self.series.code_work_branch, code_commit)
        git(self.fixture.memory, "branch", "-f", self.series.memory_work_branch, ledger_commit)
        landed = replace(
            leaf,
            closeout_status="completed",
            code_commit=code_commit,
            memory_content_commit=memory_content,
            ledger_commit=ledger_commit,
            integration_status="completed",
            integrated_code_commit=code_commit,
            integrated_memory_content_commit=memory_content,
            integrated_ledger_commit=ledger_commit,
            queue_sprint_task_document=SPRINT.key,
            queue_candidate_task_document=LEAF_B.key,
        )
        write_contract(landed.contract_path, landed)
        return landed

    @staticmethod
    def _direct_commit(repo: Path, branch: str) -> str:
        parent = git(repo, "rev-parse", branch)
        tree = git(repo, "rev-parse", f"{parent}^{{tree}}")
        commit = git(repo, "commit-tree", tree, "-p", parent, "-m", "Unjournaled ref edit")
        git(repo, "update-ref", f"refs/heads/{branch}", commit, parent)
        return commit

    def test_series_closeout_requires_every_declared_leaf_landed(self) -> None:
        with self.assertRaisesRegex(CloseoutQueueError, "has not landed"):
            series_closeout._require_every_atomic_leaf_landed(self.series)

        self._land_leaf()

        series_closeout._require_every_atomic_leaf_landed(self.series)

    def test_orphan_sibling_doc_is_not_atomic_membership_but_extra_enclosure_refuses(self) -> None:
        landed = self._land_leaf()
        leaf_doc = read_task_doc(self.series.task_root / "leaf-b.json")
        orphan = TaskDocument.model_validate(
            {
                **leaf_doc.model_dump(by_alias=True),
                "id": "ORPHAN",
                "slug": "orphan",
                "title": "Unadmitted orphan draft",
            }
        )
        write_task_doc(self.series.task_root, orphan)

        series_closeout._require_every_atomic_leaf_landed(self.series)

        copied_path = self.series.task_root / "enclosures" / "orphan" / "series-contract.md"
        copied_path.parent.mkdir(parents=True)
        copied_path.write_bytes(landed.contract_path.read_bytes())
        with self.assertRaisesRegex(CloseoutQueueError, "invalid or duplicate leaf enclosure"):
            series_closeout._require_every_atomic_leaf_landed(self.series)

    def test_foreign_contract_identity_cannot_satisfy_atomic_landing(self) -> None:
        landed = self._land_leaf()
        write_contract(landed.contract_path, replace(landed, repo_name="foreign"))

        with self.assertRaisesRegex(CloseoutQueueError, "has not landed"):
            series_closeout._require_every_atomic_leaf_landed(self.series)

    def test_clean_direct_commits_cannot_ride_the_atomic_leaf_chain(self) -> None:
        landed = self._land_leaf()
        self._direct_commit(self.fixture.code, self.series.code_work_branch)
        with self.assertRaisesRegex(CloseoutQueueError, "history outside"):
            series_closeout._require_every_atomic_leaf_landed(self.series)

        git(
            self.fixture.code,
            "update-ref",
            f"refs/heads/{self.series.code_work_branch}",
            landed.integrated_code_commit,
        )
        self._direct_commit(self.fixture.memory, self.series.memory_work_branch)
        with self.assertRaisesRegex(CloseoutQueueError, "history outside"):
            series_closeout._require_every_atomic_leaf_landed(self.series)

    def test_code_and_memory_leaf_order_must_be_one_exact_pair_chain(self) -> None:
        landed = self._land_leaf()
        second = replace(
            landed,
            leaf_id="LEAF-C",
            code_base_commit=landed.integrated_code_commit,
            memory_base_commit=self.series.memory_base_commit,
        )
        expected = {
            landed.leaf_id: LEAF_B,
            second.leaf_id: TaskDocumentRef(
                repository=LEAF_B.repository,
                path="master-b/leaf-c.json",
            ),
        }
        with self.assertRaisesRegex(CloseoutQueueError, "one exact code-and-memory"):
            series_closeout._require_exact_atomic_landing_chain(
                self.series,
                {landed.leaf_id: landed, second.leaf_id: second},
                expected,
                SPRINT,
            )

    def test_mapped_memory_content_must_descend_from_the_prior_leaf_tip(self) -> None:
        landed = self._land_leaf()
        assert landed.memory_worktree is not None
        assert landed.ledger_path is not None
        content_tree = git(
            self.fixture.memory,
            "rev-parse",
            f"{landed.integrated_memory_content_commit}^{{tree}}",
        )
        foreign_content = git(
            self.fixture.memory,
            "commit-tree",
            content_tree,
            "-m",
            "Foreign memory content",
        )
        ledger_text = landed.ledger_path.read_text(encoding="utf-8").replace(
            landed.integrated_memory_content_commit,
            foreign_content,
        )
        landed.ledger_path.write_text(ledger_text, encoding="utf-8")
        git(landed.memory_worktree, "add", "memory.md")
        ledger_tree = git(landed.memory_worktree, "write-tree")
        forged_ledger = git(
            self.fixture.memory,
            "commit-tree",
            ledger_tree,
            "-p",
            landed.memory_base_commit,
            "-p",
            foreign_content,
            "-m",
            "Merge unrelated mapped memory",
        )
        git(landed.memory_worktree, "reset", "--hard", landed.integrated_ledger_commit)
        git(
            self.fixture.memory,
            "update-ref",
            f"refs/heads/{self.series.memory_work_branch}",
            forged_ledger,
        )
        forged = replace(
            landed,
            memory_content_commit=foreign_content,
            ledger_commit=forged_ledger,
            integrated_memory_content_commit=foreign_content,
            integrated_ledger_commit=forged_ledger,
        )
        write_contract(forged.contract_path, forged)

        with self.assertRaisesRegex(CloseoutQueueError, "memory-not-landed"):
            series_closeout._require_every_atomic_leaf_landed(self.series)

    def test_production_series_closeout_publication_requires_complete_landing_set(self) -> None:
        self.fixture.mutate(
            "acquire-blocker",
            blocker=MASTER_B,
            rationale="Seal the completed atomic block.",
        )
        master = read_task_doc(self.series.task_root / "task.json")
        completed = master.model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                ],
            }
        )
        write_task_doc(self.series.task_root, completed)
        publication = mock.Mock(return_value="published")

        with self.assertRaisesRegex(CloseoutQueueError, "has not landed"):
            series_closeout.publish_closeout_under_authority(self.series, publication)
        publication.assert_not_called()

        self._land_leaf()
        self.assertEqual(
            series_closeout.publish_closeout_under_authority(self.series, publication),
            "published",
        )
        publication.assert_called_once_with()

    def test_closed_series_refuses_new_candidate_and_blocker_admission(self) -> None:
        sealed = replace(self.series, closeout_status="completed")
        write_contract(sealed.contract_path, sealed)

        with self.assertRaisesRegex(RuntimeError, "sealed against new or reopened leaves"):
            require_series_accepting_leaves(sealed, operation="leaf start")
        with self.assertRaisesRegex(CloseoutQueueError, "sealed against new or reopened leaves"):
            self.fixture.declare(MASTER_B)
        with self.assertRaisesRegex(CloseoutQueueError, "sealed against new or reopened leaves"):
            self.fixture.mutate(
                "acquire-blocker",
                blocker=MASTER_B,
                rationale="Attempt to reopen a landed atomic block.",
            )

    def test_series_terminal_routes_preserve_live_undeclared_child_resources(self) -> None:
        leaf = self.fixture.contracts[MASTER_B]
        assert leaf.memory_worktree is not None
        master = read_task_doc(self.series.task_root / "task.json")
        write_task_doc(
            self.series.task_root,
            master.model_copy(
                update={
                    "status": "Completed",
                    "subTasks": [
                        row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                    ],
                }
            ),
        )
        before_files = {
            path.relative_to(leaf.contract_path.parent).as_posix(): path.read_bytes()
            for path in leaf.contract_path.parent.rglob("*")
            if path.is_file()
        }
        before = {
            "series-code": git(
                self.fixture.code,
                "rev-parse",
                f"refs/heads/{self.series.code_work_branch}",
            ),
            "series-memory": git(
                self.fixture.memory,
                "rev-parse",
                f"refs/heads/{self.series.memory_work_branch}",
            ),
            "leaf-code": git(
                self.fixture.code,
                "rev-parse",
                f"refs/heads/{leaf.code_work_branch}",
            ),
            "leaf-memory": git(
                self.fixture.memory,
                "rev-parse",
                f"refs/heads/{leaf.memory_work_branch}",
            ),
        }

        for force in (False, True):
            with (
                self.subTest(operation="abandon", force=force),
                self.assertRaisesRegex(RuntimeError, "every child leaf"),
            ):
                abandon_result(
                    WorktreeArgs(
                        contract_path=self.series.contract_path,
                        approved=True,
                        force=force,
                    )
                )
        with (
            self.subTest(operation="cleanup"),
            self.assertRaisesRegex(RuntimeError, "every child leaf"),
        ):
            cleanup_result(WorktreeArgs(contract_path=self.series.contract_path, approved=True))

        self.assertEqual(
            {
                "series-code": git(
                    self.fixture.code,
                    "rev-parse",
                    f"refs/heads/{self.series.code_work_branch}",
                ),
                "series-memory": git(
                    self.fixture.memory,
                    "rev-parse",
                    f"refs/heads/{self.series.memory_work_branch}",
                ),
                "leaf-code": git(
                    self.fixture.code,
                    "rev-parse",
                    f"refs/heads/{leaf.code_work_branch}",
                ),
                "leaf-memory": git(
                    self.fixture.memory,
                    "rev-parse",
                    f"refs/heads/{leaf.memory_work_branch}",
                ),
            },
            before,
        )
        self.assertTrue(leaf.code_worktree.is_dir())
        self.assertTrue(leaf.memory_worktree.is_dir())
        self.assertEqual(
            {
                path.relative_to(leaf.contract_path.parent).as_posix(): path.read_bytes()
                for path in leaf.contract_path.parent.rglob("*")
                if path.is_file()
            },
            before_files,
        )

    def test_terminal_child_tombstone_is_not_a_live_resource(self) -> None:
        leaf = self.fixture.contracts[MASTER_B]
        assert leaf.memory_worktree is not None
        git(self.fixture.code, "worktree", "remove", "--force", str(leaf.code_worktree))
        git(self.fixture.code, "branch", "-D", leaf.code_work_branch)
        git(self.fixture.memory, "worktree", "remove", "--force", str(leaf.memory_worktree))
        git(self.fixture.memory, "branch", "-D", leaf.memory_work_branch)
        write_contract(leaf.contract_path, replace(leaf, cleanup="completed"))

        require_series_children_retired(self.series)

    def test_series_directory_cleanup_preserves_terminal_child_evidence(self) -> None:
        leaf = self.fixture.contracts[MASTER_B]
        evidence_before = leaf.contract_path.read_bytes()
        removers = (
            lambda: _abandon_directories(self.series, dry_run=False, force=True),
            lambda: _removed_directories(self.series, dry_run=False),
        )
        for index, remove in enumerate(removers):
            reports = self.series.worktree_group / "reports"
            self.assertEqual(
                reports,
                self.series.coordination_root
                / "worktrees"
                / self.series.repo_name
                / "master-b-ar"
                / "reports",
            )
            reports.mkdir(parents=True, exist_ok=True)
            (reports / f"series-{index}.txt").write_text("series-owned\n", encoding="utf-8")

            with self.subTest(remover=index):
                self.assertEqual(set(remove()), {"reports"})
                self.assertFalse(reports.exists())
                self.assertEqual(leaf.contract_path.read_bytes(), evidence_before)
                self.assertTrue(leaf.contract_path.parent.is_dir())

    def test_leaf_named_reports_remains_a_terminally_governed_child(self) -> None:
        fixture = QueueFixture(
            Path(self.temp.name) / "reserved-leaf",
            atomic_b=True,
            atomic_leaf_id="REPORTS",
        )
        series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
        leaf = fixture.contracts[MASTER_B]
        self.assertEqual(
            leaf.contract_path,
            series.task_root / "enclosures" / "reports" / "series-contract.md",
        )
        before = leaf.contract_path.read_bytes()

        for force in (False, True):
            with (
                self.subTest(force=force),
                self.assertRaisesRegex(RuntimeError, "every child leaf"),
            ):
                abandon_result(
                    WorktreeArgs(
                        contract_path=series.contract_path,
                        approved=True,
                        force=force,
                    )
                )
        self.assertTrue(leaf.code_worktree.is_dir())
        self.assertEqual(leaf.contract_path.read_bytes(), before)

        assert leaf.memory_worktree is not None
        git(fixture.code, "worktree", "remove", "--force", str(leaf.code_worktree))
        git(fixture.code, "branch", "-D", leaf.code_work_branch)
        git(fixture.memory, "worktree", "remove", "--force", str(leaf.memory_worktree))
        git(fixture.memory, "branch", "-D", leaf.memory_work_branch)
        write_contract(leaf.contract_path, replace(leaf, cleanup="completed"))
        terminal = leaf.contract_path.read_bytes()

        require_series_children_retired(series)
        for remove in (
            lambda: _abandon_directories(series, dry_run=False, force=True),
            lambda: _removed_directories(series, dry_run=False),
        ):
            result = remove()
            self.assertEqual(result["reports"]["reason"], "already-absent")
            self.assertEqual(
                terminal_result_blockers(
                    providers={},
                    worktrees={},
                    branches={},
                    directories=result,
                ),
                [],
            )
            self.assertEqual(leaf.contract_path.read_bytes(), terminal)

    def test_terminal_routes_recheck_children_under_queue_then_repository_authority(self) -> None:
        master = read_task_doc(self.series.task_root / "task.json")
        write_task_doc(
            self.series.task_root,
            master.model_copy(
                update={
                    "status": "Completed",
                    "subTasks": [
                        row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                    ],
                }
            ),
        )
        for module, state, mode, force in (
            (abandon_module, "abandon-blocked", "abandon", True),
            (cleanup_module, "blocked", "cleanup", False),
        ):
            with self.subTest(operation=mode):
                self._assert_terminal_race(
                    module,
                    state,
                    cast(TerminalMode, mode),
                    force,
                )

    def _assert_terminal_race(
        self,
        module: ModuleType,
        state: str,
        mode: TerminalMode,
        force: bool,
    ) -> None:
        real_inspect = queue_lifecycle.CloseoutQueueStore.inspect
        real_repository_lock = queue_lifecycle.integration_authority_lock
        flags = {"queue": False, "repository": False}
        calls = 0

        def tracked_inspect(store, initial, reader):
            def tracked_reader(queue_state):
                flags["queue"] = True
                try:
                    return reader(queue_state)
                finally:
                    flags["queue"] = False

            return real_inspect(store, initial, tracked_reader)

        @contextmanager
        def tracked_repository_lock(*args):
            with real_repository_lock(*args):
                flags["repository"] = True
                try:
                    yield
                finally:
                    flags["repository"] = False

        def child_census(_contract):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertFalse(flags["queue"] or flags["repository"])
                return
            self.assertTrue(flags["queue"] and flags["repository"])
            raise RuntimeError("child appeared before terminal publication")

        outputs = mock.Mock()
        guard = mock.Mock()
        guard.preview.return_value = {}
        args = WorktreeArgs(
            contract_path=self.series.contract_path,
            approved=True,
            force=force,
        )
        preflight = terminal_preflight(self.series, mode=mode, force=force)
        with (
            mock.patch.object(
                queue_lifecycle.CloseoutQueueStore,
                "inspect",
                new=tracked_inspect,
            ),
            mock.patch.object(
                queue_lifecycle,
                "integration_authority_lock",
                new=tracked_repository_lock,
            ),
            mock.patch.object(
                queue_lifecycle,
                "require_series_children_retired",
                side_effect=child_census,
            ),
            mock.patch.object(module, f"_{mode}_terminal_outputs", outputs),
        ):
            queue_lifecycle.require_atomic_series_terminal_release(self.series)
            require_terminal_worktree(self.series, operation=f"worktree_{mode}")
            result = getattr(module, f"_{mode}_with_guard")(
                args,
                self.series,
                preflight,
                guard,
            )

        self.assertEqual((result.returncode, result.payload["state"]), (2, state))
        self.assertIn("child appeared", result.payload["blockers"][0]["reason"])
        self.assertEqual(calls, 2)
        outputs.assert_not_called()

    def test_start_rechecks_parent_seal_inside_repository_authority(self) -> None:
        locked = False
        ensure = mock.Mock()

        @contextmanager
        def repository_authority(*_args):
            nonlocal locked
            locked = True
            try:
                yield
            finally:
                locked = False

        def sealed(*_args, **_kwargs):
            self.assertTrue(locked)
            raise RuntimeError("series sealed during start preflight")

        with (
            mock.patch.object(start_module, "_record_start_progress"),
            mock.patch.object(
                start_module,
                "integration_authority_lock",
                new=repository_authority,
            ),
            mock.patch.object(
                start_module,
                "require_parent_series_accepting_leaves",
                side_effect=sealed,
            ),
            mock.patch.object(start_module, "ensure_worktree", ensure),
            self.assertRaisesRegex(RuntimeError, "sealed during start preflight"),
        ):
            start_module._create_start_enclosure(
                mock.Mock(),
                self.fixture.contracts[MASTER_B],
                WorktreeArgs(dry_run=False),
            )

        ensure.assert_not_called()

    def test_reopen_rechecks_parent_seal_inside_queue_then_repository_publication(self) -> None:
        contract = replace(
            self.fixture.contracts[MASTER_B],
            queue_sprint_task_document=SPRINT.key,
            queue_candidate_task_document=LEAF_B.key,
            closeout_status="completed",
            integration_status="completed",
            cleanup="completed",
            code_worktree=self.fixture.root / "retired-code-worktree",
            memory_worktree=self.fixture.root / "retired-memory-worktree",
        )
        write_contract(contract.contract_path, contract)
        before = contract.contract_path.read_bytes()
        locked = False

        @contextmanager
        def repository_authority(*_args):
            nonlocal locked
            locked = True
            try:
                yield
            finally:
                locked = False

        def sealed(*_args, **_kwargs):
            self.assertTrue(locked)
            raise RuntimeError("series sealed during reopen publication")

        with (
            mock.patch.object(
                queue_lifecycle,
                "integration_authority_lock",
                new=repository_authority,
            ),
            mock.patch.object(
                reopen_module,
                "require_parent_series_accepting_leaves",
                side_effect=sealed,
            ),
            self.assertRaisesRegex(RuntimeError, "sealed during reopen publication"),
        ):
            reopen_module._publish_reopen_transition(
                contract,
                replace(contract, cleanup="reopened"),
                dry_run=False,
            )

        self.assertEqual(contract.contract_path.read_bytes(), before)
