"""Run the repository-owned Python quality rails with explicit provenance.

Ruff, Ruff format, file size, Pyright, pytest, CRAP, and changed-lines coverage enforce.
Cheap deterministic rails precede pytest, which is the final subprocess; CRAP and diff
coverage then score its artifact. Radon CC and MI are labelled reports because Radon
findings do not change its exit status. Scope
comes from ``code_quality.scope`` for a full run; ``--targeted`` derives the leaf
change-set scope from ``code_quality.targeted`` (changed files, reverse-import closure,
and the derived test subset). Full runs may additionally run under a settings-owned
memory cap (``--memory-cap-bytes`` / ``orchestration.qualityGate.memoryCapBytes``).
The wrapper reports relevant untracked files separately because the index and diff
omit them.

Nonce-attested Dagger retries can reuse a content-addressed successful pytest proof when the exact
tree is unchanged or only selected test modules changed. Test-delta reuse strips their prior
Coverage.py contexts before appending fresh data; any ambiguity runs fresh. The wrapper itself
refuses before scope or retry planning outside the graph.

Each rail prints its actual input, config, nonzero population or result denominator, and
explicit result. Missing/vacuous inputs and tool failures refuse. Findings are remediated
in source or tests; baselines, allowlists, and exemptions are not supported.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.code_quality import (
    causal_preflight,
    diff_coverage,
    post_coverage,
    retry_proof,
    scope_reporting,
    targeted,
)
from agents_remember.code_quality import scope as quality_scope
from agents_remember.code_quality.check_cli import build_parser
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.platform_subprocess import native_subprocess_environment
from agents_remember.kernel.primitives import memory_cap
from agents_remember.testing.dagger_admission import (
    DaggerAdmission,
    DaggerAdmissionError,
    require_dagger_admission,
    require_dagger_admission_capability,
)
from agents_remember.testing.evidence_lanes import EvidenceTrigger, expression_for

crap_failure_line = post_coverage.crap_failure_line
run_crap_calculator = post_coverage.run_crap_calculator
run_diff_coverage = post_coverage.run_diff_coverage

GateScope = quality_scope.GateScope
ScopeError = quality_scope.ScopeError


def git_ls_files(project_root: Path, *patterns: str) -> list[Path]:
    return quality_scope.git_ls_files(project_root, *patterns)


def top_level_packages(tracked: list[Path]) -> list[Path]:
    return quality_scope.top_level_packages(tracked)


def toml_section(data: Mapping[str, object], keys: tuple[str, ...]) -> Mapping[str, object]:
    return quality_scope.toml_section(data, keys)


def pytest_testpaths(project_root: Path) -> list[Path]:
    return quality_scope.pytest_testpaths(project_root)


def derive_scope(project_root: Path) -> GateScope:
    return quality_scope.derive_scope(project_root)


RADON_REPORT_NOTE = (
    "report only: radon exits 0 whatever it finds, so nothing below can fail the gate"
)
QUALITY_PROGRESS_REPORT_ENV = "AR_QUALITY_PROGRESS_REPORT"
QUALITY_TEMP_ROOT = Path("/tmp/arq")


@dataclass(frozen=True)
class CheckConfig:
    project_root: Path
    scope: GateScope
    admission: DaggerAdmission
    coverage_json: Path | None
    threshold: float
    top: int
    diff_base: str | None = None
    diff_floor: float = diff_coverage.DEFAULT_DIFF_COVERAGE_FLOOR
    targeted: bool = False
    targeted_base: diff_coverage.BaseResolution | None = None
    targeted_scope: targeted.TargetedScopeResult | None = None
    file_size_armed: bool = False
    pytest_report_log: Path | None = None
    pytest_phase_report: Path | None = None
    causal_failure_report: Path | None = None
    coverage_data: Path | None = None
    progress_report: Path | None = None
    progress: QualityProgress | None = None


@dataclass(frozen=True)
class Step:
    """One wrapper step.

    ``report_note`` is what separates the two kinds. ``None`` means the step enforces:
    a non-zero exit is a finding and fails the gate. A note means the step reports, and
    the note is printed in its section header so nobody reads the output as enforcement.
    A report step that exits non-zero still fails the gate -- for a tool that exits 0 on
    every finding, a non-zero exit means the tool itself broke.
    """

    name: str
    command: list[str]
    report_note: str | None = None

    @property
    def enforcing(self) -> bool:
        return self.report_note is None


@dataclass(frozen=True)
class StepResult:
    name: str
    return_code: int
    command: list[str]


CommandRunner = Callable[[str, list[str], Path, Mapping[str, str]], StepResult]
Printer = Callable[[str], None]


@dataclass(frozen=True)
class RailRuntime:
    runner: CommandRunner
    printer: Printer
    retry_plan: retry_proof.RetryPlan | None


@dataclass
class QualityProgress:
    """One atomic, self-overwriting view of the wrapper's current rail."""

    path: Path | None
    started_at: str
    completed: list[str]

    @classmethod
    def start(cls, path: Path | None) -> QualityProgress:
        progress = cls(path=path, started_at=_quality_stamp(), completed=[])
        progress.write(status="running", step="scope", detail="derive quality scope")
        return progress

    def write(self, *, status: str, step: str, detail: str) -> None:
        if self.path is None:
            return
        atomic_write_text(
            self.path,
            json.dumps(
                {
                    "status": status,
                    "step": step,
                    "detail": detail,
                    "startedAt": self.started_at,
                    "updatedAt": _quality_stamp(),
                    "completedSteps": self.completed,
                },
                indent=2,
            )
            + "\n",
        )

    def finish_step(self, step: str, *, passed: bool) -> None:
        if passed and step not in self.completed:
            self.completed.append(step)
        self.write(
            status="running" if passed else "failed",
            step=step,
            detail="passed" if passed else "failed",
        )


