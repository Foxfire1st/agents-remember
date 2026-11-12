"""Readers measure a fresh memory tree before its first onboarding card exists."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.memory_quality.knowledge_inputs import (
    build_route_indexes as build_quality_route_indexes,
)
from agents_remember.application.memory_tools import memory_baseline_status_tool, memory_init_tool
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.kernel.route_index import build_missing_route_indexes, build_route_indexes
from agents_remember.memory_quality.integrity.check_missing_onboarding import main
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    DriftSummaryOutput,
    run_drift_summary,
)
from converted_onboarding_drift_support import REPO_ID, _context, _world


def test_route_index_readers_name_an_onboarding_file_as_not_a_directory(tmp_path: Path) -> None:
    code, _settings_path = _world(tmp_path)
    onboarding = tmp_path / "onboarding"
    onboarding.write_text("not a directory\n", encoding="utf-8")

    for build in (build_route_indexes, build_missing_route_indexes, build_quality_route_indexes):
        with pytest.raises(NotADirectoryError) as error:
            build(
                code_root=code,
                onboarding_root=onboarding,
                repository=REPO_ID,
                storage=StorageSettings(),
            )
        assert str(error.value) == f"onboarding root is not a directory: {onboarding}"


def test_route_and_drift_readers_measure_a_fresh_tree_without_creating_onboarding(
    tmp_path: Path,
) -> None:
    code, settings_path = _world(tmp_path)
    config = load_config(settings_path)
    memory_init_tool(config, repo_id=REPO_ID, initial_branch="main")
    memory = load_config(settings_path).repositories[REPO_ID].memory_root
    assert memory is not None
    onboarding = memory / "onboarding"
    onboarding.rmdir()

    for build in (build_route_indexes, build_missing_route_indexes, build_quality_route_indexes):
        result = build(
            code_root=code,
            onboarding_root=onboarding,
            repository=REPO_ID,
            storage=StorageSettings(),
        )
        assert result.routes == 0 and result.written == 0 and result.unchanged == 0
        assert result.indexes == [] and result.build_errors == []

    result = run_drift_summary(
        code_repository_root=code,
        context=_context(memory, tmp_path),
        output=DriftSummaryOutput(write_report=False),
    )
    assert result["status"] == "checked", result
    assert result.get("sidecarCount") == result.get("count") == result.get("actionableCount") == 0
    assert result.get("states") == {"current": 0, "stale": 0}
    assert result.get("violationCount") == 0
    assert "error" not in result
    assert not onboarding.exists()


def test_baseline_status_measures_zero_cards_without_an_onboarding_directory(
    tmp_path: Path,
) -> None:
    _code, settings_path = _world(tmp_path)
    memory_init_tool(load_config(settings_path), repo_id=REPO_ID, initial_branch="main")
    config = load_config(settings_path)
    memory = config.repositories[REPO_ID].memory_root
    assert memory is not None
    (memory / "onboarding").rmdir()

    result = memory_baseline_status_tool(config, repo_id=REPO_ID)
    assert result["ok"] is True and result["state"] == "ready", result
    assert result["drift"]["sidecarCount"] == result["drift"]["count"] == 0
    assert not (memory / "onboarding").exists()


def test_missing_onboarding_command_reports_zero_instead_of_refusing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, settings_path = _world(tmp_path)
    memory_init_tool(load_config(settings_path), repo_id=REPO_ID, initial_branch="main")
    config = load_config(settings_path)
    memory = config.repositories[REPO_ID].memory_root
    assert memory is not None
    onboarding = memory / "onboarding"
    onboarding.rmdir()

    status = main(
        [
            "--code-repository-root",
            str(code),
            "--onboarding-root",
            str(onboarding),
            "--coordination-root",
            str(config.coordination_root),
            "--format",
            "json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert status == 0 and result["ok"] is True
    assert result["sourceCount"] == result["missingCount"] == 0
    assert result["missing"] == []
    assert not onboarding.exists()
