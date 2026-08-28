"""Owned module-level state that every supported pytest route restores."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from agents_remember.kernel.primitives import checkout_coordination


@dataclass(frozen=True)
class OwnedMutableState:
    name: str
    snapshot: Any
    restore: Any


@dataclass
class _PytestProcessState:
    snapshot: dict[str, Any] | None = None


def _declared_snapshot() -> dict[str, checkout_coordination.ExecutionMode]:
    return dict(checkout_coordination._declared)


def _declared_restore(snapshot: dict[str, checkout_coordination.ExecutionMode]) -> None:
    checkout_coordination._declared.clear()
    checkout_coordination._declared.update(snapshot)


OWNED_MUTABLE_STATES = (
    OwnedMutableState(
        name="agents_remember.kernel.primitives.checkout_coordination._declared",
        snapshot=_declared_snapshot,
        restore=_declared_restore,
    ),
)

_PYTEST_PROCESS_STATE = _PytestProcessState()


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


def begin_pytest_process() -> None:
    """Declare the process before production collection imports, once per session."""

    if _PYTEST_PROCESS_STATE.snapshot is None:
        _PYTEST_PROCESS_STATE.snapshot = snapshot_owned_mutable_state()
    checkout_coordination.declare_test_process()


def end_pytest_process() -> None:
    """Restore the execution-mode registry after every pytest exit path."""

    if _PYTEST_PROCESS_STATE.snapshot is not None:
        restore_owned_mutable_state(_PYTEST_PROCESS_STATE.snapshot)
        _PYTEST_PROCESS_STATE.snapshot = None


@contextmanager
def preserve_owned_mutable_state() -> Iterator[None]:
    """Contain a production entry point whose contract is to set process state."""

    previous = snapshot_owned_mutable_state()
    try:
        yield
    finally:
        restore_owned_mutable_state(previous)
