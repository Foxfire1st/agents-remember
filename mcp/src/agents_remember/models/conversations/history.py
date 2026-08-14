from __future__ import annotations

from pydantic import Field

from agents_remember.models.conversations.capabilities import (
    ConversationCapabilities,
    HistoryCapabilities,
)
from agents_remember.models.conversations.content import ConversationItem
from agents_remember.models.conversations.cursors import (
    ActiveEventCursor,
    ActivePageCursor,
    LibraryConversationKey,
    LibraryListCursor,
    LibraryReadCursor,
)
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    HarnessId,
    NativeConversationRef,
    NonEmptyText,
    WireModel,
)
from agents_remember.models.conversations.status import ConversationStatus


class ConversationPageWindow(WireModel):
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    older_cursor: ActivePageCursor | None = None
    has_older: bool
    total_items: int | None = Field(default=None, ge=0)


class ConversationPage(WireModel):
    identity: ActiveConversationRef
    items: tuple[ConversationItem, ...]
    page: ConversationPageWindow
    event_cursor: ActiveEventCursor
    hydration_id: NonEmptyText
    status: ConversationStatus
    capabilities: ConversationCapabilities


class ConversationLibraryAgentRow(WireModel):
    """One harness sub-agent conversation grouped under its parent library row.

    Additive and evidence-bound: the agent opens through its own ``conversation_key`` exactly
    like a top-level row. Identity fields are populated only from native evidence — codex
    ``agentNickname``/``agentRole``/``source.subAgent.thread_spawn.agent_path``, claude the
    ``.meta.json`` ``agentType``/``description``/``model`` (``join_key`` = ``toolUseId``). When
    the wire carries none, ``title`` falls back to ``agent <short-id>``, never a fabricated name.
    """

    conversation_key: LibraryConversationKey
    identity_digest: NonEmptyText
    title: NonEmptyText
    agent_path: str | None = None
    nickname: str | None = None
    role: str | None = None
    model: str | None = None
    join_key: str | None = None
    safe_native_id_suffix: str | None = None
    last_activity_at: str | None = None


class ConversationLibraryRow(WireModel):
    conversation_key: LibraryConversationKey
    identity_digest: NonEmptyText
    title: NonEmptyText
    safe_native_id_suffix: str | None = None
    last_activity_at: str | None = None
    capabilities: HistoryCapabilities
    agents: tuple[ConversationLibraryAgentRow, ...] = ()


class ConversationLibraryPageScope(WireModel):
    harness_id: HarnessId
    canonical_project_scope: NonEmptyText
    query_digest: NonEmptyText


class ConversationLibraryPage(WireModel):
    scope: ConversationLibraryPageScope
    rows: tuple[ConversationLibraryRow, ...]
    next_cursor: LibraryListCursor | None
    # Capability honesty: why sub-agent conversations are (partially)
    # unavailable on this page, when they are — the exact native reason, never silently absent.
    agents_note: str | None = None


class HistoricalConversationPage(WireModel):
    ref: NativeConversationRef
    items: tuple[ConversationItem, ...]
    older_cursor: LibraryReadCursor | None
    has_older: bool
    total_items: int | None = Field(default=None, ge=0)
    historical_capabilities: HistoryCapabilities
