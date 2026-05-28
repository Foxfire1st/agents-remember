"""Package-local runtime install service for the MCP server."""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents_remember.install.assets import long_path, packaged_source_root
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.providers import lifecycle
from agents_remember.providers.integrity import write_provider_runner_manifest
from agents_remember.providers.settings import lifecycle_settings_from_config

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
MAX_REMOVE_ATTEMPTS = 6
PROVIDER_DEPENDENCY_PATHS = {
    Path("runners"),
}
PROVIDER_DATA_PATHS = {
    Path("data"),
}
PROVIDER_USER_DIRS = (
    "logs",
    "logs/mcp",
    "logs/providers",
    "logs/providers/codegraphcontext",
    "logs/providers/grepai",
    "logs/providers/setup",
    "logs/providers/status",
    "providers/data",
    "providers/data/codegraphcontext",
    "providers/data/grepai",
    "providers/runners",
    "providers/runners/codegraphcontext",
    "providers/runners/grepai",
)


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
    same_file = destination.exists() and filecmp.cmp(
        long_path(source),
        long_path(destination),
        shallow=False,
    )
    if same_file:
        summary.unchanged_files += 1
        return

    if not dry_run:
        shutil.copy2(long_path(source), long_path(destination))
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
        for attempt in range(MAX_REMOVE_ATTEMPTS):
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(target, onerror=remove_readonly)
                else:
                    unlink_file(target)
                break
            except PermissionError as error:
                last_error = error
                if attempt == MAX_REMOVE_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"cannot remove {path}; a provider process, editor, "
                        "or file explorer may still be using it"
                    ) from error
                time.sleep(0.5)
        if last_error is not None and (path.exists() or path.is_symlink()):
            raise RuntimeError(
                f"cannot remove {path}; a provider process, editor, "
                "or file explorer may still be using it"
            ) from last_error
    summary.removed_paths += 1


def is_path_match(relative: Path, paths: set[Path]) -> bool:
    return any(relative == path or path in relative.parents for path in paths)


def is_preserved_path(relative: Path, preserve: set[Path]) -> bool:
    return is_path_match(relative, preserve)


def is_ignored_package_path(relative: Path) -> bool:
    return (
        any(part in IGNORED_COPY_NAMES for part in relative.parts)
        or relative.suffix in IGNORED_COPY_SUFFIXES
    )


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
    destinations = sorted(
        destination_root.rglob("*"),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for destination in destinations:
        relative = destination.relative_to(destination_root)
        if is_preserved_path(relative, preserve):
            continue
        if (
            is_path_match(relative, ignore)
            or is_ignored_package_path(relative)
            or not (source_root / relative).exists()
        ):
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
    scan_root = long_path(source_root)
    for source in sorted(scan_root.rglob("*")):
        relative = source.relative_to(scan_root)
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


def install_benchmarks(
    source_root: Path,
    coordination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
) -> None:
    benchmarks_root = source_root / "benchmarks"
    require_benchmarks_tree(benchmarks_root)

    ensure_gitignore_entry(
        coordination_root / ".gitignore",
        BENCHMARKS_GITIGNORE_ENTRY,
        summary,
        dry_run,
    )

    destination_root = coordination_root / "benchmarks"
    prune_tree(
        benchmarks_root,
        destination_root,
        summary,
        dry_run,
        preserve={Path("user-runs")},
        ignore={Path("workspaces")},
    )
    copy_tree(
        benchmarks_root,
        destination_root,
        summary,
        dry_run,
        ignore=BENCHMARK_SOURCE_IGNORE_PATHS,
    )


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
    return any(
        isinstance(provider, dict) and provider.get("enabled") is True
        for provider in providers.values()
    )


def install_provider_dependencies(
    coordination_root: Path,
    settings: dict[str, Any],
    summary: InstallSummary,
    dry_run: bool,
    timeout: int,
) -> None:
    if not any_provider_enabled(settings):
        if dry_run:
            print("Would skip provider dependency install; no providers enabled")
        return

    install_provider_dependencies_from_settings(
        coordination_root,
        settings,
        summary,
        dry_run=dry_run,
        timeout=timeout,
    )


def install_provider_dependencies_from_settings(
    coordination_root: Path,
    settings: dict[str, Any],
    summary: InstallSummary,
    *,
    dry_run: bool,
    timeout: int,
) -> list[dict[str, Any]]:
    settings_path = write_temp_provider_settings(settings)
    results: list[dict[str, Any]] = []
    try:
        if configured_provider_enabled(settings, "grepai-memory"):
            summary.dependency_runs += 1
            grepai_args = SimpleNamespace(
                coordination_root=coordination_root,
                from_settings=settings_path,
                dry_run=dry_run,
                timeout=timeout,
                json=True,
                force=False,
                root=None,
                runtime_root=None,
            )
            results.append(lifecycle.grepai_install(grepai_args))
        if configured_provider_enabled(settings, "codegraphcontext-code"):
            summary.dependency_runs += 1
            cgc_args = SimpleNamespace(
                coordination_root=coordination_root,
                from_settings=settings_path,
                dry_run=dry_run,
                timeout=timeout,
                json=True,
                repo_id=None,
                code_repo_root=None,
            )
            results.append(lifecycle.cgc_install_all(cgc_args))
    finally:
        settings_path.unlink(missing_ok=True)

    failed = [result for result in results if not result.get("ok")]
    if failed:
        raise RuntimeError(f"provider dependency install failed: {json.dumps(failed, indent=2)}")
    return results


def write_temp_provider_settings(settings: dict[str, Any]) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix="-agents-remember-provider-settings.json",
    ) as handle:
        path = Path(handle.name)
        json.dump(settings, handle, indent=2)
    return path


