"""Provider setup result summaries and operator log persistence."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUMMARY_VERSION = 1
MAX_TEXT_LENGTH = 500

SUMMARY_KEYS = (
    "provider",
    "action",
    "ok",
    "state",
    "partial",
    "enabled",
    "skipped",
    "dryRun",
    "reason",
    "stage",
    "operation",
    "returncode",
    "durationSeconds",
    "processNamespace",
    "process",
    "watcherRunning",
    "containerName",
    "containerId",
    "containers",
    "runtimeRoot",
    "logRoot",
    "settingsFile",
    "repoId",
    "projectId",
    "workspace",
    "strategy",
    "error",
)


def finalize_setup_payload(
    args: Any,
    payload: dict[str, Any],
    *,
    result_ok: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    results = _result_list(payload.get("results"))
    strict_ok = all(result_ok(result) for result in results)
    final_status = final_watcher_status(results)
    ready = _ready_state(payload, final_status)
    state = setup_state(strict_ok=strict_ok, ready=ready)

    payload["ok"] = strict_ok
    payload["ready"] = ready
    payload["state"] = state
    payload["failedPhases"] = [
        compact_result(result) for result in results if not result_ok(result)
    ]
    payload["finalStatus"] = final_status
    payload["resultCounts"] = result_counts(results, result_ok=result_ok)
    payload["setupSummary"] = write_setup_summary(args, payload)
    return payload


def setup_state(*, strict_ok: bool, ready: bool | None) -> str:
    if strict_ok:
        return "ok"
    if ready is True:
        return "ready-with-failed-phases"
    if ready is False:
        return "failed"
    return "failed-unchecked"


def result_counts(
    results: list[dict[str, Any]],
    *,
    result_ok: Callable[[dict[str, Any]], bool],
) -> dict[str, int]:
    return {
        "total": len(results),
        "ok": sum(1 for result in results if result_ok(result)),
        "failed": sum(1 for result in results if not result_ok(result)),
        "skipped": sum(1 for result in results if result.get("skipped")),
    }


def final_watcher_status(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for result in reversed(results):
        if result.get("provider") == "watchers" and result.get("action") == "status":
            payload = _nested_payload(result)
            return compact_result(payload or result)
    return None


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: _compact_value(result[key])
        for key in SUMMARY_KEYS
        if key in result and result[key] is not None
    }

    nested = _nested_payload(result)
    if nested is not None and nested is not result:
        compact["payload"] = compact_result(nested)

    children = _result_list(result.get("results"))
    if children:
        compact["results"] = [compact_result(child) for child in children]
    return compact


def write_setup_summary(args: Any, payload: dict[str, Any]) -> dict[str, Any]:
    paths = setup_summary_paths(args.coordination_root, args.action)
    summary = setup_summary_payload(payload)
    if getattr(args, "dry_run", False):
        return {
            "written": False,
            "reason": "dry-run",
            "last": paths["last"].as_posix(),
            "snapshot": paths["snapshot"].as_posix(),
            "lastFull": paths["lastFull"].as_posix(),
        }

    try:
        paths["last"].parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(summary, indent=2, sort_keys=True) + "\n"
        paths["last"].write_text(encoded, encoding="utf-8")
        paths["snapshot"].write_text(encoded, encoding="utf-8")
        # default=str keeps Path/other non-JSON values serializable; this copy is for humans.
        full_encoded = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
        paths["lastFull"].write_text(full_encoded, encoding="utf-8")
    except OSError as error:
        return {
            "written": False,
            "error": str(error),
            "last": paths["last"].as_posix(),
            "snapshot": paths["snapshot"].as_posix(),
            "lastFull": paths["lastFull"].as_posix(),
        }

    return {
        "written": True,
        "last": paths["last"].as_posix(),
        "snapshot": paths["snapshot"].as_posix(),
        "lastFull": paths["lastFull"].as_posix(),
    }


def setup_summary_paths(coordination_root: Path, action: str) -> dict[str, Path]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = coordination_root / "logs" / "providers" / "setup"
    return {
        "last": root / f"last-{action}.json",
        "snapshot": root / f"{timestamp}-{action}.json",
        # Full, untrimmed payload (command stdout/stderr included) for debugging. The compact
        # summary above drops command output, and the tool response is trimmed for the model's
        # context -- neither is a usable debug artifact when a provider step fails.
        "lastFull": root / f"last-{action}-full.json",
    }


def setup_summary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    isolated = payload.get("isolatedProviderSettings")
    if isinstance(isolated, dict):
        isolated = {
            key: value
            for key, value in isolated.items()
            if key != "settings"
        }

    return {
        "version": SUMMARY_VERSION,
        "kind": "provider-setup-summary",
        "generatedAt": datetime.now(UTC).isoformat(),
        "action": payload.get("action"),
        "ok": payload.get("ok"),
        "ready": payload.get("ready"),
        "state": payload.get("state"),
        "dryRun": payload.get("dryRun"),
        "coordinationRoot": payload.get("coordinationRoot"),
        "settingsFile": payload.get("settingsFile"),
        "enabled": payload.get("enabled", {}),
        "isolatedProviderSettings": isolated,
        "resultCounts": payload.get("resultCounts", {}),
        "failedPhases": payload.get("failedPhases", []),
        "finalStatus": payload.get("finalStatus"),
        "results": [compact_result(result) for result in _result_list(payload.get("results"))],
    }


def _ready_state(
    payload: dict[str, Any],
    final_status: dict[str, Any] | None,
) -> bool | None:
    if final_status is not None and isinstance(final_status.get("ok"), bool):
        return bool(final_status["ok"])
    enabled = payload.get("enabled", {})
    if isinstance(enabled, dict) and not any(value is True for value in enabled.values()):
        return True
    return None


def _nested_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("payload", "json"):
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return None


def _result_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT_LENGTH:
        return f"{value[:MAX_TEXT_LENGTH]}..."
    if isinstance(value, list):
        return [_compact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _compact_value(child) for key, child in value.items()}
    return value
