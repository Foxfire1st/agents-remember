"""The pause stops a master and publishes nothing, on real temporary Git repositories.

Every other pause case is a unit proof of one seam; this module is the boundary proof. It
drives the PUBLIC operation (``worktree_pause_tool``, the registered ``worktree_pause`` tool)
over real temporary Git repositories holding two atomic masters on one source pair, and
measures the world before and after: both repositories' refs and complete object databases,
the coordination tree, both worktrees, the enclosure, and every task document with its leaf
rows and statuses.

The claim under test is a negative one -- that pausing publishes nothing -- so the fixture
measures rather than asserts it: the exact branch tips, the full object set of each
repository, and the byte digests of everything the pause could have touched. A ref move, a
new commit object, a landing, a ledger row or a leaf advance all show up as a difference.

Each case below fails independently: the no-publication measurement, the hand-back payload,
each refusal, leave-idempotence, per-contract record isolation, and resume are separate
operations, and merging any two of them would make a failure ambiguous. They share this
module's one fixture and its measurement helpers and nothing else.

``worktree_checkpoint_landing`` is the SEPARATE, explicitly requested publication. No case
here reaches it, and none may: a test that paused a master by landing it would be proving the
defect this split exists to remove.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.models.structural.atomic_series_activation import (
    AtomicSeriesActivationRecord,
)
from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.activation.atomic_series_activation import (
    activation_path,
    activation_waiting_reason,
    contract_fingerprint,
    observe_atomic_series,
    series_master_ref,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from test_closeout_queue import MASTER_A, MASTER_B, QueueFixture
from test_worktree_support import git

ACTIVATION_STORE = Path("controlplane") / "atomic-series-activation"
TAMPERED_AT = "2026-09-13T00:00:00+00:00"


def _tree_digest(root: Path | None) -> dict[str, str]:
    """Every tracked byte under one root, keyed by relative path. Git internals are separate."""

    if root is None or not root.exists():
        return {}
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        digests[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _git_state(repository: Path) -> dict[str, object]:
    """Every ref and every object in one repository's database."""

    refs = dict(
        line.split(" ", 1)
        for line in git(
            repository, "for-each-ref", "--format=%(refname) %(objectname)"
        ).splitlines()
    )
    objects = set(git(repository, "cat-file", "--batch-all-objects", "--batch-check").splitlines())
    return {"refs": refs, "objects": objects}


def _document_status(path: Path) -> str:
    return TaskDocument.model_validate_json(path.read_text(encoding="utf-8")).status


