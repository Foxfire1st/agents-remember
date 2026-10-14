"""Analytical rollups: staleness histogram and workspace metrics.

The reducer's slice-3b rollups: the verification-age histogram, the workspace
metrics rollup, and the analytics assembly. All remain pure functions of
already-read inputs.
"""

from __future__ import annotations

from collections import Counter

from agents_remember.observer.projection import (
    STATE_COUNT_FIELDS,
    Analytics,
    AttentionItem,
    EngineProcessNode,
    LifecycleProjection,
    Metrics,
    SeriesNode,
    SidecarStaleNode,
)
from agents_remember.observer.reducer_impl._types import AnalyticalInputs


def _metrics(
    lifecycles: list[LifecycleProjection],
    sidecars: list[SidecarStaleNode] | None = None,
) -> Metrics:
    """The workspace rollup: the all-states totals plus one bucket per live state.

    The buckets come from :data:`STATE_COUNT_FIELDS`, not from a line per state. Three
    hand-written ``sum(1 for lc in lifecycles if lc.state == ...)`` lines are what made a
    lifecycle that had handed the turn back count towards ``lifecycleCount``
    and towards nothing else. Counting by vocabulary also makes the next gap
    impossible to ship quietly: ``Metrics`` is ``extra="forbid"``, so a state whose bucket
    field was never declared raises here instead of vanishing into a zero.

    The keyword expansion below is keyed by BUCKET, so it would silently drop a count if two
    states shared one -- which is why :func:`state_count_fields` refuses to build a map that
    is not one-to-one rather than leaving the loss to be noticed in a served number.
    """
    counts = Counter(lc.state for lc in lifecycles)
    return Metrics(
        lifecycleCount=len(lifecycles),
        stalenessHistogram=staleness_histogram(sidecars) if sidecars else {},
        **{bucket: counts[state] for state, bucket in STATE_COUNT_FIELDS.items()},
    )


# --- analytical rollups (slice 3b) -------------------------------------------


# Upper bound (exclusive, seconds) per verification-age bucket; the final bucket is
# open-ended and a node with no parseable date falls into "unknown".
_STALENESS_BUCKETS: tuple[tuple[str, float | None], ...] = (
    ("<7d", 7 * 86400.0),
    ("7-30d", 30 * 86400.0),
    ("30-90d", 90 * 86400.0),
    (">90d", None),
)


def staleness_histogram(nodes: list[SidecarStaleNode]) -> dict[str, int]:
    """Bucket sidecars by verification age (surface 11 rollup; unparseable -> unknown)."""
    buckets = {label: 0 for label, _ in _STALENESS_BUCKETS}
    buckets["unknown"] = 0
    for node in nodes:
        buckets[_staleness_bucket(node.ageSeconds)] += 1
    return buckets


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_metrics.py:118).
def _staleness_bucket(age: float | None) -> str:  # pragma: no cover
    if age is None:
        return "unknown"
    for label, upper in _STALENESS_BUCKETS:
        if upper is None or age < upper:
            return label
    return ">90d"


def build_analytics(
    given: AnalyticalInputs,
    *,
    series: list[SeriesNode] | None = None,
    attention_queue: list[AttentionItem] | None = None,
    engine_processes: list[EngineProcessNode] | None = None,
) -> Analytics:
    """Assemble the analytical surfaces; the full sidecar list collapses to a leaderboard.

    ``series``, ``attention_queue`` and ``engine_processes`` are DERIVED surfaces the caller
    computed from the same inputs, so they
    override what ``given`` holds rather than being read out of it.
    """
    return Analytics(
        driftSnapshots=given.drift_snapshots,
        stalestSidecars=_stalest(given.sidecar_staleness, given.stalest_limit),
        setupSummaries=given.setup_summaries,
        setupProgress=given.setup_progress,
        routeCoverage=given.route_coverage,
        toolReports=given.tool_reports,
        ledgers=given.ledgers,
        taskDocuments=given.task_documents,
        series=series if series is not None else given.series,
        attentionQueue=attention_queue or [],
        engineProcesses=engine_processes or [],
    )


def _stalest(nodes: list[SidecarStaleNode], limit: int) -> list[SidecarStaleNode]:
    """The oldest-verified sidecars first; unparseable dates sort last."""
    ranked = sorted(nodes, key=lambda node: (node.ageSeconds is None, -(node.ageSeconds or 0.0)))
    return ranked[:limit]
