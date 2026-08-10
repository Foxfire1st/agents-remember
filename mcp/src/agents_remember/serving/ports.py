"""The conversation read and control ports; lifecycle/control authority remains elsewhere."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agents_remember.models.conversations.capabilities import (
    ConversationCapabilities,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlSubmission,
    InterruptResult,
    OperationTimeline,
    SubmissionAuthorityDescriptor,
    SubmissionProvenanceBatch,
    SubmissionReceipt,
    WithdrawalResult,
)
from agents_remember.models.conversations.cursors import (
    ActiveEventCursor,
    ActivePageCursor,
    LibraryListCursor,
    LibraryReadCursor,
    NativeResumeTarget,
)
from agents_remember.models.conversations.evidence import (
    EvidencePage,
    NativeEvidencePage,
)
from agents_remember.models.conversations.history import (
    ConversationLibraryPage,
    ConversationPage,
    HistoricalConversationPage,
)
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    ConversationLibraryScope,
    HarnessId,
    NativeConversationRef,
)
from agents_remember.models.conversations.status import (
    ConversationStatus,
)
from agents_remember.models.conversations.stream_events import (
    ConversationEventEnvelope,
)
from agents_remember.models.terminal_catalog import (
    DEFAULT_LIVENESS_HYSTERESIS,
    SeatTurnState,
    TerminalCatalogEntry,
    TerminalCatalogLivenessConfig,
    TerminalLivenessEvidence,
)


class ActiveConversationPort(Protocol):
    """Native-hydrated reads for one already-running exact AR session."""

    async def identify(
        self,
        ar_session_id: str,
        bridge_epoch: str,
    ) -> ActiveConversationRef: ...

    async def page(
        self,
        ref: ActiveConversationRef,
        *,
        before: ActivePageCursor | None,
        limit: int,
    ) -> ConversationPage: ...

    def subscribe(
        self,
        ref: ActiveConversationRef,
        *,
        after: ActiveEventCursor,
    ) -> AsyncIterator[ConversationEventEnvelope]: ...

    async def status(self, ref: ActiveConversationRef) -> ConversationStatus: ...

    async def capabilities(
        self,
        ref: ActiveConversationRef,
    ) -> ConversationCapabilities: ...


class ConversationLibraryPort(Protocol):
    """Read-only dormant native catalog/history access for exactly one harness."""

    @property
    def harness_id(self) -> HarnessId: ...

    async def list(
        self,
        scope: ConversationLibraryScope,
        *,
        cursor: LibraryListCursor | None,
        limit: int,
    ) -> ConversationLibraryPage: ...

    async def read(
        self,
        ref: NativeConversationRef,
        *,
        before: LibraryReadCursor | None,
        limit: int,
    ) -> HistoricalConversationPage: ...

    async def resolve_resume_target(
        self,
        ref: NativeConversationRef,
    ) -> NativeResumeTarget: ...


class ControlSessionLike(Protocol):
    """The catalog-row surface the control socket reads are bound to."""

    @property
    def id(self) -> str: ...
    @property
    def tmux_name(self) -> str: ...
    @property
    def created_at(self) -> str: ...
    @property
    def control_endpoint(self) -> Path | None: ...


class TerminalCatalogPort(Protocol):
    """The catalog surface conversation services and their serving helpers use."""

    @property
    def path(self) -> Path: ...
    def get(self, session_id: str) -> TerminalCatalogEntry | None: ...
    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]: ...
    def upsert(self, entry: TerminalCatalogEntry) -> None: ...
    def mark_attached(self, session_id: str, attached_at: str) -> TerminalCatalogEntry | None: ...
    def mark_exited(self, session_id: str) -> TerminalCatalogEntry | None: ...
    def mark_terminated(
        self, session_id: str, terminated_at: str
    ) -> TerminalCatalogEntry | None: ...
    def mark_landed(
        self,
        session_id: str,
        *,
        at: str,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry | None: ...
    def set_label(self, session_id: str, label: str) -> TerminalCatalogEntry | None: ...
    def active_for_leaf(self, leaf_key: str, *, seat_role: str) -> TerminalCatalogEntry | None: ...
    def mark_retired(
        self,
        session_id: str,
        *,
        at: str,
        by_session: str | None,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry | None: ...
    def record_liveness_probe(
        self,
        session_id: str,
        *,
        alive: bool,
        checked_at: datetime,
        evidence: TerminalLivenessEvidence | None = None,
        hysteresis: TerminalCatalogLivenessConfig = DEFAULT_LIVENESS_HYSTERESIS,
    ) -> TerminalCatalogEntry | None: ...
    def record_turn_state(
        self,
        session_id: str,
        state: SeatTurnState,
        *,
        changed_at: str,
    ) -> TerminalCatalogEntry | None: ...
    def batch(self) -> AbstractContextManager[None]: ...
    def compact(self, *, now: datetime, retain_seconds: float = 86400.0) -> int: ...


class ControlPlanePort(Protocol):
    """The control-plane read/write surface conversation services consume.

    Implemented structurally by the serving control client; conversation
    modules never import the control plane. ``entry`` is the catalog row the
    control socket is bound to.
    """

    def read_snapshot(self, entry: TerminalCatalogEntry) -> AdapterSnapshot: ...

    def read_submission_authority(
        self, entry: TerminalCatalogEntry
    ) -> SubmissionAuthorityDescriptor: ...

    def read_evidence(
        self,
        entry: ControlSessionLike,
        *,
        after_sequence: int = 0,
        limit: int = 500,
        expected_bridge_epoch: str | None = None,
    ) -> EvidencePage: ...

    def read_native_page(
        self,
        entry: ControlSessionLike,
        *,
        cursor: str | None = None,
        limit: int = 200,
        expected_bridge_epoch: str | None = None,
        thread_id: str | None = None,
    ) -> NativeEvidencePage: ...

    def read_transcript(
        self,
        entry: ControlSessionLike,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> tuple[Mapping[str, object], ...]: ...

    def read_submission_provenance(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
    ) -> SubmissionProvenanceBatch: ...

    def interrupt(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        turn_id: str | None = None,
        expected_operation_id: str | None = None,
    ) -> InterruptResult: ...

    def withdraw_submission(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        request_id: str,
    ) -> WithdrawalResult: ...

    def submit(
        self,
        entry: ControlSessionLike,
        text: str,
        submission: ControlSubmission,
    ) -> SubmissionReceipt: ...

    def read_operation_timeline(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        after_sequence: int = 0,
        limit: int = 256,
    ) -> OperationTimeline: ...


__all__ = [
    "ActiveConversationPort",
    "ControlPlanePort",
    "ControlSessionLike",
    "ConversationLibraryPort",
    "TerminalCatalogPort",
]
