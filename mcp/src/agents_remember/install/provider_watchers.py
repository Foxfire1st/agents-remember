"""Provider watcher rebind orchestration for runtime installs."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents_remember.providers import lifecycle
from agents_remember.providers.recovery import PROVIDER_WATCHER_RESTART_RECOVERY


@dataclass
class ProviderWatcherRebindReport:
    """Lifecycle results from a runtime-install watcher rebind."""

    runs: list[dict[str, Any]] = field(default_factory=list)
    ok: bool | None = None
    recovery_actions: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "attempted": True,
            "ok": self.ok,
            "runs": self.runs,
            "recoveryActions": self.recovery_actions,
        }


def write_temp_provider_settings(settings: dict[str, Any]) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix="-agents-remember-provider-settings.json",
    ) as handle:
        path = Path(handle.name)
        json.dump(settings, handle, indent=2)
    return path


@dataclass(frozen=True)
class ProviderWatcherRebind:
    """One runtime-install watcher rebind.

    A rebind is the stop → refresh → start cycle a runtime install performs
    around the provider tree: the coordination root it acts on, the live
    provider settings its lifecycle actions are derived from, its execution
    mode and budget, and the report every phase accumulates into. Every phase
    of the cycle needs all of it, so the cycle travels as one object.
    """

    coordination_root: Path
    settings: dict[str, Any]
    dry_run: bool
    timeout: int
    report: ProviderWatcherRebindReport = field(default_factory=ProviderWatcherRebindReport)


def provider_watcher_lifecycle_args(
    coordination_root: Path,
    settings_path: Path,
    *,
    dry_run: bool,
    timeout: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        coordination_root=coordination_root,
        from_settings=settings_path,
        dry_run=dry_run,
        timeout=timeout,
        json=True,
    )


def run_provider_watcher_lifecycle(rebind: ProviderWatcherRebind, action: str) -> dict[str, Any]:
    settings_path = write_temp_provider_settings(rebind.settings)
    try:
        return lifecycle.watchers_run(
            provider_watcher_lifecycle_args(
                rebind.coordination_root,
                settings_path,
                dry_run=rebind.dry_run,
                timeout=rebind.timeout,
            ),
            action,
        )
    finally:
        settings_path.unlink(missing_ok=True)


def record_provider_watcher_lifecycle(
    rebind: ProviderWatcherRebind,
    *,
    phase: str,
    action: str,
) -> dict[str, Any]:
    result = run_provider_watcher_lifecycle(rebind, action)
    rebind.report.runs.append({"phase": phase, **result})
    return result


def provider_watcher_lifecycle_ok(result: dict[str, Any]) -> bool:
    return result.get("ok") is True and result.get("partial") is not True


def add_provider_watcher_recovery_actions(
    report: ProviderWatcherRebindReport,
    result: dict[str, Any],
) -> None:
    actions = result.get("recoveryActions")
    if isinstance(actions, list) and actions:
        report.recovery_actions.extend(action for action in actions if isinstance(action, dict))
        return
    report.recovery_actions.append(
        {
            "provider": str(result.get("provider") or "watchers"),
            "action": "restart",
            "recoveryAction": PROVIDER_WATCHER_RESTART_RECOVERY,
        }
    )


def stop_provider_watchers_before_refresh(rebind: ProviderWatcherRebind) -> None:
    result = record_provider_watcher_lifecycle(
        rebind,
        phase="pre-provider-refresh-stop",
        action="stop",
    )
    if provider_watcher_lifecycle_ok(result):
        return
    rebind.report.ok = False
    add_provider_watcher_recovery_actions(rebind.report, result)
    raise RuntimeError(
        "provider watcher stop failed before runtime provider refresh: "
        f"{json.dumps(result, indent=2)}"
    )


def complete_provider_watcher_rebind(rebind: ProviderWatcherRebind) -> None:
    report = rebind.report
    record_provider_watcher_lifecycle(rebind, phase="post-install-start", action="start")
    status = record_provider_watcher_lifecycle(
        rebind,
        phase="post-install-status",
        action="status",
    )
    if not provider_watcher_lifecycle_ok(status):
        report.messages.append(
            "Provider watchers were still degraded after runtime reinstall; "
            "attempted one non-destructive restart/rebind."
        )
        record_provider_watcher_lifecycle(rebind, phase="recovery-stop", action="stop")
        record_provider_watcher_lifecycle(rebind, phase="recovery-start", action="start")
        status = record_provider_watcher_lifecycle(
            rebind,
            phase="recovery-status",
            action="status",
        )
    report.ok = provider_watcher_lifecycle_ok(status)
    if not report.ok:
        add_provider_watcher_recovery_actions(report, status)
