"""What a landing's change reaches: the record a changed path names, and the dependents it moves.

A landing reads only the dependents a change can move, so both answers come from paths and links
alone; no record body is read to decide them.
"""

from __future__ import annotations

from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    split_record_filename,
)
from agents_remember.models.knowledge_files.records import DependentRecord


def record_id_of(path: str) -> str | None:
    """The record ID a ``knowledge/<record directory>/<ID>-<slug>.json`` path names, or ``None``."""

    parts = path.split("/")
    if len(parts) != 3 or parts[0] != KNOWLEDGE_ROOT or not parts[2].endswith(".json"):
        return None
    if parts[1] not in set(RECORD_DIRECTORIES.values()):
        return None
    try:
        return split_record_filename(parts[2])[0]
    except ValueError:
        return None


def dependent_touches(
    record: DependentRecord,
    changed_paths: frozenset[str],
    changed_records: frozenset[str],
) -> bool:
    """Whether one dependent has a link at a changed path or at a record that changed."""

    for link in record.stands_on:
        if link.kind == "record":
            if link.id in changed_records:
                return True
        elif (link.anchor.path if link.kind == "anchor" else link.path) in changed_paths:
            return True
    return False
