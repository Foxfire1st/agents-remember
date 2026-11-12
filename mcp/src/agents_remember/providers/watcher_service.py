"""Configured provider-watcher lifecycle service shared by upper performers."""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.primitives.runtime_config import (
    DEFAULT_DOCKER_CONTROL_SECONDS,
    McpRuntimeConfig,
)
from agents_remember.providers.lifecycle_service import (
    ProviderLifecycleServiceConfig,
    run_watchers_lifecycle,
)
from agents_remember.providers.settings import write_lifecycle_settings


def run_configured_watchers(
    config: McpRuntimeConfig,
    action: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run one stop-class watcher action against the authority settings.

    The action is a stop by construction: both production callers (the install refresh and the
    explicit ``providers-remove`` cleanup) ask for ``stop``, and the serving metrics loop's
    fail-safe callback asks for the same.

    e7f19b15 (MIK-R199 rule 5): the stop's settings come from the user's authority settings, which
    is the same reader and shape the install's old-watcher stop uses. The generated document
    ``lifecycle_settings_from_config()`` stays empty by design (R112 F02), so building the stop
    from it selected no provider however bad the sample was — and rule 5 says an empty launch
    document never authorizes suppressing safety. Reading the authority block is a stop input
    only: it grants no start, install, prepare, seed, query or watch authority, and rule 4's
    refusal paths are untouched. With no authority file to read there is nothing to select, and
    the stop reports that rather than inventing providers.
    """

    authority_path = config.config_path
    if authority_path.is_file():
        return _run_watcher_action(config, authority_path, action, dry_run)
    settings_path = write_lifecycle_settings()
    try:
        return _run_watcher_action(config, settings_path, action, dry_run)
    finally:
        settings_path.unlink(missing_ok=True)


def _run_watcher_action(
    config: McpRuntimeConfig,
    settings_path: Any,
    action: str,
    dry_run: bool,
) -> dict[str, Any]:
    data = run_watchers_lifecycle(
        ProviderLifecycleServiceConfig(
            coordination_root=config.coordination_root,
            settings_path=settings_path,
            dry_run=dry_run,
            timeout=DEFAULT_DOCKER_CONTROL_SECONDS,
        ),
        action=action,
    )
    return {**data, "operation": "provider_watchers", "ok": bool(data.get("ok"))}
