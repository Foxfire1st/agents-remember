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

import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.interaction_retention import (
    AGENT_PICKUP_TTL_SECONDS,
    pickup_age_seconds,
    pickup_state,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.memory_ledger import LedgerError, LedgerRow, load_ledger
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    discover_onboarding_files,
    parse_table_metadata,
)
from agents_remember.observer.paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
    observer_logs_root,
)
from agents_remember.observer.projection import (
    LEDGER_WINDOW,
    AgentPickupNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    LedgerNode,
    LedgerRefNode,
    ProviderNode,
    RouteCoverageNode,
    SeriesNode,
    SeriesSectionNode,
    SeriesSubTaskNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    TaskCodeExampleNode,
    TaskDecisionNode,
    TaskDocNode,
    TaskSectionNode,
    TaskStepNode,
    TaskSubStepNode,
    TaskSubTaskRefNode,
    ToolReportNode,
)
from agents_remember.observer.provider_nodes import (
    workspace_provider_nodes,
    worktree_provider_node,
)
from agents_remember.observer.timeutil import age_seconds
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.current_state import current_state_path
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_container_state_summary,
)
from agents_remember.providers.setup_progress import progress_status, read_setup_progress
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    TaskDocument,
    current_step,
    series_done,
    series_total,
    step_done,
    step_total,
)
from agents_remember.worktrees.modules.git import run_git
from agents_remember.worktrees.modules.guidance import (
    contract_payload,
    lifecycle_guidance,
    status_payload,
)
from agents_remember.worktrees.start_progress import read_start_progress
from agents_remember.worktrees.task_resolver import (
    ARCHIVE_DIR,
    ENCLOSURES_DIR,
    iter_leaf_enclosure_contracts,
)
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

WORKTREE_PROVIDER_STATE_SCHEMA = "ar-worktree-provider-state/v1"
WORKTREE_PROVIDER_INSPECT_SECONDS = 5


