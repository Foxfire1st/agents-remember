"""Read the content a memory review passed by record identity, independent of its slug."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git, run_git_bytes
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    parse_document_text,
    split_record_filename,
)
from agents_remember.models.knowledge_files.history_product import stored_record_content_digest
from agents_remember.models.knowledge_files.record_state import ReviewContentMatch
from agents_remember.models.knowledge_files.records import RECORD_MODELS

_DIRECTORY_MODELS = {
    directory: RECORD_MODELS[kind] for kind, directory in RECORD_DIRECTORIES.items()
}
_RECORD_TYPES = tuple(RECORD_MODELS.values())
_RECORD_ROOTS = tuple(f"{KNOWLEDGE_ROOT}/{directory}" for directory in _DIRECTORY_MODELS)


def reviewed_content(repository: Path) -> ReviewContentMatch:
    """Check facts against exact original Git blobs, reusing reads within this operation."""

    trees: dict[str, dict[str, list[tuple[str, str]]]] = {}
    blobs: dict[tuple[str, str], tuple[str, int, str] | None] = {}

    def matches(tree: str, record: str, revision: int, digest: str) -> bool | None:
        if tree not in trees:
            trees[tree] = _reviewed_records(repository, tree)
        observed: set[tuple[int, str]] = set()
        for blob in trees[tree].get(record, ()):
            if blob not in blobs:
                blobs[blob] = _read_record(repository, *blob)
            content = blobs[blob]
            if content is None or content[0] != record:
                continue
            observed.add(content[1:])
        return observed == {(revision, digest)} if observed else None

    return matches


def _reviewed_records(repository: Path, tree: str) -> dict[str, list[tuple[str, str]]]:
    """Index the canonical record locations of an immutable tree by their stable IDs."""

    verified = run_git(repository, ["--no-replace-objects", "rev-parse", f"{tree}^{{tree}}"])
    if verified.returncode != 0 or verified.stdout.strip() != tree:
        return {}
    listed = run_git_bytes(
        repository,
        ["--no-replace-objects", "ls-tree", "-r", "-z", "--full-tree", tree, "--", *_RECORD_ROOTS],
    )
    if listed.returncode != 0:
        return {}
    records: dict[str, list[tuple[str, str]]] = {}
    for entry in listed.stdout.split(b"\0"):
        if location := _record_location(entry):
            record, directory, object_id = location
            records.setdefault(record, []).append((directory, object_id))
    return records


def _record_location(entry: bytes) -> tuple[str, str, str] | None:
    metadata, _, raw_path = entry.partition(b"\t")
    path = raw_path.decode("utf-8", "surrogateescape").split("/")
    if len(path) != 3 or path[1] not in _DIRECTORY_MODELS or not path[2].endswith(".json"):
        return None
    mode, kind, object_id = metadata.decode("ascii").split()
    if mode not in ("100644", "100755") or kind != "blob":
        return None
    try:
        record, _, _ = split_record_filename(path[2])
    except ValueError:
        return None
    return record, path[1], object_id


def _read_record(repository: Path, directory: str, object_id: str) -> tuple[str, int, str] | None:
    result = run_git_bytes(repository, ["--no-replace-objects", "cat-file", "blob", object_id])
    if result.returncode:
        return None
    try:
        record = parse_document_text(result.stdout.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(record, _RECORD_TYPES):
        return None
    if not isinstance(record, _DIRECTORY_MODELS[directory]):
        return None
    return record.id, record.revision, stored_record_content_digest(record)
