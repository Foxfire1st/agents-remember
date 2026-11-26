"""Tests for the oxlint JSON scope reader and dashboard-lint scope line."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from agents_remember_test_support.code_quality.scope import (
    ScopeError,
    oxlint_result_files,
    parse_oxlint_result_files,
)
from agents_remember_test_support.code_quality.scope_reporting import (
    ScopeReportingError,
    dashboard_lint_scope_line,
    dashboard_scope_line,
)


def _write_fake_oxlint(dashboard: Path, *, stdout: str, exit_code: int = 0) -> Path:
    executable = dashboard / "node_modules" / ".bin" / "oxlint"
    executable.parent.mkdir(parents=True, exist_ok=True)
    payload_file = dashboard / "_fake_oxlint_stdout.json"
    payload_file.write_text(stdout, encoding="utf-8")
    executable.write_text(
        f"#!/bin/sh\ncat '{payload_file.as_posix()}'\nexit {exit_code}\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return executable


def test_parse_oxlint_result_files_counts_distinct_files_with_findings() -> None:
    payload = {
        "diagnostics": [
            {"filename": "src/a.ts", "code": "eslint(no-var)"},
            {"filename": "src/a.ts", "code": "eslint(prefer-const)"},
            {"filename": "src/b.tsx", "code": "eslint(no-unsafe-optional-chaining)"},
        ],
        "number_of_files": 719,
        "number_of_rules": 119,
    }
    assert parse_oxlint_result_files(json.dumps(payload)) == [Path("src/a.ts"), Path("src/b.tsx")]


def test_parse_oxlint_result_files_allows_clean_run_when_files_were_checked() -> None:
    payload = {"diagnostics": [], "number_of_files": 719, "number_of_rules": 119}
    assert parse_oxlint_result_files(json.dumps(payload)) == []


@pytest.mark.parametrize(
    ("raw", "expected_fragment"),
    [
        ("not-json", "invalid JSON"),
        ("[]", "not a JSON object"),
        (json.dumps({"diagnostics": [], "number_of_files": 0}), "resolved zero files"),
        (
            json.dumps({"diagnostics": [], "number_of_files": True}),
            "missing integer number_of_files",
        ),
        (json.dumps({"diagnostics": {}, "number_of_files": 5}), "missing diagnostics list"),
        (
            json.dumps({"diagnostics": [{"filename": ""}], "number_of_files": 5}),
            "no non-empty string filename",
        ),
    ],
)
def test_parse_oxlint_result_files_rejects_malformed_or_empty_scope(
    raw: str, expected_fragment: str
) -> None:
    with pytest.raises(ScopeError, match=expected_fragment):
        parse_oxlint_result_files(raw)


def test_oxlint_result_files_and_dashboard_lint_scope_line(tmp_path: Path) -> None:
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / ".oxlintrc.json").write_text("{}\n", encoding="utf-8")
    (dashboard / "package.json").write_text(
        json.dumps({"scripts": {"lint": "oxlint ."}}), encoding="utf-8"
    )
    payload = json.dumps(
        {
            "diagnostics": [
                {"filename": "src/explainer/ExplainerColumn.test.tsx"},
                {"filename": "src/explainer/ExplainerColumn.test.tsx"},
                {"filename": "src/cockpit/roleChatDispatch.repair.test.ts"},
            ],
            "number_of_files": 719,
        }
    )
    _write_fake_oxlint(dashboard, stdout=payload, exit_code=1)

    assert oxlint_result_files(dashboard) == [
        Path("src/explainer/ExplainerColumn.test.tsx"),
        Path("src/cockpit/roleChatDispatch.repair.test.ts"),
    ]
    line = dashboard_scope_line(tmp_path, "lint")
    assert "dashboard-lint" in line
    assert "dashboard/.oxlintrc.json" in line
    assert "2 oxlint files with findings" in line


def test_dashboard_lint_scope_line_reports_clean_run_and_missing_config(tmp_path: Path) -> None:
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    with pytest.raises(ScopeReportingError, match=r"dashboard/\.oxlintrc\.json is missing"):
        dashboard_lint_scope_line(dashboard)

    (dashboard / ".oxlintrc.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ScopeReportingError, match="oxlint executable is missing"):
        dashboard_lint_scope_line(dashboard)

    _write_fake_oxlint(
        dashboard,
        stdout=json.dumps({"diagnostics": [], "number_of_files": 719}),
        exit_code=0,
    )
    assert "0 oxlint files with findings" in dashboard_lint_scope_line(dashboard)

    _write_fake_oxlint(dashboard, stdout="", exit_code=2)
    with pytest.raises(ScopeReportingError, match="exit 2"):
        dashboard_lint_scope_line(dashboard)
