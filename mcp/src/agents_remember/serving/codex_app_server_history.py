"""Contract-probed, source-bounded Codex native-history acquisition.

The preferred experimental contracts page persisted items/turns at the app-server. The
legacy whole ``thread/read(includeTurns=true)`` path exists only for app-servers that reject
both bounded methods as unavailable. The stdio transport's 128 MiB ceiling remains an
emergency framing fuse; it is not used as a history paging mechanism.
"""

from __future__ import annotations

import base64
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.errors import (
    CodexAppServerError,
    CodexAppServerRpcError,
    NativeHistoryLimitExceeded,
    NativeHistoryUnavailable,
)
from agents_remember.serving.codex_app_server_protocol import (
    CodexAppServerTransport,
    JsonObject,
)
from agents_remember.serving.codex_app_server_state import (
    native_evidence_frames_from_thread,
    required_list,
    required_object,
    required_text,
)
from agents_remember.serving.harness_control_models import (
    NativeEvidenceFrame,
    NativeEvidencePage,
    native_evidence_frame_wire_bytes,
    window_native_evidence_page,
)

SourceContract = Literal["bounded-items", "bounded-turns", "legacy-thread-read"]
HistoryContract = Literal["unknown", "bounded-items", "bounded-turns", "legacy-thread-read"]

_METHOD_UNAVAILABLE = -32601
_BOUNDED_ITEM_PAGE_LIMIT = 1
_BOUNDED_TURN_PAGE_LIMIT = 1
_CURSOR_PREFIX = "ar-cnh1."
# Codex 0.145 exposes turns/full but not items/list. A turn response can contain many items and
# cannot be byte-limited on the wire, so this is necessarily a POST-TRANSPORT materialization
# refusal, not a promise that the shared 128 MiB fuse cannot fire first. Retaining a smaller
# response after parsing is still necessary: otherwise an abandoned opaque continuation can pin
# almost the full transport fuse in the hosted process. Ordinary output pages clip to the caller's
# much smaller byte budget.
DEFAULT_NATIVE_HISTORY_SOURCE_RESPONSE_CEILING_BYTES = 16 << 20
# Stateful opaque continuations are necessary on the installed turns/full contract: the vendor
# cursor advances by TURN while the AR output window advances by ITEM. Keeping only the unconsumed
# current source response prevents re-requesting/re-decoding that turn on every AR page. The cache
# is bounded because callers can abandon cursors after cancellation. Four maximum-sized source
# responses allow parent plus concurrent selected siblings without turning the cache into a second
# unbounded history store; least-recent abandoned walks expire typed.
DEFAULT_NATIVE_HISTORY_CACHE_CEILING_BYTES = 64 << 20
DEFAULT_NATIVE_HISTORY_ACTIVE_WALKS = 64

SourcePageReader = Callable[
    [str | None], Awaitable[tuple[tuple[NativeEvidenceFrame, ...], str | None]]
]


@dataclass
class _BoundedWalk:
    walk_id: str
    contract: SourceContract
    thread_id: str
    source_cursor: str | None
    loaded: bool = False
    frames: tuple[NativeEvidenceFrame, ...] = ()
    frame_bytes: tuple[int, ...] = ()
    next_source_cursor: str | None = None
    seen_source_cursors: set[str | None] = field(default_factory=set)
    seen_native_ids: set[str] = field(default_factory=set)
    cached_bytes: int = 0


@dataclass
class _OutputWindow:
    contract: SourceContract
    thread_id: str
    limit: int
    byte_budget: int
    frames: list[NativeEvidenceFrame] = field(default_factory=list)
    used_bytes: int = 0


@dataclass(frozen=True)
class BoundedPageRequest:
    """One bounded native-history page: which thread, from where, and how much may come back.

    The two bounds are not independent: the reader stops at whichever of ``limit`` frames or
    ``byte_budget`` bytes is reached first, and the cursor is only meaningful for the thread it was
    minted against. Reading a page under a mismatched set is how a walk silently returns another
    thread's frames.
    """

    thread_id: str
    cursor: str | None
    limit: int
    byte_budget: int


