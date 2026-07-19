"""Projection store: idempotent item/revision/ordinal state for one session.

The store applies mapper outputs into the normalized projection. It is the
idempotence authority: re-feeding the same evidence (rehydration, replay)
reproduces the identical projection — appends dedupe by native item id,
upserts compare payloads with engine-resolved fields normalized, deltas apply
in revision order, and provenance resolution rebuilds user items exactly once
per source verdict. It holds no IO, no envelope minting, and no cursor
knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.serving.conversation.models import (
    ConversationContentBlock,
    ConversationItem,
    ConversationLane,
    ConversationSource,
    MarkdownBlock,
    ProvenanceProducer,
    TextBlock,
    ThinkingBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.serving.conversation.projectors.common import (
    MappedBlockDelta,
    MappedItem,
    MappedUnknownVendor,
    harness_provenance,
)
from agents_remember.serving.harness_control_models import SubmissionSource

MAX_PENDING_DELTA_ITEMS = 64
"""Delta-before-item buffering is bounded per projection (D2)."""

MAX_PENDING_DELTAS_PER_ITEM = 64

_SOURCE_AUTHORITY: dict[
    SubmissionSource, tuple[ConversationLane, ConversationSource, ProvenanceProducer]
] = {
    "cockpit": ("operator", "cockpit-composer", "operator"),
    "terminal": ("operator", "terminal-controlled", "controlled-terminal"),
    "durable": ("agent-bus", "durable-inbox", "agent-bus"),
}

_NORMALIZED_FIELDS = ("revision", "global_ordinal", "created_at", "updated_at", "provenance")


@dataclass(frozen=True)
class StoreMutation:
    """One applied projection change the projector wraps into an envelope."""

    kind: str  # "append-item" | "upsert-item" | "append-block-delta"
    item: ConversationItem | None = None
    item_id: str | None = None
    block_id: str | None = None
    expected_revision: int | None = None
    next_revision: int | None = None
    delta: str | None = None


@dataclass(frozen=True)
class PageSlice:
    """One assembled page window over the canonical order."""

    items: tuple[ConversationItem, ...]
    older_ordinal: int | None
    has_older: bool
    total_items: int | None


class ProjectionStore:
    """The idempotent per-session projection state."""

    def __init__(self) -> None:
        self._items: dict[str, ConversationItem] = {}
        self._order: list[str] = []
        self._pending_deltas: dict[str, list[MappedBlockDelta]] = {}
        self._provenance_pending: dict[str, str] = {}
        self._provenance_done: set[str] = set()

    @property
    def item_count(self) -> int:
        return len(self._order)

    def items(self) -> tuple[ConversationItem, ...]:
        return tuple(self._items[item_id] for item_id in self._order)

    def pending_request_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                request_id
                for request_id in self._provenance_pending.values()
                if request_id not in self._provenance_done
            )
        )

    def apply_item(self, mapped: MappedItem) -> list[StoreMutation]:
        """Append or upsert one item; identical replays are no-ops.

        Tool-call items converge by scoped block-union: mappers legitimately
        emit partial-block tool items (an invocation first, its result later),
        so an upsert unions blocks by ``block_id`` — the candidate wins per
        shared id and existing sibling blocks survive. Full-item re-maps
        (codex) are identical under union. Other kinds keep whole-item
        replacement semantics.
        """

        candidate = mapped.item
        existing = self._items.get(candidate.item_id)
        if existing is None:
            item = candidate.model_copy(
                update={"revision": 1, "global_ordinal": len(self._order) + 1}
            )
            self._items[item.item_id] = item
            self._order.append(item.item_id)
            self._track_provenance(item)
            tail = self._flush_pending_deltas(item)
            return [StoreMutation(kind="append-item", item=item), *tail]
        if existing.kind == candidate.kind == "tool-call":
            candidate = candidate.model_copy(
                update={"blocks": _union_blocks(existing.blocks, candidate.blocks)}
            )
        comparable_existing = existing.model_copy(
            update={field: None for field in _NORMALIZED_FIELDS}
        )
        comparable_candidate = candidate.model_copy(
            update={field: None for field in _NORMALIZED_FIELDS}
        )
        if comparable_candidate == comparable_existing:
            return self._flush_pending_deltas(existing)
        item = candidate.model_copy(
            update={
                "revision": existing.revision + 1,
                "global_ordinal": existing.global_ordinal,
                "created_at": existing.created_at or candidate.created_at,
                "provenance": existing.provenance,
            }
        )
        self._items[item.item_id] = item
        self._track_provenance(item)
        return [
            StoreMutation(kind="upsert-item", item=item),
            *self._flush_pending_deltas(item),
        ]

    def apply_delta(self, mapped: MappedBlockDelta) -> list[StoreMutation]:
        """Apply one block delta in revision order; buffer delta-before-item."""

        item = self._items.get(mapped.item_id)
        if item is None:
            self._buffer_delta(mapped)
            return []
        block_id = mapped.block_id or self._default_block(item)
        if not self._has_block(item, block_id):
            self._buffer_delta(mapped)
            return []
        mutation = self._apply_delta_to(item, block_id, mapped.delta)
        return [mutation] if mutation is not None else []

    def apply_provenance(
        self,
        request_id: str,
        source: SubmissionSource | None,
    ) -> list[StoreMutation]:
        """Resolve one user item's producer from the provenance batch verdict."""

        self._provenance_done.add(request_id)
        if source is None:
            return []
        lane, conversation_source, producer = _SOURCE_AUTHORITY[source]
        for item_id, pending_request in self._provenance_pending.items():
            if pending_request != request_id:
                continue
            item = self._items[item_id]
            resolved = item.model_copy(
                update={
                    "lane": lane,
                    "source": conversation_source,
                    "revision": item.revision + 1,
                    "provenance": item.provenance.model_copy(
                        update={
                            "strength": "exact",
                            "producer": producer,
                            "reason": None,
                        }
                    ),
                }
            )
            self._items[item_id] = resolved
            return [StoreMutation(kind="upsert-item", item=resolved)]
        return []

    def page(self, *, before_ordinal: int | None, limit: int, total_known: bool) -> PageSlice:
        """Slice one chronological window; never viewport-derived."""

        order = self._order
        if before_ordinal is not None:
            end = max(0, before_ordinal - 1)
            start = max(0, end - limit)
        else:
            end = len(order)
            start = max(0, end - limit)
        window = tuple(self._items[item_id] for item_id in order[start:end])
        return PageSlice(
            items=window,
            older_ordinal=start if start > 0 else None,
            has_older=start > 0,
            total_items=len(order) if total_known else None,
        )

    def _track_provenance(self, item: ConversationItem) -> None:
        if (
            item.role == "user"
            and item.correlation is not None
            and item.correlation.request_id
            and item.provenance.strength != "exact"
        ):
            self._provenance_pending[item.item_id] = item.correlation.request_id

    def _buffer_delta(self, mapped: MappedBlockDelta) -> None:
        if mapped.item_id not in self._pending_deltas:
            if len(self._pending_deltas) >= MAX_PENDING_DELTA_ITEMS:
                self._pending_deltas.pop(next(iter(self._pending_deltas)))
            self._pending_deltas[mapped.item_id] = []
        pending = self._pending_deltas[mapped.item_id]
        if len(pending) < MAX_PENDING_DELTAS_PER_ITEM:
            pending.append(mapped)

    def _flush_pending_deltas(self, item: ConversationItem) -> list[StoreMutation]:
        pending = self._pending_deltas.pop(item.item_id, None)
        if not pending:
            return []
        mutations: list[StoreMutation] = []
        for delta in pending:
            current = self._items[item.item_id]
            block_id = delta.block_id or self._default_block(current)
            if not self._has_block(current, block_id):
                continue
            mutation = self._apply_delta_to(current, block_id, delta.delta)
            if mutation is not None:
                mutations.append(mutation)
        return mutations

    def _apply_delta_to(
        self,
        item: ConversationItem,
        block_id: str,
        delta: str,
    ) -> StoreMutation | None:
        blocks: list[ConversationContentBlock] = []
        replaced = False
        for block in item.blocks:
            if block.block_id != block_id:
                blocks.append(block)
                continue
            replaced = True
            blocks.append(self._append_text(block, delta))
        if not replaced:
            return None
        next_revision = item.revision + 1
        updated = item.model_copy(update={"blocks": tuple(blocks), "revision": next_revision})
        self._items[item.item_id] = updated
        return StoreMutation(
            kind="append-block-delta",
            item_id=item.item_id,
            block_id=block_id,
            expected_revision=item.revision,
            next_revision=next_revision,
            delta=delta,
        )

    @staticmethod
    def _append_text(
        block: ConversationContentBlock,
        delta: str,
    ) -> ConversationContentBlock:
        if isinstance(block, MarkdownBlock):
            return block.model_copy(update={"markdown": block.markdown + delta})
        if isinstance(block, TextBlock):
            return block.model_copy(update={"text": block.text + delta})
        if isinstance(block, ThinkingBlock):
            return block.model_copy(update={"markdown": block.markdown + delta})
        if isinstance(block, ToolOutputBlock):
            text = block.text if block.text is not None else ""
            return block.model_copy(update={"text": text + delta})
        return block

    @staticmethod
    def _has_block(item: ConversationItem, block_id: str) -> bool:
        return any(block.block_id == block_id for block in item.blocks)

    @staticmethod
    def _default_block(item: ConversationItem) -> str:
        if item.kind == "tool-call":
            return "output"
        return "markdown"


