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
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import git_environment, run_git
from agents_remember.kernel.git_reference_files import packed_references
from agents_remember.kernel.read_cancellation import raise_if_read_cancelled
from agents_remember.kernel.witnessed_memo import WitnessedMemo

type Identity = tuple[int, ...]

# A full object ID names exactly one loose file, ``<2 hex>/<rest>``; any other spelling is stat'ed
# as given.
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


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


def _fanout_names(fanout: Path) -> frozenset[str] | None:
    """The names a loose-object fan-out directory holds; ``None`` when it cannot be listed.

    A missing directory holds nothing (a stat inside it finds no file either). Any other failure
    answers ``None``, so the caller stats each name as before and meets the same error there.
    """
    try:
        return frozenset(os.listdir(fanout))
    except FileNotFoundError:
        return frozenset()
    except OSError:
        return None


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
    no fan-out directory is listed. A capture adds objects, so a caller that
    only needs to know that nothing was removed tolerates a rise; a fall revokes.

    The census runs only when :func:`_census_witness` differs from the one its last
    answer was taken under; an unchanged witness answers what Git said then. The witness
    stats the store's directories and never counts or reads an object itself.
    """

    count, in_pack, alternates = _census(root)
    return count, in_pack, tuple(sorted(alternates))


def _census(root: Path) -> tuple[int, int, tuple[str, ...]]:
    """Loose and packed counts and the alternates in Git's order, from the gated census."""

    before = _census_witness(root)
    kept = _censuses.get(root, before) if before is not None else None
    if kept is not None:
        return kept[0]
    census = _count_objects(root)
    if before is not None and _census_witness(root) == before:
        _censuses.put(root, before, census)
    return census


#: Git's last census per repository root (alternates in Git's order), with the store witness
#: it was taken under. One entry per repository or worktree root the dashboard reads (a few
#: dozen); an evicted root simply runs the census again.
_censuses: WitnessedMemo[Path, tuple[object, ...], tuple[int, int, tuple[str, ...]]] = (
    WitnessedMemo(1024)
)


#: How many closure objects are observed between two looks at the read's cancellation (D585).
_CHECK_EVERY = 256


def _census_witness(root: Path) -> tuple[object, ...] | None:
    """Every file identity ``count-objects -v`` answers from, or ``None`` when unprovable.

    Git finds the store from ``root/.git`` (a directory, or a ``gitdir:`` file) and the
    administrative directory's ``commondir``; the census counts the names in the loose
    fan-out directories, the entries of ``objects/pack`` and the alternates listed by
    ``objects/info/alternates`` (followed through each alternate's own list). A directory's
    identity moves when a name is added or removed, so a new loose object or pack moves the
    witness; the configuration files can make Git refuse, so they are part of it too. Any
    layout this cannot read plainly answers ``None``, and the census runs every time.
    """

    raise_if_read_cancelled()  # several hundred stats of the store: a read that left stops first
    try:
        dotgit = root / ".git"
        parts: list[object] = [_configuration_environment(), identity(dotgit)]
        if dotgit.is_dir():
            gitdir = dotgit
        elif dotgit.is_file():
            text = dotgit.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir: "):
                return None
            gitdir = root / text[len("gitdir: ") :]
        else:
            return None
        commondir = gitdir / "commondir"
        parts.append(identity(commondir))
        common = (
            gitdir / commondir.read_text(encoding="utf-8").strip()
            if commondir.is_file()
            else gitdir
        )
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        for configuration in (
            common / "config",
            gitdir / "config.worktree",
            Path.home() / ".gitconfig",
            xdg / "git/config",
            Path("/etc/gitconfig"),
        ):
            parts.append(identity(configuration))
        objects = common / "objects"
        parts.append(identity(objects))
        if not identity(objects):
            return None
        with os.scandir(objects) as entries:
            parts.append(
                sorted(
                    (entry.name, identity(Path(entry.path)))
                    for entry in entries
                    if len(entry.name) == 2
                )
            )
        pack = objects / "pack"
        parts.append(identity(pack))
        if pack.is_dir():
            with os.scandir(pack) as entries:
                parts.append(sorted((entry.name, identity(Path(entry.path))) for entry in entries))
        alternates = _alternates_witness(objects, frozenset())
        if alternates is None:
            return None
        parts.append(alternates)
        return tuple(parts)
    except (OSError, UnicodeError):
        return None


