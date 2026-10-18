"""File-surface readers for the projection (data surfaces 1-3, 5, 6, 8, 10, 12).

3a reads the surfaces the *named* state tree needs: provider current-state
(surface 1) and worktree enclosures (the contract, surface 6, plus the group
layout, surface 5). 3b adds the analytical surfaces -- converted drift counts read from the
persisted JSON snapshot (never re-classified here), provider setup summaries (2) and progress (3),
route-index coverage (10), the tool-report feed (12), and memory-ledger currency
(8). Every reader reuses the producing subsystem's own parser rather than
re-parsing.

The provider readers and their container-inspection helpers live in this module;
the runtime process readers (enclosures, engine facts), the analytical readers,
and the task-document readers live in responsibility-split sibling modules and
are re-exported here so the projection's public import surface is unchanged.
These functions do the file I/O at the projection's call edge; the fold itself
(:mod:`agents_remember.observer.reducer`) stays pure.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.controlplane.attention_dismissals import AttentionObservation
from agents_remember.controlplane.stamps import age_seconds
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.observer.projection import ProviderNode
from agents_remember.observer.provider_nodes import (
    workspace_provider_nodes,
    workspace_repository_id,
    worktree_provider_node,
)
from agents_remember.observer.reducer_impl._attention import observe_providers
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.current_state import current_state_path
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_container_state_summary,
)
from agents_remember.serving.projections.snapshots_impl._analytics import (
    _commit_meta_for,
    _enrich_ledger_rows,
    _git_commit_meta,
    _ledger_window,
    read_drift_snapshots,
    read_ledger,
    read_route_coverage,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_start_progress_entries,
    read_tool_reports,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    STATUS_PAYLOAD_TTL_SECONDS,
    _as_float,
    _as_int,
    _current_phase_text,
    _file_age_seconds,
    _iter_task_document_payloads,
    _iter_task_json,
    _read_json,
    _report_label,
    _status_payload_cache,
    _task_doc_cache,
    _text_or_none,
    surface_paths,
)
from agents_remember.serving.projections.snapshots_impl._runtime import (
    _cached_local_status,
    _enclosure_from_contract,
    _safe_status_payload,
    logger,
    read_enclosures,
    read_engine_process_facts,
    refresh_engine_process_landing,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    _task_doc_body_revision,
    _task_doc_node,
    _task_step_nodes,
    read_task_document_body,
    read_task_documents,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA

WORKTREE_PROVIDER_STATE_SCHEMA = "ar-worktree-provider-state/v1"
WORKTREE_PROVIDER_INSPECT_SECONDS = 5

__all__ = [
    "STATUS_PAYLOAD_TTL_SECONDS",
    "TASK_DOCUMENT_SCHEMA",
    "_as_float",
    "_as_int",
    "_cached_local_status",
    "_commit_meta_for",
    "_current_phase_text",
    "_enclosure_from_contract",
    "_enrich_ledger_rows",
    "_git_commit_meta",
    "_inspect_containers",
    "_inspect_containers_individually",
    "_inspect_result_map",
    "_iter_task_document_payloads",
    "_iter_task_json",
    "_ledger_window",
    "_report_label",
    "_safe_status_payload",
    "_status_payload_cache",
    "_task_doc_body_revision",
    "_task_doc_cache",
    "_task_doc_node",
    "_task_step_nodes",
    "logger",
    "read_drift_snapshots",
    "read_enclosures",
    "read_engine_process_facts",
    "read_ledger",
    "read_providers",
    "read_route_coverage",
    "read_setup_progress_nodes",
    "read_setup_summaries",
    "read_start_progress_entries",
    "read_task_document_body",
    "read_task_documents",
    "read_tool_reports",
    "refresh_engine_process_landing",
]


def read_providers(
    config: McpRuntimeConfig,
    *,
    now: datetime,
    active_worktree_groups: set[str] | None = None,
    observation: AttentionObservation | None = None,
) -> list[ProviderNode]:
    """Surfaces 1 + 4: the workspace provider snapshot plus admitted worktree stacks.

    Surface 1 is the workspace ``current.json`` (one call-triggered snapshot; its age is surfaced
    via ``snapshotStaleSeconds`` rather than faked live). Surface 4 is an active worktree group's
    ``provider-runtime/provider-state.json`` -- the isolated CGC (code repo) + GrepAI (memory repo)
    stack a worktree spawns at start -- bound to its worktree group + repo + role, so the engine
    room ignores parked worktrees and completed lifecycles whose providers are no longer live.
    """
    workspace = _workspace_providers(config, now=now, observation=observation)
    worktrees = _worktree_providers(
        config.coordination_root,
        now=now,
        active_worktree_groups=active_worktree_groups,
        observation=observation,
    )
    observe_providers(worktrees, observation)
    return workspace + worktrees


def _workspace_providers(
    config: McpRuntimeConfig, *, now: datetime, observation: AttentionObservation | None = None
) -> list[ProviderNode]:  # pragma: no cover
    if not config.providers:
        return []
    payload = _read_json(current_state_path(config))
    if payload is None:
        if observation is not None:
            observation.complete = False
        return []
    checked_at = payload.get("checkedAt")
    stale = age_seconds(checked_at, now) if isinstance(checked_at, str) else None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        if observation is not None:
            observation.complete = False
        return []
    if observation is not None and any(
        not isinstance(providers.get(key), dict) for key in config.providers
    ):
        observation.complete = False
    configured = {key: value for key, value in providers.items() if key in config.providers}
    nodes = workspace_provider_nodes(configured, stale_seconds=stale)
    return _observe_workspace_providers(config, nodes, observation)


def _observe_workspace_providers(
    config: McpRuntimeConfig,
    nodes: list[ProviderNode],
    observation: AttentionObservation | None,
) -> list[ProviderNode]:
    """Configuration owns existence; only explicit per-repo evidence owns recovery."""
    configured_ids = set()
    for provider in config.providers.values():
        configured_ids.add(provider.provider_id)
        for repository in config.repositories.values():
            configured_ids.add(
                f"{provider.provider_id}:{workspace_repository_id(provider.provider_id, repository.repo_id)}"
            )
    nodes = [node for node in nodes if node.id in configured_ids]
    if observation is not None:
        for identity in configured_ids:
            observation.observe(f"provider-down:{identity}")
        observe_providers(nodes, observation)
    return nodes


def _worktree_providers(
    coordination_root: Path,
    *,
    now: datetime,
    active_worktree_groups: set[str] | None,
    observation: AttentionObservation | None = None,
) -> list[ProviderNode]:
    """Surface 4: each worktree group's isolated provider stack, bound to its worktree + repo."""
    worktrees_root = coordination_root / "worktrees"
    records: list[dict[str, Any]] = []
    container_names: set[str] = set()
    settings_by_path: dict[str, dict[str, Any] | None] = {}
    runtime_specs_by_path: dict[str, dict[str, dict[str, list[str]]]] = {}
    for path in surface_paths(
        worktrees_root, "*/*/provider-runtime/provider-state.json", observation
    ):
        group = path.parent.parent.name
        payload = _read_json(path)
        if payload is None or payload.get("schema") != WORKTREE_PROVIDER_STATE_SCHEMA:
            if observation is not None:
                observation.complete = False
            continue
        _observe_worktree_configuration(payload, group, observation)
        if active_worktree_groups is not None and group not in active_worktree_groups:
            continue
        settings = _worktree_provider_settings(payload, settings_by_path, observation=observation)
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
    if inspected is None and observation is not None:
        observation.complete = False
    nodes: list[ProviderNode] = []
    for record in records:
        settings_key = record["settingsKey"]
        runtime_specs = (
            runtime_specs_by_path.get(str(settings_key), {}) if settings_key is not None else {}
        )
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


