"""The owned eve runtime process and its native HTTP session client.

One AR bridge epoch owns exactly one eve application process and one durable eve session. This
module is the whole process boundary: it starts the pinned application, waits for eve's own
health route, speaks the documented session routes, and reads the durable NDJSON stream from an
absolute event index.

Two ownership rules are enforced here rather than by convention:

* closing a stream subscriber cancels the HTTP read only -- the durable session keeps running,
  because eve's turns survive a disconnected reader;
* stopping the adapter stops the process this adapter started and nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.eve_protocol import (
    ACCEPTED_STATUS,
    CREATE_ACCEPTED_STATUS,
    EVE_HEALTH_PATH,
    EVE_SESSION_PATH,
    EVE_STREAM_FORMAT,
    NO_ACTIVE_TURN_STATUS,
    SESSION_NOT_ACTIVE_CODE,
    SESSION_NOT_ACTIVE_STATUS,
    TURN_POLICY_QUEUE,
    EveRuntimeLaunch,
    EveStreamEvent,
    eve_session_control_route,
    eve_session_route,
    eve_session_stream_route,
    parse_session_acceptance,
    parse_stream_tail_index,
    parse_stream_version,
)
from agents_remember.serving.eve_runtime_launch import resolve_node_executable
from agents_remember.serving.eve_stream_cursor import EveNdjsonDecoder

DEFAULT_HEALTH_TIMEOUT_SECONDS = 120.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_READ_CHUNK_BYTES = 65536
DEFAULT_STREAM_READ_SECONDS = 10.0
"""How long one stream connection may sit silent before the reader reconnects from its cursor.

