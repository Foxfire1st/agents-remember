from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from coverage import CoverageData

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import check, retry_proof
from agents_remember.code_quality.scope import GateScope


@pytest.fixture(autouse=True)
def _isolate_outer_quality_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests own the invocation mode instead of inheriting the wrapper's CI marker."""

    monkeypatch.delenv("AR_QUALITY_INVOCATION", raising=False)
    monkeypatch.delenv(retry_proof.DISABLE_ENV, raising=False)


def test_changed_test_contexts_and_collection_context_are_removed(tmp_path: Path) -> None:
    source = tmp_path / "source.coverage"
    destination = tmp_path / "destination.coverage"
    measured = str(tmp_path / "module.py")
    data = CoverageData(basename=str(source))
    for context, arc in (
        ("", (1, 2)),
        ("mcp/tests/test_changed.py::test_old|run", (2, 3)),
        ("mcp/tests/test_kept.py::test_kept|run", (3, 4)),
    ):
        data.set_context(context)
        data.add_arcs({measured: [arc]})
    data.write()

    retry_proof._filtered_coverage_data(  # pyright: ignore[reportPrivateUsage]
        source,
        destination,
        [Path("mcp/tests/test_changed.py")],
    )

    filtered = CoverageData(basename=str(destination))
    filtered.read()
    assert filtered.measured_contexts() == {retry_proof.CACHED_CONTEXT}
    assert filtered.arcs(measured) == [(3, 4)]


def test_delta_refuses_support_modules_and_deleted_tests() -> None:
    previous = {
        "mcp/tests/test_one.py": "old",
        "mcp/tests/conftest.py": "old",
    }
    selected = (Path("mcp/tests/test_one.py"),)
    roots = (Path("mcp/tests"),)

    assert retry_proof._eligible_test_delta(  # pyright: ignore[reportPrivateUsage]
        [Path("mcp/tests/test_one.py")], previous, selected, roots
    ) == (Path("mcp/tests/test_one.py"),)
    assert (
        retry_proof._eligible_test_delta(  # pyright: ignore[reportPrivateUsage]
            [Path("mcp/tests/conftest.py")], previous, selected, roots
        )
        is None
    )
    assert (
        retry_proof._eligible_test_delta(  # pyright: ignore[reportPrivateUsage]
            [Path("mcp/tests/test_deleted.py")],
            {**previous, "mcp/tests/test_deleted.py": "old"},
            selected,
            roots,
        )
        is None
    )


def test_full_proof_becomes_exact_then_test_delta_and_source_change_invalidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    test_path = root / "tests" / "test_sample.py"
    source_path = root / "src" / "sample.py"
    test_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    test_path.write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Retry Proof Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    inputs = _inputs(root)
    output: list[str] = []

    fresh = retry_proof.prepare(inputs, printer=output.append)
    assert fresh is not None and fresh.mode == "fresh"
    _write_context_coverage(fresh.active_data_path, root, test_path)
    coverage_json = root / "coverage.json"
    coverage_json.write_text(json.dumps({"meta": {"branch_coverage": True}}), encoding="utf-8")
    fresh.record_pytest(0)
    fresh.finish(coverage_json, quality_passed=False)

    exact = retry_proof.prepare(inputs, printer=output.append)
    assert exact is not None and exact.exact
    exact.prepare_artifacts(coverage_json)
    exact.finish(coverage_json, quality_passed=False)

    test_path.write_text(
        "def test_sample():\n    assert True\n\ndef test_more():\n    assert True\n",
        encoding="utf-8",
    )
    delta = retry_proof.prepare(inputs, printer=output.append)
    assert delta is not None and delta.delta
    assert delta.delta_tests == (Path("tests/test_sample.py"),)

    source_path.write_text("VALUE = 2\n", encoding="utf-8")
    invalidated = retry_proof.prepare(inputs, printer=output.append)
    assert invalidated is not None and invalidated.mode == "fresh"


