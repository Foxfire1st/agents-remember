"""Filesystem and Git observations for the change-set readers.

Git computes every diff this package serves; these observations only decide
whether a previous read can be reused. MIK-R176@v2 removed the hand-rolled
walks: a commit range is keyed by its commit ids
(:meth:`RepositoryLayout.object_presence`), a working view is decided by Git's
own ``status --porcelain=v2`` (:func:`working_generation`), and the object store
is summarised by Git's own object census (:func:`count_objects`) rather than by
walking and hashing its objects.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import git_environment, run_git
from agents_remember.kernel.git_reference_files import packed_references

type Identity = tuple[int, ...]


def identity(path: Path) -> Identity:
    """Include link and target identity; ctime detects a restored mtime rewrite."""
    try:
        link = path.lstat()
    except FileNotFoundError:
        return ()
    try:
        target = path.stat()
    except FileNotFoundError:
        target = None
    return tuple(
        value
        for info in (link, target)
        if info is not None
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    )


def tree_identity(root: Path, *, skip: frozenset[str] = frozenset()) -> str:
    """Names and full file identities for a small named input, never a whole worktree.

    Used for one file or one small directory a read names directly (a worktree
    text, a repository locator, ``refs/replace``); the worktree and the object
    store have their own Git-native signals.
    """
    digest = hashlib.sha256()
    digest.update(repr(identity(root)).encode())
    if not root.is_dir():
        return digest.hexdigest()

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if relative in skip:
                    continue
                stamp = identity(path)
                digest.update(os.fsencode(relative))
                digest.update(repr(stamp).encode())
                if not stamp:
                    raise OSError(f"input disappeared during enumeration: {path}")
                if stat.S_ISDIR(stamp[2]) and not entry.is_symlink():
                    visit(path)

    visit(root)
    return digest.hexdigest()


def _git_text(root: Path, arguments: list[str]) -> str:
    result = run_git(root, arguments)
    if result.returncode:
        raise OSError(result.stderr.strip())
    return result.stdout


def _locator_identity(path: Path) -> Identity:
    stamp = identity(path)
    return stamp[:3] + stamp[6:9] if path.is_dir() else stamp


def working_generation(root: Path) -> str:
    """Git's own index-stat signal for a working view (MIK-R176 rule 5).

    One ``git status --porcelain=v2`` child answers whether anything changed: Git
    reports every staged, unstaged, renamed, deleted and untracked path from the
    index's own stat cache, and no part of this module walks the worktree. Status
    names each changed path but does not hash it, so each changed path's own
    identity is folded in as well: two edits to one file carry the same status
    letter while their counted lines differ.
    """

    result = run_git(
        root,
        [
            "-c",
            "diff.autoRefreshIndex=false",
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
        ],
    )
    if result.returncode:
        raise OSError(result.stderr.strip())
    digest = hashlib.sha256(result.stdout.encode("utf-8", "surrogateescape"))
    records = result.stdout.split("\0")
    cursor = 0
    while cursor < len(records):
        record = records[cursor]
        cursor += 1
        if record.startswith("? "):
            path = record[2:]
        elif record.startswith("1 "):
            path = record.split(" ", 8)[-1]
        elif record.startswith("u "):
            path = record.split(" ", 10)[-1]
        elif record.startswith("2 "):
            path = record.split(" ", 9)[-1]
            cursor += 1  # the source path follows its rename/copy record
        else:
            continue
        digest.update(repr((path, identity(root / path))).encode())
    return digest.hexdigest()


def count_objects(root: Path) -> tuple[int, int, tuple[str, ...]]:
    """Git's own object census for one repository (MIK-R176 rule 5).

    ``count-objects -v`` reports loose and packed object counts and any alternate
    store. It is Git's number, not our walk: no object file is read or hashed and
    no directory of the store is walked. A capture adds objects, so a caller that
    only needs to know that nothing was removed tolerates a rise; a fall revokes.
    """

    result = run_git(root, ["count-objects", "-v"])
    if result.returncode:
        raise OSError(result.stderr.strip())
    fields: dict[str, str] = {}
    alternates: list[str] = []
    for line in result.stdout.splitlines():
        key, _, value = line.partition(": ")
        if key == "alternate":
            alternates.append(value)
        else:
            fields[key] = value
    return (
        int(fields.get("count", "0")),
        int(fields.get("in-pack", "0")),
        tuple(sorted(alternates)),
    )


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def configuration_identity(path: Path) -> str:
    try:
        return _file_digest(path)
    except FileNotFoundError:
        return "absent"


@dataclass(frozen=True)
class RepositoryLayout:
    """Git's own locations, invalidated if its locator or configuration changes."""

    root: Path
    git: Path
    common: Path
    configuration: tuple[Path, ...]
    object_directories: tuple[Path, ...]
    observed: tuple[tuple[Path, Identity | str], ...]
    environment: tuple[tuple[str, str], ...]

    def current(self) -> bool:
        return self.environment == _configuration_environment() and all(
            (
                configuration_identity(path)
                if isinstance(stamp, str)
                else _locator_identity(path)
                if path == self.root / ".git"
                else identity(path)
            )
            == stamp
            for path, stamp in self.observed
        )

    def metadata(self, *, working: bool, references: tuple[str, ...] = ()) -> str:
        paths = (
            self.git / "commondir",
            self.common / "shallow",
            *((self.git / "index", self.common / "info/exclude") if working else ()),
        )
        return repr(
            (
                _locator_identity(self.root / ".git"),
                tuple((str(path), tree_identity(path)) for path in paths),
                self.references((*references, *(("HEAD",) if working else ()))),
            )
        )

    def object_presence(self, oid: str) -> str:
        """A cheap proof that an object id is still available, without a store walk (MIK-R176).

        A loose object is one file this names directly. Otherwise the pack index and
        pack files are the only files that can hold it, and their identities change
        when a pack is repacked or pruned. So a removed object revokes the answer and
        an unchanged pack set keeps it, at the cost of a handful of stats instead of
        walking and hashing every object file.
        """

        stamps: list[tuple[str, tuple[tuple[str, Identity], ...]]] = []
        for directory in self.object_directories:
            loose = directory / oid[:2] / oid[2:]
            if loose.exists():
                return repr(("loose", str(loose), identity(loose)))
            packs = directory / "pack"
            entries = (
                tuple(
                    sorted(
                        (entry.name, identity(entry))
                        for entry in packs.iterdir()
                        if entry.name.endswith((".idx", ".pack"))
                    )
                )
                if packs.is_dir()
                else ()
            )
            stamps.append((str(directory), entries))
        return repr(("packed", tuple(stamps)))

    def references(self, names: tuple[str, ...]) -> str:
        packed = packed_references(self.common)
        selected = {
            name: value for name, value in packed.items() if name.startswith("refs/replace/")
        }

        def follow(name: str, seen: frozenset[str] = frozenset()) -> None:
            if name in seen:
                raise OSError("cyclic symbolic Git reference")
            path = (self.git if name == "HEAD" else self.common) / name
            try:
                value = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                value = packed.get(name, "")
            selected[name] = value
            if value.startswith("ref: "):
                follow(value[5:], seen | {name})

        for name in names:
            for candidate in (
                (name,)
                if name == "HEAD" or name.startswith("refs/")
                else (
                    name,
                    f"refs/{name}",
                    f"refs/tags/{name}",
                    f"refs/heads/{name}",
                    f"refs/remotes/{name}",
                    f"refs/remotes/{name}/HEAD",
                )
            ):
                follow(candidate)
        return repr((sorted(selected.items()), tree_identity(self.common / "refs/replace")))


