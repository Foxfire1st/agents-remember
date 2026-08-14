"""Package-local runtime install service for the MCP server."""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents_remember.install.assets import long_path, packaged_source_root
from agents_remember.install.provider_watchers import (
    ProviderWatcherRebind,
    ProviderWatcherRebindReport,
    complete_provider_watcher_rebind,
    stop_provider_watchers_before_refresh,
    write_temp_provider_settings,
)
from agents_remember.kernel.agentic_settings import (
    agentic_settings_path,
    default_agentic_settings_seed_text,
)
from agents_remember.kernel.primitives.runtime_config import (
    DEFAULT_PROVIDER_SETUP_SECONDS,
    McpRuntimeConfig,
    reload_provider_authority,
)
from agents_remember.providers import lifecycle
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


@dataclass(frozen=True)
class RuntimeTreeSync:
    """One packaged runtime tree mirrored into the coordination root.

    Ownership rules travel with the pair of roots because they are what makes
    the mirror non-destructive: ``preserve`` names destination paths a prune
    never removes (user-owned coordinator state), ``prune_ignore`` names paths
    pruned even when the packaged source still carries them, and
    ``copy_ignore`` names source paths the copy never writes.
    """

    source_root: Path
    destination_root: Path
    preserve: frozenset[Path] = frozenset()
    prune_ignore: frozenset[Path] = frozenset()
    copy_ignore: frozenset[Path] = frozenset()


@dataclass(frozen=True)
class ProviderDependencyInstall:
    """The provider-dependency step of a runtime install.

    Whether the step runs at all, the live provider settings it installs
    against, the budget each provider install gets, and whether it may reuse
    caches. The watcher rebind is derived from the same object because it is
    the same step's stop/start cycle.
    """

    settings: dict[str, Any]
    timeout: int
    enabled: bool = True
    no_cache: bool = False


@dataclass(frozen=True)
class RuntimeInstallRequest:
    """What one runtime install is asked to do.

    ``provider_deps_timeout`` and ``source_root`` stay unset for MCP callers:
    the timeout then falls back to the config's provider setup cap and the
    source to the packaged runtime tree.
    """

    dry_run: bool = False
    include_benchmarks: bool = False
    install_provider_deps: bool = True
    no_cache: bool = False
    provider_deps_timeout: int | None = None
    source_root: Path | None = None


@dataclass
class InstallSummary:
    created_dirs: int = 0
    copied_files: int = 0
    unchanged_files: int = 0
    replaced_links: int = 0
    removed_paths: int = 0
    dependency_runs: int = 0
    provider_watcher_rebind: ProviderWatcherRebindReport | None = None

    def report(self) -> str:
        return (
            f"created_dirs={self.created_dirs} "
            f"copied_files={self.copied_files} "
            f"unchanged_files={self.unchanged_files} "
            f"replaced_links={self.replaced_links} "
            f"removed_paths={self.removed_paths} "
            f"dependency_runs={self.dependency_runs}"
        )

    def provider_watcher_report(self) -> dict[str, Any] | None:
        if self.provider_watcher_rebind is None:
            return None
        return self.provider_watcher_rebind.payload()


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


def seed_agentic_settings(coordination_root: Path, summary: InstallSummary, dry_run: bool) -> None:
    """Seed the GLOBAL agentic settings file, copy-if-missing (260703-L13).

    ``<coordinationRoot>/system/settings.json`` is user-owned coordinator state
    (like ``memory-repos/``): an existing file is NEVER touched, whatever it
    contains -- the c-13 install interview and the developer own its content.
    A missing file gets the documented defaults
    (:func:`agents_remember.kernel.agentic_settings.default_agentic_settings_seed`).
    """
    target = agentic_settings_path(coordination_root)
    if target.exists():
        summary.unchanged_files += 1
        return
    ensure_dir(target.parent, summary, dry_run)
    if not dry_run:
        target.write_text(default_agentic_settings_seed_text(), encoding="utf-8")
    summary.copied_files += 1


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


def _remove_with_retry(path: Path, target: Path) -> None:
    """Remove ``target`` with retries, raising on persistent failure."""
    for attempt in range(MAX_REMOVE_ATTEMPTS):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(target, onerror=remove_readonly)
            else:
                unlink_file(target)
            return
        except PermissionError as error:
            if attempt == MAX_REMOVE_ATTEMPTS - 1:
                raise RuntimeError(
                    f"cannot remove {path}; a provider process, editor, "
                    "or file explorer may still be using it"
                ) from error
            time.sleep(0.5)


