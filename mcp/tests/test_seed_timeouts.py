"""Provider seed operations must detect wedges without punishing index size.

The seed/clone mechanic moves index data instead of re-indexing, so its
duration legitimately scales with index size and must not be total-time
capped. A wedge's signature is silence: the GrepAI clone runs under a stall
watchdog that kills only after a window of zero progress, returning a
structured phase-named result (CGC export/import keeps the configurable
provider-setup cap across the lifecycle-CLI boundary).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers.grepai import seed as grepai_seed
from agents_remember.providers.grepai.lifecycle.runner import grepai_scan_state_from_log


def _context() -> Any:
    # Duck-typed stand-in for GrepaiCloneContext; the clone only reads attributes.
    return SimpleNamespace(
        target_coordination_root=Path.cwd(),
        source_container="src-pg",
        source_password="pw",
        source_user="grepai",
        source_database="grepai",
        target_container="dst-pg",
        target_password="pw",
        target_user="grepai",
        target_database="grepai",
    )


class StallWatchdogTests(unittest.TestCase):
    def test_completing_command_returns_completed_process(self) -> None:
        result = grepai_seed._run_with_stall_watchdog(
            [sys.executable, "-c", "print('done')"],
            grepai_seed._StallWatchdog(
                progress=lambda: 0,
                stall_seconds=30,
                poll_seconds=0.1,
            ),
            cwd=Path.cwd(),
            stdout=grepai_seed.subprocess.PIPE,
            stdin=grepai_seed.subprocess.DEVNULL,
        )
        assert result is not None
        self.assertEqual(result.returncode, 0)
        self.assertIn("done", result.stdout)

    def test_stalled_command_is_killed_and_returns_none(self) -> None:
        result = grepai_seed._run_with_stall_watchdog(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            grepai_seed._StallWatchdog(
                progress=lambda: 0,  # never advances
                stall_seconds=1,
                poll_seconds=0.1,
            ),
            cwd=Path.cwd(),
            stdout=grepai_seed.subprocess.PIPE,
            stdin=grepai_seed.subprocess.DEVNULL,
        )
        self.assertIsNone(result)

    def test_progress_resets_the_stall_clock(self) -> None:
        ticks = iter(range(1000))  # progress advances every poll
        result = grepai_seed._run_with_stall_watchdog(
            [sys.executable, "-c", "import time; time.sleep(0.6); print('ok')"],
            grepai_seed._StallWatchdog(
                progress=lambda: next(ticks),
                stall_seconds=1,
                poll_seconds=0.1,
            ),
            cwd=Path.cwd(),
            stdout=grepai_seed.subprocess.PIPE,
            stdin=grepai_seed.subprocess.DEVNULL,
        )
        assert result is not None
        self.assertEqual(result.returncode, 0)

    def test_nonzero_exit_is_reported_not_treated_as_stall(self) -> None:
        result = grepai_seed._run_with_stall_watchdog(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            grepai_seed._StallWatchdog(
                progress=lambda: 0,
                stall_seconds=30,
                poll_seconds=0.1,
            ),
            cwd=Path.cwd(),
            stdout=grepai_seed.subprocess.PIPE,
            stdin=grepai_seed.subprocess.DEVNULL,
        )
        assert result is not None
        self.assertEqual(result.returncode, 3)
        self.assertIn("boom", result.stderr)


class GrepaiCloneStallTests(unittest.TestCase):
    def test_dump_stall_returns_structured_phase_result(self) -> None:
        args = SimpleNamespace(dry_run=False)
        with mock.patch.object(grepai_seed, "_run_with_stall_watchdog", return_value=None):
            result = grepai_seed._clone_database(args, _context())

        self.assertFalse(result["ok"])
        self.assertTrue(result["stalled"])
        self.assertEqual(result["phase"], "dump")
        self.assertEqual(result["stallSeconds"], grepai_seed.GREPAI_CLONE_STALL_SECONDS)
        self.assertIn("no progress", result["error"])

    def test_restore_stall_reports_restore_phase(self) -> None:
        args = SimpleNamespace(dry_run=False, seed_stall_seconds=120)
        ok_dump = mock.Mock(returncode=0)
        with mock.patch.object(
            grepai_seed, "_run_with_stall_watchdog", side_effect=[ok_dump, None]
        ):
            result = grepai_seed._clone_database(args, _context())

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "restore")
        self.assertEqual(result["stallSeconds"], 120)

    def test_clone_has_no_total_time_cap(self) -> None:
        """The watchdog is the only bound: no timeout kwarg reaches Popen."""
        args = SimpleNamespace(dry_run=False)
        ok = mock.Mock(returncode=0)
        with mock.patch.object(
            grepai_seed, "_run_with_stall_watchdog", return_value=ok
        ) as watchdog:
            result = grepai_seed._clone_database(args, _context())

        self.assertTrue(result["ok"])
        for call in watchdog.call_args_list:
            self.assertNotIn("timeout", call.kwargs)
            # `stall_seconds` now rides the `_StallWatchdog` passed positionally beside the
            # command, so the cap is asserted on the object rather than on a loose kwarg.
            self.assertEqual(call.args[1].stall_seconds, grepai_seed.GREPAI_CLONE_STALL_SECONDS)


class GrepaiScanMarkerTests(unittest.TestCase):
    def test_marker_parsing(self) -> None:
        cases = (
            ("Indexing [████░] 99% (433/436) system/settings.json", "in-progress"),
            (
                "Indexing [████] 100% (436/436) tools.md\nInitial scan complete: 0 files indexed",
                "complete",
            ),
            ("Watching 1 projects for changes...", "unknown"),
            ("", "unknown"),
        )
        for text, expected in cases:
            self.assertEqual(grepai_scan_state_from_log(text), expected, msg=text[:40])


if __name__ == "__main__":
    unittest.main()