def _quality_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/code_quality/check.py:106).
def print_line(message: str) -> None:  # pragma: no cover
    print(message, flush=True)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/code_quality/check.py:110).
def run_subprocess(
    name: str, command: list[str], cwd: Path, env: Mapping[str, str]
) -> StepResult:  # pragma: no cover
    completed = subprocess.run(
        command, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, check=False
    )
    return StepResult(name=name, return_code=completed.returncode, command=command)


# --- steps -------------------------------------------------------------------


def _fixed_steps(
    config: CheckConfig,
    lint_args: list[str],
    type_args: list[str],
) -> list[Step]:
    """Static source rails plus durable-evidence lifecycle admission."""
    steps = [
        # No `--extend-ignore` and no `--select`: this step lints exactly what
        # `pyproject.toml` selects, C901/PLR0911/PLR0912/PLR0915 included. Anything routed
        # off this command line is a rule the gate stops enforcing, which is the whole
        # reason the complexity baseline that used to own those four codes is gone.
        Step("ruff", [sys.executable, "-m", "ruff", "check", *lint_args]),
        Step("ruff-format", [sys.executable, "-m", "ruff", "format", "--check", *lint_args]),
        Step(
            "pyright",
            [
                sys.executable,
                "-m",
                "pyright",
                "--project",
                ".",
                "--pythonpath",
                sys.executable,
                *type_args,
            ],
        ),
        Step(
            "evidence-lifecycle",
            [
                sys.executable,
                "-m",
                "agents_remember.testing.evidence_lifecycle",
                "--project-root",
                ".",
            ],
        ),
    ]
    if config.causal_failure_report is not None:
        steps.append(
            Step(
                "causal-preflight",
                [
                    sys.executable,
                    "-m",
                    "agents_remember.code_quality.causal_preflight",
                    "--project-root",
                    ".",
                    "--report",
                    config.causal_failure_report.as_posix(),
                ],
            )
        )
    return steps


def _radon_report_steps(radon_args: list[str]) -> list[Step]:
    """The two report-only radon rails, scoped to the same paths as coverage/CRAP."""
    return [
        Step(
            "radon-cc",
            [
                sys.executable,
                "-m",
                "radon",
                "cc",
                *radon_args,
                "-s",
                "-n",
                "B",
                "--order",
                "SCORE",
            ],
            report_note=RADON_REPORT_NOTE,
        ),
        Step(
            "radon-mi",
            [sys.executable, "-m", "radon", "mi", *radon_args, "-s", "-n", "B"],
            report_note=RADON_REPORT_NOTE,
        ),
    ]


