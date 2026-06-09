"""Current provider runtime state projection and persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope

STATE_VERSION = 1


def build_current_provider_state(
    config: McpRuntimeConfig,
    status: dict[str, Any],
) -> dict[str, Any]:
    checked_at = datetime.now(UTC).isoformat()
    providers = current_provider_states(config, status)
    state = aggregate_state(providers)
    return {
        "version": STATE_VERSION,
        "kind": "provider-current-state",
        "instance": current_state_instance(config),
        "state": state,
        "ok": state == "ready",
        "checkedAt": checked_at,
        "settingsFile": status.get("settingsFile", ""),
        "enabled": status.get("enabled", {}),
        "processNamespace": status.get("processNamespace"),
        "providers": providers,
    }


def write_current_provider_state(
    config: McpRuntimeConfig,
    status: dict[str, Any],
) -> dict[str, Any]:
    payload = build_current_provider_state(config, status)
    path = current_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": path.as_posix(), "state": payload}


def current_state_path(config: McpRuntimeConfig) -> Path:
    instance = current_state_instance(config)
    return (
        config.coordination_root
        / "logs"
        / "providers"
        / "status"
        / instance["scope"]
        / instance["id"]
        / "current.json"
    )


def current_state_instance(config: McpRuntimeConfig) -> dict[str, str]:
    providers = list(config.providers.values())
    scopes = {provider.scope for provider in providers}
    instance_ids = {provider.instance_id for provider in providers}
    if len(scopes) == 1 and len(instance_ids) == 1:
        return {
            "id": next(iter(instance_ids)),
            "scope": next(iter(scopes)),
            "coordinationRoot": config.coordination_root.as_posix(),
        }

    digest = hashlib.sha1(
        "\n".join(
            f"{provider.provider_id}:{provider.scope}:{provider.instance_id}"
            for provider in sorted(providers, key=lambda item: item.provider_id)
        ).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "id": f"mixed-{digest}",
        "scope": "mixed",
        "coordinationRoot": config.coordination_root.as_posix(),
    }


def current_provider_states(
    config: McpRuntimeConfig,
    status: dict[str, Any],
) -> dict[str, Any]:
    results = _result_list(status.get("results"))
    enabled = status.get("enabled")
    enabled = enabled if isinstance(enabled, dict) else {}
    by_provider = {
        str(result.get("provider")): result for result in results if isinstance(result, dict)
    }
    providers: dict[str, Any] = {}
    if "grepai-memory" in config.providers:
        provider = config.providers["grepai-memory"]
        if enabled.get("grepai-memory") is False:
            providers["grepai-memory"] = disabled_provider_state(provider)
        else:
            providers["grepai-memory"] = grepai_current_state(
                provider,
                by_provider.get("grepai", {}),
            )
    if "codegraphcontext-code" in config.providers:
        provider = config.providers["codegraphcontext-code"]
        if enabled.get("codegraphcontext-code") is False:
            providers["codegraphcontext-code"] = disabled_provider_state(provider)
        else:
            providers["codegraphcontext-code"] = cgc_current_state(
                provider,
                by_provider.get("codegraphcontext", {}),
            )
    return providers


def disabled_provider_state(provider: ProviderScope) -> dict[str, Any]:
    return {
        "id": provider.provider_id,
        "state": "disabled",
        "ok": None,
        "enabled": False,
        "runtimeRoot": provider.runtime_root.as_posix(),
        "logRoot": provider.log_root.as_posix(),
        "watcherUp": False,
        "indexingState": "disabled",
        "resources": {},
    }


def grepai_current_state(provider: ProviderScope, result: dict[str, Any]) -> dict[str, Any]:
    backend = container_resource(result.get("backend", {}))
    embedder = container_resource(result.get("embedder", {}))
    watcher = container_resource(result.get("watcher", {}))
    watcher_up = watcher.get("running") is True
    state = provider_state(result.get("ok"), [backend, embedder, watcher])
    if state == "ready" and not grepai_workspace_present(result):
        # Containers are up but the watcher has no searchable workspace, so every
        # search fails. Readiness must reflect that instead of trusting liveness.
        state = "degraded"
    return {
        "id": provider.provider_id,
        "state": state,
        "ok": state == "ready",
        "enabled": True,
        "runtimeRoot": provider.runtime_root.as_posix(),
        "logRoot": provider.log_root.as_posix(),
        "watcherUp": watcher_up,
        "indexingState": grepai_indexing_state(result, watcher_up=watcher_up),
        "resources": {
            "postgres": backend,
            "ollama": embedder,
            "watcher": watcher,
        },
    }


def cgc_current_state(provider: ProviderScope, result: dict[str, Any]) -> dict[str, Any]:
    backend = container_resource(result.get("backend", {}))
    repos: dict[str, Any] = {}
    for child in _result_list(result.get("results")):
        repo_id = str(child.get("repoId") or "unknown")
        repos[repo_id] = cgc_repo_state(child)
    resources = [backend, *repos.values()]
    state = provider_state(result.get("ok"), resources)
    if state == "ready" and any(repo.get("state") != "ready" for repo in repos.values()):
        # The raw status ok only proves images/artifacts/containers; a repo
        # target degraded by graph content must degrade the provider too.
        state = "degraded"
    return {
        "id": provider.provider_id,
        "state": state,
        "ok": state == "ready",
        "enabled": True,
        "runtimeRoot": provider.runtime_root.as_posix(),
        "logRoot": provider.log_root.as_posix(),
        "indexingState": cgc_indexing_state(repos),
        "resources": {
            "falkordb": backend,
            "watchers": repos,
        },
    }


def cgc_repo_state(result: dict[str, Any]) -> dict[str, Any]:
    process = result.get("process")
    process = process if isinstance(process, dict) else {}
    container_state = process.get("containerState")
    container = normalize_container_state(container_state if isinstance(container_state, dict) else {})
    watcher_up = process.get("alive") is True
    indexing_state = str(result.get("indexingState") or "unknown")
    state = "ready" if result.get("ok") and watcher_up else ("failed" if not watcher_up else "degraded")
    if state == "ready" and indexing_state in {"empty", "backend-unreachable"}:
        # A live watcher over an empty or unreachable graph serves no queries;
        # readiness must reflect graph content, not container liveness. An
        # in-progress initial scan ("indexing") stays ready.
        state = "degraded"
    return {
        "state": state,
        "ok": state == "ready",
        "repoId": result.get("repoId"),
        "watcherUp": watcher_up,
        "indexingState": indexing_state,
        "lastRefresh": result.get("lastRefresh"),
        "containerName": process.get("containerName"),
        **container,
    }


def container_resource(result: Any) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    container = result.get("containerState")
    data = normalize_container_state(container if isinstance(container, dict) else {})
    return {
        "state": resource_state(result, data),
        "ok": result.get("ok"),
        "containerName": result.get("containerName"),
        "image": result.get("image"),
        **data,
    }


def normalize_container_state(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "containerState": str(value.get("containerState") or "unknown"),
        "running": value.get("running") is True,
        "startedAt": value.get("startedAt"),
        "uptimeSeconds": value.get("uptimeSeconds"),
        "health": value.get("health"),
    }


def resource_state(result: dict[str, Any], container: dict[str, Any]) -> str:
    if container["containerState"] == "missing":
        return "missing"
    if container["running"] and result.get("ok") is not False:
        return "ready"
    if container["running"]:
        return "degraded"
    return "failed"


def provider_state(ok: Any, resources: list[dict[str, Any]]) -> str:
    if ok is True:
        return "ready"
    if any(resource.get("state") in {"ready", "degraded"} for resource in resources):
        return "degraded"
    if resources:
        return "failed"
    return "unknown"


def aggregate_state(providers: dict[str, Any]) -> str:
    raw_states = [str(provider.get("state", "unknown")) for provider in providers.values()]
    if not raw_states:
        return "noProviders"
    states = [state for state in raw_states if state != "disabled"]
    if not states:
        return "disabled"
    distinct = set(states)
    if distinct == {"ready"}:
        return "ready"
    if distinct == {"unknown"}:
        return "unknown"
    if distinct & {"ready", "degraded"}:
        return "degraded"
    return "failed"


def grepai_workspace_present(result: dict[str, Any]) -> bool:
    """True only when the grepai watcher reports a real, searchable workspace.

    `grepai workspace status` exits 0 even when it prints "No workspaces
    configured", so the return code alone cannot prove a searchable workspace —
    the stdout has to be inspected too.
    """
    watcher = result.get("watcher")
    watcher = watcher if isinstance(watcher, dict) else {}
    workspace_status = watcher.get("workspaceStatus")
    if not isinstance(workspace_status, dict) or workspace_status.get("returncode") != 0:
        return False
    return "No workspaces configured" not in str(workspace_status.get("stdout") or "")


def grepai_indexing_state(result: dict[str, Any], *, watcher_up: bool) -> str:
    if not watcher_up:
        return "unavailable"
    # Workspace present => indexing progress is genuinely unknown (the watcher
    # status does not report it); absent => nothing is searchable.
    return "unknown" if grepai_workspace_present(result) else "noWorkspace"


def cgc_indexing_state(repos: dict[str, Any]) -> str:
    states = {str(repo.get("indexingState") or "unknown") for repo in repos.values()}
    if not states:
        return "unknown"
    if len(states) == 1:
        return next(iter(states))
    return "mixed"


def _result_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
