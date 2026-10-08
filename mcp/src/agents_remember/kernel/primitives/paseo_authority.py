"""The one installation-wide host authority, shared by every harness."""

from __future__ import annotations

import json
import warnings
from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    PaseoRuntimeSettingsError,
    parse_paseo_runtime_settings,
)


def paseo_runtime_path(coordination_root: Path) -> Path:
    return coordination_root / "system" / "settings.json"


def load_shared_paseo_runtime(coordination_root: Path) -> PaseoRuntimeSettings | None:
    """Read the shared block on every use; an absent file/block selects no host."""
    path = paseo_runtime_path(coordination_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PaseoRuntimeSettingsError(f"cannot read host settings {path}: {error}") from error
    if not isinstance(data, dict):
        raise PaseoRuntimeSettingsError(f"host settings must be a JSON object: {path}")
    settings = parse_paseo_runtime_settings(data.get("paseoRuntime"))
    return None if settings is None else replace(settings, source_path=path)


def warn_per_harness_paseo(data: dict, path: Path, coordination_root: Path) -> None:
    """The old block is ignored, with an explicit migration instruction and no fallback."""
    if "paseoRuntime" in data:
        warnings.warn(
            f"paseoRuntime in {path} is ignored; move that block to "
            f"{paseo_runtime_path(coordination_root)}, preserving explicit home, installPrefix "
            "and listen, then remove the per-harness block. The developer chooses when to "
            "change real settings and restart the host.",
            stacklevel=2,
        )
