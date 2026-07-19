"""Codex dormant native conversation library: direct app-server list/read/resolve (260718-CHATS-L2).

The Codex library is DIRECT (no Node helper): each operation opens one short-lived
``codex app-server`` stdio connection through the existing :class:`CodexStdioTransport`,
performs the exact ``thread/list`` / ``thread/read`` calls, and closes it. Nothing here resumes,
forks, or mutates a thread, and no local catalog/index is kept — the native app-server remains
the one list/read authority on every call.

Historical tool/command completeness is honestly ``partial`` (design section 8.1): Codex does
not persist every tool interaction, so the preview advertises it instead of claiming complete
history. Item normalization never flattens unknown vendor kinds into guessed semantics; anything
unmapped becomes an explicit ``unknown-vendor`` evidence item.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import TracebackType

from agents_remember.errors import CodexAppServerError, CodexAppServerRpcError
from agents_remember.observer.events import now_iso
from agents_remember.serving.codex_app_server_protocol import (
    CodexAppServerTransport,
    CodexStdioTransport,
    JsonObject,
)
from agents_remember.serving.codex_app_server_state import (
    iso_from_epoch,
    required_list,
    required_object,
    required_text,
    validate_initialize_response,
)
from agents_remember.serving.conversation.library.codex_normalize import (
    conversation_items_from_thread,
)
from agents_remember.serving.conversation.library.cursor import LibraryCursorAuthority
from agents_remember.serving.conversation.library.errors import (
    CatalogGenerationError,
    InvalidLibraryCursorError,
    LibraryStoreError,
    UnknownNativeConversationError,
)
from agents_remember.serving.conversation.library.scope import query_digest
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    ConversationItem,
    ConversationLibraryPage,
    ConversationLibraryPageScope,
    ConversationLibraryRow,
    ConversationLibraryScope,
    HarnessId,
    HistoricalConversationPage,
    HistoryCapabilities,
    LibraryListCursor,
    LibraryReadCursor,
    NativeConversationRef,
    NativeResumeTarget,
)
from agents_remember.serving.harness_control_models import ControlIdentity, LaunchSpec
from agents_remember.serving.harnesses import Harness

_CLIENT_NAME = "agents_remember"
_CLIENT_VERSION = "3.0.0"
_SOURCE_KINDS = ("cli", "vscode", "exec", "appServer")
"""Top-level native conversations; sub-agent threads belong to their parent's history."""
_LIST_GENERATION_PROBE_LIMIT = 100
_TEXT_BLOCK_CAP = 8192
_Capabilities = Callable[[HarnessId], Awaitable[HistoryCapabilities]]


async def probe_app_server_version(
    harness: Harness,
    *,
    workspace_root: Path,
    env: Mapping[str, str],
) -> str:
    """Gate probe: real connect + initialize + thread/list, returning the observed CLI version."""

    async with _AppServer(harness, workspace_root=workspace_root, env=env) as server:
        await server.thread_list(cursor=None, limit=1, scope=None)
        return server.cli_version


