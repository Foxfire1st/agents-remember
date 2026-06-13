"""The projection schema: the resolved state the reducer produces.

These are the *persisted and served* contract -- written atomically to
``latest-state.json`` / ``latest-metrics.json`` and (slice 04) streamed over
SSE. Like :mod:`agents_remember.observer.events` they are deliberately **not**
MCP response models: they carry no token-accounting fields, are never returned
by a tool, and are absent from ``PUBLIC_TOOL_RESPONSE_MODELS``. Fields are
camelCase to match the package's wire convention; ``extra="forbid"`` keeps the
served contract honest.

The shapes are client-agnostic (North-Star #2): a dashboard, a future TUI, and
an orchestrating agent are equal clients. The state tree is expressed as linked
flat collections (lifecycles, enclosures, providers) cross-referenced by id
(``enclosure.lifecycleId``, ``provider.repoId``) rather than deep nesting --
friendlier to every client than one nested document, and it keeps multi-repo
enclosures (North-Star #4) from being keyed to a single repo.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.observer.lifecycle_state import Phase, State


class ActionAvailability(BaseModel):
    """Whether one action is currently safe -- decided by the reducer, never the UI.

    ``disabledReason`` and ``nextSafeAction`` are load-bearing for slice 06: the
    cockpit renders the affordance and its tooltip straight from this and never
    infers safety client-side.
    """

    model_config = ConfigDict(extra="forbid")

    action: str
    enabled: bool
    disabledReason: str | None = None
    nextSafeAction: str | None = None


class LifecycleProjection(BaseModel):
    """One lifecycle's resolved state, folded from its event log.

    Fleeting lifecycles project as bare-bones entries (no ``enclosure``);
    persistent ones carry the full enclosure detail -- the slice-05 visual
    distinction falls out of this shape, not UI inference.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    state: State
    phase: Phase
    fleeting: bool
    enclosure: str | None = None
    repoId: str | None = None
    scope: str | None = None
    tokens: int = 0
    startedAt: str
    lastEventTs: str
    staleSeconds: float | None = None
    # True when ``state`` was *derived* by the reducer (stale heartbeat -> paused;
    # dormant fleeting -> abandoned) rather than read from a written transition.
    # Keeps "never pretend declared is observed" enforceable from the data alone.
    inferred: bool = False
    # The latest open block ask (the proto-gate); slice 06 materializes the record.
    ask: dict[str, Any] | None = None
    actions: list[ActionAvailability] = Field(default_factory=list)


class EnclosureNode(BaseModel):
    """A worktree enclosure (contract + group): the persistent identity anchor.

    ``lifecycleId`` cross-references the lifecycle this enclosure anchors (it is
    ``""`` for a legacy contract written before the lifecycle field existed).
    """

    model_config = ConfigDict(extra="forbid")

    enclosure: str
    taskId: str
    taskName: str
    repoName: str
    lifecycleId: str
    worktreeGroup: str
    humanReviewStatus: str
    closeoutStatus: str
    integrationStatus: str
    cleanup: str
    actions: list[ActionAvailability] = Field(default_factory=list)


class ProviderNode(BaseModel):
    """One provider's current-state snapshot (data surface 1).

    ``snapshotStaleSeconds`` is the age of the ``current.json`` file: provider
    state is call-triggered and stale between calls, so the reducer surfaces how
    old the snapshot is rather than pretending it is live.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    ok: bool | None = None
    watcherUp: bool = False
    indexingState: str = "unknown"
    snapshotStaleSeconds: float | None = None


class Metrics(BaseModel):
    """Workspace rollups. 3a keeps point counts; 3b adds the derived time series."""

    model_config = ConfigDict(extra="forbid")

    lifecycleCount: int = 0
    runningCount: int = 0
    blockedCount: int = 0
    pausedCount: int = 0
    totalTokens: int = 0


class WorkspaceProjection(BaseModel):
    """The whole resolved tree: lifecycles + enclosures + providers + metrics."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    generatedAt: str
    lifecycles: list[LifecycleProjection] = Field(default_factory=list)
    enclosures: list[EnclosureNode] = Field(default_factory=list)
    providers: list[ProviderNode] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
