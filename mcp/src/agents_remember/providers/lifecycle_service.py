"""Typed provider lifecycle service API for MCP callers."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers import lifecycle
from agents_remember.providers.context import ContextProviderError


@dataclass(frozen=True)
class ProviderLifecycleServiceConfig:
    """Settings-owned lifecycle context for MCP/service callers."""

    coordination_root: Path
    settings_path: Path
    dry_run: bool = True
    timeout: int = 60

    def normalized(self) -> ProviderLifecycleServiceConfig:
        return ProviderLifecycleServiceConfig(
            coordination_root=self.coordination_root.resolve(),
            settings_path=self.settings_path.resolve(),
            dry_run=self.dry_run,
            timeout=self.timeout,
        )


#: The stop-class actions a production caller actually asks for. `status` and
#: `start` were admitted here while a generic facade parameter was mistaken for a
#: caller; the lifecycle dispatcher rejects start and the watcher path is stop-only
#: (L192-F01), so the admission list names the stop family and nothing else.
WATCHER_SERVICE_ACTIONS: frozenset[str] = frozenset({"stop", "shutdown-all"})


def run_watchers_lifecycle(
    service_config: ProviderLifecycleServiceConfig,
    *,
    action: str,
) -> dict[str, Any]:
    """Run a stop-class all-provider watcher action from trusted settings."""

    if action not in WATCHER_SERVICE_ACTIONS:
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
