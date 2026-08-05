"""Owned module-level mutable state that every test must leave as it found it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from agents_remember.controlplane import durable_store


@dataclass(frozen=True)
class OwnedMutableState:
    """One deliberately-enumerated global and the operations needed to restore it."""

    name: str
    snapshot: Any
    restore: Any


def _declared_snapshot() -> dict[str, durable_store.ProcessRole]:
    return dict(durable_store._declared)


def _declared_restore(snapshot: dict[str, durable_store.ProcessRole]) -> None:
    durable_store._declared.clear()
    durable_store._declared.update(snapshot)


# This is an ownership register, not a scan. Add a row only after proving that a mutable global
# carries state from one test into another. The detector makes no claim about globals not listed.
OWNED_MUTABLE_STATES = (
    OwnedMutableState(
        name="agents_remember.controlplane.durable_store._declared",
        snapshot=_declared_snapshot,
        restore=_declared_restore,
    ),
)


def snapshot_owned_mutable_state() -> dict[str, Any]:
    return {state.name: state.snapshot() for state in OWNED_MUTABLE_STATES}


def restore_owned_mutable_state(previous: dict[str, Any]) -> list[str]:
    """Restore every owned global, returning the complete list that changed."""
    changed: list[str] = []
    for state in OWNED_MUTABLE_STATES:
        after = state.snapshot()
        state.restore(previous[state.name])
        if after != previous[state.name]:
            changed.append(f"{state.name}: before={previous[state.name]!r}, after={after!r}")
    return changed


@contextmanager
def preserve_owned_mutable_state() -> Iterator[None]:
    """Explicitly contain a production entry point whose contract is to set process state."""
    previous = snapshot_owned_mutable_state()
    try:
        yield
    finally:
        restore_owned_mutable_state(previous)
