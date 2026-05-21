#!/usr/bin/env python3
"""Install Agents Remember runtime assets into an ar-coordination root."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AGENTS_MD_TARGETS = {
    "agents-md-files/coordinator/AGENTS.md": "AGENTS.md",
    "agents-md-files/system/AGENTS.md": "system/AGENTS.md",
    "agents-md-files/skills/AGENTS.md": "skills/AGENTS.md",
    "agents-md-files/tasks/AGENTS.md": "tasks/AGENTS.md",
}

IGNORED_COPY_NAMES = {"__pycache__"}
IGNORED_COPY_SUFFIXES = {".pyc", ".pyo"}
BENCHMARKS_GITIGNORE_ENTRY = "benchmarks/"
BENCHMARK_SOURCE_IGNORE_PATHS = {Path("workspaces"), Path("user-runs")}
PROVIDER_DEPENDENCY_PATHS = {
    Path("_bin"),
    Path("_venvs"),
    Path("codegraphcontext"),
    Path("grepai"),
}


@dataclass
class InstallSummary:
    created_dirs: int = 0
    copied_files: int = 0
    unchanged_files: int = 0
    replaced_links: int = 0
    removed_paths: int = 0
    dependency_runs: int = 0

    def report(self) -> str:
        return (
            f"created_dirs={self.created_dirs} "
            f"copied_files={self.copied_files} "
            f"unchanged_files={self.unchanged_files} "
            f"replaced_links={self.replaced_links} "
            f"removed_paths={self.removed_paths} "
            f"dependency_runs={self.dependency_runs}"
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


def removable_path(path: Path) -> Path:
    if sys.platform != "win32":
        return path

    resolved = path.resolve()
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return resolved
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text.lstrip("\\"))
    return Path("\\\\?\\" + text)


def remove_readonly(function, path: str, exc_info) -> None:
    error = exc_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def unlink_file(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def remove_path(path: Path, summary: InstallSummary, dry_run: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not dry_run:
        target = removable_path(path)
        last_error: PermissionError | None = None
        for attempt in range(6):
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(target, onerror=remove_readonly)
                else:
                    unlink_file(target)
                break
            except PermissionError as error:
                last_error = error
                if attempt == 5:
                    raise RuntimeError(
                        f"cannot remove {path}; a provider process, editor, or file explorer may still be using it"
                    ) from error
                time.sleep(0.5)
        if last_error is not None and (path.exists() or path.is_symlink()):
            raise RuntimeError(
                f"cannot remove {path}; a provider process, editor, or file explorer may still be using it"
            ) from last_error
    summary.removed_paths += 1


def is_path_match(relative: Path, paths: set[Path]) -> bool:
    return any(relative == path or path in relative.parents for path in paths)


def is_preserved_path(relative: Path, preserve: set[Path]) -> bool:
    return is_path_match(relative, preserve)


def is_ignored_package_path(relative: Path) -> bool:
    return any(part in IGNORED_COPY_NAMES for part in relative.parts) or relative.suffix in IGNORED_COPY_SUFFIXES


def prune_tree(
    source_root: Path,
    destination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
    *,
    preserve: set[Path] | None = None,
    ignore: set[Path] | None = None,
) -> None:
    if not destination_root.exists() or destination_root.is_symlink():
        return

    preserve = preserve or set()
    ignore = ignore or set()
    for destination in sorted(destination_root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        relative = destination.relative_to(destination_root)
        if is_preserved_path(relative, preserve):
            continue
        if is_path_match(relative, ignore) or is_ignored_package_path(relative) or not (source_root / relative).exists():
            remove_path(destination, summary, dry_run)


def copy_tree(
    source_root: Path,
    destination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
    *,
    ignore: set[Path] | None = None,
) -> None:
    ensure_dir(destination_root, summary, dry_run)
    ignore = ignore or set()
    for source in sorted(source_root.rglob("*")):
        relative = source.relative_to(source_root)
        if is_path_match(relative, ignore) or is_ignored_package_path(relative):
            continue
        destination = destination_root / relative
        if source.is_dir():
            ensure_dir(destination, summary, dry_run)
        elif source.is_file():
            copy_file(source, destination, summary, dry_run)


def ensure_gitignore_entry(path: Path, entry: str, summary: InstallSummary, dry_run: bool) -> None:
    if path.exists() and path.is_dir():
        raise RuntimeError(f"cannot update .gitignore because a directory already exists: {path}")

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = {line.strip() for line in existing.splitlines()}
    if entry in entries:
        summary.unchanged_files += 1
        return

    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        separator = "" if not existing or existing.endswith(("\n", "\r\n")) else "\n"
        path.write_text(f"{existing}{separator}{entry}\n", encoding="utf-8")
    summary.copied_files += 1


def require_runtime_tree(runtime_root: Path) -> None:
    required = [
        runtime_root / "agents-md-files",
        runtime_root / "providers",
        runtime_root / "providers" / "requirements" / "codegraphcontext.txt",
        runtime_root / "providers" / "requirements" / "grepai.txt",
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

    ensure_gitignore_entry(coordination_root / ".gitignore", BENCHMARKS_GITIGNORE_ENTRY, summary, dry_run)

    destination_root = coordination_root / "benchmarks"
    prune_tree(
        benchmarks_root,
        destination_root,
        summary,
        dry_run,
        preserve={Path("user-runs")},
        ignore={Path("workspaces")},
    )
    copy_tree(benchmarks_root, destination_root, summary, dry_run, ignore=BENCHMARK_SOURCE_IGNORE_PATHS)


def load_settings(settings_path: Path) -> dict[str, Any] | None:
    if not settings_path.exists():
        return None
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"cannot read coordinator settings from {settings_path}: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"coordinator settings must be a JSON object: {settings_path}")
    return data


def configured_provider_enabled(settings: dict[str, Any], provider_id: str) -> bool:
    context_providers = settings.get("contextProviders")
    if not isinstance(context_providers, dict) or context_providers.get("enabled") is not True:
        return False

    providers = context_providers.get("providers")
    if not isinstance(providers, dict):
        return False

    provider = providers.get(provider_id)
    return isinstance(provider, dict) and provider.get("enabled") is True


def any_provider_enabled(settings: dict[str, Any]) -> bool:
    context_providers = settings.get("contextProviders")
    if not isinstance(context_providers, dict) or context_providers.get("enabled") is not True:
        return False
    providers = context_providers.get("providers")
    if not isinstance(providers, dict):
        return False
    return any(isinstance(provider, dict) and provider.get("enabled") is True for provider in providers.values())


def command_display(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def run_dependency_command(
    command: list[str],
    cwd: Path,
    summary: InstallSummary,
    dry_run: bool,
    timeout: int,
) -> None:
    summary.dependency_runs += 1
    if dry_run:
        print(f"Would run provider dependency install: {command_display(command)}")
        return

    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"provider dependency install timed out after {timeout}s: {command_display(command)}"
        ) from error

    if completed.returncode != 0:
        output = "\n".join(
            part
            for part in (
                f"stdout:\n{completed.stdout.strip()}" if completed.stdout.strip() else "",
                f"stderr:\n{completed.stderr.strip()}" if completed.stderr.strip() else "",
            )
            if part
        )
        if output:
            raise RuntimeError(
                f"provider dependency install failed: {command_display(command)}\n{output}"
            )
        raise RuntimeError(f"provider dependency install failed: {command_display(command)}")


def install_provider_dependencies(
    coordination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
    timeout: int,
) -> None:
    settings_file = coordination_root / "system" / "settings.json"
    settings = load_settings(settings_file)
    if settings is None or not any_provider_enabled(settings):
        if dry_run:
            print(f"Would skip provider dependency install; no providers enabled in {settings_file}")
        return

    script_path = coordination_root / "scripts" / "provider-setup.py"
    if not dry_run and not script_path.exists():
        raise RuntimeError(f"provider setup script is missing after install: {script_path}")

    run_dependency_command(
        [
            sys.executable,
            str(script_path),
            "install",
            "--coordination-root",
            str(coordination_root),
            "--timeout",
            str(timeout),
            "--json",
        ],
        cwd=coordination_root,
        summary=summary,
        dry_run=dry_run,
        timeout=timeout,
    )


def install_runtime(
    source_root: Path,
    coordination_root: Path,
    dry_run: bool,
    *,
    include_benchmarks: bool = False,
    install_provider_deps: bool = True,
    provider_deps_timeout: int = 1800,
) -> InstallSummary:
    runtime_root = source_root / "runtime"
    require_runtime_tree(runtime_root)

    summary = InstallSummary()
    ensure_dir(coordination_root, summary, dry_run)

    prune_tree(runtime_root / "skills", coordination_root / "skills", summary, dry_run, preserve={Path("AGENTS.md")})
    copy_tree(runtime_root / "skills", coordination_root / "skills", summary, dry_run)
    prune_tree(runtime_root / "scripts", coordination_root / "scripts", summary, dry_run)
    copy_tree(runtime_root / "scripts", coordination_root / "scripts", summary, dry_run)

    # Provider runtime scaffolding is disposable during a full reinstall. A
    # dependency-skipped copy must preserve installed binaries, venvs, and live
    # provider instance roots so script/docs-only updates do not break watchers.
    if install_provider_deps:
        remove_path(coordination_root / "providers", summary, dry_run)
        copy_tree(runtime_root / "providers", coordination_root / "providers", summary, dry_run)
    else:
        prune_tree(
            runtime_root / "providers",
            coordination_root / "providers",
            summary,
            dry_run,
            preserve=PROVIDER_DEPENDENCY_PATHS,
        )
        copy_tree(runtime_root / "providers", coordination_root / "providers", summary, dry_run)

    for source_rel, target_rel in AGENTS_MD_TARGETS.items():
        copy_file(runtime_root / source_rel, coordination_root / target_rel, summary, dry_run)

    for user_owned in ("memory-repos", "tasks", "worktrees", "notes", "temp", "provider-data"):
        ensure_dir(coordination_root / user_owned, summary, dry_run)

    if include_benchmarks:
        install_benchmarks(source_root, coordination_root, summary, dry_run)

    if install_provider_deps:
        install_provider_dependencies(coordination_root, summary, dry_run, provider_deps_timeout)

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
    parser.add_argument(
        "--skip-provider-deps",
        action="store_true",
        help="Only install runtime files; do not install enabled provider dependencies.",
    )
    parser.add_argument(
        "--provider-deps-timeout",
        type=int,
        default=1800,
        help="Seconds allowed for each provider dependency install command.",
    )
    args = parser.parse_args(argv)

    try:
        summary = install_runtime(
            args.source_root.resolve(),
            args.coordination_root.resolve(),
            args.dry_run,
            include_benchmarks=args.include_benchmarks,
            install_provider_deps=not args.skip_provider_deps,
            provider_deps_timeout=args.provider_deps_timeout,
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