def _worktree_provider_ids(payload: dict[str, Any]) -> list[str]:  # pragma: no cover
    settings = payload.get("isolatedProviderSettings")
    providers = settings.get("providers") if isinstance(settings, dict) else None
    if not isinstance(providers, list):
        return []
    return [str(provider) for provider in providers]


def _worktree_settings_key(payload: dict[str, Any]) -> str | None:
    settings = payload.get("isolatedProviderSettings")
    raw_path = settings.get("path") if isinstance(settings, dict) else None
    return raw_path if isinstance(raw_path, str) and raw_path else None


def _worktree_provider_settings(  # pragma: no cover
    payload: dict[str, Any],
    cache: dict[str, dict[str, Any] | None],
    *,
    observation: AttentionObservation | None = None,
) -> dict[str, Any] | None:
    raw_path = _worktree_settings_key(payload)
    if raw_path is None:
        return None
    if raw_path not in cache:
        cache[raw_path] = _read_json(Path(raw_path))
    if cache[raw_path] is None and observation is not None:
        observation.complete = False
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


def _cgc_runtime_spec(provider: dict[str, Any]) -> dict[str, list[str]]:  # pragma: no cover
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


def _container_name(value: Any) -> str | None:  # pragma: no cover
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


def _worktree_runtime_summary(  # pragma: no cover
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


def _observe_worktree_configuration(
    payload: dict[str, Any], group: str, observation: AttentionObservation | None
) -> None:
    if observation is None:
        return
    settings = payload.get("isolatedProviderSettings")
    providers = settings.get("providers") if isinstance(settings, dict) else None
    if not isinstance(providers, list) or not all(
        isinstance(provider, str) for provider in providers
    ):
        observation.complete = False
        return
    for provider in providers:
        observation.observe(f"provider-down:{provider}@{group}")