def test_wrapper_retry_runs_only_changed_test_module(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    test_path = root / "tests" / "test_sample.py"
    source_path = root / "src" / "sample.py"
    test_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    test_path.write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Retry Proof Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    coverage_json = root / "coverage.json"
    commands: list[list[str]] = []

    def runner(
        name: str,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
    ) -> check.StepResult:
        commands.append(command)
        if name == "pytest":
            data_path = Path(env["COVERAGE_FILE"])
            data = CoverageData(basename=str(data_path))
            if data_path.is_file():
                data.read()
            data.set_context("tests/test_sample.py::test_sample|run")
            data.add_arcs({str(source_path): [(-1, 1), (1, -1)]})
            data.write()
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"branch_coverage": True},
                        "files": {"src/sample.py": {"summary": {}}},
                    }
                ),
                encoding="utf-8",
            )
        return check.StepResult(name=name, return_code=0, command=command)

    config = check.CheckConfig(
        project_root=root,
        scope=GateScope(
            lint_paths=[Path("src/sample.py"), Path("tests/test_sample.py")],
            type_paths=[Path("src/sample.py"), Path("tests/test_sample.py")],
            coverage_paths=[Path("src")],
            test_paths=[Path("tests")],
            size_paths=[Path("src/sample.py"), Path("tests/test_sample.py")],
            scope_roots=[Path("src"), Path("tests")],
            untracked_paths=[],
        ),
        coverage_json=coverage_json,
        threshold=30.0,
        top=20,
        diff_base="HEAD",
    )
    with (
        mock.patch.object(check, "run_subprocess", runner),
        mock.patch.object(check, "run_coverage_rails", return_value=1),
    ):
        assert check.run_quality_check(config, runner=runner, printer=lambda line: None) == 1

    test_path.write_text(
        "def test_sample():\n    assert True\n\ndef test_more():\n    assert True\n",
        encoding="utf-8",
    )
    commands.clear()
    with (
        mock.patch.object(check, "run_subprocess", runner),
        mock.patch.object(check, "run_coverage_rails", side_effect=[1, 0]),
    ):
        assert check.run_quality_check(config, runner=runner, printer=lambda line: None) == 0

    pytest_commands = [command for command in commands if "pytest" in command]
    assert len(pytest_commands) == 2
    delta_command, fallback_command = pytest_commands
    assert "tests/test_sample.py" in delta_command
    assert "tests" not in delta_command
    assert "--cov-append" in delta_command
    assert "--cov-context=test" in delta_command
    assert "tests" in fallback_command
    assert "--cov-append" not in fallback_command
    assert "--cov-context=test" in fallback_command


def test_exact_proof_is_not_scored_when_a_cheap_rail_breaks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    test_path = root / "tests" / "test_sample.py"
    source_path = root / "src" / "sample.py"
    test_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    test_path.write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Retry Proof Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    coverage_json = root / "coverage.json"
    inputs = _inputs(root)
    proof = retry_proof.prepare(inputs, printer=lambda line: None)
    assert proof is not None
    _write_context_coverage(proof.active_data_path, root, test_path)
    coverage_json.write_text(json.dumps({"meta": {"branch_coverage": True}}), encoding="utf-8")
    proof.record_pytest(0)
    proof.finish(coverage_json, quality_passed=False)
    config = check.CheckConfig(
        project_root=root,
        scope=GateScope(
            lint_paths=[Path("src/sample.py"), Path("tests/test_sample.py")],
            type_paths=[Path("src/sample.py"), Path("tests/test_sample.py")],
            coverage_paths=[Path("src")],
            test_paths=[Path("tests")],
            size_paths=[Path("src/sample.py"), Path("tests/test_sample.py")],
            scope_roots=[Path("src"), Path("tests")],
            untracked_paths=[],
        ),
        coverage_json=coverage_json,
        threshold=30.0,
        top=20,
        diff_base="HEAD",
    )
    coverage_rails = mock.Mock(return_value=0)

    def failing_runner(
        name: str,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
    ) -> check.StepResult:
        return check.StepResult(name=name, return_code=1 if name == "ruff" else 0, command=command)

    with (
        mock.patch.object(check, "run_subprocess", failing_runner),
        mock.patch.object(check, "run_coverage_rails", coverage_rails),
    ):
        assert (
            check.run_quality_check(config, runner=failing_runner, printer=lambda line: None) == 1
        )

    coverage_rails.assert_not_called()
    assert not coverage_json.exists()


def test_repository_snapshot_hashes_symlink_identity_without_following_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    target = tmp_path / "installed-dependencies"
    target.mkdir()
    root.mkdir()
    link = root / "node_modules"
    link.symlink_to(target, target_is_directory=True)
    _git(root, "init")
    _git(root, "add", "node_modules")
    first = retry_proof._repository_snapshot(root)  # pyright: ignore[reportPrivateUsage]

    assert first == {
        "node_modules": retry_proof.hashlib.sha256(  # pyright: ignore[reportPrivateUsage]
            b"symlink\0" + os.fsencode(os.readlink(link))
        ).hexdigest()
    }
    (target / "untracked-install-byte").write_text("ignored", encoding="utf-8")
    assert (
        retry_proof._repository_snapshot(  # pyright: ignore[reportPrivateUsage]
            root
        )
        == first
    )


