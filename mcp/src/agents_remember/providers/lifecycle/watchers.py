"""All-provider watcher lifecycle orchestration."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from agents_remember.providers.cgc.lifecycle import (
    cgc_stop_all,
)
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.grepai.lifecycle import grepai_run
from agents_remember.providers.lifecycle.process_status import (
    process_namespace_status,
    require_durable_process_namespace,
)
from agents_remember.providers.lifecycle.provider_settings import (
    context_provider_enabled,
)


def watcher_scoped_args(args: argparse.Namespace, provider: str, action: str) -> argparse.Namespace:
    values = vars(args).copy()
    values["provider"] = provider
    values["action"] = action
    values.setdefault("from_settings", None)
    if provider == "cgc":
        values["repo_id"] = None
        values["code_repo_root"] = None
    if provider == "grepai":
        values.setdefault("force", False)
        values.setdefault("root", None)
        values.setdefault("runtime_root", None)
    return argparse.Namespace(**values)


def watcher_error_result(provider: str, action: str, error: BaseException) -> dict[str, Any]:
    return {
        "provider": provider,
        "action": action,
        "ok": False,
        "error": str(error),
        "recoveryAction": f"Run provider-specific {provider} status and retry {provider} {action} after resolving the reported error.",
    }


def watcher_recovery_actions(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for result in results:
        if result.get("ok"):
            continue
        recovery = result.get("recoveryAction")
        if not recovery:
            recovery = f"Inspect {result.get('provider', 'provider')} {result.get('action', 'status')} output and retry the failed provider action."
        actions.append(
            {
                "provider": str(result.get("provider", "unknown")),
                "action": str(result.get("action", "unknown")),
                "recoveryAction": str(recovery),
            }
        )
    return actions


# Stop-class actions only: the start and status branches died with the provider
# surface (L192-F01). Nothing outside this module asks a watcher to start, and the
# status question is answered by the runtime install's own report, so a map entry
# for either would be a branch with no production caller.
WATCHER_PROVIDER_ACTIONS = {
    "grepai": {
        "stop": "stop",
        "shutdown-all": "stop",
    },
    "cgc": {
        "stop": "stop-all",
        "shutdown-all": "shutdown-all",
    },
}


def watcher_enabled_providers(args: argparse.Namespace) -> tuple[Any, bool, bool]:
    settings_path, grepai_enabled = context_provider_enabled(
        getattr(args, "from_settings", None),
        "grepai-memory",
    )
    _, cgc_enabled = context_provider_enabled(
        getattr(args, "from_settings", None),
        "codegraphcontext-code",
    )
    return settings_path, grepai_enabled, cgc_enabled


def watcher_grepai_result(args: argparse.Namespace, action: str) -> dict[str, Any]:
    grepai_action = WATCHER_PROVIDER_ACTIONS["grepai"][action]
    try:
        return grepai_run(watcher_scoped_args(args, "grepai", grepai_action), grepai_action)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        return watcher_error_result("grepai", grepai_action, error)


def watcher_cgc_result(args: argparse.Namespace, action: str) -> dict[str, Any]:
    cgc_action = WATCHER_PROVIDER_ACTIONS["cgc"][action]
    scoped = watcher_scoped_args(args, "cgc", cgc_action)
    cgc_handlers = {
        "stop-all": cgc_stop_all,
        "shutdown-all": cgc_stop_all,
    }
    try:
        return cgc_handlers[cgc_action](scoped)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        return watcher_error_result("codegraphcontext", cgc_action, error)


def watchers_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
    require_durable_watcher_namespace(args, action)
    settings_path, grepai_enabled, cgc_enabled = watcher_enabled_providers(args)
    results = watcher_provider_results(args, action, grepai_enabled, cgc_enabled)
    ok = watcher_results_ok(results)
    return {
        "provider": "watchers",
        "action": action,
        "ok": ok,
        "partial": watcher_results_partial(results, ok),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "processNamespace": process_namespace_status(),
        "enabled": {
            "grepai-memory": grepai_enabled,
            "codegraphcontext-code": cgc_enabled,
        },
        "results": results,
        "recoveryActions": watcher_recovery_actions(results),
    }


def require_durable_watcher_namespace(args: argparse.Namespace, action: str) -> None:
    if action in {"stop", "shutdown-all"} and not args.dry_run:
        require_durable_process_namespace(f"watchers {action}")


def watcher_provider_results(
    args: argparse.Namespace,
    action: str,
    grepai_enabled: bool,
    cgc_enabled: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if grepai_enabled:
        results.append(watcher_grepai_result(args, action))
    if cgc_enabled:
        results.append(watcher_cgc_result(args, action))
    return results


def watcher_results_ok(results: list[dict[str, Any]]) -> bool:
    return all(result.get("ok") for result in results)


def watcher_results_partial(results: list[dict[str, Any]], ok: bool) -> bool:
    return any(result.get("ok") for result in results) and not ok
