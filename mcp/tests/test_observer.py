"""Tests for the observer write-side substrate (slice 2a).

Covers ``observer/ulid.py`` (minting), ``observer/events.py`` (the
``ar-observer-event/v1`` envelope), and ``observer/store.py`` (the append-only
store) in isolation: id sortability/uniqueness, envelope round-trip and
validation, per-lifecycle vs workspace routing, and that a self-contained
``lifecycle.started`` replays from its log alone.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer import (
    OBSERVER_EVENT_SCHEMA,
    Event,
    EventStore,
    new_ulid,
    now_iso,
)
from agents_remember.observer.ulid import _CROCKFORD, _ULID_LEN
from pydantic import ValidationError


class UlidTests(unittest.TestCase):
    def test_length_and_alphabet(self) -> None:
        ulid = new_ulid()
        self.assertEqual(len(ulid), _ULID_LEN)
        self.assertTrue(all(char in _CROCKFORD for char in ulid))

    def test_lexicographically_time_sortable(self) -> None:
        earlier = new_ulid(now_ms=1_000)
        later = new_ulid(now_ms=2_000)
        self.assertLess(earlier, later)

    def test_unique_within_one_millisecond(self) -> None:
        ids = {new_ulid(now_ms=42) for _ in range(2_000)}
        self.assertEqual(len(ids), 2_000)


class EventEnvelopeTests(unittest.TestCase):
    def _event(self, **overrides: Any) -> Event:
        base: dict[str, Any] = {
            "id": new_ulid(),
            "ts": now_iso(),
            "kind": "tool.completed",
            "trust": "observed",
            "actor": "model",
        }
        base.update(overrides)
        return Event(**base)

    def test_wire_keys_are_camelcase(self) -> None:
        event = self._event(lifecycleId="L1", repoId="agents-remember")
        wire = json.loads(event.model_dump_json(by_alias=True, exclude_none=True))
        self.assertEqual(wire["schema"], OBSERVER_EVENT_SCHEMA)
        self.assertEqual(wire["lifecycleId"], "L1")
        self.assertEqual(wire["repoId"], "agents-remember")
        self.assertNotIn("lifecycle_id", wire)

    def test_round_trip(self) -> None:
        event = self._event(lifecycleId="L1", data={"tool": "ping", "ok": True})
        restored = Event.model_validate_json(
            event.model_dump_json(by_alias=True, exclude_none=True)
        )
        self.assertEqual(restored, event)

    def test_optional_fields_omitted_when_none(self) -> None:
        wire = json.loads(self._event().model_dump_json(by_alias=True, exclude_none=True))
        self.assertNotIn("lifecycleId", wire)
        self.assertNotIn("spanId", wire)

    def test_trust_and_actor_are_validated(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(trust="totally-true")
        with self.assertRaises(ValidationError):
            self._event(actor="robot")

    def test_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(surprise="nope")


class EventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.store = EventStore(self.root)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _event(
        self, lifecycle_id: str | None, kind: str = "tool.completed", **data: Any
    ) -> Event:
        return Event(
            id=new_ulid(),
            ts=now_iso(),
            kind=kind,
            trust="observed",
            actor="model",
            lifecycleId=lifecycle_id,
            data=data,
        )

    def test_append_routes_per_lifecycle(self) -> None:
        self.store.append(self._event("L1"))
        self.assertTrue((self.root / "lifecycles" / "L1" / "events.jsonl").exists())

    def test_workspace_log_for_lifecycleless_events(self) -> None:
        self.store.append(self._event(None, kind="span.heartbeat"))
        self.assertTrue((self.root / "workspace" / "events.jsonl").exists())

    def test_round_trip_through_store(self) -> None:
        started = Event(
            id=new_ulid(),
            ts=now_iso(),
            kind="lifecycle.started",
            trust="declared",
            actor="model",
            lifecycleId="L1",
            data={"phase": "request", "fleeting": True},
        )
        self.store.append(started)
        self.store.append(self._event("L1"))
        events = self.store.read("L1")
        self.assertEqual(len(events), 2)
        # The self-contained lifecycle.started replays from its log alone.
        self.assertEqual(events[0], started)

    def test_read_absent_log_is_empty(self) -> None:
        self.assertEqual(self.store.read("nope"), [])


if __name__ == "__main__":
    unittest.main()
