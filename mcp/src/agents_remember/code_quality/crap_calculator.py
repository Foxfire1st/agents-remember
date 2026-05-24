"""CRAP-Calculator: combine function complexity with coverage data."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IGNORED_PATH_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
DEFAULT_CRAP_THRESHOLD = 30.0
DEFAULT_TOP = 25


@dataclass(frozen=True)
class FileCoverage:
    executed_lines: frozenset[int]
    missing_lines: frozenset[int]
    has_data: bool = True

    @property
    def executable_lines(self) -> frozenset[int]:
        return self.executed_lines | self.missing_lines


@dataclass(frozen=True)
class FunctionScore:
    path: Path
    function: str
    kind: str
    start_line: int
    end_line: int
    complexity: int
    covered_lines: int
    missing_lines: int
    executable_lines: int
    coverage_ratio: float
    crap: float
    missing_coverage_data: bool = False


@dataclass(frozen=True)
class FileRollup:
    path: Path
    function_count: int
    max_crap: float
    average_crap: float
    over_threshold: int


def crap_score(complexity: int, coverage_ratio: float) -> float:
    bounded_coverage = max(0.0, min(coverage_ratio, 1.0))
    return complexity**2 * (1.0 - bounded_coverage) ** 3 + complexity


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return data


def load_coverage_by_path(coverage_json: Path, project_root: Path) -> dict[str, FileCoverage]:
    data = read_json(coverage_json)
    files = data.get("files")
    if not isinstance(files, dict):
        raise RuntimeError(f"coverage JSON is missing a files object: {coverage_json}")

    coverage_by_path: dict[str, FileCoverage] = {}
    for raw_path, raw_data in files.items():
        if not isinstance(raw_path, str) or not isinstance(raw_data, dict):
            continue
        coverage = FileCoverage(
            executed_lines=frozenset(parse_line_numbers(raw_data.get("executed_lines", []))),
            missing_lines=frozenset(parse_line_numbers(raw_data.get("missing_lines", []))),
        )
        for key in coverage_keys(Path(raw_path), project_root):
            coverage_by_path[key] = coverage
    return coverage_by_path


def parse_line_numbers(raw: object) -> set[int]:
    if not isinstance(raw, list):
        return set()
    return {int(value) for value in raw if isinstance(value, int) and value > 0}


def coverage_keys(path: Path, project_root: Path) -> set[str]:
    keys = {path.as_posix().casefold(), str(path).casefold()}
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    keys.add(resolved.as_posix().casefold())
    keys.add(str(resolved).casefold())
    try:
        relative = resolved.relative_to(project_root)
    except ValueError:
        pass
    else:
        keys.add(relative.as_posix().casefold())
        keys.add(str(relative).casefold())
    return keys


def iter_python_files(paths: list[Path], project_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        resolved = path if path.is_absolute() else project_root / path
        if resolved.is_file() and resolved.suffix == ".py":
            files.append(resolved.resolve())
        elif resolved.is_dir():
            files.extend(
                child.resolve()
                for child in sorted(resolved.rglob("*.py"))
                if not has_ignored_part(child)
            )
    return sorted(dict.fromkeys(files))


def has_ignored_part(path: Path) -> bool:
    return any(part in IGNORED_PATH_PARTS for part in path.parts)


def analyze_file(
    path: Path,
    coverage_by_path: dict[str, FileCoverage],
    project_root: Path,
) -> list[FunctionScore]:
    coverage = find_file_coverage(path, coverage_by_path, project_root)
    scores: list[FunctionScore] = []
    for block in complexity_blocks(path):
        if not is_function_block(block):
            continue
        scores.append(score_block(path, block, coverage))
    return scores


def find_file_coverage(
    path: Path,
    coverage_by_path: dict[str, FileCoverage],
    project_root: Path,
) -> FileCoverage:
    for key in coverage_keys(path, project_root):
        coverage = coverage_by_path.get(key)
        if coverage is not None:
            return coverage
    return FileCoverage(frozenset(), frozenset(), has_data=False)


def complexity_blocks(path: Path) -> list[Any]:
    try:
        radon_complexity = importlib.import_module("radon.complexity")
    except ImportError as error:
        raise RuntimeError(
            "CRAP-Calculator requires radon. Install the development requirements first."
        ) from error
    return list(radon_complexity.cc_visit(path.read_text(encoding="utf-8")))


def is_function_block(block: Any) -> bool:
    return hasattr(block, "is_method")


def score_block(path: Path, block: Any, coverage: FileCoverage) -> FunctionScore:
    span = set(range(int(block.lineno), int(block.endline) + 1))
    executable = coverage.executable_lines & span
    covered = coverage.executed_lines & span
    missing = coverage.missing_lines & span
    coverage_ratio = coverage_ratio_for_function(covered, executable, coverage.has_data)
    complexity = int(block.complexity)
    return FunctionScore(
        path=path,
        function=str(getattr(block, "fullname", block.name)),
        kind="method" if bool(block.is_method) else "function",
        start_line=int(block.lineno),
        end_line=int(block.endline),
        complexity=complexity,
        covered_lines=len(covered),
        missing_lines=len(missing),
        executable_lines=len(executable),
        coverage_ratio=coverage_ratio,
        crap=crap_score(complexity, coverage_ratio),
        missing_coverage_data=not coverage.has_data,
    )


def coverage_ratio_for_function(
    covered: set[int] | frozenset[int],
    executable: set[int] | frozenset[int],
    has_data: bool,
) -> float:
    if not has_data:
        return 0.0
    if not executable:
        return 1.0
    return len(covered) / len(executable)


def calculate_scores(
    paths: list[Path],
    *,
    coverage_json: Path,
    project_root: Path,
) -> list[FunctionScore]:
    resolved_root = project_root.resolve()
    coverage = load_coverage_by_path(coverage_json, resolved_root)
    scores: list[FunctionScore] = []
    for path in iter_python_files(paths, resolved_root):
        scores.extend(analyze_file(path, coverage, resolved_root))
    return sorted(scores, key=lambda score: (-score.crap, -score.complexity, score.path.as_posix()))


def rollup_by_file(scores: list[FunctionScore], threshold: float) -> list[FileRollup]:
    grouped: dict[Path, list[FunctionScore]] = {}
    for score in scores:
        grouped.setdefault(score.path, []).append(score)
    rollups = [
        FileRollup(
            path=path,
            function_count=len(file_scores),
            max_crap=max(score.crap for score in file_scores),
            average_crap=sum(score.crap for score in file_scores) / len(file_scores),
            over_threshold=sum(1 for score in file_scores if score.crap >= threshold),
        )
        for path, file_scores in grouped.items()
    ]
    return sorted(rollups, key=lambda item: (-item.max_crap, item.path.as_posix()))


def score_to_mapping(score: FunctionScore, project_root: Path) -> dict[str, Any]:
    return {
        "path": display_path(score.path, project_root),
        "function": score.function,
        "kind": score.kind,
        "startLine": score.start_line,
        "endLine": score.end_line,
        "complexity": score.complexity,
        "coverageRatio": round(score.coverage_ratio, 4),
        "coveredLines": score.covered_lines,
        "missingLines": score.missing_lines,
        "executableLines": score.executable_lines,
        "crap": round(score.crap, 2),
        "missingCoverageData": score.missing_coverage_data,
    }


def rollup_to_mapping(rollup: FileRollup, project_root: Path) -> dict[str, Any]:
    return {
        "path": display_path(rollup.path, project_root),
        "functionCount": rollup.function_count,
        "maxCrap": round(rollup.max_crap, 2),
        "averageCrap": round(rollup.average_crap, 2),
        "overThreshold": rollup.over_threshold,
    }


def display_path(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def render_table(scores: list[FunctionScore], project_root: Path, threshold: float, top: int) -> str:
    selected = scores[:top] if top > 0 else scores
    rollups = rollup_by_file(scores, threshold)
    lines = [
        "# CRAP-Calculator",
        "",
        f"Threshold: {threshold:.1f}",
        "",
        "## Function Scores",
        "",
        "| CRAP | CC | Coverage | Exec Lines | Function | Location |",
        "| ---: | ---: | ---: | ---: | --- | --- |",
    ]
    lines.extend(function_row(score, project_root) for score in selected)
    lines.extend(
        [
            "",
            "## File Rollup",
            "",
            "| Max CRAP | Avg CRAP | Functions | Over Threshold | File |",
            "| ---: | ---: | ---: | ---: | --- |",
        ]
    )
    lines.extend(rollup_row(rollup, project_root) for rollup in rollups)
    return "\n".join(lines)


def function_row(score: FunctionScore, project_root: Path) -> str:
    coverage = f"{score.coverage_ratio * 100:.1f}%"
    location = f"{display_path(score.path, project_root)}:{score.start_line}"
    return (
        f"| {score.crap:.2f} | {score.complexity} | {coverage} | "
        f"{score.executable_lines} | `{score.function}` | `{location}` |"
    )


def rollup_row(rollup: FileRollup, project_root: Path) -> str:
    return (
        f"| {rollup.max_crap:.2f} | {rollup.average_crap:.2f} | "
        f"{rollup.function_count} | {rollup.over_threshold} | "
        f"`{display_path(rollup.path, project_root)}` |"
    )


def render_json(scores: list[FunctionScore], project_root: Path, threshold: float) -> str:
    payload = {
        "tool": "CRAP-Calculator",
        "threshold": threshold,
        "functions": [score_to_mapping(score, project_root) for score in scores],
        "files": [
            rollup_to_mapping(rollup, project_root)
            for rollup in rollup_by_file(scores, threshold)
        ],
    }
    return json.dumps(payload, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "CRAP-Calculator: combine Radon cyclomatic complexity with coverage JSON "
            "to report function-level CRAP scores."
        )
    )
    parser.add_argument("paths", nargs="*", type=Path, default=[Path(".")])
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--threshold", type=float, default=DEFAULT_CRAP_THRESHOLD)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project_root = args.project_root.resolve()
        scores = calculate_scores(
            args.paths,
            coverage_json=args.coverage_json.resolve(),
            project_root=project_root,
        )
        if args.format == "json":
            print(render_json(scores, project_root, args.threshold))
        else:
            print(render_table(scores, project_root, args.threshold, args.top))
    except RuntimeError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
