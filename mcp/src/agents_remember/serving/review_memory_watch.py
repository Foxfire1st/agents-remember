"""Connection-owned native invalidations; no projection state or idle filesystem reads.

Nonrecursive native registrations explicitly exclude symlink directories and generated
subtrees. New directories reconcile on native events, never a timer; replacements are
installed before the old handles retire. Revisions are invalidations, not tree identities.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from uuid import uuid4

from fastapi.sse import ServerSentEvent
from watchfiles._rust_notify import RustNotify

ReviewMemoryRootPort = Callable[[str, str, str], Path | None]
ReviewIdentity = tuple[str, str, str]
_EventStream = AsyncGenerator[ServerSentEvent]
_IGNORED = frozenset({".git", "provider-runtime", "__pycache__", ".cache"})


def _admitted(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return not any(
        p in _IGNORED
        or p.endswith((".index.json", ".tmp", ".lock", "~", ".swp"))
        or p.startswith(".#")
        for p in parts
    )


def _directories(root: Path) -> tuple[str, ...]:
    directories = []
    for directory, children, _files in os.walk(root, followlinks=False):
        parent = Path(directory)
        children[:] = [
            name
            for name in children
            if _admitted(parent / name, root) and not (parent / name).is_symlink()
        ]
        directories.append(str(parent))
    return tuple(directories)


def _native_worker(
    root: Path, stop: threading.Event, notify: Callable[[Exception | None], None]
) -> None:
    watcher = None
    try:
        if root.is_symlink() or not root.is_dir() or root != root.resolve():
            raise ValueError(
                "review memory root must be an existing canonical non-symlink directory"
            )
        if "provider-runtime" in root.parts:
            raise ValueError("provider-runtime cannot be a review memory root")
        paths = _directories(root)
        watcher = RustNotify(list(paths), False, False, 300, False, False)
        notify(None)  # ready only AFTER native handles are armed
        while not stop.is_set():
            relevant = _native_changes(watcher, stop, root)
            if relevant is None:
                return
            if not relevant:
                continue
            if any(p.is_dir() or str(p) in paths for p in relevant):
                updated = _directories(root)
                if updated != paths:
                    replacement = RustNotify(list(updated), False, False, 300, False, False)
                    watcher.close()
                    watcher, paths = replacement, updated
            notify(None)
    except Exception as error:
        notify(error)
    finally:
        if watcher is not None:
            watcher.close()


def _native_changes(watcher: RustNotify, stop: threading.Event, root: Path) -> list[Path] | None:
    # Zero timeout means indefinitely blocked on native notifications, not interval polling.
    changes = watcher.watch(40, 10, 0, stop)
    if changes == "stop":
        return None
    if isinstance(changes, str):
        raise RuntimeError(f"native review memory watcher stopped: {changes}")
    return [Path(path) for _change, path in changes if _admitted(Path(path), root)]


async def _memory_events(root: Path, identity: ReviewIdentity) -> _EventStream:
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[Exception | None] = loop.create_future()
    queue: asyncio.Queue[Exception | None] = asyncio.Queue(maxsize=1)
    stop = threading.Event()
    nonce = uuid4().hex

    def publish(value: Exception | None) -> None:
        # Ready is not coalescible: an edit delivered while registration completes must
        # remain a SECOND event, even if the consumer has not read its baseline yet.
        if not ready.done():
            ready.set_result(value)
            return
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(value)

    def notify(value: Exception | None) -> None:
        loop.call_soon_threadsafe(publish, value)

    thread = threading.Thread(
        target=_native_worker, args=(root, stop, notify), name=f"review-memory-{nonce}"
    )
    thread.start()
    try:
        counter = 0
        while True:
            error = await ready if counter == 0 else await queue.get()
            if error is not None:
                raise error
            yield ServerSentEvent(
                event="review.memory",
                data={
                    "repo": identity[0],
                    "master": identity[1],
                    "leaf": identity[2],
                    "revision": f"{nonce}:{counter}",
                },
            )
            counter += 1
    finally:
        stop.set()
        # The worker owns every native handle. Do not abandon a blocking watch on cancel.
        await asyncio.shield(asyncio.to_thread(thread.join))


async def _next_event(source: _EventStream) -> ServerSentEvent:
    return await anext(source)


def _error_event(identity: ReviewIdentity, error: Exception) -> ServerSentEvent:
    return ServerSentEvent(
        event="review.memory.error",
        data={
            "repo": identity[0],
            "master": identity[1],
            "leaf": identity[2],
            "detail": str(error),
            "nextAction": "Reopen the live review from its canonical task context; check the dashboard native watch adapter.",
        },
    )


async def _memory_or_error(root: Path, identity: ReviewIdentity) -> _EventStream:
    try:
        async with contextlib.aclosing(_memory_events(root, identity)) as events:
            async for event in events:
                yield event
    except Exception as error:
        yield _error_event(identity, error)


async def _merge(ordinary: _EventStream, memory: _EventStream) -> _EventStream:
    streams = {asyncio.create_task(_next_event(source)): source for source in (ordinary, memory)}
    try:
        while streams:
            done, _ = await asyncio.wait(streams, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                source = streams.pop(task)
                try:
                    event = task.result()
                except StopAsyncIteration:
                    if source is memory:
                        continue
                    return
                yield event
                streams[asyncio.create_task(_next_event(source))] = source
    finally:
        for task in streams:
            task.cancel()
        await asyncio.gather(*streams, return_exceptions=True)


async def _resolve_root(port: ReviewMemoryRootPort, identity: ReviewIdentity) -> Path | None:
    admission = asyncio.create_task(asyncio.to_thread(port, *identity))
    try:
        return await asyncio.shield(admission)
    finally:
        # Cancellation must not leave a filesystem admission running after its connection.
        await asyncio.shield(admission)


async def multiplex_review_memory(
    ordinary: _EventStream,
    identity: ReviewIdentity | None,
    resolve_root: ReviewMemoryRootPort | None,
) -> _EventStream:
    """Keep the old stream byte-shape; opt in only for this open review connection."""
    async with contextlib.aclosing(ordinary):
        if identity is None:
            async for event in ordinary:
                yield event
            return
        try:
            for value in identity:
                if not value or "/" in value or "\\" in value or value.startswith("."):
                    raise ValueError(
                        "review selectors must each be a single canonical path segment"
                    )
            if resolve_root is None:
                raise ValueError("review memory root adapter is not wired into this process")
            root = await _resolve_root(resolve_root, identity)
            if root is None:
                raise ValueError("no admitted live leaf memory worktree for this review")
            async with (
                contextlib.aclosing(_memory_or_error(root, identity)) as memory,
                contextlib.aclosing(_merge(ordinary, memory)) as merged,
            ):
                async for event in merged:
                    yield event
        except (OSError, ValueError, RuntimeError) as error:
            yield _error_event(identity, error)
            async for event in ordinary:
                yield event
