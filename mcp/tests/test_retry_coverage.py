from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember_test_support.code_quality import retry_coverage
from coverage import CoverageData


def test_retained_and_delta_contexts_merge_before_json_is_scored(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )
    source = root / "sample.py"
    source.write_text(
        "def choose(value):\n    if value:\n        return 1\n    return 0\n",
        encoding="utf-8",
    )
    retained_path = root / "retained.coverage"
    active_path = root / "active.coverage"
    coverage_json = root / "coverage.json"
    _write_arcs(
        retained_path,
        source,
        retry_coverage.CACHED_CONTEXT,
        [(-1, 1), (1, 2), (2, 3), (3, -1)],
    )
    _write_arcs(
        active_path,
        source,
        "mcp/tests/test_changed.py::test_false|run",
        [(-1, 1), (1, 2), (2, 4), (4, -1)],
    )
    coverage_json.write_text('{"stale": true}\n', encoding="utf-8")

    retry_coverage.merge_delta_artifacts(
        retained_path=retained_path,
        delta_path=active_path,
        destination_path=active_path,
        coverage_json=coverage_json,
        project_root=root,
    )

    merged = CoverageData(basename=str(active_path))
    merged.read()
    assert merged.measured_contexts() == {
        retry_coverage.CACHED_CONTEXT,
        "mcp/tests/test_changed.py::test_false|run",
    }
    assert set(merged.arcs(str(source)) or ()) == {
        (-1, 1),
        (1, 2),
        (2, 3),
        (2, 4),
        (3, -1),
        (4, -1),
    }
    report = json.loads(coverage_json.read_text(encoding="utf-8"))
    assert report["meta"]["branch_coverage"] is True
    assert any(path.endswith("sample.py") for path in report["files"])
    assert "stale" not in report


def test_empty_retained_subset_merges_only_fresh_delta_contexts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )
    source = root / "sample.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    prior_path = root / "prior.coverage"
    retained_path = root / "retained.coverage"
    active_path = root / "active.coverage"
    coverage_json = root / "coverage.json"
    _write_arcs(
        prior_path,
        source,
        "mcp/tests/test_changed.py::test_value|run",
        [(-1, 1), (1, -1)],
    )

    retained = retry_coverage.retain_unchanged_contexts(
        prior_path,
        retained_path,
        [Path("mcp/tests/test_changed.py")],
    )
    assert retained is False
    assert not retained_path.exists()
    _write_arcs(
        active_path,
        source,
        "mcp/tests/test_changed.py::test_new_value|run",
        [(-1, 1), (1, -1)],
    )

    retry_coverage.merge_delta_artifacts(
        retained_path=None,
        delta_path=active_path,
        destination_path=active_path,
        coverage_json=coverage_json,
        project_root=root,
    )

    merged = CoverageData(basename=str(active_path))
    merged.read()
    assert merged.measured_contexts() == {"mcp/tests/test_changed.py::test_new_value|run"}
    assert coverage_json.is_file()


def test_merge_failure_removes_both_public_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )
    source = root / "sample.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    active_path = root / "active.coverage"
    coverage_json = root / "coverage.json"
    _write_arcs(active_path, source, "test_sample::test_value|run", [(-1, 1), (1, -1)])
    coverage_json.write_text('{"stale": true}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"Coverage\.py data is missing"):
        retry_coverage.merge_delta_artifacts(
            retained_path=root / "missing-retained.coverage",
            delta_path=active_path,
            destination_path=active_path,
            coverage_json=coverage_json,
            project_root=root,
        )

    assert not active_path.exists()
    assert not coverage_json.exists()


def _write_arcs(
    path: Path,
    source: Path,
    context: str,
    arcs: list[tuple[int, int]],
) -> None:
    data = CoverageData(basename=str(path))
    data.set_context(context)
    data.add_arcs({str(source): arcs})
    data.write()
