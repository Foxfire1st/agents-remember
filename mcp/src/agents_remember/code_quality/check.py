"""Source quality wrapper for Agents Remember development.

Scope is not written down in this module. ``derive_scope`` reads it off the tree --
``git ls-files '*.py'`` for what gets linted and type-checked, the tracked top-level
packages for what gets covered and CRAP-scored, and pytest's own ``testpaths`` for
where the suite lives -- so a newly tracked Python file is gated the moment it is
added rather than whenever somebody remembers to extend a constant.
``mcp/tests/test_gate_scope.py`` proves the derivation reaches every tracked path.

Every step here is either enforcing or explicitly labelled a report. Radon is the
report: ``radon cc`` and ``radon mi`` exit 0 whatever they find, so no finding of
theirs has ever been able to fail this gate, and the wrapper now says so instead of
listing them alongside the checks that can. Radon stays load-bearing all the same --
``crap_calculator`` imports ``radon.complexity`` for the complexity half of CRAP.

Four steps enforce. ``ruff`` lints -- including the four complexity rules
``C901``/``PLR0911``/``PLR0912``/``PLR0915``, which it reports like every other finding --
``ruff-format`` fails on any file the formatter would rewrite, ``pyright`` types, and
``pytest`` runs the suite under coverage. Two more score that one coverage report
afterwards rather than measuring anything again: CRAP, and the changed-lines coverage
floor in ``diff_coverage``. The coverage gate is on the diff and not on the aggregate
because an aggregate over 88k lines of tests is substantially satisfied by import-time
execution and cannot move far enough to see a single change; ``diff_coverage``'s module
docstring carries the measurements.

Nothing in this gate is exempt. There is no baseline, ratchet, allowlist or grandfather
file for complexity, and none for CRAP either. A fifth ``complexity-baseline`` step used
to sit beside ``ruff`` holding the four complexity rules against
``quality/complexity-baseline.txt``, and ``ruff`` was handed ``--extend-ignore`` for
exactly those codes so the two steps could not double-report the same function. Both are
gone: the 67 recorded offenders were refactored rather than scheduled, so ``ruff``
enforces the rules directly and the only way past a finding is to fix the function.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality import crap_calculator, diff_coverage
from agents_remember.kernel import git_command

RADON_REPORT_NOTE = (
    "report only: radon exits 0 whatever it finds, so nothing below can fail the gate"
)


class ScopeError(RuntimeError):
    """The gate could not work out what it is supposed to certify."""


@dataclass(frozen=True)
class GateScope:
    """What each rail of the gate certifies, derived from the tree.

    ``lint_paths`` and ``type_paths`` are every tracked Python file, so neither rail
    can fall behind the repository. ``coverage_paths`` are the tracked top-level
    importable packages -- what ``--cov`` measures and what CRAP scores. ``test_paths``
    come from ``[tool.pytest.ini_options] testpaths``, the one place that declares
    where the suite lives.
    """

    lint_paths: list[Path]
    type_paths: list[Path]
    coverage_paths: list[Path]
    test_paths: list[Path]


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


# --- scope -------------------------------------------------------------------


def git_ls_files(project_root: Path, *patterns: str) -> list[Path]:
    """Tracked paths matching ``patterns``, relative to ``project_root``.

    A failure here is fatal rather than an empty list: an empty scope would make every
    step of the gate pass by certifying nothing.
    """
    arguments = ["ls-files", "-z", "--", *patterns]
    # On the package's one git runner, which strips GIT_DIR and friends. This gate runs
    # from the pre-push hook and git exports GIT_DIR to its hooks, so an unstripped
    # `ls-files` here would derive the whole gate's scope from another repository.
    failed = f"could not list tracked files (git {' '.join(arguments)})"
    try:
        completed = git_command.run_git(project_root, arguments)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScopeError(f"{failed}: {error}") from error
    if completed.returncode != 0:
        raise ScopeError(f"{failed}: exit {completed.returncode}: {completed.stderr.strip()}")
    return [Path(entry) for entry in completed.stdout.split("\0") if entry]


def top_level_packages(tracked: list[Path]) -> list[Path]:
    """Tracked top-level importable packages.

    A directory holding ``__init__.py`` is a package; it is top-level when its parent
    is not itself a package. Their parents are the import roots the subprocesses need
    on PYTHONPATH, and the packages themselves are what coverage measures.
    """
    packages = {path.parent for path in tracked if path.name == "__init__.py"}
    return sorted(
        package
        for package in packages
        if package.parent == package or package.parent not in packages
    )


def toml_section(data: Mapping[str, object], keys: tuple[str, ...]) -> Mapping[str, object]:
    current: object = data
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, Mapping) else {}


def pytest_testpaths(project_root: Path) -> list[Path]:
    """Where the suite lives, read from pytest's own declaration.

    ``[tool.pytest.ini_options] testpaths`` is the single place that says which
    directories hold tests. Reading it keeps the wrapper from carrying a second copy
    that can drift out of step with it. A missing or empty declaration raises rather
    than defaulting: a gate that quietly runs no tests is exactly the failure this
    module exists to make impossible.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ScopeError(f"no pyproject.toml at {pyproject}; the gate cannot derive its scope")
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    testpaths = toml_section(data, ("tool", "pytest", "ini_options")).get("testpaths")
    if not isinstance(testpaths, list) or not testpaths:
        raise ScopeError(
            "[tool.pytest.ini_options] testpaths is missing or empty in "
            f"{pyproject}; the gate refuses to guess where the suite lives"
        )
    return [Path(str(entry)) for entry in testpaths]


