"""File-surface readers for the projection (data surfaces 1-3, 5, 6, 8, 10-12).

3a reads the surfaces the *named* state tree needs: provider current-state
(surface 1) and worktree enclosures (the contract, surface 6, plus the group
layout, surface 5). 3b adds the analytical surfaces -- drift read from the
persisted JSON snapshot (never re-classified here -- that is git-per-sidecar),
git-free sidecar staleness (11), provider setup summaries (2) and progress (3),
route-index coverage (10), the tool-report feed (12), and memory-ledger currency
(8). Every reader reuses the producing subsystem's own parser rather than
re-parsing.

These functions do the file I/O at the projection's call edge; the fold itself
(:mod:`agents_remember.observer.reducer`) stays pure.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.kernel.memory_ledger import LedgerError, load_ledger
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    discover_onboarding_files,
    parse_table_metadata,
)
from agents_remember.observer.paths import DRIFT_SNAPSHOT_SCHEMA, drift_snapshot_dir
from agents_remember.observer.projection import (
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    LedgerNode,
    ProviderNode,
    RouteCoverageNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    TaskCodeExampleNode,
    TaskDecisionNode,
    TaskDocNode,
    TaskStepNode,
    TaskSubStepNode,
    ToolReportNode,
)
from agents_remember.observer.timeutil import age_seconds
from agents_remember.providers.current_state import current_state_path
from agents_remember.providers.setup_progress import progress_status, read_setup_progress
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    TaskDocument,
    current_step,
    step_done,
    step_total,
)
from agents_remember.worktrees.modules.guidance import (
    contract_payload,
    lifecycle_guidance,
    status_payload,
)
from agents_remember.worktrees.start_progress import read_start_progress
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

WORKTREE_PROVIDER_STATE_SCHEMA = "ar-worktree-provider-state/v1"


def _provider_role(provider_id: str) -> str:
    """Map a provider id to its repo role: GrepAI serves the memory repo, CGC the code repo."""
    return "memory" if "memory" in provider_id or "grepai" in provider_id else "code"


def read_providers(config: McpRuntimeConfig, *, now: datetime) -> list[ProviderNode]:
    """Surfaces 1 + 4: the workspace provider snapshot **plus** each worktree's isolated stack.

    Surface 1 is the workspace ``current.json`` (one call-triggered snapshot; its age is surfaced
    via ``snapshotStaleSeconds`` rather than faked live). Surface 4 is every worktree group's
    ``provider-runtime/provider-state.json`` -- the isolated CGC (code repo) + GrepAI (memory repo)
    stack a worktree spawns at start -- bound to its worktree group + repo + role, so the engine
    room shows each worktree's own engines instead of only main's (the gap note 03 flagged).
    """
    return _workspace_providers(config, now=now) + _worktree_providers(
        config.coordination_root, now=now
    )


def _workspace_providers(config: McpRuntimeConfig, *, now: datetime) -> list[ProviderNode]:
    payload = _read_json(current_state_path(config))
    if payload is None:
        return []
    checked_at = payload.get("checkedAt")
    stale = age_seconds(checked_at, now) if isinstance(checked_at, str) else None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return []
    nodes: list[ProviderNode] = []
    for key, value in providers.items():
        if not isinstance(value, dict):
            continue
        ok = value.get("ok")
        provider_id = str(value.get("id", key))
        nodes.append(
            ProviderNode(
                id=provider_id,
                state=str(value.get("state", "unknown")),
                ok=ok if isinstance(ok, bool) else None,
                watcherUp=bool(value.get("watcherUp", False)),
                indexingState=str(value.get("indexingState", "unknown")),
                snapshotStaleSeconds=stale,
                scope="workspace",
                role=_provider_role(provider_id),
            )
        )
    return nodes


def _worktree_providers(coordination_root: Path, *, now: datetime) -> list[ProviderNode]:
    """Surface 4: each worktree group's isolated provider stack, bound to its worktree + repo."""
    worktrees_root = coordination_root / "worktrees"
    if not worktrees_root.is_dir():
        return []
    nodes: list[ProviderNode] = []
    for path in sorted(worktrees_root.glob("*/*/provider-runtime/provider-state.json")):
        payload = _read_json(path)
        if payload is None or payload.get("schema") != WORKTREE_PROVIDER_STATE_SCHEMA:
            continue
        group = path.parent.parent.name
        repo = _text_or_none(payload.get("repoName"))
        settings = payload.get("isolatedProviderSettings")
        providers = settings.get("providers") if isinstance(settings, dict) else None
        if not isinstance(providers, list):
            continue
        stale = _file_age_seconds(path, now)
        for provider in providers:
            provider_id = str(provider)
            nodes.append(
                ProviderNode(
                    id=f"{provider_id}@{group}",
                    state="configured",  # the group state file exists => the stack was set up
                    ok=True,
                    indexingState="unknown",
                    snapshotStaleSeconds=stale,
                    scope="worktree",
                    role=_provider_role(provider_id),
                    repoId=repo,
                    worktreeGroup=group,
                )
            )
    return nodes


