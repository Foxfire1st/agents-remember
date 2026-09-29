"""The converted-base cache: one file per converted K_B, so a leaf's runs convert it once.

When K_B is unconverted and K_C is converted, K_B is compared as its conversion (MIK-R24 rule 7),
and converting a real memory tree takes about half a minute. The conversion is a pure function of
the memory commit, the conversion-format version and the paired code commit (rule 6), so its result
is cached under exactly that key:

* **Where.** ``<coordination-root>/runtime/knowledge-worklist-bases/`` by default, beside L23's
  index cache (``runtime/knowledge-index``). A directory inside any Git working tree is refused, so
  a cached base can never be staged or committed.
* **What.** ``<sha256 of the key>.json.gz``: the format, the key, and the converted tree's files the
  two consumers read, as text: the index's JSON files (the worklist, MIK-R08) and the onboarding
  Markdown (the onboarding gate, MIK-R30). A file of another format or key is ignored and
  rewritten; format ``v1`` held no Markdown, so its files are never read as a gate's base. Writes
  go through the kernel's atomic write, so a reader never sees a half-written file.
* **Eviction.** After a write, the oldest files beyond :data:`MAX_FILES` are removed.

Deleting the directory, or any file in it, loses nothing: the base is converted again.
"""

from __future__ import annotations

import contextlib
import gzip
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.conversion.base import converted_base, own_paired_code_commit
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.knowledge_index import is_indexed_path
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT

__all__ = [
    "CACHE_DIRECTORY_PARTS",
    "ConvertedBaseCache",
    "base_cache_key",
    "converted_base_files",
    "default_base_cache_directory",
    "is_cached_path",
]

CACHE_DIRECTORY_PARTS: Final = ("runtime", "knowledge-worklist-bases")
FORMAT: Final = "knowledge-worklist-base/v2"
MAX_FILES: Final = 32
_SUFFIX: Final = ".json.gz"


def default_base_cache_directory(coordination_root: Path) -> Path:
    """Return ``<coordination-root>/runtime/knowledge-worklist-bases``."""

    return coordination_root.joinpath(*CACHE_DIRECTORY_PARTS)


def base_cache_key(memory_commit: str, version: str, code_commit: str) -> list[str]:
    return [memory_commit, version, code_commit]


class ConvertedBaseCache:
    """The cache directory. :meth:`open` returns ``None`` for a location it refuses."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def open(cls, directory: Path) -> ConvertedBaseCache | None:
        """The cache at ``directory``, or ``None`` when it lies inside a Git working tree."""

        existing = directory.resolve()
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        inside = run_git(existing, ["rev-parse", "--is-inside-work-tree"])
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            return None
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return cls(directory.resolve())

    def _path(self, key: list[str]) -> Path:
        return self.directory / f"{sha256_digest(key)}{_SUFFIX}"

    def load(self, key: list[str]) -> dict[str, bytes] | None:
        """The cached files for ``key``, or ``None`` when absent or not this format and key."""

        path = self._path(key)
        try:
            loaded = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except (OSError, ValueError, EOFError):
            return None
        if (
            not isinstance(loaded, dict)
            or loaded.get("format") != FORMAT
            or loaded.get("key") != key
        ):
            return None
        files = loaded.get("files")
        if not isinstance(files, dict):
            return None
        with contextlib.suppress(OSError):
            path.touch()
        return {str(name): str(text).encode("utf-8") for name, text in files.items()}

    def store(self, key: list[str], files: Mapping[str, bytes]) -> None:
        """Write the files for ``key``; a file that is not UTF-8 text makes the base uncacheable."""

        try:
            texts = {name: data.decode("utf-8") for name, data in sorted(files.items())}
        except UnicodeDecodeError:
            return
        payload = json.dumps({"format": FORMAT, "key": key, "files": texts}, sort_keys=True)
        try:
            atomic_write_bytes(self._path(key), gzip.compress(payload.encode("utf-8"), mtime=0))
        except OSError:
            return
        self._evict()

    def _evict(self) -> None:
        cached = sorted(self.directory.glob(f"*{_SUFFIX}"), key=lambda one: one.stat().st_mtime)
        for stale in cached[:-MAX_FILES]:
            with contextlib.suppress(OSError):
                stale.unlink()


def is_cached_path(path: str) -> bool:
    """A converted base's file the cache holds: an indexed JSON file, or onboarding Markdown."""

    return is_indexed_path(path) or (
        path.startswith(f"{ONBOARDING_ROOT}/")
        and path.endswith(".md")
        and not any(part.startswith(".") for part in path.split("/")[:-1])
    )


def converted_base_files(
    memory_repository: Path,
    memory_commit: str,
    *,
    code: tuple[Path, str],
    version: str,
    cache_directory: Path | None,
) -> dict[str, bytes]:
    """The conversion of K_B (MIK-R24 rule 7), read from the cache when present.

    ``code`` is the code repository and B. The conversion runs at K_B's own paired code commit (its
    ``Code-Commit`` trailer, when the code store holds it; B otherwise), exactly as
    ``GitBaseConverter`` chooses, and the cache key is (K_B commit, version, that code commit).
    """

    code_repository, code_base = code
    own = own_paired_code_commit(memory_repository, memory_commit)
    code_commit = own if own is not None and CodeObjects(code_repository).commit(own) else code_base
    cache = None if cache_directory is None else ConvertedBaseCache.open(cache_directory)
    key = base_cache_key(memory_commit, version, code_commit)
    files = None if cache is None else cache.load(key)
    if files is None:
        converted = converted_base(
            memory_repository,
            memory_commit,
            code_repository=code_repository,
            code_commit=code_commit,
            version=version,
        )
        files = {path: data for path, data in converted.files.items() if is_cached_path(path)}
        if cache is not None:
            cache.store(key, files)
    return files
