"""F07 safe virtual-cursor mapping through the actual workspace read path.

Original L114-CODE-R1-F07: a reader that snapshots a stale base offset while a
first writer creates the missing lock and compacts would map its virtual cursor
against the replaced file. ``_read_source_lines`` now establishes its
(base, file) pair by construction: (A) both read while holding the shared river
lock, which excludes every append and every cut for the hold, or (B) no
lockfile before the read and none after it, so no writer came into being. A
lockless read that finds a lockfile afterwards discards its window and repeats
the read once under the shared lock; where the lock cannot be held the read
returns no rows with the caller's cursor unchanged. These cases prove, through
``read_new_events`` (not helpers in isolation):

* an interposed first-writer cut replays no consumed row and loses no new row;
* both lock branches stream write-free (no observer file created by the read);
* an append between drains on the existing-lock branch delivers only new rows;
* a cut landing between the base snapshot and the file read, and one landing
  between the file read and the post-check, both surface with true offsets;
* a burst past the old ten-attempt bound surfaces coherently without any bound;
* a real concurrent cut during a protected read waits for the hold and the
  mapping stays coherent;
* an unholdable lock returns no rows with the cursor unchanged: open failure,
  lock failure, and disappearance each have their own case.
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import json
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_MAX_BYTES,
    compact_workspace_river,
)
from agents_remember.observer.events import Event
from agents_remember.observer.store import EventStore, workspace_base_offset, workspace_logical_size
from agents_remember.serving import events as serving

SEED_ID = "already-seen-seed"
NEW_ID = "new-after-cut"


def _seed_row() -> dict:
    return {
        "schema": "ar-observer-event/v1",
        "id": SEED_ID,
        "ts": datetime.datetime.now(datetime.UTC).isoformat(),
        "kind": "seat.example",
        "trust": "observed",
        "actor": "system",
        "data": {},
    }


def _seed_file(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(_seed_row()) + "\n").encode()
    path.write_bytes(line * (WORKSPACE_EVENT_MAX_BYTES // len(line)))
    return path.stat().st_size


def _workspace_file(root: Path) -> Path:
    return root / "workspace" / "events.jsonl"


def _inventory(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def test_missing_lock_interposed_cut_never_replays_nor_loses() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        before = _seed_file(path)
        assert not (path.parent / "events.lock").exists()
        fired = False

        def cut_after_base_read(r: Path) -> int:
            nonlocal fired
            value = workspace_base_offset(r)
            if not fired:
                fired = True
                EventStore(r).append(
                    Event(
                        id=NEW_ID,
                        ts=_seed_row()["ts"],
                        kind="seat.example",
                        trust="observed",
                        actor="system",
                        data={"trigger": "first writer at missing-lock reader interleave"},
                    )
                )
            return value

        with mock.patch.object(serving, "workspace_base_offset", side_effect=cut_after_base_read):
            first, offsets = serving.read_new_events(root, {"workspace": before})
        assert fired, "the interposed first-writer cut never ran"
        # The repaired read re-maps against the fresh base inside the same drain,
        # so the one unconsumed row arrives at once instead of replaying history.
        assert [event.payload["id"] for event in first] == [NEW_ID]
        assert offsets["workspace"] >= before, "cursor moved backwards"
        # The writer legitimately created its lock and base-offset sidecar; the
        # reader itself created nothing (proven separately by the no-write case).
        assert (path.parent / "events.lock").exists()
        assert (path.parent / "events.cursor.json").exists()
        second, new_offsets = serving.read_new_events(root, offsets)
        assert second == [], (
            f"second drain must be empty, saw {[e.payload['id'] for e in second]!r}"
        )
        assert new_offsets["workspace"] == offsets["workspace"]


def test_missing_lock_stream_writes_no_observer_file() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        before = _seed_file(path)
        assert _inventory(root) == {"workspace/events.jsonl"}
        events, offsets = serving.read_new_events(root, {"workspace": before})
        assert events == []
        assert _inventory(root) == {"workspace/events.jsonl"}, "the read created an observer file"
        assert not (path.parent / "events.lock").exists()
        assert offsets["workspace"] == before


def test_existing_lock_branch_no_write_and_between_append() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        before = _seed_file(path)
        (path.parent / "events.lock").write_bytes(b"")
        assert _inventory(root) == {"workspace/events.jsonl", "workspace/events.lock"}
        events, offsets = serving.read_new_events(root, {"workspace": before})
        assert events == []
        assert _inventory(root) == {"workspace/events.jsonl", "workspace/events.lock"}
        EventStore(root).append(
            Event(
                id=NEW_ID,
                ts=_seed_row()["ts"],
                kind="seat.example",
                trust="observed",
                actor="system",
                data={},
            )
        )
        second, new_offsets = serving.read_new_events(root, offsets)
        assert [event.payload["id"] for event in second] == [NEW_ID]
        assert new_offsets["workspace"] > offsets["workspace"]
        # Only the writer's base-offset sidecar may appear; the read adds nothing.
        assert _inventory(root) == {
            "workspace/events.jsonl",
            "workspace/events.lock",
            "workspace/events.cursor.json",
        }


def _timed_event(base_time: datetime.datetime, i: int) -> Event:
    return Event(
        id=f"row-{i:06d}",
        ts=(base_time + datetime.timedelta(seconds=i)).isoformat(),
        kind="seat.example",
        trust="observed",
        actor="system",
        data={},
    )


def _timed_rows(base_time: datetime.datetime, count: int) -> list[bytes]:
    rows = [
        (
            _timed_event(base_time, i).model_dump_json(by_alias=True, exclude_none=True) + "\n"
        ).encode()
        for i in range(1, count + 1)
    ]
    assert len({len(row) for row in rows}) == 1
    return rows


def _assert_true_offsets(events, *, row_bytes: int, first_index: int) -> None:
    """Every returned row carries its true virtual end offset (ruling 1a)."""
    for position, event in enumerate(events):
        expected = (first_index + position) * row_bytes
        assert serving.decode_cursor(event.cursor)["workspace"] == expected, (
            f"{event.payload['id']} carries a stale offset"
        )


def test_cut_between_base_snapshot_and_file_read() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        rows = _timed_rows(base_time, 6)
        row_bytes = len(rows[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(rows))
        virtual_start = row_bytes
        assert not (path.parent / "events.lock").exists()
        original = serving._read_lines_from
        calls = 0

        def cut_then_read(p: Path, start: int):
            nonlocal calls
            calls += 1
            if calls == 1:
                dropped = compact_workspace_river(
                    root, now=base_time + datetime.timedelta(seconds=2), ttl_seconds=0
                )
                assert dropped == 1
            return original(p, start)

        with mock.patch.object(serving, "_read_lines_from", side_effect=cut_then_read):
            first, cursor = serving.read_new_events(root, {"workspace": virtual_start})
        assert [e.payload["id"] for e in first] == [f"row-{i:06d}" for i in range(2, 7)]
        _assert_true_offsets(first, row_bytes=row_bytes, first_index=2)
        assert cursor["workspace"] == 6 * row_bytes == workspace_logical_size(root)
        EventStore(root).append(_timed_event(base_time, 7))
        second, cursor2 = serving.read_new_events(root, cursor)
        assert [e.payload["id"] for e in second] == ["row-000007"]
        EventStore(root).append(_timed_event(base_time, 8))
        third, _ = serving.read_new_events(root, cursor2)
        assert [e.payload["id"] for e in third] == ["row-000008"]
        seen = [e.payload["id"] for e in first + second + third]
        assert "row-000001" not in seen, "consumed row replayed"


def test_cut_between_file_read_and_post_check() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        rows = _timed_rows(base_time, 6)
        row_bytes = len(rows[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(rows))
        virtual_start = row_bytes
        assert not (path.parent / "events.lock").exists()
        original = serving._read_lines_from
        calls = 0

        def read_then_cut(p: Path, start: int):
            nonlocal calls
            calls += 1
            lines, end = original(p, start)
            if calls == 1:
                dropped = compact_workspace_river(
                    root, now=base_time + datetime.timedelta(seconds=2), ttl_seconds=0
                )
                assert dropped == 1
            return lines, end

        with mock.patch.object(serving, "_read_lines_from", side_effect=read_then_cut):
            first, cursor = serving.read_new_events(root, {"workspace": virtual_start})
        assert [e.payload["id"] for e in first] == [f"row-{i:06d}" for i in range(2, 7)]
        _assert_true_offsets(first, row_bytes=row_bytes, first_index=2)
        assert cursor["workspace"] == 6 * row_bytes == workspace_logical_size(root)
        EventStore(root).append(_timed_event(base_time, 7))
        second, _ = serving.read_new_events(root, cursor)
        assert [e.payload["id"] for e in second] == ["row-000007"]


def test_burst_past_old_bound_surfaces_coherently() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        rows = _timed_rows(base_time, 16)
        row_bytes = len(rows[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(rows))
        virtual_start = row_bytes
        assert not (path.parent / "events.lock").exists()
        original = serving._read_lines_from
        calls = 0

        def read_then_burst(p: Path, start: int):
            nonlocal calls
            calls += 1
            lines, end = original(p, start)
            if calls == 1:
                for cut in range(12):
                    dropped = compact_workspace_river(
                        root, now=base_time + datetime.timedelta(seconds=cut + 2), ttl_seconds=0
                    )
                    assert dropped == 1
            return lines, end

        with mock.patch.object(serving, "_read_lines_from", side_effect=read_then_burst):
            first, cursor = serving.read_new_events(root, {"workspace": virtual_start})
        assert [e.payload["id"] for e in first] == [f"row-{i:06d}" for i in range(13, 17)]
        _assert_true_offsets(first, row_bytes=row_bytes, first_index=13)
        assert cursor["workspace"] == 16 * row_bytes == workspace_logical_size(root)
        EventStore(root).append(_timed_event(base_time, 17))
        second, cursor2 = serving.read_new_events(root, cursor)
        assert [e.payload["id"] for e in second] == ["row-000017"]
        EventStore(root).append(_timed_event(base_time, 18))
        third, _ = serving.read_new_events(root, cursor2)
        assert [e.payload["id"] for e in third] == ["row-000018"]
        seen = [e.payload["id"] for e in first + second + third]
        assert "row-000001" not in seen, "consumed row replayed"


def test_concurrent_cut_during_protected_read_waits() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        rows = _timed_rows(base_time, 4)
        row_bytes = len(rows[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(rows))
        (path.parent / "events.lock").write_bytes(b"")
        virtual_start = row_bytes
        original = serving._read_lines_from
        entered = threading.Event()
        outcome: dict = {}

        def slow_read(p: Path, start: int):
            entered.set()
            time.sleep(0.3)
            return original(p, start)

        def writer() -> None:
            assert entered.wait(timeout=10), "protected read never started"
            outcome["dropped"] = compact_workspace_river(
                root, now=base_time + datetime.timedelta(seconds=1, milliseconds=500), ttl_seconds=0
            )

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with mock.patch.object(serving, "_read_lines_from", side_effect=slow_read):
                first, cursor = serving.read_new_events(root, {"workspace": virtual_start})
        finally:
            thread.join(timeout=15)
        assert not thread.is_alive(), "writer did not finish after the hold released"
        assert outcome.get("dropped") == 1, "concurrent cut never landed"
        assert [e.payload["id"] for e in first] == [f"row-{i:06d}" for i in range(2, 5)]
        _assert_true_offsets(first, row_bytes=row_bytes, first_index=2)
        assert workspace_base_offset(root) == row_bytes, "cut landed before the hold released"
        second, _ = serving.read_new_events(root, cursor)
        assert second == [], "post-cut drain replayed a consumed row"


def test_lockfile_open_failure_returns_empty_with_cursor() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        rows = _timed_rows(base_time, 6)
        row_bytes = len(rows[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(rows))
        (path.parent / "events.lock").write_bytes(b"")
        virtual_start = row_bytes
        real_open = Path.open

        def fault_open(self, mode="r", *args, **kwargs):
            if self.name == "events.lock" and mode == "rb":
                raise PermissionError(errno.EACCES, "lockfile rb denied")
            return real_open(self, mode, *args, **kwargs)

        with mock.patch.object(Path, "open", autospec=True) as mocked_open:
            mocked_open.side_effect = fault_open
            events, offsets = serving.read_new_events(root, {"workspace": virtual_start})
        assert events == [], "unacquired hold must return no rows"
        assert offsets["workspace"] == virtual_start, "cursor must stay unchanged"
        dropped = compact_workspace_river(
            root, now=base_time + datetime.timedelta(seconds=2), ttl_seconds=0
        )
        assert dropped == 1
        second, cursor = serving.read_new_events(root, {"workspace": virtual_start})
        assert [e.payload["id"] for e in second] == [f"row-{i:06d}" for i in range(2, 7)]
        _assert_true_offsets(second, row_bytes=row_bytes, first_index=2)
        assert cursor["workspace"] == 6 * row_bytes == workspace_logical_size(root)


def test_shared_lock_failure_returns_empty_with_cursor() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        rows = _timed_rows(base_time, 6)
        row_bytes = len(rows[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(rows))
        (path.parent / "events.lock").write_bytes(b"")
        virtual_start = row_bytes
        real_flock = fcntl.flock

        def fault_flock(fd, op, *args):
            if op == fcntl.LOCK_SH:
                raise InterruptedError(errno.EINTR, "shared lock interrupted")
            return real_flock(fd, op, *args)

        with mock.patch.object(fcntl, "flock") as mocked_flock:
            mocked_flock.side_effect = fault_flock
            events, offsets = serving.read_new_events(root, {"workspace": virtual_start})
        assert events == [], "unacquired hold must return no rows"
        assert offsets["workspace"] == virtual_start, "cursor must stay unchanged"
        dropped = compact_workspace_river(
            root, now=base_time + datetime.timedelta(seconds=2), ttl_seconds=0
        )
        assert dropped == 1
        second, cursor = serving.read_new_events(root, {"workspace": virtual_start})
        assert [e.payload["id"] for e in second] == [f"row-{i:06d}" for i in range(2, 7)]
        _assert_true_offsets(second, row_bytes=row_bytes, first_index=2)
        assert cursor["workspace"] == 6 * row_bytes == workspace_logical_size(root)


def test_vanished_lockfile_returns_empty_with_cursor() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        base_time = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(_timed_rows(base_time, 2)))
        (path.parent / "events.lock").write_bytes(b"")
        real_hold = serving.workspace_river_read

        @contextmanager
        def vanishing_hold(r: Path):
            (r / "workspace" / "events.lock").unlink()
            with real_hold(r):
                yield

        with mock.patch.object(serving, "workspace_river_read", side_effect=vanishing_hold):
            events, offsets = serving.read_new_events(root, {"workspace": 0})
        assert events == [], "unholdable read must return no rows"
        assert offsets["workspace"] == 0, "cursor must stay unchanged for the next read"


def test_lockless_clean_read_returns_first_observation() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f07-") as name:
        root = Path(name)
        path = _workspace_file(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"schema": "ar-observer-event/v1", "id": f"row-{i}", "kind": "seat.example"}
            for i in range(3)
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        size = path.stat().st_size
        assert not (path.parent / "events.lock").exists()
        events, offsets = serving.read_new_events(root, {"workspace": 0})
        assert [event.payload["id"] for event in events] == ["row-0", "row-1", "row-2"]
        assert offsets["workspace"] == size


if __name__ == "__main__":  # pragma: no cover - process-entry guard, never a runner
    raise SystemExit("test_f07_missing_lock_cursor is imported by pytest, not executed")