class PauseStopsAnAtomicMasterTests(unittest.TestCase):
    """One real temporary Git world per case, holding two atomic masters on one source pair."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(
            self.root,
            atomic_a=True,
            atomic_b=True,
            memory_mode="external",
        )
        # ``QueueFixture.contracts`` holds each master's LEAF enclosure; the atomic master's own
        # series contract is the canonical sibling at the master task root. Both masters are
        # atomic and both protected source branches are the sprint branch, so they share one
        # atomic-series source pair -- while each holds its own activation record, keyed to the
        # contract, which is what makes releasing one master leave the other's record alone.
        self.series_a = load_contract(self.fixture.tasks / "master-a" / "series-contract.md")
        self.series_b = load_contract(self.fixture.tasks / "master-b" / "series-contract.md")
        self.leaf_a = self.fixture.contracts[MASTER_A]
        self.leaf_b = self.fixture.contracts[MASTER_B]
        assert (self.series_a.kind, self.series_b.kind) == ("series", "series")
        assert (self.leaf_a.kind, self.leaf_b.kind) == ("leaf", "leaf")
        assert (
            self.series_a.code_source_branch,
            self.series_a.memory_source_branch,
        ) == (self.series_b.code_source_branch, self.series_b.memory_source_branch)
        assert self.series_b.memory_repo_path is not None

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # -- the public operations this module drives ------------------------------------------

    def _pause(self, series: WorktreeContract) -> dict[str, Any]:
        """Drive the PUBLIC pause, exactly as the registered tool does."""

        return worktree_tools.worktree_pause_tool(
            self.fixture.cfg,
            contract_path=series.contract_path.as_posix(),
        )

    def _select(self, series: WorktreeContract) -> dict[str, Any]:
        """Select one master through the public selecting route (no ref move when current)."""

        return worktree_tools.worktree_sync_tool(
            self.fixture.cfg,
            contract_path=series.contract_path.as_posix(),
            dry_run=False,
        )

    # -- the measurement --------------------------------------------------------------------

    def _activation_bytes(self) -> dict[str, bytes]:
        store = self.fixture.coord / ACTIVATION_STORE
        return {path.name: path.read_bytes() for path in sorted(store.glob("*.json"))}

    def _record_bytes(self, series: WorktreeContract) -> bytes:
        """This master's own activation record, read from its own contract-addressed path."""

        path = activation_path(series.coordination_root, series)
        self.assertTrue(path.is_file(), path)
        return path.read_bytes()

    def _world(self) -> dict[str, object]:
        """Everything a publication would leave a mark on, measured rather than assumed.

        The activation store is the pause's one legitimate write and is measured separately, so
        excluding it here is what makes "the pause touched nothing else" a real assertion.
        """

        assert self.series_b.memory_repo_path is not None
        return {
            "code_git": _git_state(self.series_b.code_repo_path),
            "memory_git": _git_state(self.series_b.memory_repo_path),
            "coordination": {
                path: digest
                for path, digest in _tree_digest(self.fixture.coord).items()
                if not path.startswith(ACTIVATION_STORE.as_posix())
            },
            "code_worktree": _tree_digest(self.leaf_b.code_worktree),
            "memory_worktree": _tree_digest(self.leaf_b.memory_worktree),
        }

    def _tips(self) -> dict[str, str]:
        memory = self.series_b.memory_repo_path
        assert memory is not None
        series = self.series_b
        return {
            "code_work": git(series.code_repo_path, "rev-parse", series.code_work_branch),
            "code_source_destination": git(
                series.code_repo_path, "rev-parse", series.code_source_branch
            ),
            "memory_work": git(memory, "rev-parse", series.memory_work_branch),
            "memory_source_destination": git(memory, "rev-parse", series.memory_source_branch),
        }

    def _documents(self) -> dict[str, str]:
        """Every task document's status: masters, the sprint, and every unstarted leaf.

        The task tree also carries non-document authority JSON (the curator-coherence record),
        so the walk admits only objects that declare the task-document shape; their bytes are
        still covered by :meth:`_world`'s coordination digest.
        """

        statuses: dict[str, str] = {}
        for path in sorted(self.fixture.tasks.rglob("*.json")):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or "kind" not in loaded:
                continue
            statuses[path.relative_to(self.fixture.tasks).as_posix()] = TaskDocument.model_validate(
                loaded
            ).status
        return statuses

    # -- cases ------------------------------------------------------------------------------

    def test_pausing_a_master_moves_no_ref_and_creates_no_commit(self) -> None:
        """The measurement: same tips, same object databases, same bytes, no landing recorded."""

        self._select(self.series_b)
        before = self._world()
        tips_before = self._tips()
        documents_before = self._documents()
        contract_before = self.series_b.contract_path.read_bytes()

        result = self._pause(self.series_b)

        self.assertTrue(result["ok"], result)
        # Nothing moved: exact branch tips, both repositories' complete object databases, the
        # whole coordination tree, both worktrees and the enclosure are byte-identical.
        self.assertEqual(self._tips(), tips_before)
        self.assertEqual(self._world(), before)
        self.assertEqual(self._documents(), documents_before)
        self.assertEqual(self.series_b.contract_path.read_bytes(), contract_before)
        # The destination/sprint branch is untouched, and no landing was recorded anywhere.
        stored = load_contract(self.series_b.contract_path)
        self.assertEqual(stored.integration_status, "not-started")
        self.assertEqual(stored.closeout_status, "not-started")
        self.assertEqual(stored.cleanup, self.series_b.cleanup)
        # The work that was private stays private: the worktrees are still there, unlanded.
        self.assertTrue(self.leaf_b.code_worktree.exists())
        self.assertIsNotNone(self.leaf_b.memory_worktree)
        self.assertEqual(_document_status(self.series_b.task_root / "task.json"), "inProgress")
        # The unstarted leaf was not advanced: its status is the one it already had.
        self.assertEqual(
            _document_status(self.series_b.task_root / "leaf-b.json"),
            documents_before["master-b/leaf-b.json"],
        )

    def test_a_paused_master_hands_the_turn_back_with_no_next_call(self) -> None:
        """The stop's own report: it stopped, it published nothing, and it proposes nothing."""

        self._select(self.series_b)

        result = self._pause(self.series_b)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "paused")
        self.assertEqual(result["status"], "paused")
        self.assertIs(result["paused"], True)
        self.assertEqual(result["operation"], "worktree_pause")
        # Handed back, not pushed on: the result proposes no call and no continued execution.
        self.assertNotIn("nextTool", result)
        self.assertNotIn("nextArgs", result)
        self.assertNotIn("nextOperation", result)
        next_step = result["nextStep"]
        assert isinstance(next_step, dict)
        self.assertNotIn("nextTool", next_step)
        self.assertNotIn("nextArgs", next_step)
        summary = result["summary"]
        assert isinstance(summary, str)
        self.assertIn("Nothing was published", summary)
        # The release really happened, and the released state is the one this result reports.
        self.assertEqual(observe_atomic_series(self.series_b).state, "vacant")
        released = result["atomicSeriesActivation"]
        assert isinstance(released, dict)
        self.assertEqual(released["state"], "vacant")

    def test_pausing_a_master_that_was_never_selected_succeeds_and_writes_nothing(self) -> None:
        """No selection means the master is already stopped, and the pause says so.

        A master between landings holds no selection, so this is the ordinary state of a master
        the developer wants parked rather than an error: the intent is already satisfied, and
        the pause reports it in its own state instead of failing. The success must still be
        inert -- no activation record, no coordination write, no ref move.
        """

        self.assertEqual(self._activation_bytes(), {})
        before = self._world()

        stopped = self._pause(self.series_b)

        self.assertTrue(stopped["ok"], stopped)
        self.assertEqual(stopped["state"], "atomic-series-already-vacant")
        self.assertIs(stopped["paused"], True)
        self.assertEqual(stopped["atomicSeriesActivation"]["state"], "vacant")
        self.assertNotIn("nextTool", stopped)
        # An already-stopped master is not a written one: nothing was created to say so.
        self.assertEqual(self._activation_bytes(), {})
        self.assertEqual(self._world(), before)

    def test_pausing_an_already_released_master_is_idempotent(self) -> None:
        """A second pause reports the same stopped master and releases nothing new."""

        self._select(self.series_b)
        first = self._pause(self.series_b)
        self.assertTrue(first["ok"], first)
        self.assertEqual(observe_atomic_series(self.series_b).state, "vacant")
        released_record = self._record_bytes(self.series_b)

        second = self._pause(self.series_b)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["state"], "paused")
        self.assertEqual(observe_atomic_series(self.series_b).state, "vacant")
        self.assertEqual(self._record_bytes(self.series_b), released_record)

    def test_pausing_a_leaf_contract_is_refused(self) -> None:
        """The stop addresses a master's series contract, never an ordinary leaf's enclosure.

        A leaf owns no atomic-series selection of its own -- its parent master's selection is
        what exposes its work -- so pausing one must refuse rather than release the master's
        selection on a leaf's behalf or invent a record for the leaf.
        """

        self._select(self.series_b)
        self.assertEqual(observe_atomic_series(self.series_b).state, "active")
        selections = self._activation_bytes()
        world = self._world()

        refused = self._pause(self.leaf_b)

        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["state"], "pause-requires-atomic-master")
        self.assertIs(refused["paused"], False)
        detail = refused["detail"]
        assert isinstance(detail, str)
        self.assertIn("series contract", detail)
        # Nothing was written for the leaf, the master's own selection is untouched, and the
        # world is exactly as it was.
        self.assertEqual(self._activation_bytes(), selections)
        self.assertEqual(observe_atomic_series(self.series_b).state, "active")
        self.assertEqual(self._world(), world)

    def test_pausing_one_master_leaves_the_other_masters_record_byte_identical(self) -> None:
        """Two masters on one source pair, two records: the pause touches exactly one of them.

        The per-contract activation record is what makes this true. Both masters expose work at
        the same time, each in its own record; pausing A releases A's record to vacancy and
        leaves B's record byte-for-byte as it was, so B keeps exposing work and the retired
        cross-master waiting vocabulary has nothing left to say.
        """

        self._select(self.series_a)
        self._select(self.series_b)
        observed_a = observe_atomic_series(self.series_a)
        observed_b = observe_atomic_series(self.series_b)
        # The premise of the whole case: the two masters hold SEPARATE records, each naming the
        # contract that reads it -- so a record at one master's address can never be another's.
        self.assertNotEqual(observed_a.activation_path, observed_b.activation_path)
        assert observed_a.record is not None and observed_b.record is not None
        self.assertEqual(observed_a.record.selectedMaster, observed_a.selected_master)
        self.assertEqual(observed_b.record.selectedMaster, observed_b.selected_master)
        self.assertNotEqual(observed_a.record.selectedMaster, observed_b.record.selectedMaster)
        self.assertEqual((observed_a.state, observed_b.state), ("active", "active"))
        other_record = self._record_bytes(self.series_b)
        before = self._world()

        paused = self._pause(self.series_a)

        self.assertTrue(paused["ok"], paused)
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(observe_atomic_series(self.series_a).state, "vacant")
        # B's record is byte-identical, and B was never made ineligible by A's stop.
        self.assertEqual(self._record_bytes(self.series_b), other_record)
        self.assertEqual(observe_atomic_series(self.series_b).state, "active")
        self.assertEqual(self._world(), before)
        # The paused master holds no selection, and neither master is a waiting reason for the
        # other: the retired cross-master vocabulary is gone from the live read entirely.
        self.assertIsNone(activation_waiting_reason(observe_atomic_series(self.series_a)))
        self.assertIsNone(activation_waiting_reason(observe_atomic_series(self.series_b)))
        self.assertNotIn("paused-by", repr(paused))
        self.assertNotIn("not-selected", repr(paused))
        # B selects again through the public route, so pausing A changed nothing about B's work.
        selected_b = self._select(self.series_b)
        self.assertTrue(selected_b["ok"], selected_b)
        self.assertEqual(observe_atomic_series(self.series_b).state, "active")

    def test_a_record_this_contract_does_not_own_is_refused_not_released(self) -> None:
        """The release's identity guard still fires, and it fires before any release.

        On a per-contract record the cross-master refusal the old base produced cannot arise from
        a normal selection -- ``activation_path`` is a function of the contract path, so a record
        at this master's address is written only by this master. It IS still reachable under
        tampering, and the guard still refuses it: a record naming another contract reads as
        unreadable, and the pause refuses with the release's own status rather than repairing it.
        """

        self._select(self.series_b)
        foreign_path = activation_path(self.series_a.coordination_root, self.series_a)
        foreign_path.parent.mkdir(parents=True, exist_ok=True)
        foreign_path.write_text(
            json.dumps(
                AtomicSeriesActivationRecord(
                    contractFingerprint=contract_fingerprint(self.series_b),
                    selectedMaster=series_master_ref(self.series_b),
                    contractPath=self.series_b.contract_path.resolve().as_posix(),
                    state="active",
                    revision=7,
                    selectedAt=TAMPERED_AT,
                ).model_dump(mode="json")
            ),
            encoding="utf-8",
        )
        tampered = foreign_path.read_bytes()
        before = self._world()

        refused = self._pause(self.series_a)

        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["state"], "atomic-series-activation-release-unreadable")
        self.assertIs(refused["paused"], False)
        # Refused, not repaired and not released: the bytes and everything else are untouched.
        self.assertEqual(foreign_path.read_bytes(), tampered)
        self.assertEqual(self._world(), before)
        self.assertEqual(observe_atomic_series(self.series_b).state, "active")

    def test_resuming_a_paused_master_restores_work_with_nothing_published(self) -> None:
        """The pause is reversible, and the interval between stop and resume publishes nothing."""

        self._select(self.series_b)
        before = self._world()
        tips_before = self._tips()
        contract_before = self.series_b.contract_path.read_bytes()

        paused = self._pause(self.series_b)
        self.assertTrue(paused["ok"], paused)
        self.assertEqual(observe_atomic_series(self.series_b).state, "vacant")

        resumed = self._select(self.series_b)

        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(observe_atomic_series(self.series_b).state, "active")
        # Stop and resume together published nothing: the same tips, the same object databases,
        # the same coordination tree and the same contract bytes as before the pause.
        self.assertEqual(self._tips(), tips_before)
        self.assertEqual(self._world(), before)
        self.assertEqual(self.series_b.contract_path.read_bytes(), contract_before)
        # The capability is intact on both sides: the pause still works after the resume.
        again = self._pause(self.series_b)
        self.assertTrue(again["ok"], again)
        self.assertEqual(observe_atomic_series(self.series_b).state, "vacant")


if __name__ == "__main__":
    unittest.main()
