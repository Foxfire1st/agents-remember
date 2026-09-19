"""The atomic landing chain orders a memory-only leaf by the pair, not by the code commit alone.

A leaf whose code leg is ``not-applicable`` lands a memory commit and records the code position it
stood on, so two leaves can share one code commit and still be two distinct landings: each memory
commit makes the pair unique. The chain therefore orders on the pair -- code ancestry where the code
position moved, memory ancestry where it did not -- and refuses only a pair that is equal on both
sides, which really is one landing recorded twice.

Before this case existed the ordering predicate returned "unordered" in *both* directions as soon as
two leaves shared a code commit, so no total order could be built and the master closeout refused
``atomic-series-leaf-chain-invalid``: a complete atomic master containing a memory-only leaf could
not be landed by any governed route. Found by this master's own promotion attempt (register row
``D52``); the twenty-four real contracts that exposed it are named in the leaf report.

The same leaf family has a second, surviving refusal (register row ``D-45``), pinned by the class
below: the spine's admissible positions were read from the series contract alone, so a base advanced
by a *leaf-level* sync was invisible and a step to it was refused even though the leaf's own contract
records that position as synced. The new case is paid for inside this module: the identical-pair case
now also carries the second fixture pair the module used to assert separately -- the property is over
the pair, not over either commit's value -- so the module's case count is unchanged by the addition.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_mode import MemoryMode
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.series_closeout import (
    _leaf_landing_precedes,
    _require_exact_atomic_landing_chain,
)
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path, branch: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.email", "chain@example.invalid")
    _git(path, "config", "user.name", "chain")


def _commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", body)
    return _git(repo, "rev-parse", "HEAD")


def _branch(repo: Path, name: str, commit: str) -> None:
    """Point a ref the checked-out branch does not own, so the series tip can be placed exactly."""

    _git(repo, "branch", "-f", name, commit)


@dataclass(frozen=True)
class _Side:
    """One side of a leaf's recorded pair: the base it started from and the commit it landed."""

    base: str
    landed: str


