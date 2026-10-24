"""Provider lifecycle settings derived from MCP authority settings."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


def lifecycle_settings_from_config() -> dict[str, Any]:
    # MIK-R112 F02: the two retired providers are never emitted into generated
    # lifecycle settings, however the authority map reads. Readers already
    # treat them as never-enabled (setup_common.provider_enabled); emitting no
    # blocks keeps generated documents truthful. Legacy user settings files
    # still parse (parse_providers is untouched) and old installations keep
    # loading; the explicitly required old-watcher stop does not read this.
    # MIK-R112 F14: the config parameter went away with the retired branches;
    # callers pass nothing (the settings no longer depend on configuration).
    providers: dict[str, Any] = {}
    return {
        "contextProviders": {
            "enabled": bool(providers),
            "providers": providers,
        }
    }


def write_lifecycle_settings() -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix="-agents-remember-provider-settings.json",
    ) as handle:
        path = Path(handle.name)
        json.dump(lifecycle_settings_from_config(), handle, indent=2)
    return path
