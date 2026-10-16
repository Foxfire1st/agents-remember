"""The bounded workspace Event River and its shared append/read lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.observer.events import Event

WORKSPACE_SOURCE = "workspace"
WORKSPACE_CURSOR_FILE = "events.cursor.json"
WORKSPACE_LOCK_FILE = "events.lock"


@contextmanager
def workspace_river_lock(root: Path, *, timeout: float | None = None) -> Iterator[None]:
    """Cross-process exclusive lock for workspace-river append and physical compaction.

    The workspace river has multiple process writers (serving + MCP tools), and live compaction
    physically rewrites the file. A POSIX lock on a sibling lockfile is therefore the one defensive
    mechanism this path needs: without it, a writer can append into the file while another process
    is replacing it, which is exactly the F3 tear risk.

    ``timeout`` bounds the wait: ``None`` waits as long as it takes (the historical
    behavior every existing caller keeps); a number of seconds raises
    ``TimeoutError`` -- an ``OSError``, so existing ``except OSError`` containment
    keeps working -- when the lock is still held then. The tool-event writer uses a
    timeout so a stuck lock is a counted dropped event, never a hung tool call.
    """
    path = root / WORKSPACE_SOURCE / WORKSPACE_LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if timeout is None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"workspace river lock still held after {timeout} seconds: {path}"
                        ) from None
                    time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def workspace_river_read(root: Path) -> Iterator[bool]:
    """Hold the river lock for reading without creating any observer file.

    MIK-R114 rule 7: a connected browser changes no file under
    ``logs/observer/``. The writer lock opens its lockfile with ``a+b`` (which
    creates it), so a reader must not take it: this helper opens the existing
    lockfile read-only under a shared lock instead. When no lockfile exists --
    an event file without one -- the read proceeds without the lock rather
    than creating anything; readers only consume complete newline-terminated
    rows, so a concurrent rewrite costs at most a delayed row, never a torn
    one, and the virtual cursor still maps through the base-offset sidecar.

    The yielded value says whether the shared lock is held: true only after
    ``flock(LOCK_SH)`` has returned on the opened lockfile; false where the
    lockfile could not be opened or the lock could not be taken, whatever the
    error. Callers return rows only on true; anything else is not a pair.
    """
    path = root / WORKSPACE_SOURCE / WORKSPACE_LOCK_FILE
    try:
        handle = path.open("rb")
    except OSError:
        yield False
        return
    with handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def workspace_base_offset(root: Path) -> int:
    """Virtual byte offset already reclaimed from ``workspace/events.jsonl``."""
    path = root / WORKSPACE_SOURCE / WORKSPACE_CURSOR_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    value = payload.get("baseOffset") if isinstance(payload, dict) else None
    return value if isinstance(value, int) and value >= 0 else 0


def set_workspace_base_offset(root: Path, base_offset: int) -> None:
    """Persist the workspace river's virtual base offset.

    Callers hold :func:`workspace_river_lock`; this helper only performs the atomic sidecar write.
    """
    path = root / WORKSPACE_SOURCE / WORKSPACE_CURSOR_FILE
    atomic_write_text(path, json.dumps({"baseOffset": base_offset}, separators=(",", ":")) + "\n")


def workspace_logical_size(root: Path) -> int:
    """Virtual EOF for the workspace river (base offset + current physical size)."""
    path = root / WORKSPACE_SOURCE / "events.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return workspace_base_offset(root) + size


class EventStore:
    """Append to and read the workspace Event River."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self) -> Path:
        return self._root / WORKSPACE_SOURCE / "events.jsonl"

    def append(self, event: Event, *, lock_timeout: float | None = None) -> None:
        """Admit an event under the shared size bound and lock."""
        from agents_remember.observer import event_retention  # noqa: PLC0415

        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        event_retention.admit_workspace_river_row(
            self._root,
            event.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"),
            timeout=lock_timeout,
        )

    def read(self) -> list[Event]:
        """Read valid rows; a torn or malformed row cannot stop the River."""
        path = self.log_path()
        if not path.exists():
            return []
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(Event.model_validate_json(line))
            except ValidationError:
                continue
        return events
