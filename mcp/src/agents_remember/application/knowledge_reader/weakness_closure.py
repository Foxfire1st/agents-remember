"""Read a closed weakness's code attribution without changing its stored document."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final, cast

from pydantic import ValidationError

from agents_remember.application.knowledge_reader.selection import READ_FAILURES, ReaderSelection
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.kernel.memory_attribution import MemoryAttributionError
from agents_remember.memory.conversion.base import own_paired_code_commit
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, RECORD_DIRECTORIES
from agents_remember.models.knowledge_files.records import Closure, WeaknessRecord

__all__ = ["closure_document"]

_OPTIONS: Final = GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS)
_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['weakness']}"
_PENDING_NOTE: Final = "The code commit comes with this task's closeout."
_UNAVAILABLE_NOTE: Final = "The closing code commit is unavailable: "


def closure_document(selection: ReaderSelection, record: WeaknessRecord) -> dict[str, Any]:
    """A closed record's display fields; explicit attribution remains exactly as stored.

    Callers pass a closed weakness. Derived fields belong only to this returned copy, so a
    read cannot change the index or the memory tree that a closeout will publish.
    """

    closure = cast(Closure, record.closed)
    document = closure.to_document()
    if closure.commit is not None:
        return document
    origin, unavailable = _closing_memory_commit(selection, record)
    if origin is None:
        document["commitNote"] = (
            _PENDING_NOTE if unavailable is None else f"{_UNAVAILABLE_NOTE}{unavailable}"
        )
        return document
    try:
        commit = own_paired_code_commit(selection.memory_repository, origin)
        if commit is None:
            unavailable = f"memory commit {origin} has no readable Code-Commit trailer"
        elif CodeObjects(selection.code_repository).commit(commit) != commit:
            unavailable = f"the trailer on memory commit {origin} names no full code commit"
        else:
            document["commit"] = commit
    except READ_FAILURES as error:
        unavailable = str(error)
    if unavailable is not None:
        document["commitNote"] = f"{_UNAVAILABLE_NOTE}{unavailable}"
    return document


def _closing_memory_commit(
    selection: ReaderSelection, record: WeaknessRecord
) -> tuple[str | None, str | None]:
    """Return the unique committed closure origin, pending, or an unavailable explanation."""

    revision = selection.revision
    if revision is None:
        return None, None if selection.reads_directory else "the selected memory revision is absent"
    try:
        history = run_git(
            selection.memory_repository,
            [
                "log",
                "--full-history",
                "--parents",
                "--name-only",
                "--no-renames",
                "-z",
                "--format=%x1e%H%x00%P%x00",
                revision,
                "--",
                f"{_DIRECTORY}/{record.id}-*.json",
            ],
            _OPTIONS,
        )
        if history.returncode:
            raise MemoryAttributionError(
                history.stderr.strip() or "the record history is unreadable"
            )
        records = [block.split("\0") for block in history.stdout.split("\x1e") if block]
        parents = {fields[0]: tuple(fields[1].split()) for fields in records}
        paths = tuple(
            sorted(
                {name.strip("\n") for fields in records for name in fields[2:] if name.strip("\n")}
            )
        )
        if _closure_at(selection, revision, record.id, paths) != record.closed:
            return (
                (None, None)
                if selection.reads_directory
                else (None, "the closure differs from the selected memory revision")
            )
        origins = _closure_origins(selection, revision, record, paths, parents)
        if len(origins) != 1:
            return None, "the selected history has no unique closure origin"
        origin = next(iter(origins))
        # A shallow boundary must not masquerade as the commit introducing the block.
        committed = run_git(selection.memory_repository, ["cat-file", "commit", origin], _OPTIONS)
        if committed.returncode:
            raise MemoryAttributionError("a possible closure origin cannot be read")
        for header in committed.stdout.split("\n\n", 1)[0].splitlines():
            if (
                header.startswith("parent ")
                and _closure_at(selection, header.removeprefix("parent "), record.id, paths)
                == record.closed
            ):
                raise MemoryAttributionError("the closure's ancestry is incomplete")
    except (*READ_FAILURES, MemoryAttributionError) as error:
        return None, str(error)
    return origin, None


def _closure_origins(
    selection: ReaderSelection,
    revision: str,
    record: WeaknessRecord,
    paths: tuple[str, ...],
    parents: Mapping[str, tuple[str, ...]],
) -> set[str]:
    """Follow the same closed block through an ID's complete, merge-preserving history.

    Parent rewriting skips commits that did not change any filename of this ID. Keeping all
    merges preserves an origin imported from another parent and exposes independent origins.
    The memo is local to this walk and releases every observed block when the read returns.
    """

    roots = (
        {revision}
        if revision in parents
        else set(parents).difference(parent for chain in parents.values() for parent in chain)
    )
    blocks: dict[str, Closure | None] = {}
    pending = list(roots)
    visited: set[str] = set()
    origins: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current not in blocks:
            blocks[current] = _closure_at(selection, current, record.id, paths)
        if blocks[current] != record.closed:
            continue
        matching = []
        for parent in parents[current]:
            if parent not in blocks:
                blocks[parent] = _closure_at(selection, parent, record.id, paths)
            if blocks[parent] == record.closed:
                matching.append(parent)
        if matching:
            pending.extend(matching)
            continue
        origins.add(current)
    return origins


def _closure_at(
    selection: ReaderSelection, revision: str, record_id: str, paths: tuple[str, ...]
) -> Closure | None:
    """Read this ID's closure at one exact commit, independently of its filename's slug."""

    if not paths:
        return None
    listed = run_git(
        selection.memory_repository, ["ls-tree", "-r", "-z", revision, "--", *paths], _OPTIONS
    )
    if listed.returncode:
        raise MemoryAttributionError(
            listed.stderr.strip() or f"memory revision {revision} is unreadable"
        )
    matches = []
    for entry in listed.stdout.split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        name = path.rpartition("/")[2]
        if name.startswith(f"{record_id}-") and name.endswith(".json"):
            matches.append(metadata.split()[2])
    if not matches:
        return None
    if len(matches) != 1:
        raise MemoryAttributionError(
            f"memory revision {revision} holds several files for {record_id}"
        )
    try:
        document = json.loads(
            read_git_blobs_bytes(selection.memory_repository, matches)[matches[0]]
        )
        record = WeaknessRecord.model_validate(document)
    except (*READ_FAILURES, ValidationError, json.JSONDecodeError) as error:
        raise MemoryAttributionError(
            f"{record_id} at memory revision {revision} is unreadable: {error}"
        ) from error
    if record.id != record_id:
        raise MemoryAttributionError(f"the historical file for {record_id} holds another record")
    return record.closed
