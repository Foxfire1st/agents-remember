"""Who wrote a record the crossing merge stores: the line whose record it is, or the product.

The merge never combines ``writer`` as a field (:func:`merge_with_writers`). Each side's records are
merged without it, and every record the merge stores without a conflict then takes the stamp of
whoever wrote exactly that content:

* it equals the own line's record: the own line's ``writer``, or none where that record has none;
* else it equals the incoming line's record: that line's ``writer``, or none;
* it equals neither, because the merge combined the two lines' meaning edits: the product's stamp,
  ``by: product`` with the operation :data:`CROSSING_OPERATION`. The syncing owner's writable
  history file names that revision with the same stamp, so the revision's stamp and state are read
  from the same tree like any other revision's.

A difference in ``writer`` alone is therefore never a conflict; the merge's conflicts are those of
the meaning fields. The product's stamp reads ``product`` for role, harness and seat and ``none`` for
model and effort: no agent and no model wrote it. Its time is the later commit time of the two
merged lines, so the merge stays a function of its inputs and a repeated merge stores the same bytes.

When the sides hold different contents at one record and revision number, the crossing's history
records that fact. An unstamped side leaves no writer row; its content still prevents the other
side's row from identifying the shared number.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.memory.conversion.crossing import MergeResult, merge_trees
from agents_remember.memory.reviewed_content import reviewed_content
from agents_remember.models.knowledge_files.canonical import (
    CanonicalFormatError,
    canonical_text,
    parse_json,
)
from agents_remember.models.knowledge_files.converted_reviews import (
    CONVERTED_REVIEWS_PATH,
    ConvertedReviews,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    history_path,
    owner_history_attempt,
    parse_document_text,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    is_closed_history,
    writable_attempt,
)
from agents_remember.models.knowledge_files.history_product import (
    CrossingSelection,
    SharedRevision,
    WrittenRevision,
    crossing_selection_digest,
    record_content_digest,
    stored_record_document,
)
from agents_remember.models.knowledge_files.record_state import ReviewContentMatch, RevisionStates
from agents_remember.models.knowledge_files.records import LEGACY_STATE_WORDS, RECORD_MODELS
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp

CROSSING_OPERATION: Final = "crossing-merge"
PRODUCT_AGENT: Final = "product"
NO_MODEL: Final = "none"
HISTORY_SCHEMA: Final = "ar-history/v1"
_RECORD_PREFIXES: Final = tuple(f"{KNOWLEDGE_ROOT}/{name}/" for name in RECORD_DIRECTORIES.values())
_RECORD_TYPES = tuple(RECORD_MODELS.values())
_LEAF_SUFFIX: Final = re.compile(r"^(?P<task>.+)-L[0-9]+$")


def is_record_path(path: str) -> bool:
    """Whether ``path`` is a knowledge record (not a sidecar, card, history file or the marker)."""

    return path.startswith(_RECORD_PREFIXES) and path.endswith(".json")


def _document(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        document = parse_json(data.decode("utf-8"))
    except (UnicodeDecodeError, CanonicalFormatError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def without_writers(tree: Mapping[str, bytes]) -> dict[str, bytes]:
    """Remove stamps and legacy typed state words from the records used for merging."""

    stripped = dict(tree)
    for path, data in tree.items():
        document = _document(data) if is_record_path(path) else None
        if document is None:
            continue
        legacy = document.get("status") in LEGACY_STATE_WORDS
        if "writer" not in document and not legacy:
            continue
        document.pop("writer", None)
        if legacy:
            document.pop("status")
        stripped[path] = canonical_text(document).encode("utf-8")
    return stripped


@dataclass(frozen=True)
class ProductWrite:
    """The product's stamp for this merge and the history file that names its revisions."""

    stamp: dict[str, Any]
    history: str
    new_history: dict[str, Any]
    crossing_owner: str


@dataclass(frozen=True)
class MergeWrites:
    """Revisions the merge wrote and revision numbers whose contents the merge distinguished."""

    combined: list[WrittenRevision]
    shared: list[SharedRevision]
    selected: list[CrossingSelection]


def _source_states(files: Mapping[str, bytes], matches: ReviewContentMatch) -> RevisionStates:
    histories: list[HistoryFile] = []
    converted = None
    for path, data in files.items():
        if not path.startswith(f"{KNOWLEDGE_ROOT}/history/"):
            continue
        try:
            if path == CONVERTED_REVIEWS_PATH:
                converted = ConvertedReviews.model_validate_json(data)
            else:
                histories.append(parse_history_document(path, data.decode("utf-8")))
        except ValueError:
            continue  # An unrecognized history file supplies no writer evidence.
    return RevisionStates.of(histories, converted, review_content_matches=matches)


