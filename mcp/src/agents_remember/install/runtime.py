"""Package-local runtime install service for the MCP server."""

from __future__ import annotations

import contextlib
import filecmp
import os
import shlex
import shutil
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.install.assets import long_path, packaged_source_root
from agents_remember.install.effects import observe_install_effect
from agents_remember.install.experiment import (
    RETIRED_STARTUP_TARGETS,
    WITHHELD_STARTUP_TARGETS,
    CapabilityReport,
    ExperimentError,
    ExperimentRunRecord,
    ExperimentSelection,
    InstallTargets,
    InstructionDelivery,
    build_run_record,
    decide_instruction_delivery,
    probe_capabilities,
    resolve_experiment_selection,
    source_identity,
)
from agents_remember.install.mcp_settings import copy_mcp_settings
from agents_remember.install.removed_stores import removed_part_stores, removed_part_stores_report
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    agentic_settings_path,
    require_user_agentic_settings_path,
    user_agentic_settings_path,
)
from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

IGNORED_COPY_NAMES = {"__pycache__"}
IGNORED_COPY_SUFFIXES = {".pyc", ".pyo"}
BENCHMARKS_GITIGNORE_ENTRY = "benchmarks/"
BENCHMARK_SOURCE_IGNORE_PATHS = {Path("workspaces"), Path("user-runs")}
MAX_REMOVE_ATTEMPTS = 6
RUNTIME_LOG_DIRS = (
    "logs",
    "logs/mcp",
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
class RuntimeInstallRequest:
    """What one runtime install is asked to do.

    ``source_root`` stays unset for MCP callers so the source defaults to the
    packaged runtime tree.

    ``experiment`` is this one run's experiment selection, and it is the *only*
    place an experiment is selected. It is not persisted, not read from a
    settings file and not written to one, so the installation carries no global
    switch an operator could leave on: the next run that does not name it gets
    the unmodified installation. ``None`` falls back to the run's environment
    (``install/experiment.py::EXPERIMENT_ENV``).
    """

    dry_run: bool = False
    include_benchmarks: bool = False
    source_root: Path | None = None
    experiment: str | None = None
    reconcile_shared_held_stores: bool = False


@dataclass(frozen=True)
class ExperimentInstall:
    """One experiment install's resolved state: what it selected and what it decided.

    ``record`` is the per-run record the caller reports; ``capabilities`` is the
    probe that decided the delivery mode, kept whole so a refusal names every
    failed capability rather than the first one.
    """

    selection: ExperimentSelection
    capabilities: CapabilityReport
    delivery: InstructionDelivery
    record: ExperimentRunRecord

    def payload(self) -> dict[str, object]:
        return {
            "selection": self.selection.payload(),
            "delivery": self.delivery.payload(),
            "record": self.record.payload(),
            "capabilities": self.capabilities.payload(),
            "withheldStartupTargets": list(WITHHELD_STARTUP_TARGETS),
            "rollback": [],
        }


@dataclass
class InstallSummary:
    created_dirs: int = 0
    copied_files: int = 0
    unchanged_files: int = 0
    replaced_links: int = 0
    removed_paths: int = 0
    experiment: ExperimentInstall | None = None
    removed_part_stores: list[dict[str, Any]] = field(default_factory=list)

    def report(self) -> str:
        base = (
            f"created_dirs={self.created_dirs} "
            f"copied_files={self.copied_files} "
            f"unchanged_files={self.unchanged_files} "
            f"replaced_links={self.replaced_links} "
            f"removed_paths={self.removed_paths}"
        )
        stores = [item for item in self.removed_part_stores if not item["gateLog"]]
        gate_logs = [item for item in self.removed_part_stores if item["gateLog"]]
        if stores:
            names = ", ".join(f"{item['path']} ({item['bytes']} bytes)" for item in stores)
            base += f" removed_part_stores=[{names}]"
        if gate_logs:
            names = ", ".join(f"{item['path']} ({item['bytes']} bytes)" for item in gate_logs)
            base += f" stored_gate_logs=[{names}] ({STORED_GATE_WAY_OUT})"
        return base


def ensure_dir(path: Path, summary: InstallSummary, dry_run: bool) -> None:
    with observe_install_effect(path, "directory", dry_run):
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


def _close_user_settings_permissions(path: Path) -> None:
    """Leave the user's settings file read-only and its folder entry-closed (MIK-R137 rule 4).

    The file keeps mode 444 and the folder holding it keeps mode 555, so no
    process of the account can replace, remove or extend either without the
    user opening them first. The product never opens them by itself and never
    changes the file's content. Raises :class:`OSError` on a filesystem that
    cannot take the modes; the caller reports that and goes on.
    """
    os.chmod(path, 0o444)
    os.chmod(path.parent, 0o555)


def _user_settings_commands(path: Path, *, file_exists: bool) -> tuple[str, str]:
    """The user's two commands for the settings at ``path``: close, and open before an edit.

    Both name the exact folder and file as quoted shell operands, so a path under a custom
    ``XDG_CONFIG_HOME`` or one that holds a space is addressed whole. The edit command opens
    the folder with the file: an editor that saves by replacing the file makes a new entry
    in the folder. A file that does not exist is left out of both.
    """
    folder = str(path.parent)
    close = [["chmod", "444", str(path)]] if file_exists else []
    close.append(["chmod", "555", folder])
    edit = ["chmod", "u+w", folder, *([str(path)] if file_exists else [])]
    return " && ".join(shlex.join(command) for command in close), shlex.join(edit)


def user_settings_protection_report() -> dict[str, Any]:
    """Name an open settings file or folder, with the commands (MIK-R137 rule 5).

    Every ``upgrade-user-settings`` answer carries this block, so an
    installation whose file or folder is open to writing is named with the
    command that closes it and the command the user runs before an edit.
    A closed file and folder are reported as closed, not named.
    """
    path = user_agentic_settings_path()
    folder = path.parent
    try:
        file_mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() else None
    except OSError:
        file_mode = None
    try:
        folder_mode = stat.S_IMODE(folder.stat().st_mode) if folder.is_dir() else None
    except OSError:
        folder_mode = None
    file_open = file_mode is not None and bool(file_mode & 0o222)
    folder_open = folder_mode is not None and bool(folder_mode & 0o222)
    close_command, edit_command = _user_settings_commands(path, file_exists=file_mode is not None)
    return {
        "userPath": str(path),
        "fileExists": file_mode is not None,
        "fileOpen": file_open,
        "folderOpen": folder_open,
        "closeCommand": close_command,
        "editCommand": edit_command,
    }


def open_user_settings_notice() -> dict[str, Any] | None:
    """The settings file or folder a state report names as open to writing (MIK-R137 rule 5).

    ``server_info``, the report of an installation's state that every start reads, carries
    this block while the user's file or its folder is open, with the command that closes
    them and the command the user runs before an edit. Closed or absent, nothing is named.
    Only the modes are read: no mode and no byte of what is reported changes. A settings
    location that cannot be derived names nothing here; the refusal stays with the reader
    of those settings.
    """
    try:
        report = user_settings_protection_report()
    except AgenticSettingsError:
        return None
    return report if report["fileOpen"] or report["folderOpen"] else None


def upgrade_user_settings(coordination_root: Path, *, dry_run: bool) -> dict[str, Any]:
    """Move the entire old file once without overwriting an existing user file."""
    old = agentic_settings_path(coordination_root)
    new = user_agentic_settings_path()
    old_exists = old.exists() or old.is_symlink()
    new_exists = new.exists() or new.is_symlink()
    if old_exists and new_exists:
        raise AgenticSettingsError(f"agentic-settings-upgrade-collision: old: {old}; user: {new}")
    if not old_exists:
        return {
            "state": "already-current" if new_exists else "no-settings",
            "oldPath": str(old),
            "userPath": str(new),
            "dryRun": dry_run,
            "protection": user_settings_protection_report(),
            "permissionsNote": None,
        }
    if old.is_symlink() or not old.is_file():
        raise AgenticSettingsError(
            f"agentic-settings-upgrade-source-not-regular: old: {old}; user: {new}"
        )
    permissions_note: str | None = None
    if not dry_run:
        new.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also refuses a destination that appears after the check.
        # The old file remains until the complete transfer and metadata copy succeed.
        with old.open("rb") as source, new.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        shutil.copystat(old, new)
        old.unlink()
        # MIK-R137 rule 4: the installation keeps the user's file closed to
        # writing. A filesystem that cannot take the modes keeps the moved
        # file and says so in the report instead of failing the upgrade.
        try:
            _close_user_settings_permissions(new)
        except OSError as error:
            permissions_note = f"could not close the user's settings permissions: {error}"
    return {
        "state": "would-move" if dry_run else "moved",
        "oldPath": str(old),
        "userPath": str(new),
        "dryRun": dry_run,
        "protection": user_settings_protection_report(),
        "permissionsNote": permissions_note,
    }


def copy_file(source: Path, destination: Path, summary: InstallSummary, dry_run: bool) -> None:
    with observe_install_effect(destination, "copy", dry_run):
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
                    f"cannot remove {path}; another process, editor, "
                    "or file explorer may still be using it"
                ) from error
            time.sleep(0.5)


