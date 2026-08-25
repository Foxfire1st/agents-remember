"""The durability instrument refuses zero-attempt and incomplete stress results."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest
from _durability_measurement import (
    MIN_SUCCESSFUL_RECLAIMS,
    VacuousRunError,
    require_stress_measurement,
)


def _result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "case": "gate",
        "requested": 200,
        "attempted": 200,
        "completed": 200,
        "lost": 0,
        "reclaim_attempts": MIN_SUCCESSFUL_RECLAIMS,
        "successful_reclaims": MIN_SUCCESSFUL_RECLAIMS,
        "reclaim_error_count": 0,
        "reclaim_errors": [],
        "stragglers": [],
    }
    result.update(overrides)
    return result


@pytest.mark.evidence_stress
class DurabilityMeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def test_zero_attempts_are_refused_even_when_zero_loss_would_be_reported(self) -> None:
        with self.assertRaises(VacuousRunError) as refusal:
            require_stress_measurement(
                _result(
                    attempted=0,
                    completed=0,
                    reclaim_attempts=MIN_SUCCESSFUL_RECLAIMS + 5,
                    successful_reclaims=MIN_SUCCESSFUL_RECLAIMS + 5,
                ),
                self.work,
            )

        self.assertIn("attempted 0 of 200 required writes", str(refusal.exception))
        self.assertIn("will not report '0 lost'", str(refusal.exception))

    def test_every_shortfall_and_straggler_is_reported_together(self) -> None:
        with self.assertRaises(VacuousRunError) as refusal:
            require_stress_measurement(
                _result(
                    attempted=150,
                    completed=125,
                    reclaim_attempts=2,
                    successful_reclaims=2,
                    stragglers=["reclaimer", "appender-3"],
                ),
                self.work,
            )

        message = str(refusal.exception)
        self.assertIn("attempted 150 of 200 required writes", message)
        self.assertIn("completed 125 of 150 attempted writes", message)
        self.assertIn("processes did not stop: appender-3, reclaimer", message)
        self.assertIn(f"below the required {MIN_SUCCESSFUL_RECLAIMS}", message)

    def test_ten_failed_reclaims_are_not_ten_successful_compactions(self) -> None:
        with self.assertRaises(VacuousRunError) as refusal:
            require_stress_measurement(
                _result(
                    reclaim_attempts=10,
                    successful_reclaims=0,
                    reclaim_error_count=10,
                    reclaim_errors=["OSError"],
                ),
                self.work,
            )

        message = str(refusal.exception)
        self.assertIn("10 of 10 attempted compactions raised", message)
        self.assertIn("completed 0 successful compaction", message)
        self.assertIn("inspect", message)
        self.assertIn("rerun with zero reclaim errors", message)
        self.assertIn("will not report '0 lost'", message)

    def test_a_complete_run_is_returned_unchanged(self) -> None:
        result = _result()

        accepted = require_stress_measurement(result, self.work)

        self.assertIs(accepted, result)


if __name__ == "__main__":
    unittest.main()
