"""L81-F29: provision reads the filed report's host part when the tool answer cut it.

The R108 answer guard can cut a long install answer to its short form, which names
the filed report but drops the host part. ``_provision`` reads the filed report the
answer names instead of failing a provision the report shows succeeded; no host
anywhere is still the same refusal.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

sys.path.insert(0, (Path(__file__).resolve().parents[2] / "scripts").as_posix())

from pnt_sandbox import commands
from pnt_sandbox.layout import SandboxLayout
from pnt_sandbox.operations import StepFailed


def _report(home: str) -> dict[str, object]:
    return {
        "ok": True,
        "operation": "runtime_install",
        "host": {
            "ok": True,
            "home": home,
            "daemon": {"action": "started", "reasons": []},
        },
    }


class ProvisionReportFallbackTests(unittest.TestCase):
    def test_short_form_answer_provisions_from_the_filed_report(self) -> None:
        """The R108-cut answer (no host, reportPath only) still provisions."""
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "install.json"
            report.write_text(json.dumps(_report(str(Path(tmp) / "home"))))
            compact = {
                "ok": True,
                "overflow": True,
                "answerTokens": 908,
                "reportPath": report.as_posix(),
            }
            supervisor = SimpleNamespace(pid=4242)
            ops = mock.Mock()
            ops.paseo_supervisor.return_value = supervisor
            ops.runtime_install.return_value = compact
            layout = SimpleNamespace(
                run_dir=Path(tmp),
                paseo_home=Path(tmp) / "home",
                paseo_listen="127.0.0.1:6871",
            )
            said: list[str] = []
            started = commands._Started()
            got = commands._provision(
                cast(SandboxLayout, layout),
                Path(tmp),
                ops,
                said.append,
                started,  # type: ignore[arg-type]
            )
            self.assertIs(got, supervisor)
            self.assertTrue(started.paseo)

    def test_missing_host_and_missing_report_still_refuses(self) -> None:
        """No host anywhere is still the same refusal, not a new path."""
        ops = mock.Mock()
        ops.paseo_supervisor.return_value = SimpleNamespace(pid=1)
        ops.runtime_install.return_value = {"ok": True}
        layout = SimpleNamespace(
            run_dir=Path(tempfile.mkdtemp()),
            paseo_home=Path(tempfile.mkdtemp()),
            paseo_listen="127.0.0.1:6871",
        )
        with self.assertRaises(StepFailed):
            commands._provision(  # type: ignore[arg-type]
                cast(SandboxLayout, layout),
                layout.run_dir,
                ops,
                lambda line: None,
                commands._Started(),
            )
