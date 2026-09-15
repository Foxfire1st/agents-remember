"""Completion-relative proof for the observer-to-notifier handoff (`LOCR-R04@v1`).

The serving lifetime owns two independently scheduled loops, and neither can be woken by the other.
``_terminal_observation_loop`` polls the canonical liveness sweeper every
``P = DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS`` (1.0 s) AFTER each attempt returns, and the sweeper
itself admits one full sweep per ``F = TerminalCatalogLivenessConfig.sweep_interval_seconds``
(10.0 s) measured from the start of the previous full sweep. ``_agent_notifier_loop`` evaluates the
durable catalog and then sleeps ``N = settings.agent_notifier.interval_seconds`` (10.0 s), also after
its pass returns. The relay is only correct if those two cadences compose into a bounded handoff, so
the delivered latency relation is:

    observer_release = max(previous_full_sweep_start + F,
                           completion of any observer refresh() still in flight and holding the
                           shared sweep lock at that eligibility point)
    consuming_sweep  = the first lifecycle poll at or after observer_release, at most P later
    commit           = the consuming full sweep's own durable catalog commit
    next_notifier    = last_notifier_pass_end + N, which is commit + N when the notifier was
                       sleeping at the commit and commit + R_notifier + N when a pass that had not
                       already consumed the truth was still in flight

    worst phase      = F + P + R_observer + D_commit + N + R_notifier

-- 21.0 s of logical scheduling for the default knobs, plus only the measured overrun and commit
durations. ``R_observer`` is the remaining duration, measured at eligibility, of any refresh holding
the shared sweep lock: the previous full sweep or a starting-row fast-path pass. ``D_commit`` is the
consuming sweep's own duration up to its commit, and ``R_notifier`` the remaining duration of a
notifier pass already in flight at that commit.

The harness lives in ``_serving_handoff``: it enters the real ``_serving_lifespan`` under a
deadline-correct virtual clock, so the real loops, the real sweeper, the real catalog commit
boundary, and the real notifier sweep are what these cases measure. Each case asserts the bound and
its decomposition from measured terms rather than illustrating it, and each protects one clause of the
oracle: the default phase inside the bound, the previous full sweep's overrun, a starting-row
fast-path overrun, an in-flight notifier pass, committed-truth-only reads, coalesced missed ticks, a
live pass that did not observe the fact, and notifier disablement with in-place re-enabling.
"""

from __future__ import annotations

from _serving_handoff import (
    _REAL_SLEEP,
    ARMED_EVIDENCE_ID,
    WORST_PHASE_BOUND,
    F,
    N,
    P,
    _HandoffCase,
    _wait_until,
)


