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
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_mode import MemoryMode
from agents_remember.worktrees.series_closeout import _leaf_landing_precedes
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
        """Equality on both sides stays unordered in both directions -- nothing is relaxed here."""

        series = self._contract(code=self.code_next, memory=self.memory_second, leaf_id="L1")
        one = self._contract(code=self.code_next, memory=self.memory_second, leaf_id="L1")
        two = self._contract(code=self.code_next, memory=self.memory_second, leaf_id="L2")

        self.assertFalse(_leaf_landing_precedes(series, one, two))
        self.assertFalse(_leaf_landing_precedes(series, two, one))

    def test_a_shared_commit_with_the_same_memory_stays_unordered(self) -> None:
        """Two leaves recording one identical pair have no order to give."""

        series = self._contract(code=self.code_next, memory=self.memory_first, leaf_id="L1")
        left = self._contract(code=self.code_next, memory=self.memory_first, leaf_id="L1")
        right = self._contract(code=self.code_next, memory=self.memory_first, leaf_id="L2")

        self.assertFalse(_leaf_landing_precedes(series, left, right))
        self.assertFalse(_leaf_landing_precedes(series, right, left))

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


if __name__ == "__main__":
    unittest.main()
