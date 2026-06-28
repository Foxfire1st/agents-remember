"""The local dashboard serving layer (slice 04).

A FastAPI app over the observer projection read side: a single shared projector
ticks ``project_and_write`` and fans the snapshot + per-entity deltas out to every
connected client over one multiplexed SSE stream. Transport only -- it adds no
interpretation (the reducer owns that) and reads exclusively through
``McpRuntimeConfig`` + ``observer.paths`` (North-Star #5), never raw host paths.

Submodules are deliberately importable without pulling FastAPI: ``delta`` (pure diff)
and ``projector`` (the tick/fan-out) have no web dependency; only ``app`` and
``static`` import FastAPI.
"""

from __future__ import annotations
