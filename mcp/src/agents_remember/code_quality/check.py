"""Run the repository-owned Python quality rails with explicit provenance.

Ruff, Ruff format, Pyright, pytest, CRAP, and changed-lines coverage enforce. Radon CC
and MI are labelled reports because Radon findings do not change its exit status. Scope
comes from ``code_quality.scope``: lint/type paths are index-known, while Radon and
Coverage.py recursively consume the configured on-disk production roots. The wrapper
reports relevant untracked files separately because the index and diff omit them.

Each rail prints its actual input, config, nonzero population or result denominator, and
explicit result. Missing/vacuous inputs and tool failures refuse. Findings are remediated
in source or tests; baselines, allowlists, and exemptions are not supported.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality import (
    crap_calculator,
    diff_coverage,
    scope_reporting,
)
from agents_remember.code_quality import (
    scope as quality_scope,
)

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


def print_line(message: str) -> None:
    print(message, flush=True)


def run_subprocess(name: str, command: list[str], cwd: Path, env: Mapping[str, str]) -> StepResult:
    completed = subprocess.run(
        command, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, check=False
    )
    return StepResult(name=name, return_code=completed.returncode, command=command)


# --- steps -------------------------------------------------------------------


def quality_steps(config: CheckConfig, coverage_json: Path) -> list[Step]:
    scope = config.scope
    lint_args = posix_args(scope.lint_paths)
    type_args = posix_args(scope.type_paths)
    coverage_args = posix_args(scope.coverage_paths)
    test_args = posix_args(scope.test_paths)
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
        Step(
            "radon-cc",
            [
                sys.executable,
                "-m",
                "radon",
                "cc",
                *coverage_args,
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
            [sys.executable, "-m", "radon", "mi", *coverage_args, "-s", "-n", "B"],
            report_note=RADON_REPORT_NOTE,
        ),
        Step(
            "pytest",
            [
                sys.executable,
                "-m",
                "pytest",
                *test_args,
                *coverage_arguments(scope.coverage_paths),
                f"--cov-report=json:{coverage_json.as_posix()}",
                "--cov-report=term",
            ],
        ),
    ]


def posix_args(paths: list[Path]) -> list[str]:
    return [path.as_posix() for path in paths]


def coverage_arguments(coverage_paths: list[Path]) -> list[str]:
    return [f"--cov={path.as_posix()}" for path in coverage_paths]


def source_import_roots(project_root: Path, coverage_paths: list[Path]) -> list[Path]:
    """Import roots for the tracked source packages.

    Each coverage path points at a package directory (e.g. ``mcp/src/agents_remember``);
    its parent (``mcp/src``) is the directory that must be importable. Putting these on
    PYTHONPATH makes the wrapper's subprocesses import and cover *this* checkout's source
    rather than whatever an editable install resolves to, so the gate behaves identically
    from the primary clone and from any git worktree.
    """
    roots: list[Path] = []
    for source in coverage_paths:
        resolved = source if source.is_absolute() else project_root / source
        root = resolved.resolve().parent
        if root not in roots:
            roots.append(root)
    return roots


def subprocess_env(config: CheckConfig) -> dict[str, str]:
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
    printer(scope_reporting.wrapper_scope_line(project_root, config.scope))
    for line in scope_reporting.untracked_scope_lines(config.scope):
        printer(line)
    with coverage_path_context(config.coverage_json, project_root) as coverage_json:
        failed_steps = run_fixed_checks(config, coverage_json, runner=runner, printer=printer)
        failed_steps += run_crap_calculator(
            config,
            coverage_json,
            project_root,
            printer=printer,
        )
        failed_steps += run_diff_coverage(
            config,
            coverage_json,
            project_root,
            printer=printer,
        )
    if failed_steps:
        printer(f"result: quality-wrapper FAIL ({failed_steps} failed rails)")
        return 1
    printer("result: quality-wrapper PASS")
    return 0


def run_fixed_checks(
    config: CheckConfig,
    coverage_json: Path,
    *,
    runner: CommandRunner,
    printer: Printer,
) -> int:
    env = subprocess_env(config)
    failed_steps = 0
    for step in quality_steps(config, coverage_json):
        printer(step_header(step))
        printer(
            scope_reporting.fixed_step_scope_line(
                step.name,
                config.project_root,
                config.scope,
            )
        )
        result = runner(step.name, step.command, config.project_root, env)
        if step.name == "pytest" and coverage_json.is_file():
            try:
                printer(scope_reporting.coverage_result_scope_line(coverage_json))
            except ScopeError as error:
                failed_steps += 1
                printer(f"coverage result reporting failed: {error}")
                printer("result: pytest FAIL (Coverage.py result scope unavailable)")
                continue
        if result.return_code == 0:
            printer(step_success(step))
            continue
        failed_steps += 1
        printer(step_failure(step, result.return_code))
    return failed_steps


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


def run_crap_calculator(
    config: CheckConfig,
    coverage_json: Path,
    project_root: Path,
    *,
    printer: Printer,
) -> int:
    printer("\n## CRAP-Calculator")
    if not coverage_json.exists():
        printer(
            scope_reporting.crap_scope_line(
                project_root / "pyproject.toml",
                coverage_json,
                0,
                config.threshold,
            )
        )
        printer(f"coverage JSON was not created: {coverage_json}")
        printer("result: CRAP-Calculator FAIL")
        return 1
    try:
        scores = crap_calculator.calculate_scores(
            config.scope.coverage_paths,
            coverage_json=coverage_json,
            project_root=project_root,
        )
    except RuntimeError as error:
        printer(str(error))
        printer("result: CRAP-Calculator FAIL")
        return 1
    printer(
        scope_reporting.crap_scope_line(
            project_root / "pyproject.toml",
            coverage_json,
            len(scores),
            config.threshold,
        )
    )
    if not scores:
        printer(
            "CRAP scored zero functions; production coverage scope is vacuous. Correct the "
            "production roots or add the measurable production functions the declared package "
            "is required to contain."
        )
        printer("result: CRAP-Calculator FAIL")
        return 1
    printer(crap_calculator.render_table(scores, project_root, config.threshold, config.top))
    over_threshold = [score for score in scores if score.crap >= config.threshold]
    if not over_threshold:
        printer("result: CRAP-Calculator PASS")
        return 0
    # Every offender, not the first `--top` of them. The table above is a fixed-length
    # report; the failure list is the work, and a gate that truncates its own findings
    # sends the reader back to run the tool by hand to see the rest.
    printer(
        f"\n{len(over_threshold)} function(s) meet or exceed the CRAP threshold "
        f"{config.threshold:.1f}. There is no exemption list: each one is fixed by raising "
        "its branch coverage or by splitting it."
    )
    for score in over_threshold:
        printer(crap_failure_line(score, project_root, config.threshold))
    printer("result: CRAP-Calculator FAIL")
    return 1


def crap_failure_line(
    score: crap_calculator.FunctionScore, project_root: Path, threshold: float
) -> str:
    """One offender, with the branch coverage that would clear it.

    ``crap = cc**2 * (1 - coverage)**3 + cc`` inverts exactly, so the gate can say what it
    is asking for rather than leaving the reader to solve for it. When the complexity term
    alone is already at the threshold there is no such coverage, and the only way through
    is to split the function -- which the line says instead of naming an impossible number.
    """
    location = f"{crap_calculator.display_path(score.path, project_root)}:{score.start_line}"
    needed = crap_calculator.coverage_clearing(score.complexity, threshold)
    remedy = (
        f"needs branch coverage above {needed * 100:.1f}%"
        if needed is not None
        else f"cannot clear {threshold:.1f} at any coverage (cc {score.complexity}); split it"
    )
    return (
        f"  {score.crap:6.2f}  cc {score.complexity:>3}  "
        f"branch {score.coverage_ratio * 100:5.1f}%  {location} {score.function} -- {remedy}"
    )


def run_diff_coverage(
    config: CheckConfig,
    coverage_json: Path,
    project_root: Path,
    *,
    printer: Printer,
) -> int:
    """The changed-lines coverage floor, scored from the coverage JSON pytest just wrote.

    It lives inside the wrapper rather than beside it so it reaches the pre-push hook,
    the closeout path and CI through the one command they already run. A separate
    invocation is a gate somebody has to remember, which is the same as not having one.
    """
    printer("\n## diff-coverage")
    if not coverage_json.exists():
        printer(
            scope_reporting.scope_line(
                "diff-coverage",
                "changed Python diff intersected with missing Coverage.py JSON",
                f"coverage input={coverage_json.as_posix()}; floor={config.diff_floor:.1f}%",
                "0 measurable statements+branches",
            )
        )
        printer(f"coverage JSON was not created: {coverage_json}")
        printer("result: diff-coverage FAIL")
        return 1
    try:
        base = diff_coverage.resolve_base(project_root, explicit_base=config.diff_base)
        result = diff_coverage.measure(project_root, coverage_json, base)
    except (RuntimeError, OSError) as error:
        printer(str(error))
        printer("result: diff-coverage FAIL")
        return 1
    printer(scope_reporting.diff_scope_line(result, coverage_json, config.diff_floor))
    for line in diff_coverage.render(result, config.diff_floor):
        printer(line)
    if result.state == "measured" and result.percent < config.diff_floor:
        printer("result: diff-coverage FAIL")
        return 1
    if result.state == "measured":
        printer("result: diff-coverage PASS")
    else:
        printer(f"result: diff-coverage PASS (not applicable: {result.state})")
    return 0


class coverage_path_context:
    def __init__(self, requested_path: Path | None, project_root: Path) -> None:
        self.requested_path = requested_path
        self.project_root = project_root
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if self.requested_path is not None:
            self.path = resolve_under_root(self.requested_path, self.project_root)
            return self.path
        self.temp_dir = tempfile.TemporaryDirectory(prefix="agents-remember-quality-")
        self.path = Path(self.temp_dir.name) / "coverage.json"
        return self.path

    def __exit__(self, *exc_info: object) -> None:
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
            "changed-lines coverage floor. Radon "
            "cyclomatic complexity and maintainability index are printed as a report "
            "only -- radon exits 0 whatever it finds, so it cannot fail this gate. Scope "
            "is derived from the index and configured roots, not from a flag: there is "
            "no way to narrow what the gate measures. Non-ignored untracked siblings are "
            "reported as outside that measurement. No baseline, allowlist or exemption "
            "file anywhere in it can excuse a finding."
        )
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
    return CheckConfig(
        project_root=project_root,
        scope=derive_scope(project_root),
        coverage_json=args.coverage_json,
        threshold=args.threshold,
        top=args.top,
        diff_base=args.diff_base,
        diff_floor=args.diff_floor,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
    except ScopeError as error:
        print_line(f"gate scope could not be derived: {error}")
        print_line("result: quality-wrapper FAIL")
        return 1
    return run_quality_check(config)


if __name__ == "__main__":
    sys.exit(main())
