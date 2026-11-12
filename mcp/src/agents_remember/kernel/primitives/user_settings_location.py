"""The single user settings location and the refusal of its removed location.

The starter generator emits these same stdlib definitions before AR is installed.
Content reads and the explicit upgrade belong to their existing callers.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from pathlib import Path


class UserSettingsLocationError(ValueError):
    """The user's settings location needs an explicit correction or upgrade."""


def user_settings_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(configured) if configured else Path.home() / ".config"
    if not config_home.is_absolute():
        raise UserSettingsLocationError("XDG_CONFIG_HOME must be an absolute directory")
    return config_home / "agents-remember" / "settings.json"


def settings_upgrade_command(coordination_root: Path) -> str:
    return shlex.join(
        ["agents-remember", "upgrade-user-settings", "--coordination-root", str(coordination_root)]
    )


def require_user_settings_path(
    coordination_root: Path, exists: Callable[[Path], bool] = Path.exists
) -> Path:
    """Refuse the old location without reading or migrating its contents."""
    old = coordination_root / "system" / "settings.json"
    new = user_settings_path()
    if exists(old) or old.is_symlink():
        state = "both files exist" if exists(new) or new.is_symlink() else "old file exists"
        raise UserSettingsLocationError(
            f"agentic-settings-location-upgrade-required: {state}; old: {old}; user: {new}; "
            f"upgrade command: {settings_upgrade_command(coordination_root)}. "
            "The old file is not read; when both exist, resolve the collision before upgrading."
        )
    return new