def derive_scope(project_root: Path) -> GateScope:
    """The gate's scope, read off the tree rather than written down here.

    ``git ls-files`` reads the *index*, so a file is in scope the moment it is
    ``git add``-ed -- which is exactly the content the pre-commit tier certifies -- and a
    file that has never been added is not part of the tree yet. Nothing else can narrow
    this: the wrapper takes no path arguments.
    """
    tracked = git_ls_files(project_root, "*.py")
    if not tracked:
        raise ScopeError(f"git tracks no Python files under {project_root}")
    coverage_paths = top_level_packages(tracked)
    if not coverage_paths:
        raise ScopeError(
            "no tracked top-level Python package (a directory holding __init__.py) under "
            f"{project_root}; coverage and CRAP would have nothing to measure"
        )
    return GateScope(
        lint_paths=tracked,
        type_paths=tracked,
        coverage_paths=coverage_paths,
        test_paths=pytest_testpaths(project_root),
    )


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
    return 1 if failed_steps else 0


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
        result = runner(step.name, step.command, config.project_root, env)
        if result.return_code == 0:
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
        return f"{step.name} failed with exit code {return_code}"
    return (
        f"{step.name} could not run (exit code {return_code}). This is a report step, "
        "so this is the tool breaking rather than a finding -- fix the tool."
    )


def run_crap_calculator(
    config: CheckConfig,
    coverage_json: Path,
    project_root: Path,
    *,
    printer: Printer,
) -> int:
    printer("\n## CRAP-Calculator")
    if not coverage_json.exists():
        printer(f"coverage JSON was not created: {coverage_json}")
        return 1
    try:
        scores = crap_calculator.calculate_scores(
            config.scope.coverage_paths,
            coverage_json=coverage_json,
            project_root=project_root,
        )
    except RuntimeError as error:
        printer(str(error))
        return 1
    printer(crap_calculator.render_table(scores, project_root, config.threshold, config.top))
    over_threshold = [score for score in scores if score.crap >= config.threshold]
    if not over_threshold:
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
        printer(f"coverage JSON was not created: {coverage_json}")
        return 1
    try:
        base = diff_coverage.resolve_base(project_root, explicit_base=config.diff_base)
        result = diff_coverage.measure(project_root, coverage_json, base)
    except (RuntimeError, OSError) as error:
        printer(str(error))
        return 1
    for line in diff_coverage.render(result, config.diff_floor):
        printer(line)
    if result.state == "measured" and result.percent < config.diff_floor:
        return 1
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
            "is derived from the tree, not from a flag: there is no way to narrow what "
            "the gate certifies, and no baseline, allowlist or exemption file anywhere "
            "in it can excuse a finding."
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
        return 1
    return run_quality_check(config)


if __name__ == "__main__":
    sys.exit(main())
