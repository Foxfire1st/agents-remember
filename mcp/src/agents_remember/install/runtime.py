"""Package-local runtime install service for the MCP server."""

from __future__ import annotations

import contextlib
import filecmp
import json
import os
import shutil
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents_remember.install.assets import long_path, packaged_source_root
from agents_remember.install.experiment import (
    INSTALLED_APPLICATION_PATH,
    WITHHELD_STARTUP_TARGETS,
    CapabilityReport,
    ExperimentError,
    ExperimentRunRecord,
    ExperimentSelection,
    InstallTargets,
    InstructionDelivery,
    application_source_root,
    build_run_record,
    decide_instruction_delivery,
    experiment_rollback_plan,
    probe_capabilities,
    resolve_experiment_selection,
    rollback_payload,
    source_identity,
)
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
APPLICATION_MACHINE_LOCAL_PATHS = frozenset(
    {Path("node_modules"), Path(".eve"), Path(".output"), Path(".vercel")}
)
"""The pinned application's machine-local trees: never copied, never pruned.

``node_modules`` is the operator's own dependency install and ``.eve``/``.output``/``.vercel`` are
eve's generated state. They ride *both* halves of the mirror rule and the distinction matters:
``copy_ignore`` keeps the source's copy out of the install, and ``preserve`` keeps a prune from
deleting the install's own — a destination-side path in ``prune_ignore`` would do the opposite and
delete exactly what this protects (caught by
``test_the_installed_application_keeps_its_machine_local_trees_out_of_the_mirror``).
"""
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

    ``experiment`` is this one run's experiment selection, and it is the *only*
    place an experiment is selected. It is not persisted, not read from a
    settings file and not written to one, so the installation carries no global
    switch an operator could leave on: the next run that does not name it gets
    the unmodified installation. ``None`` falls back to the run's environment
    (``install/experiment.py::EXPERIMENT_ENV``).
    """

    dry_run: bool = False
    include_benchmarks: bool = False
    install_provider_deps: bool = True
    no_cache: bool = False
    provider_deps_timeout: int | None = None
    source_root: Path | None = None
    experiment: str | None = None


@dataclass(frozen=True)
class ExperimentInstall:
    """One experiment install's resolved state: what it selected and what it decided.

    ``record`` is the per-run record the caller reports; ``capabilities`` is the
    probe that decided the delivery mode, kept whole so a refusal names every
    failed capability rather than the first one. ``coordination_root`` and
    ``install_roots`` are carried so the payload's rollback rows are derived from
    the roots this run actually used rather than re-parsed out of a path string.
    """

    selection: ExperimentSelection
    capabilities: CapabilityReport
    delivery: InstructionDelivery
    record: ExperimentRunRecord
    coordination_root: Path
    install_roots: tuple[Path, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "selection": self.selection.payload(),
            "delivery": self.delivery.payload(),
            "record": self.record.payload(),
            "capabilities": self.capabilities.payload(),
            "withheldStartupTargets": list(WITHHELD_STARTUP_TARGETS),
            "rollback": rollback_payload(
                experiment_rollback_plan(
                    coordination_root=self.coordination_root,
                    install_roots=self.install_roots,
                )
            ),
        }


@dataclass
class InstallSummary:
    created_dirs: int = 0
    copied_files: int = 0
    unchanged_files: int = 0
    replaced_links: int = 0
    removed_paths: int = 0
    dependency_runs: int = 0
    provider_watcher_rebind: ProviderWatcherRebindReport | None = None
    experiment: ExperimentInstall | None = None

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


def install_eve_application(
    source_root: Path,
    coordination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
) -> Path:
    """Install the pinned eve application, and only its authored surface.

    The dependency install is the operator's, and its exact one-line command is
    reported rather than guessed: this copies ``package.json``, the committed
    lockfile and ``agent/`` so the pinned versions travelled with the install,
    and deliberately neither ships nor prunes ``node_modules``.
    """

    source = application_source_root(source_root)
    destination = coordination_root / INSTALLED_APPLICATION_PATH
    sync = RuntimeTreeSync(
        source_root=source,
        destination_root=destination,
        preserve=APPLICATION_MACHINE_LOCAL_PATHS,
        copy_ignore=APPLICATION_MACHINE_LOCAL_PATHS,
    )
    prune_tree(sync, summary, dry_run)
    copy_tree(sync, summary, dry_run)
    return destination


def resolve_experiment_install(
    source_root: Path,
    coordination_root: Path,
    experiment: ExperimentSelection | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[ExperimentSelection, InstructionDelivery, CapabilityReport, ExperimentRunRecord | None]:
    """Resolve this run's selection, probe it, and decide the delivery mode.

    Every step happens **before** the install writes anything, which is the same
    rule staging learned the hard way (D20): a refusal that a retry can undo is not
    a refusal. A selected run whose capsule path is unavailable comes back as
    ``refused`` with the failed capabilities named, and the caller raises — the
    unmodified startup chain is never installed in its place.
    """

    selection = (
        experiment
        if experiment is not None
        else resolve_experiment_selection(requested="", environ=environ)
    )
    if not selection.selected:
        return (
            selection,
            decide_instruction_delivery(selection, capsule_available=False),
            CapabilityReport(capabilities=()),
            None,
        )
    report = probe_capabilities(
        source_root=source_root,
        coordination_root=coordination_root,
        environ=environ,
    )
    delivery = decide_instruction_delivery(
        selection,
        capsule_available=report.ok,
        capsule_failure="; ".join(report.remedy_lines()),
    )
    record = build_run_record(
        selection,
        delivery,
        report,
        InstallTargets(
            source_root=source_root,
            coordination_root=coordination_root,
            source=source_identity(source_root, environ),
        ),
    )
    return selection, delivery, report, record


@dataclass(frozen=True)
class RuntimeInstallScope:
    """Everything one install run needs, so both entry points stay five-argument calls.

    ``experiment`` is what separates the two: absent is the unmodified installation,
    present is the experimental one. Nothing else in the scope differs, so the two
    entry points cannot drift into two installations.
    """

    source_root: Path
    coordination_root: Path
    dry_run: bool
    provider_deps: ProviderDependencyInstall
    include_benchmarks: bool = False
    experiment: ExperimentSelection | None = None
    install_roots: tuple[Path, ...] = ()


def install_runtime(
    source_root: Path,
    coordination_root: Path,
    dry_run: bool,
    *,
    provider_deps: ProviderDependencyInstall,
    include_benchmarks: bool = False,
) -> InstallSummary:
    """The unmodified installation. No experiment is selected, so none is consulted."""

    return _install_runtime(
        RuntimeInstallScope(
            source_root=source_root,
            coordination_root=coordination_root,
            dry_run=dry_run,
            provider_deps=provider_deps,
            include_benchmarks=include_benchmarks,
        )
    )


def install_experimental_runtime(scope: RuntimeInstallScope) -> InstallSummary:
    """The experimental installation: probe first, then cut over or refuse.

    Refusing and cutting over are the only two outcomes. There is no third path in
    which a selected experiment quietly installs the unmodified startup chain: that
    decision is :func:`~agents_remember.install.experiment.decide_instruction_delivery`,
    and ``refused`` raises here before anything is written.
    """

    if scope.experiment is None:
        raise ExperimentError(
            "the experimental install needs a resolved experiment selection; an unselected run "
            "is install_runtime(), which does not consult one"
        )
    return _install_runtime(scope)


def _capsule_cutover(
    source_root: Path,
    coordination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
) -> None:
    """Withhold the legacy startup chain and install the pinned application.

    The removal is the cutover, not housekeeping: a root that already carries an
    earlier install's ``AGENTS.md`` targets would otherwise hold both instruction
    paths at once, which is the duplicate-corpus state this behaviour exists to
    prevent.
    """

    for target_rel in WITHHELD_STARTUP_TARGETS:
        remove_path(coordination_root / target_rel, summary, dry_run)
    install_eve_application(source_root, coordination_root, summary, dry_run)


def _install_assets(
    scope: RuntimeInstallScope,
    runtime_root: Path,
    summary: InstallSummary,
    delivery: InstructionDelivery,
) -> None:
    """Write the runtime assets: providers, the startup chain, user dirs, extras.

    Split out of :func:`_install_runtime` so the refusal gate and the watcher
    recovery each stay readable; the two are the parts whose order matters.
    """

    source_root = scope.source_root
    coordination_root = scope.coordination_root
    dry_run = scope.dry_run
    install_provider_deps = scope.provider_deps.enabled
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

    if delivery.mode == "capsule":
        # One delivery path. The coordinator ``skills/`` tree copied above is the authored
        # copy this root carries (what a human, the dashboard or a curator reads); it is NOT
        # the compiler's corpus -- in production the compiler reads the packaged tree
        # (``packaged_source_root()/runtime/skills`` through ``application/skill_resources``),
        # so the installed copy is neither injected nor compiled. Running the installer again
        # with no experiment selected copies the withheld startup targets back: the undo.
        _capsule_cutover(source_root, coordination_root, summary, dry_run)
    else:
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

    if scope.include_benchmarks:
        install_benchmarks(source_root, coordination_root, summary, dry_run)

    if install_provider_deps:
        install_provider_dependencies(coordination_root, scope.provider_deps, summary, dry_run)


def _install_runtime(scope: RuntimeInstallScope) -> InstallSummary:
    source_root = scope.source_root
    coordination_root = scope.coordination_root
    dry_run = scope.dry_run
    runtime_root = source_root / "runtime"
    require_runtime_tree(runtime_root)

    selection, delivery, capabilities, record = resolve_experiment_install(
        source_root, coordination_root, scope.experiment
    )
    if delivery.mode == "refused":
        raise ExperimentError(
            "the experimental installation was refused before anything was written: "
            f"{delivery.reason}\n"
            + "\n".join(f"  - {line}" for line in capabilities.remedy_lines())
            + "\n  No file under the coordination root was created, replaced or removed, and the "
            "selected mode is preserved."
        )

    install_provider_deps = scope.provider_deps.enabled
    provider_settings = scope.provider_deps.settings
    summary = InstallSummary()
    if record is not None:
        summary.experiment = ExperimentInstall(
            selection=selection,
            capabilities=capabilities,
            delivery=delivery,
            record=record,
            coordination_root=coordination_root,
            install_roots=scope.install_roots,
        )
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
            timeout=scope.provider_deps.timeout,
        )
        summary.provider_watcher_rebind = rebind.report
        stop_provider_watchers_before_refresh(rebind)

    try:
        _install_assets(scope, runtime_root, summary, delivery)
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
    # The request's own field is the primary input; AR_EXPERIMENT is the documented fallback for
    # the CLI/developer route, whose process is short-lived. Both are named in the run record's
    # ``selectionSource``. The selection is never read from a settings file and never written back
    # to one, so an install carries no global switch.
    selection = resolve_experiment_selection(requested=request.experiment)
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
    with (
        packaged_source_root()
        if request.source_root is None
        else contextlib.nullcontext(request.source_root.resolve())
    ) as source_root:
        scope = RuntimeInstallScope(
            source_root=source_root,
            coordination_root=config.coordination_root,
            dry_run=dry_run,
            provider_deps=provider_deps,
            include_benchmarks=include_benchmarks,
            experiment=selection,
        )
        # A run that selected nothing runs the unmodified entry point, so the
        # disabled path is literally the old code path rather than a branch inside it.
        summary = (
            install_experimental_runtime(scope)
            if selection.selected
            else install_runtime(
                scope.source_root,
                scope.coordination_root,
                scope.dry_run,
                provider_deps=scope.provider_deps,
                include_benchmarks=scope.include_benchmarks,
            )
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
    if summary.experiment is not None:
        payload["experiment"] = summary.experiment.payload()
    else:
        delivery = decide_instruction_delivery(selection, capsule_available=False)
        payload["experiment"] = {
            "selection": selection.payload(),
            "delivery": delivery.payload(),
            "record": None,
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