def remove_path(path: Path, summary: InstallSummary, dry_run: bool) -> None:
    with observe_install_effect(path, "remove", dry_run):
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
    with observe_install_effect(path, "gitignore", dry_run):
        if path.exists() and path.is_dir():
            raise RuntimeError(
                f"cannot update .gitignore because a directory already exists: {path}"
            )

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


def resolve_experiment_install(
    source_root: Path,
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
    report = probe_capabilities(source_root=source_root)
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
            source=source_identity(source_root, environ),
        ),
    )
    return selection, delivery, report, record


@dataclass(frozen=True)
class RuntimeInstallScope:
    """Everything one install run needs, so both entry points stay small calls.

    ``experiment`` is what separates the two: absent is the unmodified installation,
    present is the experimental one. Nothing else in the scope differs, so the two
    entry points cannot drift into two installations.
    """

    source_root: Path
    coordination_root: Path
    dry_run: bool
    include_benchmarks: bool = False
    experiment: ExperimentSelection | None = None


def install_runtime(
    source_root: Path,
    coordination_root: Path,
    dry_run: bool,
    *,
    include_benchmarks: bool = False,
) -> InstallSummary:
    """The unmodified installation. No experiment is selected, so none is consulted."""

    return _install_runtime(
        RuntimeInstallScope(
            source_root=source_root,
            coordination_root=coordination_root,
            dry_run=dry_run,
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
    coordination_root: Path,
    summary: InstallSummary,
    dry_run: bool,
) -> None:
    """Remove retired startup targets in every accepted installation.

    The existing capsule cutover also retires earlier installed AGENTS.md files
    during ordinary installs. No delivery mode restores those instruction copies.
    """

    for target_rel in RETIRED_STARTUP_TARGETS:
        remove_path(coordination_root / target_rel, summary, dry_run)


def _install_assets(
    scope: RuntimeInstallScope,
    summary: InstallSummary,
) -> None:
    """Record retired stores, retire startup files, and prepare user dirs and extras."""

    source_root = scope.source_root
    coordination_root = scope.coordination_root
    dry_run = scope.dry_run
    summary.removed_part_stores = removed_part_stores(coordination_root)

    _capsule_cutover(coordination_root, summary, dry_run)

    for user_owned in (
        "memory-repos",
        "tasks",
        "worktrees",
        "notes",
        "temp",
        *RUNTIME_LOG_DIRS,
    ):
        ensure_dir(coordination_root / user_owned, summary, dry_run)

    # Shared host/agentic settings belong to setup; this step never writes that authority.

    if scope.include_benchmarks:
        install_benchmarks(source_root, coordination_root, summary, dry_run)


def _install_runtime(scope: RuntimeInstallScope) -> InstallSummary:
    source_root = scope.source_root
    coordination_root = scope.coordination_root
    dry_run = scope.dry_run
    require_user_agentic_settings_path(coordination_root)
    runtime_root = source_root / "runtime"
    require_runtime_tree(runtime_root)

    selection, delivery, capabilities, record = resolve_experiment_install(
        source_root, scope.experiment
    )
    if delivery.mode == "refused":
        raise ExperimentError(
            "the experimental installation was refused before anything was written: "
            f"{delivery.reason}\n"
            + "\n".join(f"  - {line}" for line in capabilities.remedy_lines())
            + "\n  No file under the coordination root was created, replaced or removed, and the "
            "selected mode is preserved."
        )

    summary = InstallSummary()
    if record is not None:
        summary.experiment = ExperimentInstall(
            selection=selection,
            capabilities=capabilities,
            delivery=delivery,
            record=record,
        )
    ensure_dir(coordination_root, summary, dry_run)

    # MIK-R196 rule 7: no skill tree is installed beside the runtime. The canonical skills live
    # in the source tree and are served from the package copy by the tool server's own resource
    # surface, so a second copy in the coordination root would be a distribution surface with no
    # reader. The folder itself is not created either: nothing writes it any more.
    # The shipped tool-detail docs (MIK-R112 rule 7): the short tool
    # descriptions name docs/tool-details.md, and this copy is what an
    # installed runtime reads.
    docs_sync = RuntimeTreeSync(
        source_root=runtime_root / "docs",
        destination_root=coordination_root / "docs",
        preserve=frozenset(),
    )
    prune_tree(docs_sync, summary, dry_run)
    copy_tree(docs_sync, summary, dry_run)
    remove_path(coordination_root / "scripts", summary, dry_run)

    _install_assets(scope, summary)

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
    with (
        packaged_source_root()
        if request.source_root is None
        else contextlib.nullcontext(request.source_root.resolve())
    ) as source_root:
        scope = RuntimeInstallScope(
            source_root=source_root,
            coordination_root=config.coordination_root,
            dry_run=dry_run,
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
                include_benchmarks=scope.include_benchmarks,
            )
        )
    ensure_dir(config.workspace_root / ".agents-remember" / "role-executions", summary, dry_run)
    ensure_gitignore_entry(
        config.workspace_root / ".gitignore", ".agents-remember/role-executions/", summary, dry_run
    )
    payload: dict[str, Any] = {
        "ok": True,
        "operation": "runtime_install",
        "mcpSettings": copy_mcp_settings(config.workspace_root, dry_run=dry_run),
        "dryRun": dry_run,
        "coordinationRoot": config.coordination_root.as_posix(),
        "includeBenchmarks": include_benchmarks,
        "summary": {
            "createdDirs": summary.created_dirs,
            "copiedFiles": summary.copied_files,
            "unchangedFiles": summary.unchanged_files,
            "replacedLinks": summary.replaced_links,
            "removedPaths": summary.removed_paths,
        },
    }
    # MIK-R113 rule 5: the install names the stores of removed parts it found, once, and
    # deletes none of them.
    if summary.removed_part_stores:
        payload["removedPartStores"] = removed_part_stores_report(summary.removed_part_stores)
    if summary.experiment is not None:
        payload["experiment"] = summary.experiment.payload()
    else:
        delivery = decide_instruction_delivery(selection, capsule_available=False)
        payload["experiment"] = {
            "selection": selection.payload(),
            "delivery": delivery.payload(),
            "record": None,
        }
    return payload
