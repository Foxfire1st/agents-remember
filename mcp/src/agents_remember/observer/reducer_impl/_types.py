"""Input bundles shared by the reducer's assembly and its split builders.

``WorkspaceStructure`` is the slice-3a pre-image (enclosures + providers +
admitted worktree groups); ``AnalyticalInputs`` is the slice-3b pre-image the
analytical surfaces are built from. Both are the design's own two slices, so
they live together and are re-exported by :mod:`agents_remember.observer.reducer`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents_remember.controlplane.attention_dismissals import AttentionDismissalRecord
from agents_remember.controlplane.records import GateRecord
from agents_remember.observer.projection import (
    AgentPickupNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    ExpectationRowNode,
    LedgerNode,
    ProviderNode,
    RouteCoverageNode,
    SeriesNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    TaskDocNode,
    ToolReportNode,
)


@dataclass(frozen=True)
class WorkspaceStructure:
    """The workspace as it exists (slice 3a): the enclosures and providers already read from
    disk, plus the worktree-group admission set the Topology constellation and the Engine Room
    share one definition of.

    ``active_worktree_groups`` belongs here rather than in :class:`AnalyticalInputs`, where it
    used to sit. It is not analytical -- it leaves as ``WorkspaceProjection.activeWorktreeGroups``,
    a structural field beside ``enclosures`` and ``providers``, and never reaches ``Analytics``
    at all. It was the one member of that bundle whose name was not true of it.
    """

    enclosures: list[EnclosureNode]
    providers: list[ProviderNode]
    active_worktree_groups: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalyticalInputs:
    """The pre-image of :class:`Analytics` (slice 3b): what the analytical surfaces are built
    from, and nothing else.

    Broad because ``Analytics`` is broad. It publishes thirteen independent surfaces, and these
    sixteen fields are exactly what produces them. Ten map to one surface by name
    (``drift_snapshots`` -> ``driftSnapshots``, ``route_coverage`` -> ``routeCoverage``,
    ``ledgers`` -> ``ledgers``, ...); the other six produce the three DERIVED surfaces, in
    pairs -- ``sidecar_staleness`` + ``stalest_limit`` -> ``stalestSidecars``,
    ``engine_process_facts`` + ``engine_start_progress`` -> ``engineProcesses``, ``gates`` +
    ``attention_dismissals`` -> ``attentionQueue``.

    So there is no sub-concept left to split out. The surfaces are independent cockpit panels
    with nothing binding any subset of them, which means a grouping here would have to be a
    grouping of ``Analytics`` itself -- a change to the projection contract the dashboard
    reads, not a tidy-up. The one field that DID break the name has been moved:
    ``active_worktree_groups`` is structural and now lives in :class:`WorkspaceStructure`.

    Optional as a set, not one by one: a caller wanting only the structural tree (the 3a
    contract) passes none of them. Several feed more than one surface -- ``setup_progress``
    alone is read by the Engine Room join, the analytics rollup and the attention queue -- so
    the whole set is threaded to each builder rather than re-selected per call. Two also leave
    through a second door: ``sidecar_staleness`` is counted into
    ``WorkspaceProjection.metrics``, and ``gates`` is materialized onto ``lifecycle.gate``.
    """

    drift_snapshots: list[DriftSnapshotNode] = field(default_factory=list)
    sidecar_staleness: list[SidecarStaleNode] = field(default_factory=list)
    setup_summaries: list[SetupSummaryNode] = field(default_factory=list)
    setup_progress: list[SetupProgressNode] = field(default_factory=list)
    route_coverage: list[RouteCoverageNode] = field(default_factory=list)
    tool_reports: list[ToolReportNode] = field(default_factory=list)
    agent_pickups: list[AgentPickupNode] = field(default_factory=list)
    expectation_rows: list[ExpectationRowNode] = field(default_factory=list)
    ledgers: list[LedgerNode] = field(default_factory=list)
    task_documents: list[TaskDocNode] = field(default_factory=list)
    series: list[SeriesNode] = field(default_factory=list)
    engine_process_facts: list[EngineProcessFacts] = field(default_factory=list)
    engine_start_progress: list[dict[str, Any]] = field(default_factory=list)
    gates: list[GateRecord] = field(default_factory=list)
    attention_dismissals: dict[str, AttentionDismissalRecord] = field(default_factory=dict)
    stalest_limit: int = 10
