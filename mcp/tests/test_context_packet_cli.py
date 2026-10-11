"""The retained context-packet CLI keeps its drift-detail-limit contract (MIK-R112 rule 1).

The provider options are retired; ``--drift-detail-limit`` is retained
(MIK-R112 CODE1 F10): the request model still reads it, so the parser must
still offer it. Ordinary and explicit-limit calls through ``main`` with
fixture settings prove the contract with stable output and exit.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agents_remember.application.context_packet import run_drift_summary as _run_drift_summary
from agents_remember.cli.context_packet import main
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    summarize_rows,
)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Tests"],
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "fixture"], check=True, capture_output=True
    )


class ContextPacketCliTests(unittest.TestCase):
    def _settings(self, root: Path) -> Path:
        declare_test_process()
        coord = root / "coord"
        coord.mkdir(parents=True, exist_ok=True)
        _init_repo(coord / "agents-remember")
        path = root / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "repositories": {"agents-remember": {}},
                    "coordinationRoot": coord.as_posix(),
                    "workspaceRoot": coord.as_posix(),
                }
            ),
            encoding="utf-8",
        )
        return path

    def _run(self, *argv: str) -> tuple[int, dict]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(list(argv))
        return exit_code, json.loads(buffer.getvalue())

    def test_ordinary_call_builds_a_packet(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exit_code, packet = self._run(
                "--config", self._settings(root).as_posix(), "--repo", "agents-remember"
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(packet.get("contextPacketVersion"))

    def test_explicit_drift_detail_limit_is_honored(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "agents_remember.application.context_packet.run_drift_summary",
                wraps=_run_drift_summary,
            ) as drift_summary:
                exit_code, packet = self._run(
                    "--config",
                    self._settings(root).as_posix(),
                    "--repo",
                    "agents-remember",
                    "--include-drift",
                    "--drift-detail-limit",
                    "3",
                )
            self.assertEqual(exit_code, 0)
            self.assertTrue(packet.get("contextPacketVersion"))
            # The chosen value reaches the behavior layer, not just the parser.
            self.assertTrue(drift_summary.called)
            self.assertEqual(drift_summary.call_args.kwargs.get("detail_limit"), 3)
            # And the delivered packet obeys it: at most three actionable rows.
            drift = packet.get("drift") or {}
            self.assertLessEqual(len(drift.get("actionableSample") or []), 3)

    def test_drift_actionable_sample_obeys_detail_limit(self) -> None:
        # Pure boundary control: five actionable rows capped at three, counts intact.
        rows = [
            {"classification": "drifted", "source_file": f"s{i}", "onboarding_file": f"o{i}"}
            for i in range(5)
        ] + [
            {"classification": "clean", "source_file": "s5", "onboarding_file": "o5"},
            {"classification": "clean", "source_file": "s6", "onboarding_file": "o6"},
        ]
        packet = summarize_rows(rows, report_path="r", detail_limit=3)
        self.assertEqual(packet.get("count"), 7)
        self.assertEqual(packet.get("actionableCount"), 5)
        self.assertEqual(len(packet.get("actionableSample") or []), 3)
