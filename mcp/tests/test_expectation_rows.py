"""Tests for R2 durable expectation rows (260707-HFX2-L1): store semantics + settings SLAs."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.expectation_rows import (
    ExpectationRowStore,
    create_expectation_row,
    due_at_from_sla,
    mark_met,
    mark_missed,
    write_expectation_row,
)
from agents_remember.kernel.agentic_settings import (
    DEFAULT_EXPECTATION_SLA_SECONDS,
    AgenticSettingsError,
    _parse_expectations,
)

T1 = "2026-06-23T10:00:00+00:00"


class ExpectationRowRecordTests(unittest.TestCase):
    def test_create_is_pending_with_dueAt_from_sla(self) -> None:
        now = datetime.fromisoformat(T1)
        row = create_expectation_row(
            row_id="R1",
            now=T1,
            kind="ack-by",
            due_at=due_at_from_sla(now=now, sla_seconds=300.0),
            source_id="entry-1",
        )
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.kind, "ack-by")
        self.assertEqual(row.dueAt, "2026-06-23T10:05:00+00:00")

    def test_mark_met_is_idempotent(self) -> None:
        row = create_expectation_row(
            row_id="R1", now=T1, kind="verdict-by", due_at=T1, source_id="gate-1"
        )
        met = mark_met(row, now="2026-06-23T10:05:00+00:00")
        self.assertEqual(met.state, "met")
        self.assertEqual(met.metAt, "2026-06-23T10:05:00+00:00")
        met_again = mark_met(met, now="2026-06-23T11:00:00+00:00")
        self.assertEqual(met_again.metAt, "2026-06-23T10:05:00+00:00")

    def test_mark_missed_is_idempotent_and_wont_overwrite_met(self) -> None:
        row = create_expectation_row(
            row_id="R1", now=T1, kind="turn-report-by", due_at=T1, source_id="session-1"
        )
        met = mark_met(row, now="2026-06-23T10:05:00+00:00")
        missed = mark_missed(met, now="2026-06-23T11:00:00+00:00")
        self.assertEqual(missed.state, "met")
        self.assertIsNone(missed.missedAt)


class ExpectationRowStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ExpectationRowStore(Path(tmp.name))

    def test_write_expectation_row_appends_a_pending_row(self) -> None:
        now = datetime.fromisoformat(T1)
        row = write_expectation_row(
            self.store,
            row_id="R1",
            now=now,
            kind="briefed-by",
            sla_seconds=120.0,
            source_id="session-1",
            subject_agent_id="worker-1",
        )
        self.assertEqual(row.state, "pending")
        self.assertEqual(self.store.pending(), [row])

    def test_pending_excludes_met_rows(self) -> None:
        write_expectation_row(
            self.store,
            row_id="R1",
            now=datetime.fromisoformat(T1),
            kind="ack-by",
            sla_seconds=300.0,
            source_id="entry-1",
        )
        self.store.mark_met("R1", now="2026-06-23T10:01:00+00:00")
        self.assertEqual(self.store.pending(), [])

    def test_overdue_returns_only_rows_past_dueAt(self) -> None:
        write_expectation_row(
            self.store,
            row_id="R1",
            now=datetime.fromisoformat(T1),
            kind="ack-by",
            sla_seconds=60.0,
            source_id="entry-1",
        )
        write_expectation_row(
            self.store,
            row_id="R2",
            now=datetime.fromisoformat(T1),
            kind="ack-by",
            sla_seconds=3600.0,
            source_id="entry-2",
        )
        later = datetime.fromisoformat("2026-06-23T10:02:00+00:00")
        overdue_ids = [row.id for row in self.store.overdue(now=later)]
        self.assertEqual(overdue_ids, ["R1"])

    def test_find_by_source_matches_kind_and_source(self) -> None:
        write_expectation_row(
            self.store,
            row_id="R1",
            now=datetime.fromisoformat(T1),
            kind="ack-by",
            sla_seconds=300.0,
            source_id="entry-1",
        )
        found = self.store.find_by_source("entry-1", kind="ack-by")
        assert found is not None
        self.assertEqual(found.id, "R1")
        self.assertIsNone(self.store.find_by_source("entry-1", kind="verdict-by"))
        self.assertIsNone(self.store.find_by_source("nope"))

    def test_mark_missed_via_store_is_reserved_for_the_ladder(self) -> None:
        write_expectation_row(
            self.store,
            row_id="R1",
            now=datetime.fromisoformat(T1),
            kind="verdict-by",
            sla_seconds=60.0,
            source_id="gate-1",
        )
        missed = self.store.mark_missed("R1", now="2026-06-23T10:05:00+00:00")
        self.assertEqual(missed.state, "missed")
        self.assertEqual(self.store.pending(), [])

    def test_mark_met_missing_row_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.mark_met("nope", now=T1)


class ExpectationSettingsParserTests(unittest.TestCase):
    def test_absent_block_returns_documented_defaults(self) -> None:
        settings = _parse_expectations(None, source="<none>")
        self.assertEqual(settings.sla_seconds, DEFAULT_EXPECTATION_SLA_SECONDS)
        self.assertEqual(settings.sla_for("ack-by"), 300.0)

    def test_override_replaces_only_the_named_kind(self) -> None:
        settings = _parse_expectations(
            {"defaults": {"ack-by": 60.0}}, source="<test>"
        )
        self.assertEqual(settings.sla_for("ack-by"), 60.0)
        self.assertEqual(settings.sla_for("briefed-by"), DEFAULT_EXPECTATION_SLA_SECONDS["briefed-by"])

    def test_unknown_kind_fails_loud(self) -> None:
        with self.assertRaises(AgenticSettingsError):
            _parse_expectations({"defaults": {"not-a-kind": 60.0}}, source="<test>")

    def test_non_positive_sla_fails_loud(self) -> None:
        with self.assertRaises(AgenticSettingsError):
            _parse_expectations({"defaults": {"ack-by": 0}}, source="<test>")

    def test_unknown_top_level_field_fails_loud(self) -> None:
        with self.assertRaises(AgenticSettingsError):
            _parse_expectations({"bogus": {}}, source="<test>")


if __name__ == "__main__":
    unittest.main()
