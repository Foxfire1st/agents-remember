"""Analytical rollups: token series, staleness histogram, and workspace metrics.

The reducer's slice-3b rollups: the bounded cumulative token series, the
verification-age histogram, the workspace metrics rollup, and the analytics
assembly. All remain pure functions of already-read inputs.
"""

from __future__ import annotations

from collections import Counter

from agents_remember.observer.events import Event
from agents_remember.observer.projection import (
    STATE_COUNT_FIELDS,
    Analytics,
    AttentionItem,
    EngineProcessNode,
    LifecycleProjection,
    Metrics,
    SeriesNode,
    SidecarStaleNode,
    TokenSample,
)
from agents_remember.observer.reducer_impl._types import AnalyticalInputs


def _metrics(
    lifecycles: list[LifecycleProjection],
    sidecars: list[SidecarStaleNode] | None = None,
) -> Metrics:
    """The workspace rollup: the all-states totals plus one bucket per live state.

    The buckets come from :data:`STATE_COUNT_FIELDS`, not from a line per state. Three
    hand-written ``sum(1 for lc in lifecycles if lc.state == ...)`` lines are what made a
    lifecycle that had handed the turn back count towards ``lifecycleCount`` and
    ``totalTokens`` and towards nothing else. Counting by vocabulary also makes the next gap
    impossible to ship quietly: ``Metrics`` is ``extra="forbid"``, so a state whose bucket
    field was never declared raises here instead of vanishing into a zero.

    The keyword expansion below is keyed by BUCKET, so it would silently drop a count if two
    states shared one -- which is why :func:`state_count_fields` refuses to build a map that
    is not one-to-one rather than leaving the loss to be noticed in a served number.
    """
    counts = Counter(lc.state for lc in lifecycles)
    return Metrics(
        lifecycleCount=len(lifecycles),
        totalTokens=sum(lc.tokens for lc in lifecycles),
        stalenessHistogram=staleness_histogram(sidecars) if sidecars else {},
        **{bucket: counts[state] for state, bucket in STATE_COUNT_FIELDS.items()},
    )


# --- analytical rollups (slice 3b) -------------------------------------------


# The served fuel-gauge bound (260703-L15): one sample per token-bearing ``tool.completed``
# event is UNBOUNDED over a long lifecycle (~60 wire bytes/sample), and the lifecycle node
# re-emits on every real change while its agent works -- an uncapped series multiplies into
# the serving hot path (10k tool calls ≈ 600 KB riding every lifecycle delta). The gauge is
# a chart, so the bound decimates: the newest TOKEN_SERIES_RECENT samples stay exact and the
# older history thins uniformly (first sample always kept) to TOKEN_SERIES_MAX total. The
# observer LOG keeps every event -- this bounds only the served projection.
TOKEN_SERIES_MAX = 512
TOKEN_SERIES_RECENT = 256


def _decimate_token_series(samples: list[TokenSample]) -> list[TokenSample]:
    """Bound the series: uniform-thin the older history, keep the newest window exact."""
    if len(samples) <= TOKEN_SERIES_MAX:
        return samples
    history = samples[:-TOKEN_SERIES_RECENT]
    budget = TOKEN_SERIES_MAX - TOKEN_SERIES_RECENT
    step = len(history) / budget
    thinned = [history[int(index * step)] for index in range(budget)]
    return thinned + samples[-TOKEN_SERIES_RECENT:]


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_metrics.py:78).
def token_series(events: list[Event]) -> list[TokenSample]:  # pragma: no cover
    """Cumulative token spend over time, from the log's ``tool.completed`` events (§2.4).

    The per-lifecycle fuel gauge: gap #2 ("no token-spend persistence") is closed by
    the event substrate, so the running total is a pure fold of the log. Served bounded
    (260703-L15): past :data:`TOKEN_SERIES_MAX` points the older history is decimated
    (shape- and total-preserving -- ``cumulative`` stays exact per retained sample) while
    the newest :data:`TOKEN_SERIES_RECENT` samples stay complete.
    """
    total = 0
    samples: list[TokenSample] = []
    for event in events:
        if event.kind != "tool.completed":
            continue
        tokens = event.data.get("tokens")
        if isinstance(tokens, int):
            total += tokens
            samples.append(TokenSample(ts=event.ts, cumulative=total))
    return _decimate_token_series(samples)


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
    computed from the same inputs (series carries token totals the raw nodes lack), so they
    override what ``given`` holds rather than being read out of it.
    """
    return Analytics(
        driftSnapshots=given.drift_snapshots,
        stalestSidecars=_stalest(given.sidecar_staleness, given.stalest_limit),
        setupSummaries=given.setup_summaries,
        setupProgress=given.setup_progress,
        routeCoverage=given.route_coverage,
        toolReports=given.tool_reports,
        agentPickups=given.agent_pickups,
        expectationRows=given.expectation_rows,
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
