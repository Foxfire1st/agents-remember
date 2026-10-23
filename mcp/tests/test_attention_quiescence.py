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


def _queue_at(now: datetime, started_ago: float):
    facts = NativeAttentionFacts(
        stall_clocks={LEAF_KEY: (NOW - timedelta(seconds=started_ago)).isoformat()}
    )
    return build_attention_queue([], _inputs(facts), [_task()], now)


def _inputs(facts: NativeAttentionFacts) -> AnalyticalInputs:
    return AnalyticalInputs(native_attention=facts)


def _projection(now: datetime, started_ago: float) -> WorkspaceProjection:
    return WorkspaceProjection(
        generatedAt=now.isoformat(),
        tasks=[_task()],
        analytics=Analytics(attentionQueue=_queue_at(now, started_ago)),
    )


def test_quiet_minutes_emit_no_delta_and_the_threshold_emits_one() -> None:
    # The row is already published: it crossed the deadline a minute ago.
    before = _projection(NOW, STALL_SECONDS + 60)

    quiet = diff_projection(before, _projection(NOW + timedelta(minutes=1), STALL_SECONDS + 120))
    assert quiet == []

    quiet_again = diff_projection(
        before, _projection(NOW + timedelta(minutes=7), STALL_SECONDS + 480)
    )
    assert quiet_again == []

    # Entry at the deadline is a membership change, and it is emitted exactly once.
    below = _projection(NOW, STALL_SECONDS - 1)
    entered = diff_projection(below, _projection(NOW, STALL_SECONDS))
    assert [event.event for event in entered] == ["analytics"]

    # Clearing is a membership change too.
    cleared = diff_projection(_projection(NOW, STALL_SECONDS + 5), _projection(NOW, 0))
    assert [event.event for event in cleared] == ["analytics"]