def _pytest_step(
    config: CheckConfig,
    coverage_json: Path,
    test_args: list[str],
    coverage_args: list[str],
    retry_plan: retry_proof.RetryPlan | None,
) -> Step | None:
    """The pytest rail, or None when a targeted run derived no test subset."""
    require_dagger_admission_capability(config.admission)
    if getattr(config, "targeted", False) and not config.scope.test_paths:
        return None
    pytest_args = [sys.executable, "-m", "pytest", *test_args]
    marker_expression = expression_for(
        EvidenceTrigger.AFFECTED if config.targeted else EvidenceTrigger.RELEASE
    )
    if marker_expression is not None:
        pytest_args += ["-m", marker_expression]
    if config.pytest_report_log is not None:
        pytest_args.append(f"--report-log={config.pytest_report_log.as_posix()}")
    if config.pytest_phase_report is not None:
        pytest_args += [
            "-p",
            "agents_remember.testing.pytest_phase_reporter",
            "--ar-pytest-phase-report",
            config.pytest_phase_report.as_posix(),
        ]
    if config.causal_failure_report is not None:
        pytest_args += [
            "--ar-causal-failure-report",
            config.causal_failure_report.as_posix(),
        ]
    if config.scope.coverage_paths:
        pytest_args += [
            *(f"--cov={module}" for module in coverage_args),
            f"--cov-report=json:{coverage_json.as_posix()}",
            "--cov-report=term",
            *(["--cov-append"] if retry_plan is not None and retry_plan.append_coverage else []),
            *(["--cov-context=test"] if retry_plan is not None else []),
        ]
    return Step("pytest", pytest_args)


def _file_size_step(config: CheckConfig, size_args: list[str]) -> Step:
    """The file-size rail, part of this command vector so hooks, closeout and CI
    pick it up without a second configuration path. While the repo is unarmed it
    still runs and reports every band; arming is one boolean in pyproject.toml
    ([tool.agents_remember] file_size_armed), and then any file at or above the
    1,200-line hard limit fails the run. A targeted run scopes the rail to the
    leaf's changed paths (see TargetedScopeResult)."""
    return Step(
        "file-size",
        [
            sys.executable,
            "-m",
            "agents_remember.code_quality.file_size",
            "--project-root",
            str(config.project_root),
            *size_args,
            *(["--report"] if not config.file_size_armed else []),
        ],
    )


def _layering_step(config: CheckConfig) -> Step:
    """The package-layering rail: the tree must satisfy ``layers.toml``'s order."""
    return Step(
        "layering",
        [
            sys.executable,
            "-m",
            "agents_remember.code_quality.layering",
            "--project-root",
            str(config.project_root),
        ],
    )


def quality_steps(
    config: CheckConfig,
    coverage_json: Path,
    *,
    retry_plan: retry_proof.RetryPlan | None = None,
) -> list[Step]:
    scope = config.scope
    targeted = getattr(config, "targeted", False)
    targeted_scope = getattr(config, "targeted_scope", None)
    if targeted and targeted_scope is not None:
        # Coverage.py instruments the top-level package root (the same shape the full
        # wrapper uses, which is the proven-safe FastMCP/pydantic path); records are
        # only written for modules the test subset actually imports. CRAP is still
        # scoped to the changed modules below.
        coverage_args = list(targeted_scope.coverage_root_modules)
    else:
        coverage_args = posix_args(scope.coverage_paths)
    # Radon consumes the same changed production module FILES the coverage/CRAP
    # rails score; the pytest-only package roots above would resolve to nothing
    # at the repo root and make the report rail vacuous.
    radon_args = posix_args(scope.coverage_paths)
    steps = _fixed_steps(
        config,
        posix_args(scope.lint_paths),
        posix_args(scope.type_paths),
    )
    # Keep the inexpensive structural rail ahead of type analysis, reports, and the broad pytest
    # run. Pytest must remain the final subprocess because CRAP and diff coverage consume the
    # coverage artifact it produces and therefore cannot safely run before it.
    steps.insert(2, _file_size_step(config, posix_args(scope.size_paths)))
    steps.insert(3, _layering_step(config))
    # A targeted run scopes radon and coverage/CRAP to the changed production
    # modules. When no production module changed (tests-only or non-Python leaves),
    # the radon report rails are not applicable and are skipped loudly by the
    # caller's not-applicable lines rather than run vacuous.
    if not targeted or scope.coverage_paths:
        steps += _radon_report_steps(radon_args)
    pytest = _pytest_step(
        config,
        coverage_json,
        posix_args(
            list(retry_plan.pytest_test_paths)
            if retry_plan is not None and retry_plan.pytest_test_paths is not None
            else scope.test_paths
        ),
        coverage_args,
        retry_plan,
    )
    if pytest is not None:
        steps.append(pytest)
    return steps


def posix_args(paths: list[Path]) -> list[str]:
    return [path.as_posix() for path in paths]


