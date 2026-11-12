"""The log-folder budgets (MIK-R108 rule 3, D1-D4; E3).

Each log folder has a budget in bytes for everything in it:
``toolLog.maxBytes`` for the transcript root (256 MiB when absent) and
``toolLog.enclosureMaxBytes`` for the ``logs/`` of one enclosure (64 MiB
when absent). Before a write that would take a folder over its budget, the
server removes earlier days whole, oldest first; when only the current day
is left and the write still does not fit, the write is dropped and counted,
and the call still succeeds. A write does not list the folder: the size
comes from a listing at most once in 60 seconds plus the server's own
writes since. The removal by age takes whole days, at most 31 in one call.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import agents_remember.application.tool_answer_log as tool_log_module
from agents_remember.application.tool_answer_log import (
    TOOL_LOG_BASENAME_PREFIX,
    TOOL_LOG_DETAIL_MAX_BYTES,
    DetailRequest,
    LineRequest,
    ToolCaller,
    ToolCallRecord,
    ToolLogOutcome,
    _FolderBudgetState,
    append_call_line,
    log_file_for,
    prepare_call_detail,
    prepare_log_folder,
    prune_old_tool_logs,
    record_tool_call,
    reset_log_budgets,
)
from agents_remember.kernel.primitives.runtime_config import (
    TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
    TOOL_LOG_KEEP_DAYS_DEFAULT,
    TOOL_LOG_MAX_BYTES_DEFAULT,
)


def _folder_bytes(folder: Path) -> int:
    """Count every byte under one folder."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            with open(Path(root) / name, "rb") as handle:
                total += len(handle.read())
    return total


