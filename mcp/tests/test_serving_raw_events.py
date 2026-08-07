from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.observer.event_retention import (
    initial_event_offsets,
    prune_expired_lifecycle_event_logs,
)
from agents_remember.serving.events import (
    decode_cursor,
    encode_cursor,
    read_new_events,
    stream_raw_events,
)
from test_serving import _config


class RawEventTests(unittest.TestCase):
    """The raw ``event`` channel's pure byte-offset tail + cursor resume (``serving.events``)."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)  # acts as the observer root

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _append(self, lifecycle: str, *lines: str) -> Path:
        path = self.root / "lifecycles" / lifecycle / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        return path

    def _event_line(self, ident: str, kind: str, ts: str, **data: object) -> str:
        return json.dumps(
            {
                "schema": "ar-observer-event/v1",
                "id": ident,
                "ts": ts,
                "kind": kind,
                "trust": "observed",
                "actor": "system",
                "lifecycleId": ident.split("-", maxsplit=1)[0],
                "data": data,
            }
        )

    def test_cursor_round_trip(self) -> None:
        offsets = {"L1": 42, "workspace": 7}
        self.assertEqual(decode_cursor(encode_cursor(offsets)), offsets)

    def test_decode_garbage_is_empty(self) -> None:
        self.assertEqual(decode_cursor(None), {})
        self.assertEqual(decode_cursor("not-base64-@@@"), {})

    def test_reads_new_lines_then_nothing(self) -> None:
        self._append("L1", '{"a":1}', '{"a":2}')
        events, offsets = read_new_events(self.root, {})
        self.assertEqual(
            [(e.source, e.data) for e in events], [("L1", '{"a":1}'), ("L1", '{"a":2}')]
        )
        again, offsets_again = read_new_events(self.root, offsets)
        self.assertEqual(again, [])
        self.assertEqual(offsets_again, offsets)

    def test_resume_from_cursor_skips_consumed(self) -> None:
        self._append("L1", '{"a":1}', '{"a":2}')
        events, _ = read_new_events(self.root, {})
        resumed, _ = read_new_events(self.root, decode_cursor(events[0].cursor))
        self.assertEqual([e.data for e in resumed], ['{"a":2}'])

    def test_mid_record_lifecycle_cursor_realigns_to_successor(self) -> None:
        path = self._append("L1", '{"id":"first"}', '{"id":"second"}')
        events, offsets = read_new_events(self.root, {"L1": 2})
        self.assertEqual([json.loads(event.data)["id"] for event in events], ["second"])
        self.assertEqual(offsets["L1"], path.stat().st_size)

    def test_mid_record_workspace_cursor_realigns_after_base_translation(self) -> None:
        workspace = self.root / "workspace" / "events.jsonl"
        workspace.parent.mkdir(parents=True)
        workspace.write_bytes(b'{"id":"first"}\n{"id":"second"}\n')
        base = 700
        (workspace.parent / "events.cursor.json").write_text(
            json.dumps({"baseOffset": base}) + "\n", encoding="utf-8"
        )
        events, offsets = read_new_events(self.root, {"workspace": base + 2})
        self.assertEqual([json.loads(event.data)["id"] for event in events], ["second"])
        self.assertEqual(offsets["workspace"], base + workspace.stat().st_size)

    def test_malformed_json_and_invalid_utf8_advance_without_retry(self) -> None:
        path = self.root / "lifecycles" / "L1" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"id":"one"}\nnot-json\n\xff\xfe\n{"id":"two"}\n')
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([json.loads(event.data)["id"] for event in events], ["one", "two"])
        self.assertEqual(offsets["L1"], path.stat().st_size)
        again, same_offsets = read_new_events(self.root, offsets)
        self.assertEqual(again, [])
        self.assertEqual(same_offsets, offsets)

    def test_valid_non_object_json_advances_without_emission(self) -> None:
        path = self._append(
            "L1",
            '{"id":"one"}',
            "null",
            "[]",
            "42",
            "true",
            '"scalar"',
            '{"id":"two"}',
        )
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([event.payload["id"] for event in events], ["one", "two"])
        self.assertEqual(offsets["L1"], path.stat().st_size)
        again, same_offsets = read_new_events(self.root, offsets)
        self.assertEqual(again, [])
        self.assertEqual(same_offsets, offsets)

    def test_cursor_beyond_eof_settles_at_current_eof(self) -> None:
        path = self._append("L1", '{"id":"one"}')
        events, offsets = read_new_events(self.root, {"L1": path.stat().st_size + 10_000})
        self.assertEqual(events, [])
        self.assertEqual(offsets["L1"], path.stat().st_size)
        self._append("L1", '{"id":"two"}')
        more, _ = read_new_events(self.root, offsets)
        self.assertEqual([json.loads(event.data)["id"] for event in more], ["two"])

    def test_partial_trailing_line_waits_for_newline(self) -> None:
        path = self.root / "lifecycles" / "L1" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"a":1}\n{"a":2}', encoding="utf-8")  # second line not terminated
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([e.data for e in events], ['{"a":1}'])
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        more, _ = read_new_events(self.root, offsets)
        self.assertEqual([e.data for e in more], ['{"a":2}'])

    def test_multi_source_ordering_with_workspace_last(self) -> None:
        self._append("L1", '{"a":1}')
        workspace = self.root / "workspace" / "events.jsonl"
        workspace.parent.mkdir(parents=True)
        workspace.write_text('{"w":1}\n', encoding="utf-8")
        events, _ = read_new_events(self.root, {})
        self.assertEqual(
            [(e.source, e.data) for e in events], [("L1", '{"a":1}'), ("workspace", '{"w":1}')]
        )

    def test_fresh_connection_offsets_skip_expired_terminal_lifecycles(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        old = self._append(
            "old",
            self._event_line("old-1", "lifecycle.started", "2026-06-14T09:00:00+00:00"),
            self._event_line(
                "old-2",
                "lifecycle.ended",
                "2026-06-14T09:30:00+00:00",
                outcome="completed",
            ),
        )
        self._append(
            "active", self._event_line("active-1", "lifecycle.started", "2026-06-14T11:00:00+00:00")
        )
        self._append(
            "recent",
            self._event_line("recent-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            self._event_line(
                "recent-2",
                "lifecycle.ended",
                "2026-06-14T11:30:00+00:00",
                outcome="completed",
            ),
        )

        offsets = initial_event_offsets(self.root, now=now)
        events, _ = read_new_events(self.root, offsets)

        self.assertEqual(offsets["old"], old.stat().st_size)
        self.assertEqual({event.source for event in events}, {"active", "recent"})

    def test_prunes_expired_terminal_lifecycle_event_logs(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        old = self._append(
            "old",
            self._event_line("old-1", "lifecycle.started", "2026-06-14T09:00:00+00:00"),
            self._event_line(
                "old-2",
                "lifecycle.ended",
                "2026-06-14T09:30:00+00:00",
                outcome="completed",
            ),
        )
        recent = self._append(
            "recent",
            self._event_line("recent-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            self._event_line(
                "recent-2",
                "lifecycle.ended",
                "2026-06-14T11:30:00+00:00",
                outcome="completed",
            ),
        )

        removed = prune_expired_lifecycle_event_logs(self.root, now=now)

        self.assertEqual(removed, [old])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_fresh_connection_offsets_workspace_events_by_age(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        workspace = self.root / "workspace" / "events.jsonl"
        workspace.parent.mkdir(parents=True)
        workspace.write_text(
            "\n".join(
                [
                    self._event_line("ws-1", "provider.status", "2026-06-14T09:00:00+00:00"),
                    self._event_line("ws-2", "provider.status", "2026-06-14T11:30:00+00:00"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        events, _ = read_new_events(self.root, initial_event_offsets(self.root, now=now))

        self.assertEqual([json.loads(event.data)["id"] for event in events], ["ws-2"])

    def test_fresh_connection_does_not_cap_parallel_active_lifecycle_history(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        noisy = tuple(
            self._event_line(
                f"noisy-{index}",
                "tool.completed",
                "2026-06-14T11:30:00+00:00",
                tool="noop",
                ok=True,
            )
            for index in range(500)
        )
        self._append(
            "noisy",
            self._event_line("noisy-start", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            *noisy,
        )
        self._append(
            "quiet",
            self._event_line("quiet-1", "lifecycle.started", "2026-06-14T11:05:00+00:00"),
        )

        events, _ = read_new_events(self.root, initial_event_offsets(self.root, now=now))
        ids = {json.loads(event.data)["id"] for event in events}

        self.assertIn("quiet-1", ids)
        self.assertEqual(len([event for event in events if event.source == "noisy"]), 501)

    def test_read_new_events_skips_heartbeats(self) -> None:
        self._append(
            "L1",
            self._event_line("L1-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            self._event_line(
                "L1-2", "lifecycle.heartbeat", "2026-06-14T11:00:16+00:00", state="running"
            ),
            self._event_line("L1-3", "tool.completed", "2026-06-14T11:00:30+00:00", tool="ping"),
        )
        events, offsets = read_new_events(self.root, {})
        self.assertEqual(
            [json.loads(e.data)["kind"] for e in events],
            ["lifecycle.started", "tool.completed"],
        )
        # The heartbeat is consumed (offset advanced past it), never re-read on resume.
        again, _ = read_new_events(self.root, offsets)
        self.assertEqual(again, [])

    def test_read_new_events_limit_bounds_batch(self) -> None:
        self._append(
            "L1",
            *(
                self._event_line(
                    f"L1-{index}", "tool.completed", "2026-06-14T11:00:00+00:00", n=index
                )
                for index in range(10)
            ),
        )
        first, offsets = read_new_events(self.root, {}, limit=3)
        self.assertEqual([json.loads(e.data)["data"]["n"] for e in first], [0, 1, 2])
        more, _ = read_new_events(self.root, offsets, limit=3)
        self.assertEqual([json.loads(e.data)["data"]["n"] for e in more], [3, 4, 5])

    def test_dormant_promoted_lifecycle_pruned_without_terminal_event(self) -> None:
        # The keystone: an enclosure-backed (promoted) lifecycle that went quiet with NO
        # lifecycle.ended -- and whose heartbeat kept ticking until recently -- is still
        # retired. Retention keys on *real* activity, so the recent heartbeat does not keep
        # the log alive; last real activity (10:30, 1.5h before now) is past the TTL.
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        dead = self._append(
            "dead",
            self._event_line(
                "dead-1", "lifecycle.started", "2026-06-14T09:00:00+00:00", fleeting=True
            ),
            self._event_line(
                "dead-2", "lifecycle.promoted", "2026-06-14T09:01:00+00:00", scope="r"
            ),
            self._event_line("dead-3", "tool.completed", "2026-06-14T10:30:00+00:00", tool="x"),
            self._event_line(
                "dead-hb", "lifecycle.heartbeat", "2026-06-14T11:50:00+00:00", state="running"
            ),
        )
        removed = prune_expired_lifecycle_event_logs(self.root, now=now)
        self.assertEqual(removed, [dead])
        self.assertFalse(dead.exists())

    def test_dormant_fleeting_lifecycle_pruned_without_terminal_event(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        fleet = self._append(
            "fleet",
            self._event_line(
                "fleet-1", "lifecycle.started", "2026-06-14T10:00:00+00:00", fleeting=True
            ),
            self._event_line(
                "fleet-hb", "lifecycle.heartbeat", "2026-06-14T11:55:00+00:00", state="running"
            ),
        )
        # Only real activity is the start at 10:00 (2h ago); the recent heartbeat is ignored.
        self.assertEqual(prune_expired_lifecycle_event_logs(self.root, now=now), [fleet])

    def test_protected_lifecycle_log_survives_inactivity(self) -> None:
        # A dormant, enclosure-backed log that belongs to a not-yet-retired master series is exempt:
        # the dashboard passes its id in `protected_lifecycle_ids`, so a running durable task keeps its
        # (and its siblings') history regardless of how long since its last lifecycle event.
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        kept = self._append(
            "keepme",
            self._event_line(
                "k-1", "lifecycle.started", "2026-06-14T09:00:00+00:00", fleeting=True
            ),
            self._event_line("k-2", "lifecycle.promoted", "2026-06-14T09:01:00+00:00", scope="r"),
            self._event_line("k-3", "tool.completed", "2026-06-14T10:00:00+00:00", tool="x"),
        )
        # Protected -> not pruned even though last real activity (10:00) is 2h ago, past the TTL.
        self.assertEqual(
            prune_expired_lifecycle_event_logs(
                self.root, now=now, protected_lifecycle_ids={"keepme"}
            ),
            [],
        )
        self.assertTrue(kept.exists())
        # Drop the protection and it IS pruned — proving the dormancy precondition held all along.
        self.assertEqual(prune_expired_lifecycle_event_logs(self.root, now=now), [kept])
        self.assertFalse(kept.exists())

    def test_active_lifecycle_with_recent_activity_not_pruned(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        alive = self._append(
            "alive",
            self._event_line(
                "alive-1", "lifecycle.started", "2026-06-14T09:00:00+00:00", fleeting=True
            ),
            self._event_line(
                "alive-2", "lifecycle.promoted", "2026-06-14T09:01:00+00:00", scope="r"
            ),
            self._event_line("alive-3", "tool.completed", "2026-06-14T11:50:00+00:00", tool="x"),
        )
        self.assertEqual(prune_expired_lifecycle_event_logs(self.root, now=now), [])
        self.assertTrue(alive.exists())

    def test_initial_offsets_bound_active_replay_to_recent_window(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        # Active (recent activity) but with history older than the 1h replay window.
        self._append(
            "long",
            self._event_line(
                "long-start", "lifecycle.started", "2026-06-14T08:00:00+00:00", fleeting=False
            ),
            self._event_line("long-old", "tool.completed", "2026-06-14T09:00:00+00:00", tool="x"),
            self._event_line(
                "long-recent", "tool.completed", "2026-06-14T11:50:00+00:00", tool="x"
            ),
        )
        offsets = initial_event_offsets(self.root, now=now)
        events, _ = read_new_events(self.root, offsets)
        self.assertEqual([json.loads(e.data)["id"] for e in events], ["long-recent"])


class StreamRawEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_streams_backlog(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text('{"a":1}\n', encoding="utf-8")
        gen = stream_raw_events(_config(self.tmp), interval=0.01)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "event")
        # The line is parsed to an object so ServerSentEvent single-encodes it (matching the
        # state channel); emitting the raw JSON string would double-encode the SSE wire.
        self.assertEqual(first.data, {"a": 1})
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        self.assertEqual(ready.data, {"ready": True})
        await gen.aclose()

    async def test_mid_record_cursor_streams_successor_then_ready(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text('{"id":"first"}\n{"id":"second"}\n', encoding="utf-8")
        gen = stream_raw_events(
            _config(self.tmp),
            interval=0.01,
            last_event_id=encode_cursor({"L1": 2}),
        )
        successor = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(successor.data, {"id": "second"})
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        await gen.aclose()

    async def test_stream_skips_non_object_json_then_emits_object_and_ready(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(
            'null\n[]\n42\ntrue\n"scalar"\n{"id":"valid"}\n',
            encoding="utf-8",
        )
        gen = stream_raw_events(
            _config(self.tmp),
            interval=0.01,
            last_event_id=encode_cursor({"L1": 0}),
        )
        event = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(event.event, "event")
        self.assertEqual(event.data, {"id": "valid"})
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        self.assertEqual(decode_cursor(ready.id)["L1"], log.stat().st_size)
        await gen.aclose()

    async def test_stream_does_not_emit_heartbeats(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        ts = datetime.now(UTC).isoformat()
        log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "hb",
                            "ts": ts,
                            "kind": "lifecycle.heartbeat",
                            "data": {"state": "running"},
                        }
                    ),
                    json.dumps(
                        {"id": "real", "ts": ts, "kind": "tool.completed", "data": {"tool": "ping"}}
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        gen = stream_raw_events(_config(self.tmp), interval=0.01)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "event")
        self.assertEqual(first.data["kind"], "tool.completed")
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        await gen.aclose()

    async def test_invalid_cursor_uses_retained_fresh_offsets(self) -> None:
        log = self.tmp / "logs" / "observer" / "workspace" / "events.jsonl"
        log.parent.mkdir(parents=True)
        recent = datetime.now(UTC).isoformat()
        log.write_text(
            "\n".join(
                [
                    json.dumps({"id": "old", "ts": "2000-01-01T00:00:00+00:00"}),
                    json.dumps({"id": "recent", "ts": recent}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        gen = stream_raw_events(_config(self.tmp), interval=0.01, last_event_id="not-base64")
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.data["id"], "recent")
        await gen.aclose()
