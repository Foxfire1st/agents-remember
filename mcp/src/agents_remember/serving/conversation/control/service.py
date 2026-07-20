"""Per-app conversation control service authority (260718-CHATS-L3).

One service per installed :class:`ConversationRuntime`, holding the control
secret, the bounded per-session operation ledgers (interrupt, withdrawal,
recovery, attachment), the cockpit submit journal, and the per-session
serialization locks. Every route resolves the exact session, verifies
``expectedBridgeEpoch`` against the live submission authority, and builds the
native identity through the landed L1 factory seams; the L2E control-plane
client reads (interrupt, operation-timeline, submit-with-assets, withdrawal
recovery) are consumed, never reimplemented.

Ledgers are bounded, reconstructable, and daemon-scoped: records are
semantic-revisioned per (session, epoch) channel, terminal/expired records are
reclaimed on write sweeps, and a daemon restart invalidates every reference
loudly (the same posture as the L1 app-scoped cursor secret).
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.errors import (
    AgentsRememberError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.conversation.active.factories import (
    build_identity,
    current_bridge_epoch,
    live_snapshot,
    resolve_running_entry,
)
from agents_remember.serving.conversation.models import ActiveConversationRef
from agents_remember.serving.conversation.runtime import ConversationRuntime
from agents_remember.serving.harness_control_client import (
    ControlledSession,
    read_operation_timeline,
)
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    OperationTimeline,
    OperationTimelineItem,
)
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

Clock = Callable[[], str]

MAX_CHANNELS_PER_APP = 64
"""Bounded (session, epoch) channel set per app; oldest evicted (D2)."""

MAX_INTERRUPTS_PER_CHANNEL = 64
MAX_WITHDRAWALS_PER_CHANNEL = 64
MAX_RECOVERIES_PER_CHANNEL = 32
MAX_ATTACHMENT_OPS_PER_CHANNEL = 32
MAX_JOURNAL_ENTRIES_PER_CHANNEL = 256
MAX_SUBMITS_PER_CHANNEL = 256

RECOVERY_TTL_SECONDS = 900
"""Bounded withdrawal-recovery lease; expiry deletes recoverable content (D3)."""

STAGED_ASSET_TTL_SECONDS = 900
"""Bounded staged-asset lease; expiry deletes staged bytes (D3)."""


def utc_clock() -> str:
    return datetime.now(UTC).isoformat()


def iso_seconds_after(stamp: str, seconds: int) -> str:
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return datetime.fromtimestamp(moment.timestamp() + seconds, UTC).isoformat()


def iso_expired(stamp: str, *, now: str) -> bool:
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    current = datetime.fromisoformat(now)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current >= moment


class ControlOperationError(AgentsRememberError):
    """Base for typed control-operation failures; each names its wire status/slug."""

    http_status: int = 409
    status_slug: str = "request-conflict"


class OperationNotFoundError(ControlOperationError):
    """The named operation/recovery/attachment is not retained by this authority."""

    http_status = 404
    status_slug = "not-found"


class OperationConflictError(ControlOperationError):
    """A reused request id names a different immutable fingerprint."""

    http_status = 409
    status_slug = "request-conflict"


class CapabilityRefusedError(ControlOperationError):
    """The exact-session capability gate refuses the action (unsupported/unverified)."""

    http_status = 422
    status_slug = "unsupported"


class OperationRejectedError(ControlOperationError):
    """The native/identity authority refused the operation with a terminal detail."""

    http_status = 422
    status_slug = "rejected"


class ControlUnavailableError(ControlOperationError):
    """The control bridge is unavailable before any write; retry with the same id."""

    http_status = 503
    status_slug = "control-unavailable"


@dataclass
class JournalEntry:
    """One cockpit submission's daemon-held content (preview/recovery source)."""

    request_id: str
    text: str
    content_digest: str
    draft_revision: int | None
    asset_ids: tuple[str, ...]
    submitted_at: str


@dataclass
class ControlChannel:
    """The bounded per-(session, epoch) control state; one per live session."""

    interrupts: OrderedDict[str, object] = field(default_factory=OrderedDict)
    withdrawals: OrderedDict[str, object] = field(default_factory=OrderedDict)
    recoveries: OrderedDict[str, object] = field(default_factory=OrderedDict)
    attachments: OrderedDict[str, object] = field(default_factory=OrderedDict)
    journal: OrderedDict[str, JournalEntry] = field(default_factory=OrderedDict)
    submits: OrderedDict[str, tuple[object, object]] = field(default_factory=OrderedDict)
    queue_rows: dict[tuple[str, str, int], tuple[int, str]] = field(default_factory=dict)
    queue_revision: int = 0
    queue_items_key: str = ""
    pending_revision: int = 0
    pending_key: str = ""
    telemetry_revision: int = 0
    telemetry_key: str = ""
    policy_revision: int = 0
    policy_key: str = ""


