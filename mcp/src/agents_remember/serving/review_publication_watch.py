"""One connection's exact page and settled-environment invalidations across processes."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import threading
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi.sse import ServerSentEvent
from watchfiles._rust_notify import RustNotify

from agents_remember.canvas.explainer_paths import (
    LOCK_RESOURCE,
    binding_path,
    leaf_lock_resource,
    state_directory,
)
from agents_remember.canvas.explainer_state import (
    SEAT_ATTEMPTS_NAME,
    ExplainerBinding,
    binding_digest,
    generation,
    latest_build_failure,
    read_binding,
    read_ledger,
    seat_for_workspace,
)
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock

PublicationIdentity = tuple[str, str, str]


@dataclass(frozen=True)
class PublicationInputs:
    """Paths resolved by the same owners as state, never by a cached or client path."""

    root: Path
    pointer: Path
    workspace: Path
    generation_file: Path
    ledger: Path

    def validate_root(self) -> None:
        if self.root != self.root.resolve() or not self.root.is_dir():
            raise ValueError("explainer watch root must be a canonical directory")

    @property
    def paths(self) -> tuple[Path, ...]:
        return (
            self.pointer,
            self.ledger,
            binding_path(self.workspace),
            self.generation_file,
            state_directory(self.workspace) / SEAT_ATTEMPTS_NAME,
        )


PublicationPointerPort = Callable[[PublicationIdentity], PublicationInputs]


@dataclass(frozen=True)
class ChangeNotice:
    """What changed; a baseline never claims that a new page was published."""

    kind: Literal["baseline", "page", "environment"]
    page_stamp: str | None
    #: The identity of the environment facts the watch settled (binding, generation, build
    #: failure): with ``page_stamp`` it names the publication state, so a reconnect's baseline that
    #: repeats both changed nothing a page has not seen.
    environment_stamp: str | None = None


type WatchObservation = ChangeNotice | Exception


def _directories(root: Path, pointers: tuple[Path, ...]) -> tuple[str, ...]:
    """Only existing ancestors of the exact inputs; never recursive notes or worktrees."""
    directories: dict[str, None] = {}
    for pointer in pointers:
        pointer.relative_to(root)
        for depth in range(len(pointer.parent.relative_to(root).parts) + 1):
            directory = root.joinpath(*pointer.parent.relative_to(root).parts[:depth])
            if directory.is_symlink():
                raise ValueError("explainer watch cannot follow a symlink")
            if directory.is_dir():
                directories[str(directory)] = None
        if pointer.is_symlink():
            raise ValueError("explainer watch input cannot be a symlink")
    return tuple(directories)


def _change(watcher: RustNotify, pointers: tuple[Path, ...], stop: threading.Event) -> str:
    changes = watcher.watch(40, 10, 0, stop)
    if changes == "stop":
        return "stop"
    if isinstance(changes, str):
        raise RuntimeError(f"explainer watcher stopped: {changes}")
    touched = {Path(path) for _kind, path in changes}
    if any(touched.intersection(pointer.parents) for pointer in pointers):
        return "ancestors"
    if not touched.intersection(pointers):
        return "unrelated"
    if any(pointer.is_symlink() for pointer in pointers):
        raise ValueError("explainer watch input cannot be a symlink")
    return "input"


def _page(pointer: Path) -> str | None:
    try:
        return hashlib.sha256(pointer.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _environment_stamp(settled: object) -> str | None:
    return None if settled is None else hashlib.sha256(repr(settled).encode()).hexdigest()


def _settled(inputs: PublicationInputs, stop: threading.Event) -> tuple[object, ...] | None:
    """Read under the writer's lock; an initial binding before create is not completion.

    A workspace that is not on disk is absent -- never built, or removed (MIK-R211 v5 rule 12) --
    and is read without the lock, because the lock lives in its ``state/`` and taking it would
    make the folder again.
    """
    if not inputs.workspace.is_dir():
        return ("absent",)
    while not stop.is_set():
        try:
            with exclusive_file_lock(
                leaf_lock_resource(inputs.workspace), LOCK_RESOURCE, deadline=time.monotonic() + 0.2
            ):
                return _settled_locked(inputs)
        except LockAcquisitionTimeout:
            continue  # Wait for this writer, with cancellation checked between lock attempts.
    return None


def _settled_locked(inputs: PublicationInputs) -> tuple[object, ...] | None:
    ledger = read_ledger(inputs.ledger)
    if ledger.get("inFlightCreate") is not None:
        return None
    number = generation(inputs.generation_file)
    failure = latest_build_failure(inputs.ledger, number)
    failure_stamp = None if failure is None else repr(sorted(failure.items()))
    binding = read_binding(inputs.workspace) if inputs.workspace.is_dir() else None
    if failure is not None:
        # The writer records this after rollback. No created/current seat is implied.
        return "failed", failure_stamp, None if binding is None else binding_digest(binding)
    if not number and binding is None:
        return ("absent",)
    seat = seat_for_workspace(inputs.workspace, number)
    return _terminal_environment(number, seat, binding, ledger["requests"])


def _terminal_environment(
    number: int, seat: str, binding: ExplainerBinding | None, requests: dict[str, Any]
) -> tuple[object, ...] | None:
    rows = [
        row
        for row in requests.values()
        if isinstance(row, dict) and row.get("generation") == number and row.get("agentId") == seat
    ]
    if not rows:
        return None  # No legacy success assumption: a matching terminal writer receipt is required.
    if any(row.get("delivery") not in {"delivered", "held", "failed"} for row in rows):
        return None
    if binding is None and any(row.get("delivery") != "failed" for row in rows):
        return None
    receipts = tuple(
        sorted(
            (
                str(row.get("requestId", "")),
                str(row.get("delivery", "")),
                str(row.get("at", "")),
                str(row.get("error", "")),
            )
            for row in rows
        )
    )
    return (
        "environment",
        number,
        seat,
        receipts,
        None if binding is None else binding_digest(binding),
    )


def _relevant_change(watcher: RustNotify, pointers: tuple[Path, ...], stop: threading.Event) -> str:
    state = _change(watcher, pointers, stop)
    while state == "unrelated" and not stop.is_set():
        state = _change(watcher, pointers, stop)
    return "stop" if stop.is_set() else state


def _watch(
    inputs: PublicationInputs,
    stop: threading.Event,
    notify: Callable[[WatchObservation], None],
) -> None:
    watcher = None
    last: tuple[object, ...] | None = None
    try:
        inputs.validate_root()
        while not stop.is_set():
            paths = _directories(inputs.root, inputs.paths)
            replacement = RustNotify(list(paths), False, False, 300, False, False)
            if watcher is not None:
                watcher.close()
            watcher = replacement
            while not stop.is_set():
                # Read after handles are armed, including every new ancestor discovery.
                page = _page(inputs.pointer)
                settled = _settled(inputs, stop)
                if stop.is_set():
                    return
                # An unsettled environment does not replace the last coherent lifecycle fact.
                previous = None if last is None else last[1]
                state = page, settled if settled is not None else previous
                if state != last and (settled is not None or page is not None or last is not None):
                    kind = (
                        "baseline" if last is None else "page" if page != last[0] else "environment"
                    )
                    last = state
                    notify(ChangeNotice(kind, page, _environment_stamp(state[1])))
                change = _relevant_change(watcher, inputs.paths, stop)
                if change == "stop":
                    return
                if change == "ancestors":
                    break

    except Exception as error:
        notify(error)
    finally:
        if watcher is not None:
            watcher.close()


def valid_publication_identity(identity: PublicationIdentity) -> bool:
    return all(
        value and "/" not in value and "\\" not in value and not value.startswith(".")
        for value in identity
    )


async def publication_events(
    identity: PublicationIdentity, resolve: PublicationPointerPort
) -> AsyncGenerator[ServerSentEvent]:
    """One connection owns one native watcher; events request a fresh read, never assert success."""
    if not valid_publication_identity(identity):
        raise ValueError("publication selectors must be canonical path segments")
    admission = asyncio.create_task(asyncio.to_thread(resolve, identity))
    try:
        inputs = await asyncio.shield(admission)
    finally:
        await asyncio.shield(admission)
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[WatchObservation] = loop.create_future()
    queue: asyncio.Queue[WatchObservation] = asyncio.Queue(maxsize=1)
    stop = threading.Event()
    nonce = uuid4().hex

    def publish(observation: WatchObservation) -> None:
        if not ready.done():
            ready.set_result(observation)
        else:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(observation)

    def notify(observation: WatchObservation) -> None:
        loop.call_soon_threadsafe(publish, observation)

    worker = threading.Thread(
        target=_watch, args=(inputs, stop, notify), name=f"review-publication-{nonce}"
    )
    worker.start()
    try:
        counter = 0
        while True:
            observation = await ready if counter == 0 else await queue.get()
            if isinstance(observation, ChangeNotice):
                event = "review.explainer.changed"
                details = {
                    "kind": observation.kind,
                    "pageStamp": observation.page_stamp,
                    "environmentStamp": observation.environment_stamp,
                }
            else:
                event = "review.explainer.watch.error"
                details = {"detail": str(observation)}
            yield ServerSentEvent(
                event=event,
                data={
                    "repo": identity[0],
                    "master": identity[1],
                    "leaf": identity[2],
                    "revision": f"{nonce}:{counter}",
                    **details,
                },
            )
            if isinstance(observation, Exception):
                return
            counter += 1
    finally:
        stop.set()
        await asyncio.shield(asyncio.to_thread(worker.join))


async def safe_publication_events(
    identity: PublicationIdentity, resolve: PublicationPointerPort
) -> AsyncGenerator[ServerSentEvent]:
    """A rejected scope reports its own failure without ending the projection stream."""
    try:
        async with contextlib.aclosing(publication_events(identity, resolve)) as events:
            async for event in events:
                yield event
    except Exception as error:
        yield ServerSentEvent(
            event="review.explainer.watch.error",
            data={
                "repo": identity[0],
                "master": identity[1],
                "leaf": identity[2],
                "detail": str(error),
            },
        )
