"""The managed citation cache reclaims the dead and never on an unproved occupant.

Recorded defect (S5 of `260915-CAPS-L21`): the cache limited the WHOLE coordination root to
``MANAGED_NAMESPACE_LIMIT`` namespaces, released one only through the owning leaf's terminal
cleanup, and never evicted. ``reclaim_managed_namespace`` had zero callers. A finished leaf in
an unrelated master therefore blocked every citation repair everywhere -- measured 2026-09-17,
four occupants (375 MB), two of them finished leaves whose enclosure locators were already
gone and whose control records still said ``phase: active``. The refusal told the blocked leaf
to "complete worktree cleanup or abandon for an inactive leaf", i.e. to clean up another
master's leaf, which it cannot do.

The behaviours below are the operator-visible outcomes the repair promises, plus the boundary
facts that make them safe: admission reclaims only from positive terminal evidence, and the
resource is bounded by bytes rather than by a slot count that happened to fit one repository.
``_prove_terminal`` is the whole safety property, so each verdict that licenses eviction and
each input that must NOT license it are asserted directly, not only through an admission.

THE GUARANTEE IS FAIL-CLOSED, and that is the honest statement of it: an occupant is evicted
only when it presents a positive terminal verdict, so nothing UNPROVED is ever reclaimed. It is
NOT the stronger claim that no live occupant can ever be evicted -- the licensing verdicts are
read from bytes a live process could in principle still be sitting behind (a parsed terminal
``cleanup`` cell; a parsed contract whose stated worktrees are both gone). Fix round F2 removed
the one input that was neither evidence nor a statement by the leaf: contract bytes that fail
to PARSE. ``test_a_corrupted_contract_is_never_evidence_of_a_dead_leaf`` pins that.
"""

from __future__ import annotations

import fcntl
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations.source_index_cache import (
    MANAGED_NAMESPACE_BYTES_LIMIT,
    MANAGED_NAMESPACE_LIMIT,
    CacheControlState,
    ManagedCacheAuthority,
    _prove_terminal,
    admit_managed_namespace,
    open_shared_namespace,
)

CONTRACT_BODY = """---
schema: ar-series-contract/v1
schemaVersion: 1.0
kind: leaf
task_id: FIXTURE
task_name: fixture
repo_name: agents-remember
workflow_kind: light-task
memory_mode: external

coordination:
  root: {root}
  task_root: {root}/tasks/fixture
  series_contract_path: {root}/tasks/fixture/enclosures/{leaf}/series-contract.md
  task_artifact: {root}/tasks/fixture/task.md
  worktree_group: {root}/worktrees/fixture
  leaf_id: {leaf}
  parent_task_name: fixture
  parent_contract_path: {root}/tasks/fixture/series-contract.md

code:
  repo_path: {root}/repo
  source_branch: main
  work_branch: ar/{leaf}
  base_commit: 0000000000000000000000000000000000000000
  worktree: {root}/worktrees/fixture/{leaf}

memory:
  mode: external
  repo_path: {root}/memory
  source_branch: main
  work_branch: ar/{leaf}
  base_commit: 0000000000000000000000000000000000000000
  worktree: {root}/worktrees/fixture/memory-{leaf}
  ledger: {root}/worktrees/fixture/memory-{leaf}/memory.md

closeout:
  status: not-started

integration:
  status: not-started
  cleanup: {cleanup}
---

# Fixture contract
"""


@dataclass(frozen=True)
class OccupantFacts:
    """The facts one occupant is published with, so a case names only what it varies."""

    cleanup: str = "pending"
    worktrees: bool = True
    phase: Literal["active", "terminal"] = "active"
    bytes_of_payload: int = 1024
    contract_exists: bool = True
    publish: bool = True
    # `_namespace_ids` returns SORTED names, so a case that needs an occupant to sort at a
    # particular position (D45) has to state the id rather than inherit one from the leaf name.
    namespace_id: str | None = None


class CacheReclamationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.coordination = self.root / "coord"
        self.coordination.mkdir(parents=True)

    def contract(self, leaf: str, *, cleanup: str, worktrees: bool) -> Path:
        """One parseable leaf contract, with its worktrees present or absent on request."""
        path = self.coordination / "tasks" / "fixture" / "enclosures" / leaf / "series-contract.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            CONTRACT_BODY.format(root=self.coordination, leaf=leaf, cleanup=cleanup),
            encoding="utf-8",
        )
        if worktrees:
            (self.coordination / "worktrees" / "fixture" / leaf).mkdir(parents=True, exist_ok=True)
            (self.coordination / "worktrees" / "fixture" / f"memory-{leaf}").mkdir(
                parents=True, exist_ok=True
            )
        return path

    def authority(self, leaf: str, *, namespace_id: str | None = None) -> ManagedCacheAuthority:
        return ManagedCacheAuthority(
            coordination_root=self.coordination,
            contract_path=(
                self.coordination / "tasks" / "fixture" / "enclosures" / leaf / "series-contract.md"
            ),
            code_root=(self.coordination / "worktrees" / "fixture" / leaf).resolve(),
            memory_root=(self.coordination / "worktrees" / "fixture" / f"memory-{leaf}").resolve(),
            namespace_id=namespace_id or f"{leaf:0<64}"[:64].replace("-", "a"),
            lifecycle_id=f"01{leaf:0<24}"[:26],
        )

    def occupy(
        self,
        leaf: str,
        *,
        facts: OccupantFacts | None = None,
    ) -> ManagedCacheAuthority:
        """Write one namespace's control facts, and optionally publish its payload.

        ``publish=False`` leaves only the control record, which is the state of a leaf that has
        been given its reservation but has not yet stored an index -- the state admission has to
        refuse when there is no room.
        """
        asked = facts or OccupantFacts()
        authority = self.authority(leaf, namespace_id=asked.namespace_id)
        self.contract(leaf, cleanup=asked.cleanup, worktrees=asked.worktrees)
        if asked.publish:
            authority.namespace.mkdir(parents=True, exist_ok=True)
            (authority.namespace / "index.sqlite3").write_bytes(b"x" * asked.bytes_of_payload)
        authority.control_dir.mkdir(parents=True, exist_ok=True)
        authority.control_state.write_text(
            CacheControlState(
                authority.lifecycle_id or "", phase=asked.phase, outcome="completed"
            ).to_json(authority),
            encoding="utf-8",
        )
        if not asked.contract_exists:
            authority.contract_path.unlink()
        return authority

    def namespaces(self) -> set[str]:
        return {
            path.name
            for path in (self.coordination / "temp/citation-source-index/managed").iterdir()
            if path.is_dir() and not path.name.startswith(".")
        }

    def test_a_terminal_cleanup_frees_the_slot_and_a_fifth_leaf_is_admitted(self) -> None:
        """The two halves of the recorded block, in one sequence: release, then admit."""
        for index in range(MANAGED_NAMESPACE_LIMIT):
            self.occupy(
                f"leaf-{index}",
                facts=OccupantFacts(cleanup="completed" if index == 0 else "pending"),
            )
        self.assertEqual(len(self.namespaces()), MANAGED_NAMESPACE_LIMIT)
        newcomer = self.occupy("fresh")

        report = admit_managed_namespace(newcomer)

        self.assertEqual(len(self.namespaces()), MANAGED_NAMESPACE_LIMIT)
        self.assertIn(newcomer.namespace_id, self.namespaces())
        self.assertTrue(report["reclaimed"], report)

    def test_a_live_leaf_is_never_evicted(self) -> None:
        """Every occupant here is still working, so a full cache reclaims nothing."""
        live = [self.occupy(f"live-{index}") for index in range(4)]
        before = self.namespaces()
        newcomer = self.occupy("fresh")

        report = admit_managed_namespace(newcomer)

        self.assertEqual(report["reclaimed"], [])
        for authority in live:
            self.assertIn(authority.namespace_id, self.namespaces())
        self.assertEqual(before | {newcomer.namespace_id}, self.namespaces())

    def test_the_byte_bound_decides_even_under_the_count_ceiling(self) -> None:
        """One oversized occupant is over the byte bound and is reclaimed below the count."""
        self.occupy(
            "fat",
            facts=OccupantFacts(
                cleanup="completed", bytes_of_payload=MANAGED_NAMESPACE_BYTES_LIMIT + 1
            ),
        )
        newcomer = self.occupy("fresh")

        report = admit_managed_namespace(newcomer)

        self.assertEqual(report["reclaimed"], [self.authority("fat").namespace_id])
        self.assertNotIn(self.authority("fat").namespace_id, self.namespaces())

    def test_a_stuck_record_is_reclaimed_only_on_positive_terminal_evidence(self) -> None:
        """The recorded stuck shape: the locator is gone, the record still says ``active``."""
        gone_worktrees = self.occupy("stuck", facts=OccupantFacts(worktrees=False))
        gone_contract = self.occupy("orphan", facts=OccupantFacts(contract_exists=False))
        terminal = self.occupy(
            "finished", facts=OccupantFacts(cleanup="abandoned", worktrees=False)
        )
        fenced = self.occupy("fenced", facts=OccupantFacts(phase="terminal"))
        live = self.occupy("live")

        self.assertEqual(_prove_terminal(gone_worktrees.control_state), "worktrees-gone")
        self.assertEqual(_prove_terminal(gone_contract.control_state), "contract-gone")
        self.assertEqual(_prove_terminal(terminal.control_state), "contract-terminal")
        self.assertEqual(_prove_terminal(fenced.control_state), "terminal-fence")
        self.assertIsNone(_prove_terminal(live.control_state))

    def test_a_record_that_cannot_be_read_is_never_evidence_of_a_dead_leaf(self) -> None:
        """Silence is not proof. A foreign or truncated RECORD leaves its occupant alone."""
        occupant = self.occupy("opaque")
        occupant.control_state.write_text("{ not json", encoding="utf-8")

        self.assertIsNone(_prove_terminal(occupant.control_state))
        self.assertEqual(admit_managed_namespace(occupant)["reclaimed"], [])

    def test_a_corrupted_contract_is_never_evidence_of_a_dead_leaf(self) -> None:
        """F2 (fix round, `260915-CAPS-L21`). Bytes that fail to PARSE are not bytes that are GONE.

        The reproduced defect: `_terminal_contract_evidence` returned a ``contract-unreadable``
        verdict when ``load_contract`` raised, so a LIVE occupant whose contract was corrupt,
        mid-write, or written under an unknown schema was reclaimed -- and the module docstring
        and worker report claimed the stronger "no live leaf is ever evicted", which that path
        falsified. The three cases below are the three ways a contract fails to read, and each
        must leave the namespace exactly where it is.
        """
        corrupt = self.occupy("corrupt")
        corrupt.contract_path.write_text("this is not a contract\n", encoding="utf-8")
        truncated = self.occupy("truncated")
        truncated.contract_path.write_text(
            truncated.contract_path.read_text(encoding="utf-8")[:200], encoding="utf-8"
        )
        empty = self.occupy("empty")
        empty.contract_path.write_text("", encoding="utf-8")

        for occupant in (corrupt, truncated, empty):
            self.assertIsNone(
                _prove_terminal(occupant.control_state),
                f"{occupant.namespace_id} was proved terminal from unreadable contract bytes",
            )

        # And through a real admission that is over both bounds, so the reclamation pass runs.
        self.occupy(
            "fat",
            facts=OccupantFacts(
                cleanup="completed", bytes_of_payload=MANAGED_NAMESPACE_BYTES_LIMIT + 1
            ),
        )
        newcomer = self.occupy("fresh")
        report = admit_managed_namespace(newcomer)

        for occupant in (corrupt, truncated, empty):
            self.assertNotIn(occupant.namespace_id, report["reclaimed"])
            self.assertIn(occupant.namespace_id, self.namespaces())

    def test_a_terminal_occupant_sorting_beyond_the_old_scan_limit_is_still_reclaimed(self) -> None:
        """D45 (round 2, from observation 4 of the round-2 fix verification).

        The recorded defect: `_eviction_candidates` enumerated only the first
        ``MANAGED_NAMESPACE_LIMIT`` names, so a provably-terminal occupant whose id sorted
        beyond them was never examined and admission still refused at capacity -- the eviction
        fix was capacity-only under exactly the condition it was written for. Both directions
        are pinned here, because widening a scan is only safe if it widens nothing else: the
        dead occupant at that position is reclaimed, and a live one at the same position is
        still left alone.
        """
        # Four live occupants whose ids sort FIRST, so anything starting with "z" sorts last.
        for index in range(MANAGED_NAMESPACE_LIMIT):
            self.occupy(f"live-{index}", facts=OccupantFacts(namespace_id=f"a{index:063d}"))
        self.occupy(
            "zombie",
            facts=OccupantFacts(namespace_id="z" * 64, cleanup="completed", worktrees=False),
        )
        self.assertEqual(
            sorted(self.namespaces())[-1], "z" * 64, "the terminal occupant must sort last"
        )
        newcomer = self.occupy("fresh", facts=OccupantFacts(namespace_id="m" * 64))

        report = admit_managed_namespace(newcomer)

        self.assertIn("z" * 64, report["reclaimed"])
        self.assertNotIn("z" * 64, self.namespaces())

    def test_a_live_occupant_sorting_beyond_the_old_scan_limit_is_still_left_alone(self) -> None:
        """The other half of D45: widening WHICH occupants are examined changes no verdict."""
        for index in range(MANAGED_NAMESPACE_LIMIT):
            self.occupy(f"live-{index}", facts=OccupantFacts(namespace_id=f"a{index:063d}"))
        live_beyond = self.occupy(
            "live-last",
            facts=OccupantFacts(namespace_id="z" * 64, cleanup="pending", worktrees=True),
        )
        newcomer = self.occupy("fresh", facts=OccupantFacts(namespace_id="m" * 64))

        report = admit_managed_namespace(newcomer)

        self.assertNotIn("z" * 64, report["reclaimed"])
        self.assertIn(live_beyond.namespace_id, self.namespaces())

    def test_a_live_namespace_is_left_alone_even_when_it_reads_as_terminal(self) -> None:
        """The lease is the second half of the proof: a held lease defeats a terminal record.

        A terminal record and a live lease cannot both be true, so this is the case where the
        two proofs disagree. The lease wins, which is why eviction takes it.
        """
        occupant = self.occupy("leasing", facts=OccupantFacts(cleanup="completed"))
        with occupant.control_lock.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                self.assertEqual(_prove_terminal(occupant.control_state), "contract-terminal")
                self.assertEqual(admit_managed_namespace(occupant)["reclaimed"], [])
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertIn(occupant.namespace_id, self.namespaces())

    def test_a_full_cache_refuses_without_telling_one_leaf_to_clean_up_another(self) -> None:
        """The recorded refusal's exact defect: it named an action the blocked leaf cannot take.

        With four live occupants there is genuinely no room, and the refusal must say so while
        naming the OWNERS and the resource. What it must not do is reproduce the sentence
        measured on 2026-09-17 -- "Complete worktree cleanup or abandon for an inactive leaf"
        -- which addresses one master's leaf with another master's enclosure.
        """
        # Four live occupants with a payload each, and a fifth leaf that has its reservation
        # record but no namespace yet: the exact state admission has to refuse.
        for index in range(MANAGED_NAMESPACE_LIMIT):
            self.occupy(f"live-{index}")
        newcomer = self.occupy("fresh", facts=OccupantFacts(publish=False))

        with self.assertRaises(CitationCacheError) as raised:
            open_shared_namespace(newcomer, create=True)

        message = str(raised.exception)
        self.assertIn("no reclaimable room", message)
        self.assertIn("none was evicted", message)
        self.assertIn("Owners:", message)
        self.assertIn(
            "released automatically when each of those leaves reaches its terminal", message
        )
        self.assertNotIn("Complete worktree cleanup or abandon", message)
        self.assertNotIn("before admitting another namespace", message)
        # The reported totals are the WHOLE root's, not a truncated candidate scan's: four
        # published occupants at 1024 bytes each, both ceilings, and an owner line per occupant.
        self.assertIn(f"{MANAGED_NAMESPACE_LIMIT} namespace(s)", message)
        self.assertIn(f"{MANAGED_NAMESPACE_LIMIT * 1024} bytes", message)
        self.assertIn(f"count ceiling {MANAGED_NAMESPACE_LIMIT}", message)
        self.assertIn(f"byte ceiling {MANAGED_NAMESPACE_BYTES_LIMIT}", message)
        self.assertEqual(message.count("contract="), MANAGED_NAMESPACE_LIMIT)


if __name__ == "__main__":
    unittest.main()
