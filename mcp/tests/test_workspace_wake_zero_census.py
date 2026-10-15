"""L166-bound checks (L81-R1-F08/F09 added evidence, MIK-R81 rule 3 unchanged).

A WORKSPACE-only wake must read zero closeout queues and run no full census -- while
genuine task/worktree changes still invalidate and scheduled healing still verifies.
No new finding ID: these are the two checks the round-3 health/census binding names.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from agents_remember.serving.projections.snapshots_impl import _closeout_queue as queue_module
from agents_remember.serving.projections.snapshots_impl._closeout_queue import (
    refresh_closeout_queues,
)
from test_closeout_queue import MASTER_A, QueueFixture


class WorkspaceOnlyWakeTests(unittest.TestCase):
    def test_workspace_only_wake_reads_no_closeout(self) -> None:
        """A WORKSPACE-only scoped wake performs zero closeout IO (F09/L166).

        From populated caches, a change sample naming only workspace files (the
        workspace row plus an atomic temp sibling) must neither walk the task tree,
        nor re-list documents, nor run any per-sprint live fingerprint, nor
        recompute any queue -- while returning the retained queues identical.
        """
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            queues, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            self.assertTrue(queues, "the fixture sprint must yield a queue")
            health = fixture.coord / "logs" / "observer" / "workspace" / "probe.json"
            health.parent.mkdir(parents=True, exist_ok=True)
            health.write_text('{"tick": 1}\n', encoding="utf-8")
            temp_sibling = health.parent / (health.name + ".tmp.12345")
            temp_sibling.write_text("partial\n", encoding="utf-8")
            try:
                with (
                    mock.patch.object(
                        queue_module, "_walk_files", wraps=queue_module._walk_files
                    ) as walked,
                    mock.patch.object(
                        queue_module,
                        "_iter_task_document_payloads",
                        wraps=queue_module._iter_task_document_payloads,
                    ) as relisted,
                    mock.patch.object(
                        queue_module,
                        "sprint_live_fingerprint",
                        wraps=queue_module.sprint_live_fingerprint,
                    ) as fingerprinted,
                    mock.patch.object(
                        queue_module,
                        "_snapshot_entries",
                        wraps=queue_module._snapshot_entries,
                    ) as snapshotted,
                    mock.patch.object(
                        queue_module,
                        "_project_queue",
                        side_effect=AssertionError("recomputed"),
                    ),
                ):
                    retained, _ = refresh_closeout_queues(
                        fixture.coord,
                        now=now,
                        cache=cache,
                        paths=(health.as_posix(), temp_sibling.as_posix()),
                    )
                self.assertEqual(walked.call_count, 0, "no task-tree walk")
                self.assertEqual(relisted.call_count, 0, "no document re-list")
                self.assertEqual(fingerprinted.call_count, 0, "no live fingerprint")
                self.assertEqual(snapshotted.call_count, 0, "no worktree snapshot")
            finally:
                temp_sibling.unlink(missing_ok=True)
            self.assertEqual(len(retained), len(queues))
            for first, second in zip(queues, retained, strict=True):
                self.assertIs(second, first)

    def test_unscoped_and_live_touches_still_verify(self) -> None:
        """Healing is preserved: heartbeat/full/incomplete and live touches verify."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp))
            fixture.declare(MASTER_A)
            now = datetime.now(UTC)
            _, cache = refresh_closeout_queues(fixture.coord, now=now, cache=None)
            health = fixture.coord / "logs" / "observer" / "workspace" / "probe.json"
            health.parent.mkdir(parents=True, exist_ok=True)
            health.write_text("{}\n", encoding="utf-8")
            try:
                for paths in (
                    (),
                    None,
                    (health.as_posix(), fixture.code.as_posix()),
                ):
                    with mock.patch.object(
                        queue_module,
                        "sprint_live_fingerprint",
                        wraps=queue_module.sprint_live_fingerprint,
                    ) as fingerprinted:
                        refresh_closeout_queues(fixture.coord, now=now, cache=cache, paths=paths)
                    self.assertGreater(
                        fingerprinted.call_count,
                        0,
                        f"paths={paths!r} must still verify live inputs",
                    )
            finally:
                health.unlink(missing_ok=True)
