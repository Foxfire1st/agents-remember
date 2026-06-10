from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers.setup_progress import (
    PROGRESS_SCHEMA,
    STALE_AFTER_SECONDS,
    SetupProgress,
    SetupProgressFile,
    progress_status,
    read_setup_progress,
)


class ManualClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def progress_file(root: Path, clock: ManualClock) -> SetupProgressFile:
    # A huge heartbeat interval keeps the ticker thread quiet so tests only
    # observe explicit event writes.
    return SetupProgressFile(
        root / "provider-runtime" / "setup-progress.json",
        identity={"repoName": "repo-a", "taskName": "task-1", "worktreeGroup": "group-a"},
        clock=clock,
        heartbeat_seconds=3600,
    )


class SetupProgressFileTests(unittest.TestCase):
    def test_event_lifecycle_writes_durable_phase_records(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            initial = read_setup_progress(progress.path)
            assert initial is not None
            self.assertEqual(initial["schema"], PROGRESS_SCHEMA)
            self.assertEqual(initial["state"], "running")
            self.assertEqual(initial["repoName"], "repo-a")
            self.assertIsNone(initial["currentPhase"])

            progress.phase_start("grepai", "clone-db", note="database clone")
            clock.advance(5)
            progress.phase_done({"ok": True, "provider": "grepai", "action": "clone-db"})

            progress.phase_start("codegraphcontext", "seed")
            clock.advance(3)
            progress.phase_done(
                {"ok": False, "skipped": True, "reason": "HEAD commits differ"}
            )

            data = read_setup_progress(progress.path)
            assert data is not None
            phases = data["completedPhases"]
            self.assertEqual(len(phases), 2)
            self.assertEqual(phases[0]["provider"], "grepai")
            self.assertTrue(phases[0]["ok"])
            self.assertEqual(phases[0]["note"], "database clone")
            self.assertFalse(phases[1]["ok"])
            self.assertTrue(phases[1]["skipped"])
            self.assertEqual(phases[1]["reason"], "HEAD commits differ")
            self.assertIsNone(data["currentPhase"])
            progress.finish(state="ok")

    def test_seed_fallback_recorded_when_fallback_phase_starts(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_start(
                "codegraphcontext",
                "refresh-all",
                note="full reindex fallback",
                seed_fallback={"active": True, "reason": "HEAD commits differ"},
            )
            data = read_setup_progress(progress.path)
            assert data is not None
            self.assertEqual(
                data["seedFallback"], {"active": True, "reason": "HEAD commits differ"}
            )
            current = data["currentPhase"]
            self.assertEqual(current["action"], "refresh-all")
            progress.finish(state="ok")

    def test_phase_update_attaches_metrics_to_current_phase(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_update({"itemsDone": 1})  # no current phase: ignored
            progress.phase_start("codegraphcontext", "refresh-all")
            progress.phase_update(
                {"itemsDone": 412, "itemsTotal": 1380, "percent": 29.9, "unit": "files"}
            )
            data = read_setup_progress(progress.path)
            assert data is not None
            self.assertEqual(data["currentPhase"]["metrics"]["itemsDone"], 412)
            progress.finish(state="ok")

    def test_finish_records_terminal_state_error_and_summary(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_start("watchers", "start")
            progress.finish(
                state="failed",
                error="boom",
                summary={"resultCounts": {"total": 3, "ok": 2, "failed": 1}},
            )
            data = read_setup_progress(progress.path)
            assert data is not None
            self.assertEqual(data["state"], "failed")
            self.assertEqual(data["error"], "boom")
            self.assertIsNone(data["currentPhase"])
            self.assertIn("finishedAt", data)
            self.assertEqual(data["summary"]["resultCounts"]["failed"], 1)

    def test_write_errors_never_raise_into_the_setup_chain(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            # A directory squatting on the file path forces write_text to fail.
            progress.path.unlink()
            progress.path.mkdir()
            progress.phase_start("grepai", "install")  # must not raise
            progress.finish(state="ok")  # must not raise

    def test_noop_sink_accepts_all_events(self) -> None:
        sink = SetupProgress()
        sink.phase_start("grepai", "install", note="x", seed_fallback={"active": True})
        sink.phase_update({"itemsDone": 1})
        sink.phase_done({"ok": True})


class ReadAndProjectionTests(unittest.TestCase):
    def test_read_rejects_missing_invalid_and_foreign_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(read_setup_progress(root / "missing.json"))
            bad = root / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertIsNone(read_setup_progress(bad))
            foreign = root / "foreign.json"
            foreign.write_text(json.dumps({"schema": "other/v9"}), encoding="utf-8")
            self.assertIsNone(read_setup_progress(foreign))

    def test_running_projection_reports_phase_elapsed_and_heartbeat(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_start("grepai", "clone-db")
            progress.phase_done({"ok": True, "provider": "grepai", "action": "clone-db"})
            progress.phase_start(
                "codegraphcontext",
                "refresh-all",
                note="full reindex fallback",
                seed_fallback={"active": True, "reason": "seed export failed"},
            )
            data = read_setup_progress(progress.path)
            assert data is not None
            clock.advance(42)
            status = progress_status(data, clock=clock)
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["heartbeatAgeSeconds"], 42.0)
            self.assertEqual(status["currentPhase"]["action"], "refresh-all")
            self.assertEqual(status["currentPhase"]["elapsedSeconds"], 42.0)
            self.assertEqual(status["seedFallback"]["reason"], "seed export failed")
            self.assertEqual(status["completedPhases"], ["grepai clone-db: ok"])
            progress.finish(state="ok")

    def test_stale_heartbeat_projects_as_stale(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_start("codegraphcontext", "refresh-all")
            data = read_setup_progress(progress.path)
            assert data is not None
            clock.advance(STALE_AFTER_SECONDS + 1)
            status = progress_status(data, clock=clock)
            self.assertEqual(status["state"], "stale")
            progress.finish(state="ok")

    def test_failed_projection_lists_failed_phases(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_start("watchers", "start")
            progress.phase_done(
                {"ok": False, "provider": "watchers", "action": "start", "reason": "docker down"}
            )
            progress.finish(state="failed", error="provider setup failed")
            data = read_setup_progress(progress.path)
            assert data is not None
            status = progress_status(data, clock=clock)
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["error"], "provider setup failed")
            self.assertEqual(status["failedPhases"], ["watchers start: failed (docker down)"])
            self.assertNotIn("heartbeatAgeSeconds", status)

    def test_skipped_phases_do_not_count_as_failed(self) -> None:
        clock = ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            progress = progress_file(Path(tmp), clock)
            progress.phase_start("codegraphcontext", "seed")
            progress.phase_done(
                {"ok": False, "skipped": True, "reason": "HEAD commits differ"}
            )
            progress.finish(state="ok")
            data = read_setup_progress(progress.path)
            assert data is not None
            status = progress_status(data, clock=clock)
            self.assertNotIn("failedPhases", status)


if __name__ == "__main__":
    unittest.main()
