from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeCommandResult:
    returncode: int
    payload: dict[str, object]


@dataclass(frozen=True)
class WorktreeProviderSetupConfig:
    coordination_root: Path
    settings_path: Path
    seed_source_coordination_root: Path | None = None
