"""Bounded JSONL transport for the Codex app-server protocol."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Protocol

from agents_remember.errors import (
    CodexAppServerError,
    CodexAppServerRpcError,
    HarnessAdapterDisconnectedError,
)
from agents_remember.models.conversations.control_wire import (
    LaunchSpec,
)
from agents_remember.serving.harness_control_models import (
    ShutdownMode,
)

CODEX_APP_SERVER_PROTOCOL = "codex-app-server"
# Compatibility precedent: Codex's remote app-server client accepts 128 MiB WebSocket
# messages/frames. This is an emergency framing fuse for malformed/runaway JSONL only;
# native-history acquisition imposes its own source paging and output budgets.
CODEX_REMOTE_COMPATIBILITY_CEILING_BYTES = 128 << 20
DEFAULT_MAX_MESSAGE_BYTES = CODEX_REMOTE_COMPATIBILITY_CEILING_BYTES

JsonObject = dict[str, object]
RequestId = str | int
WriteGuard = Callable[[], None]


class CodexAppServerTransport(Protocol):
    """One bidirectional app-server connection owned by the Codex adapter."""

    async def start(self, launch: LaunchSpec) -> None: ...

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: WriteGuard | None = None,
    ) -> JsonObject: ...

    async def notify(self, method: str, params: Mapping[str, object]) -> None: ...

    def messages(self) -> AsyncIterator[JsonObject]: ...

    async def respond(self, request_id: RequestId, result: Mapping[str, object]) -> None: ...

    async def respond_error(
        self,
        request_id: RequestId,
        *,
        code: int,
        message: str,
    ) -> None: ...

    async def stop(self, mode: ShutdownMode) -> None: ...


class CodexStdioTransport:
    """Newline-delimited JSON transport over one exact app-server subprocess.

    The message and event bounds are security/stability boundaries: a subprocess is an external
    protocol peer, so an unterminated or unconsumed stream must not grow the hosted process without
    limit. Crossing either bound fails the adapter loudly.
    """

    def __init__(
        self,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        event_queue_limit: int = 256,
    ) -> None:
        if max_message_bytes < 1 or event_queue_limit < 1:
            raise CodexAppServerError("Codex transport bounds must be positive")
        self._max_message_bytes = max_message_bytes
        self._events: asyncio.Queue[JsonObject | Exception | None] = asyncio.Queue(
            maxsize=event_queue_limit
        )
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, tuple[str, asyncio.Future[JsonObject]]] = {}
        self._next_request_id = 1
        self._write_lock = asyncio.Lock()
        self._closing = False

    async def start(self, launch: LaunchSpec) -> None:
        if self._process is not None:
            raise CodexAppServerError("Codex transport is already started")
        if not launch.argv:
            raise CodexAppServerError("Codex app-server launch argv cannot be empty")
        environment = {**os.environ, **launch.env}
        self._process = await asyncio.create_subprocess_exec(
            *launch.argv,
            cwd=launch.cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=self._max_message_bytes + 1,
        )
        self._reader_task = asyncio.create_task(self._read_messages())

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: WriteGuard | None = None,
    ) -> JsonObject:
        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        try:
            await self._write(
                {"id": request_id, "method": method, "params": dict(params)},
                before_write=before_write,
            )
            return await future
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            raise
        except HarnessAdapterDisconnectedError:
            self._pending.pop(request_id, None)
            raise
        except Exception:
            # A final pre-write guard can reject after the pending RPC future was installed. It
            # certified zero bytes, so this future must be removed before the caller may requeue.
            self._pending.pop(request_id, None)
            raise

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        await self._write({"method": method, "params": dict(params)})

    async def respond(self, request_id: RequestId, result: Mapping[str, object]) -> None:
        await self._write({"id": request_id, "result": dict(result)})

    async def respond_error(
        self,
        request_id: RequestId,
        *,
        code: int,
        message: str,
    ) -> None:
        await self._write({"id": request_id, "error": {"code": code, "message": message}})

    async def _message_stream(self) -> AsyncIterator[JsonObject]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def messages(self) -> AsyncIterator[JsonObject]:
        return self._message_stream()

    async def stop(self, mode: ShutdownMode) -> None:
        process = self._process
        if process is None:
            return
        self._closing = True
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None and mode == "graceful":
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.terminate()
        elif process.returncode is None:
            process.terminate()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._reader_task is not None:
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._fail_pending(CodexAppServerError("Codex app-server transport stopped"))
        self._offer_event(None)

    async def _write(
        self,
        message: Mapping[str, object],
        *,
        before_write: WriteGuard | None = None,
    ) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise HarnessAdapterDisconnectedError(
                "Codex app-server transport is not connected",
                may_have_sent=False,
            )
        try:
            payload = (
                json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise CodexAppServerError("Codex JSON-RPC payload is not JSON serializable") from exc
        async with self._write_lock:
            try:
                if before_write is not None:
                    before_write()
                # No await is permitted between the guard and first process write. That closes
                # the stale-active-turn window rather than adding a best-effort pre-check.
                process.stdin.write(payload)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                raise HarnessAdapterDisconnectedError(
                    "Codex app-server disconnected while writing JSON-RPC",
                    may_have_sent=True,
                ) from exc

    async def _read_messages(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    if not self._closing:
                        self._disconnect("Codex app-server stdout closed")
                    return
                # The fuse is a JSON payload bound. JSONL's one record delimiter is framing,
                # not payload: exactly 128 MiB plus its required newline is valid; the first
                # payload byte beyond the fuse is fatal to this shared transport.
                payload = line[:-1] if line.endswith(b"\n") else line
                if len(payload) > self._max_message_bytes:
                    raise CodexAppServerError("Codex app-server message exceeded the byte limit")
                message = self._decode(payload)
                if "method" in message:
                    self._offer_event(message)
                else:
                    self._resolve_response(message)
        except CodexAppServerError as exc:
            self._fail_pending(exc)
            self._offer_event(exc)
        except ValueError:
            error = CodexAppServerError(
                "Codex app-server message exceeded the configured stream limit"
            )
            self._fail_pending(error)
            self._offer_event(error)

    @staticmethod
    def _decode(line: bytes) -> JsonObject:
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexAppServerError("Codex app-server emitted malformed JSONL") from exc
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise CodexAppServerError("Codex app-server message must be a JSON object")
        return value

    def _resolve_response(self, message: JsonObject) -> None:
        response_id = message.get("id")
        if not isinstance(response_id, int) or isinstance(response_id, bool) or response_id < 1:
            raise CodexAppServerError("Codex app-server response has an invalid request id")
        pending = self._pending.pop(response_id, None)
        if pending is None:
            # A caller may cancel after the request is written. Once its future is removed, a
            # syntactically valid late response cannot satisfy any live request and is discarded.
            return
        method, future = pending
        error = message.get("error")
        if error is not None:
            if not isinstance(error, dict):
                future.set_exception(CodexAppServerError("Codex JSON-RPC error must be an object"))
                return
            code = error.get("code")
            detail = error.get("message")
            if not isinstance(code, int) or not isinstance(detail, str):
                future.set_exception(CodexAppServerError("Codex JSON-RPC error is malformed"))
                return
            future.set_exception(CodexAppServerRpcError(method, code, detail))
            return
        result = message.get("result")
        if not isinstance(result, dict):
            future.set_exception(CodexAppServerError("Codex JSON-RPC result must be an object"))
            return
        future.set_result(result)

    def _disconnect(self, detail: str) -> None:
        error = HarnessAdapterDisconnectedError(detail, may_have_sent=True)
        self._fail_pending(error)
        self._offer_event(error)

    def _fail_pending(self, error: Exception) -> None:
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _offer_event(self, event: JsonObject | Exception | None) -> None:
        if self._events.full():
            overflow = CodexAppServerError("Codex app-server event queue is full")
            self._fail_pending(overflow)
            while not self._events.empty():
                self._events.get_nowait()
            self._events.put_nowait(overflow)
            return
        self._events.put_nowait(event)
