"""The anchor half of the graph: authored source locations, stored exactly as recorded.

Storage and resolution are different concerns. An anchor records the repository-relative path, the
Git object identity the author attributed and the locator shape; it is written and read back
without consulting a filesystem, a Git object or a parser. Whether that location still resolves in
a selected snapshot is a fact a later reader reports, and a missing source is never a reason to
retire a stored attribution.

Removal is therefore explicit and narrow: an anchor is removed by its identity, one at a time, and
never while a stored realization claim still cites it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import records
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    SqliteFailureContext,
    duplicate_anchor_refusal,
    missing_expected_row_refusal,
    referenced_anchor_refusal,
    scope_refusal,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.result import (
    CreateSourceAnchorResult,
    KnowledgeRefusal,
    RemoveSourceAnchorRequest,
    RemoveSourceAnchorResult,
    SourceAnchorRequest,
)
from agents_remember.models.knowledge.source import SourceAnchor, SourceAnchorDraft

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


_ANCHOR_COLUMNS = "repository_id, anchor_id, path, source_identity, locator, provenance"

_ANCHOR_INSERT = (
    "INSERT INTO source_anchor (repository_id, anchor_id, path, source_identity, locator, "
    "provenance) VALUES (?, ?, ?, ?, ?, ?)"
)


def create_source_anchor(
    store: OpenedKnowledgeStore, request: SourceAnchorRequest
) -> CreateSourceAnchorResult:
    """Insert one source anchor exactly as authored, without resolving its target."""

    denied = scope_refusal("create_source_anchor", store.repository_id, request.repository_id)
    if denied is not None:
        return _anchor_refusal(request, denied)
    with store.exclusive_candidate_lock("create_source_anchor") as lock_refusal:
        if lock_refusal is not None:
            return _anchor_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _insert_anchor(store, request),
            on_refusal=lambda refused: _anchor_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="create_source_anchor",
                table="source_anchor",
                record_id=str(request.anchor.anchor_id),
            ),
        )


def _insert_anchor(
    store: OpenedKnowledgeStore, request: SourceAnchorRequest
) -> CreateSourceAnchorResult:
    anchor = source_anchor_from_draft(request.anchor, request.provenance)
    existing = get_anchor(store, str(anchor.anchor_id))
    if existing is not None and existing == anchor:
        return _anchor_result(request, "no_change")
    insert_anchor_row(store, anchor)
    return _anchor_result(request, "created")


def source_anchor_from_draft(draft: SourceAnchorDraft, provenance: Authorship) -> SourceAnchor:
    """Return the stored anchor one authored draft describes, with its provenance attached.

    One construction owner: a realization claim that records a new anchor in its own transaction
    builds the same value through this function, so the two write paths cannot store subtly
    different anchors from the same draft.
    """

    return SourceAnchor(
        anchor_id=draft.anchor_id,
        path=draft.path,
        source_identity=draft.source_identity,
        locator=draft.locator,
        provenance=provenance,
    )


def insert_anchor_row(store: OpenedKnowledgeStore, anchor: SourceAnchor) -> None:
    """Write one anchor row inside the caller's open transaction.

    Raises :class:`KnowledgeRefused` when the identity is already stored with a different payload:
    an anchor records the location the author attributed, so silently accepting a different one
    under the same identity would serve a location nobody authored.
    """

    existing = get_anchor(store, str(anchor.anchor_id))
    if existing is not None:
        if existing == anchor:
            return
        raise KnowledgeRefused(
            duplicate_anchor_refusal(
                str(anchor.anchor_id),
                records.anchor_row_digest(existing, store.repository_id),
                records.anchor_row_digest(anchor, store.repository_id),
            )
        )
    store.write(_ANCHOR_INSERT, records.anchor_row(anchor, store.repository_id))


def find_claim_citing_anchor(store: OpenedKnowledgeStore, anchor_id: str) -> str | None:
    """Return the first stored claim identity that cites this anchor, in stable order.

    The question belongs to the anchor's lifetime -- may this anchor be removed -- and
    ``realization_claim`` is the only table that can answer it, so the one query lives here rather
    than in the module that owns the claim rows.
    """

    row = fetch_one(
        store.connection,
        "SELECT claim_id FROM realization_claim WHERE repository_id = ? AND anchor_id = ? "
        "ORDER BY claim_id",
        (store.repository_id, anchor_id),
    )
    return None if row is None else str(row[0])


def get_anchor(store: OpenedKnowledgeStore, anchor_id: str) -> SourceAnchor | None:
    """Return one stored anchor exactly as authored, or ``None`` when it is not stored.

    Reading an anchor resolves nothing: no path, blob or locator is checked against a source
    snapshot here, so an anchor whose target no longer exists still reads back in full.
    """

    row = fetch_one(
        store.connection,
        f"SELECT {_ANCHOR_COLUMNS} FROM source_anchor WHERE repository_id = ? AND anchor_id = ?",
        (store.repository_id, anchor_id),
    )
    return None if row is None else records.decode_anchor_row(row)


def remove_source_anchor(
    store: OpenedKnowledgeStore, request: RemoveSourceAnchorRequest
) -> RemoveSourceAnchorResult:
    """Remove one anchor that no stored realization claim cites."""

    denied = scope_refusal("remove_source_anchor", store.repository_id, request.repository_id)
    if denied is not None:
        return _anchor_removal_refusal(request, denied)
    with store.exclusive_candidate_lock("remove_source_anchor") as lock_refusal:
        if lock_refusal is not None:
            return _anchor_removal_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _delete_anchor(store, request),
            on_refusal=lambda refused: _anchor_removal_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="remove_source_anchor",
                table="source_anchor",
                record_id=request.anchor_id,
            ),
        )


def _delete_anchor(
    store: OpenedKnowledgeStore, request: RemoveSourceAnchorRequest
) -> RemoveSourceAnchorResult:
    delete_anchor(store, request.anchor_id)
    return RemoveSourceAnchorResult(
        state="removed", repository_id=request.repository_id, anchor_id=request.anchor_id
    )


def delete_anchor(store: OpenedKnowledgeStore, anchor_id: str) -> None:
    """Remove one unreferenced anchor inside the caller's open transaction.

    Raises :class:`KnowledgeRefused` when the anchor is not stored, or while a stored realization
    claim still cites it: the claim is what the anchor was recorded for, so removing the location
    under it would leave the claim pointing at nothing.
    """

    if get_anchor(store, anchor_id) is None:
        raise KnowledgeRefused(
            missing_expected_row_refusal(
                operation="remove_source_anchor",
                table="source_anchor",
                record_id=anchor_id,
            )
        )
    citing = find_claim_citing_anchor(store, anchor_id)
    if citing is not None:
        raise KnowledgeRefused(referenced_anchor_refusal(anchor_id, citing))
    store.write(
        "DELETE FROM source_anchor WHERE repository_id = ? AND anchor_id = ?",
        (store.repository_id, anchor_id),
    )


def _anchor_result(
    request: SourceAnchorRequest,
    state: Literal["created", "no_change"],
) -> CreateSourceAnchorResult:
    return CreateSourceAnchorResult(
        state=state,
        repository_id=request.repository_id,
        anchor_id=str(request.anchor.anchor_id),
        stored=state == "created",
    )


def _anchor_refusal(
    request: SourceAnchorRequest, refused: KnowledgeRefusal
) -> CreateSourceAnchorResult:
    return CreateSourceAnchorResult(
        state="refused",
        repository_id=request.repository_id,
        anchor_id=str(request.anchor.anchor_id),
        stored=False,
        refusal=refused,
    )


def _anchor_removal_refusal(
    request: RemoveSourceAnchorRequest, refused: KnowledgeRefusal
) -> RemoveSourceAnchorResult:
    return RemoveSourceAnchorResult(
        state="refused",
        repository_id=request.repository_id,
        anchor_id=request.anchor_id,
        refusal=refused,
    )


__all__ = [
    "create_source_anchor",
    "delete_anchor",
    "find_claim_citing_anchor",
    "get_anchor",
    "insert_anchor_row",
    "remove_source_anchor",
    "source_anchor_from_draft",
]
