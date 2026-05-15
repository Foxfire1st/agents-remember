#!/usr/bin/env python3
"""Install Agents Remember runtime assets into an ar-coordination root."""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


AGENTS_MD_TARGETS = {
    "agents-md-files/coordinator/AGENTS.md": "AGENTS.md",
    "agents-md-files/system/AGENTS.md": "system/AGENTS.md",
    "agents-md-files/skills/AGENTS.md": "skills/AGENTS.md",
    "agents-md-files/tasks/AGENTS.md": "tasks/AGENTS.md",
}

IGNORED_COPY_NAMES = {"__pycache__"}
IGNORED_COPY_SUFFIXES = {".pyc", ".pyo"}


@dataclass
class InstallSummary:
    created_dirs: int = 0
    copied_files: int = 0
    unchanged_files: int = 0
    replaced_links: int = 0
    removed_paths: int = 0

    def report(self) -> str:
        return (
            f"created_dirs={self.created_dirs} "
            f"copied_files={self.copied_files} "
            f"unchanged_files={self.unchanged_files} "
            f"replaced_links={self.replaced_links} "
            f"removed_paths={self.removed_paths}"
        )


def source_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_dir(path: Path, summary: InstallSummary, dry_run: bool) -> None:
    if path.is_symlink():
        if not dry_run:
            path.unlink()
            path.mkdir(parents=True, exist_ok=True)
        summary.replaced_links += 1
        summary.created_dirs += 1
        return
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"cannot create directory because a file already exists: {path}")
    if not path.exists():
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)
        summary.created_dirs += 1


def copy_file(source: Path, destination: Path, summary: InstallSummary, dry_run: bool) -> None:
    if destination.is_symlink():
        if not dry_run:
            destination.unlink()
        summary.replaced_links += 1
    elif destination.exists() and destination.is_dir():
        raise RuntimeError(f"cannot replace directory with file: {destination}")

    ensure_dir(destination.parent, summary, dry_run)
    if destination.exists() and filecmp.cmp(source, destination, shallow=False):
        summary.unchanged_files += 1
        return

    if not dry_run:
        shutil.copy2(source, destination)
    summary.copied_files += 1


def remove_path(path: Path, summary: InstallSummary, dry_run: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not dry_run:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    summary.removed_paths += 1


def is_preserved_path(relative: Path, preserve: set[Path]) -> bool:
    return any(relative == path or path in relative.parents for path in preserve)


def is_ignored_package_path(relative: Path) -> bool:
    return any(part in IGNORED_COPY_NAMES for part in relative.parts) or relative.suffix in IGNORED_COPY_SUFFIXES


def prune_tree(
    source_root: Path,
    destination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
    *,
    preserve: set[Path] | None = None,
) -> None:
    if not destination_root.exists() or destination_root.is_symlink():
        return

    preserve = preserve or set()
    for destination in sorted(destination_root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        relative = destination.relative_to(destination_root)
        if is_preserved_path(relative, preserve):
            continue
        if is_ignored_package_path(relative) or not (source_root / relative).exists():
            remove_path(destination, summary, dry_run)


def copy_tree(source_root: Path, destination_root: Path, summary: InstallSummary, dry_run: bool) -> None:
    ensure_dir(destination_root, summary, dry_run)
    for source in sorted(source_root.rglob("*")):
        relative = source.relative_to(source_root)
        if is_ignored_package_path(relative):
            continue
        destination = destination_root / relative
        if source.is_dir():
            ensure_dir(destination, summary, dry_run)
        elif source.is_file():
            copy_file(source, destination, summary, dry_run)


def require_runtime_tree(runtime_root: Path) -> None:
    required = [
        runtime_root / "agents-md-files",
        runtime_root / "skills",
        runtime_root / "scripts",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        joined = "\n".join(f"  {path}" for path in missing)
        raise RuntimeError(f"runtime source is incomplete:\n{joined}")


def require_benchmarks_tree(benchmarks_root: Path) -> None:
    required = [
        benchmarks_root / "README.md",
        benchmarks_root / "cases",
        benchmarks_root / "templates" / "workspace-AGENTS.md",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        joined = "\n".join(f"  {path}" for path in missing)
        raise RuntimeError(f"benchmark source is incomplete:\n{joined}")


def install_benchmarks(source_root: Path, coordination_root: Path, summary: InstallSummary, dry_run: bool) -> None:
    benchmarks_root = source_root / "benchmarks"
    require_benchmarks_tree(benchmarks_root)

    destination_root = coordination_root / "benchmarks"
    prune_tree(benchmarks_root, destination_root, summary, dry_run, preserve={Path("user-runs")})
    copy_tree(benchmarks_root, destination_root, summary, dry_run)


def install_runtime(
    source_root: Path,
    coordination_root: Path,
    dry_run: bool,
    *,
    include_benchmarks: bool = False,
) -> InstallSummary:
    runtime_root = source_root / "runtime"
    require_runtime_tree(runtime_root)

    summary = InstallSummary()
    ensure_dir(coordination_root, summary, dry_run)

    prune_tree(runtime_root / "skills", coordination_root / "skills", summary, dry_run, preserve={Path("AGENTS.md")})
    copy_tree(runtime_root / "skills", coordination_root / "skills", summary, dry_run)
    prune_tree(runtime_root / "scripts", coordination_root / "scripts", summary, dry_run)
    copy_tree(runtime_root / "scripts", coordination_root / "scripts", summary, dry_run)

    for source_rel, target_rel in AGENTS_MD_TARGETS.items():
        copy_file(runtime_root / source_rel, coordination_root / target_rel, summary, dry_run)

    for user_owned in ("memory-repos", "tasks", "worktrees", "notes", "temp"):
        ensure_dir(coordination_root / user_owned, summary, dry_run)

    if include_benchmarks:
        install_benchmarks(source_root, coordination_root, summary, dry_run)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coordination_root", type=Path, help="Target ar-coordination root to install or update.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=source_root_from_script(),
        help="Agents Remember checkout root. Defaults to the checkout that owns this installer.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview the install without writing files.")
    parser.add_argument(
        "--include-benchmarks",
        action="store_true",
        help="Also install the benchmark package into ar-coordination/benchmarks.",
    )
    args = parser.parse_args(argv)

    try:
        summary = install_runtime(
            args.source_root.resolve(),
            args.coordination_root.resolve(),
            args.dry_run,
            include_benchmarks=args.include_benchmarks,
        )
    except RuntimeError as error:
        parser.error(str(error))

    prefix = "Would install" if args.dry_run else "Installed"
    print(f"{prefix} Agents Remember runtime into {args.coordination_root.resolve()}")
    if args.include_benchmarks:
        print(f"{prefix} benchmark package into {(args.coordination_root / 'benchmarks').resolve()}")
    print(summary.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
