"""Analytical file-surface readers: drift, sidecars, setup, routes, tools, ledger.

The observers' slice-3b readers plus the shared ledger-window enrichment used by
the engine-process facts and the official ledger surface. Every reader reuses
the producing subsystem's own parser rather than re-parsing.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.controlplane.stamps import age_seconds
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import LedgerError, LedgerRow, load_ledger
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    discover_onboarding_files,
    parse_table_metadata,
)
from agents_remember.observer.paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
)
from agents_remember.observer.projection import (
    LEDGER_WINDOW,
    DriftSnapshotNode,
    LedgerNode,
    LedgerRefNode,
    RouteCoverageNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    ToolReportNode,
)
from agents_remember.observer.snapshots_impl._common import (
    _as_float,
    _as_int,
    _current_phase_text,
    _file_age_seconds,
    _read_json,
    _report_label,
    _text_or_none,
)
from agents_remember.providers.setup_progress import progress_status, read_setup_progress
from agents_remember.worktrees.start_progress import read_start_progress


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:50).
def read_start_progress_entries(
    coordination_root: Path, *, now: datetime
) -> list[dict[str, Any]]:  # pragma: no cover
    """Slice 5e §5.4: pre-contract worktree-start blocks (a start gated before its contract).

    Reads the transient ``temp/worktree-start/<repo>/<worktree>.json`` files ``start.py`` writes
    when a start blocks before writing its contract, stamping each with the heartbeat age. A start
    that reached its contract has had this file cleared, so these are exactly the starts the
    contract-keyed enclosure surface cannot see.
    """
    root = coordination_root / "temp" / "worktree-start"
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.json")):
        payload = read_start_progress(path)
        if payload is None:
            continue
        updated = payload.get("updatedAt")
        entries.append(
            {
                **payload,
                "sourceFile": path.as_posix(),
                "ageSeconds": age_seconds(updated, now) if isinstance(updated, str) else None,
            }
        )
    return entries


def read_drift_snapshots(coordination_root: Path, *, now: datetime) -> list[DriftSnapshotNode]:
    """Surface 9 (b1): the persisted drift snapshots the memory_quality run writes.

    A cheap read with a staleness age -- the reducer never classifies drift itself
    (git-per-sidecar). A snapshot whose schema does not match is skipped.
    """
    directory = drift_snapshot_dir(coordination_root)
    if not directory.is_dir():
        return []
    nodes: list[DriftSnapshotNode] = []
    for path in sorted(directory.glob("*.json")):
        payload = _read_json(path)
        if payload is None or payload.get("schema") != DRIFT_SNAPSHOT_SCHEMA:
            continue
        raw_counts = payload.get("counts")
        counts = (
            {str(key): _as_int(value) for key, value in raw_counts.items()}
            if isinstance(raw_counts, dict)
            else {}
        )
        checked = payload.get("checkedAt")
        nodes.append(
            DriftSnapshotNode(
                repository=str(payload.get("repository", "")),
                branch=str(payload.get("branch", "")),
                counts=counts,
                actionableCount=_as_int(payload.get("actionableCount")),
                checkedAt=checked if isinstance(checked, str) else None,
                sourceRoot=_text_or_none(payload.get("sourceRoot")),
                memoryRoot=_text_or_none(payload.get("memoryRoot")),
                reportPath=_text_or_none(payload.get("reportPath")),
                snapshotStaleSeconds=age_seconds(checked, now)
                if isinstance(checked, str)
                else None,
            )
        )
    return nodes


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:116).
def read_sidecar_staleness(  # pragma: no cover
    onboarding_root: Path, *, repository: str, now: datetime
) -> list[SidecarStaleNode]:
    """Surface 11 (git-free): each supported sidecar's verification age.

    Reuses the drift package's discovery + table-metadata parse -- no git, no
    classification, just the age of the ``lastVerifiedCommitDate`` stamp.
    """
    if not onboarding_root.is_dir():
        return []
    nodes: list[SidecarStaleNode] = []
    for path in discover_onboarding_files(onboarding_root):
        try:
            metadata = parse_table_metadata(path)
        except (OSError, UnicodeDecodeError):
            continue
        stamp = metadata.get("lastVerifiedCommitDate", "")
        nodes.append(
            SidecarStaleNode(
                onboardingFile=path.relative_to(onboarding_root).as_posix(),
                repository=repository,
                lastVerifiedDate=stamp,
                ageSeconds=age_seconds(stamp, now) if stamp else None,
            )
        )
    return nodes


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:144).
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:184).
def read_setup_progress_nodes(  # pragma: no cover
    coordination_root: Path,
    *,
    now: datetime,
    active_worktree_groups: set[str] | None = None,
) -> list[SetupProgressNode]:
    """Surface 3: each worktree group's live provider-setup progress.

    Projected through the producer's own ``progress_status`` so a ``running`` group
    whose heartbeat went stale reads ``stale`` -- the boot-sequence widget data.
    """
    worktrees_root = coordination_root / "worktrees"
    if not worktrees_root.is_dir():
        return []
    nodes: list[SetupProgressNode] = []
    for path in sorted(worktrees_root.glob("*/*/provider-runtime/setup-progress.json")):
        group = path.parent.parent.name
        if active_worktree_groups is not None and group not in active_worktree_groups:
            continue
        progress = read_setup_progress(path)
        if progress is None:
            continue
        status = progress_status(progress, clock=lambda: now)
        completed = progress.get("completedPhases")
        nodes.append(
            SetupProgressNode(
                group=group,
                state=str(status.get("state", "unknown")),
                currentPhase=_current_phase_text(status.get("currentPhase")),
                heartbeatAgeSeconds=_as_float(status.get("heartbeatAgeSeconds")),
                completedCount=len(completed) if isinstance(completed, list) else 0,
                failedPhases=[str(line) for line in status.get("failedPhases", []) or []],
            )
        )
    return nodes


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:221).
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:246).
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_analytics.py:321).
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
