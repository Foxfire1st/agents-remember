"""Pure contract tests for the non-accepting cadence route."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.testing import cadence_runner
from agents_remember.testing.dagger_admission import DaggerAdmissionError
from agents_remember.testing.evidence_lanes import EvidenceTrigger


class CadenceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.result = self.root / "reports/cadence.json"
        self.events = self.root / "reports/events.jsonl"
        self.phases = self.root / "reports/phases.json"

    def run_trigger(self, trigger: EvidenceTrigger) -> int:
        return cadence_runner.run_cadence_evidence(
            self.root,
            trigger=trigger,
            json_output=self.result,
            pytest_report_log=self.events,
            pytest_phase_report=self.phases,
        )

    def test_host_process_is_refused_before_inventory_or_execution(self) -> None:
        refusal = DaggerAdmissionError("not admitted")
        with (
            mock.patch.object(cadence_runner, "require_dagger_admission", side_effect=refusal),
            mock.patch.object(cadence_runner, "load_evidence_inventory") as inventory,
            mock.patch.object(cadence_runner.subprocess, "run") as execute,
            self.assertRaises(DaggerAdmissionError),
        ):
            self.run_trigger(EvidenceTrigger.SCHEDULED)

        inventory.assert_not_called()
        execute.assert_not_called()

    def test_scheduled_stress_is_serial_loud_and_explicitly_non_accepting(self) -> None:
        completed = subprocess.CompletedProcess([], 0)
        with (
            mock.patch.object(cadence_runner, "require_dagger_admission"),
            mock.patch.object(
                cadence_runner,
                "load_evidence_inventory",
                return_value=SimpleNamespace(artifacts=()),
            ),
            mock.patch.object(cadence_runner.subprocess, "run", return_value=completed) as execute,
        ):
            exit_code = self.run_trigger(EvidenceTrigger.SCHEDULED)

        self.assertEqual(exit_code, 0)
        command = execute.call_args.args[0]
        self.assertIn("-n=0", command)
        self.assertEqual(command[command.index("-m", 3) + 1], "evidence_stress")
        self.assertIs(execute.call_args.kwargs["stdin"], subprocess.DEVNULL)
        payload = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertFalse(payload["acceptanceEligible"])
        self.assertFalse(payload["certifying"])
        self.assertEqual(payload["trigger"], "scheduled")

    def test_provider_bump_uses_its_own_population(self) -> None:
        completed = subprocess.CompletedProcess([], 1)
        with (
            mock.patch.object(cadence_runner, "require_dagger_admission"),
            mock.patch.object(
                cadence_runner,
                "load_evidence_inventory",
                return_value=SimpleNamespace(artifacts=()),
            ),
            mock.patch.object(cadence_runner.subprocess, "run", return_value=completed) as execute,
        ):
            exit_code = self.run_trigger(EvidenceTrigger.PROVIDER_BUMP)

        self.assertEqual(exit_code, 1)
        command = execute.call_args.args[0]
        self.assertEqual(command[command.index("-m", 3) + 1], "evidence_provider")
        payload = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "failed")

    def test_empty_migration_window_is_not_applicable_without_running_pytest(self) -> None:
        with (
            mock.patch.object(cadence_runner, "require_dagger_admission"),
            mock.patch.object(
                cadence_runner,
                "load_evidence_inventory",
                return_value=SimpleNamespace(artifacts=()),
            ),
            mock.patch.object(cadence_runner.subprocess, "run") as execute,
        ):
            exit_code = self.run_trigger(EvidenceTrigger.MIGRATION_WINDOW)

        self.assertEqual(exit_code, 0)
        execute.assert_not_called()
        payload = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "not-applicable")
        self.assertFalse(payload["executed"])

    def test_release_and_diagnostic_triggers_are_not_shadow_quality_routes(self) -> None:
        with mock.patch.object(cadence_runner, "require_dagger_admission"):
            for trigger in (EvidenceTrigger.RELEASE, EvidenceTrigger.DIAGNOSTIC):
                with (
                    self.subTest(trigger=trigger),
                    self.assertRaisesRegex(
                        ValueError,
                        "cadence runner accepts only",
                    ),
                ):
                    self.run_trigger(trigger)
