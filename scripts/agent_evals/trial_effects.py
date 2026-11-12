"""Capture effects from the declared native repositories and their exact aliases."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from agents_remember.errors import AuthorityError

from .artifacts import EvidenceFile, child_path
from .fixtures import CaseBundle
from .trial_isolation import CREDENTIAL_NAMES
from .trial_setup import ProvisionedTrial


def _effect_bindings(provisioned: ProvisionedTrial) -> tuple[tuple[Path, Path, str], ...]:
    """Bind only the declared native topology; never follow actor-created aliases."""
    root = provisioned.project_root
    official = root / "coordination/memory-repos/ar-code"
    leaf = provisioned.native_setup.get("leaf")
    if leaf is None:
        code, memory = root / "projects/code", official
    else:
        group = root / "coordination/worktrees/code" / (leaf["leafId"].lower() + "-ar")
        if Path(leaf["worktreeGroup"]) != group:
            raise AuthorityError("effect capture native leaf group differs from its binding")
        code, memory = group / "code", group / "memory"
        if Path(leaf["officialMemoryRoot"]) != official:
            raise AuthorityError("effect capture official memory root differs from its binding")
        _physical_repositories(root, (official,))
    if provisioned.code_root != code or provisioned.memory_root != memory:
        raise AuthorityError("effect capture repositories differ from the declared native roots")
    _physical_repositories(root, (code, memory))
    bindings = (
        (root / "projects/code", code, "projects/code"),
        (root / "projects/memory", memory, "projects/memory"),
    )
    if leaf is None:
        bindings += ((official, memory, "projects/memory"),)
    _native_aliases(root, bindings)
    return bindings


def _physical_repositories(root: Path, repositories: tuple[Path, ...]) -> None:
    for physical in repositories:
        if not physical.is_dir():
            raise AuthorityError(f"effect capture repository absent: {physical}")
        for ancestor in (physical, *physical.parents):
            if ancestor.is_symlink():
                raise AuthorityError(f"unexpected link in effect capture: {ancestor}")
            if ancestor == root:
                break


def _native_aliases(root: Path, bindings: tuple[tuple[Path, Path, str], ...]) -> None:
    aliases = {alias for alias, _, _ in bindings}
    for alias, physical, _ in bindings:
        for ancestor in alias.parents:
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise AuthorityError(f"unexpected link in effect capture: {ancestor}")
        if alias == physical:
            continue
        if not alias.is_symlink() or alias.resolve(strict=True) != physical:
            raise AuthorityError(f"effect capture requires its exact native alias: {alias}")
        # A declared alias may point through another declared alias, never an arbitrary link.
        immediate = Path(os.path.abspath(alias.parent / alias.readlink()))
        if immediate != physical and immediate not in aliases:
            raise AuthorityError(f"unexpected alias chain in effect capture: {alias}")


def _walk_error(error: OSError) -> None:
    raise error


def _effect_files(root: Path, omitted: tuple[Path, ...]):
    if root.is_symlink():
        raise AuthorityError(f"unexpected link in effect capture: {root}")
    if not root.exists():
        return
    for base, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        directory = Path(base)
        for name in list(directories):
            path = directory / name
            if path in omitted:
                directories.remove(name)
            elif path.is_symlink():
                raise AuthorityError(f"unexpected link in effect capture: {path}")
        for name in files:
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise AuthorityError(f"unexpected link or special file in effect capture: {path}")
            yield path


def _effect_root(root: Path, name: str, bindings: tuple[tuple[Path, Path, str], ...]) -> Path:
    source_root = child_path(root, name)
    aliases = {alias for alias, _, _ in bindings}
    for ancestor in (source_root, *source_root.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink() and ancestor not in aliases:
            raise AuthorityError(f"unexpected link in effect capture: {ancestor}")
    return source_root


def _effect_sources(
    root: Path, source_root: Path, bindings: tuple[tuple[Path, Path, str], ...]
) -> dict[str, Path]:
    for alias, physical, prefix in bindings:
        for bound in (alias, physical):
            if source_root.is_relative_to(bound):
                selected = physical / source_root.relative_to(bound)
                return {
                    prefix + "/" + source.relative_to(physical).as_posix(): source
                    for source in _effect_files(selected, ())
                }
    omitted = tuple({path for alias, physical, _ in bindings for path in (alias, physical)})
    sources = {
        source.relative_to(root).as_posix(): source
        for source in _effect_files(source_root, omitted)
    }
    for alias, physical, prefix in bindings:
        if alias.is_relative_to(source_root) or physical.is_relative_to(source_root):
            for source in _effect_files(physical, ()):
                sources[prefix + "/" + source.relative_to(physical).as_posix()] = source
    return sources


def save_effects(
    provisioned: ProvisionedTrial, case: CaseBundle, destination: Path
) -> list[dict[str, str]]:
    root = provisioned.project_root
    bindings = _effect_bindings(provisioned)
    sources = {}
    for name in case.manifest["effectRoots"]:
        source_root = _effect_root(root, name, bindings)
        sources.update(_effect_sources(root, source_root, bindings))
    for relative in sorted(sources):
        if Path(relative).name in CREDENTIAL_NAMES:
            raise AuthorityError(f"credential-named evidence refused before reading: {relative}")
    inventory = []
    for relative, source in sorted(sources.items()):
        item = EvidenceFile.capture(root, source.relative_to(root).as_posix())
        item.verify(root)
        target = child_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        inventory.append({"path": relative, "sha256": item.sha256})
    _effect_bindings(provisioned)
    return inventory
