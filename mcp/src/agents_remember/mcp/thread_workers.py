"""Run blocking MCP request work through completion, even under cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from anyio import CancelScope


async def run_in_request_thread[**P, R](
    function: Callable[P, R], /, *arguments: P.args, **keywords: P.kwargs
) -> R:
    """Carry the caller's context and drain its thread before propagating cancellation."""

    worker = asyncio.create_task(asyncio.to_thread(function, *arguments, **keywords))
    try:
        return await asyncio.shield(worker)
    except BaseException:
        # A thread cannot be cancelled. Keep the owning scopes and any write lock
        # alive until the thread exits, even under repeated transport cancellation.
        with CancelScope(shield=True):
            while not worker.done():
                try:
                    await asyncio.wait((worker,))
                except asyncio.CancelledError:
                    continue
        if not worker.cancelled():
            worker.exception()
        raise