def read_enclosures(coordination_root: Path) -> list[EnclosureNode]:
    """Surfaces 5/6: every worktree contract under ``tasks/<repo>/<task>/``.

    The contract lives in the durable task folder (design §1.1), so it outlives
    worktree cleanup -- an enclosure stays in the projection as the kanban record
    even after its worktree is reclaimed. A malformed contract is skipped, never
    fatal to the whole projection.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    nodes: list[EnclosureNode] = []
    for path in sorted(tasks_root.glob("*/*/contract.md")):
        node = _enclosure_from_contract(path)
        if node is not None:
            nodes.append(node)
    return nodes


def _enclosure_from_contract(path: Path) -> EnclosureNode | None:
    try:
        contract = load_contract(path)
    except (ContractError, OSError):
        return None
    return EnclosureNode(
        enclosure=contract.contract_path.as_posix(),
        taskId=contract.task_id,
        taskName=contract.task_name,
        repoName=contract.repo_name,
        lifecycleId=contract.lifecycle_id,
        worktreeGroup=contract.worktree_group.as_posix(),
        humanReviewStatus=contract.human_review_status,
        closeoutStatus=contract.closeout_status,
        integrationStatus=contract.integration_status,
        cleanup=contract.cleanup,
    )


def read_engine_process_facts(coordination_root: Path) -> list[EngineProcessFacts]:
    """Slice 5e: gather one fact bundle per worktree contract for the Engine Room map.

    Globs the same ``tasks/<repo>/<task>/contract.md`` files as :func:`read_enclosures`, but
    enriches each with the status-guidance facts the structural ``EnclosureNode`` omits (the
    code/memory branches, base commits, worktree paths, existence/dirty flags, base freshness,
    and provider-boot status). ``contract_payload`` and ``lifecycle_guidance`` are pure; only
    ``status_payload`` touches git, and it is best-effort so a contract pointing at absent or
    fake worktrees degrades to ``status=None`` (rendered as missing/derived) instead of
    crashing the projection tick. A malformed contract is skipped, never fatal.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    facts: list[EngineProcessFacts] = []
    for path in sorted(tasks_root.glob("*/*/contract.md")):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        facts.append(
            EngineProcessFacts(
                contract=contract_payload(contract),
                guidance=lifecycle_guidance(contract),
                status=_safe_status_payload(contract),
            )
        )
    return facts


def _safe_status_payload(contract: Any) -> dict[str, Any] | None:
    """``status_payload`` is the only git-touching part; never let it crash the tick."""
    try:
        return status_payload(contract)
    except Exception:  # a single worktree's git state must never fail the projection tick
        return None


def read_start_progress_entries(coordination_root: Path, *, now: datetime) -> list[dict[str, Any]]:
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


# --- analytical surface readers (slice 3b) -----------------------------------


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
                snapshotStaleSeconds=age_seconds(checked, now)
                if isinstance(checked, str)
                else None,
            )
        )
    return nodes


