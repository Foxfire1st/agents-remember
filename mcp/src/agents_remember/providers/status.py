"""Read-only provider watcher status for context packets."""

from __future__ import annotations

import argparse
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope
from agents_remember.providers import provider_lifecycle
from agents_remember.providers.integrity import check_provider_runner_integrity
from agents_remember.providers.settings import write_lifecycle_settings


def provider_status_packet(
    config: McpRuntimeConfig,
    *,
    include_providers: bool = True,
    detail_limit: int = 20,
) -> dict[str, Any]:
    configured = bool(config.providers)
    if not include_providers:
        return {
            "configured": configured,
            "enabled": configured,
            "state": "skipped",
            "ok": None,
            "partial": False,
            "processNamespace": None,
            "items": [],
            "recoveryActions": [],
        }
    if not configured:
        return {
            "configured": False,
            "enabled": False,
            "state": "noProviders",
            "ok": True,
            "partial": False,
            "processNamespace": None,
            "items": [],
            "recoveryActions": [],
        }

    integrity = check_provider_runner_integrity(config)
    if integrity.get("ok") is False:
        return {
            "configured": True,
            "enabled": True,
            "state": "runnerIntegrityFailed",
            "ok": False,
            "partial": False,
            "processNamespace": None,
            "items": _configured_provider_items(config)[:detail_limit],
            "integrity": integrity,
            "recoveryActions": [
                {
                    "action": "runtime_install",
                    "reason": "provider runner files changed or were not recorded since install",
                }
            ],
        }

    status = _watchers_status(config)
    return {
        "configured": True,
        "enabled": any(status.get("enabled", {}).values()),
        "state": "checked",
        "ok": status.get("ok"),
        "partial": status.get("partial", False),
        "settingsFile": status.get("settingsFile", ""),
        "integrity": integrity,
        "processNamespace": status.get("processNamespace"),
        "items": _provider_items(config, status)[:detail_limit],
        "recoveryActions": status.get("recoveryActions", []),
        "rawStatus": status,
    }


def _watchers_status(config: McpRuntimeConfig) -> dict[str, Any]:
    settings_path = write_lifecycle_settings(config)
    try:
        args = argparse.Namespace(
            coordination_root=config.coordination_root,
            from_settings=settings_path,
            dry_run=False,
            timeout=config.timeout_caps.get("providerSeconds", 120),
            json=True,
        )
        return provider_lifecycle.watchers_run(args, "status")
    finally:
        settings_path.unlink(missing_ok=True)


def _provider_items(config: McpRuntimeConfig, status: dict[str, Any]) -> list[dict[str, Any]]:
    results = status.get("results", [])
    if not isinstance(results, list):
        results = []
    by_provider = {
        str(result.get("provider")): result for result in results if isinstance(result, dict)
    }
    items: list[dict[str, Any]] = []
    if "codegraphcontext-code" in config.providers:
        items.append(
            _provider_item(
                config.providers["codegraphcontext-code"],
                by_provider.get("codegraphcontext", {}),
            )
        )
    if "grepai-memory" in config.providers:
        items.append(
            _provider_item(config.providers["grepai-memory"], by_provider.get("grepai", {}))
        )
    return items


def _configured_provider_items(config: McpRuntimeConfig) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if "codegraphcontext-code" in config.providers:
        items.append(_provider_item(config.providers["codegraphcontext-code"], {}))
    if "grepai-memory" in config.providers:
        items.append(_provider_item(config.providers["grepai-memory"], {}))
    return items


def _provider_item(provider: ProviderScope, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": provider.provider_id,
        "runtimeRoot": provider.runtime_root.as_posix(),
        "logRoot": provider.log_root.as_posix(),
        "configured": True,
        "ok": result.get("ok"),
        "watcherState": _watcher_state(provider.provider_id, result),
        "freshness": _freshness_label(provider.provider_id),
        "rawStatus": result,
    }


def _watcher_state(provider_id: str, result: dict[str, Any]) -> str:
    state = "unknown"
    if not result:
        return state
    if provider_id == "grepai-memory":
        state = "running" if result.get("watcherRunning") else "stopped"
    elif provider_id == "codegraphcontext-code":
        child_results = result.get("results")
        if not isinstance(child_results, list):
            state = "running" if result.get("ok") else "stopped"
        else:
            alive_count = sum(
                1
                for child in child_results
                if isinstance(child, dict) and child.get("process", {}).get("alive") is True
            )
            if alive_count == len(child_results) and child_results:
                state = "running"
            elif alive_count:
                state = "partial"
            else:
                state = "stopped"
    return state


def _freshness_label(provider_id: str) -> str:
    if provider_id == "grepai-memory":
        return "replaceable-cache"
    return "unknown"