def _configuration_environment() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (key, value)
            for key, value in git_environment().items()
            if key.startswith("GIT_") or key in ("HOME", "XDG_CONFIG_HOME")
        )
    )


def _included_configurations(root: Path) -> set[Path]:
    paths: set[Path] = set()
    includes = run_git(
        root,
        [
            "config",
            "--includes",
            "--null",
            "--path",
            "--show-origin",
            "--get-regexp",
            r"(include|includeif\..*)\.path",
        ],
    )
    if includes.returncode not in (0, 1):
        raise OSError(includes.stderr.strip())
    fields = includes.stdout.split("\0")
    for origin, setting in zip(fields[::2], fields[1::2], strict=False):
        _, _, value = setting.partition("\n")
        directory = (
            (root / origin.removeprefix("file:")).parent if origin.startswith("file:") else root
        )
        paths.add((directory / value).absolute())
    return paths


def _configuration_paths(root: Path, git: Path, common: Path) -> set[Path]:
    origins = _git_text(root, ["config", "--null", "--show-origin", "--list"]).split("\0")
    configuration = {git / "config", common / "config", git / "config.worktree"}
    for origin in origins[::2]:
        if origin.startswith("file:"):
            configuration.add((root / origin.removeprefix("file:")).absolute())
    configuration.update(_included_configurations(root))
    # Absent conventional config files must invalidate the native origin list when created.
    configuration.update(
        (
            Path.home() / ".gitconfig",
            Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "git/config",
        )
    )
    xdg_git = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "git"
    configuration.update((xdg_git / "ignore", xdg_git / "attributes", common / "info/attributes"))
    for name in ("core.excludesFile", "core.attributesFile"):
        result = run_git(root, ["config", "--path", "--get", name])
        if result.returncode == 0:
            configuration.add((root / result.stdout.strip()).absolute())
    return configuration


def repository_layout(root: Path) -> RepositoryLayout:
    """Ask native Git once, retaining all config origins and external ignore/attribute files."""
    git = Path(_git_text(root, ["rev-parse", "--absolute-git-dir"]).strip())
    common = Path(
        _git_text(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
    )
    configuration = _configuration_paths(root, git, common)
    objects = (
        common / "objects",
        *tuple(
            Path(line.removeprefix("alternate: "))
            for line in _git_text(root, ["count-objects", "-v"]).splitlines()
            if line.startswith("alternate: ")
        ),
    )
    paths = (
        root / ".git",
        git / "commondir",
        git / "HEAD",  # includeIf.onbranch changes which configuration Git reads.
        *(path / "info/alternates" for path in objects),
        *sorted(configuration),
    )
    return RepositoryLayout(
        root,
        git,
        common,
        tuple(sorted(configuration)),
        objects,
        tuple(
            (
                path,
                configuration_identity(path)
                if path in configuration
                else _locator_identity(path)
                if path == root / ".git"
                else identity(path),
            )
            for path in paths
        ),
        _configuration_environment(),
    )
