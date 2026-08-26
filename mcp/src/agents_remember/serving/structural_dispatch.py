"""Per-seat dispatch serialization and durable pinned-brief reconciliation."""

from __future__ import annotations

import fcntl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import StructuralDispatchError, StructuralDispatchLockError
from agents_remember.models.task_document_ref import TaskDocumentRef

_FAILED_BRIEF_STATES = frozenset({"superseded", "unresolved", "expired"})
_LOCK_STRIPE_COUNT = 4096
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[int, tuple[threading.Lock, int]] = {}


def _seat_lock_slot(
    document: TaskDocumentRef,
    role: str,
) -> int:
    identity = f"{document.repository}\0{document.path}\0{role}".encode()
    digest = sha256(identity).digest()
    return int.from_bytes(digest[:8], "big") % _LOCK_STRIPE_COUNT


def _seat_lock_path(coordination_root: Path, slot: int) -> Path:
    return coordination_root / "runtime" / "structural-seat-locks" / f"{slot:03x}.lock"


@contextmanager
def _exclusive_process_slot(slot: int) -> Iterator[None]:
    """Compose same-process threads while keeping the live lock map bounded by contention."""

    with _PROCESS_LOCKS_GUARD:
        current = _PROCESS_LOCKS.get(slot)
        lock, users = current if current is not None else (threading.Lock(), 0)
        _PROCESS_LOCKS[slot] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _PROCESS_LOCKS_GUARD:
            current_lock, users = _PROCESS_LOCKS[slot]
            if users == 1:
                del _PROCESS_LOCKS[slot]
            else:
                _PROCESS_LOCKS[slot] = (current_lock, users - 1)


@contextmanager
def exclusive_structural_dispatch_lock(
    coordination_root: Path,
    document: TaskDocumentRef,
    role: str,
) -> Iterator[None]:
    """Linearize the complete spawn-plus-pinned-brief transaction for one canonical seat.

    The lock is intentionally scoped to ``(task_document_ref, role)``: unrelated repositories,
    tasks, and roles remain concurrent. A process-local keyed lock composes threads; a POSIX whole-
    file lock on one of 4,096 hash stripes composes MCP processes and releases on process death. The
    live keyed map is reclaimed when contention ends, and the filesystem namespace has the same fixed
    upper bound, so historical seats cannot grow it without limit. A hash collision may conservatively
    serialize two unrelated seats; there is no process- or repository-global lock and no false-safety
    fallback.
    """

    slot = _seat_lock_slot(document, role)
    lock_path = _seat_lock_path(coordination_root, slot)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise StructuralDispatchLockError(f"structural seat lock is unavailable: {exc}") from exc
    with _exclusive_process_slot(slot), handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise StructuralDispatchLockError(
                f"structural seat lock could not be acquired: {exc}"
            ) from exc
        else:
            yield


def pinned_dispatch_brief(
    store: OperatorInboxStore,
    *,
    document: TaskDocumentRef,
    role: str,
    occupant_id: str,
) -> OperatorInboxEntry | None:
    """Return the one durable initial brief pinned to the current private occupant."""

    matches = [
        entry
        for entry in store.current().values()
        if entry.messageKind == "dispatch-brief"
        and entry.agentId == occupant_id
        and entry.taskDocumentRef == document
        and entry.recipientRole == role
    ]
    if len(matches) > 1:
        raise StructuralDispatchError(
            f"multiple pinned dispatch briefs exist for {document.key} as {role}"
        )
    return matches[0] if matches else None


def dispatch_brief_viable(entry: OperatorInboxEntry) -> bool:
    """Whether durable brief evidence still represents a live/retryable dispatch generation."""

    return entry.state not in _FAILED_BRIEF_STATES


def dispatch_brief_status(entry: OperatorInboxEntry) -> str:
    """Project the same structural status a first successful dispatch returned."""

    delivered = entry.deliveryState == "delivered" and entry.adapterDeliveryState in {
        "accepted",
        "queued",
        "completed",
    }
    return "dispatched" if delivered else "dispatch-queued"