def source_import_roots(project_root: Path, coverage_paths: list[Path]) -> list[Path]:
    """Import roots for the tracked source packages.

    A coverage path pointing at a package directory (e.g. ``mcp/src/agents_remember``)
    resolves to the directory that must be importable (``mcp/src``). A targeted run's
    coverage path is a *file* inside the package, so the package root is recovered by
    walking up while ``__init__.py`` exists. Putting these on PYTHONPATH makes the
    wrapper's subprocesses import and cover *this* checkout's source rather than
    whatever an editable install resolves to, so the gate behaves identically from the
    primary clone and from any git worktree.
    """
    roots: list[Path] = []
    resolved_root = project_root.resolve()
    for source in coverage_paths:
        resolved = source if source.is_absolute() else project_root / source
        if resolved.is_file() and resolved.suffix == ".py":
            package_root = resolved.resolve().parent
            while (package_root / "__init__.py").is_file() and package_root not in {
                package_root.parent,
                resolved_root,
            }:
                package_root = package_root.parent
            root = resolved_root if (package_root / "__init__.py").is_file() else package_root
        else:
            root = resolved.resolve().parent
        if root not in roots:
            roots.append(root)
    return roots


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/code_quality/check.py:226).
def subprocess_env(config: CheckConfig) -> dict[str, str]:  # pragma: no cover
    """Subprocess environment with this checkout's source roots first on PYTHONPATH."""
    env = dict(os.environ)
    roots = [
        str(root) for root in source_import_roots(config.project_root, config.scope.coverage_paths)
    ]
    existing = env.get("PYTHONPATH")
    if existing:
        roots.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(roots)
    if config.coverage_data is not None:
        config.coverage_data.parent.mkdir(parents=True, exist_ok=True)
        env["COVERAGE_FILE"] = str(config.coverage_data)
    return env


def run_quality_check(
    config: CheckConfig,
    *,
    runner: CommandRunner = run_subprocess,
    printer: Printer = print_line,
) -> int:
    require_dagger_admission_capability(config.admission)
    project_root = config.project_root.resolve()
    progress = QualityProgress.start(config.progress_report)
    targeted = getattr(config, "targeted", False)
    if targeted and config.targeted_scope is not None and config.targeted_base is not None:
        for line in scope_reporting.targeted_scope_lines(
            config.targeted_base, config.targeted_scope
        ):
            printer(line)
        if not config.scope.lint_paths:
            printer(
                "targeted: no Python files changed against the leaf base; there is nothing "
                "for the leaf rails to certify"
            )
            progress.write(status="completed", step="complete", detail="no Python changes")
            printer("result: quality-wrapper PASS")
            return 0
        if not config.scope.coverage_paths:
            printer(
                "targeted: radon report and CRAP rails are not applicable -- no changed "
                "production modules"
            )
        if not config.scope.test_paths:
            printer(
                "targeted: pytest rail is not applicable -- no test subset was derived "
                "(no changed production modules and no changed tests)"
            )
    printer(scope_reporting.wrapper_scope_line(project_root, config.scope, targeted=targeted))
    for line in scope_reporting.untracked_scope_lines(config.scope):
        printer(line)
    with coverage_path_context(config.coverage_json, project_root) as coverage_json:
        failed_steps = execute_quality_rails(
            replace(config, progress=progress),
            coverage_json,
            project_root,
            runner=runner,
            printer=printer,
        )
    if failed_steps:
        progress.write(status="failed", step="complete", detail=f"{failed_steps} failed rails")
        printer(f"result: quality-wrapper FAIL ({failed_steps} failed rails)")
        return 1
    progress.write(status="completed", step="complete", detail="all quality rails passed")
    printer("result: quality-wrapper PASS")
    return 0