def install_runtime(
    source_root: Path,
    coordination_root: Path,
    dry_run: bool,
    *,
    include_benchmarks: bool = False,
    install_provider_deps: bool = True,
    provider_deps_timeout: int = 1800,
    provider_settings: dict[str, Any],
) -> InstallSummary:
    runtime_root = source_root / "runtime"
    require_runtime_tree(runtime_root)

    summary = InstallSummary()
    ensure_dir(coordination_root, summary, dry_run)

    prune_tree(
        runtime_root / "skills",
        coordination_root / "skills",
        summary,
        dry_run,
        preserve={Path("AGENTS.md")},
    )
    copy_tree(runtime_root / "skills", coordination_root / "skills", summary, dry_run)
    remove_path(coordination_root / "scripts", summary, dry_run)

    # Provider runtime scaffolding is disposable during a full reinstall. A
    # dependency-skipped copy preserves live provider runner state so
    # script/docs-only updates do not interrupt Docker-owned watchers. Host
    # provider binaries and venvs are not managed runtime contracts.
    # Durable provider data and logs are user-owned coordinator state and must
    # not be removed by either install mode.
    if install_provider_deps:
        prune_tree(
            runtime_root / "providers",
            coordination_root / "providers",
            summary,
            dry_run,
            preserve=PROVIDER_DATA_PATHS,
        )
        copy_tree(runtime_root / "providers", coordination_root / "providers", summary, dry_run)
    else:
        prune_tree(
            runtime_root / "providers",
            coordination_root / "providers",
            summary,
            dry_run,
            preserve=PROVIDER_DEPENDENCY_PATHS | PROVIDER_DATA_PATHS,
        )
        copy_tree(runtime_root / "providers", coordination_root / "providers", summary, dry_run)

    for source_rel, target_rel in AGENTS_MD_TARGETS.items():
        copy_file(runtime_root / source_rel, coordination_root / target_rel, summary, dry_run)

    for user_owned in (
        "memory-repos",
        "tasks",
        "worktrees",
        "notes",
        "temp",
        *PROVIDER_USER_DIRS,
    ):
        ensure_dir(coordination_root / user_owned, summary, dry_run)

    if include_benchmarks:
        install_benchmarks(source_root, coordination_root, summary, dry_run)

    if install_provider_deps:
        install_provider_dependencies(
            coordination_root,
            provider_settings,
            summary,
            dry_run,
            provider_deps_timeout,
        )

    return summary


def install_runtime_from_config(
    config: McpRuntimeConfig,
    *,
    dry_run: bool = True,
    include_benchmarks: bool = False,
    install_provider_deps: bool = True,
    provider_deps_timeout: int | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    timeout = provider_deps_timeout or config.timeout_caps.get("providerSeconds", 1800)
    provider_settings = lifecycle_settings_from_config(config)
    if source_root is not None:
        summary = install_runtime(
            source_root.resolve(),
            config.coordination_root,
            dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
            provider_deps_timeout=timeout,
            provider_settings=provider_settings,
        )
    else:
        with packaged_source_root() as packaged_root:
            summary = install_runtime(
                packaged_root.resolve(),
                config.coordination_root,
                dry_run,
                include_benchmarks=include_benchmarks,
                install_provider_deps=install_provider_deps,
                provider_deps_timeout=timeout,
                provider_settings=provider_settings,
            )
    integrity = None
    if not dry_run:
        integrity = write_provider_runner_manifest(config)
    return {
        "ok": True,
        "operation": "runtime_install",
        "dryRun": dry_run,
        "coordinationRoot": config.coordination_root.as_posix(),
        "includeBenchmarks": include_benchmarks,
        "installProviderDeps": install_provider_deps,
        "summary": {
            "createdDirs": summary.created_dirs,
            "copiedFiles": summary.copied_files,
            "unchangedFiles": summary.unchanged_files,
            "replacedLinks": summary.replaced_links,
            "removedPaths": summary.removed_paths,
            "dependencyRuns": summary.dependency_runs,
        },
        "integrity": integrity,
    }