class ConversationControlService:
    """The per-app control authority behind the control routes."""

    def __init__(self, runtime: ConversationRuntime, *, clock: Clock = utc_clock) -> None:
        self._runtime = runtime
        self._clock = clock
        self._secret = os.urandom(32)
        self._channels: OrderedDict[tuple[str, str], ControlChannel] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def secret(self) -> bytes:
        return self._secret

    @property
    def clock(self) -> Clock:
        return self._clock

    def channel(self, ar_session_id: str, bridge_epoch: str) -> ControlChannel:
        key = (ar_session_id, bridge_epoch)
        channel = self._channels.get(key)
        if channel is None:
            while len(self._channels) >= MAX_CHANNELS_PER_APP:
                self._channels.popitem(last=False)
            channel = ControlChannel()
            self._channels[key] = channel
        else:
            self._channels.move_to_end(key)
        return channel

    def session_lock(self, ar_session_id: str) -> asyncio.Lock:
        """One serializer per session above the L2E replay cache (L2E.4)."""

        lock = self._locks.get(ar_session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[ar_session_id] = lock
        return lock

    def resolve_entry(self, ar_session_id: str) -> TerminalCatalogEntry:
        return resolve_running_entry(self._runtime, ar_session_id)

    async def verify_epoch(self, entry: TerminalCatalogEntry, expected_bridge_epoch: str) -> str:
        actual = await asyncio.to_thread(current_bridge_epoch, entry)
        if actual != expected_bridge_epoch:
            raise HarnessBridgeEpochMismatchError(expected_bridge_epoch, actual)
        return actual

    async def live_snapshot(self, entry: TerminalCatalogEntry) -> AdapterSnapshot:
        try:
            return await asyncio.to_thread(live_snapshot, entry)
        except HarnessControlError:
            raise
        except Exception as exc:  # defensive: factories already type these
            raise HarnessControlError(str(exc)) from exc

    def build_identity(
        self,
        entry: TerminalCatalogEntry,
        *,
        bridge_epoch: str,
        snapshot: AdapterSnapshot,
    ) -> ActiveConversationRef:
        identity, _mapper = build_identity(
            entry, secret=self._secret, bridge_epoch=bridge_epoch, snapshot=snapshot
        )
        return identity

    async def read_full_timeline(
        self,
        entry: ControlledSession,
        *,
        expected_bridge_epoch: str,
    ) -> list[OperationTimelineItem]:
        """The complete retained ledger as one paged union through latestSequence."""

        items: list[OperationTimelineItem] = []
        after = 0
        while True:
            page: OperationTimeline = await asyncio.to_thread(
                read_operation_timeline,
                entry,
                expected_bridge_epoch=expected_bridge_epoch,
                after_sequence=after,
            )
            items.extend(page.items)
            if not page.truncated:
                break
            after = page.items[-1].sequence
        return items

    def spool_assets_root(self, entry: ControlledSession) -> Path:
        """The session's user-private asset spool root (L2E endpoint convention)."""

        endpoint = entry.control_endpoint
        if endpoint is None:
            raise HarnessControlError("catalog session has no protocol control endpoint")
        return endpoint.parent / "assets"


_SERVICES: weakref.WeakKeyDictionary[ConversationRuntime, ConversationControlService] = (
    weakref.WeakKeyDictionary()
)


def conversation_control_service(runtime: ConversationRuntime) -> ConversationControlService:
    """Return the one control service per installed runtime authority."""

    service = _SERVICES.get(runtime)
    if service is None:
        service = ConversationControlService(runtime)
        _SERVICES[runtime] = service
    return service


__all__ = [
    "MAX_ATTACHMENT_OPS_PER_CHANNEL",
    "MAX_INTERRUPTS_PER_CHANNEL",
    "MAX_JOURNAL_ENTRIES_PER_CHANNEL",
    "MAX_RECOVERIES_PER_CHANNEL",
    "MAX_SUBMITS_PER_CHANNEL",
    "MAX_WITHDRAWALS_PER_CHANNEL",
    "RECOVERY_TTL_SECONDS",
    "STAGED_ASSET_TTL_SECONDS",
    "CapabilityRefusedError",
    "ControlChannel",
    "ControlOperationError",
    "ControlUnavailableError",
    "ConversationControlService",
    "JournalEntry",
    "OperationConflictError",
    "OperationNotFoundError",
    "OperationRejectedError",
    "conversation_control_service",
    "iso_expired",
    "iso_seconds_after",
    "utc_clock",
]