def execute_quality_rails(
    config: CheckConfig,
    coverage_json: Path,
    project_root: Path,
    *,
    runner: CommandRunner,
    printer: Printer,
) -> int:
    # An explicit output path may contain a report from an earlier tree. Never let a
    # pre-pytest refusal feed stale coverage into CRAP or diff-coverage.
    coverage_json.unlink(missing_ok=True)
    if config.coverage_data is not None:
        config.coverage_data.parent.mkdir(parents=True, exist_ok=True)
        config.coverage_data.unlink(missing_ok=True)
    if config.pytest_report_log is not None:
        config.pytest_report_log.parent.mkdir(parents=True, exist_ok=True)
        config.pytest_report_log.unlink(missing_ok=True)
    if config.pytest_phase_report is not None:
        config.pytest_phase_report.parent.mkdir(parents=True, exist_ok=True)
        config.pytest_phase_report.unlink(missing_ok=True)
    if config.causal_failure_report is not None:
        config.causal_failure_report.parent.mkdir(parents=True, exist_ok=True)
        config.causal_failure_report.unlink(missing_ok=True)
        config.causal_failure_report.with_suffix(".md").unlink(missing_ok=True)
    retry_plan = initialized_retry_plan(
        config,
        coverage_json,
        project_root,
        runner=runner,
        printer=printer,
    )
    failed_steps = run_fixed_checks(
        config,
        coverage_json,
        runner=runner,
        printer=printer,
        retry_plan=retry_plan,
    )
    if failed_steps and not coverage_json.is_file():
        report_missing_coverage_failure(printer)
    else:
        failed_steps = complete_coverage_rails(
            config,
            coverage_json,
            fixed_failures=failed_steps,
            runtime=RailRuntime(runner=runner, printer=printer, retry_plan=retry_plan),
        )
    if retry_plan is not None:
        retry_plan.finish(coverage_json, quality_passed=failed_steps == 0)
    return failed_steps


def complete_coverage_rails(
    config: CheckConfig,
    coverage_json: Path,
    *,
    fixed_failures: int,
    runtime: RailRuntime,
) -> int:
    project_root = config.project_root.resolve()
    coverage_failures = run_coverage_rails(
        config,
        coverage_json,
        project_root,
        printer=runtime.printer,
    )
    if not (
        runtime.retry_plan is not None
        and runtime.retry_plan.delta
        and runtime.retry_plan.pytest_passed
        and coverage_failures
        and not fixed_failures
    ):
        return fixed_failures + coverage_failures
    runtime.printer(
        "retry-proof: conservative delta coverage did not clear every post-pytest rail; "
        "running the full pytest selection once for a conclusive verdict"
    )
    runtime.retry_plan.prepare_full_fallback(coverage_json)
    pytest_failures = run_pytest_only(
        config,
        coverage_json,
        runtime.retry_plan,
        runner=runtime.runner,
        printer=runtime.printer,
    )
    if pytest_failures and not coverage_json.is_file():
        report_missing_coverage_failure(runtime.printer)
        return pytest_failures
    return pytest_failures + run_coverage_rails(
        config,
        coverage_json,
        project_root,
        printer=runtime.printer,
    )


def run_pytest_only(
    config: CheckConfig,
    coverage_json: Path,
    retry_plan: retry_proof.RetryPlan,
    *,
    runner: CommandRunner,
    printer: Printer,
) -> int:
    progress = config.progress or QualityProgress.start(None)
    step = next(
        (
            candidate
            for candidate in quality_steps(config, coverage_json, retry_plan=retry_plan)
            if candidate.name == "pytest"
        ),
        None,
    )
    if step is None:
        printer("result: pytest FAIL (full fallback derived no pytest rail)")
        return 1
    printer(step_header(step))
    printer(
        scope_reporting.fixed_step_scope_line(
            step.name,
            config.project_root,
            config.scope,
            targeted=config.targeted,
        )
    )
    env = subprocess_env(config)
    env["COVERAGE_FILE"] = str(retry_plan.active_data_path)
    result = runner(step.name, step.command, config.project_root, env)
    retry_plan.record_pytest(result.return_code)
    failures = report_pytest_result(step, result, coverage_json, printer)
    progress.finish_step(step.name, passed=failures == 0)
    return failures


def initialized_retry_plan(
    config: CheckConfig,
    coverage_json: Path,
    project_root: Path,
    *,
    runner: CommandRunner,
    printer: Printer,
) -> retry_proof.RetryPlan | None:
    plan = prepare_retry_plan(
        config,
        project_root,
        runner=runner,
        printer=printer,
    )
    if plan is None:
        return None
    try:
        plan.prepare_artifacts(coverage_json)
    except (OSError, RuntimeError) as error:
        printer(f"retry-proof: artifact preparation failed ({error}); running fresh")
        coverage_json.unlink(missing_ok=True)
        return None
    return plan


def report_missing_coverage_failure(printer: Printer) -> None:
    printer("\n## CRAP-Calculator")
    printer("result: CRAP-Calculator SKIPPED (pytest coverage was not produced)")
    printer("\n## diff-coverage")
    printer("result: diff-coverage SKIPPED (pytest coverage was not produced)")


