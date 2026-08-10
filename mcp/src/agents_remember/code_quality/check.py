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

Local retries can reuse a content-addressed successful pytest proof when the exact tree
is unchanged or only selected test modules changed. Test-delta reuse strips their prior
Coverage.py contexts before appending fresh data; any ambiguity runs fresh, and CI never
reuses local proof.

Each rail prints its actual input, config, nonzero population or result denominator, and
explicit result. Missing/vacuous inputs and tool failures refuse. Findings are remediated
in source or tests; baselines, allowlists, and exemptions are not supported.
"""

from __future__ import annotations

import argparse
import os
import resource
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality import (
    crap_calculator,
    diff_coverage,
    memory_cap,
    post_coverage,
    retry_proof,
    scope_reporting,
    targeted,
)
from agents_remember.code_quality import (
    scope as quality_scope,
)

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


@dataclass(frozen=True)
class CheckConfig:
    project_root: Path
    scope: GateScope
    coverage_json: Path | None
    threshold: float
    top: int
    diff_base: str | None = None
    diff_floor: float = diff_coverage.DEFAULT_DIFF_COVERAGE_FLOOR
    targeted: bool = False
    targeted_base: diff_coverage.BaseResolution | None = None
    targeted_scope: targeted.TargetedScopeResult | None = None
    file_size_armed: bool = False


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


def _fixed_steps(lint_args: list[str], type_args: list[str]) -> list[Step]:
    """Ruff, ruff-format, and pyright over the rail's path scope."""
    return [
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
    ]


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
    if getattr(config, "targeted", False) and not config.scope.test_paths:
        return None
    pytest_args = [sys.executable, "-m", "pytest", *test_args]
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
    steps = _fixed_steps(posix_args(scope.lint_paths), posix_args(scope.type_paths))
    # Keep the inexpensive structural rail ahead of type analysis, reports, and the broad pytest
    # run. Pytest must remain the final subprocess because CRAP and diff coverage consume the
    # coverage artifact it produces and therefore cannot safely run before it.
    steps.insert(2, _file_size_step(config, posix_args(scope.size_paths)))
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
    return env


def run_quality_check(
    config: CheckConfig,
    *,
    runner: CommandRunner = run_subprocess,
    printer: Printer = print_line,
) -> int:
    project_root = config.project_root.resolve()
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
            config,
            coverage_json,
            project_root,
            runner=runner,
            printer=printer,
        )
    if failed_steps:
        printer(f"result: quality-wrapper FAIL ({failed_steps} failed rails)")
        return 1
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
    return report_pytest_result(step, result, coverage_json, printer)


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
    return run_crap_calculator(
        config,
        coverage_json,
        project_root,
        printer=printer,
    ) + run_diff_coverage(
        config,
        coverage_json,
        project_root,
        printer=printer,
    )


