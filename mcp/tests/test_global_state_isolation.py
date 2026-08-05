"""The suite identifies the test that leaks an explicitly-owned mutable global."""

from __future__ import annotations

import unittest

from _global_state import restore_owned_mutable_state, snapshot_owned_mutable_state
from agents_remember.controlplane.durable_store import (
    declare_process_role,
    declared_process_role,
)


class GlobalStateLeakDetectionTests(unittest.TestCase):
    def test_a_deliberate_process_role_leak_is_reported_after_being_restored(self) -> None:
        previous = snapshot_owned_mutable_state()
        declare_process_role("dashboard")

        changed = restore_owned_mutable_state(previous)

        self.assertEqual(
            changed,
            [
                "agents_remember.controlplane.durable_store._declared: "
                "before={}, after={'role': 'dashboard'}"
            ],
        )
        self.assertIsNone(
            declared_process_role(),
            "restoration must happen before the leak becomes a test failure, so later tests "
            "cannot inherit the role",
        )

    def test_the_owned_state_register_does_not_claim_to_scan_unknown_globals(self) -> None:
        self.assertEqual(
            list(snapshot_owned_mutable_state()),
            ["agents_remember.controlplane.durable_store._declared"],
        )


if __name__ == "__main__":
    unittest.main()