def run_coverage_rails(
    config: CheckConfig,
    coverage_json: Path,
    project_root: Path,
    *,
    printer: Printer,
) -> int:
    progress = config.progress or QualityProgress.start(None)
    progress.write(status="running", step="CRAP-Calculator", detail="score covered functions")
    crap_failures = run_crap_calculator(
        config,
        coverage_json,
        project_root,
        printer=printer,
    )
    progress.finish_step("CRAP-Calculator", passed=crap_failures == 0)
    progress.write(status="running", step="diff-coverage", detail="score changed statements")
    diff_failures = run_diff_coverage(
        config,
        coverage_json,
        project_root,
        printer=printer,
    )
    progress.finish_step("diff-coverage", passed=diff_failures == 0)
    return crap_failures + diff_failures


def run_fixed_checks(
    config: CheckConfig,
    coverage_json: Path,
    *,
    runner: CommandRunner,
    printer: Printer,
    retry_plan: retry_proof.RetryPlan | None = None,
) -> int:
    progress = config.progress or QualityProgress.start(None)
    env = subprocess_env(config)
    if retry_plan is not None:
        env["COVERAGE_FILE"] = str(retry_plan.active_data_path)
    targeted = getattr(config, "targeted", False)
    failed_steps = 0
    pytest_blocking_failures = 0
    causal_failure = False
    for step in quality_steps(config, coverage_json, retry_plan=retry_plan):
        progress.write(status="running", step=step.name, detail="run quality rail")
        printer(step_header(step))
        printer(
            scope_reporting.fixed_step_scope_line(
                step.name,
                config.project_root,
                config.scope,
                targeted=targeted,
            )
        )
        if step.name == "pytest" and pytest_blocking_failures:
            # An exact retry restored cached JSON before the cheap rails ran. If one of
            # those rails now breaks, discard that artifact too: no post-pytest rail may
            # consume earlier-tree evidence after pytest was deliberately skipped.
            coverage_json.unlink(missing_ok=True)
            printer("result: pytest SKIPPED (an earlier quality rail failed)")
            progress.finish_step(step.name, passed=False)
            continue
        active_step = _causal_continuation_step(
            step,
            config,
            coverage_json,
            causal_failure=causal_failure,
        )
        if active_step is None:
            failed_steps += 1
            pytest_blocking_failures += 1
            printer("result: pytest FAIL (causal continuation derived no pytest rail)")
            progress.finish_step(step.name, passed=False)
            continue
        if (
            active_step.name == "pytest"
            and retry_plan is not None
            and retry_plan.exact
            and not causal_failure
        ):
            cached_failures = report_cached_pytest(coverage_json, printer)
            failed_steps += cached_failures
            progress.finish_step(step.name, passed=cached_failures == 0)
            continue
        result = runner(active_step.name, active_step.command, config.project_root, env)
        if active_step.name == "pytest" and retry_plan is not None:
            retry_plan.record_pytest(result.return_code)
        if active_step.name == "pytest":
            pytest_failures = report_pytest_result(active_step, result, coverage_json, printer)
            failed_steps += pytest_failures
            progress.finish_step(active_step.name, passed=pytest_failures == 0)
            continue
        if result.return_code == 0:
            printer(step_success(active_step))
            progress.finish_step(active_step.name, passed=True)
            continue
        failed_steps += 1
        valid_causal_report = (
            active_step.name == "causal-preflight"
            and config.causal_failure_report is not None
            and causal_preflight.failed_report(config.causal_failure_report)
        )
        if valid_causal_report:
            causal_failure = True
        else:
            pytest_blocking_failures += 1
        printer(step_failure(active_step, result.return_code))
        progress.finish_step(active_step.name, passed=False)
        report_memory_cap_failure(active_step.name, printer)
    return failed_steps


def _causal_continuation_step(
    step: Step,
    config: CheckConfig,
    coverage_json: Path,
    *,
    causal_failure: bool,
) -> Step | None:
    if step.name != "pytest" or not causal_failure:
        return step
    coverage_json.unlink(missing_ok=True)
    if config.coverage_data is not None:
        config.coverage_data.unlink(missing_ok=True)
    return next(
        (
            candidate
            for candidate in quality_steps(config, coverage_json, retry_plan=None)
            if candidate.name == "pytest"
        ),
        None,
    )


def report_cached_pytest(coverage_json: Path, printer: Printer) -> int:
    try:
        printer(scope_reporting.coverage_result_scope_line(coverage_json))
    except ScopeError as error:
        printer(f"coverage result reporting failed: {error}")
        printer("result: pytest FAIL (cached Coverage.py result scope unavailable)")
        return 1
    printer("result: pytest PASS (exact content-addressed proof reused)")
    return 0


