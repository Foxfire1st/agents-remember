"""Structured, actionable checkpoint reporting for clean-room scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CheckpointFailure(AssertionError):
    """One acceptance checkpoint did not match its declared expectation."""


@dataclass(frozen=True)
class CheckpointDefinition:
    """Stable acceptance meaning kept separate from observed candidate evidence."""

    checkpoint_id: str
    requirement: str
    expected: str
    owner: str


@dataclass
class CheckpointRecorder:
    """Collect pass/fail evidence before raising on the first bad checkpoint."""

    scenario: str
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def check(
        self,
        definition: CheckpointDefinition,
        *,
        actual: object,
        passed: bool,
    ) -> None:
        record = {
            "id": definition.checkpoint_id,
            "requirement": definition.requirement,
            "expected": definition.expected,
            "actual": actual,
            "owner": definition.owner,
            "status": "passed" if passed else "failed",
        }
        self.checkpoints.append(record)
        if not passed:
            raise CheckpointFailure(
                f"{definition.checkpoint_id}: expected {definition.expected}; "
                f"actual {actual!r}; owner {definition.owner}"
            )

    def diagnostic(self, name: str, value: object) -> None:
        """Retain bounded failure-state evidence without treating it as acceptance proof."""

        self.diagnostics.append({"name": name, "value": value})

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
