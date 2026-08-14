"""What one submission authority retains about its operations, and what it can say about them.

The ledger is the authority's memory: every ordinary operation ever admitted at this bridge epoch,
in admission order, bounded by one eviction rule -- terminal rows go first and a live or queued row
is never dropped. Reads that answer for a request id (status, provenance, the paged timeline) come
from here, so a caller asking about a submission and the dispatcher advancing it are looking at the
same rows.

LOCKING, which a caller must know before touching anything here. The ledger does not own its lock;
it is handed the authority's, because the retained rows, the dispatch timeline and the active
operation are one consistency domain and one lock is what keeps them so. The three ``async`` reads
acquire that lock themselves. Every other method is synchronous and MUST be called with the lock
already held -- ``asyncio.Lock`` is not reentrant, so calling one from inside a read deadlocks.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from agents_remember.errors import HarnessBridgeEpochMismatchError, HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
    AssetReference,
    ControlOperationKind,
    ControlOperationRef,
    OperationTimeline,
    OperationTimelineItem,
    SubmissionLifecycleState,
    SubmissionProvenance,
    SubmissionProvenanceBatch,
    SubmissionReceipt,
    SubmissionSource,
    operation_timeline_item_wire_bytes,
)
from agents_remember.models.conversations.evidence import (
    EVIDENCE_PAGE_BYTE_BUDGET,
)
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_models import (
    MAX_OPERATION_TIMELINE_PAGE,
    ReconciliationResult,
    ReconciliationState,
    SubmissionLookup,
    SubmissionStatus,
    SubmissionStatusBatch,
)

OperationKey = tuple[ControlOperationKind, str]

MAX_LOOKUP_REQUEST_IDS = 64

EVICTABLE_STATES: frozenset[SubmissionLifecycleState] = frozenset(
    {"delivered", "withdrawn", "rejected", "unsupported"}
)


@dataclass
class OperationRecord:
    """One ordinary operation's whole life: its state, its evidence, and what it answers with.

    Mutable and shared -- the dispatcher advances the same object a status read projects -- so every
    transition below runs under the authority's lock. The projections are pure and take the bridge
    epoch rather than storing it, because the epoch belongs to the authority that minted the ref.
    """

    ref: ControlOperationRef
    state: SubmissionLifecycleState
    submitted_at: str
    updated_at: str
    source: str | None = None
    payload_digest: str | None = None
    text: str | None = None
    requested_value: str | None = None
    accepted_at: str | None = None
    vendor_correlation_id: str | None = None
    detail: str | None = None
    buffered_completion_sequence: int | None = None
    result_future: asyncio.Future[object] | None = None
    assets: tuple[AssetReference, ...] = ()

    @property
    def key(self) -> OperationKey:
        return (self.ref.kind, self.ref.operation_id)

    @property
    def live(self) -> bool:
        return self.state in {"queued", "dispatching", "unknown"}

    def resolve(self, value: object) -> None:
        """Hand the waiting submitter its result, if one is still waiting."""

        future = self.result_future
        if future is not None and not future.done():
            future.set_result(value)

    def mark_terminal(self, state: SubmissionLifecycleState, detail: str | None, at: str) -> None:
        self.state = state
        self.detail = detail
        self.updated_at = at
        if state == "delivered" and self.accepted_at is None:
            self.accepted_at = at
        # Terminal tombstones retain the digest and lifecycle metadata, never full prompt text.
        self.text = None
        self.assets = ()

    def mark_unknown(
        self,
        detail: str,
        vendor_correlation_id: str | None,
        *,
        at: str,
        bridge_epoch: str,
    ) -> None:
        """Install the ambiguity barrier and settle whoever is waiting on this operation."""

        self.state = "unknown"
        self.detail = detail
        self.vendor_correlation_id = vendor_correlation_id
        self.updated_at = at
        if self.ref.kind == "prompt":
            self.resolve(self.receipt("unknown", bridge_epoch=bridge_epoch))
        elif self.requested_value is not None:
            self.resolve(
                SetResult(
                    ok=False,
                    acceptance="unknown",
                    requested_value=self.requested_value,
                    detail=detail,
                )
            )

    def receipt(
        self,
        acceptance: str,
        *,
        bridge_epoch: str,
        detail: str | None = None,
    ) -> SubmissionReceipt:
        raw: dict[str, object] = {}
        if self.assets:
            # Additive native-acceptance evidence; asset-free receipts keep raw={} byte-identical.
            raw["assetIds"] = [asset.asset_id for asset in self.assets]
        return SubmissionReceipt(
            request_id=self.ref.operation_id,
            acceptance=cast(AcceptanceState, acceptance),
            submitted_at=self.submitted_at,
            vendor_correlation_id=self.vendor_correlation_id,
            accepted_at=self.accepted_at,
            detail=self.detail if detail is None else detail,
            raw=raw,
            bridge_epoch=bridge_epoch,
        )

    def duplicate_receipt(self, bridge_epoch: str) -> SubmissionReceipt:
        """The receipt a re-submission of this same request id and payload is owed."""

        acceptance = {
            "queued": "queued",
            "dispatching": "unknown",
            "delivered": "immediate",
            "withdrawn": "rejected",
            "unknown": "unknown",
            "rejected": "rejected",
            "unsupported": "unsupported",
        }[self.state]
        detail = self.detail
        if self.state == "dispatching":
            detail = "dispatch is in flight; query status/reconcile with the same request id"
        elif self.state == "withdrawn":
            detail = "the queued submission was withdrawn and will not be dispatched"
        return self.receipt(acceptance, bridge_epoch=bridge_epoch, detail=detail)

    def pending_dispatch_receipt(self, bridge_epoch: str) -> SubmissionReceipt:
        """Receipt for a live record whose acceptance evidence outlasted the dispatch grace.

        "queued" is the honest synchronous word for a not-yet-vendor-verified submission: the
        record stays live on the timeline, and status/reconcile project acceptance when the echo
        lands.  A record that raced into a terminal or ambiguous state during the grace reports
        its exact state through the duplicate mapping instead.
        """

        if self.state == "dispatching":
            return self.receipt(
                "queued",
                bridge_epoch=bridge_epoch,
                detail=(
                    "dispatch is in flight; acceptance evidence is pending — "
                    "query status/reconcile with the same request id"
                ),
            )
        if self.state == "queued":
            return self.receipt("queued", bridge_epoch=bridge_epoch)
        return self.duplicate_receipt(bridge_epoch)

    def reconciliation(self, bridge_epoch: str) -> ReconciliationResult | None:
        """What this record already proves about delivery; ``None`` while it is ambiguous."""

        if self.state == "unknown":
            return None
        state: ReconciliationState
        if self.state in {"queued", "dispatching", "delivered"}:
            state = "accepted"
        elif self.state in {"withdrawn", "rejected"}:
            state = "rejected"
        else:
            state = "unsupported"
        return ReconciliationResult(
            request_id=self.ref.operation_id,
            state=state,
            reconciled_at=self.accepted_at or self.updated_at,
            vendor_correlation_id=self.vendor_correlation_id,
            detail=self.detail,
            bridge_epoch=bridge_epoch,
            submission_state=self.state,
        )

    def status(self) -> SubmissionStatus:
        return SubmissionStatus(
            request_id=self.ref.operation_id,
            state=self.state,
            submitted_at=self.submitted_at,
            updated_at=self.updated_at,
            accepted_at=self.accepted_at,
            withdrawable=self.state == "queued",
            detail=self.detail,
        )

    def provenance(self) -> SubmissionProvenance:
        return SubmissionProvenance(
            request_id=self.ref.operation_id,
            outcome="found",
            source=cast(SubmissionSource | None, self.source),
            state=self.state,
            submitted_at=self.submitted_at,
            updated_at=self.updated_at,
            accepted_at=self.accepted_at,
            vendor_correlation_id=self.vendor_correlation_id,
        )

    def timeline_item(self) -> OperationTimelineItem:
        return OperationTimelineItem(
            operation_id=self.ref.operation_id,
            kind=self.ref.kind,
            source=cast(SubmissionSource | None, self.source),
            state=self.state,
            sequence=self.ref.sequence,
            submitted_at=self.submitted_at,
            updated_at=self.updated_at,
            accepted_at=self.accepted_at,
            payload_digest_present=self.payload_digest is not None,
            vendor_correlation_id=self.vendor_correlation_id,
        )


class SubmissionLedger:
    """The bounded, epoch-stamped record store one submission authority admits into and reads from.

    Bounded means a submission can be forgotten, and the ledger says so rather than pretending
    completeness: ``operation_timeline`` reports ``evicted_before_sequence``, and a request id past
    it answers ``not-found``.
    """

    def __init__(self, *, bridge_epoch: str, limit: int, lock: asyncio.Lock) -> None:
        self._bridge_epoch = bridge_epoch
        self._limit = limit
        self._lock = lock
        self._records: OrderedDict[OperationKey, OperationRecord] = OrderedDict()
        self._prompt_ids: dict[str, OperationKey] = {}
        self._sequence = 0
        self._evicted_before_sequence = 0

    @property
    def retained_record_count(self) -> int:
        return len(self._records)

    def records(self) -> tuple[OperationRecord, ...]:
        """Every retained record in admission order, as a snapshot safe to iterate."""

        return tuple(self._records.values())

    def by_key(self, key: OperationKey) -> OperationRecord | None:
        return self._records.get(key)

    def by_request_id(self, request_id: str) -> OperationRecord | None:
        key = self._prompt_ids.get(request_id)
        return self._records.get(key) if key is not None else None

    def next_ref(
        self, kind: ControlOperationKind, operation_id: str | None = None
    ) -> ControlOperationRef:
        """Mint the next ordinal reference; a setter carries no caller id and is named by ordinal."""

        self._sequence += 1
        return ControlOperationRef(
            bridge_epoch=self._bridge_epoch,
            sequence=self._sequence,
            operation_id=operation_id or f"{self._bridge_epoch}:{self._sequence}:{kind}",
            kind=kind,
        )

    def enrol(self, record: OperationRecord) -> None:
        """Retain one admitted operation, indexing prompts by the request id their caller owns."""

        self._records[record.key] = record
        if record.ref.kind == "prompt":
            self._prompt_ids[record.ref.operation_id] = record.key

    def touch(self, record: OperationRecord) -> None:
        """Move a record to the young end so eviction reaches the least recently answered first."""

        self._records.move_to_end(record.key)

    def live_count(self, active: OperationKey | None) -> int:
        return sum(1 for record in self._records.values() if record.live or record.key == active)

    def make_room(self, pinned: Callable[[OperationKey], bool]) -> bool:
        """Evict terminal rows until one more fits; ``False`` when nothing may be dropped.

        ``pinned`` answers for the caller's own dispatch state -- the active operation and anything
        still on the timeline -- which the ledger deliberately does not track.
        """

        while len(self._records) >= self._limit:
            evictable = next(
                (
                    key
                    for key, record in self._records.items()
                    if not pinned(key) and record.state in EVICTABLE_STATES
                ),
                None,
            )
            if evictable is None:
                return False
            record = self._records.pop(evictable)
            self._evicted_before_sequence = max(self._evicted_before_sequence, record.ref.sequence)
            if record.ref.kind == "prompt":
                self._prompt_ids.pop(record.ref.operation_id, None)
        return True

    async def status(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
        *,
        cockpit_only: bool,
    ) -> SubmissionStatusBatch:
        self._require_epoch(expected_bridge_epoch)
        self._require_lookup_ids("submission status", request_ids)
        async with self._lock:
            lookups: list[SubmissionLookup] = []
            for request_id in request_ids:
                record = self.by_request_id(request_id)
                if record is None or (cockpit_only and record.source != "cockpit"):
                    lookups.append(SubmissionLookup(request_id=request_id, outcome="not-found"))
                    continue
                lookups.append(
                    SubmissionLookup(
                        request_id=request_id,
                        outcome="found",
                        submission=record.status(),
                    )
                )
            return SubmissionStatusBatch(
                bridge_epoch=self._bridge_epoch,
                submissions=tuple(lookups),
            )

    async def provenance(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
    ) -> SubmissionProvenanceBatch:
        """Read-only provenance batch across every source; never origin-filtered."""

        self._require_epoch(expected_bridge_epoch)
        self._require_lookup_ids("submission provenance", request_ids)
        async with self._lock:
            provenance: list[SubmissionProvenance] = []
            for request_id in request_ids:
                record = self.by_request_id(request_id)
                if record is None:
                    provenance.append(
                        SubmissionProvenance(request_id=request_id, outcome="not-found")
                    )
                    continue
                provenance.append(record.provenance())
            return SubmissionProvenanceBatch(
                bridge_epoch=self._bridge_epoch,
                provenance=tuple(provenance),
            )

    async def operation_timeline(
        self,
        expected_bridge_epoch: str,
        *,
        after_sequence: int = 0,
        limit: int = MAX_OPERATION_TIMELINE_PAGE,
        byte_budget: int = EVIDENCE_PAGE_BYTE_BUDGET,
    ) -> OperationTimeline:
        """Page the retained ledger, never bodies; completeness is the union of pages."""

        self._require_epoch(expected_bridge_epoch)
        if limit < 1 or byte_budget < 1:
            raise HarnessControlError("operation timeline requires positive limit and byte budget")
        bounded = min(MAX_OPERATION_TIMELINE_PAGE, limit)
        async with self._lock:
            items: list[OperationTimelineItem] = []
            used = 0
            truncated = False
            for record in sorted(self._records.values(), key=lambda item: item.ref.sequence):
                if record.ref.sequence <= after_sequence:
                    continue
                item = record.timeline_item()
                size = operation_timeline_item_wire_bytes(item)
                if len(items) >= bounded or (items and used + size > byte_budget):
                    truncated = True
                    break
                items.append(item)
                used += size
            return OperationTimeline(
                bridge_epoch=self._bridge_epoch,
                latest_sequence=self._sequence,
                evicted_before_sequence=self._evicted_before_sequence,
                truncated=truncated,
                items=tuple(items),
            )

    def _require_epoch(self, expected: str) -> None:
        if expected != self._bridge_epoch:
            raise HarnessBridgeEpochMismatchError(expected, self._bridge_epoch)

    @staticmethod
    def _require_lookup_ids(subject: str, request_ids: tuple[str, ...]) -> None:
        if not 1 <= len(request_ids) <= MAX_LOOKUP_REQUEST_IDS:
            raise HarnessControlError(f"{subject} requires 1..{MAX_LOOKUP_REQUEST_IDS} request ids")
        if len(set(request_ids)) != len(request_ids):
            raise HarnessControlError(f"{subject} request ids must be unique")


def ref_key(ref: ControlOperationRef) -> OperationKey:
    return (ref.kind, ref.operation_id)
