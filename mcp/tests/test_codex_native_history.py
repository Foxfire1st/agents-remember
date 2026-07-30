from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy

import pytest
from agents_remember.errors import (
    CodexAppServerRpcError,
    NativeHistoryLimitExceeded,
    NativeHistoryUnavailable,
)
from agents_remember.serving.codex_app_server_history import CodexNativeHistoryReader
from agents_remember.serving.codex_app_server_protocol import JsonObject, RequestId
from agents_remember.serving.codex_app_server_state import native_evidence_frames_from_thread
from agents_remember.serving.harness_control_client import _decode_control_response
from agents_remember.serving.harness_control_ipc import (
    _error_response,
    _raise_control_response_error,
)
from agents_remember.serving.harness_control_models import (
    LaunchSpec,
    ShutdownMode,
    native_evidence_frame_wire_bytes,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class HistoryTransport:
    def __init__(self) -> None:
        self.responses: dict[str, deque[JsonObject | Exception]] = defaultdict(deque)
        self.requests: list[tuple[str, JsonObject]] = []

    def queue(self, method: str, response: JsonObject | Exception) -> None:
        self.responses[method].append(response)

    async def start(self, launch: LaunchSpec) -> None:
        del launch

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> JsonObject:
        if before_write is not None:
            before_write()
        self.requests.append((method, dict(params)))
        response = self.responses[method].popleft()
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        del method, params

    async def _messages(self) -> AsyncIterator[JsonObject]:
        if False:
            yield {}

    def messages(self) -> AsyncIterator[JsonObject]:
        return self._messages()

    async def respond(self, request_id: RequestId, result: Mapping[str, object]) -> None:
        del request_id, result

    async def respond_error(
        self,
        request_id: RequestId,
        *,
        code: int,
        message: str,
    ) -> None:
        del request_id, code, message

    async def stop(self, mode: ShutdownMode) -> None:
        del mode


def item_page(
    item_id: str,
    *,
    text: str = "content",
    next_cursor: str | None,
) -> JsonObject:
    return {
        "data": [
            {
                "turnId": f"turn-{item_id}",
                "item": {"id": item_id, "type": "agentMessage", "text": text},
            }
        ],
        "nextCursor": next_cursor,
        "backwardsCursor": None,
    }


def unavailable(method: str) -> CodexAppServerRpcError:
    return CodexAppServerRpcError(method, -32601, "method not found")


def legacy_thread_response(
    thread_id: str,
    *,
    item_count: int,
    text_bytes: int = 700,
) -> JsonObject:
    return {
        "thread": {
            "id": thread_id,
            "turns": [
                {
                    "id": f"turn-{thread_id}",
                    "items": [
                        {
                            "id": f"{thread_id}-item-{index}",
                            "type": "agentMessage",
                            "text": "x" * text_bytes,
                        }
                        for index in range(item_count)
                    ],
                }
            ],
        }
    }


@pytest.mark.anyio
async def test_bounded_items_are_probed_and_opaque_cursor_consumes_each_source_page_once() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", item_page("item-1", next_cursor="source-1"))
    transport.queue("thread/items/list", item_page("item-2", next_cursor="source-2"))
    transport.queue("thread/items/list", item_page("item-3", next_cursor=None))
    reader = CodexNativeHistoryReader()

    first = await reader.read_page(
        transport,
        thread_id="agent-1",
        cursor=None,
        limit=2,
        byte_budget=48 * 1024,
    )
    assert [frame.native_id for frame in first.frames] == ["item-1", "item-2"]
    assert first.next_cursor is not None
    assert first.next_cursor.startswith("ar-cnh1.")
    second = await reader.read_page(
        transport,
        thread_id="agent-1",
        cursor=first.next_cursor,
        limit=2,
        byte_budget=48 * 1024,
    )
    assert [frame.native_id for frame in second.frames] == ["item-3"]
    assert second.next_cursor is None
    assert [params["cursor"] for method, params in transport.requests if "cursor" in params] == [
        "source-1",
        "source-2",
    ]
    assert all(params["limit"] == 1 for method, params in transport.requests)
    assert not transport.responses["thread/read"]


@pytest.mark.anyio
async def test_turns_list_is_used_when_bounded_items_method_is_unavailable() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue(
        "thread/turns/list",
        {
            "data": [
                {
                    "id": "turn-1",
                    "items": [{"id": "item-1", "type": "agentMessage", "text": "ok"}],
                }
            ],
            "nextCursor": None,
            "backwardsCursor": None,
        },
    )
    page = await CodexNativeHistoryReader().read_page(
        transport,
        thread_id="agent-1",
        cursor=None,
        limit=10,
        byte_budget=4096,
    )
    assert [frame.native_id for frame in page.frames] == ["item-1"]
    assert transport.requests[1] == (
        "thread/turns/list",
        {
            "threadId": "agent-1",
            "limit": 1,
            "sortDirection": "asc",
            "itemsView": "full",
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize("item_count", [10, 20, 40])
async def test_turns_full_continuation_requests_and_decodes_each_source_turn_once(
    item_count: int,
) -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue(
        "thread/turns/list",
        {
            "data": [
                {
                    "id": "turn-many",
                    "items": [
                        {
                            "id": f"item-{index}",
                            "type": "agentMessage",
                            "text": "x" * 700,
                        }
                        for index in range(item_count)
                    ],
                }
            ],
            "nextCursor": None,
            "backwardsCursor": None,
        },
    )
    reader = CodexNativeHistoryReader()

    cursor: str | None = None
    observed: list[str] = []
    while True:
        page = await reader.read_page(
            transport,
            thread_id="agent-1",
            cursor=cursor,
            limit=200,
            byte_budget=1200,
        )
        observed.extend(frame.native_id for frame in page.frames)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert observed == [f"item-{index}" for index in range(item_count)]
    # The installed 0.145 contract pages by turn, not item. Stateful AR continuation must retain
    # only the unconsumed suffix of this response; another request would also re-decode all items.
    assert [method for method, _params in transport.requests].count("thread/turns/list") == 1


@pytest.mark.anyio
async def test_abandoned_continuations_evict_oldest_walk_at_the_hard_cap() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    for thread_index in range(3):
        transport.queue(
            "thread/turns/list",
            {
                "data": [
                    {
                        "id": f"turn-{thread_index}",
                        "items": [
                            {
                                "id": f"item-{thread_index}-{item_index}",
                                "type": "agentMessage",
                                "text": "bounded",
                            }
                            for item_index in range(2)
                        ],
                    }
                ],
                "nextCursor": None,
                "backwardsCursor": None,
            },
        )
    reader = CodexNativeHistoryReader(max_active_walks=2)

    cursors: list[str] = []
    for thread_index in range(3):
        page = await reader.read_page(
            transport,
            thread_id=f"agent-{thread_index}",
            cursor=None,
            limit=1,
            byte_budget=4096,
        )
        assert [frame.native_id for frame in page.frames] == [f"item-{thread_index}-0"]
        assert page.next_cursor is not None
        cursors.append(page.next_cursor)

    with pytest.raises(NativeHistoryUnavailable) as expired:
        await reader.read_page(
            transport,
            thread_id="agent-0",
            cursor=cursors[0],
            limit=1,
            byte_budget=4096,
        )
    assert expired.value.code == "cursor-expired"
    retained = await reader.read_page(
        transport,
        thread_id="agent-1",
        cursor=cursors[1],
        limit=1,
        byte_budget=4096,
    )
    assert [frame.native_id for frame in retained.frames] == ["item-1-1"]
    assert [method for method, _params in transport.requests].count("thread/turns/list") == 3


@pytest.mark.anyio
async def test_continuation_refuses_when_one_retained_suffix_exceeds_cache_cap() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue(
        "thread/turns/list",
        {
            "data": [
                {
                    "id": "turn-cache",
                    "items": [
                        {
                            "id": f"item-{index}",
                            "type": "agentMessage",
                            "text": "x" * 256,
                        }
                        for index in range(2)
                    ],
                }
            ],
            "nextCursor": None,
            "backwardsCursor": None,
        },
    )
    reader = CodexNativeHistoryReader(cache_ceiling_bytes=1)
    with pytest.raises(NativeHistoryLimitExceeded, match="bounded cache"):
        await reader.read_page(
            transport,
            thread_id="agent-cache",
            cursor=None,
            limit=1,
            byte_budget=4096,
        )


@pytest.mark.anyio
async def test_two_cursor_cycle_terminates_typed_without_re_requesting_a_source_page() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", item_page("item-0", next_cursor="A"))
    transport.queue("thread/items/list", item_page("item-1", next_cursor="B"))
    transport.queue("thread/items/list", item_page("item-2", next_cursor="A"))
    reader = CodexNativeHistoryReader()

    cursor: str | None = None
    for expected in ("item-0", "item-1", "item-2"):
        page = await reader.read_page(
            transport,
            thread_id="agent-cycle",
            cursor=cursor,
            limit=1,
            byte_budget=4096,
        )
        assert [frame.native_id for frame in page.frames] == [expected]
        cursor = page.next_cursor
        assert cursor is not None

    with pytest.raises(NativeHistoryUnavailable) as raised:
        await reader.read_page(
            transport,
            thread_id="agent-cycle",
            cursor=cursor,
            limit=1,
            byte_budget=4096,
        )
    assert raised.value.code == "source-cursor-cycle"
    assert [
        params.get("cursor")
        for method, params in transport.requests
        if method == "thread/items/list"
    ] == [None, "A", "B"]


@pytest.mark.anyio
async def test_legacy_whole_thread_read_requires_both_bounded_methods_to_be_unavailable() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue("thread/turns/list", unavailable("thread/turns/list"))
    transport.queue(
        "thread/read",
        {
            "thread": {
                "id": "agent-1",
                "turns": [
                    {
                        "id": "turn-1",
                        "items": [{"id": "item-1", "type": "agentMessage", "text": "ok"}],
                    }
                ],
            }
        },
    )
    reader = CodexNativeHistoryReader()
    page = await reader.read_page(
        transport,
        thread_id="agent-1",
        cursor=None,
        limit=10,
        byte_budget=4096,
    )
    assert reader.contract == "legacy-thread-read"
    assert [frame.native_id for frame in page.frames] == ["item-1"]
    assert transport.requests[-1] == (
        "thread/read",
        {"threadId": "agent-1", "includeTurns": True},
    )


@pytest.mark.anyio
async def test_legacy_complete_response_aggregate_over_ceiling_is_typed() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue("thread/turns/list", unavailable("thread/turns/list"))
    response = legacy_thread_response("agent-large", item_count=2)
    transport.queue("thread/read", response)
    thread = response["thread"]
    assert isinstance(thread, Mapping)
    frame_bytes = [
        native_evidence_frame_wire_bytes(frame)
        for frame in native_evidence_frames_from_thread(thread)
    ]
    assert len(frame_bytes) == 2
    assert all(size < 1000 for size in frame_bytes)
    assert sum(frame_bytes) > 1000

    reader = CodexNativeHistoryReader(materialization_ceiling_bytes=1000)
    with pytest.raises(NativeHistoryLimitExceeded) as raised:
        await reader.read_page(
            transport,
            thread_id="agent-large",
            cursor=None,
            limit=200,
            byte_budget=4096,
        )

    assert raised.value.actual_bytes == sum(frame_bytes)
    assert raised.value.limit_bytes == 1000
    assert [method for method, _params in transport.requests].count("thread/read") == 1


@pytest.mark.anyio
async def test_legacy_multipage_walk_reads_complete_response_once() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue("thread/turns/list", unavailable("thread/turns/list"))
    transport.queue(
        "thread/read",
        legacy_thread_response("agent-pages", item_count=3),
    )
    reader = CodexNativeHistoryReader(materialization_ceiling_bytes=4096)

    cursor: str | None = None
    observed: list[str] = []
    page_count = 0
    while True:
        page = await reader.read_page(
            transport,
            thread_id="agent-pages",
            cursor=cursor,
            limit=200,
            byte_budget=1000,
        )
        page_count += 1
        observed.extend(frame.native_id for frame in page.frames)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert page_count == 3
    assert observed == [f"agent-pages-item-{index}" for index in range(3)]
    assert [method for method, _params in transport.requests].count("thread/read") == 1


@pytest.mark.anyio
async def test_evicted_legacy_continuation_expires_without_refetch() -> None:
    transport = HistoryTransport()
    transport.queue("thread/items/list", unavailable("thread/items/list"))
    transport.queue("thread/turns/list", unavailable("thread/turns/list"))
    transport.queue(
        "thread/read",
        legacy_thread_response("agent-old", item_count=2, text_bytes=32),
    )
    transport.queue(
        "thread/read",
        legacy_thread_response("agent-new", item_count=2, text_bytes=32),
    )
    reader = CodexNativeHistoryReader(
        materialization_ceiling_bytes=4096,
        max_active_walks=1,
    )

    old_page = await reader.read_page(
        transport,
        thread_id="agent-old",
        cursor=None,
        limit=1,
        byte_budget=4096,
    )
    assert old_page.next_cursor is not None
    new_page = await reader.read_page(
        transport,
        thread_id="agent-new",
        cursor=None,
        limit=1,
        byte_budget=4096,
    )
    assert new_page.next_cursor is not None

    with pytest.raises(NativeHistoryUnavailable) as raised:
        await reader.read_page(
            transport,
            thread_id="agent-old",
            cursor=old_page.next_cursor,
            limit=1,
            byte_budget=4096,
        )
    assert raised.value.code == "cursor-expired"
    assert [method for method, _params in transport.requests].count("thread/read") == 2


@pytest.mark.anyio
async def test_recognized_bounded_rpc_failure_never_silently_falls_back() -> None:
    transport = HistoryTransport()
    transport.queue(
        "thread/items/list",
        CodexAppServerRpcError("thread/items/list", -32600, "thread is not materialized yet"),
    )
    with pytest.raises(NativeHistoryUnavailable) as raised:
        await CodexNativeHistoryReader().read_page(
            transport,
            thread_id="agent-1",
            cursor=None,
            limit=10,
            byte_budget=4096,
        )
    assert raised.value.code == "bounded-rpc-refused"
    assert [method for method, _params in transport.requests] == ["thread/items/list"]


@pytest.mark.anyio
async def test_source_response_over_post_transport_materialization_ceiling_is_typed() -> None:
    transport = HistoryTransport()
    transport.queue(
        "thread/items/list",
        item_page("item-big", text="x" * 4096, next_cursor=None),
    )
    reader = CodexNativeHistoryReader(materialization_ceiling_bytes=1024)
    with pytest.raises(NativeHistoryLimitExceeded) as raised:
        await reader.read_page(
            transport,
            thread_id="agent-1",
            cursor=None,
            limit=10,
            byte_budget=4096,
        )
    assert raised.value.actual_bytes > raised.value.limit_bytes


def test_native_history_limit_outcome_survives_both_control_ipc_clients() -> None:
    error = NativeHistoryLimitExceeded(
        "one selected child item is too large",
        actual_bytes=2048,
        limit_bytes=1024,
    )
    response = _error_response(error)
    assert response["status"] == "native-history-limit-exceeded"

    with pytest.raises(NativeHistoryLimitExceeded) as async_client:
        _raise_control_response_error(response)
    assert async_client.value.actual_bytes == 2048
    assert async_client.value.limit_bytes == 1024

    encoded = json.dumps(response).encode()
    with pytest.raises(NativeHistoryLimitExceeded) as sync_client:
        _decode_control_response(encoded)
    assert sync_client.value.code == "materialization-limit"
