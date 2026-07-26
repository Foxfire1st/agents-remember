"""Claude dormant native conversation library through the locked helper (260718-CHATS-L2).

Every operation runs through the repository-owned locked helper
(``@anthropic-ai/claude-agent-sdk`` ``listSessions`` / ``getSessionMessages`` /
``getSessionInfo``) via :class:`ConversationLibraryHelperHost`. The helper handshake on every
spawn reports the observed runtime/helper versions as informational evidence only — THE CONTRACT IS
THE ONLY GATE (developer ruling 2026-07-21, 260718-CHATS-L5F R4): the native ``list``/``read``
operation succeeding is the proof; a version drift never demotes the surface.

Claude history is honestly ``partial``: the SDK rebuilds chronological user/assistant chains,
and thinking/tool/permission records appear only where the installed history persists them.
Unknown content blocks become explicit ``unknown-vendor`` evidence, never guessed Markdown.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from agents_remember.serving.conversation.library.cursor import LibraryCursorAuthority
from agents_remember.serving.conversation.library.errors import (
    CatalogGenerationError,
    InvalidLibraryCursorError,
    LibraryStoreError,
)
from agents_remember.serving.conversation.library.helper_host import (
    ConversationLibraryHelperHost,
)
from agents_remember.serving.conversation.library.normalize_common import (
    capped_text as _capped,
)
from agents_remember.serving.conversation.library.normalize_common import (
    first_text as _first_text,
)
from agents_remember.serving.conversation.library.normalize_common import (
    native_provenance,
    required_field,
    text_content_parts,
)
from agents_remember.serving.conversation.library.scope import query_digest
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    ConversationContentBlock,
    ConversationCorrelation,
    ConversationItem,
    ConversationLibraryAgentRow,
    ConversationLibraryPage,
    ConversationLibraryPageScope,
    ConversationLibraryRow,
    ConversationLibraryScope,
    HarnessId,
    HistoricalConversationPage,
    HistoryCapabilities,
    LibraryListCursor,
    LibraryReadCursor,
    MarkdownBlock,
    NativeConversationRef,
    NativeResumeTarget,
    ProvenanceEvidence,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)

_Capabilities = Callable[[HarnessId], Awaitable[HistoryCapabilities]]

# Sub-agent conversations: the library's own composite vendor id grammar,
# ``<sessionId>/<agentId>``, minted only by this port (session ids and agent ids never contain
# "/"). Opening an agent conversation reads ``subagents/agent-<agentId>.jsonl`` through the
# locked helper; the ``.meta.json`` ``toolUseId`` is the join key to the spawning tool call.
_AGENT_ID_SEPARATOR = "/"
_AGENTS_UNAVAILABLE_NOTE = (
    "sub-agent conversations are unavailable: the locked helper returned no sub-agent evidence"
)


class ClaudeConversationLibrary:
    """The dormant Claude library port: helper-backed list/read/resolve, no local index."""

    harness_id: HarnessId = "claude"

    def __init__(
        self,
        *,
        authorization: AuthorizationBinding,
        cursor_authority: LibraryCursorAuthority,
        capabilities: _Capabilities,
        helper_host: ConversationLibraryHelperHost,
    ) -> None:
        self._authorization = authorization
        self._cursor_authority = cursor_authority
        self._capabilities = capabilities
        self._helper = helper_host

    async def list(
        self,
        scope: ConversationLibraryScope,
        *,
        cursor: LibraryListCursor | None,
        limit: int,
    ) -> ConversationLibraryPage:
        native_cursor, expected_generation = self._verify_list_position(cursor, scope)
        result, _runtime, _helper = await self._helper.call(
            "claude",
            "list",
            {
                "canonicalProjectScope": scope.canonical_project_scope,
                "cursor": native_cursor,
                "limit": limit,
            },
        )
        generation = self._cursor_authority.catalog_generation(
            required_field(result, "signature", source="Claude helper")
        )
        if expected_generation is not None and expected_generation != generation:
            raise CatalogGenerationError(
                "the native Claude catalog changed; the list cursor is reset"
            )
        capabilities = await self._capabilities(self.harness_id)
        rows, agents_note = self._rows(result, scope, generation=generation, capabilities=capabilities)
        next_cursor = self._next_cursor(result, scope, generation)
        return ConversationLibraryPage(
            scope=ConversationLibraryPageScope(
                harness_id=self.harness_id,
                canonical_project_scope=scope.canonical_project_scope,
                query_digest=scope.query_digest,
            ),
            rows=rows,
            next_cursor=next_cursor,
            agents_note=agents_note,
        )

    async def read(
        self,
        ref: NativeConversationRef,
        *,
        before: LibraryReadCursor | None,
        limit: int,
    ) -> HistoricalConversationPage:
        native_cursor, expected_generation = self._verify_read_position(before, ref)
        session_id, agent_id = _split_agent_vendor_id(ref.vendor_conversation_id)
        payload: dict[str, object] = {
            "vendorConversationId": session_id,
            "expectedIdentityDigest": ref.identity_digest,
            "canonicalProjectScope": ref.project_scope,
            "cursor": native_cursor,
            "limit": limit,
        }
        if agent_id is not None:
            payload["agentId"] = agent_id
        result, _runtime, _helper = await self._helper.call("claude", "read", payload)
        generation = self._cursor_authority.catalog_generation(
            required_field(result, "signature", source="Claude helper")
        )
        if expected_generation is not None and expected_generation != generation:
            raise CatalogGenerationError(
                "the native Claude conversation changed; the read cursor is reset"
            )
        items, older_cursor, has_older, total = self._read_page(result, ref, generation)
        return HistoricalConversationPage(
            ref=ref,
            items=items,
            older_cursor=older_cursor,
            has_older=has_older,
            total_items=total,
            historical_capabilities=await self._capabilities(self.harness_id),
        )

    async def resolve_resume_target(self, ref: NativeConversationRef) -> NativeResumeTarget:
        _session_id, agent_id = _split_agent_vendor_id(ref.vendor_conversation_id)
        if agent_id is not None:
            raise LibraryStoreError(
                "Claude sub-agent transcripts have no native resume target"
            )
        result, _runtime, _helper = await self._helper.call(
            "claude",
            "resolve-resume-target",
            {
                "vendorConversationId": ref.vendor_conversation_id,
                "expectedIdentityDigest": ref.identity_digest,
                "canonicalProjectScope": ref.project_scope,
            },
        )
        resolved_id = required_field(result, "vendorConversationId", source="Claude helper")
        if resolved_id != ref.vendor_conversation_id:
            raise LibraryStoreError("Claude helper resolved a different native identity")
        generation = self._cursor_authority.catalog_generation(
            f"claude:resolve:{ref.project_scope}:{ref.vendor_conversation_id}"
        )
        return self._cursor_authority.mint_resume_target(
            self._scope_for_ref(ref),
            vendor_conversation_id=ref.vendor_conversation_id,
            identity_digest=ref.identity_digest,
            catalog_generation=generation,
            launch={"kind": "argv", "args": ["--resume", ref.vendor_conversation_id]},
        )

    # -- internals ----------------------------------------------------------

    def _verify_list_position(
        self,
        cursor: LibraryListCursor | None,
        scope: ConversationLibraryScope,
    ) -> tuple[str | None, int | None]:
        if cursor is None:
            return None, None
        binding, position = self._cursor_authority.verify_list_cursor(cursor)
        if binding.scope != scope:
            raise InvalidLibraryCursorError(
                "library cursor does not match this harness/scope/query"
            )
        if not isinstance(position, str):
            raise InvalidLibraryCursorError("Claude list cursor position must be text")
        return position, binding.catalog_generation

    def _verify_read_position(
        self,
        before: LibraryReadCursor | None,
        ref: NativeConversationRef,
    ) -> tuple[str | None, int | None]:
        if before is None:
            return None, None
        binding, position = self._cursor_authority.verify_read_cursor(before)
        if binding.scope != self._scope_for_ref(ref):
            raise InvalidLibraryCursorError(
                "library read cursor does not match this conversation scope"
            )
        if not isinstance(position, str):
            raise InvalidLibraryCursorError("Claude read cursor position must be text")
        return position, binding.catalog_generation

    def _rows(
        self,
        result: Mapping[str, object],
        scope: ConversationLibraryScope,
        *,
        generation: int,
        capabilities: HistoryCapabilities,
    ) -> tuple[tuple[ConversationLibraryRow, ...], str | None]:
        rows_raw = result.get("rows")
        if not isinstance(rows_raw, list):
            raise LibraryStoreError("Claude helper list returned no rows")
        rows = tuple(
            self._row(raw, scope, generation=generation, capabilities=capabilities)
            for raw in rows_raw
        )
        # Capability honesty: a helper that predates sub-agent enumeration
        # returns rows without the ``agents`` evidence key — visible, never silently absent.
        # The response-level ``agentsEnumerated`` marker covers the EMPTY catalog too:
        # over zero rows there is no row-level evidence either way, so only the marker
        # proves the helper enumerates agents.
        agents_unavailable = result.get("agentsEnumerated") is not True or any(
            isinstance(raw, Mapping) and "agents" not in raw for raw in rows_raw
        )
        agents_note = _AGENTS_UNAVAILABLE_NOTE if agents_unavailable else None
        # Nested sub-agents: a ``spawnDepth`` above 1 means the
        # row's real parent is another sub-agent, which the flat per-session grouping cannot
        # model — the row stays listed under the top-level session AND the note says so,
        # never silently absent.
        if not agents_unavailable:
            nested = sum(
                1
                for raw in rows_raw
                if isinstance(raw, Mapping) and isinstance(raw.get("agents"), list)
                for item in raw["agents"]
                if isinstance(item, Mapping)
                and isinstance(item.get("spawnDepth"), int)
                and not isinstance(item.get("spawnDepth"), bool)
                and item["spawnDepth"] > 1
            )
            if nested:
                nested_note = (
                    f"{nested} nested sub-agent(s) (spawnDepth > 1) are shown flat under "
                    "the top-level session; the library cannot group them under their "
                    "parent sub-agent"
                )
                agents_note = (
                    f"{agents_note}; {nested_note}" if agents_note else nested_note
                )
        return rows, agents_note

    def _next_cursor(
        self,
        result: Mapping[str, object],
        scope: ConversationLibraryScope,
        generation: int,
    ) -> LibraryListCursor | None:
        next_native = result.get("nextCursor")
        if next_native is not None and not isinstance(next_native, str):
            raise LibraryStoreError("Claude helper returned a non-text continuation cursor")
        if next_native is None:
            return None
        return self._cursor_authority.mint_list_cursor(
            scope, catalog_generation=generation, native_cursor=next_native
        )

    def _read_page(
        self,
        result: Mapping[str, object],
        ref: NativeConversationRef,
        generation: int,
    ) -> tuple[tuple[ConversationItem, ...], LibraryReadCursor | None, bool, int]:
        items_raw = result.get("items")
        total = result.get("totalItems")
        has_older = result.get("hasOlder")
        if not isinstance(items_raw, list) or not isinstance(total, int):
            raise LibraryStoreError("Claude helper read returned an invalid page")
        if not isinstance(has_older, bool):
            raise LibraryStoreError("Claude helper read returned no window evidence")
        items = tuple(_conversation_item(raw) for raw in items_raw)
        return items, self._older_cursor(result, ref, generation), has_older, total

    def _older_cursor(
        self,
        result: Mapping[str, object],
        ref: NativeConversationRef,
        generation: int,
    ) -> LibraryReadCursor | None:
        has_older = result.get("hasOlder")
        older_ordinal = result.get("olderOrdinal")
        if not has_older or not isinstance(older_ordinal, int):
            return None
        return self._cursor_authority.mint_read_cursor(
            self._scope_for_ref(ref),
            catalog_generation=generation,
            native_cursor=str(older_ordinal),
        )

    def _scope_for_ref(self, ref: NativeConversationRef) -> ConversationLibraryScope:
        return ConversationLibraryScope(
            authorization=self._authorization,
            harness_id=ref.harness_id,
            canonical_project_scope=ref.project_scope,
            query_digest=query_digest(ref.harness_id, ref.project_scope),
        )

    def _row(
        self,
        raw: object,
        scope: ConversationLibraryScope,
        *,
        generation: int,
        capabilities: HistoryCapabilities,
    ) -> ConversationLibraryRow:
        if not isinstance(raw, Mapping):
            raise LibraryStoreError("Claude helper row is not an object")
        session_id = required_field(raw, "sessionId", source="Claude helper")
        digest = self._cursor_authority.identity_digest(
            self.harness_id, session_id, scope.canonical_project_scope
        )
        title = _first_text(raw, "customTitle", "summary", "firstPrompt")
        last_modified = raw.get("lastModified")
        agents_raw = raw.get("agents")
        if agents_raw is not None and not isinstance(agents_raw, list):
            raise LibraryStoreError("Claude helper row agents are not a list")
        agents = tuple(
            self._agent_row(item, session_id, scope, generation=generation)
            for item in agents_raw or ()
        )
        return ConversationLibraryRow(
            conversation_key=self._cursor_authority.mint_conversation_key(
                scope,
                vendor_conversation_id=session_id,
                identity_digest=digest,
                catalog_generation=generation,
            ),
            identity_digest=digest,
            title=title or "(untitled session)",
            safe_native_id_suffix=session_id[-6:],
            last_activity_at=(
                _iso_from_millis(last_modified) if isinstance(last_modified, int) else None
            ),
            capabilities=capabilities,
            agents=agents,
        )

    def _agent_row(
        self,
        raw: object,
        session_id: str,
        scope: ConversationLibraryScope,
        *,
        generation: int,
    ) -> ConversationLibraryAgentRow:
        """One sub-agent child row from the helper's ``.meta.json`` evidence.

        Identity comes only from the native meta (``agentType``/``description``/``model``,
        ``toolUseId`` as the join key); without it the title is the honest
        ``agent <short-id>`` fallback, never a fabricated name.
        """

        if not isinstance(raw, Mapping):
            raise LibraryStoreError("Claude helper agent row is not an object")
        agent_id = required_field(raw, "agentId", source="Claude helper")
        vendor_id = f"{session_id}{_AGENT_ID_SEPARATOR}{agent_id}"
        digest = self._cursor_authority.identity_digest(
            self.harness_id, vendor_id, scope.canonical_project_scope
        )
        description = raw.get("description")
        agent_type = raw.get("agentType")
        model = raw.get("model")
        join_key = raw.get("toolUseId")
        last_modified = raw.get("lastModified")
        title = description if isinstance(description, str) and description.strip() else None
        if title is None and isinstance(agent_type, str) and agent_type.strip():
            title = agent_type
        return ConversationLibraryAgentRow(
            conversation_key=self._cursor_authority.mint_conversation_key(
                scope,
                vendor_conversation_id=vendor_id,
                identity_digest=digest,
                catalog_generation=generation,
            ),
            identity_digest=digest,
            title=title or f"agent {agent_id[:8]}",
            role=agent_type if isinstance(agent_type, str) and agent_type.strip() else None,
            model=model if isinstance(model, str) and model.strip() else None,
            join_key=join_key if isinstance(join_key, str) and join_key.strip() else None,
            safe_native_id_suffix=agent_id[-6:],
            last_activity_at=(
                _iso_from_millis(last_modified) if isinstance(last_modified, int) else None
            ),
        )


def _split_agent_vendor_id(vendor_id: str) -> tuple[str, str | None]:
    """Split the library's composite ``<sessionId>/<agentId>`` grammar."""

    session_id, separator, agent_id = vendor_id.partition(_AGENT_ID_SEPARATOR)
    if separator and agent_id:
        return session_id, agent_id
    return session_id if separator else vendor_id, None


