"""Async subprocess transport for Pi's strict RPC JSONL protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_control_models import LaunchSpec, ShutdownMode
from agents_remember.serving.pi_rpc_protocol import PiRpcJsonlDecoder, encode_pi_rpc_frame


class PiRpcTransport(Protocol):
    """The narrow process seam used by the Pi adapter and deterministic fakes."""

    async def start(self, launch: LaunchSpec) -> None: ...

    async def request(self, command: Mapping[str, object]) -> Mapping[str, object]: ...

    async def send(self, command: Mapping[str, object]) -> None: ...

    def events(self) -> AsyncIterator[Mapping[str, object]]: ...

    async def stop(self, mode: ShutdownMode) -> None: ...


class PiRpcSubprocess:
    """Own one Pi RPC child with correlated requests and bounded output buffering."""

    def __init__(
        self,
        *,
        max_frame_bytes: int = 1024 * 1024,
        event_queue_limit: int = 256,
        stderr_limit: int = 64 * 1024,
    ) -> None:
        if event_queue_limit < 1 or stderr_limit < 1:
            raise HarnessControlError("Pi RPC queue and stderr limits must be positive")
        self._decoder = PiRpcJsonlDecoder(max_frame_bytes=max_frame_bytes)
        self._events: asyncio.Queue[Mapping[str, object] | HarnessControlError | None] = (
            asyncio.Queue(maxsize=event_queue_limit)
        )
        self._stderr_limit = stderr_limit
        self._stderr = bytearray()
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Mapping[str, object]]] = {}
        self._failure: HarnessControlError | None = None
        self._stopping = False

    async def start(self, launch: LaunchSpec) -> None:
        if self._process is not None:
            raise HarnessControlError("Pi RPC transport is already started")
        self._process = await asyncio.create_subprocess_exec(
            *launch.argv,
            cwd=str(launch.cwd),
            env=dict(launch.env) if launch.env else None,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def request(self, command: Mapping[str, object]) -> Mapping[str, object]:
        request_id = command.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise HarnessControlError("Pi RPC correlated command requires a non-empty id")
        if request_id in self._pending:
            raise HarnessControlError(f"duplicate in-flight Pi RPC id: {request_id}")
        self._raise_if_failed(request_id)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send(command)
            return await future
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            raise
        except HarnessControlError:
            self._pending.pop(request_id, None)
            raise

    async def send(self, command: Mapping[str, object]) -> None:
        self._raise_if_failed(_optional_command_id(command))
        process = self._process
        if process is None or process.stdin is None or process.stdin.is_closing():
            raise HarnessAdapterDisconnectedError(
                "Pi RPC stdin is not connected", may_have_sent=False
            )
        payload = encode_pi_rpc_frame(command)
        try:
            process.stdin.write(payload)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise HarnessAdapterDisconnectedError(
                f"Pi RPC disconnected before writing: {exc}",
                may_have_sent=False,
                vendor_correlation_id=_optional_command_id(command),
            ) from exc
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise HarnessAdapterDisconnectedError(
                f"Pi RPC disconnected while writing: {exc}",
                may_have_sent=True,
                vendor_correlation_id=_optional_command_id(command),
            ) from exc

    async def _event_stream(self) -> AsyncIterator[Mapping[str, object]]:
        while True:
            item = await self._events.get()
            if item is None:
                raise HarnessAdapterDisconnectedError("Pi RPC stdout closed", may_have_sent=False)
            if isinstance(item, HarnessControlError):
                raise item
            yield item

    def events(self) -> AsyncIterator[Mapping[str, object]]:
        return self._event_stream()

    async def stop(self, mode: ShutdownMode) -> None:
        process = self._process
        if process is None:
            return
        self._stopping = True
        self._signal_stop(process, mode)
        await self._wait_for_exit(process)
        await self._cancel_readers()
        self._fail_pending(
            HarnessAdapterDisconnectedError("Pi RPC transport stopped", may_have_sent=False)
        )
        await self._events.put(None)

    def _signal_stop(self, process: asyncio.subprocess.Process, mode: ShutdownMode) -> None:
        if mode == "graceful" and process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            return
        if process.returncode is None:
            process.terminate()

    async def _cancel_readers(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader_task, self._stderr_task) if task is not None),
            return_exceptions=True,
        )

    @property
    def stderr_tail(self) -> str:
        return bytes(self._stderr).decode("utf-8", errors="replace")

    async def _read_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while chunk := await process.stdout.read(65536):
                for frame in self._decoder.feed(chunk):
                    await self._dispatch(frame)
            for frame in self._decoder.finish():
                await self._dispatch(frame)
        except HarnessControlError as exc:
            await self._fail(exc)
            return
        if not self._stopping:
            await self._fail(
                HarnessAdapterDisconnectedError(
                    "Pi RPC stdout closed unexpectedly", may_have_sent=False
                )
            )

    async def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        while chunk := await process.stderr.read(65536):
            self._stderr.extend(chunk)
            overflow = len(self._stderr) - self._stderr_limit
            if overflow > 0:
                del self._stderr[:overflow]

    async def _dispatch(self, frame: Mapping[str, object]) -> None:
        frame_type = frame.get("type")
        if not isinstance(frame_type, str) or not frame_type:
            raise HarnessControlError("Pi RPC frame requires a non-empty type")
        if frame_type == "response":
            request_id = frame.get("id")
            if not isinstance(request_id, str) or not request_id:
                raise HarnessControlError("Pi RPC response requires a non-empty correlation id")
            future = self._pending.pop(request_id, None)
            if future is None:
                # Cancelled callers leave no consumable future. A later correlated response is
                # therefore stale and can be dropped without retaining an unbounded tombstone.
                return
            if not future.done():
                future.set_result(frame)
            return
        await self._events.put(frame)

    async def _fail(self, error: HarnessControlError) -> None:
        if self._failure is not None:
            return
        self._failure = error
        self._fail_pending(error)
        await self._events.put(error)

    def _fail_pending(self, error: HarnessControlError) -> None:
        for request_id, future in tuple(self._pending.items()):
            if not future.done():
                if isinstance(error, HarnessAdapterDisconnectedError):
                    future.set_exception(
                        HarnessAdapterDisconnectedError(
                            str(error),
                            may_have_sent=True,
                            vendor_correlation_id=request_id,
                        )
                    )
                else:
                    future.set_exception(HarnessControlError(str(error)))
        self._pending.clear()

    def _raise_if_failed(self, correlation_id: str | None) -> None:
        failure = self._failure
        if failure is None:
            return
        if isinstance(failure, HarnessAdapterDisconnectedError):
            raise HarnessAdapterDisconnectedError(
                str(failure),
                may_have_sent=False,
                vendor_correlation_id=correlation_id,
            )
        raise HarnessControlError(str(failure))

    @staticmethod
    async def _wait_for_exit(process: asyncio.subprocess.Process) -> None:
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
            return
        except TimeoutError:
            if process.returncode is None:
                process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()


def _optional_command_id(command: Mapping[str, object]) -> str | None:
    request_id = command.get("id")
    return request_id if isinstance(request_id, str) and request_id else None
