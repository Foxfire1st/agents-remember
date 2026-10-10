"""Publisher-owned citation source index census: the orphaned slot directories (MIK-R82 rule 3).

A citation source index is the citation publisher's own record (schema version, roots,
snapshot id, sizes, file lists) beside its database. The managed namespace
(``temp/citation-source-index/managed``) belongs to the cache authority and is never entered;
every other publisher-shaped slot directory anywhere under the addressed coordination root --
the ``temp/citation-source-index`` tree, task and enclosure ``ar-cache-*`` placements (the
publisher's ``cache_root`` supports ``XDG_CACHE_HOME`` and ``ar-cache-<uid>`` temporary
placements, which is how such slots end up rooted inside a task or enclosure) -- is listed
with its slot directory under the rule ``unmanaged-citation-source-index``, and the reviewed
apply deletes those slot directories whole.

Ownership is by the manifest's content, never by a database file's name: a foreign database
file under the tree is never mistaken for an index. A slot Git tracks anywhere is listed and
kept, never deleted. The derived-index cache (``runtime/knowledge-index``) is never entered,
a symbolic link is never followed, read or deleted, and Git is asked only inside the
repository a file lies in (the nearest directory with a ``.git`` entry below the root), never
at the root itself.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CITATION_CACHE_RULE",
    "CitationCache",
    "delete_citation_caches",
    "scan_citation_caches",
]

# The one citation-cache rule (MIK-R82 rule 3): an index outside the managed root is listed
# with its slot directory and deleted with it. The value is the rule name the report carries.
CITATION_CACHE_RULE: Final = "unmanaged-citation-source-index"
_CITATION_CACHE_RULE_TEXT: Final = (
    "deleted: a citation source index outside the managed root, with its slot directory"
)
_CITATION_INDEX_ROOT: Final = ("temp", "citation-source-index")
_CITATION_MANAGED: Final = "managed"
_CITATION_MANIFEST_NAME: Final = "manifest.json"
_INDEX_CACHE: Final = ("runtime", "knowledge-index")

# Git's answer about one file, as ``(state, detail)``: ``tracked``, ``untracked``,
# ``no-checkout``, ``gone`` or ``unknown``. Injected by the caller so this census owns no
# Git plumbing of its own.
Tracking = Callable[[Path, Path], tuple[str, str]]


@dataclass(frozen=True)
class CitationCache:
    """One citation source index outside the managed root, with the slot directory it is deleted with."""

    path: Path
    relative: str
    size: int
    tracked: bool = False
    detail: str = ""

    @property
    def deleted_by_rule(self) -> bool:
        return not self.tracked

    @property
    def rule(self) -> str:
        return CITATION_CACHE_RULE

    def document(self) -> dict[str, Any]:
        text = _CITATION_CACHE_RULE_TEXT + (f" ({self.detail})" if self.detail else "")
        if self.tracked:
            text = "kept: Git tracks a file under the slot directory, so it stays"
            if self.detail:
                text += f" ({self.detail})"
        return {
            "path": self.relative,
            "bytes": self.size,
            "rule": self.rule,
            "ruleText": text,
            "owner": "-",
        }


def scan_citation_caches(
    root: Path,
    caches: list[CitationCache],
    unreadable: list[dict[str, str]],
    *,
    tracking: Tracking,
) -> None:
    """Append every publisher-owned citation source index under ``root`` to ``caches``.

    The walk covers the whole coordination root outside the exact managed subtree, so task
    and enclosure ``ar-cache-*`` placements are found wherever a publisher process rooted
    them. The managed subtree and the derived-index cache are never entered, ``.git``
    directories are never entered, and a symbolic link is never followed: a slot is only
    ever a real directory whose own manifest reads as a citation source index manifest. A
    slot Git could not be asked about is reported in ``unreadable`` instead of being listed.
    Directory readability itself is left to the caller's own walk, which covers the same
    directories; this census reports nothing twice.
    """

    managed = root.joinpath(*_CITATION_INDEX_ROOT, _CITATION_MANAGED)
    index_cache = root.joinpath(*_INDEX_CACHE)
    stack = [root]
    while stack:
        directory = stack.pop()
        if directory.is_symlink():
            continue
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            continue
        if _is_citation_slot(directory):
            size, tracked, unknown = _slot_inventory(root, directory, tracking=tracking)
            if unknown and not tracked:
                unreadable.append(
                    {
                        "path": directory.relative_to(root).as_posix(),
                        "detail": unknown,
                    }
                )
                continue
            caches.append(
                CitationCache(
                    path=directory,
                    relative=directory.relative_to(root).as_posix(),
                    size=size,
                    tracked=tracked,
                    detail="a file under the slot directory is Git-tracked" if tracked else "",
                )
            )
            continue
        for entry in entries:
            if entry.name == ".git" or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                child = Path(entry.path)
                if _within(child, managed) or _within(child, index_cache):
                    continue
                stack.append(child)


def delete_citation_caches(
    root: Path,
    caches: list[CitationCache],
    failures: list[dict[str, str]],
    *,
    tracking: Tracking,
) -> list[str]:
    """Delete every untracked citation cache with its slot directory; tracked ones are kept."""

    deleted: list[str] = []
    for cache in caches:
        if not cache.deleted_by_rule:
            continue
        refusal = _cache_refusal(root, cache, tracking=tracking)
        if refusal is not None:
            failures.append({"path": cache.relative, "detail": refusal})
            continue
        try:
            shutil.rmtree(cache.path)
        except OSError as error:
            failures.append({"path": cache.relative, "detail": str(error)})
            continue
        deleted.append(cache.relative)
    return deleted


def _within(child: Path, stop: Path) -> bool:
    """Whether ``child`` is ``stop`` itself or lies under it (the never-entered subtrees)."""

    return child == stop or stop in child.parents


def _is_citation_slot(directory: Path) -> bool:
    """Whether ``directory`` is a citation source index slot: its manifest says so.

    By the manifest's content, not by any database file's name: the manifest is the citation
    publisher's own record (schema version, roots, snapshot id, sizes, file lists), so a
    foreign database file under the tree is never mistaken for an index. The values are the
    index's own record and are never trusted here beyond this shape check.
    """

    manifest = directory / _CITATION_MANIFEST_NAME
    if manifest.is_symlink() or not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("schemaVersion"), int)
        and isinstance(payload.get("codeRoot"), str)
        and isinstance(payload.get("memoryRoot"), str)
        and isinstance(payload.get("snapshotId"), str)
        and isinstance(payload.get("sourceBytes"), int)
        and isinstance(payload.get("directories"), list)
        and isinstance(payload.get("files"), list)
    )


def _slot_inventory(root: Path, slot: Path, *, tracking: Tracking) -> tuple[int, bool, str]:
    """``(bytes, tracked, unknown)`` for one slot: its size, and whether Git tracks any of it.

    Links are never followed. ``tracked`` counts any tracked file under the slot: a slot with
    a tracked file in it is listed and kept, never deleted. ``unknown`` is Git's answer when
    it could not say, and then the slot is skipped the same way.
    """

    size = 0
    tracked = False
    unknown = ""
    for directory, _names, files in os.walk(slot, followlinks=False):
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                continue
            try:
                size += candidate.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            state, detail = tracking(root, candidate)
            if state == "tracked":
                tracked = True
            elif state == "unknown" and not unknown:
                unknown = detail
    return size, tracked, unknown


def _cache_refusal(root: Path, cache: CitationCache, *, tracking: Tracking) -> str | None:
    """Why a planned cache deletion is not carried out after all, or ``None``."""

    path = cache.path
    try:
        anchored = root in path.resolve().parents
    except OSError:
        return "the slot directory changed since it was listed"
    if path.is_symlink() or not path.is_dir() or not anchored:
        return "the slot directory changed since it was listed"
    if not _is_citation_slot(path):
        return "the slot's citation manifest is no longer where the slot directory was listed"
    for directory, _names, files in os.walk(path, followlinks=False):
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                continue
            state, detail = tracking(root, candidate)
            if state == "tracked":
                return "a file under the slot directory is Git-tracked; its deletion was refused"
            if state == "unknown":
                return (
                    "Git could not determine whether a file under the slot directory is "
                    f"tracked; its deletion was refused ({detail})"
                )
    return None
