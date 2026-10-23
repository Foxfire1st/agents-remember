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
from agents_remember.observer.host_activity import HostActivitySnapshot
from agents_remember.observer.native_attention import NativeAttentionFacts
from agents_remember.observer.projection import (
    CloseoutQueueNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    LedgerNode,
    ProviderNode,
    RouteCoverageNode,
    SetupProgressNode,
    SetupSummaryNode,
    ToolReportNode,
)
from agents_remember.observer.task_entries import TaskDocumentSource, TaskEntries


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
    """Independent analytics plus native task sources and host activity snapshots.

    The task-entry producer owns task membership/state/counts. Retained analytics
    and derived engine/attention surfaces share the same already-read inputs.
    """

    drift_snapshots: list[DriftSnapshotNode] = field(default_factory=list)
    setup_summaries: list[SetupSummaryNode] = field(default_factory=list)
    setup_progress: list[SetupProgressNode] = field(default_factory=list)
    route_coverage: list[RouteCoverageNode] = field(default_factory=list)
    tool_reports: list[ToolReportNode] = field(default_factory=list)
    ledgers: list[LedgerNode] = field(default_factory=list)
    task_sources: list[TaskDocumentSource] = field(default_factory=list)
    host_activity: HostActivitySnapshot = field(default_factory=HostActivitySnapshot)
    closeout_queues: list[CloseoutQueueNode] = field(default_factory=list)
    engine_process_facts: list[EngineProcessFacts] = field(default_factory=list)
    engine_start_progress: list[dict[str, Any]] = field(default_factory=list)
    attention_dismissals: dict[str, AttentionDismissalRecord] = field(default_factory=dict)
    # The five native facts, bounded and complete-or-retained (MIK-R200 rules 1-6).
    native_attention: NativeAttentionFacts = field(default_factory=NativeAttentionFacts)
    # The canonical task list already joined this pass, when the caller built it (L190-R1-F04):
    # the stall clock read the same relation, so the rows must be built from it too.
    task_entries: TaskEntries | None = None
