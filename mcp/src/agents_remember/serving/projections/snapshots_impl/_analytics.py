"""Analytical file-surface readers: drift, setup, routes, tools, ledger.

The observers' slice-3b readers plus the shared ledger-window enrichment used by
the engine-process facts and the official ledger surface. Every reader reuses
the producing subsystem's own parser rather than re-parsing.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.controlplane.attention_dismissals import AttentionObservation
from agents_remember.controlplane.stamps import age_seconds
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import LedgerError, LedgerRow, load_ledger
from agents_remember.observer.projection import (
    LEDGER_WINDOW,
    DriftSnapshotNode,
    LedgerNode,
    LedgerRefNode,
    RouteCoverageNode,
    SetupProgressNode,
    SetupSummaryNode,
    ToolReportNode,
)
from agents_remember.observer.reducer_impl._attention import observe_setup, observe_start
from agents_remember.providers.setup_progress import progress_status, read_setup_progress
from agents_remember.serving.projections.drift_snapshots import read_drift_snapshot
from agents_remember.serving.projections.paths import (
    drift_snapshot_dir,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _as_float,
    _as_int,
    _current_phase_text,
    _file_age_seconds,
    _read_json,
    _report_label,
    _text_or_none,
    surface_paths,
)
from agents_remember.worktrees.start_progress import read_start_progress


def read_start_progress_entries(
    coordination_root: Path, *, now: datetime, observation: AttentionObservation | None = None
) -> list[dict[str, Any]]:  # pragma: no cover
    """Slice 5e §5.4: pre-contract worktree-start blocks (a start gated before its contract).

    Reads the transient ``temp/worktree-start/<repo>/<worktree>.json`` files ``start.py`` writes
    when a start blocks before writing its contract, stamping each with the heartbeat age. A start
    that reached its contract has had this file cleared, so these are exactly the starts the
    contract-keyed enclosure surface cannot see.
    """
    root = coordination_root / "temp" / "worktree-start"
    entries: list[dict[str, Any]] = []
    for path in surface_paths(root, "*/*.json", observation):
        payload = read_start_progress(path)
        if payload is None:
            if observation is not None:
                observation.complete = False
            continue
        observe_start(payload, observation)
        updated = payload.get("updatedAt")
        entries.append(
            {
                **payload,
                "sourceFile": path.as_posix(),
                "ageSeconds": age_seconds(updated, now) if isinstance(updated, str) else None,
            }
        )
    return entries


def read_drift_snapshots(
    coordination_root: Path, *, now: datetime, observation: AttentionObservation | None = None
) -> list[DriftSnapshotNode]:
    """Surface 9 (b1): the persisted drift snapshots the memory_quality run writes.

    A cheap read with a staleness age -- the reducer never classifies drift itself
    (git-per-sidecar). A snapshot whose schema does not match is skipped.
    """
    directory = drift_snapshot_dir(coordination_root)
    nodes: list[DriftSnapshotNode] = []
    for path in surface_paths(directory, "*.json", observation):
        payload = read_drift_snapshot(path)
        if payload is None:
            if observation is not None:
                observation.complete = False
            continue
        raw_counts = payload.get("counts")
        counts = (
            {str(key): _as_int(value) for key, value in raw_counts.items()}
            if isinstance(raw_counts, dict)
            else {}
        )
        checked = payload.get("checkedAt")
        if (
            observation is not None
            and "actionableFingerprint" in payload
            and not payload["actionableFingerprint"]
        ):
            observation.complete = False
        node = DriftSnapshotNode(
            repository=str(payload.get("repository", "")),
            branch=str(payload.get("branch", "")),
            counts=counts,
            actionableCount=_as_int(payload.get("actionableCount")),
            actionableFingerprint=_text_or_none(payload.get("actionableFingerprint")),
            checkedAt=checked if isinstance(checked, str) else None,
            sourceRoot=_text_or_none(payload.get("sourceRoot")),
            memoryRoot=_text_or_none(payload.get("memoryRoot")),
            reportPath=_text_or_none(payload.get("reportPath")),
            snapshotStaleSeconds=age_seconds(checked, now) if isinstance(checked, str) else None,
        )
        item_id = f"actionable-drift:{node.repository}:{node.branch}"
        if observation is not None and observation.complete and item_id not in observation.existing:
            # An unlink failure cannot republish an orphan whose branch is gone.
            continue
        nodes.append(node)
        # Missing fingerprints are old/sampled snapshots. They cannot prove
        # recovery or a changed occurrence, and do not invent a replacement.
        if (
            observation is not None
            and item_id in observation.existing
            and node.actionableFingerprint
        ):
            observation.observe(
                item_id,
                healthy=node.actionableCount == 0,
                fingerprint=node.actionableFingerprint,
            )
    return nodes


def read_setup_summaries(
    coordination_root: Path, *, now: datetime
) -> list[SetupSummaryNode]:  # pragma: no cover
    """Surface 2: the latest provider-setup summary per action.

    Reads ``logs/providers/setup/last-<action>.json`` (the compact summary the
    setup run writes); the ``-full`` debug copies are skipped.
    """
    directory = coordination_root / "logs" / "providers" / "setup"
    if not directory.is_dir():
        return []
    nodes: list[SetupSummaryNode] = []
    for path in sorted(directory.glob("last-*.json")):
        if path.name.endswith("-full.json"):
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        generated = payload.get("generatedAt")
        raw_counts = payload.get("resultCounts")
        ok_value = payload.get("ok")
        ready_value = payload.get("ready")
        nodes.append(
            SetupSummaryNode(
                action=str(payload.get("action") or path.stem.removeprefix("last-")),
                ok=ok_value if isinstance(ok_value, bool) else None,
                ready=ready_value if isinstance(ready_value, bool) else None,
                state=_text_or_none(payload.get("state")),
                generatedAt=generated if isinstance(generated, str) else None,
                snapshotStaleSeconds=age_seconds(generated, now)
                if isinstance(generated, str)
                else None,
                resultCounts=(
                    {str(key): _as_int(value) for key, value in raw_counts.items()}
                    if isinstance(raw_counts, dict)
                    else {}
                ),
            )
        )
    return nodes


def read_setup_progress_nodes(  # pragma: no cover
    coordination_root: Path,
    *,
    now: datetime,
    active_worktree_groups: set[str] | None = None,
    observation: AttentionObservation | None = None,
) -> list[SetupProgressNode]:
    """Surface 3: each worktree group's live provider-setup progress.

    Projected through the producer's own ``progress_status`` so a ``running`` group
    whose heartbeat went stale reads ``stale`` -- the boot-sequence widget data.
    """
    worktrees_root = coordination_root / "worktrees"
    nodes: list[SetupProgressNode] = []
    if observation is not None:
        for group_path in surface_paths(worktrees_root, "*/*", observation):
            if group_path.is_dir():
                observation.observe(f"failed-setup:{group_path.name}")
    for path in surface_paths(
        worktrees_root, "*/*/provider-runtime/setup-progress.json", observation
    ):
        group = path.parent.parent.name
        progress = read_setup_progress(path)
        if progress is None:
            if observation is not None:
                observation.complete = False
            continue
        status = progress_status(progress, clock=lambda: now)
        completed = progress.get("completedPhases")
        node = SetupProgressNode(
            group=group,
            state=str(status.get("state", "unknown")),
            currentPhase=_current_phase_text(status.get("currentPhase")),
            heartbeatAgeSeconds=_as_float(status.get("heartbeatAgeSeconds")),
            completedCount=len(completed) if isinstance(completed, list) else 0,
            failedPhases=[str(line) for line in status.get("failedPhases", []) or []],
        )
        observe_setup(node, observation)
        if active_worktree_groups is None or group in active_worktree_groups:
            nodes.append(node)
    return nodes


def read_route_coverage(  # pragma: no cover
    onboarding_root: Path, *, repository: str | None = None
) -> list[RouteCoverageNode]:
    """Surface 10: per-route coverage from generated ``overview.index.json`` files."""
    if not onboarding_root.is_dir():
        return []
    nodes: list[RouteCoverageNode] = []
    for path in sorted(onboarding_root.rglob("overview.index.json")):
        payload = _read_json(path)
        if payload is None:
            continue
        raw_counts = payload.get("coverageCounts")
        counts = raw_counts if isinstance(raw_counts, dict) else {}
        nodes.append(
            RouteCoverageNode(
                repository=_text_or_none(payload.get("repository")) or repository,
                route=str(payload.get("route", "")),
                sourceFilesInScope=_as_int(counts.get("sourceFilesInScope")),
                fileSidecars=_as_int(counts.get("fileSidecars")),
                childRoutes=_as_int(counts.get("childRoutes")),
            )
        )
    return nodes


def read_tool_reports(
    coordination_root: Path, *, now: datetime
) -> list[ToolReportNode]:  # pragma: no cover
    """Surface 12: the newest verbose tool-report per tool (``temp/tool-reports/<tool>/``)."""
    root = coordination_root / "temp" / "tool-reports"
    if not root.is_dir():
        return []
    nodes: list[ToolReportNode] = []
    for tool_dir in sorted(root.iterdir()):
        if not tool_dir.is_dir():
            continue
        reports = sorted(
            tool_dir.glob("*.json"), reverse=True
        )  # UTC-stamped names sort newest-first
        if not reports:
            continue
        newest = reports[0]
        nodes.append(
            ToolReportNode(
                tool=tool_dir.name,
                path=newest.as_posix(),
                label=_report_label(newest.name),
                ageSeconds=_file_age_seconds(newest, now),
            )
        )
    return nodes


def read_ledger(memory_root: Path, code_root: Path | None = None) -> LedgerNode | None:
    """Surface 8: a repo's memory-ledger currency (closeout count + last-verified commit).

    Ledger rows carry no timestamps, so only the count + currency are surfaced --
    never a frequency-over-time series. Missing/invalid ledgers are skipped. The windowed rows for
    the OFFICIAL coupler popover are enriched with each side's commit message + date probed from
    ``code_root`` (the repo checkout) and ``memory_root`` (Tier 2); best-effort, absent when not local.
    """
    try:
        ledger = load_ledger(memory_root / "memory.md")
    except (LedgerError, OSError):
        return None
    return LedgerNode(
        repository=ledger.repo_name,
        closeoutCount=len(ledger.rows),
        lastVerifiedCodeCommit=ledger.last_verified_code_commit,
        baseCodeCommit=ledger.base_code_commit,
        # the newest window for the OFFICIAL coupler popover (5h); closeoutCount stays the full total
        rows=_enrich_ledger_rows(
            ledger.rows[:LEDGER_WINDOW],
            code_root=code_root.as_posix() if code_root is not None else None,
            memory_root=memory_root.as_posix(),
        ),
    )


def _ledger_window(
    ledger_path: Any, *, code_root: Any = None, memory_root: Any = None
) -> tuple[list[LedgerRefNode], int]:
    """The worktree memory.md ledger window for the coupler popover (5h).

    Best-effort like ``status_payload``: a missing / invalid / unreadable ledger yields an empty
    window so the projection tick never fails. Returns the newest ``LEDGER_WINDOW`` rows plus the
    total row count (for the popover's "+N more in memory.md" footer). Each row is enriched with the
    per-side commit message + date probed from ``code_root`` / ``memory_root`` (Tier 2); a row whose
    commit is not in the local repo keeps only its hash.
    """
    if not isinstance(ledger_path, str) or not ledger_path:
        return [], 0
    try:
        ledger = load_ledger(Path(ledger_path))
    except (LedgerError, OSError):
        return [], 0
    rows = _enrich_ledger_rows(
        ledger.rows[:LEDGER_WINDOW], code_root=code_root, memory_root=memory_root
    )
    return rows, len(ledger.rows)


def _git_commit_meta(
    repo_root: Any, commits: list[str]
) -> dict[str, tuple[str, str]]:  # pragma: no cover
    """Best-effort batched commit metadata for the ledger popover (5h Tier 2).

    ONE ``git log`` per repo for the whole window (never one subprocess per commit): ``--no-walk``
    shows only the named commits (no history traversal) and ``--ignore-missing`` drops any SHA that
    is not in this local repo. Returns ``{full_hash: (committer_iso_date, subject)}`` for the commits
    that resolved; ``{}`` on any failure (no repo path, git absent, non-zero exit) so the projection
    tick never fails and the row honestly falls back to the hash alone. Metadata is never faked -- a
    commit absent from the local repo simply has no entry (``--ignore-missing`` with an all-missing
    set returns empty, never a HEAD fallback).
    """
    if not isinstance(repo_root, str) or not repo_root or not commits:
        return {}
    try:
        result = run_git(
            Path(repo_root),
            ["log", "--no-walk", "--ignore-missing", "--format=%H%x1f%cI%x1f%s", *commits],
        )
    except (OSError, subprocess.SubprocessError):
        # SubprocessError is not a subclass of OSError, and TimeoutExpired is one: this call
        # moved onto a runner that has a timeout, so a wedged `git log` now raises something
        # `except OSError` cannot see. It would escape through `_enrich_ledger_rows` and fail
        # the projection tick, breaking the promise `_ledger_window` and `read_ledger` both
        # make -- that an unreadable ledger degrades to hash-only rows, never to an exception.
        return {}
    if result.returncode != 0:
        return {}
    meta: dict[str, tuple[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            full_hash, iso_date, subject = parts
            meta[full_hash] = (iso_date, subject)
    return meta


def _commit_meta_for(
    commit: str, meta: dict[str, tuple[str, str]]
) -> tuple[str | None, str | None]:
    """Resolve a (possibly short) ledger SHA against the full-hash-keyed probe map (prefix-tolerant)."""
    info = meta.get(commit)
    if info is None:
        info = next(
            (value for full_hash, value in meta.items() if full_hash.startswith(commit)), None
        )
    if info is None:
        return None, None
    iso_date, subject = info
    return iso_date, subject


def _enrich_ledger_rows(
    rows: list[LedgerRow], *, code_root: Any, memory_root: Any
) -> list[LedgerRefNode]:
    """Window rows -> served LedgerRefNodes, each carrying best-effort per-side commit meta (Tier 2).

    One batched probe per side: code commits against ``code_root``, memory commits against
    ``memory_root``. A row whose commit isn't in the local repo keeps its hash with no message/date.
    """
    code_meta = _git_commit_meta(code_root, [row.code_commit for row in rows])
    memory_meta = _git_commit_meta(memory_root, [row.memory_commit for row in rows])
    enriched: list[LedgerRefNode] = []
    for row in rows:
        code_date, code_subject = _commit_meta_for(row.code_commit, code_meta)
        memory_date, memory_subject = _commit_meta_for(row.memory_commit, memory_meta)
        enriched.append(
            LedgerRefNode(
                codeCommit=row.code_commit,
                memoryCommit=row.memory_commit,
                codeSubject=code_subject,
                codeDate=code_date,
                memorySubject=memory_subject,
                memoryDate=memory_date,
            )
        )
    return enriched
