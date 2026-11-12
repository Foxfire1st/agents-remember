"""The real rejected Codex request must stop before the paid boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import HERMES_ACTOR
from agent_evals.adapters.judges import JudgeRequest
from agent_evals.adapters.native_trials import NativeJudgeOptions, TrialSources, run_native_judge
from agent_evals.judge_evidence import JudgePackageTooLarge


def test_retained_codex_character_rejection_stops_before_any_execution(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures/judge-package-budget-real.json.gz"
    records = json.loads(gzip.decompress(fixture.read_bytes()))["records"]
    row = next(record for record in records if record["attempt"].startswith("ea34127e"))
    value = row["request"]
    request = JudgeRequest(
        tuple(value["rubric"]), tuple(value["observations"]), tuple(value["artifact_evidence"])
    )
    # Synthetic-home test data keeps the historical task with a recomputed hash;
    # constructing today's request intentionally uses today's judge task text.
    original_prompt = json.dumps(value, sort_keys=True)
    assert len(original_prompt) == 1_070_103
    assert hashlib.sha256(original_prompt.encode()).hexdigest() == row["sourcePromptSha256"]
    assert len(request.prompt()) > 1_048_576
    provisioned = SimpleNamespace(environment=lambda: {"PATH": "/stub"})
    with (
        patch("agent_evals.adapters.native_trials.shutil.which", return_value=sys.executable),
        patch(
            "agent_evals.adapters.native_trials.execute_judge_native",
            side_effect=AssertionError("no paid call is permitted"),
        ) as execute,
        pytest.raises(JudgePackageTooLarge) as error,
    ):
        run_native_judge(
            cast(TrialSources, provisioned), HERMES_ACTOR, request, tmp_path, NativeJudgeOptions(10)
        )
    assert "input-package-over-budget" in error.value.cause
    assert "character" in error.value.cause
    execute.assert_not_called()
    assert not (tmp_path / "rubric-judge").exists()
