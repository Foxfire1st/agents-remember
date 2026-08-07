from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Literal

from agents_remember.observer.ambient import AmbientLifecycle, AmbientTiming
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import (
    DEFAULT_END_OUTCOME,
    LIVE_STATES,
    STATES,
    TERMINAL_STATES,
    LifecycleError,
    LifecycleState,
    LifecycleVocabularyError,
    LiveState,
    TerminalState,
    check_state_partition,
    coerce_end_outcome,
    vocabulary_names,
)
from agents_remember.observer.projection import (
    ACTIVE_STATES,
    STATE_COUNT_FIELDS,
    Metrics,
    state_count_field,
    state_count_fields,
)
from agents_remember.observer.reducer import (
    _KIND_UPDATES,
    TOKEN_SERIES_MAX,
    TOKEN_SERIES_RECENT,
    WorkspaceStructure,
    project_lifecycle,
    project_workspace,
    token_series,
)
from agents_remember.observer.store import EventStore
from test_observer_projection import FRESH, OBSERVED_BY_MODEL, T0, _event, _started


class TokenSeriesTests(unittest.TestCase):
    def test_cumulative_series_from_tool_events(self) -> None:
        log = [
            _started(),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:05+00:00",
                by=OBSERVED_BY_MODEL,
                tool="a",
                tokens=100,
                ok=True,
            ),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:10+00:00",
                by=OBSERVED_BY_MODEL,
                tool="b",
                tokens=50,
                ok=True,
            ),
        ]
        self.assertEqual(
            [(s.ts, s.cumulative) for s in token_series(log)],
            [("2026-06-13T18:00:05+00:00", 100), ("2026-06-13T18:00:10+00:00", 150)],
        )

    def test_series_on_projection(self) -> None:
        log = [
            _started(),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:05+00:00",
                by=OBSERVED_BY_MODEL,
                tool="a",
                tokens=7,
                ok=True,
            ),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual([s.cumulative for s in proj.tokenSeries], [7])

    def test_no_tool_events_is_empty(self) -> None:
        self.assertEqual(token_series([_started()]), [])

    def _tool_log(self, count: int) -> list[Event]:
        log = [_started()]
        for index in range(count):
            log.append(
                _event(
                    "tool.completed",
                    ts=f"2026-06-13T18:{index // 60000:02d}:{(index // 1000) % 60:02d}.{index % 1000:03d}+00:00",
                    by=OBSERVED_BY_MODEL,
                    tool="a",
                    tokens=10,
                    ok=True,
                )
            )
        return log

    def test_series_at_the_bound_is_untouched(self) -> None:
        series = token_series(self._tool_log(TOKEN_SERIES_MAX))
        self.assertEqual(len(series), TOKEN_SERIES_MAX)
        self.assertEqual(series[-1].cumulative, 10 * TOKEN_SERIES_MAX)

    def test_long_series_is_decimated_to_the_bound(self) -> None:
        # The served fuel gauge is bounded -- a long lifecycle's series decimates
        # to TOKEN_SERIES_MAX (newest TOKEN_SERIES_RECENT exact, older history uniform-thinned,
        # first sample kept) instead of growing ~60 B/sample into every lifecycle delta.
        count = 3000
        series = token_series(self._tool_log(count))
        self.assertEqual(len(series), TOKEN_SERIES_MAX)
        # The newest window is exact: the last TOKEN_SERIES_RECENT cumulative values.
        recent = [sample.cumulative for sample in series[-TOKEN_SERIES_RECENT:]]
        self.assertEqual(
            recent, [10 * n for n in range(count - TOKEN_SERIES_RECENT + 1, count + 1)]
        )
        # The thinned history keeps the first sample and stays monotonic (a decimated
        # cumulative series must still read as a fuel gauge).
        self.assertEqual(series[0].cumulative, 10)
        cumulative = [sample.cumulative for sample in series]
        self.assertEqual(cumulative, sorted(cumulative))
        self.assertEqual(series[-1].cumulative, 10 * count)