def report_pytest_result(
    step: Step,
    result: StepResult,
    coverage_json: Path,
    printer: Printer,
) -> int:
    if coverage_json.is_file():
        try:
            printer(scope_reporting.coverage_result_scope_line(coverage_json))
        except ScopeError as error:
            printer(f"coverage result reporting failed: {error}")
            printer("result: pytest FAIL (Coverage.py result scope unavailable)")
            return 1
    if result.return_code == 0:
        printer(step_success(step))
        return 0
    printer(step_failure(step, result.return_code))
    report_memory_cap_failure(step.name, printer)
    return 1


def report_memory_cap_failure(step_name: str, printer: Printer) -> None:
    cap = os.environ.get(memory_cap.MEMORY_CAP_ENV)
    if cap:
        printer(
            f"{step_name} may have died from the quality memory cap "
            f"(policy={memory_cap.QUALITY_MEMORY_CAP_POLICY}; "
            f"mechanism={memory_cap.RLIMIT_MECHANISM}; cap={cap} bytes; "
            "see the memory-cap line at the top of this run)"
        )


def prepare_retry_plan(
    config: CheckConfig,
    project_root: Path,
    *,
    runner: CommandRunner,
    printer: Printer,
) -> retry_proof.RetryPlan | None:
    """Enable proof reuse only for the real wrapper subprocess pipeline."""
    if runner is not run_subprocess:
        return None
    try:
        base = (
            config.targeted_base
            if config.targeted_base is not None
            else diff_coverage.resolve_base(project_root, explicit_base=config.diff_base)
        )
        return retry_proof.prepare(
            retry_proof.RetryInputs(
                project_root=project_root,
                targeted=config.targeted,
                base_revision=base.revision,
                threshold=config.threshold,
                top=config.top,
                diff_floor=config.diff_floor,
                coverage_paths=tuple(config.scope.coverage_paths),
                test_arguments=tuple(config.scope.test_paths),
                untracked_paths=tuple(config.scope.untracked_paths),
            ),
            admission=config.admission,
            printer=printer,
        )
    except (OSError, RuntimeError, ScopeError) as error:
        printer(f"retry-proof: unavailable ({error}); running fresh")
        return None


def step_header(step: Step) -> str:
    if step.report_note is None:
        return f"\n## {step.name}"
    return f"\n## {step.name} -- {step.report_note}"


def step_failure(step: Step, return_code: int) -> str:
    if step.report_note is None:
        return f"result: {step.name} FAIL (exit code {return_code})"
    return (
        f"result: {step.name} FAIL; {step.name} could not run (exit code {return_code}). "
        "This is a report step, "
        "so this is the tool breaking rather than a finding -- fix the tool."
    )


def step_success(step: Step) -> str:
    if step.report_note is None:
        return f"result: {step.name} PASS"
    return f"result: {step.name} REPORT COMPLETE (non-enforcing)"


class coverage_path_context:
    def __init__(self, requested_path: Path | None, project_root: Path) -> None:
        self.requested_path = requested_path
        self.project_root = project_root
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    # 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/code_quality/check.py:468).
    def __enter__(self) -> Path:  # pragma: no cover
        if self.requested_path is not None:
            self.path = resolve_under_root(self.requested_path, self.project_root)
            return self.path
        self.temp_dir = tempfile.TemporaryDirectory(prefix="agents-remember-quality-")
        self.path = Path(self.temp_dir.name) / "coverage.json"
        return self.path

    # 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/code_quality/check.py:476).
    def __exit__(self, *exc_info: object) -> None:  # pragma: no cover
        if self.temp_dir is not None:
            self.temp_dir.cleanup()