class _AppServer:
    """One short-lived read-only app-server connection (initialize handshake included)."""

    def __init__(
        self,
        harness: Harness,
        *,
        workspace_root: Path,
        env: Mapping[str, str],
        transport_factory: Callable[[], CodexAppServerTransport] = CodexStdioTransport,
    ) -> None:
        self._harness = harness
        self._workspace_root = workspace_root
        self._env = env
        self._transport_factory = transport_factory
        self._transport: CodexAppServerTransport | None = None
        self.cli_version = ""

    async def __aenter__(self) -> _AppServer:
        resolver = shutil.which
        resolved = resolver(self._harness.command)
        if resolved is None:
            raise LibraryStoreError(f"harness not installed: {self._harness.id!r}")
        try:
            executable = str(Path(resolved).resolve(strict=True))
        except OSError as exc:
            raise LibraryStoreError(
                f"could not resolve installed harness {self._harness.id!r}: {exc}"
            ) from exc
        launch = LaunchSpec(
            identity=ControlIdentity(
                ar_session_id="conversation-library-dormant",
                tmux_name="conversation-library-dormant",
                created_at=now_iso(),
            ),
            harness_id="codex",
            cwd=self._workspace_root,
            argv=(executable, "app-server"),
            env=dict(self._env),
        )
        transport = self._transport_factory()
        try:
            await transport.start(launch)
            initialize = await transport.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": _CLIENT_NAME,
                        "title": "Agents Remember",
                        "version": _CLIENT_VERSION,
                    },
                    "capabilities": {"experimentalApi": False},
                },
            )
            cli_version, _evidence = validate_initialize_response(
                initialize, client_name=_CLIENT_NAME
            )
            await transport.notify("initialized", {})
        except (CodexAppServerError, OSError) as exc:
            await transport.stop("forced")
            raise LibraryStoreError(f"Codex app-server connection failed closed: {exc}") from exc
        self._transport = transport
        self.cli_version = cli_version
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._transport is not None:
            await self._transport.stop("forced")

    async def thread_list(
        self,
        *,
        cursor: str | None,
        limit: int,
        scope: str | None,
    ) -> JsonObject:
        transport = self._require_transport()
        params: JsonObject = {
            "cursor": cursor,
            "limit": limit,
            "sourceKinds": list(_SOURCE_KINDS),
        }
        if scope is not None:
            params["cwd"] = scope
        try:
            return await transport.request("thread/list", params)
        except CodexAppServerError as exc:
            raise LibraryStoreError(f"Codex thread/list failed closed: {exc}") from exc

    async def thread_read(self, thread_id: str, *, include_turns: bool) -> JsonObject:
        transport = self._require_transport()
        params: JsonObject = {"threadId": thread_id}
        if include_turns:
            params["includeTurns"] = True
        try:
            return await transport.request("thread/read", params)
        except CodexAppServerRpcError as exc:
            if exc.code == -32601:
                raise LibraryStoreError(
                    f"Codex thread/read is not available on this install: {exc}"
                ) from exc
            raise UnknownNativeConversationError(
                f"Codex thread {thread_id!r} is not readable: {exc}"
            ) from exc
        except CodexAppServerError as exc:
            raise LibraryStoreError(f"Codex thread/read failed closed: {exc}") from exc

    def _require_transport(self) -> CodexAppServerTransport:
        if self._transport is None:
            raise LibraryStoreError("Codex app-server connection is not open")
        return self._transport