class AtomicSeriesChainPairOrderTests(unittest.TestCase):
    """One code commit, two memory commits: two landings, ordered by memory ancestry."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.code = self.root / "code"
        self.memory = self.root / "memory"
        _init_repo(self.code, "line")
        _init_repo(self.memory, "line")
        self.code_base = _commit(self.code, "a.txt", "code base")
        self.code_next = _commit(self.code, "b.txt", "code next")
        self.memory_first = _commit(self.memory, "m1.txt", "memory first")
        self.memory_second = _commit(self.memory, "m2.txt", "memory second")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _contract(
        self,
        *,
        code: str,
        memory: str,
        leaf_id: str,
        memory_mode: MemoryMode = "external",
    ) -> WorktreeContract:
        external = memory_mode == "external"
        return WorktreeContract(
            task_id="260915_TEST",
            task_name="chain-pair-order",
            repo_name="agents-remember",
            workflow_kind="light-task",
            memory_mode=memory_mode,
            coordination_root=self.root,
            task_root=self.root / "tasks",
            contract_path=self.root / "tasks" / f"{leaf_id}-contract.md",
            task_artifact=self.root / "tasks" / "task.md",
            worktree_group=self.root / "worktrees",
            code_repo_path=self.code,
            code_source_branch="line",
            code_work_branch=f"ar/{leaf_id}",
            code_base_commit=code,
            code_worktree=self.root / "worktrees" / leaf_id,
            memory_repo_path=self.memory if external else None,
            memory_source_branch="line",
            memory_work_branch=f"ar/{leaf_id}",
            memory_base_commit=memory,
            memory_worktree=self.root / "worktrees" / f"{leaf_id}-memory",
            integrated_code_commit=code,
            integrated_memory_content_commit=memory,
            leaf_id=leaf_id,
        )

    def test_a_shared_code_commit_with_ordered_memory_is_a_chain_step(self) -> None:
        """The memory-only leaf that landed second follows the leaf that landed first."""

        series = self._contract(code=self.code_next, memory=self.memory_first, leaf_id="L1")
        first = self._contract(code=self.code_next, memory=self.memory_first, leaf_id="L1")
        second = self._contract(code=self.code_next, memory=self.memory_second, leaf_id="L2")

        self.assertTrue(_leaf_landing_precedes(series, first, second))
        self.assertFalse(_leaf_landing_precedes(series, second, first))

    def test_code_ancestry_still_orders_when_the_code_position_moved(self) -> None:
        series = self._contract(code=self.code_base, memory=self.memory_first, leaf_id="L1")
        earlier = self._contract(code=self.code_base, memory=self.memory_first, leaf_id="L1")
        later = self._contract(code=self.code_next, memory=self.memory_second, leaf_id="L2")

        self.assertTrue(_leaf_landing_precedes(series, earlier, later))
        self.assertFalse(_leaf_landing_precedes(series, later, earlier))

    def test_an_identical_pair_is_still_one_landing_recorded_twice(self) -> None:
        """Equality on both sides stays unordered in both directions -- nothing is relaxed here.

        The property is over the pair, not over either commit's value, so both fixture pairs are
        asserted in this one case: the shared code commit with the second memory commit and with
        the first.
        """

        for memory in (self.memory_second, self.memory_first):
            series = self._contract(code=self.code_next, memory=memory, leaf_id="L1")
            one = self._contract(code=self.code_next, memory=memory, leaf_id="L1")
            two = self._contract(code=self.code_next, memory=memory, leaf_id="L2")

            self.assertFalse(_leaf_landing_precedes(series, one, two))
            self.assertFalse(_leaf_landing_precedes(series, two, one))

    def test_disabled_memory_keeps_the_code_only_rule(self) -> None:
        """With memory disabled there is no pair to order, so a shared code commit stays unordered."""

        disabled = self._contract(
            code=self.code_next,
            memory=self.memory_first,
            leaf_id="L1",
            memory_mode="disabled",
        )
        one = self._contract(
            code=self.code_next,
            memory=self.memory_first,
            leaf_id="L1",
            memory_mode="disabled",
        )
        two = self._contract(
            code=self.code_next,
            memory=self.memory_second,
            leaf_id="L2",
            memory_mode="disabled",
        )

        self.assertFalse(_leaf_landing_precedes(disabled, one, two))
        self.assertFalse(_leaf_landing_precedes(disabled, two, one))


class AtomicSeriesLeafSyncPositionTests(unittest.TestCase):
    """A leaf-level sync position is admissible; a position no contract recorded is not.

    A sync is journaled on the contract it ran for. When a leaf's own base was advanced -- the
    memory-only leaf whose base moved to its own landing, which is row ``D-45``'s case -- the entry
    is on the *leaf's* contract, and the series contract holds only its own syncs. Reading the
    series contract alone therefore refused a step to a base the leaf's own contract records as
    synced, and named a commit that is that leaf's recorded base *and* its landing.

    Both directions are pinned here, in one case, because they are one property -- the admissible
    positions are exactly what the chain's own contracts recorded. The recorded ``codeBaseTo`` and
    ``memoryBaseTo`` admit their steps; a commit that no contract's sync ever recorded is still
    refused as history beyond the leaf landing chain.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.code = self.root / "code"
        self.memory = self.root / "memory"
        _init_repo(self.code, "line")
        _init_repo(self.memory, "line")
        self.code_first = _commit(self.code, "a.txt", "code first")
        self.code_second = _commit(self.code, "b.txt", "code second")
        self.code_synced = _commit(self.code, "c.txt", "code synced")
        self.code_foreign = _commit(self.code, "d.txt", "code foreign")
        self.memory_base = _commit(self.memory, "m0.txt", "memory base")
        self.memory_first = _commit(self.memory, "m1.txt", "memory first")
        self.memory_synced = _commit(self.memory, "m2.txt", "memory synced")
        self.memory_second = _commit(self.memory, "m3.txt", "memory second")
        self.memory_third = _commit(self.memory, "m4.txt", "memory third")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _series(self) -> WorktreeContract:
        return WorktreeContract(
            task_id="260915_TEST",
            task_name="chain-leaf-sync",
            repo_name="agents-remember",
            workflow_kind="light-task",
            memory_mode="external",
            coordination_root=self.root,
            task_root=self.root / "tasks",
            contract_path=self.root / "tasks" / "series-contract.md",
            task_artifact=self.root / "tasks" / "task.md",
            worktree_group=self.root / "worktrees",
            code_repo_path=self.code,
            code_source_branch="line",
            code_work_branch="series",
            code_base_commit=self.code_first,
            code_worktree=self.root / "worktrees" / "series",
            memory_repo_path=self.memory,
            memory_source_branch="line",
            memory_work_branch="series",
            memory_base_commit=self.memory_base,
            memory_worktree=self.root / "worktrees" / "series-memory",
            kind="series",
        )

    def _leaf(
        self,
        series: WorktreeContract,
        leaf_id: str,
        *,
        code: _Side,
        memory: _Side,
        sync_log: tuple[dict[str, str], ...] = (),
    ) -> WorktreeContract:
        return WorktreeContract(
            task_id=series.task_id,
            task_name=series.task_name,
            repo_name=series.repo_name,
            workflow_kind=series.workflow_kind,
            memory_mode=series.memory_mode,
            coordination_root=series.coordination_root,
            task_root=series.task_root,
            contract_path=leaf_enclosure_path(series.task_root, leaf_id),
            task_artifact=series.task_artifact,
            worktree_group=series.worktree_group,
            code_repo_path=self.code,
            code_source_branch=series.code_work_branch,
            code_work_branch=f"ar/{leaf_id.lower()}",
            code_base_commit=code.base,
            code_worktree=self.root / "worktrees" / leaf_id,
            memory_repo_path=self.memory,
            memory_source_branch=series.memory_work_branch,
            memory_work_branch=f"ar/{leaf_id.lower()}",
            memory_base_commit=memory.base,
            memory_worktree=self.root / "worktrees" / f"{leaf_id}-memory",
            code_commit=code.landed,
            memory_content_commit=memory.landed,
            integration_status="completed",
            integrated_code_commit=code.landed,
            integrated_memory_content_commit=memory.landed,
            kind="leaf",
            leaf_id=leaf_id,
            parent_task_name=series.task_name,
            parent_contract_path=series.contract_path,
            sync_log=sync_log,
        )

    def test_a_leaf_level_sync_position_is_admitted_and_a_silent_one_is_refused(self) -> None:
        """The union is the chain's own records: the synced base is admitted, the silent one is not."""

        series = self._series()
        first = self._leaf(
            series,
            "L1",
            code=_Side(self.code_first, self.code_second),
            memory=_Side(self.memory_base, self.memory_first),
        )
        synced = self._leaf(
            series,
            "L2",
            code=_Side(self.code_synced, self.code_synced),
            memory=_Side(self.memory_synced, self.memory_second),
            sync_log=({"codeBaseTo": self.code_synced, "memoryBaseTo": self.memory_synced},),
        )
        _branch(self.code, "series", self.code_synced)
        _branch(self.memory, "series", self.memory_second)

        ordered = _require_exact_atomic_landing_chain(series, {"L1": first, "L2": synced})

        self.assertEqual([leaf.leaf_id for leaf in ordered], ["L1", "L2"])

        unrecorded = self._leaf(
            series,
            "L3",
            code=_Side(self.code_foreign, self.code_foreign),
            memory=_Side(self.memory_second, self.memory_third),
        )
        _branch(self.code, "series", self.code_foreign)
        _branch(self.memory, "series", self.memory_third)

        with self.assertRaises(CloseoutQueueError) as refused:
            _require_exact_atomic_landing_chain(
                series, {"L1": first, "L2": synced, "L3": unrecorded}
            )

        self.assertEqual(refused.exception.status, "atomic-series-leaf-chain-invalid")
        self.assertIn(self.code_foreign, str(refused.exception))

    def test_a_position_that_descends_from_a_step_does_not_vacate_it(self) -> None:
        """A recorded position reaching PAST a step cannot erase the foreign commit inside it.

        The rail used to subtract every recorded position from the revision walk
        (``rev-list <later> --not <earlier> <positions>``). Subtraction removes everything a
        position reaches, so one position descending from the step's own endpoint removed the whole
        step and the check passed vacuously: a genuinely foreign commit between the previous
        landing and the next leaf's base was admitted. The repair tests MEMBERSHIP instead, and
        this case pins the four ways the vacating entry can be supplied -- recorded by the leaf
        whose own step is checked, and recorded by a different ordered leaf -- on both spines.

        Both directions of the property are one assertion: the vacated step is refused AND names
        the commit that is neither the step's endpoint nor a recorded position.
        """

        code_out = _commit(self.code, "e.txt", "code out")
        memory_foreign = _commit(self.memory, "m5.txt", "memory foreign")
        memory_out = _commit(self.memory, "m6.txt", "memory out")

        for spine, recorder in (
            ("code", "own"),
            ("memory", "own"),
            ("code", "other"),
            ("memory", "other"),
        ):
            with self.subTest(spine=spine, recorder=recorder):
                self._assert_the_vacating_position_is_refused(
                    spine=spine,
                    own_leaf=recorder == "own",
                    code_out=code_out,
                    memory_foreign=memory_foreign,
                    memory_out=memory_out,
                )

    def _assert_the_vacating_position_is_refused(
        self,
        *,
        spine: str,
        own_leaf: bool,
        code_out: str,
        memory_foreign: str,
        memory_out: str,
    ) -> None:
        """One vacating configuration, built so the named spine is the one that must refuse."""

        series = self._series()
        vacated_code = spine == "code"
        # The vacating entry is recorded against the spine under test: that spine's leaf base is
        # advanced onto a commit it never reconciled with (``code_foreign`` / ``memory_foreign``),
        # while the position the contract records reaches PAST it. The other spine keeps D-45's
        # admitted shape, so a refusal can only come from the vacated spine.
        vacating = (
            {"codeBaseTo": code_out, "memoryBaseTo": self.memory_synced}
            if vacated_code
            else {"codeBaseTo": self.code_synced, "memoryBaseTo": memory_out}
        )
        first = self._leaf(
            series,
            "L1",
            code=_Side(self.code_first, self.code_second),
            memory=_Side(self.memory_base, self.memory_first),
        )
        second = self._leaf(
            series,
            "L2",
            code=(
                _Side(self.code_foreign, self.code_foreign)
                if vacated_code
                else _Side(self.code_synced, self.code_synced)
            ),
            memory=(
                _Side(memory_foreign, memory_foreign)
                if not vacated_code
                else _Side(self.memory_synced, self.memory_second)
            ),
            sync_log=(vacating,) if own_leaf else (),
        )
        contracts = {"L1": first, "L2": second}
        if own_leaf:
            # The series ref sits exactly on the last landing, so no step follows the one under test.
            _branch(self.code, "series", second.integrated_code_commit or "")
            _branch(self.memory, "series", second.integrated_memory_content_commit or "")
        else:
            # A different ordered leaf records the position, so the union is global rather than
            # per-step -- the shape L27 introduced and the shape this repair must not admit.
            third = self._leaf(
                series,
                "L3",
                code=(
                    _Side(code_out, code_out)
                    if vacated_code
                    else _Side(self.code_synced, self.code_synced)
                ),
                memory=(
                    _Side(memory_out, memory_out)
                    if not vacated_code
                    else _Side(self.memory_second, self.memory_second)
                ),
                sync_log=(vacating,),
            )
            contracts["L3"] = third
            _branch(self.code, "series", code_out if vacated_code else self.code_synced)
            _branch(self.memory, "series", memory_out if not vacated_code else self.memory_second)

        with self.assertRaises(CloseoutQueueError) as refused:
            _require_exact_atomic_landing_chain(series, contracts)

        self.assertEqual(refused.exception.status, "atomic-series-leaf-chain-invalid")
        named = code_out if vacated_code else memory_out
        foreign = self.code_foreign if vacated_code else self.memory_second
        self.assertIn(foreign, str(refused.exception))
        self.assertNotIn(named, str(refused.exception))


if __name__ == "__main__":
    unittest.main()