def read_providers(
    config: McpRuntimeConfig,
    *,
    now: datetime,
    active_worktree_groups: set[str] | None = None,
) -> list[ProviderNode]:
    """Surfaces 1 + 4: the workspace provider snapshot plus admitted worktree stacks.

    Surface 1 is the workspace ``current.json`` (one call-triggered snapshot; its age is surfaced
    via ``snapshotStaleSeconds`` rather than faked live). Surface 4 is an active worktree group's
    ``provider-runtime/provider-state.json`` -- the isolated CGC (code repo) + GrepAI (memory repo)
    stack a worktree spawns at start -- bound to its worktree group + repo + role, so the engine
    room ignores parked worktrees and completed lifecycles whose providers are no longer live.
    """
    return _workspace_providers(config, now=now) + _worktree_providers(
        config.coordination_root,
        now=now,
        active_worktree_groups=active_worktree_groups,
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
    return workspace_provider_nodes(providers, stale_seconds=stale)


def _worktree_providers(
    coordination_root: Path,
    *,
    now: datetime,
    active_worktree_groups: set[str] | None,
) -> list[ProviderNode]:
    """Surface 4: each worktree group's isolated provider stack, bound to its worktree + repo."""
    worktrees_root = coordination_root / "worktrees"
    if not worktrees_root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    container_names: set[str] = set()
    settings_by_path: dict[str, dict[str, Any] | None] = {}
    runtime_specs_by_path: dict[str, dict[str, dict[str, list[str]]]] = {}
    for path in sorted(worktrees_root.glob("*/*/provider-runtime/provider-state.json")):
        group = path.parent.parent.name
        if active_worktree_groups is not None and group not in active_worktree_groups:
            continue
        payload = _read_json(path)
        if payload is None or payload.get("schema") != WORKTREE_PROVIDER_STATE_SCHEMA:
            continue
        settings = _worktree_provider_settings(payload, settings_by_path)
        runtime_specs = _worktree_runtime_specs(settings)
        for spec in runtime_specs.values():
            container_names.update(spec["resources"])
        settings_key = _worktree_settings_key(payload)
        if settings_key is not None:
            runtime_specs_by_path[settings_key] = runtime_specs
        records.append(
            {
                "path": path,
                "payload": payload,
                "settingsKey": settings_key,
                "group": group,
                "repo": _text_or_none(payload.get("repoName")),
                "stale": _file_age_seconds(path, now),
                "providers": _worktree_provider_ids(payload),
            }
        )
    inspected = _inspect_containers(container_names, cwd=coordination_root)
    nodes: list[ProviderNode] = []
    for record in records:
        settings_key = record["settingsKey"]
        runtime_specs = runtime_specs_by_path.get(str(settings_key), {}) if settings_key is not None else {}
        for provider in record["providers"]:
            provider_id = str(provider)
            nodes.append(
                worktree_provider_node(
                    provider_id,
                    group=record["group"],
                    repo_id=record["repo"],
                    stale_seconds=record["stale"],
                    runtime=_worktree_runtime_summary(
                        runtime_specs.get(provider_id, {}),
                        inspected,
                    ),
                )
            )
    return nodes


def _worktree_provider_ids(payload: dict[str, Any]) -> list[str]:
    settings = payload.get("isolatedProviderSettings")
    providers = settings.get("providers") if isinstance(settings, dict) else None
    if not isinstance(providers, list):
        return []
    return [str(provider) for provider in providers]


def _worktree_settings_key(payload: dict[str, Any]) -> str | None:
    settings = payload.get("isolatedProviderSettings")
    raw_path = settings.get("path") if isinstance(settings, dict) else None
    return raw_path if isinstance(raw_path, str) and raw_path else None


def _worktree_provider_settings(
    payload: dict[str, Any],
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    raw_path = _worktree_settings_key(payload)
    if raw_path is None:
        return None
    if raw_path not in cache:
        cache[raw_path] = _read_json(Path(raw_path))
    return cache[raw_path]


def _worktree_runtime_specs(
    settings: dict[str, Any] | None,
) -> dict[str, dict[str, list[str]]]:
    providers = _settings_providers(settings)
    specs: dict[str, dict[str, list[str]]] = {}
    cgc = providers.get("codegraphcontext-code")
    if isinstance(cgc, dict):
        specs["codegraphcontext-code"] = _cgc_runtime_spec(cgc)
    grepai = providers.get("grepai-memory")
    if isinstance(grepai, dict):
        specs["grepai-memory"] = _grepai_runtime_spec(grepai)
    return specs


def _settings_providers(settings: dict[str, Any] | None) -> dict[str, Any]:
    context = settings.get("contextProviders") if isinstance(settings, dict) else None
    providers = context.get("providers") if isinstance(context, dict) else None
    return providers if isinstance(providers, dict) else {}


def _cgc_runtime_spec(provider: dict[str, Any]) -> dict[str, list[str]]:
    backend = provider.get("backend")
    runtime = provider.get("runtime")
    runner = runtime.get("runner") if isinstance(runtime, dict) else None
    roots = provider.get("roots")
    watchers: list[str] = []
    template = runner.get("containerNameTemplate") if isinstance(runner, dict) else None
    if isinstance(template, str):
        for root in roots if isinstance(roots, list) else []:
            repo_id = root.get("repoId") if isinstance(root, dict) else None
            if isinstance(repo_id, str) and repo_id:
                watchers.append(template.replace("<repoId>", repo_id))
    resources = _compact_strings([_container_name(backend), *watchers])
    return {"watchers": watchers, "resources": resources}


def _grepai_runtime_spec(provider: dict[str, Any]) -> dict[str, list[str]]:
    runtime = provider.get("runtime")
    runner = runtime.get("runner") if isinstance(runtime, dict) else None
    backend = provider.get("backend")
    embedder = provider.get("embedder")
    embedder_backend = embedder.get("backend") if isinstance(embedder, dict) else None
    watcher = _container_name(runner)
    resources = _compact_strings(
        [
            _container_name(backend),
            _container_name(embedder_backend),
            watcher,
        ]
    )
    return {"watchers": _compact_strings([watcher]), "resources": resources}


def _container_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    name = value.get("containerName")
    return name if isinstance(name, str) and name else None


def _compact_strings(values: list[str | None]) -> list[str]:
    return [value for value in values if value]


def _inspect_containers(
    names: set[str],
    *,
    cwd: Path,
) -> dict[str, dict[str, Any] | None] | None:
    if not names:
        return {}
    try:
        docker = docker_command()
        result = run_command(
            [docker, "inspect", *sorted(names)],
            cwd=cwd,
            timeout=WORKTREE_PROVIDER_INSPECT_SECONDS,
            allow_timeout=True,
        )
    except (ContextProviderError, OSError):
        return None
    if result.get("timedOut"):
        return None
    inspected = _inspect_result_map(result.get("stdout"))
    if result.get("returncode") == 0:
        return {name: inspected.get(name) for name in names}
    return _inspect_containers_individually(names, cwd=cwd, docker=docker)


def _inspect_containers_individually(
    names: set[str],
    *,
    cwd: Path,
    docker: str,
) -> dict[str, dict[str, Any] | None] | None:
    inspected: dict[str, dict[str, Any] | None] = {}
    for name in sorted(names):
        try:
            result = run_command(
                [docker, "inspect", name],
                cwd=cwd,
                timeout=WORKTREE_PROVIDER_INSPECT_SECONDS,
                allow_timeout=True,
            )
        except OSError:
            return None
        if result.get("timedOut"):
            return None
        if result.get("returncode") != 0:
            inspected[name] = None
            continue
        inspected[name] = _inspect_result_map(result.get("stdout")).get(name)
    return inspected


def _inspect_result_map(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, list):
        return {}
    results: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").lstrip("/")
        if name:
            results[name] = item
    return results


def _worktree_runtime_summary(
    spec: dict[str, list[str]],
    inspected: dict[str, dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    resources = spec.get("resources") or []
    if not resources or inspected is None:
        return None
    summaries = [docker_container_state_summary(inspected.get(name)) for name in resources]
    running = [summary for summary in summaries if summary.get("running") is True]
    watcher_names = spec.get("watchers") or []
    watcher_up = bool(watcher_names) and all(
        docker_container_state_summary(inspected.get(name)).get("running") is True
        for name in watcher_names
    )
    if len(running) == len(resources):
        state = "ready"
    elif running:
        state = "degraded"
    else:
        state = "failed"
    return {
        "state": state,
        "ok": state == "ready",
        "watcherUp": watcher_up,
        "indexingState": "unknown",
    }


def read_enclosures(coordination_root: Path) -> list[EnclosureNode]:
    """Surfaces 5/6: every active leaf enclosure contract.

    Leaf contracts live below ``enclosures/<leaf-id>/series-contract.md``. Root
    series contracts describe integration branches and are not live worktree
    processes. A malformed contract is skipped, never fatal to the projection.
    """
    tasks_root = coordination_root / "tasks"
    nodes: list[EnclosureNode] = []
    for path in iter_leaf_enclosure_contracts(tasks_root):
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
        enclosureId=contract.leaf_id or contract.contract_path.parent.name,
        leafId=contract.leaf_id,
        taskId=contract.task_id,
        taskName=contract.task_name,
        repoName=contract.repo_name,
        taskRoot=contract.task_root.as_posix(),
        lifecycleId=contract.lifecycle_id,
        worktreeGroup=contract.worktree_group.as_posix(),
        humanReviewStatus=contract.human_review_status,
        closeoutStatus=contract.closeout_status,
        integrationStatus=contract.integration_status,
        cleanup=contract.cleanup,
    )


def read_gates(coordination_root: Path, *, now: datetime | None = None) -> list[GateRecord]:
    """Every lifecycle's current (folded) gate set + the workspace log (slice 6c).

    Reads the gate logs co-located with the event store under ``observer_logs_root``
    and folds each by id (last-wins), so the projection sees live gate state with no
    event machinery. A malformed log is skipped, never fatal to the tick.
    """
    root = observer_logs_root(coordination_root)
    store = GateStore(root)
    if now is not None:
        for lifecycle_id in store.lifecycle_ids():
            with contextlib.suppress(OSError, ValueError):
                store.compact(lifecycle_id, now=now)
    gates: list[GateRecord] = []
    lifecycles_dir = root / "lifecycles"
    if lifecycles_dir.is_dir():
        for log in sorted(lifecycles_dir.glob("*/gates.jsonl")):
            try:
                gates.extend(store.current(log.parent.name).values())
            except (OSError, ValueError):
                continue
    with contextlib.suppress(OSError, ValueError):
        gates.extend(store.current(None).values())
    return gates


def read_attention_dismissals(coordination_root: Path) -> dict[str, AttentionDismissalRecord]:
    """The operator's current lifecycle attention acknowledgements (leaf-28 S5.2).

    Reads ``<observer_root>/workspace/attention-dismissals.jsonl`` (co-located with
    the gate logs) into ``{itemId: AttentionDismissalRecord}`` so the reducer can
    suppress a cleared lifecycle item across projection passes.
    """
    store = AttentionDismissalStore(observer_logs_root(coordination_root))
    with contextlib.suppress(OSError, ValueError):
        return store.current()
    return {}


def read_agent_pickups(coordination_root: Path, *, now: datetime) -> list[AgentPickupNode]:
    """Pending dashboard responses waiting for agent-side inbox consumption."""
    store = OperatorInboxStore(observer_logs_root(coordination_root))
    with contextlib.suppress(OSError, ValueError):
        store.compact(now=now)
    pickups: list[AgentPickupNode] = []
    with contextlib.suppress(OSError, ValueError):
        for entry in store.current().values():
            if entry.state != "pending":
                continue
            pickups.append(
                AgentPickupNode(
                    id=f"pickup:{entry.id}",
                    entryId=entry.id,
                    lifecycleId=entry.lifecycleId,
                    agentId=entry.agentId,
                    gateId=entry.gateId,
                    state=pickup_state(entry, now=now),
                    ageSeconds=pickup_age_seconds(entry, now=now),
                    ttlSeconds=AGENT_PICKUP_TTL_SECONDS,
                )
            )
    return sorted(pickups, key=lambda item: item.ageSeconds or 0.0, reverse=True)


def read_engine_process_facts(
    coordination_root: Path,
    *,
    active_worktree_groups: set[str] | None = None,
) -> list[EngineProcessFacts]:
    """Slice 5e: gather one fact bundle per leaf enclosure for the Engine Room map.

    Globs the same leaf enclosure files as :func:`read_enclosures`, but
    enriches each with the status-guidance facts the structural ``EnclosureNode`` omits (the
    code/memory branches, base commits, worktree paths, existence/dirty flags, base freshness,
    and provider-boot status). ``contract_payload`` and ``lifecycle_guidance`` are pure; only
    ``status_payload`` touches git, and it is best-effort so a contract pointing at absent or
    fake worktrees degrades to ``status=None`` (rendered as missing/derived) instead of
    crashing the projection tick. A malformed contract is skipped, never fatal.
    """
    tasks_root = coordination_root / "tasks"
    facts: list[EngineProcessFacts] = []
    for path in iter_leaf_enclosure_contracts(tasks_root):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if (
            active_worktree_groups is not None
            and contract.worktree_group.name not in active_worktree_groups
        ):
            continue
        cp = contract_payload(contract)
        ledger_rows, ledger_total = _ledger_window(
            cp.get("ledger_path"),
            code_root=cp.get("code_worktree"),
            memory_root=cp.get("memory_worktree"),
        )
        facts.append(
            EngineProcessFacts(
                contract=cp,
                guidance=lifecycle_guidance(contract),
                status=_safe_status_payload(contract),
                ledger_rows=ledger_rows,
                ledger_row_count=ledger_total,
            )
        )
    return facts


def _safe_status_payload(contract: Any) -> dict[str, Any] | None:
    """``status_payload`` is the only git-touching part; never let it crash the tick."""
    try:
        return status_payload(contract)
    except Exception:  # a single worktree's git state must never fail the projection tick
        return None


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


def _git_commit_meta(repo_root: Any, commits: list[str]) -> dict[str, tuple[str, str]]:
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
    except OSError:
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


def read_setup_progress_nodes(
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


def read_task_documents(
    coordination_root: Path, *, enclosures: list[EnclosureNode], now: datetime
) -> list[TaskDocNode]:
    """Surface 7 (slices 3c + 6g): active task-document reader content.

    Reads each ``ar-task-document/v1`` JSON under ``tasks/<repo>/<task>/`` -- the
    source of truth, never the rendered markdown. ``lifecycleId`` is optional
    runtime context: a ``light``/``subTask`` doc uses its own lifecycle id or the
    lifecycle of its matching leaf enclosure when present, but planning docs are
    still projected before an enclosure exists. Master docs are projected here as
    task documents and still projected on the series surface for compatibility.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    lifecycle_by_enclosure = {
        enclosure.enclosure: enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId
    }
    lifecycle_by_dir = {
        Path(enclosure.taskRoot).resolve(): enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId and enclosure.taskRoot
    }
    lifecycle_by_root_doc = {
        Path(enclosure.taskRoot).resolve(): enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId
        and enclosure.taskRoot
        and enclosure.lifecycleId in {enclosure.taskId, enclosure.taskName}
    }
    # Enclosure leaf ids are lowercase directory names (enclosures/260628-l7/) while doc ids are
    # uppercase (260628-L7), and series leaf docs carry no enclosures[] refs in practice — so this
    # join is keyed case-insensitively on the doc's own id (the durable, human-stable key), with the
    # filename stem kept as a legacy alternative. Suffixed reopen enclosures (…-r1) deliberately do
    # not bind here; the sidebar admits those through its lifecycle-guarded suffix rule.
    lifecycle_by_leaf_doc = {
        (Path(enclosure.taskRoot).resolve(), enclosure.leafId.lower()): enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId and enclosure.taskRoot and enclosure.leafId
    }
    nodes: list[TaskDocNode] = []
    for path in _iter_task_json(tasks_root):
        payload = _read_json(path)
        if payload is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        lifecycle_id: str | None = None
        if doc.kind == "master":
            lifecycle_id = lifecycle_by_root_doc.get(path.parent.resolve())
        else:
            lifecycle_id = (
                doc.lifecycleId
                or _doc_enclosure_lifecycle(doc, lifecycle_by_enclosure)
                or lifecycle_by_leaf_doc.get((path.parent.resolve(), doc.id.lower()))
                or lifecycle_by_leaf_doc.get((path.parent.resolve(), path.stem.lower()))
            )
        nodes.append(_task_doc_node(doc, lifecycle_id, path, lifecycle_by_dir, now))
    return nodes


def _doc_enclosure_lifecycle(
    doc: TaskDocument, lifecycle_by_enclosure: dict[str, str]
) -> str | None:
    for enclosure in doc.enclosures:
        lifecycle_id = lifecycle_by_enclosure.get(enclosure.enclosurePath)
        if lifecycle_id:
            return lifecycle_id
    return None


def read_series_documents(coordination_root: Path, *, now: datetime) -> list[SeriesNode]:
    """Series surface (R1): per-master series progress, keyed by the task FOLDER.

    Reads each ``ar-task-document/v1`` JSON with ``kind == "master"`` under
    ``tasks/<repo>/<task>/``. Masters are also projected by :func:`read_task_documents`
    so direct task-document selection can render them; this companion surface keeps the
    folder-keyed series checklist where each subtask is one checkbox and ``doneCount``
    counts the *declared* ``Completed`` subtasks, authoritative over a slice's own internal
    steps.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    nodes: list[SeriesNode] = []
    for path in _iter_task_json(tasks_root):
        payload = _read_json(path)
        if payload is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        if payload.get("kind") != "master":
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        nodes.append(
            SeriesNode(
                seriesId=path.parent.name,
                repository=doc.repo,
                title=doc.title,
                status=doc.status,
                createdAt=doc.createdAt,
                objective=doc.objective,
                subTasks=_series_subtask_nodes(path, doc),
                doneCount=series_done(doc),
                totalCount=series_total(doc),
                sections=[
                    SeriesSectionNode(kind=section.kind, heading=section.heading, body=section.body)
                    for section in doc.sections
                ],
                decisions=[
                    TaskDecisionNode(at=item.at, decision=item.decision, rationale=item.rationale)
                    for item in doc.decisions
                ],
                docPath=path.as_posix(),
                ageSeconds=_file_age_seconds(path, now),
            )
        )
    return nodes


def _series_subtask_nodes(path: Path, doc: TaskDocument) -> list[SeriesSubTaskNode]:
    indexed = [
        (index, sub, _series_subtask_created_at(path.parent, sub.file))
        for index, sub in enumerate(doc.subTasks)
    ]
    if all(created_at for _, _, created_at in indexed):
        indexed.sort(key=lambda item: (item[2] or "", item[0]))
    return [
        SeriesSubTaskNode(
            number=sub.number,
            name=sub.name,
            file=sub.file,
            status=sub.status,
            scope=sub.scope,
            createdAt=created_at,
        )
        for _, sub, created_at in indexed
    ]


def _series_subtask_created_at(base_dir: Path, ref_file: str) -> str | None:
    if not ref_file:
        return None
    ref_path = (base_dir / ref_file).with_suffix(".json")
    payload = _read_json(ref_path)
    if payload is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
        return None
    if payload.get("kind") == "master":
        return None
    try:
        doc = TaskDocument.model_validate(payload)
    except ValueError:
        return None
    return doc.createdAt


def _iter_task_json(tasks_root: Path) -> list[Path]:
    return [
        path
        for path in sorted(tasks_root.rglob("*.json"))
        if ARCHIVE_DIR not in path.parts and ENCLOSURES_DIR not in path.parts
    ]


def _ref_lifecycle(
    base_dir: Path, ref_file: str | None, lifecycle_by_dir: dict[Path, str]
) -> str | None:
    """A cross-folder ref (``../<task>/task.md``) resolves to that folder's contract-paired lifecycle;
    a bare slug (a same-folder slice) or empty ref does not (slice 6g cross-master link)."""
    if not ref_file or "/" not in ref_file:
        return None
    return lifecycle_by_dir.get((base_dir / ref_file).resolve().parent)


def _task_doc_node(
    doc: TaskDocument,
    lifecycle_id: str | None,
    path: Path,
    lifecycle_by_dir: dict[Path, str],
    now: datetime,
) -> TaskDocNode:
    """Project one task document, carrying the resolved lifecycle id and (for a master) its index.

    Cross-master links resolve here: a subTask whose ``file`` points at another master, and the doc's
    own ``master`` parent ref, each resolve to the linked lifecycle (None when same-series/in-folder).
    """
    base_dir = path.parent
    parent_lifecycle = _ref_lifecycle(base_dir, doc.master, lifecycle_by_dir)
    return TaskDocNode(
        id=doc.id,
        lifecycleId=lifecycle_id,
        repository=doc.repo,
        title=doc.title,
        status=doc.status,
        kind=doc.kind,
        stepsDone=step_done(doc),
        stepsTotal=step_total(doc),
        currentStep=current_step(doc),
        docPath=path.as_posix(),
        createdAt=doc.createdAt,
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
        subTasks=[
            TaskSubTaskRefNode(
                number=ref.number,
                name=ref.name,
                file=ref.file,
                status=ref.status,
                scope=ref.scope,
                linkedLifecycleId=_ref_lifecycle(base_dir, ref.file, lifecycle_by_dir),
            )
            for ref in doc.subTasks
        ],
        sections=[
            TaskSectionNode(kind=section.kind, heading=section.heading, body=section.body)
            for section in doc.sections
        ],
        masterLifecycleId=parent_lifecycle,
    )


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
