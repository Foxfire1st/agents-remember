"""Application entry point for MCP-owned runtime installation."""

from __future__ import annotations

from typing import Any, Protocol

from agents_remember.install.checkout_environments import inspect_checkout_environments
from agents_remember.install.runtime import (
    RuntimeInstallRequest,
    install_runtime_from_config,
    open_user_settings_notice,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.memory_quality.style.citations.language_scanner import (
    warm_repository_languages,
)
from agents_remember.models.core import RunningServerPayload
from agents_remember.serving.daemon import ensure_running_dashboard, plan_running_dashboard
from agents_remember.serving.paseo.paseo_install import install_host
from agents_remember.serving.server_registry import preview_server_inventory, server_inventory

__all__ = ["RuntimeInstallRequest", "open_user_settings_notice", "run_runtime_install"]


class SharedHeldStoreReconciler(Protocol):
    def __call__(
        self,
        config: McpRuntimeConfig,
        *,
        dry_run: bool,
        servers: list[RunningServerPayload],
    ) -> dict[str, Any]: ...


def run_runtime_install(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
    *,
    reconcile_shared_stores: SharedHeldStoreReconciler | None = None,
) -> dict[str, Any]:
    result = install_runtime_from_config(config, request)
    host = install_host(config, request.dry_run)
    result["host"] = host
    result["ok"] = result["ok"] and host["ok"]
    dashboard = (
        plan_running_dashboard(config) if request.dry_run else ensure_running_dashboard(config)
    )
    if dashboard is not None:
        result["dashboard"] = {
            "action": dashboard.action,
            "detail": dashboard.detail,
            **(
                {"pid": dashboard.state.pid, "sourceDigest": dashboard.state.source_digest}
                if dashboard.state is not None
                else {}
            ),
        }
        result["ok"] = result["ok"] and dashboard.action not in {"failed", "lock-held"}
    servers = preview_server_inventory(config) if request.dry_run else server_inventory(config)
    result["runningServers"] = [
        server.model_dump(mode="json", exclude_none=True) for server in servers
    ]
    if request.reconcile_shared_held_stores:
        if reconcile_shared_stores is None or not result["ok"]:
            shared_stores = {
                "state": "blocked",
                "reason": (
                    "Resolve the failed runtime installation before reconciling held stores."
                    if reconcile_shared_stores is not None
                    else "This runtime installation has no held-store reconciliation service."
                ),
            }
        else:
            shared_stores = reconcile_shared_stores(
                config, dry_run=request.dry_run, servers=servers
            )
        result["sharedHeldStores"] = shared_stores
        result["ok"] = result["ok"] and shared_stores["state"] != "blocked"
    if repository := config.repositories.get("agents-remember"):
        result["checkoutEnvironments"] = inspect_checkout_environments(repository.path)
    if config.repositories:
        result["languages"] = {
            repo_id: warm_repository_languages(repo.path, dry_run=request.dry_run).to_payload()
            for repo_id, repo in sorted(config.repositories.items())
            if repo.path.is_dir()
        }
    return result