def test_snapshot_and_inventory_fail_closed_with_actionable_errors(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    failed = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=1, stdout="", stderr="inventory failed"
    )
    with (
        mock.patch.object(retry_proof.git_command, "run_git", return_value=failed),
        pytest.raises(RuntimeError, match="inventory failed"),
    ):
        retry_proof._tracked_paths(root)  # pyright: ignore[reportPrivateUsage]
    with (
        mock.patch.object(retry_proof.git_command, "run_git", return_value=failed),
        pytest.raises(RuntimeError, match="inventory failed"),
    ):
        retry_proof._cache_dir(root)  # pyright: ignore[reportPrivateUsage]

    with (
        mock.patch.object(
            retry_proof,
            "_tracked_paths",
            return_value=(Path("missing.py"),),
        ),
        pytest.raises(RuntimeError, match=r"could not fingerprint tracked input missing\.py"),
    ):
        retry_proof._repository_snapshot(root)  # pyright: ignore[reportPrivateUsage]


def test_retry_shape_helpers_cover_direct_new_and_malformed_selections(tmp_path: Path) -> None:
    direct = Path("tests/test_direct.py")
    inputs = retry_proof.RetryInputs(
        project_root=tmp_path,
        targeted=True,
        base_revision="base",
        threshold=20.0,
        top=10,
        diff_floor=100.0,
        coverage_paths=(Path("src"),),
        test_arguments=(direct,),
        test_roots=(Path("tests"),),
        untracked_paths=(),
    )
    with mock.patch.object(retry_proof, "_tracked_paths", return_value=()):
        assert retry_proof._selected_test_modules(  # pyright: ignore[reportPrivateUsage]
            inputs
        ) == (direct,)

    assert retry_proof._snapshot_delta(  # pyright: ignore[reportPrivateUsage]
        [], {"tests/test_direct.py": "new"}
    ) == (direct,)
    assert retry_proof._eligible_test_delta(  # pyright: ignore[reportPrivateUsage]
        [direct], {}, [direct], [Path("tests")]
    ) == (direct,)
    assert (
        retry_proof._eligible_test_delta(  # pyright: ignore[reportPrivateUsage]
            [direct], [], [direct], [Path("tests")]
        )
        is None
    )
    assert not retry_proof._selection_is_compatible(  # pyright: ignore[reportPrivateUsage]
        {"selectedTests": "not-a-list"}, [direct], [direct]
    )


def test_prepare_disable_and_fail_closed_routes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inputs = _inputs(root)
    output: list[str] = []

    with mock.patch.dict(os.environ, {retry_proof.DISABLE_ENV: "1"}):
        assert retry_proof.prepare(inputs, printer=output.append) is None
    assert retry_proof.DISABLE_ENV in output[-1]

    with mock.patch.dict(
        os.environ,
        {"AR_QUALITY_INVOCATION": retry_proof.CI_INVOCATION},
    ):
        assert retry_proof.prepare(inputs, printer=output.append) is None
    assert "CI requires a fresh matrix proof" in output[-1]

    assert "untracked" in str(
        retry_proof._disabled_reason(  # pyright: ignore[reportPrivateUsage]
            replace(inputs, untracked_paths=(Path("new.py"),))
        )
    )
    assert "no Coverage.py" in str(
        retry_proof._disabled_reason(  # pyright: ignore[reportPrivateUsage]
            replace(inputs, coverage_paths=())
        )
    )

    with mock.patch.object(retry_proof, "_fresh_plan", side_effect=RuntimeError("broken")):
        assert retry_proof.prepare(inputs, printer=output.append) is None
    assert "unavailable (broken)" in output[-1]

    with mock.patch.object(retry_proof, "_fresh_plan", return_value=None):
        assert retry_proof.prepare(inputs, printer=output.append) is None
    assert "selection has no concrete test modules" in output[-1]

    with (
        mock.patch.object(retry_proof, "_cache_dir", return_value=tmp_path / "cache"),
        mock.patch.object(retry_proof, "_selected_test_modules", return_value=()),
    ):
        assert retry_proof._fresh_plan(inputs) is None  # pyright: ignore[reportPrivateUsage]