class MetricsBucketVocabularyTests(unittest.TestCase):
    """Every lifecycle state the vocabulary declares reaches a metrics bucket.

    The defect these exist to make impossible is a set difference, not a typo: ``Metrics``
    bucketed three states by hand while ``State`` declared six, so an ``awaiting-developer``
    lifecycle counted towards ``lifecycleCount`` and ``totalTokens`` and towards nothing else
    -- the rollup could not show a lifecycle that had handed the turn back. The live set here
    is re-derived as ``STATES`` minus ``TERMINAL_STATES`` (rather than read off
    ``projection.ACTIVE_STATES``, which is the live half verbatim), so the measurement is not
    taken with the same instrument it is checking, and a SEVENTH state fails here.

    That is the *coverage* half. It is only as good as the classification underneath it, which
    :class:`StatePartitionTests` and :class:`TerminalityIsStructuralTests` hold separately --
    a state mis-filed as terminal would be excluded from these buckets *correctly*, and this
    class would have nothing to say about it.
    """

    def live_states(self) -> tuple[str, ...]:
        """The states a rollup bucket is owed: every non-terminal member of the vocabulary."""
        return tuple(state for state in STATES if state not in TERMINAL_STATES)

    def test_the_vocabulary_scan_found_the_states(self) -> None:
        """A scan that silently matched nothing would satisfy every assertion below."""
        self.assertIn("running", self.live_states())
        self.assertIn("awaiting-developer", self.live_states())
        self.assertNotIn("completed", self.live_states())

    def test_every_live_state_has_a_metrics_bucket(self) -> None:
        unbucketed = [
            state
            for state in self.live_states()
            if state_count_field(state) not in Metrics.model_fields
        ]
        self.assertEqual(
            unbucketed,
            [],
            f"lifecycle state(s) {unbucketed} inflate Metrics.lifecycleCount and "
            "Metrics.totalTokens but land in no bucket; declare "
            f"{[state_count_field(state) for state in unbucketed]} on Metrics",
        )

    def test_the_metrics_buckets_are_exactly_the_live_states(self) -> None:
        """The other direction: a bucket no state can fill is a rollup that outlived its state.

        ``lifecycleCount`` is excluded by name because it is the all-states total, not a
        bucket -- the one ``*Count`` field that is deliberately not per-state.
        """
        declared = {name for name in Metrics.model_fields if name.endswith("Count")} - {
            "lifecycleCount"
        }
        self.assertEqual(declared, {state_count_field(state) for state in self.live_states()})

    def test_every_live_state_is_actually_counted(self) -> None:
        """The fields existing is not the same as the reducer filling them.

        One lifecycle per live state, through the real ``project_workspace``: every bucket must
        read 1 and the total must be the number of live states. A declared state that the fold
        cannot even be driven into is reported as such rather than silently skipped.
        """
        logs = [self._log(state) for state in self.live_states()]
        proj = project_workspace(
            logs, structure=WorkspaceStructure(enclosures=[], providers=[]), now=FRESH
        )
        self.assertEqual(
            sorted(lc.state for lc in proj.lifecycles),
            sorted(self.live_states()),
            "an event log did not drive its lifecycle into the state it names",
        )
        metrics = proj.metrics.model_dump()
        for state in self.live_states():
            with self.subTest(state=state):
                self.assertEqual(metrics[state_count_field(state)], 1)
        self.assertEqual(proj.metrics.lifecycleCount, len(self.live_states()))

    def test_an_awaiting_developer_lifecycle_is_no_longer_uncountable(self) -> None:
        """The reported symptom, pinned: the turn-end handoff was in the total and nowhere else."""
        proj = project_workspace(
            [self._log("awaiting-developer")],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertEqual(proj.metrics.lifecycleCount, 1)
        self.assertEqual(proj.metrics.awaitingDeveloperCount, 1)
        self.assertEqual(proj.metrics.runningCount, 0)
        self.assertEqual(proj.metrics.blockedCount, 0)
        self.assertEqual(proj.metrics.pausedCount, 0)

    def _log(self, state: str) -> list[Event]:
        """A log that ends with its lifecycle in ``state``.

        ``lifecycle.started`` seeds ``running``; every other live state is declared by the
        event kind of the same name (``lifecycle.blocked``, ``lifecycle.paused``,
        ``lifecycle.awaiting-developer``), so this is derived from the state rather than a
        fourth table keyed by it.
        """
        started = _started(lifecycle_id=f"LC-{state}")
        if state == "running":
            return [started]
        return [
            started,
            _event(
                f"lifecycle.{state}",
                lifecycle_id=f"LC-{state}",
                ts="2026-06-13T18:00:10+00:00",
            ),
        ]


class StatePartitionTests(unittest.TestCase):
    """``State`` is exactly its live half plus its terminal half -- nothing filed twice, nothing
    unfiled, nothing filed that does not exist.

    Deriving the bucket set as "the vocabulary minus ``TERMINAL_STATES``" only moves the
    hand-written list one level down unless ``TERMINAL_STATES`` is itself tied to the
    vocabulary. It is: ``State`` is COMPOSED from the two halves, so the halves are the
    vocabulary rather than a second opinion about it. The one way to break that -- append a
    bare literal to the composition -- is what these hold, using synthetic vocabularies so the
    guard is exercised without the real declaration having to be wrong.
    """

    LIVE = Literal["running", "paused"]
    TERMINAL = Literal["completed"]

    def test_the_real_partition_is_total_and_disjoint(self) -> None:
        self.assertEqual(set(STATES), set(LIVE_STATES) | TERMINAL_STATES)
        self.assertEqual(set(LIVE_STATES) & TERMINAL_STATES, set())
        self.assertEqual(len(STATES), len(LIVE_STATES) + len(TERMINAL_STATES))

    def test_the_live_half_is_the_metrics_bucket_set_verbatim(self) -> None:
        """``ACTIVE_STATES`` is the live half itself, not a re-subtraction that could differ."""
        self.assertEqual(ACTIVE_STATES, LIVE_STATES)

    def test_a_state_filed_on_neither_side_is_refused_by_name(self) -> None:
        """The reviewer's mutation: a seventh state added to ``State`` alone.

        Previously this shipped -- the state joined ``lifecycleCount`` and ``totalTokens`` and
        no bucket, exactly the original defect. Now the composition itself rejects it, so it
        cannot reach a served number at all.
        """
        with self.assertRaises(LifecycleVocabularyError) as caught:
            check_state_partition(
                live=self.LIVE,
                terminal=self.TERMINAL,
                whole=Literal[self.LIVE, self.TERMINAL, "superseded"],
            )
        self.assertIn("superseded", str(caught.exception))
        self.assertIn("neither live nor terminal", str(caught.exception))

    def test_a_state_filed_on_both_sides_is_refused(self) -> None:
        with self.assertRaises(LifecycleVocabularyError) as caught:
            check_state_partition(
                live=Literal["running", "completed"],
                terminal=self.TERMINAL,
                whole=Literal["running", "completed"],
            )
        self.assertIn("completed", str(caught.exception))
        self.assertIn("both live and terminal", str(caught.exception))

    def test_a_filed_state_missing_from_the_vocabulary_is_refused(self) -> None:
        """The other drift direction: a half that outgrew the vocabulary it partitions."""
        with self.assertRaises(LifecycleVocabularyError) as caught:
            check_state_partition(
                live=self.LIVE, terminal=Literal["completed", "archived"], whole=Literal[self.LIVE]
            )
        self.assertIn("archived", str(caught.exception))

    def test_a_well_formed_partition_returns_the_vocabulary_in_declaration_order(self) -> None:
        self.assertEqual(
            check_state_partition(
                live=self.LIVE, terminal=self.TERMINAL, whole=Literal[self.LIVE, self.TERMINAL]
            ),
            ("running", "paused", "completed"),
        )


class TerminalityIsStructuralTests(unittest.TestCase):
    """Filing a state terminal is a claim about the reducer, and these hold it to it.

    Totality (:class:`StatePartitionTests`) stops a state from escaping classification; it
    cannot stop a state from being classified WRONGLY, and a mis-filed state is the same
    defect wearing the fix. So terminality is given an observable definition and checked
    against the fold in both directions: a terminal state is one the log reaches ONLY through
    ``lifecycle.ended``, and a live state is one some event kind declares outright. A live
    state mis-filed as terminal keeps its declaring kind and fails here; a terminal state
    mis-filed as live has no declaring kind and fails here.
    """

    def seeded_state(self) -> str:
        """The state a bare ``lifecycle.started`` log projects into.

        The one live state no declaring event kind owns, obtained by asking the fold rather
        than by naming it -- a second seeded state would have to be listed, and is not.
        """
        return project_lifecycle([_started()], now=FRESH).state

    def test_the_seeded_state_is_live(self) -> None:
        """A lifecycle that begins already over would make everything below vacuous."""
        self.assertIn(self.seeded_state(), LIVE_STATES)

    def test_every_terminal_state_is_reached_by_ending_with_its_own_name(self) -> None:
        """``lifecycle.ended``'s outcome IS the terminal state: no mapping table between them."""
        for state in sorted(TERMINAL_STATES):
            with self.subTest(state=state):
                proj = project_lifecycle(self._ended_log(state), now=FRESH)
                self.assertEqual(proj.state, state)
                self.assertEqual(coerce_end_outcome(state), state)

    def test_no_terminal_state_is_declared_by_an_event_kind_of_its_own(self) -> None:
        """Ending is the only door in.

        A state with a ``lifecycle.<state>`` kind is something a running session announces
        about itself -- that is what live means. Filing such a state as terminal drops it from
        the rollup and from the attention queue while its session is still working, which is
        the defect this leaf exists to remove, one level down.
        """
        declared = sorted(
            state for state in TERMINAL_STATES if f"lifecycle.{state}" in _KIND_UPDATES
        )
        self.assertEqual(
            declared,
            [],
            f"state(s) {declared} are filed terminal but a session can declare them mid-run; "
            "file them under LiveState or drop the event kind",
        )

    def test_every_live_state_is_declared_or_seeded(self) -> None:
        """The mirror: a live state a log cannot be driven into is history filed as work."""
        undeclarable = sorted(
            state
            for state in LIVE_STATES
            if state != self.seeded_state() and f"lifecycle.{state}" not in _KIND_UPDATES
        )
        self.assertEqual(
            undeclarable,
            [],
            f"state(s) {undeclarable} are filed live but nothing can declare them; "
            f"only {self.seeded_state()!r} is reached by seeding",
        )

    def test_no_live_state_is_reachable_by_ending(self) -> None:
        for state in LIVE_STATES:
            with self.subTest(state=state):
                self.assertEqual(coerce_end_outcome(state), DEFAULT_END_OUTCOME)
                self.assertNotEqual(
                    project_lifecycle(self._ended_log(state), now=FRESH).state, state
                )

    def test_is_terminal_reads_the_same_partition(self) -> None:
        """``LifecycleState.is_terminal`` and the projection must not disagree about a state."""
        for state in STATES:
            with self.subTest(state=state):
                record = LifecycleState(
                    id="LC1", state=state, phase="build", fleeting=True, started_at=T0
                )
                self.assertEqual(record.is_terminal, state in TERMINAL_STATES)

    def test_an_unrecognized_end_outcome_abandons_rather_than_completes(self) -> None:
        """Preserved from the hand-written conditional this replaced, including for junk."""
        for outcome in ("shipped", "", None, {"outcome": "completed"}):
            with self.subTest(outcome=outcome):
                self.assertEqual(coerce_end_outcome(outcome), DEFAULT_END_OUTCOME)

    def test_the_ambient_end_signal_accepts_exactly_the_terminal_states(self) -> None:
        """The write side and the read side agree about which states ending can produce.

        ``AmbientLifecycle.end`` now validates against ``TERMINAL_STATES`` and converts through
        ``coerce_end_outcome`` rather than against a literal pair of its own (the structure of
        that is pinned by ``EndSignalVocabularyTests`` in ``test_observer_ambient.py``); this
        holds the behaviour, so a terminal state the reducer can project but no session can
        write still fails here whatever the write side is spelled in.
        """
        for state in STATES:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                ambient = AmbientLifecycle(
                    EventStore(Path(tmp)), timing=AmbientTiming(heartbeat_seconds=3600)
                )
                ambient.start()
                if state in TERMINAL_STATES:
                    ended = ambient.end(state)
                    self.assertEqual(ended.state, state)
                    self.assertTrue(ended.is_terminal)
                    self.assertIsNone(ambient.current, "ending must clear the ambient lifecycle")
                else:
                    with self.assertRaises(LifecycleError):
                        ambient.end(state)
                    self.assertIsNotNone(ambient.current)
                    ambient.end("abandoned")

    def _ended_log(self, outcome: str) -> list[Event]:
        return [
            _started(),
            _event("lifecycle.ended", ts="2026-06-13T18:00:10+00:00", outcome=outcome),
        ]


class StateVocabularyReaderTests(unittest.TestCase):
    """``vocabulary_names`` reads every legal declaration form and refuses the rest by name.

    ``get_args`` alone is only correct for a FLAT ``Literal``. On the union form --
    ``Literal[...] | ReviewState``, a plausible way to fold a second vocabulary in -- it
    returns ``Literal`` objects, and the first consumer to call ``.split`` on one dies with
    ``AttributeError`` at import of ``agents_remember.observer``: the whole package goes down
    and the traceback names none of this. These pin the reading, not the crash.
    """

    def test_a_flat_literal_reads_as_strings_in_order(self) -> None:
        self.assertEqual(vocabulary_names(Literal["b", "a"], label="x"), ("b", "a"))

    def test_a_composition_of_aliases_flattens(self) -> None:
        self.assertEqual(vocabulary_names(Literal[LiveState, TerminalState], label="x"), STATES)

    def test_the_union_form_reads_as_strings_rather_than_literal_objects(self) -> None:
        names = vocabulary_names(Literal["running"] | Literal["reviewing", "closed"], label="x")
        self.assertEqual(names, ("running", "reviewing", "closed"))
        for name in names:
            self.assertIsInstance(name, str)

    def test_a_non_string_member_is_refused_by_name(self) -> None:
        with self.assertRaises(LifecycleVocabularyError) as caught:
            vocabulary_names(Literal["running"] | None, label="State")
        self.assertIn("State contains", str(caught.exception))

    def test_an_empty_declaration_is_refused(self) -> None:
        with self.assertRaises(LifecycleVocabularyError) as caught:
            vocabulary_names(Literal[()], label="State")
        self.assertIn("declares no members", str(caught.exception))

    def test_the_real_declarations_read_as_plain_strings(self) -> None:
        """A vocabulary of ``Literal`` objects would compare equal to no event payload ever."""
        for name in (*STATES, *LIVE_STATES, *TERMINAL_STATES):
            self.assertIsInstance(name, str)


class StateCountFieldTests(unittest.TestCase):
    """The state -> bucket-field naming rule: one-to-one, and the same rule the client uses."""

    def test_the_rule_camel_cases_the_hyphenated_name(self) -> None:
        self.assertEqual(state_count_field("running"), "runningCount")
        self.assertEqual(state_count_field("awaiting-developer"), "awaitingDeveloperCount")

    def test_a_capital_in_the_tail_survives(self) -> None:
        """Matches the TypeScript mirror, which cannot do otherwise.

        ``dashboard/src/types/projection.ts`` derives the field NAMES with
        ``Capitalize<Camel<Tail>>``, a type-level operation that upper-cases the first
        character and leaves the rest alone; its runtime ``stateCountField`` matches it. Python
        used ``str.capitalize``, which lower-cases the tail -- so the two copies of one rule
        disagreed on ``awaiting-DEVELOPER`` (``awaitingDeveloperCount`` vs
        ``awaitingDEVELOPERCount``). TypeScript's is the rule that can be expressed on both
        sides, so Python moved.
        """
        self.assertEqual(state_count_field("awaiting-DEVELOPER"), "awaitingDEVELOPERCount")
        self.assertEqual(state_count_field("awaiting-Developer"), "awaitingDeveloperCount")

    def test_the_real_bucket_fields_are_distinct(self) -> None:
        fields = list(STATE_COUNT_FIELDS.values())
        self.assertEqual(len(set(fields)), len(fields))
        self.assertEqual(set(STATE_COUNT_FIELDS), set(ACTIVE_STATES))

    def test_two_states_sharing_a_bucket_are_refused_rather_than_merged(self) -> None:
        """``Metrics`` is keyed by field, so a collision loses a count with nothing to see.

        The reviewer's case (``awaiting-Developer`` beside ``awaiting-developer``) produced
        three lifecycles, ``awaitingDeveloperCount: 1``, and a green vocabulary suite: the
        coverage tests compare per-state values that both resolve to the merged field, so they
        agree with themselves. The map refuses to be built instead.
        """
        for clashing in (
            ("awaiting-developer", "awaiting-Developer"),
            ("awaiting-developer", "awaitingDeveloper"),
        ):
            with self.subTest(clashing=clashing):
                with self.assertRaises(LifecycleVocabularyError) as caught:
                    state_count_fields(clashing)
                message = str(caught.exception)
                self.assertIn("awaitingDeveloperCount", message)
                for state in clashing:
                    self.assertIn(repr(state), message)

    def test_a_merged_bucket_would_have_under_counted_the_rollup(self) -> None:
        """Why the refusal is worth an exception: the number the dashboard reads is wrong.

        Built by hand the way ``_metrics`` builds it -- keyed by bucket -- two states that
        share a field yield ONE entry and the later count silently wins.
        """
        counts = {"awaiting-developer": 2, "awaiting-Developer": 1}
        merged = {state_count_field(state): count for state, count in counts.items()}
        self.assertEqual(merged, {"awaitingDeveloperCount": 1})
        self.assertNotEqual(sum(merged.values()), sum(counts.values()))
