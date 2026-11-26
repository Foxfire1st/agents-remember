"""The dashboard and plug-in share limit facts across attempts and writers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, local
from typing import cast

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.provider_limits import ProviderLimits, read_provider_limits
from agents_remember.serving.paseo import provider_limits as writer
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

NOW = datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
RESET = NOW + timedelta(days=3)
PERIOD = timedelta(minutes=5)
WORKER = "11111111-1111-5111-8111-111111111111"
MANAGER = "22222222-2222-5222-8222-222222222222"
NO_CONFIG = cast(McpRuntimeConfig, None)


@pytest.fixture
def limits_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "agents-remember"
    folder.mkdir()
    (folder / "role-agents.json").write_text(
        json.dumps(
            {
                "agents": {
                    agent_id: {
                        "role": role,
                        "task": "repo/master/leaf.json",
                        "reportPath": f"{agent_id}.md",
                    }
                    for agent_id, role in ((WORKER, "worker"), (MANAGER, "manager"))
                }
            }
        ),
        encoding="utf-8",
    )
    path = folder / "provider-limits.json"
    monkeypatch.setattr(writer, "provider_limits_path", lambda _config: path)
    return path


def _entry(report_id: str, *, used: int = 100, until: datetime = RESET) -> dict[str, object]:
    return {
        "id": report_id,
        "fetchedAt": NOW.isoformat(),
        "report": {
            "status": "available",
            "windows": [
                {"id": "weekly", "summary": True, "usedPct": used, "resetsAt": until.isoformat()}
            ],
        },
    }


def test_due_claim_survives_a_fresh_snapshot_and_keeps_the_fact(limits_path: Path) -> None:
    # A dashboard starting against an existing file sees only the stored observation at first.
    limits_path.write_text(
        json.dumps(
            {
                "schema": "ar-provider-limits/v1",
                "reports": {
                    "report-a": {"until": RESET.isoformat(), "observedAt": NOW.isoformat()}
                },
                "agents": {WORKER: ["report-a"]},
            }
        ),
        encoding="utf-8",
    )

    assert writer.claim_due_reports(NO_CONFIG, now=NOW + PERIOD - timedelta(seconds=1))[1] == ()
    assert writer.claim_due_reports(NO_CONFIG, now=NOW + PERIOD)[1] == ("report-a",)

    # No result was committed. A fresh reader inherits the attempt from the file.
    stored = read_provider_limits(limits_path)
    assert stored.reports["report-a"].lastReadAttemptAt == NOW + PERIOD
    assert stored.reports["report-a"].observedAt == NOW
    assert stored.reports["report-a"].until == RESET
    assert stored.agents == {WORKER: ("report-a",)}
    assert writer.claim_due_reports(NO_CONFIG, now=NOW + PERIOD + timedelta(seconds=1))[1] == ()

    # A stamped read answers from its old cache between attempts.
    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("report-a")]}, now=NOW + PERIOD + timedelta(seconds=2)
    )
    assert writer.claim_due_reports(NO_CONFIG, now=NOW + 2 * PERIOD)[1] == ("report-a",)
    assert writer.claim_due_reports(NO_CONFIG, now=RESET)[0].reports == {}


@pytest.mark.parametrize(
    ("departure_until", "checked_at", "expected_due", "remaining_reports"),
    [
        (RESET, NOW + PERIOD, ("report-a", "report-b"), {"report-a", "report-b"}),
        (NOW + timedelta(minutes=4), NOW + timedelta(minutes=4), (), {"report-b"}),
    ],
    ids=["due", "expiry"],
)
def test_quiet_replacement_prunes_departed_agent_mappings(
    limits_path: Path,
    departure_until: datetime,
    checked_at: datetime,
    expected_due: tuple[str, ...],
    remaining_reports: set[str],
) -> None:
    writer.record_usage_reads(
        NO_CONFIG,
        {WORKER: [_entry("report-a", until=departure_until)], MANAGER: [_entry("report-b")]},
        now=NOW,
    )
    role_agents = limits_path.with_name("role-agents.json")
    records = json.loads(role_agents.read_text(encoding="utf-8"))
    del records["agents"][WORKER]
    role_agents.write_text(json.dumps(records), encoding="utf-8")

    before = limits_path.read_bytes()
    assert writer.claim_due_reports(NO_CONFIG, now=NOW + timedelta(seconds=1))[1] == ()
    assert limits_path.read_bytes() == before

    assert writer.claim_due_reports(NO_CONFIG, now=checked_at)[1] == expected_due
    stored = read_provider_limits(limits_path)
    assert stored.agents == {MANAGER: ("report-b",)}
    assert stored.reports.keys() == remaining_reports
    assert stored.reports["report-b"].until == RESET
    assert stored.reports["report-b"].lastReadAttemptAt == (checked_at if expected_due else None)
    assert writer.claim_due_reports(NO_CONFIG, now=checked_at + timedelta(seconds=1))[1] == ()


def test_clear_marker_survives_an_unrelated_write_and_expires(limits_path: Path) -> None:
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry("report-a")]}, now=NOW)
    cleared_at = NOW + PERIOD
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry("report-a", used=0)]}, now=cleared_at)
    writer.record_usage_reads(
        NO_CONFIG, {MANAGER: [_entry("report-b")]}, now=cleared_at + timedelta(seconds=1)
    )

    stored = read_provider_limits(limits_path)
    marker = stored.clearedSeats[WORKER]
    assert marker.clearedAt == cleared_at
    assert marker.previousUntil == RESET
    assert stored.updatedAt != cleared_at
    assert stored.reports.keys() == {"report-b"}

    reclaim_at = RESET + timedelta(minutes=1)
    before_reclaim, _ = writer.claim_due_reports(NO_CONFIG, now=reclaim_at - timedelta(seconds=1))
    assert before_reclaim.clearedSeats
    assert writer.claim_due_reports(NO_CONFIG, now=reclaim_at)[0].clearedSeats == {}
    assert read_provider_limits(limits_path).clearedSeats == {}


def test_only_the_last_cleared_blocker_releases_a_registered_seat(limits_path: Path) -> None:
    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("report-a"), _entry("report-b")]}, now=NOW
    )
    writer.record_usage_reads(
        NO_CONFIG,
        {WORKER: [_entry("report-a", used=0), _entry("report-b")]},
        now=NOW + PERIOD,
    )
    assert read_provider_limits(limits_path).clearedSeats == {}

    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("report-b", used=0)]}, now=NOW + 2 * PERIOD
    )
    assert read_provider_limits(limits_path).clearedSeats[WORKER].clearedAt == NOW + 2 * PERIOD

    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry("report-b")]}, now=NOW + 3 * PERIOD)
    assert read_provider_limits(limits_path).clearedSeats == {}

    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("report-b", used=0)]}, now=NOW + 4 * PERIOD
    )
    assert read_provider_limits(limits_path).clearedSeats.keys() == {WORKER}
    role_agents = limits_path.with_name("role-agents.json")
    role_agents.write_text(json.dumps({"agents": {}}), encoding="utf-8")
    assert writer.claim_due_reports(NO_CONFIG, now=NOW + 4 * PERIOD)[0].clearedSeats == {}


def test_failed_refresh_keeps_the_fact_and_named_agent_mapping(limits_path: Path) -> None:
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry("report-a")]}, now=NOW)
    writer.claim_due_reports(NO_CONFIG, now=NOW + PERIOD)
    before = read_provider_limits(limits_path).reports["report-a"]

    writer.record_usage_reads(
        NO_CONFIG,
        {MANAGER: [_entry("report-a", used=0), _entry("report-b")]},
        failed_refreshes={MANAGER: {"report-a"}},
        now=NOW + PERIOD,
    )

    stored = read_provider_limits(limits_path)
    assert stored.reports["report-a"] == before
    assert stored.reports.keys() == {"report-a", "report-b"}
    assert stored.agents == {WORKER: ("report-a",), MANAGER: ("report-a", "report-b")}
    assert stored.clearedSeats == {}


def test_periodic_preservation_does_not_restore_obsolete_or_foreign_mappings(
    limits_path: Path,
) -> None:
    writer.record_usage_reads(
        NO_CONFIG,
        {WORKER: [_entry("old-account"), _entry("current-account")], MANAGER: [_entry("other")]},
        now=NOW,
    )
    # A newer stamped read drops the worker's old account before the periodic reply lands.
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry("current-account")]}, now=NOW + PERIOD)
    writer.record_usage_reads(
        NO_CONFIG,
        {WORKER: [_entry("current-account", used=0)]},
        preserved_reports={WORKER: {"old-account", "other"}},
        now=NOW + PERIOD,
    )

    stored = read_provider_limits(limits_path)
    assert stored.agents == {WORKER: ("current-account",), MANAGER: ("other",)}
    assert stored.reports.keys() == {"old-account", "other"}
    assert stored.clearedSeats.keys() == {WORKER}


def test_concurrent_usage_reads_keep_both_reports_and_mappings(
    limits_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_write_ready = Event()
    second_lock_ready = Event()
    let_first_write = Event()
    second_thread = local()
    original_lock = writer.exclusive_file_lock
    original_write = writer._write_limits

    @contextmanager
    def observed_lock(path: Path, resource: str) -> Iterator[None]:
        if getattr(second_thread, "writing", False):
            second_lock_ready.set()
        with original_lock(path, resource):
            yield

    def paused_write(path: Path, limits: ProviderLimits) -> None:
        if not getattr(second_thread, "writing", False):
            first_write_ready.set()
            assert let_first_write.wait(timeout=HANG_GUARD_SECONDS)
        original_write(path, limits)

    def write_second() -> ProviderLimits | None:
        second_thread.writing = True
        return writer.record_usage_reads(NO_CONFIG, {MANAGER: [_entry("report-b")]}, now=NOW)

    monkeypatch.setattr(writer, "exclusive_file_lock", observed_lock)
    monkeypatch.setattr(writer, "_write_limits", paused_write)
    with ThreadPoolExecutor(max_workers=2) as threads:
        first = threads.submit(
            writer.record_usage_reads, NO_CONFIG, {WORKER: [_entry("report-a")]}, now=NOW
        )
        try:
            assert first_write_ready.wait(timeout=HANG_GUARD_SECONDS)
            second = threads.submit(write_second)
            assert second_lock_ready.wait(timeout=HANG_GUARD_SECONDS)
        finally:
            let_first_write.set()
        assert first.result(timeout=HANG_GUARD_SECONDS) is not None
        assert second.result(timeout=HANG_GUARD_SECONDS) is not None

    stored = read_provider_limits(limits_path)
    assert stored.reports.keys() == {"report-a", "report-b"}
    assert stored.agents == {WORKER: ("report-a",), MANAGER: ("report-b",)}
