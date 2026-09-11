"""Separation guards for the parked open-turn external-await design.

The completion relay observes canonical terminal truth only after a subordinate provider turn has
ended. The parked external-await design is the opposite contract: a seat keeps its turn open while
an external condition resolves, registering a durable wait row that a watcher rechecks and
resurfaces. Combining the two would blur one lifecycle boundary into the other.

These guards fail if the parked mechanism is adopted as relay substrate, exposed as a public
capability, or imported into the shipped runtime as a fallback producer.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import get_args

import agents_remember
from agents_remember.controlplane.expectation_rows import (
    EXPECTATION_ROW_SCHEMA,
    ExpectationKind,
    ExpectationRow,
)
from agents_remember.kernel._agentic_settings_core import KNOWN_EXPECTATION_KINDS
from agents_remember.mcp.tools import PUBLIC_TOOLS
from pydantic import ValidationError

PACKAGE_ROOT = Path(agents_remember.__file__).resolve().parent

# Exact identifiers the parked design introduces: its wait expectation kind, its registration
# entry point, its resurface/recheck fields, and its agent-authored check descriptor. Generic
# words such as "wait" or "script" are deliberately excluded because they occur legitimately
# across the relay and the wider runtime.
PARKED_MECHANISM_IDENTIFIERS = (
    "register_wait",
    "external_await",
    "resurface_by",
    "recheck_cadence",
    "recheckCadenceSeconds",
    "check_descriptor",
    "CheckDescriptor",
    "last_checked_at",
    "lastCheckedAt",
    "last_exit_class",
    "lastExitClass",
    "waiting expectation",
)


def _row_payload(*, kind: str) -> dict[str, str]:
    return {
        "schema": EXPECTATION_ROW_SCHEMA,
        "id": "row-1",
        "ts": "2026-01-01T00:00:00+00:00",
        "kind": kind,
        "state": "pending",
        "createdAt": "2026-01-01T00:00:00+00:00",
        "dueAt": "2026-01-01T01:00:00+00:00",
        "sourceId": "source-1",
    }


class ParkedExternalAwaitSeparationTests(unittest.TestCase):
    def test_parked_waiting_expectation_kind_is_not_accepted(self) -> None:
        """A parked wait row cannot enter the relay as a completion fallback."""

        self.assertNotIn("waiting", get_args(ExpectationKind))
        self.assertNotIn("waiting", KNOWN_EXPECTATION_KINDS)
        with self.assertRaises(ValidationError):
            ExpectationRow.model_validate(_row_payload(kind="waiting"))

    def test_shipped_runtime_exposes_no_parked_await_mechanism(self) -> None:
        """No wait registration is advertised, and no wait/check machinery ships."""

        self.assertNotIn("register_wait", PUBLIC_TOOLS)
        offenders = [
            f"{path.relative_to(PACKAGE_ROOT)}:{identifier}"
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
            for identifier in PARKED_MECHANISM_IDENTIFIERS
            if identifier in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