def read_sidecar_staleness(
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


def read_setup_summaries(coordination_root: Path, *, now: datetime) -> list[SetupSummaryNode]:
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


def read_setup_progress_nodes(coordination_root: Path, *, now: datetime) -> list[SetupProgressNode]:
    """Surface 3: each worktree group's live provider-setup progress.

    Projected through the producer's own ``progress_status`` so a ``running`` group
    whose heartbeat went stale reads ``stale`` -- the boot-sequence widget data.
    """
    worktrees_root = coordination_root / "worktrees"
    if not worktrees_root.is_dir():
        return []
    nodes: list[SetupProgressNode] = []
    for path in sorted(worktrees_root.glob("*/*/provider-runtime/setup-progress.json")):
        progress = read_setup_progress(path)
        if progress is None:
            continue
        status = progress_status(progress, clock=lambda: now)
        completed = progress.get("completedPhases")
        nodes.append(
            SetupProgressNode(
                group=path.parent.parent.name,
                state=str(status.get("state", "unknown")),
                currentPhase=_current_phase_text(status.get("currentPhase")),
                heartbeatAgeSeconds=_as_float(status.get("heartbeatAgeSeconds")),
                completedCount=len(completed) if isinstance(completed, list) else 0,
                failedPhases=[str(line) for line in status.get("failedPhases", []) or []],
            )
        )
    return nodes


def read_route_coverage(
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


def read_tool_reports(coordination_root: Path, *, now: datetime) -> list[ToolReportNode]:
    """Surface 12: the newest verbose tool-report per tool (``temp/tool-reports/<tool>/``)."""
    root = coordination_root / "temp" / "tool-reports"
    if not root.is_dir():
        return []
    nodes: list[ToolReportNode] = []
    for tool_dir in sorted(root.iterdir()):
        if not tool_dir.is_dir():
            continue
        reports = sorted(tool_dir.glob("*.json"), reverse=True)  # UTC-stamped names sort newest-first
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


def read_ledger(memory_root: Path) -> LedgerNode | None:
    """Surface 8: a repo's memory-ledger currency (closeout count + last-verified commit).

    Ledger rows carry no timestamps, so only the count + currency are surfaced --
    never a frequency-over-time series. Missing/invalid ledgers are skipped.
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
    )


def read_task_documents(coordination_root: Path, *, now: datetime) -> list[TaskDocNode]:
    """Surface 7 (slice 3c): per-lifecycle task-document progress.

    Reads each ``ar-task-document/v1`` JSON under ``tasks/<repo>/<task>/`` -- the
    source of truth, never the rendered markdown -- keyed by ``lifecycleId`` so the
    dashboard can show what a lifecycle is doing. Documents with no lifecycle key
    (not yet bound to a durable worktree), non-task JSON, and malformed files are
    skipped.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    nodes: list[TaskDocNode] = []
    for path in sorted(tasks_root.glob("*/*/*.json")):
        payload = _read_json(path)
        if payload is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        if not payload.get("lifecycleId"):
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        nodes.append(
            TaskDocNode(
                lifecycleId=doc.lifecycleId or "",
                repository=doc.repo,
                title=doc.title,
                status=doc.status,
                kind=doc.kind,
                stepsDone=step_done(doc),
                stepsTotal=step_total(doc),
                currentStep=current_step(doc),
                docPath=path.as_posix(),
                ageSeconds=_file_age_seconds(path, now),
                steps=[
                    TaskStepNode(
                        id=step.id,
                        title=step.title,
                        status=step.status,
                        substeps=[
                            TaskSubStepNode(id=sub.id, title=sub.title, status=sub.status)
                            for sub in step.substeps
                        ],
                    )
                    for step in doc.steps
                ],
                objective=doc.objective,
                requirements=list(doc.requirements),
                design=doc.design,
                codeExamples=[
                    TaskCodeExampleNode(
                        id=example.id,
                        title=example.title,
                        distinctChange=example.distinctChange,
                        why=example.why,
                        language=example.language,
                        snippet=example.snippet,
                    )
                    for example in doc.codeExamples
                ],
                decisions=[
                    TaskDecisionNode(at=item.at, decision=item.decision, rationale=item.rationale)
                    for item in doc.decisions
                ],
                openQuestions=list(doc.openQuestions),
                references=list(doc.references),
            )
        )
    return nodes


# --- shared helpers ----------------------------------------------------------


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _report_label(name: str) -> str:
    stem = name[:-5] if name.endswith(".json") else name
    parts = stem.split("-", 1)
    return parts[1] if len(parts) == 2 else stem


def _file_age_seconds(path: Path, now: datetime) -> float | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return now.timestamp() - mtime


def _current_phase_text(current: Any) -> str | None:
    if not isinstance(current, dict):
        return None
    provider = current.get("provider")
    action = current.get("action")
    if provider and action:
        return f"{provider} {action}"
    return str(provider or action) if (provider or action) else None