class ServingNotifierHandoffTests(_HandoffCase):
    async def test_the_default_phase_handoff_stays_inside_the_worst_phase_bound(self) -> None:
        # The notifier's very first pass is a long one, so its interval is offset from the observer's
        # poll grid the way two real loops drift apart.
        self.world.heartbeat_gate.arm()
        async with self.fixture.running():
            await _wait_until(self.world.heartbeat_gate.entered.is_set)
            self.clock.elapse(N - P / 2)
            self.world.heartbeat_gate.release.set()
            await self.wait_for_notifier_pass(after=0)

            previous = await self.drive_to_full_sweep()
            # The fact becomes readable half a polling delay after that sweep started: the packet's
            # conforming example, where evidence appears shortly after its row was visited rather
            # than in the same instant the sweep began.
            self.arm_the_fact_after(previous.entered_at, P / 2)
            # A notifier pass runs after that sweep and before the consuming one, so its read
            # demonstrably does not carry the fact: being alive does not satisfy the stage.
            await self.notifier_poll()
            live = self.passes.passes[-1]
            self.assertTrue(live.reads)
            self.assertFalse(live.observed(ARMED_EVIDENCE_ID))
            self.assertFalse(self.catalog.committed_holds(ARMED_EVIDENCE_ID))

            commit = await self.drive_to_commit()
            self.assertFalse(self.passes.passes[-1].observed(ARMED_EVIDENCE_ID))
            await self.notifier_poll()
            handoff = self.measure(previous)
            self.assert_oracle(handoff)

            # No overrun was injected and none was measured, so the nominal 21.0 s is the bound.
            self.assertEqual(handoff.overrun, 0.0)
            self.assertEqual(handoff.commit_duration, 0.0)
            self.assertEqual(handoff.notifier_overrun, 0.0)
            # The STRICT form of the observer-phase bound, asserted where the arming that makes it
            # strict actually happens: this case arms `P / 2` after the previous sweep's start, so the
            # phase is strictly inside its own maximum. Its failing state is the tight arming
            # (`delay = 0`) that the other six handoff cases use -- it is a constraint on this case's
            # scenario, verified to fire there, not a claim about production behaviour. The
            # arithmetic identity that would restate the arming constant has no such state and is
            # not asserted.
            self.assertLess(handoff.observer_term, F + handoff.overrun)
            self.assertLessEqual(handoff.latency, WORST_PHASE_BOUND)
            # The sleeping-notifier branch: the consuming pass woke on the interval it had already
            # started before the commit, and consumed on that same sleep rather than a fresh one.
            self.assertLess(handoff.wait, N)
            self.assertEqual(self.consume(), self.passes.passes[-1])
            self.assertGreater(handoff.consumption.entered_at, commit.at)

    async def test_an_in_flight_full_sweep_overrun_delays_the_consuming_sweep(self) -> None:
        async with self.fixture.running():
            await self.settle()
            # The previous full sweep is parked INSIDE its own batch, after it already read the
            # evidence row unarmed: the fact exists from that sweep's start and it cannot consume it.
            self.adapter.overrun.arm()
            await self.poll_until(self.adapter.overrun.entered.is_set, gate=self.adapter.overrun)
            started = self.clock.seconds
            self.arm_the_fact_at(started)
            # A notifier pass starts while that sweep still holds the shared lock, so its read is
            # serialized behind the commit and returns truth without the fact.
            reads_before = len(self.reads.reads)
            passes_before = len(self.passes.passes)
            self.world.heartbeat_gate.arm()
            self.clock.release_delay(N)
            await _wait_until(lambda: self.passes.calls > passes_before)
            self.assertEqual(len(self.reads.reads), reads_before)

            self.clock.elapse(F + P / 2)
            records_before = len(self.sweeps.records)
            self.adapter.overrun.release.set()
            await self.wait_for_sweep_record(after=records_before)
            previous = self.sweeps.full_sweeps[-1]
            self.assertEqual(previous.entered_at, started)
            await _wait_until(self.world.heartbeat_gate.entered.is_set)

            commit = await self.drive_to_commit()
            read = self.reads.reads[reads_before]
            self.assertLess(read.attempted, commit.sequence)
            self.assertFalse(read.holds(ARMED_EVIDENCE_ID))

            self.clock.elapse(F / 2)
            self.world.heartbeat_gate.release.set()
            await self.wait_for_notifier_pass(after=passes_before)
            await self.notifier_poll()
            handoff = self.measure(previous)
            self.assert_oracle(handoff)

            # The overrun was the previous full sweep's own remaining duration at eligibility, and
            # the whole bound is met exactly rather than merely satisfied.
            self.assertAlmostEqual(handoff.overrun, P / 2)
            self.assertAlmostEqual(handoff.release, previous.entered_at + F + P / 2)
            self.assertAlmostEqual(handoff.poll_delay, P)
            self.assertAlmostEqual(handoff.notifier_overrun, F / 2)
            self.assertEqual(handoff.commit_duration, 0.0)
            self.assertAlmostEqual(handoff.latency, handoff.bound)

    async def test_a_starting_row_fast_path_pass_counts_toward_the_same_overrun(self) -> None:
        async with self.fixture.running():
            await self.settle()
            previous = await self.drive_to_full_sweep()
            self.arm_the_fact_at(previous.entered_at)
            # A notifier pass evaluates its snapshot and parks at its own last act.
            passes_before = len(self.passes.passes)
            self.world.heartbeat_gate.arm()
            await self.notifier_poll(gate=self.world.heartbeat_gate)
            self.assertGreater(self.passes.calls, passes_before)
            self.assertEqual(len(self.passes.passes), passes_before)
            self.assertFalse(self.reads.holds(ARMED_EVIDENCE_ID))

            # The one-second fast path owns the same sweep lock while the full sweep becomes due.
            self.adapter.fastpath.arm()
            await self.poll_until(self.adapter.fastpath.entered.is_set, gate=self.adapter.fastpath)
            self.clock.elapse(F + P * 5)
            overrun = self.clock.seconds - (previous.entered_at + F)
            records_before = len(self.sweeps.records)
            self.adapter.fastpath.release.set()
            await self.wait_for_sweep_record(after=records_before)

            # The consuming sweep's own work before its commit is measured too.
            self.adapter.commit.arm()
            await self.poll_until(self.adapter.commit.entered.is_set, gate=self.adapter.commit)
            consuming_started = self.clock.seconds
            self.clock.elapse(P * 3)
            records_before = len(self.sweeps.records)
            self.adapter.commit.release.set()
            await self.wait_for_sweep_record(after=records_before)
            commit = self.catalog.commit_holding(ARMED_EVIDENCE_ID)
            assert commit is not None
            self.assertAlmostEqual(commit.at, consuming_started + P * 3)

            self.clock.elapse(P * 4)
            released_at = self.clock.seconds
            self.world.heartbeat_gate.release.set()
            await self.wait_for_notifier_pass(after=passes_before)
            await self.notifier_poll()
            handoff = self.measure(previous)
            self.assert_oracle(handoff)

            # Every term of the bound is realized and measured, and the latency meets it exactly.
            self.assertAlmostEqual(handoff.overrun, overrun)
            self.assertAlmostEqual(handoff.poll_delay, P)
            self.assertAlmostEqual(handoff.commit_duration, P * 3)
            self.assertAlmostEqual(handoff.notifier_overrun, released_at - commit.at)
            # Every term is measured, and the observed latency is exactly the oracle's sum.
            self.assertAlmostEqual(handoff.latency, handoff.bound)
            self.assertAlmostEqual(
                handoff.latency,
                WORST_PHASE_BOUND
                + handoff.overrun
                + handoff.commit_duration
                + handoff.notifier_overrun,
            )
            self.assertGreater(handoff.latency, WORST_PHASE_BOUND)

    async def test_an_in_flight_notifier_pass_finishes_before_its_interval_starts(self) -> None:
        async with self.fixture.running():
            await self.settle()
            previous = await self.drive_to_full_sweep()
            self.arm_the_fact_at(previous.entered_at)
            # The notifier's pass is in flight at the commit: it read its snapshot from before the
            # fact, and it is held after that snapshot until the consuming sweep has committed.
            passes_before = len(self.passes.passes)
            self.world.heartbeat_gate.arm()
            await self.notifier_poll(gate=self.world.heartbeat_gate)
            self.assertGreater(self.passes.calls, passes_before)
            self.assertFalse(self.reads.holds(ARMED_EVIDENCE_ID))

            commit = await self.drive_to_commit()
            self.clock.elapse(F / 2)
            self.world.heartbeat_gate.release.set()
            await self.wait_for_notifier_pass(after=passes_before)
            in_flight = self.passes.passes[-1]
            self.assertGreater(in_flight.exited_at, commit.at)
            self.assertFalse(in_flight.observed(ARMED_EVIDENCE_ID))

            await self.notifier_poll()
            handoff = self.measure(previous)
            self.assert_oracle(handoff)

            # The interval starts when the in-flight pass finished, not at the commit, so the wait
            # is its remaining duration plus one whole interval.
            self.assertAlmostEqual(handoff.notifier_overrun, F / 2)
            self.assertAlmostEqual(handoff.wait, N + F / 2)
            self.assertEqual(handoff.overrun, 0.0)
            self.assertAlmostEqual(handoff.poll_delay, 0.0)
            self.assertLessEqual(handoff.latency, handoff.bound)

    async def test_the_notifier_reads_only_committed_catalog_truth(self) -> None:
        async with self.fixture.running():
            await self.settle()
            previous = await self.drive_to_full_sweep()
            self.arm_the_fact_at(previous.entered_at)
            # Hold the consuming sweep inside its own open batch, after it projected the fact, so an
            # in-memory observation exists that no reader may treat as truth yet.
            self.catalog.upsert_gate = self.world.upsert_gate
            self.world.upsert_gate.arm()
            await self.poll_until(
                self.world.upsert_gate.entered.is_set, gate=self.world.upsert_gate
            )
            self.assertFalse(self.catalog.committed_holds(ARMED_EVIDENCE_ID))

            # The notifier's own pass now reaches the catalog while that batch is open: its read is
            # attempted, and it does not return.
            reads_before = len(self.reads.reads)
            passes_before = len(self.passes.passes)
            self.clock.release_delay(N)
            await _wait_until(lambda: self.passes.calls > passes_before)
            await _REAL_SLEEP(P / 20)
            self.assertEqual(len(self.reads.reads), reads_before)
            self.assertEqual(len(self.passes.passes), passes_before)
            self.assertFalse(self.catalog.committed_holds(ARMED_EVIDENCE_ID))

            self.world.upsert_gate.release.set()
            commit = await self.drive_to_commit()
            await self.wait_for_notifier_pass(after=passes_before)

            read = self.reads.reads[reads_before]
            # The read was attempted before the commit and returned after it: it could not have
            # carried the uncommitted observation, and it carries the committed rows exactly.
            self.assertLess(read.attempted, commit.sequence)
            self.assertGreater(read.returned, commit.sequence)
            self.assertTrue(read.holds(ARMED_EVIDENCE_ID))
            # Both sides are the notifier's own scope: ``read.rows`` comes from the notifier's
            # ``list(include_terminated=True)``, so the committed side must not be the default
            # filtered read -- that would compare a full collection against a narrower one and hold
            # only while no catalog row is terminated.
            self.assertEqual(read.rows, tuple(self.catalog.list_committed(include_terminated=True)))
            self.assertTrue(self.catalog.committed_holds(ARMED_EVIDENCE_ID))
            self.assertTrue(self.passes.passes[-1].observed(ARMED_EVIDENCE_ID))

    async def test_missed_ticks_coalesce_instead_of_queueing_a_catch_up_burst(self) -> None:
        async with self.fixture.running():
            await self.settle()
            # The observation owner's one pass is parked while more than one nominal tick expires.
            self.adapter.fastpath.arm()
            await self.poll_until(self.adapter.fastpath.entered.is_set, gate=self.adapter.fastpath)
            calls_while_parked = self.sweeps.calls
            self.clock.elapse(P * 3)
            await _REAL_SLEEP(P / 20)
            self.assertEqual(self.sweeps.calls, calls_while_parked)

            records_before = len(self.sweeps.records)
            self.adapter.fastpath.release.set()
            await self.wait_for_sweep_record(after=records_before)
            self.assertEqual(self.sweeps.calls, calls_while_parked)

            # The next attempt is one whole delay after the parked one returned: the three missed
            # ticks queued nothing, and no second attempt of this kind was ever in flight.
            parked = self.sweeps.records[-1]
            await self.observer_poll()
            attempts = self.sweeps.records
            self.assertEqual(self.sweeps.calls, calls_while_parked + 1)
            self.assertAlmostEqual(attempts[-1].entered_at, parked.exited_at + P)
            self.assertLessEqual(attempts[-2].exited_at, attempts[-1].entered_at)

            # The same for the notifier: a long pass coalesces the intervals it outran.
            passes_before = len(self.passes.passes)
            self.world.heartbeat_gate.arm()
            await self.notifier_poll(gate=self.world.heartbeat_gate)
            passes_while_parked = self.passes.calls
            self.clock.elapse(N * 3)
            await _REAL_SLEEP(P / 20)
            self.assertEqual(self.passes.calls, passes_while_parked)

            self.world.heartbeat_gate.release.set()
            await self.wait_for_notifier_pass(after=passes_before)
            self.assertEqual(self.passes.calls, passes_while_parked)
            await self.notifier_poll()
            self.assertEqual(self.passes.calls, passes_while_parked + 1)
            self.assertAlmostEqual(
                self.passes.passes[-1].entered_at, self.passes.passes[-2].exited_at + N
            )

    async def test_a_live_pass_that_did_not_observe_the_fact_does_not_satisfy_the_stage(
        self,
    ) -> None:
        async with self.fixture.running():
            await self.settle()
            previous = await self.drive_to_full_sweep()
            self.arm_the_fact_at(previous.entered_at)
            # Three rate-limited polls are alive while the fact exists; none of them reads the
            # evidence row, so none of them can be the pass that satisfies the stage.
            reads_before = len(self.adapter.evidence_reads())
            for _ in range(3):
                await self.observer_poll()
            self.assertEqual(len(self.adapter.evidence_reads()), reads_before)
            self.assertFalse(self.catalog.committed_holds(ARMED_EVIDENCE_ID))

            # A notifier pass alive in the same window reads only pre-fact truth.
            await self.notifier_poll()
            live = self.passes.passes[-1]
            self.assertFalse(live.observed(ARMED_EVIDENCE_ID))

            await self.drive_to_commit()
            self.assertGreater(len(self.adapter.evidence_reads()), reads_before)
            self.assertTrue(self.catalog.committed_holds(ARMED_EVIDENCE_ID))
            self.assertFalse(live.observed(ARMED_EVIDENCE_ID))

            await self.notifier_poll()
            handoff = self.measure(previous)
            self.assert_oracle(handoff)
            self.assertIsNot(self.consume(), live)
            self.assertGreater(handoff.consumption.entered_at, live.exited_at)

    async def test_a_disabled_notifier_pauses_signal_derivation_only(self) -> None:
        self.world.settings.set_enabled(enabled=False)
        async with self.fixture.running():
            await self.settle()
            previous = await self.drive_to_full_sweep()
            self.arm_the_fact_at(previous.entered_at)
            # Observation continues and commits the fact with signal derivation switched off.
            await self.drive_to_commit()
            self.assertTrue(self.catalog.committed_holds(ARMED_EVIDENCE_ID))
            self.assertGreaterEqual(len(self.sweeps.full_sweeps), 2)
            await self.notifier_poll()
            self.assertEqual(self.passes.calls, 0)

            # Re-enabling in place makes the already-committed truth visible on the notifier's next
            # completion-relative pass, with no external traffic and no restart.
            self.world.settings.set_enabled(enabled=True)
            await self.notifier_poll()
            self.assertEqual(self.passes.calls, 1)
            self.assertTrue(self.passes.passes[0].observed(ARMED_EVIDENCE_ID))
            self.assertTrue(self.passes.passes[0].reads)
            self.assertEqual(self.consume(), self.passes.passes[0])