def _alternates_witness(objects: Path, seen: frozenset[Path]) -> tuple[object, ...] | None:
    """The identities of one store's alternates list and of every store it names, recursively."""

    listing = objects / "info" / "alternates"
    stamp = identity(listing)
    if not stamp:
        return (str(listing), stamp)
    named: list[object] = [str(listing), stamp]
    for line in listing.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith('"'):
            return None  # a quoted path; Git's unquoting is not repeated here
        alternate = Path(entry) if Path(entry).is_absolute() else objects / entry
        named.append((entry, identity(alternate)))
        if alternate in seen or alternate == objects or not alternate.is_dir():
            continue
        nested = _alternates_witness(alternate, seen | {objects})
        if nested is None:
            return None
        named.append(nested)
    return tuple(named)


def _count_objects(root: Path) -> tuple[int, int, tuple[str, ...]]:
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
        tuple(alternates),
    )


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


#: The last content digest per configuration file, with the full file identity it was read
#: under. Only a digest whose identity was the same before and after its read is kept. A
#: repository names about fifteen configuration paths and the dashboard reads a few dozen
#: repositories and worktrees, so the bound sits well above that working set; an evicted
#: file is simply hashed again.
_configuration_digests: WitnessedMemo[Path, Identity, str] = WitnessedMemo(4096)


def configuration_identity(path: Path) -> str:
    """The file's content digest, hashed again only when its identity moved.

    Link and target device, inode, mode, size, mtime and ctime all unchanged reuse the
    digest; any difference re-hashes. ctime moves on an in-place rewrite even when the
    writer restores mtime and size, so a rewrite always reaches the hash.
    """
    before = identity(path)
    kept = _configuration_digests.get(path, before) if before else None
    if kept is not None:
        return kept[0]
    try:
        if not before:
            raise FileNotFoundError(path)
        digest = _file_digest(path)
    except FileNotFoundError:
        _configuration_digests.discard(path)
        return "absent"
    if identity(path) == before:
        _configuration_digests.put(path, before, digest)
    return digest


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

        return self.objects_presence((oid,))[0]

    def objects_presence(self, oids: tuple[str, ...]) -> tuple[str, ...]:
        """The same witnesses for a closure, listing each pack directory at most once.

        Only named loose files and direct pack entries are observed. Every descendant in the
        caller's validated closure participates; observing its root alone cannot detect a lost
        blob. ctime makes an in-place rewrite visible even when mtime is restored.

        Each loose fan-out directory is listed once, and only a name it holds is stat'ed: a name
        it does not hold has no file to stat, so the closure's packed objects cost no ``lstat``
        each. A fan-out that cannot be listed is stat'ed name by name, as before.
        """
        packs: str | None = None
        result: list[str] = []
        listed: dict[tuple[Path, str], frozenset[str] | None] = {}
        for index, oid in enumerate(oids):
            if not index % _CHECK_EVERY:
                raise_if_read_cancelled()  # a closure of thousands of objects: a read that left stops
            named = _OBJECT_ID.fullmatch(oid) is not None
            for directory in self.object_directories:
                if named:
                    fanout = (directory, oid[:2])
                    if fanout not in listed:
                        listed[fanout] = _fanout_names(directory / oid[:2])
                    names = listed[fanout]
                    if names is not None and oid[2:] not in names:
                        continue
                loose = directory / oid[:2] / oid[2:]
                stamp = identity(loose)
                if stamp:
                    result.append(repr(("loose", str(loose), stamp)))
                    break
            else:
                if packs is None:
                    stamps = []
                    for directory in self.object_directories:
                        packed = directory / "pack"
                        entries = (
                            tuple(
                                sorted(
                                    (entry.name, identity(entry))
                                    for entry in packed.iterdir()
                                    if entry.name.endswith((".idx", ".pack"))
                                )
                            )
                            if packed.is_dir()
                            else ()
                        )
                        stamps.append((str(directory), entries))
                    packs = repr(("packed", tuple(stamps)))
                result.append(packs)
        return tuple(result)

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
        *tuple(Path(alternate) for alternate in _census(root)[2]),
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
