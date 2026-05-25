"""Typed provider lifecycle service API for MCP callers."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers import lifecycle
from agents_remember.providers.context import ContextProviderError, stable_provider_id


@dataclass(frozen=True)
class ProviderLifecycleServiceConfig:
    """Settings-owned lifecycle context for MCP/service callers."""

    coordination_root: Path
    settings_path: Path
    dry_run: bool = True
    timeout: int = 60
    python: str = sys.executable

    def normalized(self) -> ProviderLifecycleServiceConfig:
        return ProviderLifecycleServiceConfig(
            coordination_root=self.coordination_root.resolve(),
            settings_path=self.settings_path.resolve(),
            dry_run=self.dry_run,
            timeout=self.timeout,
            python=self.python,
        )


def run_cgc_lifecycle(
    service_config: ProviderLifecycleServiceConfig,
    *,
    action: str,
    repo_id: str | None = None,
    native_args: list[str] | None = None,
    port: int = 8000,
    context: str | None = None,
) -> dict[str, Any]:
    """Run a typed CGC lifecycle action from trusted service settings."""

    allowed_actions = {"run", "visualize", "refresh-all"}
    if action not in allowed_actions:
        raise ValueError(f"unsupported CGC service action: {action}")
    config = service_config.normalized()
    args = argparse.Namespace(
        provider="cgc",
        action=action,
        coordination_root=config.coordination_root,
        from_settings=config.settings_path,
        dry_run=config.dry_run,
        json=False,
        timeout=config.timeout,
        repo_id=stable_provider_id(repo_id) if repo_id else None,
        code_repo_root=None,
        python=config.python,
        native_args=list(native_args or []),
        port=port,
        context=context,
        lifecycle_json=False,
    )
    try:
        handlers = {
            "run": lifecycle.cgc_run,
            "visualize": lifecycle.cgc_visualize,
            "refresh-all": lifecycle.cgc_refresh_all,
        }
        return handlers[action](args)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        return {
            "provider": "codegraphcontext",
            "action": action,
            "ok": False,
            "error": str(error),
        }


def run_grepai_lifecycle(
    service_config: ProviderLifecycleServiceConfig,
    *,
    action: str,
    native_args: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run a typed GrepAI lifecycle action from trusted service settings."""

    allowed_actions = {"run", "refresh"}
    if action not in allowed_actions:
        raise ValueError(f"unsupported GrepAI service action: {action}")
    config = service_config.normalized()
    args = argparse.Namespace(
        provider="grepai",
        action=action,
        coordination_root=config.coordination_root,
        from_settings=config.settings_path,
        dry_run=config.dry_run,
        json=False,
        timeout=config.timeout,
        force=force,
        root=None,
        runtime_root=None,
        native_args=list(native_args or []),
        lifecycle_json=False,
    )
    try:
        return lifecycle.grepai_run(args, action)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        return {
            "provider": "grepai",
            "action": action,
            "ok": False,
            "error": str(error),
        }


def run_watchers_lifecycle(
    service_config: ProviderLifecycleServiceConfig,
    *,
    action: str,
) -> dict[str, Any]:
    """Run an all-provider watcher lifecycle action from trusted settings."""

    allowed_actions = {"status", "start", "stop", "shutdown-all"}
    if action not in allowed_actions:
        raise ValueError(f"unsupported watcher service action: {action}")
    config = service_config.normalized()
    args = argparse.Namespace(
        provider="watchers",
        action=action,
        coordination_root=config.coordination_root,
        from_settings=config.settings_path,
        dry_run=config.dry_run,
        json=False,
        timeout=config.timeout,
    )
    try:
        return lifecycle.watchers_run(args, action)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        return {
            "provider": "watchers",
            "action": action,
            "ok": False,
            "error": str(error),
        }