def remove_path(path: Path, summary: InstallSummary, dry_run: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not dry_run:
        _remove_with_retry(path, long_path(path))
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


def prune_tree(sync: RuntimeTreeSync, summary: InstallSummary, dry_run: bool) -> None:
    destination_root = sync.destination_root
    if not destination_root.exists() or destination_root.is_symlink():
        return

    destinations = sorted(
        destination_root.rglob("*"),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for destination in destinations:
        relative = destination.relative_to(destination_root)
        if is_preserved_path(relative, set(sync.preserve)):
            continue
        if (
            is_path_match(relative, set(sync.prune_ignore))
            or is_ignored_package_path(relative)
            or not (sync.source_root / relative).exists()
        ):
            remove_path(destination, summary, dry_run)


def copy_tree(sync: RuntimeTreeSync, summary: InstallSummary, dry_run: bool) -> None:
    ensure_dir(sync.destination_root, summary, dry_run)
    ignore = set(sync.copy_ignore)
    scan_root = long_path(sync.source_root)
    for source in sorted(scan_root.rglob("*")):
        relative = source.relative_to(scan_root)
        if is_path_match(relative, ignore) or is_ignored_package_path(relative):
            continue
        destination = sync.destination_root / relative
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

    sync = RuntimeTreeSync(
        source_root=benchmarks_root,
        destination_root=coordination_root / "benchmarks",
        preserve=frozenset({Path("user-runs")}),
        prune_ignore=frozenset({Path("workspaces")}),
        copy_ignore=frozenset(BENCHMARK_SOURCE_IGNORE_PATHS),
    )
    prune_tree(sync, summary, dry_run)
    copy_tree(sync, summary, dry_run)


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
    provider_deps: ProviderDependencyInstall,
    summary: InstallSummary,
    dry_run: bool,
) -> None:
    if not any_provider_enabled(provider_deps.settings):
        if dry_run:
            print("Would skip provider dependency install; no providers enabled")
        return

    install_provider_dependencies_from_settings(
        coordination_root,
        provider_deps,
        summary,
        dry_run=dry_run,
    )


def install_provider_dependencies_from_settings(
    coordination_root: Path,
    provider_deps: ProviderDependencyInstall,
    summary: InstallSummary,
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    settings = provider_deps.settings
    settings_path = write_temp_provider_settings(settings)
    results: list[dict[str, Any]] = []
    try:
        if configured_provider_enabled(settings, "grepai-memory"):
            summary.dependency_runs += 1
            grepai_args = SimpleNamespace(
                coordination_root=coordination_root,
                from_settings=settings_path,
                dry_run=dry_run,
                timeout=provider_deps.timeout,
                json=True,
                force=False,
                root=None,
                runtime_root=None,
                no_cache=provider_deps.no_cache,
            )
            results.append(lifecycle.grepai_install(grepai_args))
        if configured_provider_enabled(settings, "codegraphcontext-code"):
            summary.dependency_runs += 1
            cgc_args = SimpleNamespace(
                coordination_root=coordination_root,
                from_settings=settings_path,
                dry_run=dry_run,
                timeout=provider_deps.timeout,
                json=True,
                repo_id=None,
                code_repo_root=None,
                no_cache=provider_deps.no_cache,
            )
            results.append(lifecycle.cgc_install_all(cgc_args))
    finally:
        settings_path.unlink(missing_ok=True)

    failed = [result for result in results if not result.get("ok")]
    if failed:
        raise RuntimeError(f"provider dependency install failed: {json.dumps(failed, indent=2)}")
    return results


def install_runtime(
    source_root: Path,
    coordination_root: Path,
    dry_run: bool,
    *,
    provider_deps: ProviderDependencyInstall,
    include_benchmarks: bool = False,
) -> InstallSummary:
    runtime_root = source_root / "runtime"
    require_runtime_tree(runtime_root)

    install_provider_deps = provider_deps.enabled
    provider_settings = provider_deps.settings
    summary = InstallSummary()
    ensure_dir(coordination_root, summary, dry_run)

    skills_sync = RuntimeTreeSync(
        source_root=runtime_root / "skills",
        destination_root=coordination_root / "skills",
        preserve=frozenset({Path("AGENTS.md")}),
    )
    prune_tree(skills_sync, summary, dry_run)
    copy_tree(skills_sync, summary, dry_run)
    remove_path(coordination_root / "scripts", summary, dry_run)
    rebind: ProviderWatcherRebind | None = None

    if install_provider_deps and any_provider_enabled(provider_settings):
        rebind = ProviderWatcherRebind(
            coordination_root=coordination_root,
            settings=provider_settings,
            dry_run=dry_run,
            timeout=provider_deps.timeout,
        )
        summary.provider_watcher_rebind = rebind.report
        stop_provider_watchers_before_refresh(rebind)

    try:
        # Provider runtime scaffolding is disposable during a full reinstall. A
        # dependency-skipped copy preserves live provider runner state so
        # script/docs-only updates do not interrupt Docker-owned watchers. Host
        # provider binaries and venvs are not managed runtime contracts.
        # Durable provider data and logs are user-owned coordinator state and must
        # not be removed by either install mode.
        providers_sync = RuntimeTreeSync(
            source_root=runtime_root / "providers",
            destination_root=coordination_root / "providers",
            preserve=frozenset(
                PROVIDER_DATA_PATHS
                if install_provider_deps
                else PROVIDER_DEPENDENCY_PATHS | PROVIDER_DATA_PATHS
            ),
        )
        prune_tree(providers_sync, summary, dry_run)
        copy_tree(providers_sync, summary, dry_run)

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

        # The global agentic settings file rides the same user-owned posture:
        # seeded once with the documented defaults, never clobbered.
        seed_agentic_settings(coordination_root, summary, dry_run)

        if include_benchmarks:
            install_benchmarks(source_root, coordination_root, summary, dry_run)

        if install_provider_deps:
            install_provider_dependencies(coordination_root, provider_deps, summary, dry_run)
    except Exception as error:
        if rebind is not None:
            complete_provider_watcher_rebind(rebind)
            raise RuntimeError(
                "runtime install failed after provider watchers were stopped; "
                "attempted non-destructive watcher recovery. "
                f"original error: {error}; provider watcher recovery: "
                f"{json.dumps(summary.provider_watcher_report(), indent=2)}"
            ) from error
        raise

    if rebind is not None:
        complete_provider_watcher_rebind(rebind)

    return summary


def install_runtime_from_config(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
) -> dict[str, Any]:
    dry_run = request.dry_run
    include_benchmarks = request.include_benchmarks
    # Containment R1 (260707-HFX-L1): the watcher rebind's stop→start cycle is a
    # launch path — derive its settings from the LIVE on-disk authority, never the
    # boot snapshot. An empty (or unreadable: fail-closed) live map disables the
    # rebind while the runtime install itself proceeds.
    provider_deps = ProviderDependencyInstall(
        settings=lifecycle_settings_from_config(reload_provider_authority(config).apply(config)),
        timeout=request.provider_deps_timeout
        or config.timeout_caps.get("providerSetupSeconds", DEFAULT_PROVIDER_SETUP_SECONDS),
        enabled=request.install_provider_deps,
        no_cache=request.no_cache,
    )
    if request.source_root is not None:
        summary = install_runtime(
            request.source_root.resolve(),
            config.coordination_root,
            dry_run,
            provider_deps=provider_deps,
            include_benchmarks=include_benchmarks,
        )
    else:
        with packaged_source_root() as packaged_root:
            summary = install_runtime(
                packaged_root.resolve(),
                config.coordination_root,
                dry_run,
                provider_deps=provider_deps,
                include_benchmarks=include_benchmarks,
            )
    ok = summary.provider_watcher_rebind is None or summary.provider_watcher_rebind.ok is not False
    payload = {
        "ok": ok,
        "operation": "runtime_install",
        "dryRun": dry_run,
        "coordinationRoot": config.coordination_root.as_posix(),
        "includeBenchmarks": include_benchmarks,
        "installProviderDeps": request.install_provider_deps,
        "summary": {
            "createdDirs": summary.created_dirs,
            "copiedFiles": summary.copied_files,
            "unchangedFiles": summary.unchanged_files,
            "replacedLinks": summary.replaced_links,
            "removedPaths": summary.removed_paths,
            "dependencyRuns": summary.dependency_runs,
        },
    }
    provider_watcher_report = summary.provider_watcher_report()
    if provider_watcher_report is not None:
        payload["providerWatcherRebind"] = provider_watcher_report
    if summary.provider_watcher_rebind is not None:
        if summary.provider_watcher_rebind.recovery_actions:
            payload["recoveryActions"] = summary.provider_watcher_rebind.recovery_actions
        if summary.provider_watcher_rebind.messages:
            payload["messages"] = summary.provider_watcher_rebind.messages
    return payload