class CodexConversationLibrary:
    """The dormant Codex library port: native list/read/resolve with no local index.

    Constructed per request with the caller's server-resolved authorization binding so every
    minted cursor/key re-binds that exact principal/tenant; the port itself never authorizes.
    """

    harness_id: HarnessId = "codex"

    def __init__(
        self,
        *,
        authorization: AuthorizationBinding,
        cursor_authority: LibraryCursorAuthority,
        capabilities: _Capabilities,
        harness: Harness,
        env: Callable[[], Mapping[str, str]] = lambda: os.environ,
        transport_factory: Callable[[], CodexAppServerTransport] = CodexStdioTransport,
    ) -> None:
        self._authorization = authorization
        self._cursor_authority = cursor_authority
        self._capabilities = capabilities
        self._harness = harness
        self._env = env
        self._transport_factory = transport_factory

    async def list(
        self,
        scope: ConversationLibraryScope,
        *,
        cursor: LibraryListCursor | None,
        limit: int,
    ) -> ConversationLibraryPage:
        native_cursor, expected_generation = self._verify_list_position(cursor, scope)
        async with self._connect(scope) as server:
            generation = await self._list_generation(server, scope)
            if expected_generation is not None and expected_generation != generation:
                raise CatalogGenerationError(
                    "the native Codex catalog changed; the list cursor is reset"
                )
            page = await server.thread_list(
                cursor=native_cursor, limit=limit, scope=scope.canonical_project_scope
            )
        capabilities = await self._capabilities(self.harness_id)
        rows, next_native = self._rows(
            page, scope, generation=generation, capabilities=capabilities
        )
        next_cursor = (
            self._cursor_authority.mint_list_cursor(
                scope, catalog_generation=generation, native_cursor=next_native
            )
            if next_native is not None
            else None
        )
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
        before_ordinal, expected_generation = self._verify_read_position(before, ref)
        async with self._connect_scope(ref.project_scope) as server:
            result = await server.thread_read(ref.vendor_conversation_id, include_turns=True)
        try:
            thread = required_object(result.get("thread"), context="thread/read response")
            items = conversation_items_from_thread(thread)
        except CodexAppServerError as exc:
            raise _shape_error(exc) from exc
        generation = self._read_generation(ref, thread, len(items))
        if expected_generation is not None and expected_generation != generation:
            raise CatalogGenerationError(
                "the native Codex conversation changed; the read cursor is reset"
            )
        window, has_older, older_ordinal = _window(items, before_ordinal, limit)
        older_cursor = (
            self._cursor_authority.mint_read_cursor(
                self._scope_for_ref(ref),
                catalog_generation=generation,
                native_cursor=older_ordinal,
            )
            if has_older and older_ordinal is not None
            else None
        )
        return HistoricalConversationPage(
            ref=ref,
            items=tuple(window),
            older_cursor=older_cursor,
            has_older=has_older,
            total_items=len(items),
            historical_capabilities=await self._capabilities(self.harness_id),
        )

    async def resolve_resume_target(self, ref: NativeConversationRef) -> NativeResumeTarget:
        async with self._connect_scope(ref.project_scope) as server:
            await server.thread_read(ref.vendor_conversation_id, include_turns=False)
        scope = self._scope_for_ref(ref)
        generation = self._cursor_authority.catalog_generation(
            f"codex:resolve:{ref.project_scope}:{ref.vendor_conversation_id}"
        )
        return self._cursor_authority.mint_resume_target(
            scope,
            vendor_conversation_id=ref.vendor_conversation_id,
            identity_digest=ref.identity_digest,
            catalog_generation=generation,
            launch={"kind": "codex-thread-resume", "threadId": ref.vendor_conversation_id},
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
        self._require_scope(binding.scope, scope)
        if not isinstance(position, str):
            raise InvalidLibraryCursorError("Codex list cursor position must be text")
        return position, binding.catalog_generation

    def _verify_read_position(
        self,
        before: LibraryReadCursor | None,
        ref: NativeConversationRef,
    ) -> tuple[int | None, int | None]:
        if before is None:
            return None, None
        binding, position = self._cursor_authority.verify_read_cursor(before)
        self._require_ref(binding.scope, ref)
        if isinstance(position, bool) or not isinstance(position, int) or position < 2:
            raise InvalidLibraryCursorError(
                "Codex read cursor must name an ordinal above the first item"
            )
        return position, binding.catalog_generation

    def _connect(self, scope: ConversationLibraryScope) -> _AppServer:
        return self._connect_scope(scope.canonical_project_scope)

    def _connect_scope(self, canonical_scope: str) -> _AppServer:
        return _AppServer(
            self._harness,
            workspace_root=Path(canonical_scope),
            env=self._env(),
            transport_factory=self._transport_factory,
        )

    async def _list_generation(
        self,
        server: _AppServer,
        scope: ConversationLibraryScope,
    ) -> int:
        probe = await server.thread_list(
            cursor=None,
            limit=_LIST_GENERATION_PROBE_LIMIT,
            scope=scope.canonical_project_scope,
        )
        try:
            data = required_list(probe, "data", context="thread/list generation probe")
            ids = [
                required_text(
                    required_object(row, context="thread/list row"), "id", context="row"
                )
                for row in data
            ]
        except CodexAppServerError as exc:
            raise _shape_error(exc) from exc
        has_more = probe.get("nextCursor") is not None
        signature = f"codex:list:{scope.canonical_project_scope}:{ids}:{has_more}"
        return self._cursor_authority.catalog_generation(signature)

    def _read_generation(
        self,
        ref: NativeConversationRef,
        thread: Mapping[str, object],
        total_items: int,
    ) -> int:
        updated = thread.get("updatedAt")
        signature = f"codex:read:{ref.vendor_conversation_id}:{total_items}:{updated!r}"
        return self._cursor_authority.catalog_generation(signature)

    def _rows(
        self,
        page: Mapping[str, object],
        scope: ConversationLibraryScope,
        *,
        generation: int,
        capabilities: HistoryCapabilities,
    ) -> tuple[tuple[ConversationLibraryRow, ...], str | None]:
        try:
            data = required_list(page, "data", context="thread/list response")
            rows = tuple(
                self._row(
                    required_object(raw, context="thread/list row"),
                    scope,
                    generation=generation,
                    capabilities=capabilities,
                )
                for raw in data
            )
        except CodexAppServerError as exc:
            raise _shape_error(exc) from exc
        next_native = page.get("nextCursor")
        if next_native is not None and not isinstance(next_native, str):
            raise LibraryStoreError("Codex thread/list returned a non-text continuation cursor")
        return rows, next_native

    def _row(
        self,
        row: Mapping[str, object],
        scope: ConversationLibraryScope,
        *,
        generation: int,
        capabilities: HistoryCapabilities,
    ) -> ConversationLibraryRow:
        thread_id = required_text(row, "id", context="thread/list row")
        preview = row.get("preview")
        name = row.get("name")
        title = name if isinstance(name, str) and name.strip() else preview
        digest = self._cursor_authority.identity_digest(
            self.harness_id, thread_id, scope.canonical_project_scope
        )
        try:
            last_activity = iso_from_epoch(row.get("updatedAt"), fallback="")
        except (CodexAppServerError, OSError, OverflowError, ValueError) as exc:
            # Range-absurd but type-valid timestamps fail as typed store errors (review F4).
            raise _shape_error(exc) from exc
        return ConversationLibraryRow(
            conversation_key=self._cursor_authority.mint_conversation_key(
                scope,
                vendor_conversation_id=thread_id,
                identity_digest=digest,
                catalog_generation=generation,
            ),
            identity_digest=digest,
            title=title if isinstance(title, str) and title.strip() else "(untitled thread)",
            safe_native_id_suffix=thread_id[-6:],
            last_activity_at=last_activity,
            capabilities=capabilities,
        )

    @staticmethod
    def _require_scope(
        binding_scope: ConversationLibraryScope,
        scope: ConversationLibraryScope,
    ) -> None:
        if binding_scope != scope:
            raise InvalidLibraryCursorError(
                "library cursor does not match this harness/scope/query"
            )

    def _require_ref(
        self,
        binding_scope: ConversationLibraryScope,
        ref: NativeConversationRef,
    ) -> None:
        scope = self._scope_for_ref(ref)
        self._require_scope(binding_scope, scope)

    def _scope_for_ref(self, ref: NativeConversationRef) -> ConversationLibraryScope:
        """Rebuild the exact minted scope from a ref + this caller's authorization."""

        return ConversationLibraryScope(
            authorization=self._authorization,
            harness_id=ref.harness_id,
            canonical_project_scope=ref.project_scope,
            query_digest=query_digest(ref.harness_id, ref.project_scope),
        )


def _shape_error(exc: Exception) -> LibraryStoreError:
    """Shape/range-skewed native payloads fail as typed store errors, never raw 500s (F3/F4)."""

    return LibraryStoreError(f"Codex native payload failed shape validation: {exc}")


def _window(
    items: list[ConversationItem],
    before_ordinal: int | None,
    limit: int,
) -> tuple[list[ConversationItem], bool, int | None]:
    """Chronological newest window; ``before`` pages strictly older ordinals."""

    upper = before_ordinal - 1 if before_ordinal is not None else len(items)
    upper = min(upper, len(items))
    lower = max(0, upper - limit)
    window = items[lower:upper]
    has_older = lower > 0
    older_ordinal = window[0].global_ordinal if has_older and window else None
    return window, has_older, older_ordinal


__all__ = ["CodexConversationLibrary", "probe_app_server_version"]
