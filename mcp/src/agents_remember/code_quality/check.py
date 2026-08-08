"""Run the repository-owned Python quality rails with explicit provenance.

Ruff, Ruff format, Pyright, pytest, CRAP, and changed-lines coverage enforce. Radon CC
and MI are labelled reports because Radon findings do not change its exit status. Scope
comes from ``code_quality.scope`` for a full run; ``--targeted`` derives the leaf
change-set scope from ``code_quality.targeted`` (changed files, reverse-import closure,
and the derived test subset). Full runs may additionally run under a settings-owned
memory cap (``--memory-cap-bytes`` / ``orchestration.qualityGate.memoryCapBytes``).
The wrapper reports relevant untracked files separately because the index and diff
omit them.

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
    scope_reporting,
    targeted,
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


def quality_steps(config: CheckConfig, coverage_json: Path) -> list[Step]:
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
    # A targeted run scopes radon and coverage/CRAP to the changed production
    # modules. When no production module changed (tests-only or non-Python leaves),
    # the radon report rails are not applicable and are skipped loudly by the
    # caller's not-applicable lines rather than run vacuous.
    if not targeted or scope.coverage_paths:
        steps += _radon_report_steps(radon_args)
    pytest = _pytest_step(
        config,
        coverage_json,
        posix_args(scope.test_paths),
        coverage_args,
    )
    if pytest is not None:
        steps.append(pytest)
    steps.append(_file_size_step(config, posix_args(scope.size_paths)))
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
    targeted = getattr(config, "targeted", False)
    failed_steps = 0
    for step in quality_steps(config, coverage_json):
        printer(step_header(step))
        printer(
            scope_reporting.fixed_step_scope_line(
                step.name,
                config.project_root,
                config.scope,
                targeted=targeted,
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
        cap = os.environ.get(memory_cap.MEMORY_CAP_ENV)
        if cap:
            printer(
                f"{step.name} may have died from the quality memory cap "
                f"(policy={memory_cap.QUALITY_MEMORY_CAP_POLICY}; "
                f"mechanism={memory_cap.RLIMIT_MECHANISM}; cap={cap} bytes; "
                "see the memory-cap line at the top of this run)"
            )
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
    if getattr(config, "targeted", False) and not config.scope.coverage_paths:
        printer(
            "not applicable: targeted run changed no production modules, so there are no "
            "changed functions for a CRAP floor to score"
        )
        printer("result: CRAP-Calculator PASS (not applicable)")
        return 0
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
            "CRAP scored zero functions; coverage scope is vacuous. Correct the "
            "coverage roots or add the measurable functions the declared package "
            "and test tree are required to contain."
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
    if (
        getattr(config, "targeted", False)
        and not coverage_json.exists()
        and not config.scope.coverage_paths
    ):
        printer(
            "not applicable: targeted run changed no production modules, so no coverage "
            "report was produced and there is no changed production line to score"
        )
        printer("result: diff-coverage PASS (not applicable)")
        return 0
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
