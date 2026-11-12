"""A review connection's native memory invalidations; no projection state or idle reads.

The connection subscribes to the process's one worktree watch
(:func:`~.leaf_worktree_watch.process_worktree_watch`) for its leaf's memory root while it is open
(D569): the watch registers the root's directories non-recursively, never symlinked or generated
ones, and reconciles new directories on native events, never a timer. The connection owns no
native handle of its own. Revisions are invalidations, not tree identities.

The arming baseline also says how long ago the watched memory last changed (``changedAgoMs``):
the newest modification or inode-change stamp of every admitted directory and file, read AFTER the
native handles are armed. An edit before arming produced no notification, so a reader whose HTTP
answers were asked for before arming compares this age with its own open time and re-reads only
when memory changed since then. A duration, never a wall-clock instant, so no clock is shared.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi.sse import ServerSentEvent

from agents_remember.serving.leaf_worktree_watch import (
    IGNORED,
    LeafWorktreeWatch,
    admitted,
    process_worktree_watch,
)

ReviewMemoryRootPort = Callable[[str, str, str], Path | None]
ReviewIdentity = tuple[str, str, str]
_EventStream = AsyncGenerator[ServerSentEvent]
# One admission rule for every review watch: the process's leaf worktree watch owns it.
_IGNORED = IGNORED
_admitted = admitted


@dataclass(frozen=True)
class _Armed:
    """The ready signal: native handles are armed, and memory last changed at this stamp."""

    latest_change_ns: int


def _stamp(path: Path) -> int:
    try:
        status = path.lstat()
    except OSError:
        return 0
    # ctime also moves when a tool restores an old mtime; on Windows it is creation and harmless.
    return max(status.st_mtime_ns, status.st_ctime_ns)


def _latest_change_ns(root: Path) -> int:
    """The newest stamp of every admitted directory and file: deletes move their directory's."""

    latest = 0
    for directory, children, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        children[:] = [
            name
            for name in children
            if _admitted(parent / name, root) and not (parent / name).is_symlink()
        ]
        latest = max(latest, _stamp(parent))
        for name in files:
            if _admitted(parent / name, root):
                latest = max(latest, _stamp(parent / name))
    return latest


def _admitted_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir() or root != root.resolve():
        raise ValueError("review memory root must be an existing canonical non-symlink directory")
    if "provider-runtime" in root.parts:
        raise ValueError("provider-runtime cannot be a review memory root")
    return root


async def _memory_events(
    root: Path, identity: ReviewIdentity, watch: LeafWorktreeWatch[ReviewIdentity] | None = None
) -> _EventStream:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Exception | None] = asyncio.Queue(maxsize=1)
    nonce = uuid4().hex
    watch = watch if watch is not None else process_worktree_watch()

    def publish(value: Exception | None) -> None:
        # An edit delivered after arming and before the baseline is read stays a SECOND event.
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(value)

    def notify(value: Exception | None) -> None:
        loop.call_soon_threadsafe(publish, value)

    def armed() -> tuple[Callable[[], None], _Armed]:
        unsubscribe = watch.subscribe(identity, _admitted_root(root), notify)
        try:
            # The stamp is read after the watches are armed, so an edit is either in it or produces
            # a native notification of its own.
            return unsubscribe, _Armed(_latest_change_ns(root))
        except BaseException:
            unsubscribe()
            raise

    unsubscribe, baseline = await asyncio.to_thread(armed)
    try:
        counter = 0
        while True:
            signal: Exception | _Armed | None = baseline if counter == 0 else await queue.get()
            if isinstance(signal, Exception):
                raise signal
            data: dict[str, str | int] = {
                "repo": identity[0],
                "master": identity[1],
                "leaf": identity[2],
                "revision": f"{nonce}:{counter}",
            }
            if isinstance(signal, _Armed):
                data["changedAgoMs"] = max(
                    0, (time.time_ns() - signal.latest_change_ns) // 1_000_000
                )
            yield ServerSentEvent(event="review.memory", data=data)
            counter += 1
    finally:
        # The watch keeps the leaf's directories while another reason or stream holds them.
        await asyncio.shield(asyncio.to_thread(unsubscribe))


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


async def _memory_or_error(
    root: Path, identity: ReviewIdentity, watch: LeafWorktreeWatch[ReviewIdentity] | None
) -> _EventStream:
    try:
        async with contextlib.aclosing(_memory_events(root, identity, watch)) as events:
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
    watch: LeafWorktreeWatch[ReviewIdentity] | None = None,
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
                contextlib.aclosing(_memory_or_error(root, identity, watch)) as memory,
                contextlib.aclosing(_merge(ordinary, memory)) as merged,
            ):
                async for event in merged:
                    yield event
        except (OSError, ValueError, RuntimeError) as error:
            yield _error_event(identity, error)
            async for event in ordinary:
                yield event