def _crossing_selection(
    selected: dict[str, Any], sources: tuple[RevisionStates, RevisionStates], *, tied: bool
) -> CrossingSelection | None:
    try:
        record = parse_document_text(canonical_text(selected))
    except ValueError:
        return None
    if not isinstance(record, _RECORD_TYPES):
        return None

    selected_state, peer_state = (states.resolve_current(record) for states in sources)
    document_digest = crossing_selection_digest(stored_record_document(record))
    identity = (record.id, record.revision, document_digest)
    own_heads, peer_heads = (states.active_selections(*identity) for states in sources)
    if tied and own_heads.keys() == peer_heads.keys():
        # The two lines share the same selection history. Their merged facts settle the tie.
        return None

    heads = own_heads.keys() | peer_heads.keys()
    excluded = {
        fact
        for head in (*own_heads.values(), *peer_heads.values())
        for fact in head.excluded_reviews
    }
    if tied:
        excluded -= selected_state.reviews | peer_state.reviews
    else:
        excluded = (excluded | peer_state.reviews) - selected_state.reviews
    writer = selected_state.writer
    if tied and writer is None:
        writer = peer_state.writer
    if writer == peer_state.writer and not excluded and not heads:
        return None
    document: dict[str, Any] = {
        "record": record.id,
        "revision": record.revision,
        "documentDigest": document_digest,
    }
    if writer is not None:
        document["writer"] = writer
    if excluded:
        document["excludedReviews"] = sorted(excluded)
    if heads:
        document["supersedes"] = sorted(heads)
    return CrossingSelection.model_validate(document)


def _leaf_history(owner: str, files: Mapping[str, bytes | None]) -> tuple[str, dict[str, Any]]:
    closed = {
        attempt: is_closed_history(files.get(path))
        for path in files
        if (attempt := owner_history_attempt(path, owner)) is not None and files[path] is not None
    }
    attempt = writable_attempt(closed)
    return history_path(owner, attempt), {
        "schema": HISTORY_SCHEMA,
        "leaf": owner,
        **({"attempt": attempt} if attempt > 1 else {}),
        "closed": False,
        "rows": [],
    }


def product_write(
    kind: Literal["leaf", "master"],
    owner_id: str,
    crossing_owner: str,
    own: Mapping[str, bytes],
    merged_at: str,
) -> ProductWrite:
    """The stamp of a revision this merge combines, and where its ``written`` row stands.

    A leaf's revision is named in the leaf's writable attempt on its own line: the latest attempt
    while it is open, the next one once it is closed. A master line's is named in the crossing file
    ``crossing_owner``, which the sync closes in the merge commit.
    """

    if kind == "leaf":
        match = _LEAF_SUFFIX.match(owner_id)
        task = match["task"] if match else owner_id
        owner_field = {"leaf": owner_id}
        path, seed = _leaf_history(owner_id, own)
    else:
        task = owner_id
        owner_field = {"crossing": crossing_owner}
        path = history_path(crossing_owner)
        seed = {"schema": HISTORY_SCHEMA, "crossing": crossing_owner, "closed": False, "rows": []}
    stamp = WriterStamp.model_validate(
        {
            "by": "product",
            "role": PRODUCT_AGENT,
            "harness": PRODUCT_AGENT,
            "model": NO_MODEL,
            "effort": NO_MODEL,
            "seat": PRODUCT_AGENT,
            "task": task,
            **owner_field,
            "at": merged_at,
            "operation": CROSSING_OPERATION,
        }
    )
    return ProductWrite(
        stamp=stamp.to_document(), history=path, new_history=seed, crossing_owner=crossing_owner
    )


def _shared_revision(
    own: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
    own_writer: Any,
    incoming_writer: Any,
) -> SharedRevision | None:
    """A number both lines hold at different content, proved by the records themselves."""

    if own is None or incoming is None or own == incoming:
        return None
    if own.get("id") != incoming.get("id") or own.get("revision") != incoming.get("revision"):
        return None
    if record_content_digest(own) == record_content_digest(incoming):
        return None
    matching_writer = own_writer if own_writer == incoming_writer else None
    identity = {"record": own["id"], "revision": own["revision"]}
    if matching_writer is not None:
        identity["conflictingWriter"] = matching_writer
    return SharedRevision.model_validate(identity)


def _stamp_combined(
    document: dict[str, Any], path: str, product: ProductWrite | None
) -> WrittenRevision:
    if product is None:
        raise ValueError(
            f"{path} combines both lines' edits, and this merge has no owner or time "
            "to stamp the product's revision with"
        )
    document["writer"] = product.stamp
    return WrittenRevision.model_validate(
        {
            "record": document["id"],
            "revision": document["revision"],
            "writer": product.stamp,
            "contentDigest": record_content_digest(document),
        }
    )


