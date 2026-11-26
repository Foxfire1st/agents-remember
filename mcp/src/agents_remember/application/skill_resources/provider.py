"""Where the served skills tree comes from.

The served tree is the package's own runtime skills copy. That copy is generated
from the repository's canonical ``skills/`` tree by the repository's sync script,
and publishing the packaged copy is what makes a served revision reproducible: the
bytes a client reads belong to exactly one installed build, so a digest recorded in
one exchange still names the same content in the next.

An operator can serve a different tree by supplying the root explicitly, which is
how a test drives a synthetic tree without touching the shipped corpus.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from agents_remember.install.assets import PACKAGE_DATA_ROOT, packaged_source_root
from agents_remember.kernel.coordination_context.paths import resolve_coordination_root_hint

from .catalog import SkillSourceTree

#: The server identity that publishes the shipped skills tree. It is the first two
#: skill-path segments of every shipped skill URI and the ``origin`` half of a
#: skill's distinct identity, so two servers serving one skill name stay distinct.
SHIPPED_SKILL_ORIGIN = "agents-remember/skills"

#: The packaged runtime skills copy, relative to the package-data root.
PACKAGED_SKILLS_DIRECTORY = "runtime/skills"

#: The admitted corpus inside that copy: the lifecycle tree whose own directory is
#: the base of every source path the composition manifest declares.
PACKAGED_COMPOSITION_ROOT = "l-01-agent-lifecycles"

#: The composition manifest inside that corpus.
PACKAGED_COMPOSITION_MANIFEST = "composition-manifest.json"


@contextmanager
def shipped_skill_tree() -> Iterator[SkillSourceTree]:
    """Yield the shipped skills tree for the duration of one server's registration."""

    with packaged_source_root() as root:
        yield SkillSourceTree(
            root=Path(root) / PACKAGED_SKILLS_DIRECTORY,
            origin=SHIPPED_SKILL_ORIGIN,
        )


@contextmanager
def shipped_composition_corpus() -> Iterator[tuple[Path, str]]:
    """Yield the shipped corpus root and its manifest, both admitted together.

    The two are one value because they are one admission: a corpus root without its
    manifest, or a manifest read from another root, is a pairing that cannot select
    a source set.
    """

    with packaged_source_root() as root:
        yield (
            Path(root) / PACKAGED_SKILLS_DIRECTORY / PACKAGED_COMPOSITION_ROOT,
            PACKAGED_COMPOSITION_MANIFEST,
        )


def durable_composition_corpus_root(cache_root: Path | None = None) -> Path:
    """Return a durable filesystem root for the packaged composition corpus.

    Installed filesystem wheels resolve directly to their package-data tree.
    Non-filesystem archives are materialized atomically into a content-addressed
    cache keyed by the full corpus digest so emitted links stay valid after the
    caller exits.
    """

    root = resources.files("agents_remember").joinpath(PACKAGE_DATA_ROOT)
    if isinstance(root, Path):
        return (root / PACKAGED_SKILLS_DIRECTORY / PACKAGED_COMPOSITION_ROOT).resolve()

    corpus = root.joinpath(PACKAGED_SKILLS_DIRECTORY, PACKAGED_COMPOSITION_ROOT)
    entries = _collect_traversable_files(corpus)
    corpus_digest = _compute_corpus_digest(entries)
    base = (
        Path(cache_root)
        if cache_root is not None
        else resolve_coordination_root_hint(None) / ".cache" / "skill-corpus"
    )
    digest_dir = base / corpus_digest
    target = digest_dir / PACKAGED_COMPOSITION_ROOT
    if not digest_dir.is_symlink() and target.is_dir():
        if _directory_corpus_digest(target) == corpus_digest:
            return target.resolve()
        raise RuntimeError(
            f"Cached skill corpus at {digest_dir} does not match expected digest "
            f"{corpus_digest}; remove {digest_dir} and retry."
        )
    if digest_dir.exists() or digest_dir.is_symlink():
        raise RuntimeError(
            f"Cached skill corpus path {digest_dir} is invalid for digest "
            f"{corpus_digest}; remove {digest_dir} and retry."
        )
    return _materialize_corpus(base, digest_dir, target, entries, corpus_digest)


def _collect_traversable_files(root: Traversable) -> list[tuple[str, bytes]]:
    if not root.is_dir():
        raise RuntimeError(f"packaged composition corpus is missing: {root}")
    entries: list[tuple[str, bytes]] = []
    _walk_traversable(root, "", entries)
    entries.sort(key=lambda item: item[0])
    return entries


def _walk_traversable(node: Traversable, prefix: str, entries: list[tuple[str, bytes]]) -> None:
    for child in node.iterdir():
        name = child.name
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise RuntimeError(f"packaged composition corpus has unsafe entry name: {name!r}")
        rel = f"{prefix}/{name}" if prefix else name
        if child.is_dir():
            _walk_traversable(child, rel, entries)
        elif child.is_file():
            with child.open("rb") as stream:
                entries.append((rel, stream.read()))


def _compute_corpus_digest(entries: list[tuple[str, bytes]]) -> str:
    hasher = hashlib.sha256()
    for rel, data in entries:
        hasher.update(f"{rel}\0{len(data)}\0".encode())
        hasher.update(data)
    return hasher.hexdigest()


def _collect_directory_files(directory: Path) -> list[tuple[str, Path]] | None:
    root_stat = os.lstat(directory)
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return None
    files: list[tuple[str, Path]] = []
    pending: list[tuple[Path, str]] = [(directory, "")]
    while pending:
        current, prefix = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink():
                    return None
                rel = f"{prefix}/{entry.name}" if prefix else entry.name
                entry_path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append((entry_path, rel))
                elif entry.is_file(follow_symlinks=False):
                    files.append((rel, entry_path))
                else:
                    return None
    files.sort(key=lambda item: item[0])
    return files


def _directory_corpus_digest(directory: Path) -> str | None:
    try:
        files = _collect_directory_files(directory)
        if files is None:
            return None
        hasher = hashlib.sha256()
        for rel, file_path in files:
            fd = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return None
                data = stream.read()
            hasher.update(f"{rel}\0{len(data)}\0".encode())
            hasher.update(data)
    except OSError:
        return None
    return hasher.hexdigest()


def _materialize_corpus(
    base: Path,
    digest_dir: Path,
    target: Path,
    entries: list[tuple[str, bytes]],
    corpus_digest: str,
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    staging = base / f".{corpus_digest}.staging-{uuid.uuid4().hex}"
    staging_target = staging / PACKAGED_COMPOSITION_ROOT
    try:
        staging_target.mkdir(parents=True, exist_ok=False)
        for rel, data in entries:
            destination = staging_target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if _directory_corpus_digest(staging_target) != corpus_digest:
            raise RuntimeError(
                f"Staged skill corpus at {staging_target} failed digest verification for "
                f"{corpus_digest}."
            )
        try:
            staging.rename(digest_dir)
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            if not digest_dir.is_symlink() and _directory_corpus_digest(target) == corpus_digest:
                return target.resolve()
            raise RuntimeError(
                f"Cached skill corpus at {digest_dir} could not be populated for digest "
                f"{corpus_digest}; remove {digest_dir} and retry."
            ) from error
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target.resolve()


__all__ = [
    "PACKAGED_COMPOSITION_MANIFEST",
    "PACKAGED_COMPOSITION_ROOT",
    "PACKAGED_SKILLS_DIRECTORY",
    "SHIPPED_SKILL_ORIGIN",
    "durable_composition_corpus_root",
    "shipped_composition_corpus",
    "shipped_skill_tree",
]
