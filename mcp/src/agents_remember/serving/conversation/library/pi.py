"""Pi dormant native conversation library through the locked helper (260718-CHATS-L2).

Every operation runs through the repository-owned helper
(``@earendil-works/pi-coding-agent`` ``SessionManager.list`` / ``open`` +
``getBranch``) via :class:`ConversationLibraryHelperHost`. The handshake reports the observed
runtime/helper versions as informational evidence only — THE CONTRACT IS THE ONLY GATE (developer
ruling 2026-07-21, 260718-CHATS-L5F R4): the ``list``/``getBranch`` operation succeeding is the
proof, never a version-string comparison. The durable Pi entry id is the native item identity
anchor; reading a dormant conversation never calls ``switch_session`` on any running process — the
helper opens the session file read-only and open starts a new AR session (design section 10.4).

Pi native append-only entries are the complete session line, so historical and tool
completeness are honest ``supported`` once the production contract probe passes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agents_remember.models.conversations.capabilities import (
    HistoryCapabilities,
)
from agents_remember.models.conversations.content import (
    ConversationContentBlock,
    ConversationCorrelation,
    ConversationItem,
    MarkdownBlock,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.models.conversations.cursors import (
    LibraryListCursor,
    LibraryReadCursor,
    NativeResumeTarget,
)
from agents_remember.models.conversations.history import (
    ConversationLibraryPage,
    ConversationLibraryPageScope,
    ConversationLibraryRow,
    HistoricalConversationPage,
)
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
    ConversationLibraryScope,
    HarnessId,
    NativeConversationRef,
    ProvenanceEvidence,
)
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
    first_text,
    native_provenance,
    required_field,
    text_content_parts,
)
from agents_remember.serving.conversation.library.scope import query_digest

_Capabilities = Callable[[HarnessId], Awaitable[HistoryCapabilities]]


def _provenance(producer: str) -> ProvenanceEvidence:
    return native_provenance(producer, "pi-native-history")


class PiConversationLibrary:
    """The dormant Pi library port: helper-backed list/read/resolve, no local index."""

    harness_id: HarnessId = "pi"

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
            "pi",
            "list",
            {
                "canonicalProjectScope": scope.canonical_project_scope,
                "cursor": native_cursor,
                "limit": limit,
            },
        )
        generation = self._cursor_authority.catalog_generation(
            required_field(result, "signature", source="Pi helper")
        )
        if expected_generation is not None and expected_generation != generation:
            raise CatalogGenerationError("the native Pi catalog changed; the list cursor is reset")
        capabilities = await self._capabilities(self.harness_id)
        rows = self._rows(result, scope, generation=generation, capabilities=capabilities)
        next_cursor = self._next_cursor(result, scope, generation)
        return ConversationLibraryPage(
            scope=ConversationLibraryPageScope(
                harness_id=self.harness_id,
                canonical_project_scope=scope.canonical_project_scope,
                query_digest=scope.query_digest,
            ),
            rows=rows,
            next_cursor=next_cursor,
        )

    async def read(
        self,
        ref: NativeConversationRef,
        *,
        before: LibraryReadCursor | None,
        limit: int,
    ) -> HistoricalConversationPage:
        native_cursor, expected_generation = self._verify_read_position(before, ref)
        result, _runtime, _helper = await self._helper.call(
            "pi",
            "read",
            {
                "vendorConversationId": ref.vendor_conversation_id,
                "expectedIdentityDigest": ref.identity_digest,
                "canonicalProjectScope": ref.project_scope,
                "cursor": native_cursor,
                "limit": limit,
            },
        )
        generation = self._cursor_authority.catalog_generation(
            required_field(result, "signature", source="Pi helper")
        )
        if expected_generation is not None and expected_generation != generation:
            raise CatalogGenerationError(
                "the native Pi conversation changed; the read cursor is reset"
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
        result, _runtime, _helper = await self._helper.call(
            "pi",
            "resolve-resume-target",
            {
                "vendorConversationId": ref.vendor_conversation_id,
                "expectedIdentityDigest": ref.identity_digest,
                "canonicalProjectScope": ref.project_scope,
            },
        )
        resolved_id = required_field(result, "vendorConversationId", source="Pi helper")
        session_file = required_field(result, "sessionFile", source="Pi helper")
        if resolved_id != ref.vendor_conversation_id:
            raise LibraryStoreError("Pi helper resolved a different native identity")
        generation = self._cursor_authority.catalog_generation(
            f"pi:resolve:{ref.project_scope}:{ref.vendor_conversation_id}"
        )
        return self._cursor_authority.mint_resume_target(
            self._scope_for_ref(ref),
            vendor_conversation_id=ref.vendor_conversation_id,
            identity_digest=ref.identity_digest,
            catalog_generation=generation,
            launch={"kind": "argv", "args": ["--session", session_file]},
        )

    # -- internals ----------------------------------------------------------

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
            raise LibraryStoreError("Pi helper read returned an invalid page")
        if not isinstance(has_older, bool):
            raise LibraryStoreError("Pi helper read returned no window evidence")
        items = tuple(_conversation_item(raw) for raw in items_raw)
        older_cursor = self._older_cursor(result, ref, generation)
        return items, older_cursor, has_older, total

    def _rows(
        self,
        result: Mapping[str, object],
        scope: ConversationLibraryScope,
        *,
        generation: int,
        capabilities: HistoryCapabilities,
    ) -> tuple[ConversationLibraryRow, ...]:
        rows_raw = result.get("rows")
        if not isinstance(rows_raw, list):
            raise LibraryStoreError("Pi helper list returned no rows")
        return tuple(
            self._row(raw, scope, generation=generation, capabilities=capabilities)
            for raw in rows_raw
        )

    def _next_cursor(
        self,
        result: Mapping[str, object],
        scope: ConversationLibraryScope,
        generation: int,
    ) -> LibraryListCursor | None:
        next_native = result.get("nextCursor")
        if next_native is not None and not isinstance(next_native, str):
            raise LibraryStoreError("Pi helper returned a non-text continuation cursor")
        if next_native is None:
            return None
        return self._cursor_authority.mint_list_cursor(
            scope, catalog_generation=generation, native_cursor=next_native
        )

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
            raise InvalidLibraryCursorError("Pi list cursor position must be text")
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
            raise InvalidLibraryCursorError("Pi read cursor position must be text")
        return position, binding.catalog_generation

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
            raise LibraryStoreError("Pi helper row is not an object")
        session_id = required_field(raw, "sessionId", source="Pi helper")
        digest = self._cursor_authority.identity_digest(
            self.harness_id, session_id, scope.canonical_project_scope
        )
        title = first_text(raw, "name", "firstMessage")
        modified = raw.get("modified")
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
            last_activity_at=modified if isinstance(modified, str) and modified else None,
            capabilities=capabilities,
        )


def _stable_ordinal(raw: Mapping[str, object]) -> int:
    ordinal = raw.get("ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise LibraryStoreError("Pi helper record lacks a stable ordinal")
    return ordinal


def _record_base(raw: object) -> tuple[Mapping[str, object], str, str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise LibraryStoreError("Pi helper record is not an object")
    entry_type = required_field(raw, "type", source="Pi helper")
    entry_id = required_field(raw, "id", source="Pi helper")
    ordinal = _stable_ordinal(raw)
    timestamp = raw.get("timestamp")
    base: dict[str, Any] = {
        "item_id": entry_id,
        "revision": 1,
        "global_ordinal": ordinal,
        "created_at": timestamp if isinstance(timestamp, str) and timestamp else None,
        "evidence_ref": f"pi-entry:{entry_id}",
    }
    return raw, entry_type, entry_id, base


def _conversation_item(raw: object) -> ConversationItem:
    record, entry_type, entry_id, base = _record_base(raw)
    if entry_type == "message":
        return _message_item(record, base)
    notice_builder = _NOTICE_BUILDERS.get(entry_type)
    notice = notice_builder(record) if notice_builder is not None else None
    if notice is not None:
        return _notice_item(base, entry_id, notice)
    return _unknown_entry_item(base, entry_id, entry_type)


def _notice_item(base: Mapping[str, Any], entry_id: str, notice: str) -> ConversationItem:
    return ConversationItem(
        **base,
        lane="system",
        source="native-history",
        role="system",
        kind="notice",
        phase="completed",
        provenance=_provenance("system"),
        blocks=(TextBlock(block_id=f"{entry_id}:b0", text=_capped(notice)),),
    )


def _unknown_entry_item(
    base: Mapping[str, Any], entry_id: str, entry_type: str
) -> ConversationItem:
    return ConversationItem(
        **base,
        lane="harness",
        source="native-history",
        role="tool",
        kind="unknown-vendor",
        phase="unknown",
        provenance=_provenance("harness"),
        blocks=(
            UnknownVendorBlock(
                block_id=f"{entry_id}:b0",
                vendor_type=entry_type,
                safe_summary=f"unsupported Pi history entry type {entry_type!r}",
                evidence_ref=f"pi-entry:{entry_id}",
            ),
        ),
    )


def _message_item(raw: Mapping[str, object], base: Mapping[str, Any]) -> ConversationItem:
    message = raw.get("message")
    entry_id = str(base["item_id"])
    if not isinstance(message, Mapping):
        return ConversationItem(
            **base,
            lane="harness",
            source="native-history",
            role="tool",
            kind="unknown-vendor",
            phase="unknown",
            provenance=_provenance("harness"),
            blocks=(
                UnknownVendorBlock(
                    block_id=f"{entry_id}:b0",
                    vendor_type="message",
                    safe_summary="Pi message detail unavailable",
                    evidence_ref=f"pi-entry:{entry_id}",
                ),
            ),
        )
    role = message.get("role")
    if role == "user":
        return ConversationItem(
            **base,
            lane="unknown-input",
            source="native-history",
            role="user",
            kind="message",
            phase="completed",
            provenance=ProvenanceEvidence(strength="native-only", origin="pi-native-history"),
            blocks=_pi_user_blocks(message, entry_id),
        )
    if role == "assistant":
        return ConversationItem(
            **base,
            lane="harness",
            source="native-history",
            role="assistant",
            kind="message",
            phase="completed",
            provenance=_provenance("harness"),
            blocks=_pi_assistant_blocks(message, entry_id),
        )
    if role == "toolResult":
        return ConversationItem(
            **base,
            lane="harness",
            source="native-history",
            role="tool",
            kind="tool-result",
            phase="completed",
            provenance=_provenance("harness"),
            blocks=(
                ToolOutputBlock(
                    block_id=f"{entry_id}:b0",
                    text=_capped(_pi_tool_result_text(message)),
                ),
            ),
            correlation=_pi_tool_correlation(message),
        )
    return ConversationItem(
        **base,
        lane="harness",
        source="native-history",
        role="tool",
        kind="unknown-vendor",
        phase="unknown",
        provenance=_provenance("harness"),
        blocks=(
            UnknownVendorBlock(
                block_id=f"{entry_id}:b0",
                vendor_type=f"message:{role!r}",
                safe_summary="unsupported Pi message role",
                evidence_ref=f"pi-entry:{entry_id}",
            ),
        ),
    )


def _pi_user_blocks(
    message: Mapping[str, object], entry_id: str
) -> tuple[ConversationContentBlock, ...]:
    return tuple(
        _pi_user_block(raw, entry_id, index)
        for index, raw in enumerate(_pi_user_entries(message.get("content")))
        if isinstance(raw, Mapping)
    )


def _pi_user_entries(content: object) -> list[object]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return content
    return []


def _pi_user_block(
    raw: Mapping[str, object], entry_id: str, index: int
) -> ConversationContentBlock:
    if raw.get("type") == "text":
        text = raw.get("text")
        if isinstance(text, str) and text:
            return TextBlock(block_id=f"{entry_id}:b{index}", text=_capped(text))
    return UnknownVendorBlock(
        block_id=f"{entry_id}:b{index}",
        vendor_type=str(raw.get("type")),
        safe_summary=f"Pi {raw.get('type')!r} content is not rendered in the history preview",
        evidence_ref=f"pi-entry:{entry_id}",
    )


def _pi_assistant_blocks(
    message: Mapping[str, object], entry_id: str
) -> tuple[ConversationContentBlock, ...]:
    content = message.get("content")
    if not isinstance(content, list):
        return ()
    return tuple(
        _pi_assistant_block(raw, entry_id, index)
        for index, raw in enumerate(content)
        if isinstance(raw, Mapping)
    )


def _pi_block_id(raw: Mapping[str, object], entry_id: str, index: int) -> str:
    block_id = raw.get("id")
    if isinstance(block_id, str) and block_id:
        return block_id
    return f"{entry_id}:b{index}"


_TEXTUAL_BLOCKS: Mapping[str, tuple[type[MarkdownBlock] | type[ThinkingBlock], str]] = {
    "text": (MarkdownBlock, "text"),
    "thinking": (ThinkingBlock, "thinking"),
}


def _pi_assistant_block(
    raw: Mapping[str, object], entry_id: str, index: int
) -> ConversationContentBlock:
    block_id = _pi_block_id(raw, entry_id, index)
    block_type = str(raw.get("type"))
    textual = _TEXTUAL_BLOCKS.get(block_type)
    if textual is not None:
        block_class, field = textual
        text = raw.get(field)
        return block_class(
            block_id=block_id, markdown=_capped(text if isinstance(text, str) else "")
        )
    if block_type == "toolCall":
        return _tool_call_block(raw, block_id)
    return UnknownVendorBlock(
        block_id=block_id,
        vendor_type=block_type,
        safe_summary=f"unsupported Pi content block type {block_type!r}",
        evidence_ref=f"pi-entry:{entry_id}",
    )


def _tool_call_block(raw: Mapping[str, object], block_id: str) -> ToolInputBlock:
    name = raw.get("name")
    summary = name if isinstance(name, str) and name else "(tool)"
    return ToolInputBlock(block_id=block_id, summary=_capped(summary, 512))


def _pi_tool_result_text(message: Mapping[str, object]) -> str:
    return "\n".join(text_content_parts(message.get("content")))


def _pi_tool_correlation(message: Mapping[str, object]) -> ConversationCorrelation | None:
    tool_call_id = message.get("toolCallId")
    if isinstance(tool_call_id, str) and tool_call_id:
        return ConversationCorrelation(tool_call_id=tool_call_id)
    return None


def _thinking_notice(raw: Mapping[str, object]) -> str:
    return f"Pi thinking level changed to {raw.get('thinkingLevel')!r}."


def _model_notice(raw: Mapping[str, object]) -> str:
    return f"Pi model changed to {raw.get('provider')!r}/{raw.get('modelId')!r}."


def _compaction_notice(raw: Mapping[str, object]) -> str:
    text = "Pi compacted the conversation context at this point."
    summary = raw.get("summary")
    if isinstance(summary, str) and summary:
        return f"{text}\n\n{summary}"
    return text


def _branch_notice(raw: Mapping[str, object]) -> str:
    text = "Pi branched the conversation from an earlier entry."
    summary = raw.get("summary")
    if isinstance(summary, str) and summary:
        return f"{text}\n\n{summary}"
    return text


def _session_info_notice(raw: Mapping[str, object]) -> str | None:
    name = raw.get("name")
    return f"Pi session named {name!r}." if isinstance(name, str) and name else None


def _label_notice(raw: Mapping[str, object]) -> str | None:
    label = raw.get("label")
    return f"Pi bookmark set: {label!r}." if isinstance(label, str) and label else None


def _custom_message_notice(raw: Mapping[str, object]) -> str:
    custom_type = raw.get("customType")
    prefix = f"Pi extension message ({custom_type!r})"
    content = raw.get("content")
    if isinstance(content, str) and content:
        return f"{prefix}:\n\n{content}"
    return f"{prefix} (content not rendered)."


_NOTICE_BUILDERS: Mapping[str, Callable[[Mapping[str, object]], str | None]] = {
    "thinking_level_change": _thinking_notice,
    "model_change": _model_notice,
    "compaction": _compaction_notice,
    "branch_summary": _branch_notice,
    "session_info": _session_info_notice,
    "label": _label_notice,
    "custom_message": _custom_message_notice,
}


__all__ = ["PiConversationLibrary"]
