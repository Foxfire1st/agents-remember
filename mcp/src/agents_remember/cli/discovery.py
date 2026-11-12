"""Read the user's tool-server settings without consulting workspace registrations.

Explicit --config arguments are handled by callers and bypass discovery.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agents_remember.cli.settings_provenance import discovered_settings_line
from agents_remember.kernel.primitives.mcp_settings_location import mcp_settings_path
from agents_remember.kernel.primitives.user_settings_location import UserSettingsLocationError


class ConfigDiscoveryError(Exception):
    """The user's tool-server settings cannot be used."""


def discover_config() -> Path:
    """Select the one user-config file, independently of the caller's directory."""
    try:
        path = mcp_settings_path()
    except UserSettingsLocationError as error:
        raise ConfigDiscoveryError(str(error)) from error
    root = _settings_coordination_root(path)
    if root is None:
        raise ConfigDiscoveryError(
            f"no trusted settings found at {path}: create that settings file with an existing "
            "absolute coordinationRoot, or pass --config with an explicit settings path"
        )
    print(discovered_settings_line(path, root), file=sys.stderr)
    return path


def _settings_coordination_root(path: Path) -> Path | None:
    """Accept JSON objects naming an existing absolute coordination directory."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    root = data.get("coordinationRoot") if isinstance(data, dict) else None
    if not isinstance(root, str) or not root:
        return None
    candidate = Path(root)
    return candidate if candidate.is_absolute() and candidate.is_dir() else None