def _owned_names(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


def _lines(folder: Path, day: str) -> list[dict[str, Any]]:
    path = log_file_for(folder, day)
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@dataclass(frozen=True)
class _BudgetCall:
    """One budgeted log write in a test (rule 3)."""

    day: str
    now: float
    max_bytes: int = 10**9
    enclosure: bool = False
    enclosure_max_bytes: int = TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT
    whole: dict[str, Any] | None = None
    tool_name: str = "ping"
    result_bytes: int = 12
    result_digest: str | None = None


def _record(folder: Path, call: _BudgetCall) -> ToolLogOutcome:
    # NOTE: no per-call reset here: consecutive calls in one test share the
    # folder's budget state like consecutive calls share one server.
    dest_kwargs: dict[str, Any] = {
        "enclosure_dir": folder if call.enclosure else None,
        "coord_log_dir": None if call.enclosure else folder,
    }
    return record_tool_call(
        ToolCallRecord(
            tool_name=call.tool_name,
            operation=call.tool_name,
            arguments={},
            caller=ToolCaller(agent_id="a1", role="worker", task="t"),
            ok=True,
            code=None,
            duration_ms=1.0,
            answer_tokens=10,
            whole_result=call.whole if call.whole is not None else {"ok": True, "n": 1},
            result_bytes=call.result_bytes,
            result_digest=call.result_digest,
            guard_cut=False,
            needs_detail=True,
            keep_days=TOOL_LOG_KEEP_DAYS_DEFAULT,
            max_bytes=call.max_bytes,
            enclosure_max_bytes=call.enclosure_max_bytes,
            day=call.day,
            now=call.now,
            **dest_kwargs,  # type: ignore[arg-type]
        )
    )


class ToolLogBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_log_budgets()

    def test_small_load_stays_within_budget_oldest_first(self) -> None:
        # E3, first load: small lines and details across days stay within
        # the budget; earlier days go, oldest first; no marker is left.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_760_000_000.0
            day_size: int | None = None
            for index, day in enumerate(
                ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]
            ):
                outcome = _record(folder, _BudgetCall(day=day, now=now + index))
                self.assertIsNone(outcome.log_error)
                if day_size is None:
                    day_size = _folder_bytes(folder)
            assert day_size is not None and day_size > 0
            budget = int(day_size * 2.5)
            for index, day in enumerate(
                ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10"]
            ):
                outcome = _record(
                    folder, _BudgetCall(day=day, now=now + 100 + index, max_bytes=budget)
                )
                self.assertIsNone(outcome.log_error)
            self.assertLessEqual(_folder_bytes(folder), budget)
            names = _owned_names(folder)
            self.assertNotIn(f"{TOOL_LOG_BASENAME_PREFIX}2026-10-01.jsonl", names)
            self.assertNotIn(f"{TOOL_LOG_BASENAME_PREFIX}2026-10-01", names)
            self.assertIn(f"{TOOL_LOG_BASENAME_PREFIX}2026-10-10.jsonl", names)
            for name in names:
                self.assertTrue(
                    name.startswith(TOOL_LOG_BASENAME_PREFIX),
                    f"marker or foreign file left behind: {name}",
                )

    def test_large_detail_load_stays_within_budget(self) -> None:
        # E3, second load: larger detail files (tens of kilobytes) across
        # days stay within their budget as well.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_760_000_000.0
            whole = {"ok": True, "blob": "x" * 40_000}
            first = _record(folder, _BudgetCall(day="2026-10-01", now=now, whole=whole))
            self.assertIsNone(first.log_error)
            day_size = _folder_bytes(folder)
            self.assertGreater(day_size, 30_000)
            budget = int(day_size * 2.5)
            for index, day in enumerate(["2026-10-02", "2026-10-03", "2026-10-04"]):
                outcome = _record(
                    folder,
                    _BudgetCall(day=day, now=now + 10 + index, max_bytes=budget, whole=whole),
                )
                self.assertIsNone(outcome.log_error)
            self.assertLessEqual(_folder_bytes(folder), budget)
            names = _owned_names(folder)
            self.assertNotIn(f"{TOOL_LOG_BASENAME_PREFIX}2026-10-01.jsonl", names)
            self.assertIn(f"{TOOL_LOG_BASENAME_PREFIX}2026-10-04.jsonl", names)

    def test_write_not_fitting_last_day_is_dropped_and_counted(self) -> None:
        # Rule 3: when only the current day is left and the write still does
        # not fit, the write is dropped; the next written line counts it;
        # the call succeeds.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_760_000_000.0
            first = _record(folder, _BudgetCall(day="2026-10-08", now=now, max_bytes=10))
            self.assertIsNone(first.log)
            self.assertIsNotNone(first.log_error)
            self.assertIn("budget", first.log_error or "")
            self.assertFalse((folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl").exists())
            second = _record(folder, _BudgetCall(day="2026-10-08", now=now + 1, max_bytes=10))
            self.assertIsNotNone(second.log_error)
            third = _record(folder, _BudgetCall(day="2026-10-08", now=now + 2))
            self.assertIsNone(third.log_error)
            self.assertIsNotNone(third.log)
            lines = _lines(folder, "2026-10-08")
            self.assertEqual(len(lines), 1)
            # Two starved calls dropped their detail and their line each.
            self.assertEqual(lines[0]["dropped"], 4)

    def test_oversize_detail_not_written_line_holds_size_and_digest(self) -> None:
        # Rule 3: a result over the detail bound is not written; its line
        # holds its size and its digest.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_760_000_000.0
            whole = {"ok": True, "blob": "x" * (TOOL_LOG_DETAIL_MAX_BYTES + 1024)}
            whole_text = json.dumps(whole, sort_keys=True)
            digest = hashlib.sha256(whole_text.encode("utf-8")).hexdigest()
            outcome = _record(
                folder,
                _BudgetCall(
                    day="2026-10-08",
                    now=now,
                    whole=whole,
                    result_bytes=len(whole_text.encode("utf-8")),
                    result_digest=digest,
                ),
            )
            self.assertIsNone(outcome.log)
            self.assertIsNotNone(outcome.log_error)
            self.assertIn("detail bound", outcome.log_error or "")
            lines = _lines(folder, "2026-10-08")
            self.assertEqual(len(lines), 1)
            self.assertIsNone(lines[0]["detail"])
            self.assertGreater(lines[0]["resultBytes"], TOOL_LOG_DETAIL_MAX_BYTES)
            self.assertEqual(lines[0]["resultDigest"], digest)
            self.assertEqual(lines[0]["dropped"], 1)

    def test_sweep_takes_thirty_one_days_per_call(self) -> None:
        # Rule 3: the removal by age takes whole days, at most 31 in one
        # call; what is left goes at the next call.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base = date(2026, 1, 1)
            for index in range(40):
                day = (base + timedelta(days=index)).isoformat()
                (folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}.jsonl").write_text("{}\n")
                detail = folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}"
                detail.mkdir(exist_ok=True)
                (detail / "x.json").write_text("{}")
            first = prune_old_tool_logs(folder, 1, today="2026-10-08", now=1_760_000_000.0)
            self.assertEqual(first, 31)
            rest = prune_old_tool_logs(folder, 1, today="2026-10-08", now=1_760_000_001.0)
            self.assertEqual(rest, 9)
            self.assertEqual(_owned_names(folder), [])

    def test_write_does_not_list_the_folder(self) -> None:
        # Rule 3: a write does not list the folder. After a warm census,
        # further writes within 60 seconds list nothing.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in range(200):
                (folder / f"junk-{index:04d}.bin").write_text("x" * 100)
            now = 1_760_000_000.0
            first = _record(folder, _BudgetCall(day="2026-10-08", now=now))
            self.assertIsNone(first.log_error)
            real_iterdir = Path.iterdir
            real_walk = os.walk
            listings = {"iterdir": 0, "walk": 0}

            def counting_iterdir(self: Path) -> Any:
                listings["iterdir"] += 1
                return real_iterdir(self)

            def counting_walk(*args: Any, **kwargs: Any) -> Any:
                listings["walk"] += 1
                return real_walk(*args, **kwargs)

            with (
                mock.patch.object(Path, "iterdir", autospec=True, side_effect=counting_iterdir),
                mock.patch.object(os, "walk", side_effect=counting_walk),
            ):
                for offset in range(1, 4):
                    outcome = _record(folder, _BudgetCall(day="2026-10-08", now=now + offset))
                    self.assertIsNone(outcome.log_error)
            self.assertEqual(listings, {"iterdir": 0, "walk": 0})

    def test_enclosure_budget_is_the_only_removal_and_leaves_no_marker(self) -> None:
        # Rule 3: in an enclosure the budget is the only removal before
        # cleanup (no age sweep); a removed day is gone, with no marker.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_760_000_000.0
            stale = folder / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            stale.write_text("{}\n", encoding="utf-8")
            outcome = _record(folder, _BudgetCall(day="2026-10-08", now=now, enclosure=True))
            self.assertIsNone(outcome.log_error)
            self.assertTrue(stale.exists(), "enclosure must not sweep by age")
            day_size = _folder_bytes(folder) - stale.stat().st_size
            budget = int(day_size * 1.5)
            pressured = _record(
                folder,
                _BudgetCall(
                    day="2026-10-09",
                    now=now + 10,
                    enclosure=True,
                    enclosure_max_bytes=budget,
                ),
            )
            self.assertIsNone(pressured.log_error)
            names = _owned_names(folder)
            self.assertNotIn(f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl", names)
            for name in names:
                self.assertTrue(
                    name.startswith(TOOL_LOG_BASENAME_PREFIX),
                    f"marker left behind: {name}",
                )

    def test_two_servers_share_a_folder_within_the_stated_margin(self) -> None:
        # Rule 3: several servers write the same folder, so a budget can be
        # passed by what they write between two listings, and by no more.
        # The margin below is exactly that: the second server's bytes since
        # the first server's last listing.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_760_000_000.0
            budget = 3000
            first = _record(folder, _BudgetCall(day="2026-10-08", now=now, max_bytes=budget))
            self.assertIsNone(first.log_error)
            other = _FolderBudgetState()
            detail_log, detail_error, _digest = prepare_call_detail(
                DetailRequest(
                    log_dir=folder,
                    budget_bytes=budget,
                    tool_name="ping",
                    whole_result={"ok": True, "blob": "y" * 2200},
                ),
                day="2026-10-08",
                now=now,
                state=other,
            )
            self.assertIsNone(detail_error)
            self.assertIsNotNone(detail_log)
            line_error = append_call_line(
                LineRequest(log_dir=folder, budget_bytes=budget, line={"tool": "ping", "n": 2}),
                day="2026-10-08",
                now=now,
                state=other,
            )
            self.assertIsNone(line_error)
            foreign = sum(other.own_by_day.values())
            self.assertGreater(foreign, 0)
            second = _record(folder, _BudgetCall(day="2026-10-08", now=now, max_bytes=budget))
            self.assertIsNone(second.log_error)
            disk = _folder_bytes(folder)
            self.assertLessEqual(disk, budget + foreign)

    def test_prepare_helpers_are_owned_by_the_writing_server(self) -> None:
        # The tool server that writes a log owns both removals: no separate
        # process is involved; the folder helpers do it in the call.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            error = prepare_log_folder(folder, 14, True)
            self.assertIsNone(error)
            self.assertTrue(folder.is_dir())
            log, log_error, _digest = prepare_call_detail(
                DetailRequest(
                    log_dir=folder, budget_bytes=1024, tool_name="ping", whole_result={"ok": True}
                ),
                day="2026-10-08",
                now=1_760_000_000.0,
            )
            self.assertIsNone(log_error)
            self.assertIsNotNone(log)


class ToolLogBudgetConstantsTests(unittest.TestCase):
    def test_named_budgets(self) -> None:
        self.assertEqual(TOOL_LOG_DETAIL_MAX_BYTES, 8388608)
        self.assertEqual(TOOL_LOG_MAX_BYTES_DEFAULT, 268435456)
        self.assertEqual(TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT, 67108864)


class ToolLogCensusCoherenceTests(unittest.TestCase):
    """The census counts everything; only reclaimed bytes leave it (rule 3)."""

    def _stale_day(self, folder: Path, day: str, size: int) -> None:
        line = folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}.jsonl"
        line.write_text("x" * size + "\n", encoding="utf-8")
        detail = folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}"
        detail.mkdir(exist_ok=True)
        (detail / "whole.json").write_text("{}", encoding="utf-8")

    def test_non_owned_bytes_stay_in_the_census(self) -> None:
        # Two load sizes: small lines and a line with a large detail.
        for payload in ({"ok": True}, {"ok": True, "blob": "y" * 200000}):
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                reset_log_budgets()
                junk = folder / "foreign.bin"
                junk.write_bytes(b"j" * 4096)
                outcome = _record(folder, _BudgetCall(day="2026-10-08", now=1000.0, max_bytes=4096))
                self.assertIsNotNone(outcome.log_error, "write over foreign bytes must drop")
                self.assertLessEqual(_folder_bytes(folder), 4096)
                outcome = _record(
                    folder,
                    _BudgetCall(day="2026-10-08", now=1001.0, max_bytes=10**9, whole=payload),
                )
                self.assertIsNone(outcome.log_error)
                lines = _lines(folder, "2026-10-08")
                self.assertEqual(len(lines), 1)
                # The detail write and the line write each dropped once.
                self.assertEqual(lines[0]["dropped"], 2)

    def test_failed_whole_day_removal_keeps_its_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            reset_log_budgets()
            self._stale_day(folder, "2026-10-01", 3000)
            with (
                mock.patch.object(tool_log_module.shutil, "rmtree", side_effect=OSError("forced")),
                mock.patch.object(Path, "unlink", side_effect=OSError("forced")),
            ):
                outcome = _record(folder, _BudgetCall(day="2026-10-08", now=1000.0, max_bytes=3200))
            self.assertIsNotNone(outcome.log_error, "misfit must drop, not overshoot")
            self.assertTrue(
                (folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-01.jsonl").exists(),
                "failed removal must keep the day",
            )
            # The small detail fit and landed; the line did not, and the kept
            # day kept its bytes in the census instead of funding overshoot.
            self.assertLessEqual(_folder_bytes(folder), 3200)
            outcome = _record(folder, _BudgetCall(day="2026-10-08", now=1001.0, max_bytes=10**9))
            self.assertIsNone(outcome.log_error)
            lines = _lines(folder, "2026-10-08")
            self.assertEqual(lines[-1]["dropped"], 1)


class ToolLogEvictionInventoryTests(unittest.TestCase):
    """Eviction reuses one inventory; days are not re-listed per day (rule 3)."""

    def _pressure_folder(self, tmp: str, days: int) -> Path:
        folder = Path(tmp)
        reset_log_budgets()
        base = date(2026, 9, 1)
        for index in range(days):
            day = (base + timedelta(days=index)).isoformat()
            line = folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}.jsonl"
            line.write_text("x" * 400 + "\n", encoding="utf-8")
            detail = folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}"
            detail.mkdir(exist_ok=True)
            (detail / "whole.json").write_text("{}", encoding="utf-8")
        (folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        return folder

    def _counted_record(self, folder: Path) -> tuple[int, int, ToolLogOutcome]:
        listings = 0
        walks = 0
        real_iterdir = Path.iterdir
        real_walk = os.walk

        def counted_iterdir(self: Path) -> Any:
            nonlocal listings
            listings += 1
            return real_iterdir(self)

        def counted_walk(top: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal walks
            walks += 1
            return real_walk(top, *args, **kwargs)

        with (
            mock.patch.object(Path, "iterdir", counted_iterdir),
            mock.patch.object(os, "walk", counted_walk),
        ):
            outcome = _record(folder, _BudgetCall(day="2026-10-08", now=2000.0, max_bytes=2000))
        return listings, walks, outcome

    def test_pressure_eviction_bounds_its_listings(self) -> None:
        # Two pressure loads share the same listing bound: no per-day re-list.
        # Eviction removes oldest first only until the write fits, so the
        # days left behind are always the newest ones. Each finished day is
        # walked once by the census and never again on successful removal.
        for days in (10, 20):
            with tempfile.TemporaryDirectory() as tmp:
                folder = self._pressure_folder(tmp, days)
                listings, walks, outcome = self._counted_record(folder)
                self.assertIsNone(outcome.log_error)
                self.assertLessEqual(listings, 4, f"{days} days listed {listings}x")
                self.assertLessEqual(walks, days + 3, f"{days} days walked {walks}x")
                left = sorted(
                    path.name
                    for path in folder.iterdir()
                    if path.name.startswith(TOOL_LOG_BASENAME_PREFIX)
                    and "2026-09" in path.name
                    and path.name.endswith(".jsonl")
                )
                self.assertTrue(left, "pressure must leave the newest days, not nothing")
                newest = [
                    f"{TOOL_LOG_BASENAME_PREFIX}2026-09-{day:02d}.jsonl"
                    for day in range(1, days + 1)
                ]
                self.assertEqual(left, [name for name in newest if name in left])
                self.assertEqual(left, newest[len(newest) - len(left) :])
                self.assertTrue((folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl").is_file())


class ToolLogSweepContinuationTests(unittest.TestCase):
    """A capped age sweep continues on the next call, same day included."""

    def test_capped_sweep_continues_next_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            reset_log_budgets()
            base = date(2026, 1, 1)
            for index in range(40):
                day = (base + timedelta(days=index)).isoformat()
                (folder / f"{TOOL_LOG_BASENAME_PREFIX}{day}.jsonl").write_text(
                    "{}\n", encoding="utf-8"
                )
            first = prepare_log_folder(folder, 0, is_enclosure=False, day="2026-10-08")
            self.assertIsNone(first)
            left_after_first = [
                path.name
                for path in folder.iterdir()
                if path.name.startswith(TOOL_LOG_BASENAME_PREFIX)
            ]
            self.assertEqual(len(left_after_first), 9)
            # A line lands for today; the continuation must still run.
            (folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )
            second = prepare_log_folder(folder, 0, is_enclosure=False, day="2026-10-08")
            self.assertIsNone(second)
            left_after_second = [
                path.name
                for path in folder.iterdir()
                if path.name.startswith(TOOL_LOG_BASENAME_PREFIX)
            ]
            self.assertEqual(left_after_second, [f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl"])


if __name__ == "__main__":
    unittest.main()
