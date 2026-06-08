"""Source quality wrapper for Agents Remember development."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.code_quality import crap_calculator

DEFAULT_SOURCE_PATHS = [Path("mcp/src/agents_remember")]
DEFAULT_TEST_PATHS = [Path("mcp/tests")]


@dataclass(frozen=True)
class CheckConfig:
    project_root: Path
    source_paths: list[Path]
    test_paths: list[Path]
    coverage_json: Path | None
    threshold: float
    top: int
    fail_on_crap_threshold: bool


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


def quality_commands(config: CheckConfig, coverage_json: Path) -> list[tuple[str, list[str]]]:
    source_args = [path.as_posix() for path in config.source_paths]
    test_args = [path.as_posix() for path in config.test_paths]
    return [
        ("ruff", [sys.executable, "-m", "ruff", "check", *source_args, *test_args]),
        (
            "pyright",
            [
                sys.executable,
                "-m",
                "pyright",
                "--project",
                ".",
                "--pythonpath",
                sys.executable,
                *source_args,
                *test_args,
            ],
        ),
        (
            "radon-cc",
            [
                sys.executable,
                "-m",
                "radon",
                "cc",
                *source_args,
                "-s",
                "-n",
                "B",
                "--order",
                "SCORE",
            ],
        ),
        ("radon-mi", [sys.executable, "-m", "radon", "mi", *source_args, "-s", "-n", "B"]),
        (
            "pytest",
            [
                sys.executable,
                "-m",
                "pytest",
                *test_args,
                *coverage_arguments(config.source_paths),
                f"--cov-report=json:{coverage_json.as_posix()}",
                "--cov-report=term",
            ],
        ),
    ]


def coverage_arguments(source_paths: list[Path]) -> list[str]:
    return [f"--cov={path.as_posix()}" for path in source_paths]


def source_import_roots(project_root: Path, source_paths: list[Path]) -> list[Path]:
    """Import roots for the configured source packages.

    Each source path points at a package directory (e.g. ``mcp/src/agents_remember``);
    its parent (``mcp/src``) is the directory that must be importable. Putting these on
    PYTHONPATH makes the wrapper's subprocesses import and cover *this* checkout's source
    rather than whatever an editable install resolves to, so the gate behaves identically
    from the primary clone and from any git worktree.
    """
    roots: list[Path] = []
    for source in source_paths:
        resolved = source if source.is_absolute() else project_root / source
        root = resolved.resolve().parent
        if root not in roots:
            roots.append(root)
    return roots


def subprocess_env(config: CheckConfig) -> dict[str, str]:
    """Subprocess environment with this checkout's source roots first on PYTHONPATH."""
    env = dict(os.environ)
    roots = [str(root) for root in source_import_roots(config.project_root, config.source_paths)]
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
    for name, command in quality_commands(config, coverage_json):
        printer(f"\n## {name}")
        result = runner(name, command, config.project_root, env)
        if result.return_code != 0:
            failed_steps += 1
            printer(f"{name} failed with exit code {result.return_code}")
    return failed_steps


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
            config.source_paths,
            coverage_json=coverage_json,
            project_root=project_root,
        )
    except RuntimeError as error:
        printer(str(error))
        return 1
    printer(crap_calculator.render_table(scores, project_root, config.threshold, config.top))
    over_threshold = [score for score in scores if score.crap >= config.threshold]
    if over_threshold:
        printer(f"{len(over_threshold)} function(s) meet or exceed the CRAP threshold.")
        if config.fail_on_crap_threshold:
            return 1
        printer("CRAP threshold is report-only; pass --fail-on-crap-threshold to gate on it.")
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
            "Run the Agents Remember source quality suite: Ruff, Pyright, Radon, "
            "pytest coverage, and CRAP-Calculator."
        )
    )
    parser.add_argument(
        "source_paths",
        nargs="*",
        type=Path,
        default=DEFAULT_SOURCE_PATHS,
        help="Python source paths to lint, measure, cover, and score.",
    )
    parser.add_argument(
        "--tests",
        nargs="+",
        type=Path,
        default=DEFAULT_TEST_PATHS,
        help="Pytest paths to execute.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--coverage-json",
        type=Path,
        help="Optional path for the generated Coverage.py JSON report.",
    )
    parser.add_argument("--threshold", type=float, default=crap_calculator.DEFAULT_CRAP_THRESHOLD)
    parser.add_argument("--top", type=int, default=crap_calculator.DEFAULT_TOP)
    parser.add_argument(
        "--fail-on-crap-threshold",
        action="store_true",
        help="Return a failing exit code when any function meets or exceeds the CRAP threshold.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> CheckConfig:
    return CheckConfig(
        project_root=args.project_root.resolve(),
        source_paths=args.source_paths,
        test_paths=args.tests,
        coverage_json=args.coverage_json,
        threshold=args.threshold,
        top=args.top,
        fail_on_crap_threshold=args.fail_on_crap_threshold,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_quality_check(config_from_args(args))


if __name__ == "__main__":
    sys.exit(main())
