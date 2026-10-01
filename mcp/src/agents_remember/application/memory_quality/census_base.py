"""The census's comparison base on a converting leaf: K_B's conversion, as a Git tree (MIK-R24 rule 7).

The memory census compares the memory candidate with the leaf's baseline commit to find what the task
edited. When the candidate is converted and the baseline is not -- the converting leaf, or a line that
has crossed -- every card differs only because it was converted, and the census would count each one
as a task edit with no metadata. Rule 7 compares a converted candidate with K_B's *conversion*
instead, exactly as the worklist and the gates do: the files come from the one converted-base cache
(:func:`~agents_remember.application.knowledge_worklist.base_cache.converted_base_files`, keyed by the
memory commit, the version and K_B's own ``Code-Commit`` or B), so the census never converts again
what the gate converted.

The comparison tree is the baseline's tree with the converted kinds replaced: every path the cache
holds (onboarding Markdown and the knowledge JSON) is taken from the conversion, and every other path
(``system/``, ``docs/``, the frozen database, ...) stays the baseline's own. Only objects are written
into the memory repository; no ref, index or working tree is touched, and the same inputs always give
the same tree.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from agents_remember.application.knowledge_worklist.base_cache import (
    converted_base_files,
    default_base_cache_directory,
    is_cached_path,
)
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.memory_quality.memory_census_scope import MemoryComparison
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = ["census_comparison"]

_TERRITORY: Final = ("knowledge", "onboarding")
_TREE_MODE: Final = "040000"
_BLOB_MODE: Final = "100644"
Entry = tuple[str, str, str]  # (mode, type, object id)


def _git(repository: Path, args: list[str], input_text: str | None = None) -> str:
    result = run_git(repository, args, GitRunnerOptions(input_text=input_text))
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} failed in {repository}: {result.stderr.strip()}")
    return result.stdout


def _layout_version(repository: Path, treeish: str) -> str | None:
    """The conversion-format version ``treeish``'s layout marker pins, or ``None`` (unconverted)."""

    shown = run_git(repository, ["cat-file", "blob", f"{treeish}:{LAYOUT_MARKER_PATH}"])
    if shown.returncode != 0:
        return None
    return str(parse_json(shown.stdout)["conversion"])


def _listing(repository: Path, treeish: str, *, recursive: bool) -> dict[str, Entry]:
    args = ["ls-tree", "-z", *(["-r"] if recursive else []), treeish]
    entries: dict[str, Entry] = {}
    for row in _git(repository, args).split("\0"):
        if row:
            header, path = row.split("\t", 1)
            mode, kind, object_id = header.split()
            entries[path] = (mode, kind, object_id)
    return entries


def _hash(repository: Path, files: Mapping[str, bytes]) -> dict[str, Entry]:
    """Write ``files`` as blobs; return each path's entry."""

    names = sorted(files)
    with TemporaryDirectory(prefix="ar-census-base-") as scratch:
        root = Path(scratch)
        for position, name in enumerate(names):
            (root / str(position)).write_bytes(files[name])
        listed = "".join(f"{root / str(position)}\n" for position in range(len(names)))
        blobs = _git(
            repository, ["hash-object", "-w", "--no-filters", "--stdin-paths"], listed
        ).split()
    if len(blobs) != len(names):
        raise ValueError("the converted base's blobs could not all be written")
    return {name: (_BLOB_MODE, "blob", blob) for name, blob in zip(names, blobs, strict=True)}


def _directories(files: Mapping[str, Entry]) -> dict[str, dict[str, Entry]]:
    """Each directory's entries by name (``""`` is the root); every ancestor is present."""

    if any("\n" in path for path in files):
        raise ValueError("a memory path holding a newline cannot be written as a census tree")
    children: dict[str, dict[str, Entry]] = {"": {}}
    for path, entry in files.items():
        parent, _, name = path.rpartition("/")
        children.setdefault(parent, {})[name] = entry
        while parent:
            parent = parent.rpartition("/")[0]
            children.setdefault(parent, {})
    return children


def _listing_of(entries: Mapping[str, Entry]) -> str:
    return "".join(
        f"{mode} {kind} {object_id}\t{name}\n"
        for name, (mode, kind, object_id) in sorted(entries.items())
    )


def _make_trees(repository: Path, files: Mapping[str, Entry]) -> str:
    """The root tree of ``files`` (path -> entry), built deepest directories first, one batch each."""

    children = _directories(files)
    depths: dict[int, list[str]] = {}
    for directory in children:
        depths.setdefault(directory.count("/") + bool(directory), []).append(directory)
    for depth in sorted(depths, reverse=True):
        directories = sorted(depths[depth])
        batch = "\n".join(_listing_of(children[directory]) for directory in directories)
        made = _git(repository, ["mktree", "--batch"], batch).split()
        if len(made) != len(directories):
            raise ValueError("the census comparison tree could not be written")
        for directory, tree in zip(directories, made, strict=True):
            if not directory:
                return tree  # the root is the last batch, alone
            parent, _, name = directory.rpartition("/")
            children[parent][name] = (_TREE_MODE, "tree", tree)
    raise ValueError("the census comparison tree has no root")


def _overlay(repository: Path, baseline: str, files: Mapping[str, bytes]) -> str:
    """The baseline's tree with every cached kind replaced by ``files`` (the conversion)."""

    top = _listing(repository, baseline, recursive=False)
    kept = {
        path: entry
        for path, entry in _listing(repository, baseline, recursive=True).items()
        if path.split("/", 1)[0] in _TERRITORY and not is_cached_path(path)
    }
    rebuilt = {**kept, **_hash(repository, files)}
    outside = {name: entry for name, entry in top.items() if name not in _TERRITORY}
    return _make_trees(repository, {**outside, **rebuilt})


def census_comparison(contract: WorktreeContract) -> MemoryComparison:
    """The census's comparison base for ``contract``: K_B, or its conversion (rule 7)."""

    code = (contract.code_repo_path, contract.code_base_commit)
    cache = default_base_cache_directory(contract.coordination_root)

    def compare(repository: Path, baseline: str, candidate_tree: str) -> str:
        version = _layout_version(repository, candidate_tree)
        if version is None or _layout_version(repository, baseline) is not None:
            return baseline
        try:
            files = converted_base_files(
                repository, baseline, code=code, version=version, cache_directory=cache
            )
            return _overlay(repository, baseline, files)
        except (ValueError, KeyError, OSError) as error:
            raise ValueError(
                f"the memory census cannot compare the converted candidate with its base: the "
                f"conversion of {baseline[:12]} (MIK-R24 rule 7) cannot be built ({error})"
            ) from error

    return compare