A parked session legitimately emits nothing for a long time, and a durable reader must outlive
that silence without either giving up on the session or holding one socket forever. Bounding the
read and reconnecting from the persisted absolute index satisfies both: the events are already
durable, so a reconnect loses nothing and duplicates nothing.
"""


def create_session_body(message: str) -> dict[str, object]:
    """The body that starts a durable session.

    The queued turn policy is spelled here because this adapter's ordinary deliveries must never
    inherit eve's cancellation-backed ``steer`` default. eve's own client omits the field on the
    create route (it relies on the channel default there) and sends it on follow-ups; this module
    spells it on both, because the policy is this adapter's contract at either entry point.
    """

    return {"message": message, "turnPolicy": TURN_POLICY_QUEUE}


def follow_up_body(message: str) -> dict[str, object]:
    """The body that continues an existing durable session.

    A follow-up is where eve's default genuinely steers -- an active turn would be cancelled -- so
    the queued policy is spelled on the wire here rather than left to the runtime's channel
    default. This is the field eve's own client sends for ``Session.send(...)``.
    """

    return {"message": message, "turnPolicy": TURN_POLICY_QUEUE}


def cancel_turn_body(turn_id: str | None) -> dict[str, object] | None:
    """The body that cancels one observed turn; ``None`` cancels whatever turn is active."""

    return {"turnId": turn_id} if turn_id is not None else None


class EveRuntimeTransport(Protocol):
    """The narrow native seam the eve adapter and its deterministic fakes implement."""

    @property
    def endpoint(self) -> str: ...

    async def start(self) -> None: ...

    async def health(self) -> Mapping[str, object]: ...

    async def create_session(self, message: str) -> tuple[str, str | None]: ...

    async def send_message(self, session_id: str, message: str) -> tuple[str, str | None]: ...

    async def send_input_responses(
        self, session_id: str, responses: Sequence[Mapping[str, object]]
    ) -> None: ...

    async def cancel_turn(
        self, session_id: str, *, turn_id: str | None
    ) -> Mapping[str, object]: ...

    def stream(self, session_id: str, *, start_index: int) -> AsyncIterator[EveStreamEvent]: ...

    async def stop(self, mode: str) -> None: ...


class EveRuntimeProcess:
    """Own one ``eve dev --no-ui`` child process and its HTTP session client."""

    def __init__(
        self,
        launch: EveRuntimeLaunch,
        *,
        health_timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        stream_read_seconds: float = DEFAULT_STREAM_READ_SECONDS,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        if health_timeout_seconds <= 0 or request_timeout_seconds <= 0 or stream_read_seconds <= 0:
            raise HarnessControlError("eve runtime timeouts must be positive")
        self._launch = launch
        self._health_timeout = health_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._stream_read = stream_read_seconds
        self._client_factory = client_factory or self._default_client
        self._process: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._stderr = bytearray()
        self._stderr_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._health: Mapping[str, object] = {}

    @property
    def endpoint(self) -> str:
        return f"http://{self._launch.host}:{self._launch.port}"

    @property
    def health_payload(self) -> Mapping[str, object]:
        return self._health

    @property
    def stderr_tail(self) -> str:
        return bytes(self._stderr).decode("utf-8", errors="replace")

    async def start(self) -> None:
        if self._process is not None:
            raise HarnessControlError("eve runtime process is already started")
        entrypoint = Path(self._launch.runtime_root) / "node_modules" / "eve" / "bin" / "eve.js"
        if not entrypoint.is_file():
            raise HarnessControlError(
                f"the eve application at {self._launch.runtime_root} has no installed eve package; "
                "run its documented dependency install before launching it"
            )
        argv = (
            self._launch.node_executable or resolve_node_executable(self._launch.env),
            str(entrypoint),
            "dev",
            "--no-ui",
            "--port",
            str(self._launch.port),
            "--host",
            self._launch.host,
        )
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._launch.runtime_root,
            env=dict(self._launch.env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._client = self._client_factory()
        await self._await_health()

    async def health(self) -> Mapping[str, object]:
        payload = await self._get_json(EVE_HEALTH_PATH)
        if payload.get("ok") is not True or payload.get("status") != "ready":
            raise HarnessControlError("eve health route did not report a ready application")
        self._health = payload
        return payload

    async def create_session(self, message: str) -> tuple[str, str | None]:
        response = await self._post_json(
            EVE_SESSION_PATH, create_session_body(message), expect=(CREATE_ACCEPTED_STATUS,)
        )
        return parse_session_acceptance(response.body, response.headers)

    async def send_message(self, session_id: str, message: str) -> tuple[str, str | None]:
        response = await self._post_json(
            eve_session_route(session_id),
            follow_up_body(message),
            expect=(ACCEPTED_STATUS,),
        )
        return parse_session_acceptance(response.body, response.headers)

    async def send_input_responses(
        self, session_id: str, responses: Sequence[Mapping[str, object]]
    ) -> None:
        if not responses:
            raise HarnessControlError("eve input responses require at least one entry")
        await self._post_json(
            eve_session_route(session_id),
            {"inputResponses": [dict(entry) for entry in responses]},
            expect=(ACCEPTED_STATUS,),
        )

    async def cancel_turn(self, session_id: str, *, turn_id: str | None) -> Mapping[str, object]:
        response = await self._post_json(
            eve_session_control_route(session_id, "cancel"),
            cancel_turn_body(turn_id),
            expect=(ACCEPTED_STATUS, NO_ACTIVE_TURN_STATUS),
        )
        return response.body if isinstance(response.body, Mapping) else {}

    async def stream(self, session_id: str, *, start_index: int) -> AsyncIterator[EveStreamEvent]:
        """Read the durable stream from one absolute index until the connection ends.

        The iterator ends when the connection closes or the bounded read elapses. Neither is a
        session failure: the event record is durable, so a caller reconnects from the index it
        already consumed. Read framing is line-based rather than buffered, because a session
        stream is an unbounded record and a chunked reader would wait for a buffer size that may
        never arrive.
        """

        client = self._require_client()
        route = eve_session_stream_route(session_id, start_index=start_index)
        decoder = EveNdjsonDecoder(start_index=start_index)
        try:
            async with client.stream(
                "GET",
                route,
                headers={"accept": EVE_STREAM_FORMAT},
                timeout=httpx.Timeout(self._request_timeout, read=self._stream_read),
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", errors="replace")
                    raise HarnessAdapterDisconnectedError(
                        f"eve stream refused with {response.status_code}: {detail}",
                        may_have_sent=False,
                    )
                parse_stream_version(dict(response.headers))
                parse_stream_tail_index(dict(response.headers))
                async for line in response.aiter_lines():
                    for event in decoder.feed(line.encode("utf-8") + b"\n"):
                        yield event
        except httpx.ReadTimeout:
            # A silent bounded read is the expected shape of a parked session; the caller decides
            # whether to reopen from the cursor.
            pass
        except httpx.HTTPError as exc:
            raise HarnessAdapterDisconnectedError(
                f"eve stream connection ended: {exc}", may_have_sent=False
            ) from exc
        for event in decoder.finish():
            yield event

    async def stop(self, mode: str) -> None:
        self._stopping = True
        client = self._client
        self._client = None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()
        process = self._process
        self._process = None
        if process is not None:
            await _terminate(process, graceful=mode == "graceful")
        task = self._stderr_task
        self._stderr_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- internals ------------------------------------------------------------------------

    def _default_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.endpoint,
            timeout=httpx.Timeout(self._request_timeout),
        )

    async def _await_health(self) -> None:
        deadline = asyncio.get_running_loop().time() + self._health_timeout
        last: str = "no attempt completed"
        while asyncio.get_running_loop().time() < deadline:
            process = self._process
            if process is not None and process.returncode is not None:
                raise HarnessControlError(
                    "eve runtime exited before it became healthy: " + self._stderr_excerpt()
                )
            try:
                await self.health()
                return
            except (httpx.HTTPError, HarnessControlError) as exc:
                last = str(exc)
            await asyncio.sleep(0.25)
        raise HarnessControlError(
            f"eve runtime did not become healthy within {self._health_timeout:g}s: {last}"
        )

    async def _get_json(self, route: str) -> Mapping[str, object]:
        client = self._require_client()
        try:
            response = await client.get(route)
        except httpx.HTTPError as exc:
            raise HarnessAdapterDisconnectedError(
                f"eve route {route} is unreachable: {exc}", may_have_sent=False
            ) from exc
        return _decoded_object(response, route)

    async def _post_json(
        self,
        route: str,
        body: Mapping[str, object] | None,
        *,
        expect: tuple[int, ...],
    ) -> _JsonResponse:
        client = self._require_client()
        try:
            response = await client.post(route, json=dict(body) if body is not None else None)
        except httpx.HTTPError as exc:
            # The request may or may not have reached eve; the caller must reconcile rather than
            # repeat it, so the ambiguity is carried on the error instead of hidden.
            raise HarnessAdapterDisconnectedError(
                f"eve route {route} did not answer: {exc}",
                may_have_sent=True,
            ) from exc
        payload = _decoded_object(response, route)
        if response.status_code not in expect:
            if response.status_code == SESSION_NOT_ACTIVE_STATUS and (
                payload.get("code") == SESSION_NOT_ACTIVE_CODE
            ):
                raise HarnessControlError(
                    "eve refused the message because the durable session is unknown or terminal; "
                    "no replacement session is created"
                )
            raise HarnessControlError(
                f"eve route {route} answered {response.status_code}: {payload}"
            )
        return _JsonResponse(body=payload, headers=dict(response.headers))

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise HarnessControlError("eve runtime transport is not started")
        return self._client

    async def _drain_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        while chunk := await process.stderr.read(DEFAULT_READ_CHUNK_BYTES):
            self._stderr.extend(chunk)
            overflow = len(self._stderr) - 32 * 1024
            if overflow > 0:
                del self._stderr[:overflow]

    def _stderr_excerpt(self) -> str:
        return self.stderr_tail.strip() or "<no stderr>"


@dataclass(frozen=True)
class _JsonResponse:
    body: object
    headers: Mapping[str, str]


def _decoded_object(response: httpx.Response, route: str) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise HarnessControlError(f"eve route {route} did not answer with JSON") from exc
    if not isinstance(payload, Mapping):
        raise HarnessControlError(f"eve route {route} answered with a non-object JSON body")
    return payload


async def _terminate(process: asyncio.subprocess.Process, *, graceful: bool) -> None:
    """Stop only the process this adapter started, escalating once and then killing.

    ``graceful`` asks the child to exit on its own first; ``forced`` terminates immediately.
    Either way the escalation is bounded, and a process that never exits is killed rather than
    left behind.
    """

    if process.returncode is not None:
        return
    process.terminate()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5.0 if graceful else 2.0)
        return
    if process.returncode is None:
        process.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=5.0)