@dataclass
class CodexNativeHistoryReader:
    """One connection's probed native-history contract and bounded page reader."""

    materialization_ceiling_bytes: int = DEFAULT_NATIVE_HISTORY_SOURCE_RESPONSE_CEILING_BYTES
    cache_ceiling_bytes: int = DEFAULT_NATIVE_HISTORY_CACHE_CEILING_BYTES
    max_active_walks: int = DEFAULT_NATIVE_HISTORY_ACTIVE_WALKS
    contract: HistoryContract = "unknown"
    _walks: OrderedDict[str, _BoundedWalk] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
    )
    _cached_bytes: int = field(default=0, init=False, repr=False)
    _next_walk_id: int = field(default=1, init=False, repr=False)

    def reset_probe(self) -> None:
        """A reconnected process must prove its contract independently."""

        self.contract = "unknown"
        self._walks.clear()
        self._cached_bytes = 0

    async def read_page(
        self,
        transport: CodexAppServerTransport,
        *,
        thread_id: str,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage:
        window = BoundedPageRequest(
            thread_id=thread_id, cursor=cursor, limit=limit, byte_budget=byte_budget
        )
        if cursor is not None and self.contract == "unknown":
            raise NativeHistoryUnavailable(
                "Codex bounded history cursor expired with its app-server connection",
                code="cursor-expired",
            )
        if self.contract == "bounded-items":
            return await self._read_bounded_items(
                transport,
                window,
            )
        if self.contract == "bounded-turns":
            return await self._read_bounded_turns(
                transport,
                window,
            )
        if self.contract == "legacy-thread-read":
            return await self._read_legacy_whole_thread_page_under_transport_fuse(
                transport,
                thread_id=thread_id,
                cursor=cursor,
                limit=limit,
                byte_budget=byte_budget,
            )

        first_items = await self._probe_bounded_method(
            transport,
            "thread/items/list",
            self._items_params(thread_id, source_cursor=None),
        )
        if first_items is not None:
            self.contract = "bounded-items"
            return await self._read_bounded_items(
                transport,
                window,
                first_response=first_items,
            )

        first_turns = await self._probe_bounded_method(
            transport,
            "thread/turns/list",
            self._turns_params(thread_id, source_cursor=None),
        )
        if first_turns is not None:
            self.contract = "bounded-turns"
            return await self._read_bounded_turns(
                transport,
                window,
                first_response=first_turns,
            )

        self.contract = "legacy-thread-read"
        return await self._read_legacy_whole_thread_page_under_transport_fuse(
            transport,
            thread_id=thread_id,
            cursor=cursor,
            limit=limit,
            byte_budget=byte_budget,
        )

    async def _probe_bounded_method(
        self,
        transport: CodexAppServerTransport,
        method: str,
        params: JsonObject,
    ) -> JsonObject | None:
        try:
            return await transport.request(method, params)
        except CodexAppServerRpcError as exc:
            if exc.code == _METHOD_UNAVAILABLE:
                return None
            raise NativeHistoryUnavailable(
                f"Codex {method} recognized the bounded history request but could not read it: {exc}",
                code="bounded-rpc-refused",
            ) from exc

    async def _read_bounded_items(
        self,
        transport: CodexAppServerTransport,
        window: BoundedPageRequest,
        first_response: JsonObject | None = None,
    ) -> NativeEvidencePage:
        thread_id = window.thread_id

        async def read_source_page(
            source_cursor: str | None,
        ) -> tuple[tuple[NativeEvidenceFrame, ...], str | None]:
            response = (
                first_response
                if source_cursor is None and first_response is not None
                else await self._bounded_request(
                    transport,
                    "thread/items/list",
                    self._items_params(thread_id, source_cursor=source_cursor),
                )
            )
            return self._parse_items_page(response)

        return await self._scan_bounded_source(read_source_page, window, contract="bounded-items")

    async def _read_bounded_turns(
        self,
        transport: CodexAppServerTransport,
        window: BoundedPageRequest,
        first_response: JsonObject | None = None,
    ) -> NativeEvidencePage:
        thread_id = window.thread_id

        async def read_source_page(
            source_cursor: str | None,
        ) -> tuple[tuple[NativeEvidenceFrame, ...], str | None]:
            response = (
                first_response
                if source_cursor is None and first_response is not None
                else await self._bounded_request(
                    transport,
                    "thread/turns/list",
                    self._turns_params(thread_id, source_cursor=source_cursor),
                )
            )
            return self._parse_turns_page(response)

        return await self._scan_bounded_source(read_source_page, window, contract="bounded-turns")

    async def _scan_bounded_source(
        self,
        read_source_page: SourcePageReader,
        window: BoundedPageRequest,
        *,
        contract: SourceContract,
    ) -> NativeEvidencePage:
        thread_id = window.thread_id
        walk = self._walk_for(
            window.cursor,
            contract=contract,
            thread_id=thread_id,
        )
        output = _OutputWindow(
            contract=contract,
            thread_id=thread_id,
            limit=window.limit,
            byte_budget=window.byte_budget,
        )
        while True:
            if not walk.loaded:
                await self._load_source_page(walk, read_source_page)

            if not walk.frames:
                if walk.next_source_cursor is None:
                    return NativeEvidencePage(
                        frames=tuple(output.frames),
                        next_cursor=None,
                        truncated=False,
                        bridge_epoch="",
                    )
                walk.source_cursor = walk.next_source_cursor
                walk.loaded = False
                walk.next_source_cursor = None
                continue

            consumed, page = self._select_loaded_frames(walk, output)
            if page is not None:
                return page
            self._consume_cached_frames(walk, consumed)
            if walk.next_source_cursor is None:
                return NativeEvidencePage(
                    frames=tuple(output.frames),
                    next_cursor=None,
                    truncated=False,
                    bridge_epoch="",
                )
            walk.source_cursor = walk.next_source_cursor
            walk.loaded = False
            walk.next_source_cursor = None

    async def _load_source_page(
        self,
        walk: _BoundedWalk,
        read_source_page: SourcePageReader,
    ) -> None:
        if walk.source_cursor in walk.seen_source_cursors:
            raise NativeHistoryUnavailable(
                "Codex bounded history repeated a source cursor across its walk",
                code="source-cursor-cycle",
            )
        walk.seen_source_cursors.add(walk.source_cursor)
        frames, next_source_cursor = await read_source_page(walk.source_cursor)
        if not frames and next_source_cursor is not None:
            raise NativeHistoryUnavailable(
                "Codex bounded history returned an empty continued source page",
                code="source-page-empty",
            )
        frame_bytes = tuple(native_evidence_frame_wire_bytes(frame) for frame in frames)
        materialized_bytes = sum(frame_bytes)
        if materialized_bytes > self.materialization_ceiling_bytes:
            raise NativeHistoryLimitExceeded(
                f"Codex native history source response for thread {walk.thread_id!r} "
                "exceeded the post-transport materialization ceiling",
                actual_bytes=materialized_bytes,
                limit_bytes=self.materialization_ceiling_bytes,
            )
        walk.loaded = True
        walk.frames = frames
        walk.frame_bytes = frame_bytes
        walk.next_source_cursor = next_source_cursor
        walk.cached_bytes = materialized_bytes

    def _select_loaded_frames(
        self,
        walk: _BoundedWalk,
        output: _OutputWindow,
    ) -> tuple[int, NativeEvidencePage | None]:
        consumed = 0
        for index, frame in enumerate(walk.frames):
            if frame.native_id in walk.seen_native_ids:
                raise NativeHistoryUnavailable(
                    f"Codex bounded history repeated item id {frame.native_id!r}",
                    code="native-id-repeated",
                )
            bounded_frame = window_native_evidence_page(
                (frame,),
                cursor=None,
                limit=1,
                byte_budget=output.byte_budget,
            ).frames[0]
            bounded_bytes = native_evidence_frame_wire_bytes(bounded_frame)
            if output.frames and output.used_bytes + bounded_bytes > output.byte_budget:
                continuation = self._store_continuation(
                    contract=output.contract,
                    thread_id=output.thread_id,
                    walk=walk,
                    consumed=consumed,
                )
                return consumed, NativeEvidencePage(
                    frames=tuple(output.frames),
                    next_cursor=continuation,
                    truncated=True,
                    bridge_epoch="",
                )
            walk.seen_native_ids.add(frame.native_id)
            output.frames.append(bounded_frame)
            output.used_bytes += bounded_bytes
            consumed = index + 1
            if len(output.frames) >= output.limit:
                continuation = self._store_continuation(
                    contract=output.contract,
                    thread_id=output.thread_id,
                    walk=walk,
                    consumed=consumed,
                )
                return consumed, NativeEvidencePage(
                    frames=tuple(output.frames),
                    next_cursor=continuation,
                    truncated=continuation is not None,
                    bridge_epoch="",
                )
        return consumed, None

    def _walk_for(
        self,
        cursor: str | None,
        *,
        contract: SourceContract,
        thread_id: str,
    ) -> _BoundedWalk:
        if cursor is None:
            walk_id = f"w{self._next_walk_id}"
            self._next_walk_id += 1
            return _BoundedWalk(
                walk_id=walk_id,
                contract=contract,
                thread_id=thread_id,
                source_cursor=None,
            )
        walk_id = _decode_bounded_cursor(
            cursor,
            contract=contract,
            thread_id=thread_id,
        )
        walk = self._walks.pop(walk_id, None)
        if walk is None:
            raise NativeHistoryUnavailable(
                "Codex bounded history cursor expired or was already consumed",
                code="cursor-expired",
            )
        self._cached_bytes -= walk.cached_bytes
        return walk

    def _store_continuation(
        self,
        *,
        contract: SourceContract,
        thread_id: str,
        walk: _BoundedWalk,
        consumed: int,
    ) -> str | None:
        self._consume_cached_frames(walk, consumed)
        if not walk.frames:
            if walk.next_source_cursor is None:
                return None
            walk.source_cursor = walk.next_source_cursor
            walk.loaded = False
            walk.next_source_cursor = None
        self._walks[walk.walk_id] = walk
        self._cached_bytes += walk.cached_bytes
        while (
            len(self._walks) > self.max_active_walks
            or self._cached_bytes > self.cache_ceiling_bytes
        ):
            expired_id, expired = self._walks.popitem(last=False)
            self._cached_bytes -= expired.cached_bytes
            if expired_id == walk.walk_id:
                raise NativeHistoryLimitExceeded(
                    "Codex native history continuation could not fit the bounded cache",
                    actual_bytes=walk.cached_bytes,
                    limit_bytes=self.cache_ceiling_bytes,
                )
        return _encode_bounded_cursor(
            contract=contract,
            thread_id=thread_id,
            walk_id=walk.walk_id,
        )

    @staticmethod
    def _consume_cached_frames(walk: _BoundedWalk, consumed: int) -> None:
        if consumed < 1:
            return
        walk.cached_bytes -= sum(walk.frame_bytes[:consumed])
        walk.frames = walk.frames[consumed:]
        walk.frame_bytes = walk.frame_bytes[consumed:]

    async def _bounded_request(
        self,
        transport: CodexAppServerTransport,
        method: str,
        params: JsonObject,
    ) -> JsonObject:
        try:
            return await transport.request(method, params)
        except CodexAppServerRpcError as exc:
            raise NativeHistoryUnavailable(
                f"Codex bounded history request failed after capability acceptance: {exc}",
                code="bounded-rpc-failed",
            ) from exc

    async def _read_legacy_whole_thread_page_under_transport_fuse(
        self,
        transport: CodexAppServerTransport,
        *,
        thread_id: str,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage:
        """Compatibility path for contracts where both bounded RPCs are unavailable."""

        async def read_source_page(
            source_cursor: str | None,
        ) -> tuple[tuple[NativeEvidenceFrame, ...], str | None]:
            if source_cursor is not None:
                raise NativeHistoryUnavailable(
                    "Codex legacy whole-thread history produced an unexpected source cursor",
                    code="legacy-cursor-invalid",
                )
            try:
                result = await transport.request(
                    "thread/read",
                    {"threadId": thread_id, "includeTurns": True},
                )
                thread = required_object(
                    result.get("thread"), context="legacy thread/read response.thread"
                )
                returned_thread_id = required_text(
                    thread, "id", context="legacy thread/read response.thread"
                )
                if returned_thread_id != thread_id:
                    raise CodexAppServerError(
                        "legacy thread/read returned a different Codex thread id"
                    )
                return native_evidence_frames_from_thread(thread), None
            except NativeHistoryUnavailable:
                raise
            except CodexAppServerError as exc:
                raise NativeHistoryUnavailable(
                    f"Codex legacy whole-thread history is unavailable: {exc}",
                    code="legacy-read-failed",
                ) from exc

        return await self._scan_bounded_source(
            read_source_page,
            BoundedPageRequest(
                thread_id=thread_id, cursor=cursor, limit=limit, byte_budget=byte_budget
            ),
            contract="legacy-thread-read",
        )

    @staticmethod
    def _items_params(thread_id: str, source_cursor: str | None) -> JsonObject:
        params: JsonObject = {
            "threadId": thread_id,
            "limit": _BOUNDED_ITEM_PAGE_LIMIT,
            "sortDirection": "asc",
        }
        if source_cursor is not None:
            params["cursor"] = source_cursor
        return params

    @staticmethod
    def _turns_params(thread_id: str, source_cursor: str | None) -> JsonObject:
        params: JsonObject = {
            "threadId": thread_id,
            "limit": _BOUNDED_TURN_PAGE_LIMIT,
            "sortDirection": "asc",
            "itemsView": "full",
        }
        if source_cursor is not None:
            params["cursor"] = source_cursor
        return params

    @staticmethod
    def _parse_items_page(
        response: Mapping[str, object],
    ) -> tuple[tuple[NativeEvidenceFrame, ...], str | None]:
        try:
            data = required_list(response, "data", context="thread/items/list response")
            next_cursor = _optional_cursor(response, "nextCursor", "thread/items/list response")
            frames: list[NativeEvidenceFrame] = []
            seen: set[str] = set()
            for raw_entry in data:
                entry = required_object(raw_entry, context="thread/items/list entry")
                turn_id = required_text(entry, "turnId", context="thread/items/list entry")
                item = required_object(entry.get("item"), context="thread/items/list entry.item")
                item_id = required_text(item, "id", context="thread/items/list item")
                item_type = required_text(item, "type", context="thread/items/list item")
                if item_id in seen:
                    raise CodexAppServerError(
                        f"thread/items/list repeated item id {item_id!r} within a page"
                    )
                seen.add(item_id)
                frames.append(
                    NativeEvidenceFrame(
                        native_id=item_id,
                        native_parent_id=turn_id,
                        native_type=item_type,
                        created_at=None,
                        raw=item,
                    )
                )
            return tuple(frames), next_cursor
        except CodexAppServerError as exc:
            raise NativeHistoryUnavailable(
                f"Codex thread/items/list returned unreadable history: {exc}",
                code="bounded-shape-invalid",
            ) from exc

    @staticmethod
    def _parse_turns_page(
        response: Mapping[str, object],
    ) -> tuple[tuple[NativeEvidenceFrame, ...], str | None]:
        try:
            turns = required_list(response, "data", context="thread/turns/list response")
            next_cursor = _optional_cursor(response, "nextCursor", "thread/turns/list response")
            frames = native_evidence_frames_from_thread({"turns": turns})
            return frames, next_cursor
        except CodexAppServerError as exc:
            raise NativeHistoryUnavailable(
                f"Codex thread/turns/list returned unreadable history: {exc}",
                code="bounded-shape-invalid",
            ) from exc


def _optional_cursor(
    value: Mapping[str, object],
    key: str,
    context: str,
) -> str | None:
    cursor = value.get(key)
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise CodexAppServerError(f"{context}.{key} must be non-empty text or null")
    return cursor


def _encode_bounded_cursor(
    *,
    contract: SourceContract,
    thread_id: str,
    walk_id: str,
) -> str:
    payload = json.dumps(
        {
            "contract": contract,
            "threadId": thread_id,
            "walkId": walk_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{_CURSOR_PREFIX}{token}"


def _decode_bounded_cursor(
    cursor: str | None,
    *,
    contract: SourceContract,
    thread_id: str,
) -> str:
    if cursor is None or not cursor.startswith(_CURSOR_PREFIX):
        raise NativeHistoryUnavailable(
            "Codex bounded history cursor has the wrong purpose",
            code="cursor-invalid",
        )
    encoded = cursor.removeprefix(_CURSOR_PREFIX)
    try:
        padding = "=" * (-len(encoded) % 4)
        value = json.loads(base64.urlsafe_b64decode(encoded + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise NativeHistoryUnavailable(
            "Codex bounded history cursor is malformed",
            code="cursor-invalid",
        ) from exc
    if not isinstance(value, dict):
        raise NativeHistoryUnavailable(
            "Codex bounded history cursor payload must be an object",
            code="cursor-invalid",
        )
    if value.get("contract") != contract or value.get("threadId") != thread_id:
        raise NativeHistoryUnavailable(
            "Codex bounded history cursor does not match its thread contract",
            code="cursor-invalid",
        )
    walk_id = value.get("walkId")
    if not isinstance(walk_id, str) or not walk_id:
        raise NativeHistoryUnavailable(
            "Codex bounded history walk id is invalid",
            code="cursor-invalid",
        )
    return walk_id
