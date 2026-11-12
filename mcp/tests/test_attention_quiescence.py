"""A quiet attention surface emits nothing; the row that appears or clears emits once.

MIK-R200 rule 9 asks that the five native kinds cost nothing while nothing happens. Two things
can break that: a store re-read on every pass (``test_attention_native_inputs`` counts those),
and a row whose *stable* form moves with the clock -- which the SSE delta compares after the
volatile ages are stripped, so an interpolated minute count would wake every client each minute.
These cases drive the real diff over the real analytics payload (L190-R1-F12).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents_remember.observer.native_attention import STALL_SECONDS, NativeAttentionFacts
from agents_remember.observer.projection import (
    Analytics,
    TaskDocumentRef,
    TaskEntry,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import AnalyticalInputs, build_attention_queue
from agents_remember.serving.delta import diff_projection

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
LEAF_KEY = "repo/master/leaf.json"


def _task() -> TaskEntry:
    return TaskEntry(
        key=LEAF_KEY,
        reference=TaskDocumentRef(repository="repo", path="master/leaf.json"),
        id="leaf",
        title="Leaf",
        stage="started",
        activity="paused",
        eventAt=None,
    )


def _queue_at(now: datetime, start: datetime):
    facts = NativeAttentionFacts(stall_clocks={LEAF_KEY: start.isoformat()})
    return build_attention_queue(_inputs(facts), [_task()], now)


def _inputs(facts: NativeAttentionFacts) -> AnalyticalInputs:
    return AnalyticalInputs(native_attention=facts)


def _projection(now: datetime, start: datetime) -> WorkspaceProjection:
    return WorkspaceProjection(
        generatedAt=now.isoformat(),
        tasks=[_task()],
        analytics=Analytics(attentionQueue=_queue_at(now, start)),
    )


def test_quiet_minutes_emit_no_delta_and_the_threshold_emits_one() -> None:
    # The row is already published: it crossed the deadline a minute ago. One interval, one
    # identity (v2 rule 7) -- only the pass clock moves as the minutes pass.
    start = NOW - timedelta(seconds=STALL_SECONDS + 60)
    before = _projection(NOW, start)

    quiet = diff_projection(before, _projection(NOW + timedelta(minutes=1), start))
    assert quiet == []

    quiet_again = diff_projection(before, _projection(NOW + timedelta(minutes=7), start))
    assert quiet_again == []

    # Entry at the deadline is a membership change, and it is emitted exactly once.
    below = _projection(NOW, NOW - timedelta(seconds=STALL_SECONDS - 1))
    entered = diff_projection(below, _projection(NOW, NOW - timedelta(seconds=STALL_SECONDS)))
    assert [event.event for event in entered] == ["analytics"]

    # Clearing is a membership change too.
    cleared = diff_projection(_projection(NOW, start), _projection(NOW, NOW))
    assert [event.event for event in cleared] == ["analytics"]
