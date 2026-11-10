"""Read-only host installation planning and shared descriptions of owned actions.

The plan observes configured values, not resolved provider values. Provider entries therefore
remain IDs-only in both the preview and the real install's change list.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.serving.paseo.paseo_daemon import plugin_entry
from agents_remember.serving.paseo.paseo_daemon_config import (
    CONFIG_FILE,
    PROVIDER_ENTRIES,
    provider_recovery_plan,
)
from agents_remember.serving.paseo.paseo_node import _temporaries
from agents_remember.serving.paseo.paseo_plugin_files import (
    AR_HOME_DIRECTORY,
    PLUGIN_ID,
    embed_path,
    installed_plugin_path,
    read_embed,
    read_loaded_stamp,
    tree_digest,
)
from agents_remember.serving.paseo.paseo_process_record import inspect_record
from agents_remember.serving.paseo.paseo_run import _Run
from agents_remember.serving.paseo.paseo_settings import (
    DaemonSetting,
    pending_settings,
    pending_settings_from_document,
)

STAGING_DIRECTORY = ".ar-staging"
PREVIOUS_DIRECTORY = ".ar-previous"


@dataclass(frozen=True)
class InstallObservation:
    node_present: bool
    installed_version: str | None
    install_required: bool


def installed_manifest_version(prefix: Path) -> str | None:
    """Observe a CLI awaiting Node acquisition; the real pass validates its executable later."""
    manifest = prefix / "node_modules" / "@getpaseo" / "cli" / "package.json"
    if not manifest.is_file():
        return None
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        return None
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) else None


def setting_change(setting: DaemonSetting) -> dict[str, Any]:
    """Describe a setting without exposing provider environment or credentials."""
    facts = (
        {"providerIds": sorted(setting.value)}
        if setting.path == PROVIDER_ENTRIES
        else {"value": setting.value}
    )
    return {
        "step": "config",
        "action": "set",
        "path": setting.path,
        **facts,
        "applies": setting.applies,
    }


def home_changes(settings: PaseoRuntimeSettings, source: Path) -> list[dict[str, Any]]:
    """The exact product-owned embed and plugin files a pass will replace."""
    changes = []
    if read_embed(settings.home) != settings.embed_payload():
        changes.append(
            {
                "step": "embed",
                "action": "written",
                "entries": len(settings.embed),
                "path": embed_path(settings.home).as_posix(),
            }
        )
    target = installed_plugin_path(settings.home)
    digest = tree_digest(source)
    if tree_digest(target) != digest:
        changes.append(
            {"step": "plugin", "action": "copied", "path": target.as_posix(), "digest": digest}
        )
    return changes


def plugin_changes(entry: dict[str, Any] | None, target: Path, stale: bool) -> list[dict[str, Any]]:
    """Choose the same single load operation after any wrong-path registration is removed."""
    changes = []
    if entry is not None and entry.get("path") != target.as_posix():
        changes.append({"step": "plugin", "action": "removed", "path": entry.get("path")})
        entry = None
    if entry is None:
        action = "installed"
    elif entry.get("enabled") is False:
        action = "enabled"
    elif stale or entry.get("status") != "running":
        action = "reloaded"
    else:
        return changes
    return [*changes, {"step": "plugin", "action": action}]


def embed_digest(settings: PaseoRuntimeSettings) -> str:
    payload = json.dumps(settings.embed_payload(), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def planned_changes(run: _Run, observed: InstallObservation) -> list[dict[str, Any]]:
    """Describe a successful pass on these observations; execute no writes or reloads.

    Admission has already applied the install's no-stop policy. Late command failures and
    changes made by another process are still reported by the real operation, not promised
    here as successful actions.
    """
    if run.restart_reasons:
        return []
    settings = run.settings
    changes = _node_changes(observed)
    restored, document = provider_recovery_plan(settings.home)
    changes.extend(_recovery_changes(settings.home, restored))
    record = inspect_record(settings.home, "install", run.reader)
    running = record.kind == "own"
    if record.kind == "stale":
        changes.append(
            {
                "step": "daemon",
                "action": "removed-stale-record",
                "pid": record.pid,
                "reason": record.reason,
            }
        )
    changes.extend(_installation_changes(settings, observed))
    pending = (
        pending_settings(run.cli)
        if observed.node_present and observed.installed_version is not None and restored is None
        else pending_settings_from_document(settings, document)
    )
    changes.extend(_settings_changes(pending, running, restored))
    changes.extend(home_changes(settings, run.plugin_source))
    if not running:
        changes.append({"step": "daemon", "action": "started", "listen": settings.listen})
    target = installed_plugin_path(settings.home)
    loaded = {"plugin": tree_digest(run.plugin_source), "embed": embed_digest(settings)}
    stale = running and read_loaded_stamp(settings.home) != loaded
    entry = plugin_entry(run.cli, "plugin") if running else _configured_plugin(document)
    changes.extend(plugin_changes(entry, target, stale))
    return changes


def _node_changes(observed: InstallObservation) -> list[dict[str, Any]]:
    node = product_node()
    if not observed.node_present:
        return [
            {
                "step": "node",
                "action": "installed",
                "version": node.version,
                "path": node.node.as_posix(),
            }
        ]
    return [
        {"step": "node", "action": "removed-leftover", "path": path.as_posix()}
        for path in _temporaries(node)
        if path.exists() or path.is_symlink()
    ]


def _recovery_changes(home: Path, restored: str | None) -> list[dict[str, Any]]:
    changes = [
        {"step": "config", "action": "removed-leftover", "path": path.as_posix()}
        for path in [
            *home.glob(f".{CONFIG_FILE}.*.tmp"),
            *(home / AR_HOME_DIRECTORY).glob(".config-*.tmp"),
        ]
    ]
    if restored is not None:
        changes.append(
            {"step": "config", "action": "restored", "path": PROVIDER_ENTRIES, "restored": restored}
        )
    return changes


def _installation_changes(
    settings: PaseoRuntimeSettings, observed: InstallObservation
) -> list[dict[str, Any]]:
    if observed.install_required:
        return [
            {
                "step": "install",
                "action": "replaced"
                if (settings.install_prefix / "node_modules").exists()
                else "installed",
                "previousVersion": observed.installed_version,
                "version": settings.version,
            }
        ]
    return [
        {"step": "install", "action": "removed-leftover", "path": path.as_posix()}
        for name in (STAGING_DIRECTORY, PREVIOUS_DIRECTORY)
        if (path := settings.install_prefix / name).exists()
    ]


def _settings_changes(
    pending: list[DaemonSetting], running: bool, restored: str | None
) -> list[dict[str, Any]]:
    changes = []
    for applies in ("live", "start"):
        for setting in pending:
            if setting.applies == applies:
                changes.append(setting_change(setting))
                if setting.path == PROVIDER_ENTRIES and running:
                    changes.append(
                        {"step": "config", "action": "reloaded", "path": PROVIDER_ENTRIES}
                    )
        if (
            applies == "live"
            and restored is not None
            and running
            and not any(item.path == PROVIDER_ENTRIES for item in pending)
        ):
            changes.append({"step": "config", "action": "reloaded", "path": PROVIDER_ENTRIES})
    return changes


def _configured_plugin(document: object) -> dict[str, Any] | None:
    plugins = document.get("plugins") if isinstance(document, dict) else None
    entry = plugins.get(PLUGIN_ID) if isinstance(plugins, dict) else None
    # A stopped daemon loads an enabled registration while starting. Its file has no live status.
    return {**entry, "status": "running"} if isinstance(entry, dict) else None
