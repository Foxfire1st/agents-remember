"""Structured, actionable checkpoint reporting for clean-room scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CheckpointDefinition:
    """Stable acceptance meaning kept separate from observed candidate evidence."""

    checkpoint_id: str
    requirement: str
    expected: str
    owner: str


@dataclass
class CheckpointRecorder:
    """Collect one scenario's checkpoint records for its report."""

    scenario: str
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def report(self, **values: object) -> dict[str, object]:
        return {
            "schema": "ar-e2e-checkpoint-report/v1",
            "scenario": self.scenario,
            "checkpoints": self.checkpoints,
            "diagnostics": self.diagnostics,
            **values,
        }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
