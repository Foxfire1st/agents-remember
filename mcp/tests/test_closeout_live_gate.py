"""MIK-R81 rules 2+3 (F09/F13): the closeout gate watches code/memory/source-tip inputs.

Each sprint recomputes only when its own inputs move -- a changed live fingerprint
(worktree heads, index stamps, file stats, source branch tips) or changed document
inputs; quiet passes retain every queue object-identical, invalid-empty included,
with no re-list, no re-validation and no recompute of unrelated queues.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from agents_remember.serving.projections.snapshots_impl import _closeout_queue as queue_module
from agents_remember.serving.projections.snapshots_impl._closeout_queue import (
    read_closeout_queues,
    refresh_closeout_queues,
    sprint_live_fingerprint,
    tasks_tree_digest,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from test_closeout_queue import MASTER_A, QueueFixture
from test_worktree_support import git


class CloseoutLiveGateTests(unittest.TestCase):
    def test_quiet_pass_retains_every_queue_without_recompute(self) -> None:
        """Two identical reads: the same objects, zero projection work."""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            self.assertTrue(queues, "the fixture sprint must yield a queue")
            with mock.patch.object(
                queue_module, "_project_queue", side_effect=AssertionError("recomputed")
            ):
                retained, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            self.assertEqual(len(retained), len(queues))
            for first, second in zip(queues, retained, strict=True):
                self.assertIs(second, first)
                self.assertEqual(second.model_dump(), first.model_dump(), "retained byte for byte")

    def test_unwatched_worktree_edits_arrive_on_the_next_heartbeat(self) -> None:
        """Unseen input freshness stays heartbeat-bounded, including a second dirty edit."""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None, paths=())
            edited = self._write_code_file(fixture)
            for tick in (15, 30):
                edited.write_text(f"dirty at {tick}\n", encoding="utf-8")
                when = now + timedelta(seconds=tick)
                arrived, cache = refresh_closeout_queues(
                    fixture.coord, now=when, cache=cache, paths=()
                )
                self.assertIsNot(arrived[0], queues[0])
                self.assertEqual(
                    arrived[0].model_dump(),
                    read_closeout_queues(fixture.coord, now=when)[0].model_dump(),
                )
                queues = arrived

    def test_trust_flagged_edits_recompute_on_the_next_heartbeat(self) -> None:
        """An empty native status cannot vouch for a trust-flagged tracked file (R1-03)."""

        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                fixture = QueueFixture(Path(tmp))
                fixture.declare(MASTER_A)
                edited = self._write_code_file(fixture)
                worktree = edited.parent
                git(worktree, "add", "-A")
                git(worktree, "commit", "-m", "tracked heartbeat input")
                git(worktree, "update-index", flag, edited.name)
                index = Path(
                    git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index")
                )
                original_index = index.read_bytes()
                before = worktree_candidate_tree(worktree, Path(tmp) / "before.index")
                now = datetime.now(UTC)
                queues, cache = refresh_closeout_queues(
                    fixture.coord, now=now, cache=None, paths=()
                )
                for tick in (15, 30):
                    edited.write_text(f"hidden eligible edit at {tick}\n", encoding="utf-8")
                    self.assertEqual(
                        git(worktree, "--no-optional-locks", "status", "--porcelain=v2"), ""
                    )
                    after = worktree_candidate_tree(worktree, Path(tmp) / "after.index")
                    self.assertNotEqual(after, before, "fresh capture observes the hidden edit")
                    when = now + timedelta(seconds=tick)
                    arrived, cache = refresh_closeout_queues(
                        fixture.coord, now=when, cache=cache, paths=()
                    )
                    self.assertIsNot(arrived[0], queues[0], "hidden input must recompute")
                    self.assertEqual(
                        arrived[0].model_dump(),
                        read_closeout_queues(fixture.coord, now=when)[0].model_dump(),
                    )
                    self.assertIsNone(queue_module._worktree_live_parts(worktree, "code"))
                    self.assertEqual(index.read_bytes(), original_index)
                    before, queues = after, arrived

    def test_a_missed_watched_change_arrives_at_the_five_minute_refresh(self) -> None:
        """A lost task-file notification is healed by 300 seconds, without aging worktrees."""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None, paths=())
            original = cache.tasks_digest
            probe = fixture.tasks / "sprint" / "task.json"
            payload = json.loads(probe.read_text())
            payload["title"] = "changed without a watcher notification"
            probe.write_text(json.dumps(payload), encoding="utf-8")
            _, cache = refresh_closeout_queues(
                fixture.coord, now=now + timedelta(seconds=15), cache=cache, paths=()
            )
            self.assertEqual(cache.tasks_digest, original)
            when = now + timedelta(seconds=300)
            arrived, cache = refresh_closeout_queues(fixture.coord, now=when, cache=cache, paths=())
            self.assertNotEqual(cache.tasks_digest, original)
            self.assertIsNot(arrived[0], queues[0], "the missed document change is delivered")
            self.assertEqual(
                [row.model_dump() for row in arrived],
                [row.model_dump() for row in read_closeout_queues(fixture.coord, now=when)],
            )

    def test_tasks_tree_digest_moves_on_input_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            tasks = fixture.coord / "tasks"
            before = tasks_tree_digest(tasks)
            self.assertIsNotNone(before)
            probe = fixture.tasks / "sprint" / "new-leaf.json"
            probe.write_text("note\n", encoding="utf-8")
            try:
                self.assertNotEqual(tasks_tree_digest(tasks), before)
            finally:
                probe.unlink()

    def test_code_edit_recomputes_only_its_own_sprint(self) -> None:
        """An edit under one sprint's code worktree recomputes it; the other stays put."""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            self._add_idle_sprint(fixture)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            self.assertEqual(len(queues), 2)
            by_path = {queue.sprintRef.path: queue for queue in queues}
            live_before = sprint_live_fingerprint(
                fixture.coord, by_path["sprint/task.json"].sprintRef, cache.overrides
            )
            self.assertIsNotNone(live_before)
            edited = self._write_code_file(fixture)
            live_after = sprint_live_fingerprint(
                fixture.coord, by_path["sprint/task.json"].sprintRef, cache.overrides
            )
            self.assertNotEqual(live_after, live_before)
            with mock.patch.object(
                queue_module, "_project_queue", wraps=queue_module._project_queue
            ) as projected:
                reread, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            self.assertEqual(projected.call_count, 1, "only the touched sprint recomputes")
            reread_by_path = {queue.sprintRef.path: queue for queue in reread}
            self.assertIsNot(reread_by_path["sprint/task.json"], by_path["sprint/task.json"])
            self.assertIs(reread_by_path["sprint-idle/task.json"], by_path["sprint-idle/task.json"])
            fresh = read_closeout_queues(fixture.coord, now=now)
            fresh_by_path = {queue.sprintRef.path: queue for queue in fresh}
            for path, queue in reread_by_path.items():
                self.assertEqual(
                    queue.model_dump(),
                    fresh_by_path[path].model_dump(),
                    f"retained-or-recomputed {path} matches a fresh effective read",
                )
            edited.unlink()

    def test_sweep_compaction_tick_touches_no_task_file(self) -> None:
        """MIK-R81 rule 2 (F09): a write outside tasks/ re-lists nothing.

        The sweep's compaction lands under workspace/, so the tasks-tree digest is
        unchanged and the refresh reuses the retained overrides with no re-list, no
        re-validation and no recompute -- every queue identical.
        """
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            self.assertTrue(queues)
            sweep_note = fixture.coord / "observer" / "workspace" / "sweep-note.json"
            sweep_note.parent.mkdir(parents=True, exist_ok=True)
            sweep_note.write_text('{"compacted": true}\n', encoding="utf-8")
            try:
                with (
                    mock.patch.object(
                        queue_module,
                        "_iter_task_document_payloads",
                        side_effect=AssertionError("re-listed"),
                    ),
                    mock.patch.object(
                        queue_module,
                        "_project_queue",
                        side_effect=AssertionError("recomputed"),
                    ),
                ):
                    retained, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            finally:
                sweep_note.unlink()
            self.assertEqual(len(retained), len(queues))
            for first, second in zip(queues, retained, strict=True):
                self.assertIs(second, first)

    def test_stat_touch_without_content_change_relists_but_does_not_recompute(
        self,
    ) -> None:
        """An mtime-only touch re-lists and re-validates, then retains every queue."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            doc_path = next(
                fixture.coord / "tasks" / ref.repository / ref.path
                for ref in cache.overrides
                if ref.path == "sprint/task.json"
            )
            os.utime(doc_path, None)
            with (
                mock.patch.object(
                    queue_module,
                    "_iter_task_document_payloads",
                    wraps=queue_module._iter_task_document_payloads,
                ) as relisted,
                mock.patch.object(
                    queue_module,
                    "_project_queue",
                    side_effect=AssertionError("recomputed"),
                ),
            ):
                retained, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            self.assertEqual(relisted.call_count, 1, "the moved digest re-lists once")
            self.assertEqual(len(retained), len(queues))
            for first, second in zip(queues, retained, strict=True):
                self.assertIs(second, first)

    def test_pinned_mtime_rewrite_still_recomputes_through_ctime(self) -> None:
        """A same-size rewrite with pinned mtime moves ctime: the digest must flip."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            doc_path = next(
                fixture.coord / "tasks" / ref.repository / ref.path
                for ref in cache.overrides
                if ref.path == "sprint/task.json"
            )
            stamp = doc_path.stat()
            raw = doc_path.read_bytes()
            self.assertIn(b'"title":', raw)
            # Same-length title swap, then pin mtime back: size+mtime identical.
            title_at = raw.index(b'"title": "') + len(b'"title": "')
            end_at = raw.index(b'"', title_at)
            patched = raw[:title_at] + b"X" * (end_at - title_at) + raw[end_at:]
            self.assertEqual(len(patched), len(raw))
            doc_path.write_bytes(patched)
            os.utime(doc_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            pinned = doc_path.stat()
            self.assertEqual(pinned.st_size, stamp.st_size)
            self.assertEqual(pinned.st_mtime_ns, stamp.st_mtime_ns)
            with mock.patch.object(
                queue_module, "_project_queue", wraps=queue_module._project_queue
            ) as projected:
                reread, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            self.assertGreater(
                projected.call_count,
                0,
                "the ctime-moved digest recomputes the touched sprint",
            )
            reread_by_path = {queue.sprintRef.path: queue for queue in reread}
            self.assertIsNot(
                reread_by_path["sprint/task.json"],
                {queue.sprintRef.path: queue for queue in queues}["sprint/task.json"],
            )

    def test_recomputed_queues_converge_on_the_next_quiet_pass(self) -> None:
        """A doc-content change recomputes once, then the next pass retains (no flip)."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            doc_path = next(
                fixture.coord / "tasks" / ref.repository / ref.path
                for ref in cache.overrides
                if ref.path == "sprint/task.json"
            )
            payload = json.loads(doc_path.read_text(encoding="utf-8"))
            payload["title"] = payload["title"] + " (edited)"
            doc_path.write_text(json.dumps(payload), encoding="utf-8")
            changed, changed_cache = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            self.assertEqual(len(changed), len(queues))
            with mock.patch.object(
                queue_module,
                "_project_queue",
                side_effect=AssertionError("recomputed"),
            ):
                converged, _ = refresh_closeout_queues(fixture.coord, now=now, cache=changed_cache)
            for first, second in zip(changed, converged, strict=True):
                self.assertIs(second, first)

    def test_source_tip_advance_recomputes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            sprint_ref = queues[0].sprintRef
            before = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
            self.assertIsNotNone(before)
            contract = fixture.contracts[MASTER_A]
            (contract.code_repo_path / "tip-advance.txt").write_text("tip\n", encoding="utf-8")
            git(contract.code_repo_path, "add", "-A")
            git(contract.code_repo_path, "commit", "-m", "advance the source line")
            git(
                contract.code_repo_path,
                "branch",
                "-f",
                contract.code_source_branch,
                "HEAD",
            )
            try:
                after = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
                self.assertNotEqual(after, before)
                reread, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
                self.assertIsNot(reread[0], queues[0])
            finally:
                git(contract.code_repo_path, "rm", "-q", "tip-advance.txt")
                git(contract.code_repo_path, "commit", "-q", "-m", "restore the source line")

    def test_an_unmoved_sprint_reads_its_census_once(self) -> None:
        """Stat first: unchanged documents and contracts are not read again (F13, D569)."""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            _, cache = refresh_closeout_queues(fixture.coord, now=datetime.now(UTC), cache=None)
            sprint_ref = cache.queues[0].sprintRef
            _settled(fixture.root)
            with mock.patch.object(
                queue_module, "_task_census", wraps=queue_module._task_census
            ) as census:
                first = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
                second = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
            self.assertIsNotNone(first)
            self.assertEqual(second, first)
            self.assertEqual(census.call_count, 1)

    def test_every_moved_input_recounts_and_a_code_edit_still_recomputes(self) -> None:
        """The gate is not a stub: a moved document or contract reads the census again, and an
        edit under the worktree still moves the fingerprint and recomputes its sprint."""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            sprint_ref = cache.queues[0].sprintRef
            census = queue_module._task_census(fixture.coord, sprint_ref, [], cache.overrides)
            leaf_document = census.leafs[0][0].path
            contract = fixture.contracts[MASTER_A].contract_path
            for moved in (leaf_document, contract):
                with self.subTest(moved=moved.name):
                    _settled(fixture.root)
                    held = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
                    moved.write_bytes(moved.read_bytes())  # the same bytes, a new write
                    with mock.patch.object(
                        queue_module, "_task_census", wraps=queue_module._task_census
                    ) as recount:
                        again = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
                    self.assertEqual(recount.call_count, 1, "a moved input reads the census again")
                    self.assertEqual(again, held)
            _settled(fixture.root)
            held = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
            self._write_code_file(fixture)
            with mock.patch.object(
                queue_module, "_task_census", wraps=queue_module._task_census
            ) as unread:
                edited = sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
            self.assertEqual(unread.call_count, 0, "the documents did not move")
            self.assertIsNotNone(edited)
            self.assertNotEqual(edited, held, "the held census still reads the live inputs")
            with mock.patch.object(
                queue_module, "_project_queue", wraps=queue_module._project_queue
            ) as projected:
                reread, _ = refresh_closeout_queues(fixture.coord, now=now, cache=cache)
            self.assertEqual(projected.call_count, 1, "the edited sprint recomputes")
            self.assertIsNot(reread[0], queues[0])

    def test_an_input_written_just_before_its_read_is_never_held(self) -> None:
        """A write that may land while the census reads leaves nothing held (racy, as Git's).

        Building the fixture takes seconds, so its files are older than the racy window by the
        time they are read; the window is widened instead, making every input a recent write.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            _, cache = refresh_closeout_queues(fixture.coord, now=datetime.now(UTC), cache=None)
            sprint_ref = cache.queues[0].sprintRef
            queue_module._held_door_sources.clear()
            with (
                mock.patch.object(queue_module, "_RACY_NS", 10**18),
                mock.patch.object(
                    queue_module, "_task_census", wraps=queue_module._task_census
                ) as census,
            ):
                sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
                sprint_live_fingerprint(fixture.coord, sprint_ref, cache.overrides)
            self.assertEqual(census.call_count, 2)

    def _write_code_file(self, fixture: QueueFixture) -> Path:
        """An unstaged edit under the sprint's code worktree (real dirs or fresh init)."""

        contract = fixture.contracts[MASTER_A]
        worktree = contract.code_worktree
        assert worktree is not None, "the fixture contract must name a code worktree"
        if not (worktree / ".git").exists():
            worktree.mkdir(parents=True, exist_ok=True)
            git(worktree, "init", "-b", "main")
            git(worktree, "commit", "--allow-empty", "-m", "seed")
        edited = worktree / "live-edit.txt"
        edited.write_text("an unstaged code edit\n", encoding="utf-8")
        return edited

    def _add_idle_sprint(self, fixture: QueueFixture) -> None:
        """A second, disjoint sprint with docs only: its fingerprint never moves here."""

        tasks = (
            fixture.coord
            / "tasks"
            / fixture.contracts[MASTER_A].task_artifact.parts[
                fixture.contracts[MASTER_A].task_artifact.parts.index("tasks") + 1
            ]
        )
        leaf_dir = tasks / "leaf-idle"
        write_task_doc(
            leaf_dir,
            TaskDocument.model_validate(
                {
                    "id": "LEAF-IDLE",
                    "slug": "leaf-idle",
                    "title": "Idle leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": tasks.name,
                    "createdAt": "2026-10-13T00:00:00Z",
                    "master": "master-idle/task.json",
                    "steps": [{"id": "S1", "title": "idle", "status": "pending"}],
                }
            ),
        )
        master_dir = tasks / "master-idle"
        write_task_doc(
            master_dir,
            TaskDocument.model_validate(
                {
                    "id": "MASTER-IDLE",
                    "slug": "master-idle",
                    "title": "Idle master",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": tasks.name,
                    "createdAt": "2026-10-13T00:00:00Z",
                    "executionNature": "atomic",
                    "subTasks": [
                        {
                            "number": "LEAF-IDLE",
                            "name": "LEAF-IDLE",
                            "file": "leaf-idle.md",
                            "status": "inProgress",
                        }
                    ],
                }
            ),
        )
        sprint_dir = tasks / "sprint-idle"
        write_task_doc(
            sprint_dir,
            TaskDocument.model_validate(
                {
                    "id": "SPRINT-IDLE",
                    "slug": "sprint-idle",
                    "title": "Idle sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": tasks.name,
                    "createdAt": "2026-10-13T00:00:00Z",
                    "orchestrates": ["master-idle"],
                }
            ),
        )


def _settled(root: Path) -> None:
    """Every fixture file last written well before the next read (past the racy margin)."""

    settled = time.time() - 10
    for directory, _dirs, files in os.walk(root):
        for name in (*files, ""):
            os.utime(Path(directory) / name, (settled, settled), follow_symlinks=False)
