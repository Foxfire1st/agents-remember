"""Copy the convention settings file into the user's config directory once."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from agents_remember.install.effects import observe_install_effect
from agents_remember.kernel.primitives.mcp_settings_location import mcp_settings_path

OLD_SETTINGS_CONVENTION = Path(".claude/mcp/agents-remember-settings.json")


def copy_mcp_settings(workspace_root: Path, *, dry_run: bool) -> dict[str, str]:
    """Create an absent destination exclusively, leaving the source and permissions alone."""
    old = workspace_root / OLD_SETTINGS_CONVENTION
    new = mcp_settings_path()
    result = {"sourcePath": str(old), "userPath": str(new)}
    try:
        try:
            new.lstat()
        except FileNotFoundError:
            pass
        else:
            return {**result, "state": "already-present"}
        try:
            old.lstat()
        except FileNotFoundError:
            return {**result, "state": "no-source"}
        if dry_run:
            return {**result, "state": "would-copy"}
        with observe_install_effect(new, "settings-copy", dry_run), old.open("rb") as source:
            new.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            destination = os.fdopen(handle, "wb")
            completed = False
            try:
                with destination:
                    shutil.copyfileobj(source, destination)
                shutil.copystat(old, new)
                completed = True
            finally:
                if not completed:
                    new.unlink()
    except OSError as error:
        return {
            **result,
            "state": "permission" if isinstance(error, PermissionError) else "refused",
            "detail": str(error),
            "remedy": (
                f"Ask the developer to resolve the settings-copy error for {old} to {new}, "
                "then rerun runtime_install. This install did not change the config "
                "permissions. Check for an incomplete destination if cleanup failed."
            ),
        }
    return {**result, "state": "copied"}
