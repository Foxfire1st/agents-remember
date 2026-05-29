"""Detect and remove stale or out-of-layout CGC runtime artifacts."""

from __future__ import annotations

from pathlib import Path

from agents_remember.providers.cgc.context.constants import SOURCE_ARTIFACT_NAMES
from agents_remember.providers.cgc.context.core import CgcRuntimeLayout
from agents_remember.providers.context.common import ContextProviderError, remove_runtime_path


def source_provider_artifacts(code_repo_root: Path) -> list[Path]:
    """Return provider-created artifacts that should not exist in source repos."""

    root = code_repo_root.resolve()
    return [root / name for name in SOURCE_ARTIFACT_NAMES if (root / name).exists()]


def assert_no_source_provider_artifacts(code_repo_root: Path) -> None:
    artifacts = source_provider_artifacts(code_repo_root)
    if artifacts:
        rendered = ", ".join(path.name for path in artifacts)
        raise ContextProviderError(f"provider artifacts found in source repo: {rendered}")


def cleanup_cgc_runtime_artifacts(
    layouts: list[CgcRuntimeLayout], *, dry_run: bool = False
) -> list[dict[str, str]]:
    """Remove stale CGC runtime artifacts that are outside the desired layout."""

    if not layouts:
        return []

    provider_root = layouts[0].runtime_root.parent.resolve()
    configured_roots = {layout.runtime_root.resolve() for layout in layouts}
    removals: list[dict[str, str]] = []
    removals.extend(
        _unconfigured_cgc_runtime_removals(
            provider_root,
            configured_roots,
            dry_run=dry_run,
        )
    )
    removals.extend(_obsolete_cgc_runtime_removals(layouts, dry_run=dry_run))
    return removals


def _unconfigured_cgc_runtime_removals(
    provider_root: Path,
    configured_roots: set[Path],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    removals: list[dict[str, str]] = []
    for child in sorted(provider_root.iterdir()) if provider_root.exists() else []:
        if not _should_remove_cgc_runtime_child(child, configured_roots):
            continue
        _assert_cgc_child_under_provider_root(child, provider_root)
        removals.append({"path": child.as_posix(), "reason": "unconfigured-cgc-instance"})
        remove_runtime_path(child, dry_run)
    return removals


def _should_remove_cgc_runtime_child(child: Path, configured_roots: set[Path]) -> bool:
    if not child.is_dir() or child.resolve() in configured_roots:
        return False
    return (
        (child / ".codegraphcontext").exists()
        or (child / "provider-state.json").exists()
        or child.name.startswith("_")
    )


def _assert_cgc_child_under_provider_root(child: Path, provider_root: Path) -> None:
    if not child.resolve().is_relative_to(provider_root):
        raise ContextProviderError(f"refusing to remove CGC path outside provider root: {child}")


def _obsolete_cgc_runtime_removals(
    layouts: list[CgcRuntimeLayout],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    removals: list[dict[str, str]] = []
    for layout in layouts:
        cgc_root = layout.cgc_root.resolve()
        for name in ("db", "global", "kuzu", "kuzu.wal"):
            target = (layout.cgc_root / name).resolve()
            if not target.exists():
                continue
            if not target.is_relative_to(cgc_root):
                raise ContextProviderError(
                    f"refusing to remove CGC path outside instance root: {target}"
                )
            removals.append({"path": target.as_posix(), "reason": f"legacy-embedded-{name}"})
            remove_runtime_path(target, dry_run)
    return removals
