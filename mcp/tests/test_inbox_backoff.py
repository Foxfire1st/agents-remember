"""Tests for the redelivery backoff math + per-target rate limiting (R3, 260707-HFX2-L1)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import unittest

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.kernel.primitives.inbox_backoff import (
    BACKOFF_SCHEDULE_SECONDS,
    DEFAULT_RATE_LIMIT_SECONDS,
    MIN_REDELIVERY_INTERVAL_SECONDS,
    backoff_seconds_for_attempt,
    is_due,
    is_rate_limited,
    next_attempt_at,
    redeliverable,
)

T1 = "2026-06-23T10:00:00+00:00"


def _entry(*, entry_id: str = "A", agent_id: str | None = "agent-a"):
    return create_operator_inbox_entry(
        InboxMessage(ask="Continue?", response="Yes."),
        entry_id=entry_id,
        now=T1,
        routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id=agent_id)),
        poster=InboxPoster(created_by="developer", created_via="dashboard"),
    )


class BackoffMathTests(unittest.TestCase):
    def test_backoff_ladder_climbs_then_clamps_at_ceiling(self) -> None:
        self.assertEqual(backoff_seconds_for_attempt(0), BACKOFF_SCHEDULE_SECONDS[0])
        self.assertEqual(backoff_seconds_for_attempt(1), BACKOFF_SCHEDULE_SECONDS[1])
        # Past the ladder's length, it clamps at the ceiling rather than indexing out of range.
        self.assertEqual(
            backoff_seconds_for_attempt(len(BACKOFF_SCHEDULE_SECONDS) + 50),
            BACKOFF_SCHEDULE_SECONDS[-1],
        )

    def test_next_attempt_at_respects_the_15_minute_floor(self) -> None:
        now = datetime.fromisoformat(T1)
        stamped = next_attempt_at(now=now, attempt_count=0)
        due = datetime.fromisoformat(stamped)
        self.assertAlmostEqual(
            (due - now).total_seconds(), MIN_REDELIVERY_INTERVAL_SECONDS, places=3
        )

    def test_next_attempt_at_rejects_a_sub_floor_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 900 seconds"):
            next_attempt_at(
                now=datetime.fromisoformat(T1),
                attempt_count=0,
                redelivery_floor_seconds=30.0,
            )


class DueAndRateLimitTests(unittest.TestCase):
    def test_a_fresh_pending_row_with_no_schedule_is_due(self) -> None:
        entry = _entry()
        self.assertTrue(is_due(entry, now=datetime.fromisoformat(T1)))

    def test_a_row_not_yet_at_its_next_attempt_is_not_due(self) -> None:
        entry = _entry().model_copy(update={"nextAttemptAt": "2026-06-23T10:10:00+00:00"})
        self.assertFalse(is_due(entry, now=datetime.fromisoformat(T1)))

    def test_a_row_past_its_next_attempt_is_due(self) -> None:
        entry = _entry().model_copy(update={"nextAttemptAt": "2026-06-23T09:00:00+00:00"})
        self.assertTrue(is_due(entry, now=datetime.fromisoformat(T1)))

    def test_consumed_rows_are_never_due(self) -> None:
        entry = _entry().model_copy(update={"state": "consumed"})
        self.assertFalse(is_due(entry, now=datetime.fromisoformat(T1)))

    def test_ladder_resolved_rows_are_never_due(self) -> None:
        entry = _entry().model_copy(update={"state": "ladder-resolved"})
        self.assertFalse(is_due(entry, now=datetime.fromisoformat(T1)))

    def test_a_recent_attempt_is_rate_limited(self) -> None:
        entry = _entry().model_copy(update={"lastAttemptAt": T1})
        now = datetime.fromisoformat("2026-06-23T10:00:10+00:00")
        self.assertTrue(
            is_rate_limited(entry, now=now, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS)
        )

    def test_an_attempt_outside_the_rate_limit_window_is_not_limited(self) -> None:
        entry = _entry().model_copy(update={"lastAttemptAt": T1})
        now = datetime.fromisoformat("2026-06-23T10:16:00+00:00")
        self.assertFalse(
            is_rate_limited(entry, now=now, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS)
        )

    def test_rate_limit_rejects_a_sub_floor_override(self) -> None:
        entry = _entry().model_copy(update={"lastAttemptAt": T1})
        with self.assertRaisesRegex(ValueError, "at least 900 seconds"):
            is_rate_limited(
                entry,
                now=datetime.fromisoformat("2026-06-23T10:00:10+00:00"),
                rate_limit_seconds=30.0,
            )

    def test_redeliverable_filters_due_and_unlimited_entries_only(self) -> None:
        due_and_clear = _entry(entry_id="A")
        rate_limited = _entry(entry_id="B").model_copy(update={"lastAttemptAt": T1})
        not_due = _entry(entry_id="C").model_copy(
            update={"nextAttemptAt": "2026-06-23T12:00:00+00:00"}
        )
        now = datetime.fromisoformat("2026-06-23T10:00:05+00:00")
        selected = redeliverable(
            [due_and_clear, rate_limited, not_due],
            now=now,
            rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS,
        )
        self.assertEqual([entry.id for entry in selected], ["A"])


if __name__ == "__main__":
    unittest.main()