def resolve_under_root(path: Path, project_root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def config_from_args(
    args: argparse.Namespace,
    *,
    admission: DaggerAdmission,
) -> CheckConfig:
    require_dagger_admission_capability(admission)
    project_root = args.project_root.resolve()
    configured_progress = getattr(args, "progress_report", None)
    if configured_progress is None and (
        progress_env := os.environ.get(QUALITY_PROGRESS_REPORT_ENV)
    ):
        configured_progress = Path(progress_env)
    configured_coverage_data = getattr(args, "coverage_data", None)
    if configured_coverage_data is None and (coverage_env := os.environ.get("COVERAGE_FILE")):
        configured_coverage_data = Path(coverage_env)
    configured_causal_report = getattr(args, "causal_failure_report", None)
    if configured_causal_report is None:
        configured_causal_report = QUALITY_TEMP_ROOT / str(os.getpid()) / "causal-failures.json"
    quality_scope.validate_quality_config(project_root)
    try:
        scope_reporting.validate_invocation_environment()
    except scope_reporting.ScopeReportingError as error:
        raise ScopeError(str(error)) from error
    if args.targeted:
        base = diff_coverage.resolve_base(project_root, explicit_base=args.diff_base)
        derived = targeted.derive_targeted_scope(project_root, base.revision)
        full_scope = derive_scope(project_root)
        return CheckConfig(
            project_root=project_root,
            admission=admission,
            scope=derived.to_gate_scope(full_scope),
            coverage_json=args.coverage_json,
            threshold=args.threshold,
            top=args.top,
            diff_base=args.diff_base,
            diff_floor=args.diff_floor,
            targeted=True,
            targeted_base=base,
            targeted_scope=derived,
            file_size_armed=quality_scope.file_size_armed(project_root),
            pytest_report_log=getattr(args, "pytest_report_log", None),
            pytest_phase_report=getattr(args, "pytest_phase_report", None),
            causal_failure_report=configured_causal_report,
            coverage_data=configured_coverage_data,
            progress_report=configured_progress,
        )
    return CheckConfig(
        project_root=project_root,
        admission=admission,
        scope=derive_scope(project_root),
        coverage_json=args.coverage_json,
        threshold=args.threshold,
        top=args.top,
        diff_base=args.diff_base,
        diff_floor=args.diff_floor,
        file_size_armed=quality_scope.file_size_armed(project_root),
        pytest_report_log=getattr(args, "pytest_report_log", None),
        pytest_phase_report=getattr(args, "pytest_phase_report", None),
        causal_failure_report=configured_causal_report,
        coverage_data=configured_coverage_data,
        progress_report=configured_progress,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        admission = require_dagger_admission(subject="Agents Remember quality wrapper")
    except DaggerAdmissionError as error:
        print_line(str(error))
        print_line("result: quality-wrapper FAIL")
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    native_environment = native_subprocess_environment(os.environ, temp_root=QUALITY_TEMP_ROOT)
    os.environ.clear()
    os.environ.update(native_environment)
    # ``tempfile`` caches its chosen directory process-wide. Reset it after sanitising the
    # environment so a module imported before ``main`` cannot preserve a Windows/long-path root.
    tempfile.tempdir = QUALITY_TEMP_ROOT.as_posix()
    if args.memory_cap_bytes is not None and args.memory_cap_bytes <= 0:
        print_line(
            "--memory-cap-bytes must be a positive integer "
            f"(policy={memory_cap.QUALITY_MEMORY_CAP_POLICY})"
        )
        print_line("result: quality-wrapper FAIL")
        return 1
    if args.memory_cap_bytes is not None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (args.memory_cap_bytes, args.memory_cap_bytes))
        except (ValueError, OSError) as error:
            print_line(
                f"memory-cap could not be applied (policy={memory_cap.QUALITY_MEMORY_CAP_POLICY}; "
                f"mechanism={memory_cap.RLIMIT_MECHANISM}; cap={args.memory_cap_bytes} bytes): "
                f"{error}"
            )
            print_line("result: quality-wrapper FAIL")
            return 1
        os.environ[memory_cap.MEMORY_CAP_ENV] = str(args.memory_cap_bytes)
        print_line(
            f"memory-cap: policy={memory_cap.QUALITY_MEMORY_CAP_POLICY}; "
            f"mechanism={memory_cap.RLIMIT_MECHANISM}; cap={args.memory_cap_bytes} bytes"
        )
    try:
        config = config_from_args(args, admission=admission)
        return run_quality_check(config)
    except ScopeError as error:
        print_line(f"gate scope could not be derived: {error}")
        print_line("result: quality-wrapper FAIL")
        return 1
    except MemoryError:
        if args.memory_cap_bytes is not None:
            print_line(
                "result: quality-wrapper FAIL (memory cap exceeded; "
                f"policy={memory_cap.QUALITY_MEMORY_CAP_POLICY}; "
                f"mechanism={memory_cap.RLIMIT_MECHANISM}; cap={args.memory_cap_bytes} bytes)"
            )
        else:
            print_line("result: quality-wrapper FAIL (out of memory)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