def _record_base(raw: object) -> tuple[Mapping[str, object], str, str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise LibraryStoreError("Claude helper record is not an object")
    record_type = required_field(raw, "type", source="Claude helper")
    uuid = required_field(raw, "uuid", source="Claude helper")
    ordinal = raw.get("ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise LibraryStoreError("Claude helper record lacks a stable ordinal")
    timestamp = raw.get("timestamp")
    base: dict[str, Any] = {
        "item_id": uuid,
        "revision": 1,
        "global_ordinal": ordinal,
        "created_at": timestamp if isinstance(timestamp, str) and timestamp else None,
        "evidence_ref": f"claude-message:{uuid}",
    }
    return raw, record_type, uuid, base


def _conversation_item(raw: object) -> ConversationItem:
    record, record_type, uuid, base = _record_base(raw)
    if record_type == "user":
        return _user_item(record, base, uuid)
    if record_type == "assistant":
        return ConversationItem(
            **base,
            lane="harness",
            source="native-history",
            role="assistant",
            kind="message",
            phase="completed",
            provenance=_provenance("harness"),
            blocks=_content_blocks(record.get("content"), uuid),
        )
    return ConversationItem(
        **base,
        lane="system",
        source="native-history",
        role="system",
        kind="notice",
        phase="completed",
        provenance=_provenance("system"),
        blocks=_content_blocks(
            record.get("content"), uuid, fallback_summary=f"Claude {record_type} record"
        ),
    )


def _user_item(
    record: Mapping[str, object], base: Mapping[str, Any], uuid: str
) -> ConversationItem:
    blocks = _content_blocks(record.get("content"), uuid)
    if blocks and all(isinstance(block, ToolOutputBlock) for block in blocks):
        parent_tool_use_id = record.get("parentToolUseId")
        return ConversationItem(
            **base,
            lane="harness",
            source="native-history",
            role="tool",
            kind="tool-result",
            phase="completed",
            provenance=_provenance("harness"),
            blocks=blocks,
            correlation=(
                ConversationCorrelation(tool_call_id=parent_tool_use_id)
                if isinstance(parent_tool_use_id, str) and parent_tool_use_id
                else None
            ),
        )
    return ConversationItem(
        **base,
        lane="unknown-input",
        source="native-history",
        role="user",
        kind="message",
        phase="completed",
        provenance=ProvenanceEvidence(strength="native-only", origin="claude-native-history"),
        blocks=blocks,
    )


def _content_blocks(
    content: object,
    uuid: str,
    *,
    fallback_summary: str | None = None,
) -> tuple[ConversationContentBlock, ...]:
    if isinstance(content, str) and content:
        return (TextBlock(block_id=f"{uuid}:b0", text=_capped(content)),)
    if isinstance(content, list):
        return tuple(_content_block(raw, uuid, index) for index, raw in enumerate(content))
    if fallback_summary is None:
        return ()
    return (
        UnknownVendorBlock(
            block_id=f"{uuid}:b0",
            vendor_type="opaque",
            safe_summary=fallback_summary,
            evidence_ref=f"claude-message:{uuid}",
        ),
    )


_TEXTUAL_CONTENT: Mapping[str, tuple[type[MarkdownBlock] | type[ThinkingBlock], str]] = {
    "text": (MarkdownBlock, "text"),
    "thinking": (ThinkingBlock, "thinking"),
}


def _content_block(raw: object, uuid: str, index: int) -> ConversationContentBlock:
    if not isinstance(raw, Mapping):
        return _unknown_content_block("opaque", uuid, index)
    block_type = str(raw.get("type"))
    block_id = _content_block_id(raw, uuid, index)
    textual = _TEXTUAL_CONTENT.get(block_type)
    if textual is not None:
        block_class, field = textual
        text = raw.get(field)
        return block_class(
            block_id=block_id, markdown=_capped(text if isinstance(text, str) else "")
        )
    if block_type == "tool_use":
        return _tool_use_block(raw, block_id)
    if block_type == "tool_result":
        return ToolOutputBlock(block_id=block_id, text=_capped(_tool_result_text(raw)))
    if block_type == "image":
        return _image_block(raw, block_id, uuid)
    return _unknown_content_block(block_type, block_id, uuid)


def _content_block_id(raw: Mapping[str, object], uuid: str, index: int) -> str:
    native_id = raw.get("id")
    if isinstance(native_id, str) and native_id:
        return native_id
    return f"{uuid}:b{index}"


def _tool_use_block(raw: Mapping[str, object], block_id: str) -> ToolInputBlock:
    name = raw.get("name")
    summary = name if isinstance(name, str) and name else "(tool)"
    return ToolInputBlock(block_id=block_id, summary=_capped(summary, 512))


def _image_block(raw: Mapping[str, object], block_id: str, uuid: str) -> UnknownVendorBlock:
    media = raw.get("source")
    mime = media.get("media_type") if isinstance(media, Mapping) else None
    label = f"image ({mime})" if isinstance(mime, str) else "image"
    return UnknownVendorBlock(
        block_id=block_id,
        vendor_type="image",
        safe_summary=f"{label} is not rendered in the history preview",
        evidence_ref=f"claude-message:{uuid}",
    )


def _unknown_content_block(
    vendor_type: str, block_id: str, uuid_or_index: object
) -> UnknownVendorBlock:
    if isinstance(uuid_or_index, int):
        block_id = f"unknown:{uuid_or_index}"
    return UnknownVendorBlock(
        block_id=block_id if isinstance(block_id, str) else "unknown",
        vendor_type=vendor_type,
        safe_summary=f"unsupported Claude content block type {vendor_type!r}",
        evidence_ref="claude-message:unknown",
    )


def _tool_result_text(raw: Mapping[str, object]) -> str:
    return "\n".join(text_content_parts(raw.get("content")))


def _provenance(producer: str) -> ProvenanceEvidence:
    return native_provenance(producer, "claude-native-history")


def _iso_from_millis(value: int) -> str:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError) as exc:
        # Range-absurd but type-valid timestamps fail as typed store errors (review F4).
        raise LibraryStoreError(
            f"Claude native payload has an out-of-range timestamp: {exc}"
        ) from exc


__all__ = ["ClaudeConversationLibrary"]
