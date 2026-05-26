from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

from agents_remember.benchmarks.runner_modules.constants import (
    AGENTS_MD_TARGETS,
    BENCHMARK_ROOT_MARKER,
    CODEX_HARNESS_DIR,
    COPYTREE_IGNORE,
    PROVIDER_ASSET_DIRS,
    SKILL_EXPOSURE_MODES,
    SKILLS_EXPOSURE_NAMESPACE,
)
from agents_remember.install.assets import long_path, packaged_source_root


def is_ignored_package_path(relative: Path) -> bool:
    return "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}


def copy_file(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would copy {source} -> {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(long_path(source), long_path(destination))


def replace_tree(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would replace {destination} from {source}")
        return
    if destination.exists() or destination.is_symlink():
        remove_path(destination)
    shutil.copytree(long_path(source), long_path(destination), ignore=COPYTREE_IGNORE)


def copy_tree(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would copy tree {source} -> {destination}")
        return
    destination.mkdir(parents=True, exist_ok=True)
    scan_root = long_path(source)
    for child in sorted(scan_root.rglob("*")):
        copy_tree_child(child, scan_root, destination)


def copy_tree_child(child: Path, scan_root: Path, destination: Path) -> None:
    relative = child.relative_to(scan_root)
    if is_ignored_package_path(relative):
        return
    target = destination / relative
    if child.is_dir():
        target.mkdir(parents=True, exist_ok=True)
    elif child.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(long_path(child), long_path(target))


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def is_windows_directory_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return sys.platform == "win32" and path.is_dir() and (path.is_symlink() or is_junction())


def absolute_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def removable_path(path: Path, *, resolve: bool = True) -> Path:
    if sys.platform != "win32":
        return path

    resolved = path.resolve() if resolve else absolute_path(path)
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


def remove_directory_link(path: Path) -> None:
    try:
        path.rmdir()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.rmdir()


def remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    path_is_link = path.is_symlink() or getattr(path, "is_junction", lambda: False)()
    target = removable_path(path, resolve=not path_is_link)
    if is_windows_directory_link(path):
        remove_directory_link(target)
    elif path.is_dir() and not path_is_link:
        shutil.rmtree(target, onerror=remove_readonly)
    else:
        unlink_file(target)


def sync_runtime_assets(coordination_root: Path, dry_run: bool) -> None:
    with packaged_source_root() as source_root:
        if dry_run:
            print(f"Would ensure directory {coordination_root}")
        else:
            coordination_root.mkdir(parents=True, exist_ok=True)
        runtime_root = source_root / "runtime"
        replace_tree(runtime_root / "skills", coordination_root / "skills", dry_run)
        for source_rel, target_rel in AGENTS_MD_TARGETS.items():
            copy_file(source_root / source_rel, coordination_root / target_rel, dry_run)

        sync_provider_assets(source_root, coordination_root, dry_run)

    for folder in (
        "memory-repos",
        "tasks",
        "worktrees",
        "notes",
        "temp",
        "providers/data",
        "providers/logs",
        "providers/runners",
    ):
        path = coordination_root / folder
        if dry_run:
            print(f"Would ensure directory {path}")
        else:
            path.mkdir(parents=True, exist_ok=True)


def sync_provider_assets(root: Path, coordination_root: Path, dry_run: bool) -> None:
    providers_source = root / "runtime" / "providers"
    providers_target = coordination_root / "providers"
    if dry_run:
        print(f"Would replace benchmark provider assets in {providers_target}")
    else:
        remove_path(providers_target)
        providers_target.mkdir(parents=True, exist_ok=True)

    for relative in PROVIDER_ASSET_DIRS:
        source = providers_source / relative
        if source.exists():
            copy_tree(source, providers_target / relative, dry_run)


def copy_workspace_skill_exposure(
    workspace_root: Path, coordination_root: Path, dry_run: bool
) -> None:
    install_root = workspace_root / CODEX_HARNESS_DIR / "skills"
    exposure_path = install_root / SKILLS_EXPOSURE_NAMESPACE
    skills_source = coordination_root / "skills"
    if dry_run:
        print(f"Would copy benchmark skills {skills_source} -> {exposure_path}")
        return
    if not skills_source.is_dir():
        raise RuntimeError(f"benchmark runtime skills directory missing: {skills_source}")
    if exposure_path.exists() or exposure_path.is_symlink():
        remove_path(exposure_path)
    exposure_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skills_source, exposure_path, ignore=COPYTREE_IGNORE)


def sync_workspace_skill_exposure(
    workspace_root: Path,
    coordination_root: Path,
    dry_run: bool,
    mode: str = "copy",
) -> None:
    if mode not in SKILL_EXPOSURE_MODES:
        raise RuntimeError(f"unsupported skill exposure mode: {mode}")
    if mode == "none":
        print(f"Skipping benchmark skill exposure for {workspace_root}")
        return
    copy_workspace_skill_exposure(workspace_root, coordination_root, dry_run)


def write_benchmark_root_marker(root: Path, dry_run: bool) -> Path:
    marker = root / BENCHMARK_ROOT_MARKER
    if dry_run:
        print(f"Would write benchmark root marker {marker}")
        return marker
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("Codex benchmark project root.\n", encoding="utf-8")
    return marker