def run_fixed_checks(
    config: CheckConfig,
    coverage_json: Path,
    *,
    runner: CommandRunner,
    printer: Printer,
    retry_plan: retry_proof.RetryPlan | None = None,
) -> int:
    env = subprocess_env(config)
    if retry_plan is not None:
        env["COVERAGE_FILE"] = str(retry_plan.active_data_path)
    targeted = getattr(config, "targeted", False)
    failed_steps = 0
    for step in quality_steps(config, coverage_json, retry_plan=retry_plan):
        printer(step_header(step))
        printer(
            scope_reporting.fixed_step_scope_line(
                step.name,
                config.project_root,
                config.scope,
                targeted=targeted,
            )
        )
        if step.name == "pytest" and failed_steps:
            # An exact retry restored cached JSON before the cheap rails ran. If one of
            # those rails now breaks, discard that artifact too: no post-pytest rail may
            # consume earlier-tree evidence after pytest was deliberately skipped.
            coverage_json.unlink(missing_ok=True)
            printer("result: pytest SKIPPED (an earlier quality rail failed)")
            continue
        if step.name == "pytest" and retry_plan is not None and retry_plan.exact:
            failed_steps += report_cached_pytest(coverage_json, printer)
            continue
        result = runner(step.name, step.command, config.project_root, env)
        if step.name == "pytest" and retry_plan is not None:
            retry_plan.record_pytest(result.return_code)
        if step.name == "pytest":
            failed_steps += report_pytest_result(step, result, coverage_json, printer)
            continue
        if result.return_code == 0:
            printer(step_success(step))
            continue
        failed_steps += 1
        printer(step_failure(step, result.return_code))
        report_memory_cap_failure(step.name, printer)
    return failed_steps


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
                test_roots=tuple(pytest_testpaths(project_root)),
                untracked_paths=tuple(config.scope.untracked_paths),
            ),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Agents Remember source quality suite over every tracked Python "
            "file. Enforcing steps: Ruff (lint, complexity rules "
            "C901/PLR0911/PLR0912/PLR0915 included), Ruff format (--check), Pyright "
            "(types), pytest coverage, mandatory CRAP threshold enforcement, and the "
            "changed-lines coverage floor. The File Size Budget rail is wired here "
            "too; [tool.agents_remember] file_size_armed decides whether a violation "
            "fails the run (unarmed runs still report every band). Radon "
            "cyclomatic complexity and maintainability index are printed as a report "
            "only -- radon exits 0 whatever it finds, so it cannot fail this gate. Scope "
            "orders cheap deterministic subprocesses before pytest; CRAP and diff coverage "
            "then consume pytest's branch data. Local exact/test-only retries are "
            "content-addressed and fail closed; CI always runs fresh. Quality scope "
            "is derived from the index and configured roots, not from a flag: there is "
            "no way to narrow what the gate measures. Non-ignored untracked siblings are "
            "reported as outside that measurement. No baseline, allowlist or exemption "
            "file anywhere in it can excuse a finding."
        )
    )
    parser.add_argument(
        "--targeted",
        action="store_true",
        help=(
            "Run the leaf change-set contract instead of the full tree: ruff over the "
            "changed Python files, pyright over the changed files plus the reverse-import "
            "closure, pytest over the derived test subset, and coverage/CRAP scoped to the "
            "changed production modules. The derivation is printed for review."
        ),
    )
    parser.add_argument(
        "--memory-cap-bytes",
        type=int,
        help=(
            "Apply a POSIX address-space rlimit (RLIMIT_AS) to this process and every rail "
            "it spawns, so an over-cap run dies inside its own process instead of taking the "
            f"host down. Policy: {memory_cap.QUALITY_MEMORY_CAP_POLICY}."
        ),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--coverage-json",
        type=Path,
        help="Optional path for the generated Coverage.py JSON report.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=crap_calculator.DEFAULT_CRAP_THRESHOLD,
        help="Fail when any function has a CRAP score at or above this value.",
    )
    parser.add_argument("--top", type=int, default=crap_calculator.DEFAULT_TOP)
    parser.add_argument(
        "--diff-base",
        help=(
            "Revision the changed-lines coverage floor diffs against. Defaults to the "
            "merge base with, in order, AR_GATE_DIFF_BASE, the pull request base, the "
            "branch's upstream, then the default branch. The base actually used is "
            "printed on every run."
        ),
    )
    parser.add_argument(
        "--diff-floor",
        type=float,
        default=diff_coverage.DEFAULT_DIFF_COVERAGE_FLOOR,
        help=(
            "Fail when coverage of the changed statements and branches is below this percentage."
        ),
    )
    return parser


def config_from_args(args: argparse.Namespace) -> CheckConfig:
    project_root = args.project_root.resolve()
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
            scope=derived.to_gate_scope(full_scope),
            coverage_json=args.coverage_json,
            threshold=args.threshold,
            top=args.top,
            diff_base=args.diff_base,
            diff_floor=args.diff_floor,
            targeted=True,
            targeted_base=base,
            targeted_scope=derived,
        )
    return CheckConfig(
        project_root=project_root,
        scope=derive_scope(project_root),
        coverage_json=args.coverage_json,
        threshold=args.threshold,
        top=args.top,
        diff_base=args.diff_base,
        diff_floor=args.diff_floor,
        file_size_armed=quality_scope.file_size_armed(project_root),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
        config = config_from_args(args)
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
