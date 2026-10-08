"""The settings provision owns, and which changes require a host restart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.serving.paseo.paseo_command import PaseoCli
from agents_remember.serving.paseo.paseo_daemon_config import (
    AGENT_TOOLS_SETTING,
    AGENT_TOOLS_VALUE,
    PROVIDER_ENTRIES,
)


@dataclass(frozen=True)
class DaemonSetting:
    """One daemon configuration path provision writes, and when Paseo applies a change to it."""

    path: str
    value: Any
    applies: Literal["start", "live"]


def daemon_settings(settings: PaseoRuntimeSettings) -> tuple[DaemonSetting, ...]:
    """The complete list of daemon settings provision writes; every other key is left alone.

    ``start`` settings are read only when the daemon starts; ``live`` ones are applied by Paseo's
    configuration reload. The split follows Paseo's configuration reference and was confirmed by
    run on 0.11.0-beta.2 for every path except relay enablement, which is never switched on here.
    Dictation and voice mode are off before the first start, so no speech model is downloaded.
    Paseo's own agent tools are written off, not left to Paseo's default (PNT-R06): Paseo lists
    that path among the ones it applies on a reload.
    """
    return (
        DaemonSetting("daemon.listen", settings.listen, "start"),
        DaemonSetting("daemon.relay.enabled", False, "live"),
        DaemonSetting(AGENT_TOOLS_SETTING, AGENT_TOOLS_VALUE, "live"),
        DaemonSetting("features.webUi.enabled", True, "start"),
        DaemonSetting("features.dictation.enabled", False, "start"),
        DaemonSetting("features.voiceMode.enabled", False, "start"),
        DaemonSetting("pluginsEnabled", True, "live"),
        DaemonSetting(PROVIDER_ENTRIES, settings.providers, "live"),
    )


def pending_settings(cli: PaseoCli) -> list[DaemonSetting]:
    document = cli.json("config", "daemon", "config", "get", "--json")
    current = document.get("value")
    return [
        setting
        for setting in daemon_settings(cli.settings)
        if _configured_value(current, setting) != setting.value
    ]


def _configured_value(current: object, setting: DaemonSetting) -> Any:
    value = current
    for key in setting.path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    # A configuration without provider entries and an empty provider object are the same state.
    return {} if value is None and setting.value == {} else value


def restart_reasons(
    settings: PaseoRuntimeSettings, status: dict[str, Any], pending: list[DaemonSetting]
) -> list[str]:
    """Why a running daemon must be restarted: its version, or a setting applied only at start."""
    reasons = [f"setting:{item.path}" for item in pending if item.applies == "start"]
    version = status.get("daemonVersion")
    if version and version != settings.version:
        reasons.append("version")
    if status.get("listen") != settings.listen and "setting:daemon.listen" not in reasons:
        reasons.append("listen")
    return reasons
