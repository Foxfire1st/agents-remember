"""Unit tests for the L108 tool-log sandbox witness driver (MIK-R108 E4/E7).

The driver itself (``tool_log_witness.py``) runs by hand against a hermetic
world and writes the frozen evidence file; these tests pin its pure pieces
(the directory snapshot and the call recorder) and form its exact lifecycle
consumer.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tool_log_witness import (
    _assert_witness,
    _checks_destinations,
    _persisted_terminating_proof,
    _record_call,
    _snapshot,
)


class TestWitnessHelpers(unittest.TestCase):
    def test_snapshot_lists_files_with_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.jsonl").write_text("{}\n", encoding="utf-8")
            detail = root / "a"
            detail.mkdir()
            (detail / "whole.json").write_text("{}", encoding="utf-8")
            shot = _snapshot(root)
        self.assertTrue(shot["exists"])
        self.assertTrue(shot["is_dir"])
        self.assertEqual(shot["files"]["a.jsonl"]["bytes"], 3)
        self.assertEqual(shot["files"]["a/whole.json"]["bytes"], 2)

    def test_snapshot_of_missing_root_stays_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shot = _snapshot(Path(tmp) / "no-such-dir")
        self.assertFalse(shot["exists"])
        self.assertFalse(shot["is_dir"])
        self.assertEqual(shot["files"], {})

    def test_record_call_shapes_worker_and_raised_entries(self) -> None:
        calls: list[dict[str, Any]] = []

        def fake_call(name: str, args: dict[str, Any]) -> Any:
            if name == "ping":
                return "RETURNED", {"ok": True, "log": "folder/today/ping.json"}
            return "RAISED ToolError", {"detail": "no such tool"}

        world = SimpleNamespace(call=fake_call)
        _record_call(world, calls, "ping", {}, "worker")
        _record_call(world, calls, "nope", {}, "worker")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["status"], "RETURNED")
        self.assertTrue(calls[0]["ok"])
        self.assertEqual(calls[0]["log"], "folder/today/ping.json")
        self.assertIsNone(calls[0]["raised_detail"])
        self.assertEqual(calls[1]["status"], "RAISED ToolError")
        self.assertEqual(calls[1]["raised_detail"], "no such tool")

    def test_destination_checks_separate_worker_and_controller(self) -> None:
        calls: list[dict[str, Any]] = [
            {"plane": "worker", "status": "RETURNED", "log": "/enc/logs/a.json"},
            {"plane": "worker", "status": "RETURNED", "log": "/enc/logs/b.json"},
            {"plane": "worker", "status": "RETURNED", "log": "/enc/logs/c.json"},
            {"plane": "controller", "status": "RETURNED", "log": "/root/logs/a.json"},
            {"plane": "controller", "status": "RETURNED", "log": "/root/logs/b.json"},
            {"plane": "controller", "status": "RETURNED", "log": "/root/logs/c.json"},
        ]
        checks = _checks_destinations(calls, Path("/enc/logs"), Path("/root/logs"))
        self.assertEqual(
            checks,
            {
                "worker_calls_logged": True,
                "worker_logs_in_enclosure": True,
                "controller_calls_logged": True,
                "controller_logs_at_root": True,
            },
        )
        misplaced = [dict(entry, log="/root/logs/x.json") for entry in calls[:3]] + calls[3:]
        checks = _checks_destinations(misplaced, Path("/enc/logs"), Path("/root/logs"))
        self.assertFalse(checks["worker_logs_in_enclosure"])

    def test_persisted_proof_keeps_parsed_line_and_inspected_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            day_dir = logs / "ar-tools-2026-10-08"
            day_dir.mkdir()
            (day_dir / "120000-worktree_cleanup-abcdef12.json").write_text(
                '{"ok": true, "state": "removed"}', encoding="utf-8"
            )
            (logs / "ar-tools-2026-10-08.jsonl").write_text(
                '\n{"tool": "worktree_cleanup", "ok": true, "detail": "120000-worktree_cleanup-abcdef12.json"}\n',
                encoding="utf-8",
            )
            proof = _persisted_terminating_proof(logs, [], "worktree_cleanup", "worktree_abandon")
        cleanup = proof["worktree_cleanup"]
        self.assertTrue(cleanup["line"]["ok"])
        self.assertEqual(cleanup["detail_whole"], {"ok": True, "state": "removed"})
        missing = proof["worktree_abandon"]
        self.assertIsNone(missing["line"])
        self.assertIsNone(missing["detail_whole"])

    def test_assert_witness_passes_on_success_and_fails_loudly(self) -> None:
        clean = {"porcelain": ""}
        proof_ok = {"line": {"ok": True}, "detail_whole": {"ok": True}}
        abandon_world = {
            "destination_checks": {"worker_logs_in_enclosure": True},
            "abandon_checks": {"returned_ok": True, "group_removed": True},
            "persisted_proof": {"worktree_abandon": proof_ok},
            "repository_state": {"code": clean, "memory": clean},
        }
        cleanup_world = {
            "cleanup_checks": {"cleanup_ok": True, "group_removed": True},
            "persisted_proof": {"worktree_cleanup": proof_ok},
            "outlived_group": {
                "status": "RETURNED",
                "log": "/root/x.json",
                "enclosure_logs_recreated": False,
            },
            "repository_state": {"code": clean, "memory": clean},
        }
        _assert_witness(abandon_world, cleanup_world)
        bad = dict(cleanup_world, cleanup_checks={"cleanup_ok": False, "group_removed": True})
        with self.assertRaises(SystemExit):
            _assert_witness(abandon_world, bad)
        dirty = dict(
            abandon_world, repository_state={"code": {"porcelain": " M x"}, "memory": clean}
        )
        with self.assertRaises(SystemExit):
            _assert_witness(dirty, cleanup_world)


if __name__ == "__main__":
    unittest.main()
