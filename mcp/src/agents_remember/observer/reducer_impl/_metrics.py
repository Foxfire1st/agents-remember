"""Assemble retained analytics from already-read inputs."""

from __future__ import annotations

from agents_remember.observer.projection import (
    Analytics,
    AttentionItem,
    EngineProcessNode,
)
from agents_remember.observer.reducer_impl._types import AnalyticalInputs


def build_analytics(
    given: AnalyticalInputs,
    *,
    attention_queue: list[AttentionItem] | None = None,
    engine_processes: list[EngineProcessNode] | None = None,
) -> Analytics:
    """Assemble the retained analytical surfaces.

    ``attention_queue`` and ``engine_processes`` are derived surfaces the caller
    computed from the same inputs, rather than being read out of ``given``.
    """
    return Analytics(
        driftSnapshots=given.drift_snapshots,
        routeCoverage=given.route_coverage,
        toolReports=given.tool_reports,
        ledgers=given.ledgers,
        attentionQueue=attention_queue or [],
        engineProcesses=engine_processes or [],
    )
