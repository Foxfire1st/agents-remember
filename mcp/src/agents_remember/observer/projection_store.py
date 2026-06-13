"""Log/snapshot reading and atomic projection writes -- the reducer's I/O edge.

``read_lifecycle_logs`` enumerates the per-lifecycle truth; ``project_and_write``
ties reading, the pure reduction, and the write together (the entry the serving
layer drives on a tick, slice 04). Projections are written tmp-write +
``os.replace`` so a polling dashboard reader never sees a half-written
``latest-state.json`` -- stronger than the plain ``write_text`` of the
``setup_progress`` precedent, which a concurrent reader can tear.

The store root is resolved through :func:`agents_remember.observer.paths.observer_root`,
the single path abstraction shared with the write side.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.observer.events import Event
from agents_remember.observer.paths import observer_root
from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.observer.reducer import project_workspace
from agents_remember.observer.snapshots import read_enclosures, read_providers
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

LATEST_STATE = "latest-state.json"
LATEST_METRICS = "latest-metrics.json"


def read_lifecycle_logs(root: Path) -> list[list[Event]]:
    """Every per-lifecycle log under ``lifecycles/<id>/events.jsonl``, validated."""
    store = EventStore(root)
    lifecycles_dir = root / "lifecycles"
    if not lifecycles_dir.is_dir():
        return []
    logs: list[list[Event]] = []
    for entry in sorted(lifecycles_dir.iterdir()):
        if not entry.is_dir():
            continue
        events = store.read(entry.name)
        if events:
            logs.append(events)
    return logs


def write_projection(root: Path, projection: WorkspaceProjection) -> None:
    """Atomically write ``latest-state.json`` + ``latest-metrics.json``."""
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(root / LATEST_STATE, projection.model_dump(by_alias=True, exclude_none=True))
    _atomic_write_json(
        root / LATEST_METRICS,
        projection.metrics.model_dump(by_alias=True, exclude_none=True),
    )


def project_and_write(
    config: McpRuntimeConfig, *, now: datetime | None = None
) -> WorkspaceProjection:
    """Read logs + structural snapshots, reduce the tree, write it atomically."""
    moment = now or datetime.now(UTC)
    root = observer_root(config)
    projection = project_workspace(
        read_lifecycle_logs(root),
        enclosures=read_enclosures(config.coordination_root),
        providers=read_providers(config, now=moment),
        now=moment,
    )
    write_projection(root, projection)
    return projection


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{new_ulid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