def test_reuse_plan_rejects_changed_selection_and_unusable_context(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    plan = _plan(tmp_path)
    old_test = Path("tests/test_old.py")
    new_test = Path("tests/test_new.py")
    plan.snapshot = {new_test.as_posix(): "new"}
    plan.selected_tests = (new_test,)
    output: list[str] = []
    result = retry_proof._reuse_plan(  # pyright: ignore[reportPrivateUsage]
        plan,
        {
            "snapshot": {old_test.as_posix(): "old"},
            "selectedTests": [old_test.as_posix()],
        },
        inputs,
        output.append,
    )
    assert result.mode == "fresh"
    assert "population changed ambiguously" in output[-1]

    plan = _plan(tmp_path)
    selected = plan.selected_tests[0]
    plan.snapshot = {selected.as_posix(): "new"}
    result = retry_proof._reuse_plan(  # pyright: ignore[reportPrivateUsage]
        plan,
        {
            "snapshot": {selected.as_posix(): "old"},
            "selectedTests": [selected.as_posix()],
        },
        inputs,
        output.append,
    )
    assert result.mode == "fresh"
    assert "prior context proof is unusable" in output[-1]


def test_cache_dir_accepts_an_absolute_git_common_directory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    common = tmp_path / "common-git"
    completed = subprocess.CompletedProcess(
        args=["git", "rev-parse"], returncode=0, stdout=f"{common}\n", stderr=""
    )
    with mock.patch.object(retry_proof.git_command, "run_git", return_value=completed):
        cache = retry_proof._cache_dir(root)  # pyright: ignore[reportPrivateUsage]
    assert cache.parent == common / retry_proof.CACHE_DIRECTORY


def test_context_proof_and_filtering_reject_non_branch_or_contextless_data(
    tmp_path: Path,
) -> None:
    measured = str(tmp_path / "module.py")
    line_only = tmp_path / "line-only.coverage"
    data = CoverageData(basename=str(line_only))
    data.add_lines({measured: [1]})
    data.write()
    with pytest.raises(RuntimeError, match="branch coverage data is missing"):
        retry_proof._validate_context_proof(  # pyright: ignore[reportPrivateUsage]
            line_only
        )
    with pytest.raises(RuntimeError, match="does not contain branch arcs"):
        retry_proof._filtered_coverage_data(  # pyright: ignore[reportPrivateUsage]
            line_only, tmp_path / "filtered.coverage", [Path("tests/test_one.py")]
        )

    contextless = tmp_path / "contextless.coverage"
    data = CoverageData(basename=str(contextless))
    data.set_context("")
    data.add_arcs({measured: [(1, 2)]})
    data.write()
    with pytest.raises(RuntimeError, match="pytest test contexts are missing"):
        retry_proof._validate_context_proof(  # pyright: ignore[reportPrivateUsage]
            contextless
        )


def test_publish_proof_refuses_missing_or_invalid_artifacts(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text("{}", encoding="utf-8")
    retry_proof._publish_proof(  # pyright: ignore[reportPrivateUsage]
        plan, coverage_json
    )
    assert not plan.manifest_path.exists()

    data = CoverageData(basename=str(plan.active_data_path))
    data.add_lines({str(tmp_path / "module.py"): [1]})
    data.write()
    retry_proof._publish_proof(  # pyright: ignore[reportPrivateUsage]
        plan, coverage_json
    )
    assert not plan.manifest_path.exists()


def test_delta_fallback_without_coverage_reports_early_failure(tmp_path: Path) -> None:
    plan = _plan(tmp_path, mode="delta")
    plan.pytest_passed = True
    coverage_json = tmp_path / "coverage.json"
    output: list[str] = []
    config = mock.Mock(project_root=tmp_path)
    runtime = check.RailRuntime(
        runner=mock.Mock(),
        printer=output.append,
        retry_plan=plan,
    )
    with (
        mock.patch.object(check, "run_coverage_rails", return_value=1),
        mock.patch.object(check, "run_pytest_only", return_value=1),
    ):
        assert (
            check.complete_coverage_rails(
                config,
                coverage_json,
                fixed_failures=0,
                runtime=runtime,
            )
            == 1
        )
    assert any("result: CRAP-Calculator SKIPPED" in line for line in output)
    assert any("result: diff-coverage SKIPPED" in line for line in output)


def test_pytest_fallback_and_exact_paths_report_their_own_results(tmp_path: Path) -> None:
    coverage_json = tmp_path / "coverage.json"
    config = mock.Mock(project_root=tmp_path, scope=mock.Mock(), targeted=True)
    plan = _plan(tmp_path, mode="fresh")
    output: list[str] = []
    with mock.patch.object(check, "quality_steps", return_value=[]):
        assert (
            check.run_pytest_only(
                config,
                coverage_json,
                plan,
                runner=mock.Mock(),
                printer=output.append,
            )
            == 1
        )
    assert "result: pytest FAIL (full fallback derived no pytest rail)" in output

    pytest_step = check.Step(name="pytest", command=["pytest"])
    exact = _plan(tmp_path, mode="exact")
    cached = mock.Mock(return_value=0)
    with (
        mock.patch.object(check, "quality_steps", return_value=[pytest_step]),
        mock.patch.object(check, "report_cached_pytest", cached),
        mock.patch.object(check, "subprocess_env", return_value={}),
        mock.patch.object(check.scope_reporting, "fixed_step_scope_line", return_value="scope"),
    ):
        assert (
            check.run_fixed_checks(
                config,
                coverage_json,
                runner=mock.Mock(),
                printer=lambda line: None,
                retry_plan=exact,
            )
            == 0
        )
    cached.assert_called_once_with(coverage_json, mock.ANY)

    coverage_json.write_text("{}", encoding="utf-8")
    with mock.patch.object(
        check.scope_reporting, "coverage_result_scope_line", return_value="scope"
    ):
        assert (
            check.report_pytest_result(
                pytest_step,
                check.StepResult(name="pytest", return_code=1, command=["pytest"]),
                coverage_json,
                output.append,
            )
            == 1
        )


def test_retry_artifact_and_scope_failures_fall_back_without_stale_evidence(
    tmp_path: Path,
) -> None:
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text("{}", encoding="utf-8")
    plan = _plan(tmp_path)
    output: list[str] = []
    with (
        mock.patch.object(check, "prepare_retry_plan", return_value=plan),
        mock.patch.object(plan, "prepare_artifacts", side_effect=RuntimeError("broken proof")),
    ):
        assert (
            check.initialized_retry_plan(
                mock.Mock(),
                coverage_json,
                tmp_path,
                runner=mock.Mock(),
                printer=output.append,
            )
            is None
        )
    assert not coverage_json.exists()
    assert "artifact preparation failed (broken proof)" in output[-1]

    with mock.patch.object(
        check.scope_reporting, "coverage_result_scope_line", return_value="scope"
    ):
        assert check.report_cached_pytest(coverage_json, output.append) == 0
    assert output[-1] == "result: pytest PASS (exact content-addressed proof reused)"

    with mock.patch.object(
        check.scope_reporting,
        "coverage_result_scope_line",
        side_effect=check.ScopeError("bad scope"),
    ):
        assert check.report_cached_pytest(coverage_json, output.append) == 1
    assert output[-1] == "result: pytest FAIL (cached Coverage.py result scope unavailable)"

    config = mock.Mock(targeted_base=None, diff_base="base")
    with mock.patch.object(
        check.diff_coverage, "resolve_base", side_effect=RuntimeError("bad base")
    ):
        assert (
            check.prepare_retry_plan(
                config,
                tmp_path,
                runner=check.run_subprocess,
                printer=output.append,
            )
            is None
        )
    assert output[-1] == "retry-proof: unavailable (bad base); running fresh"


def _inputs(root: Path) -> retry_proof.RetryInputs:
    return retry_proof.RetryInputs(
        project_root=root,
        targeted=False,
        base_revision="HEAD",
        threshold=30.0,
        top=20,
        diff_floor=100.0,
        coverage_paths=(Path("src"),),
        test_arguments=(Path("tests"),),
        test_roots=(Path("tests"),),
        untracked_paths=(),
    )


def _plan(tmp_path: Path, *, mode: str = "fresh") -> retry_proof.RetryPlan:
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    return retry_proof.RetryPlan(
        mode=mode,
        cache_dir=cache,
        manifest_path=cache / "manifest.json",
        proof_data_path=cache / "proof.coverage",
        proof_json_path=cache / "proof.json",
        active_data_path=cache / "active.coverage",
        snapshot={},
        selected_tests=(Path("tests/test_one.py"),),
        compatibility_key="key",
        delta_tests=(Path("tests/test_one.py"),) if mode == "delta" else (),
    )


def _write_context_coverage(path: Path, root: Path, test_path: Path) -> None:
    data = CoverageData(basename=str(path))
    data.set_context(f"{test_path.relative_to(root).as_posix()}::test_sample|run")
    data.add_arcs({str(root / "src" / "sample.py"): [(-1, 1), (1, -1)]})
    data.write()


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