def _union_blocks(
    existing: tuple[ConversationContentBlock, ...],
    candidate: tuple[ConversationContentBlock, ...],
) -> tuple[ConversationContentBlock, ...]:
    """Union blocks by ``block_id``; candidate wins per id, order is stable."""

    merged: dict[str, ConversationContentBlock] = {block.block_id: block for block in existing}
    for block in candidate:
        merged[block.block_id] = block
    ordered: list[ConversationContentBlock] = []
    seen: set[str] = set()
    for block in (*existing, *candidate):
        if block.block_id in seen:
            continue
        seen.add(block.block_id)
        ordered.append(merged[block.block_id])
    return tuple(ordered)


def unknown_vendor_item(mapped: MappedUnknownVendor, *, evidence_ref: str) -> MappedItem:
    """Mint one unknown-vendor evidence item; raw payloads stay server-side."""

    return MappedItem(
        item=ConversationItem(
            item_id=mapped.item_id,
            revision=1,
            global_ordinal=1,
            turn_id=mapped.turn_id,
            parent_item_id=mapped.parent_item_id,
            lane="harness",
            source="harness-live" if mapped.live else "native-history",
            provenance=harness_provenance(
                f"unrecognized native shape ({mapped.vendor_type})",
                observed_at=mapped.created_at,
                reason="unknown vendor evidence preserved; semantics never guessed",
            ),
            role=mapped.role,
            kind="unknown-vendor",
            phase="unknown",
            blocks=(
                UnknownVendorBlock(
                    block_id="unknown",
                    vendor_type=mapped.vendor_type,
                    safe_summary=mapped.safe_summary,
                    evidence_ref=evidence_ref,
                ),
            ),
            created_at=mapped.created_at,
            evidence_ref=evidence_ref,
        )
    )


__all__ = ["PageSlice", "ProjectionStore", "StoreMutation", "unknown_vendor_item"]