def stamp_merged_records(
    merge: MergeResult,
    converted: Mapping[str, Mapping[str, bytes]],
    stripped: Mapping[str, Mapping[str, bytes]],
    product: ProductWrite | None,
    matches: ReviewContentMatch,
) -> MergeWrites:
    """Give each record the merge stores cleanly its writer and retain evidence of shared numbers.

    ``converted`` and ``stripped`` hold each side (``own``, ``incoming``) with and without its
    records' writers. A conflicted record is left to the curator's write.
    """

    files = merge.files
    conflicted = set(merge.conflicted_paths)
    combined: list[WrittenRevision] = []
    shared: list[SharedRevision] = []
    selected: list[CrossingSelection] = []
    sources: dict[str, RevisionStates] = {}

    def states(side: str) -> RevisionStates:
        if side not in sources:
            sources[side] = _source_states(converted[side], matches)
        return sources[side]

    for path in sorted(files):
        if not is_record_path(path):
            continue
        own = _document(stripped["own"].get(path))
        incoming = _document(stripped["incoming"].get(path))
        own_record = _document(converted["own"].get(path)) or {}
        incoming_record = _document(converted["incoming"].get(path)) or {}
        own_writer, incoming_writer = own_record.get("writer"), incoming_record.get("writer")
        conflict = _shared_revision(own, incoming, own_writer, incoming_writer)
        if conflict is not None:
            shared.append(conflict)
        merged = _document(files[path]) if path not in conflicted else None
        if merged is None:
            continue
        if merged in (own, incoming):
            side, other = ("own", "incoming") if merged == own else ("incoming", "own")
            files[path] = converted[side][path]
            source = own_record if side == "own" else incoming_record
            peer = incoming_record if side == "own" else own_record
            if source.get("status") not in LEGACY_STATE_WORDS and source.get(
                "revision"
            ) == peer.get("revision"):
                selection = _crossing_selection(
                    source,
                    (states(side), states(other)),
                    tied=files[path] == converted[other].get(path),
                )
                if selection is not None:
                    selected.append(selection)
            continue
        combined.append(_stamp_combined(merged, path, product))
        files[path] = canonical_text(merged).encode("utf-8")
    return MergeWrites(combined, shared, selected)


def name_product_revisions(
    files: dict[str, bytes | None], product: ProductWrite, revisions: list[WrittenRevision]
) -> None:
    """Record the revisions the merge wrote in the owner's writable history.

    The file's rows of such a record at that revision or above go: the product is now the last
    writer of the revision, as a second write of one revision renews its stamp.
    """

    if not revisions:
        return
    owner = product.stamp.get("leaf")
    path, seed = (
        _leaf_history(owner, files)
        if isinstance(owner, str)
        else (product.history, product.new_history)
    )
    document = _document(files.get(path)) or dict(seed)
    written = [row for row in document.get("written") or () if isinstance(row, Mapping)]
    for one in revisions:
        written = [
            row
            for row in written
            if row.get("record") != one.record or int(row.get("revision", 0)) < one.revision
        ]
        written.append(one.to_document())
    written.sort(key=lambda row: (str(row.get("record")), int(row.get("revision", 0))))
    document["written"] = written
    files[path] = canonical_text(document).encode("utf-8")


def name_crossing_evidence(
    files: dict[str, bytes | None], crossing: str, writes: MergeWrites
) -> None:
    """Store the crossing's witnesses without changing either side's history or leaf attempts."""

    if not writes.shared and not writes.selected:
        return
    path = history_path(crossing)
    document = _document(files.get(path)) or {
        "schema": HISTORY_SCHEMA,
        "crossing": crossing,
        "closed": False,
        "rows": [],
    }
    if writes.shared:
        document["sharedRevisions"] = [
            one.to_document()
            for one in sorted(writes.shared, key=lambda one: (one.record, one.revision))
        ]
    if writes.selected:
        document["crossingSelections"] = [
            one.to_document()
            for one in sorted(writes.selected, key=lambda one: (one.record, one.revision))
        ]
    files[path] = canonical_text(document).encode("utf-8")


def merge_with_writers(
    base: Mapping[str, bytes],
    own: Mapping[str, bytes],
    incoming: Mapping[str, bytes],
    *,
    repository: Path,
    product: ProductWrite | None,
) -> MergeResult:
    """Merge three converted trees with every record's ``writer`` decided from the merged content."""

    sides = {"own": own, "incoming": incoming}
    stripped = {name: without_writers(tree) for name, tree in (("base", base), *sides.items())}
    merged = merge_trees(
        stripped["base"], stripped["own"], stripped["incoming"], repository=repository
    )
    writes = stamp_merged_records(merged, sides, stripped, product, reviewed_content(repository))
    if product is not None:
        name_product_revisions(merged.files, product, writes.combined)
        name_crossing_evidence(merged.files, product.crossing_owner, writes)
    return merged
