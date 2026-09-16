"""The whole leaf-and-master lifecycle, played through in order on real temporary Git worlds.

Every other end-to-end module proves one interaction at one boundary. This module plays the
lifecycle as a sequence, because that is the only way to catch the defect class the sequence
produces: an operation that is correct on its own, and leaves the world in a state the *next*
operation cannot accept. The seal that locked a master on its own first landing is exactly that
shape, and no single-boundary case could have seen it.

The run, in order:

1. the master is open, unlanded and holds no activation selection;
2. a leaf is commanded and started on the master's current line;
3. that leaf is worked, committed and closed out;
4. the leaf lands -- the PUBLIC ``worktree_integrate`` -- onto the master's work branch;
5. the unfinished master publishes its accumulated line -- the PUBLIC
   ``worktree_checkpoint_landing`` -- and stays open;
6. the master is stopped -- the PUBLIC ``worktree_pause`` -- and publishes nothing;
7. the master is resumed by the ordinary attach route;
8. a leaf commanded *after* that landing still starts, on the line the master now has.

Step 8 is the regression this module exists for: while the seal read the integration cell as
closeout, a master could never admit another leaf after its first landing, so a paused master
was a dead master. Steps 6 and 7 are the pause/resume pair the developer asked to see played.

The public tools are driven wherever a public route exists. Leaf start and leaf closeout use
this fixture's structural equivalents (``QueueFixture.start_leaf``, which is what
``worktree_start`` records, and the same closeout recording the checkpoint module uses), because
the registered start route needs the harness lifecycle and provider scaffolding that a
coordination-root fixture deliberately does not carry; the master-level interactions -- status,
integrate, checkpoint, pause, resume -- are the registered tools themselves.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from checkpoint_landing_test_support import close_out_leaf
from test_closeout_queue import REPO, QueueFixture
from test_worktree_support import git

pytestmark = pytest.mark.integration


def _snapshot(repository: Path) -> dict[str, str]:
    """Every ref in one repository, so a publication is a visible difference."""

    return dict(
        line.split(" ", 1)
        for line in git(
            repository, "for-each-ref", "--format=%(refname) %(objectname)"
        ).splitlines()
    )


class LifecyclePlaythroughTests(unittest.TestCase):
    """One real temporary world, walked from an unstarted master to a resumed one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(self.root, atomic_b=True, memory_mode="external")
        self.series = load_contract(self.fixture.tasks / "master-b" / "series-contract.md")
        assert self.series.kind == "series"
        self.cfg = self.fixture.cfg

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # -- the public operations, exactly as the registered tools call them -------------------

    def _status(self, contract: WorktreeContract) -> dict[str, Any]:
        return worktree_tools.worktree_status_tool(
            self.cfg,
            TaskRef(repo_id=REPO, contract_path=contract.contract_path.as_posix()),
        )

    def _integrate(self, contract: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
        return worktree_tools.worktree_integrate_tool(
            self.cfg,
            contract_path=contract.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=dry_run,
        )

    def _checkpoint(self, contract: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
        return worktree_tools.worktree_checkpoint_landing_tool(
            self.cfg,
            contract_path=contract.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=dry_run,
        )

    def _pause(self, contract: WorktreeContract) -> dict[str, Any]:
        return worktree_tools.worktree_pause_tool(
            self.cfg,
            contract_path=contract.contract_path.as_posix(),
        )

    def _attach(self, contract: WorktreeContract) -> dict[str, Any]:
        return worktree_tools.worktree_attach_tool(
            self.cfg,
            TaskRef(repo_id=REPO, contract_path=contract.contract_path.as_posix()),
        )

    def _reload(self) -> WorktreeContract:
        return load_contract(self.series.contract_path)

    # -- the playthrough --------------------------------------------------------------------

    def test_the_lifecycle_plays_through_from_an_unstarted_master_to_a_resumed_one(self) -> None:
        series = self._reload()

        # 1. An open master, nothing landed, nothing selected.
        opened = self._status(series)
        self.assertEqual(opened["integration_status"], "not-started")
        self.assertEqual(opened["atomicSeriesActivation"]["state"], "vacant")

        # 2. A leaf is commanded, and 3. started on the master's current line.
        self.fixture.author_unstarted_leaf("master-b", "LEAF-C")
        leaf = self.fixture.start_leaf("LEAF-C", master="master-b")
        self.assertEqual(leaf.kind, "leaf")
        self.assertTrue(leaf.code_worktree.exists())

        # 4. The leaf is worked and closed out, then lands through the PUBLIC integrate.
        closed = close_out_leaf(leaf)
        self.assertEqual(closed.closeout_status, "completed")
        landed = self._integrate(closed, dry_run=False)
        self.assertTrue(landed["ok"], landed.get("summary"))
        self.assertEqual(self._reload().integration_status, "not-started")

        # 5. The unfinished master publishes its accumulated line and stays open.
        preview = self._checkpoint(series, dry_run=True)
        self.assertEqual(preview["state"], "would-checkpoint")
        checkpointed = self._checkpoint(series, dry_run=False)
        self.assertTrue(checkpointed["ok"], checkpointed.get("summary"))
        self.assertEqual(checkpointed["state"], "checkpointed")
        after_landing = self._reload()
        self.assertEqual(after_landing.integration_status, "checkpointed")
        self.assertEqual(after_landing.closeout_status, "not-started")

        # 6. The master is stopped, and the stop publishes nothing.
        # This master's memory is external, so its repository is never absent; assert it rather
        # than trust it, because "publishes nothing" is measured on both repositories.
        memory_repository = after_landing.memory_repo_path
        assert memory_repository is not None
        code_before = _snapshot(after_landing.code_repo_path)
        memory_before = _snapshot(memory_repository)
        paused = self._pause(after_landing)
        self.assertTrue(paused["ok"], paused.get("summary"))
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["atomicSeriesActivation"]["state"], "vacant")
        self.assertNotIn("nextTool", paused)
        self.assertEqual(_snapshot(after_landing.code_repo_path), code_before)
        self.assertEqual(_snapshot(memory_repository), memory_before)

        # 7. The master resumes through the ordinary attach route.
        resumed = self._attach(after_landing)
        self.assertTrue(resumed["ok"], resumed.get("summary"))
        self.assertEqual(_snapshot(after_landing.code_repo_path), code_before)

        # 8. A leaf commanded AFTER the landing still starts: the master is open, not sealed.
        self.fixture.author_unstarted_leaf("master-b", "LEAF-D")
        second = self.fixture.start_leaf("LEAF-D", master="master-b")
        self.assertEqual(second.leaf_id, "LEAF-D")
        self.assertTrue(second.code_worktree.exists())
        self.assertNotEqual(second.code_base_commit, closed.code_base_commit)
