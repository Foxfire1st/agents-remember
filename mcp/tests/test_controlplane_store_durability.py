"""Durability contract for the six control-plane JSONL stores (260731-EFA-L5 R10/R8/R14).

Every store under ``agents_remember/controlplane`` keeps an append-only JSONL log that a reclaim
pass also rewrites whole (``compact`` / ``prune_lifecycles`` / ``replace_records``). At the leaf's
base commit only ``OperatorInboxStore`` took a lock. For the other five, an append landing between
a reclaim's read and its ``os.replace`` was dropped, and an append holding an ``"a"``-mode handle
across the reclaim's ``unlink`` (taken when the kept set comes out empty) was written into a dead
inode. Neither loss raised, and neither was logged: the caller was told the record was written.

``GateStore`` is the one that matters most. Its snapshots are what stop a human approval being
replayed by a second closeout (``records.apply_gate`` + ``enforcement.evaluate_gate``), and it was
the store with no lock, no pid-scoped temp name, no fsync and no torn-line tolerance at all.

Three assertions live here:

R10  no record an appender reports as written is missing afterwards, for all six record types,
     under real processes -- ``multiprocessing``, never threads, because the GIL would serialise
     the very window under test.
R8   the per-store torn-line policy, which is deliberately NOT uniform: three logs are read
     strictly and three tolerantly. The two whose rows a decision AND a screen both depend on --
     gates and expectation-rows -- carry two readers each rather than one policy splitting the
     difference, because the two folds need opposite behaviour. ``GateStore.read`` and
     ``ExpectationRowStore.read`` decide, and must fail loudly (silently skipping a malformed
     line could drop an ``applied`` marker and re-open the replay window); the
     ``read_for_projection`` beside each only renders, and must degrade PER ROW rather than
     crash a 1s tick or cost the reader the rest of the file.
R14  the harness is sensitive enough to detect the defect -- proven against a ``git archive`` of
     the leaf's base commit, so the proof does not depend on when the fix lands -- AND it refuses
     to report a run in which the compactor never ran, which is the way R10 above was once green
     over almost nothing (see :class:`HarnessVacuityGuardTests`).
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from _store_durability import (
    ADAPTERS,
    APPEND_CASES,
    CASES,
    MIN_SUCCESSFUL_RECLAIMS,
    STRESS_PROFILE,
    VacuousRunError,
    extract_base_commit_tree,
    run_against_source,
    run_case,
)
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.snapshots import read_expectation_rows, read_gates
from pydantic import ValidationError

# A line cut off mid-write: exactly what a crash or an interleaved append leaves behind.
TORN_LINE = '{"schema":"ar-gate-record/v1","id":"torn","ts":"2026-0'

# Which reads are correctness-bearing (a dropped row changes a decision) and which only paint a
# screen. Derived from the call sites, not from the docstrings:
#   gate            -> worktrees/modules/closeout.py, worktrees/modules/integrate.py and
#                      serving/hosted_interactions.py fold it to permit or refuse a mutation, none
#                      of them wrapping the read in a suppression.
#   expectation     -> the L2 overdue sweep reads it directly (observer/snapshots.py calls itself
#                      "surfacing only" and says so).
#   operator_inbox  -> mcp/tools/operator_inbox.py consumes rows; a consume is the ack of record.
#   attention       -> observer/snapshots.py + observer/projection_inputs.py only: dashboard state.
#   nudge           -> rate-limit bookkeeping for orchestration nudges.
#   supervisor      -> the sweep's cooldown memory, documented as non-authoritative.
STRICT_READ_CASES = ("gate", "expectation", "operator_inbox")
TOLERANT_READ_CASES = ("attention", "nudge", "supervisor_signal")


def _describe(result: dict[str, object]) -> str:
    attempted = int(result["attempted"])  # type: ignore[arg-type]
    completed = int(result.get("completed", attempted))  # type: ignore[arg-type]
    lost = int(result["lost"])  # type: ignore[arg-type]
    percent = (100.0 * lost / completed) if completed else 0.0
    return (
        f"{result['case']} / {result['scenario']}: attempted={attempted} completed={completed}; "
        f"{lost} completed records "
        f"written were missing afterwards ({percent:.2f}% loss); "
        f"sample={result['lost_sample']} torn_lines={result['torn_lines']} "
        f"reclaim_attempts={result.get('reclaim_attempts')} "
        f"successful_reclaims={result.get('successful_reclaims')} "
        f"stragglers={result['stragglers']}"
    )


class _TempRootTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def _tear(self, case: str) -> Path:
        """Seed one intact survivor for ``case`` and append a torn line after it."""
        return self._tear_mid_log(case, ("survivor-intact",), ())

    def _tear_mid_log(self, case: str, before: tuple[str, ...], after: tuple[str, ...]) -> Path:
        """Seed the ``before`` records, write a torn line, then seed the ``after`` records.

        A non-empty ``after`` puts the torn line INSIDE the log rather than at its end, which is
        what separates a reader that drops one row from a reader that stops at the first bad one:
        the trailing records are only reachable if the skip resumes.
        """
        adapter = ADAPTERS[case]()
        root = observer_logs_root(self.tmp)
        store = adapter.open(root)
        for record_id in before:
            adapter.write(store, record_id)
        with adapter.log_path(root).open("a", encoding="utf-8") as handle:
            handle.write(TORN_LINE + "\n")
        for record_id in after:
            adapter.write(store, record_id)
        return root


class MultiProcessDurabilityTests(_TempRootTest):
    """R10: N processes appending while one compacts, over all six record types."""

    def test_no_record_is_lost_when_an_append_races_a_compaction(self) -> None:
        """The lost-update window: an append lands between the reclaim's read and its commit.

        Forced rather than raced, so the result is the same on every run and on every machine --
        "no flakes" cuts both ways, and a stochastic reproduction of a narrow window is exactly
        the kind of test that passes on a loaded CI box and proves nothing.
        """
        for case in CASES:
            with self.subTest(store=case):
                result = run_case("forced_lost_update", case, self.tmp / f"lost-{case}")
                self.assertEqual(result["attempted"], 1, _describe(result))
                self.assertEqual(result["lost"], 0, _describe(result))
                self.assertEqual(result["stragglers"], [], _describe(result))

    def test_no_record_is_lost_when_a_compaction_empties_and_unlinks_the_log(self) -> None:
        """The unlink window: the rewrite drops the file while an appender holds its handle.

        Only stores whose write is a real ``open(path, "a")`` append can be stranded in an
        unlinked inode; ``AttentionDismissalStore`` has no append at all (its ``dismiss`` is a
        whole-file read-modify-write whose ``os.replace`` recreates the log), so it is not in
        ``APPEND_CASES`` and is covered by the lost-update case above.
        """
        for case in APPEND_CASES:
            with self.subTest(store=case):
                result = run_case("forced_unlink", case, self.tmp / f"unlink-{case}")
                self.assertEqual(result["attempted"], 1, _describe(result))
                self.assertEqual(result["lost"], 0, _describe(result))
                self.assertEqual(result["stragglers"], [], _describe(result))

    def test_no_record_is_lost_under_sustained_multi_process_write_and_compaction(self) -> None:
        """The unforced version: real concurrent processes, no interposition anywhere.

        This is the one that produces a loss *rate* rather than a yes/no, and the one whose
        failure text carries the number. Bounded by ``STRESS_PROFILE['timeout']`` per store and
        by the reclaimer's tick budget, so it terminates whatever the store does.
        """
        for case in CASES:
            with self.subTest(store=case):
                result = run_case("stress", case, self.tmp / f"stress-{case}")
                self.assertEqual(result["stragglers"], [], _describe(result))
                self.assertEqual(
                    result["attempted"],
                    STRESS_PROFILE["appenders"] * STRESS_PROFILE["per_appender"],
                    f"{_describe(result)}; write calls raised: {result['append_errors']}",
                )
                self.assertEqual(result["lost"], 0, _describe(result))
                self.assertEqual(result["torn_lines"], 0, _describe(result))

    def test_concurrent_operation_never_raises_out_of_a_store_call(self) -> None:
        """Losing no records is half of it; the caller must also not be handed an exception.

        Separate from the loss assertions on purpose: a store that starts raising instead of
        losing has not been fixed, it has moved the failure. At the base commit this fails for
        ``AttentionDismissalStore``, whose two concurrent rewriters collide on one non-pid-scoped
        temp path and hand one of them ``FileNotFoundError`` out of ``os.replace``.

        This is its OWN stress run against its own root, so the sibling test's "the run actually
        happened" guards do not cover it and are repeated here. Without them, an appender that
        died before reaching its loop would write no error file, and "zero write calls raised"
        would be reported over zero write calls -- a green that means nothing was measured.
        """
        for case in CASES:
            with self.subTest(store=case):
                result = run_case("stress", case, self.tmp / f"raise-{case}")
                self.assertEqual(result["stragglers"], [], _describe(result))
                self.assertEqual(
                    result["attempted"],
                    STRESS_PROFILE["appenders"] * STRESS_PROFILE["per_appender"],
                    f"no error is only evidence over a run that happened: {_describe(result)}",
                )
                self.assertEqual(
                    result["append_error_count"],
                    0,
                    f"write calls raised: {result['append_errors']}",
                )
                self.assertEqual(
                    result["reclaim_error_count"],
                    0,
                    f"reclaim calls raised: {result['reclaim_errors']}",
                )


class TornLinePolicyTests(_TempRootTest):
    """R8: the fold that decides and the fold that renders need opposite torn-line policies.

    Which is why gates and expectation-rows each ship TWO readers instead of one compromise:
    ``read`` raises for the enforcement fold, ``read_for_projection`` skips the bad row and keeps
    going for the dashboard. Both halves are pinned below, on both logs -- a strict reader that
    started skipping and a tolerant reader that started raising are each a defect, and each has a
    test here that fails on it.
    """

    def _seed_gate(self) -> None:
        # ``open`` and not ``applied``/``cancelled``/``expired``: the projection keep-filter prunes
        # those, and a gate the filter removed would prove nothing about torn-line handling.
        GateStore(observer_logs_root(self.tmp)).append(
            GateRecord(
                id="gate-intact",
                ts=datetime.now(UTC).isoformat(),
                kind="closeout-approval",
                state="open",
            )
        )

    def _append_torn_line(self) -> None:
        path = observer_logs_root(self.tmp) / "workspace" / "gates.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(TORN_LINE + "\n")

    def test_gate_enforcement_fold_refuses_a_torn_line(self) -> None:
        """The enforcement fold must fail loudly, never skip.

        ``current`` / ``all_current`` are what ``worktrees/modules/closeout.py``,
        ``worktrees/modules/integrate.py`` and ``serving/hosted_interactions.py`` read to decide
        whether a mutation may proceed, and none of them wraps the read in a suppression. Skipping
        a malformed line there could drop exactly the ``applied`` marker that closes the replay
        window, and the second mutation would be permitted with no error and no log line.
        """
        self._seed_gate()
        self._append_torn_line()
        store = GateStore(observer_logs_root(self.tmp))

        for path_name, read in (
            ("read", lambda: store.read(None)),
            ("current", lambda: store.current(None)),
            ("all_current", store.all_current),
        ):
            with self.subTest(enforcement_path=path_name), self.assertRaises(ValidationError):
                read()

    def test_gate_projection_fold_degrades_instead_of_crashing(self) -> None:
        """The dashboard fold over the same log must survive a torn line, per row.

        ``observer/snapshots.read_gates`` is the projection side; it runs on the 1s tick and
        behind the dashboard's ASGI handlers. A single malformed row must neither raise nor cost
        the reader every other gate in that log -- degrading to "one row missing" is a dashboard
        degrading; degrading to "no gates at all" is the projection losing its content.
        """
        self._seed_gate()
        self._append_torn_line()

        gates = read_gates(self.tmp, now=datetime.now(UTC))

        self.assertEqual(
            [gate.id for gate in gates],
            ["gate-intact"],
            "the projection must still surface the intact gates beside a torn line",
        )

    def test_correctness_bearing_reads_refuse_a_torn_line(self) -> None:
        """The three logs whose rows change a decision are read strictly, and stay that way."""
        for case in STRICT_READ_CASES:
            with self.subTest(store=case):
                root = self._tear(case)
                adapter = ADAPTERS[case]()
                with self.assertRaises(ValidationError):
                    adapter.read(adapter.open(root))

    def test_display_only_reads_skip_a_torn_line(self) -> None:
        """The three logs read only for display or rate-limiting keep reading past a bad row."""
        for case in TOLERANT_READ_CASES:
            with self.subTest(store=case):
                root = self._tear(case)
                adapter = ADAPTERS[case]()

                records = adapter.read(adapter.open(root))

                identifiers = [getattr(row, adapter.id_field) for row in records]
                self.assertIn("survivor-intact", identifiers)
                self.assertNotIn("torn", identifiers)

    def test_expectation_row_projection_degrades_instead_of_crashing(self) -> None:
        """The deadline projection loses the torn row and nothing else.

        ``ExpectationRowStore.read`` is strict because the L2 overdue sweep reads it directly and
        a dropped pending row is a missed deadline nobody is told about. The tolerance therefore
        lives on the projection side, and it is per row: ``observer/snapshots.read_expectation_rows``
        folds ``pending_for_projection()``, whose read skips the malformed line and keeps going.
        The ``contextlib.suppress(OSError, ValueError)`` still wrapping that fold is there for the
        I/O it was always there for; it is no longer what handles a malformed row -- and the
        control arm below is why that distinction is the whole test, because a ``suppress`` around
        the STRICT read degrades to the entire file.
        """
        self._tear_mid_log("expectation", ("survivor-a", "survivor-b"), ("survivor-c",))

        rows = read_expectation_rows(self.tmp, now=datetime.now(UTC))

        self.assertEqual(
            sorted(row.id for row in rows),
            ["survivor-a", "survivor-b", "survivor-c"],
            "the projection must still surface every intact deadline beside a torn row, "
            "including the rows appended after it",
        )

        # Control arm: reconstruct the whole-log tolerance this leaf removed -- the strict read
        # under the same suppress -- against the SAME log. It yields nothing at all: three
        # deadlines the operator is shown as due become an empty dashboard with no error. That is
        # the difference the assertion above pins, and the one an ``assertIsInstance(rows, list)``
        # could not see, since "3 of 4 rows" and "0 of 4 rows" are both lists.
        strict = ExpectationRowStore(observer_logs_root(self.tmp))
        with self.assertRaises(ValidationError):
            strict.pending()
        whole_log_tolerance: list[str] = []
        with contextlib.suppress(OSError, ValueError):
            whole_log_tolerance = [row.id for row in strict.pending()]
        self.assertEqual(
            whole_log_tolerance,
            [],
            "whole-log tolerance is not a degraded projection, it is an empty one -- if this "
            "arm ever returns rows, the strict reader stopped being strict",
        )


class HarnessVacuityGuardTests(_TempRootTest):
    """R14, the other half: the instrument must refuse to report a run that measured nothing.

    Every loss figure above is a figure about a window -- the one between a reclaim's read and its
    commit. Open that window twice instead of two hundred times and "0 records lost" costs the
    store nothing to earn, which is precisely how the defect this class guards against went
    unseen: ``run_stress`` used to key its stop flag off ``root.parent``, all six cases here pass
    sibling roots under one ``self.tmp``, and so every case after the first found the previous
    case's flag already set and left the tick loop after ONE tick. All six were green. Driving that
    layout across all eight stores this instrument covers, on this tree: 25 reclaim ticks for the
    first and exactly 1 for each of the seven after it, 0.00% loss reported by every one.

    The guard is in the instrument (``_store_durability.MIN_SUCCESSFUL_RECLAIMS``) rather than
    here, so
    that the provider suite and the base-commit script entry point are covered by the same floor
    and not by a copy of it. This is the test that proves the refusal is real and reachable, using
    the shipped code path rather than a hand-built result dict.
    """

    def test_a_stress_run_whose_reclaimer_barely_ticked_is_refused(self) -> None:
        """A one-tick run raises out of the harness instead of returning a green result."""
        with self.assertRaises(VacuousRunError) as refusal:
            run_case("stress", "gate", self.tmp / "vacuous", max_ticks=1)

        message = str(refusal.exception)
        self.assertIn("completed 1 successful compaction", message)
        self.assertIn(str(MIN_SUCCESSFUL_RECLAIMS), message)

    def test_the_floor_is_far_enough_below_what_a_real_run_produces(self) -> None:
        """The floor separates a vacuous run from a real one, with room on both sides.

        Asserted rather than left in a comment because both halves are load-bearing and neither is
        obvious from the constant. Too low and it re-admits the run that measured nothing; too
        high and it turns a slow machine red for a durability defect that is not there. Measured
        over all eight stores, four runs each: 22-39 reclaim ticks idle, 34-49 under 24 CPU hogs
        on a 20-core box -- contention RAISES the count, because the appenders' 2ms pacing
        stretches in wall clock while the reclaimer keeps polling.
        """
        self.assertGreater(
            MIN_SUCCESSFUL_RECLAIMS,
            2,
            "one or two successful compactions is the vacuous run itself",
        )
        self.assertLess(
            MIN_SUCCESSFUL_RECLAIMS,
            22,
            "the floor must sit below the lowest tick count ever measured on this profile",
        )


class HarnessSensitivityTests(_TempRootTest):
    """R14: the contract above must be able to fail. Proven against the leaf's base commit.

    The fix for this leaf lands in this same worktree while this test is being written, so
    "it failed when I ran it" is not evidence anyone can re-check. A ``git archive`` of the base
    commit is: it is the same defect, byte for byte, whenever this runs.

    The scenario asserted below is the deterministic one, because a rate is not a thing to assert
    on. The rates are worth recording, though, and these were taken through this same archive on
    ``STRESS_PROFILE`` after ``harness_work_dir`` was fixed -- four runs, base commit, percentage
    of records the store reported written and then did not have:

        attention 18.27-30.10 | gate 7.50-10.50 | supervisor_signal 7.50-9.00
        expectation 6.50-9.50 | nudge 5.50-9.00 | operator_inbox 0.00 (all four runs)

    Which is the same ordering, and the same lone survivor, as the figures this leaf recorded
    before the instrument was fixed (31.45 / 11.50 / 10.50 / 10.20 / 9.20 / 0.00): those sit at or
    just above the top of each range here rather than outside it. The stalled compactor did not
    manufacture the base-commit rates -- ``main`` already gave each case its own parent, so the
    archive runs were never the ones it silenced. What it silenced was the CONTRACT above, which
    is measured against the live tree and passed over one tick per store.
    """

    def test_the_forced_scenario_detects_loss_in_the_base_commit(self) -> None:
        source = extract_base_commit_tree(self.tmp / "base")
        self.assertTrue(
            (source / "agents_remember" / "controlplane" / "store.py").is_file(),
            "base-commit archive is missing the store under test",
        )

        result = run_against_source(
            source,
            {
                "scenario": "forced_lost_update",
                "cases": list(CASES),
                "root": str(self.tmp / "base-run"),
                "handoff_seconds": 2.0,
                "timeout": 60.0,
            },
        )

        self.assertEqual(result["source_root"], str(source))
        lost = {row["case"]: row["lost"] for row in result["runs"]}
        unlocked = [case for case in CASES if case != "operator_inbox"]
        self.assertEqual(
            {case: lost[case] for case in unlocked},
            dict.fromkeys(unlocked, 1),
            f"the harness failed to detect the base-commit defect: {json.dumps(lost)}",
        )
        self.assertEqual(
            lost["operator_inbox"],
            0,
            "OperatorInboxStore took an fcntl lock across append and compaction at the base "
            "commit and is the one store that already survived this window; a loss here means "
            "the harness is measuring something other than the defect",
        )
