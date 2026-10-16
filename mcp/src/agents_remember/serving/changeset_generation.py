"""Filesystem generations for the existing change-set readers, without polling Git.

Git still selects repositories, configuration origins and ignored directories. These
observations only decide whether a previous native read can be reused. Incomplete
observations never form a key; the caller then performs its ordinary uncached read.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.kernel.git_command import GitRunnerOptions, git_environment, run_git
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
    """Names and full file identities, including missing roots and directory changes."""
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


@dataclass
class ObjectGeneration:
    """Adding objects cannot change old content; removing/replacing one revokes proof.

    Loose objects and packs stay Git-owned. This observes their native file
    metadata and raw-byte digests, including alternate stores, and never parses an object or a pack.
    A removed/replaced object file forces the ordinary Git read and its refusal.
    """

    files: dict[Path, tuple[Identity, str]] = field(default_factory=dict)
    revision: str = ""

    @property
    def complete_revision(self) -> str:
        """Absence proofs also observe additions, including a restored missing object."""
        return hashlib.sha256(
            repr(sorted((str(path), digest) for path, (_, digest) in self.files.items())).encode()
        ).hexdigest()

    def observe(self, directories: tuple[Path, ...]) -> str:
        files: dict[Path, tuple[Identity, str]] = {}
        for root in directories:
            files[root] = (_locator_identity(root), "directory")
            for parent, _, names in os.walk(root, onerror=_raise_walk_error):
                for name in names:
                    path = Path(parent) / name
                    stamp = identity(path)
                    old = self.files.get(path)
                    digest = old[1] if old is not None and old[0] == stamp else _file_digest(path)
                    if identity(path) != stamp:
                        raise OSError(f"Git object file changed while observed: {path}")
                    files[path] = (stamp, digest)
        if not self.revision or any(
            path not in files or not _same_object(stamp, files[path])
            for path, stamp in self.files.items()
        ):
            self.revision = hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest()
        self.files = files
        return self.revision


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def configuration_identity(path: Path) -> str:
    try:
        return _file_digest(path)
    except FileNotFoundError:
        return "absent"


def _same_object(before: tuple[Identity, str], after: tuple[Identity, str]) -> bool:
    # Native capture freshens loose objects/packs with utime. Compare their bytes,
    # retaining replacement detection even when replacement content is identical.
    old, new = before[0], after[0]
    return old[:4] + old[6:10] == new[:4] + new[6:10] and before[1] == after[1]


def _raise_walk_error(error: OSError) -> None:
    raise error


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


@dataclass(frozen=True)
class WorkingManifest:
    """Native directory exclusions and the directory/configuration inputs that govern them."""

    skip: frozenset[str]
    inputs: tuple[tuple[Path, Identity | str], ...]

    def current(self) -> bool:
        return all(
            (configuration_identity(path) if isinstance(stamp, str) else identity(path)) == stamp
            for path, stamp in self.inputs
        )


def working_manifest(layout: RepositoryLayout) -> WorkingManifest:
    root = layout.root
    tracked = _git_text(root, ["ls-files", "--cached", "-z"]).split("\0")
    ancestors = {str(parent) for name in tracked if name for parent in Path(name).parents}
    ignored = _git_text(
        root, ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"]
    ).split("\0")
    candidates = [
        name.rstrip("/")
        for name in ignored
        if name.endswith("/") and name.rstrip("/") not in ancestors
    ]
    # ls-files can collapse a directory whose current *contents* are ignored.
    # Only check-ignore's proof about the directory itself permits skipping it.
    excluded = (
        run_git(
            root,
            ["check-ignore", "-z", "--stdin"],
            GitRunnerOptions(input_text="".join(name + "/\0" for name in candidates)),
        )
        if candidates
        else None
    )
    if excluded is not None and excluded.returncode not in (0, 1):
        raise OSError(excluded.stderr.strip())
    skip = frozenset(
        name.rstrip("/") for name in (excluded.stdout.split("\0") if excluded else ()) if name
    ) | {".git"}
    paths = {layout.git / "index", layout.common / "info/exclude", *layout.configuration}

    # Every potentially governing .gitignore, including one created in a previously empty dir.
    def visit(directory: Path) -> None:
        # Directory identity sees late arrivals without reading excluded contents.
        paths.add(directory)
        paths.add(directory / ".gitignore")
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    path = Path(entry.path)
                    if path.relative_to(root).as_posix() not in skip:
                        visit(path)

    visit(root)
    return WorkingManifest(
        skip,
        tuple(
            (path, configuration_identity(path) if path in layout.configuration else identity(path))
            for path in sorted(paths)
        ),
    )
