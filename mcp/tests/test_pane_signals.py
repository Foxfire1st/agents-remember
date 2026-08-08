"""Tests for the agent-notifier's pane-state classifier (260707-HFX2-L2, R2a)."""

from __future__ import annotations

import sys
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import unittest

from agents_remember.serving.pane_signals import classify_pane_signal


class PaneSignalClassifierTests(unittest.TestCase):
    def test_esc_to_interrupt_is_mid_turn(self) -> None:
        pane = "Thinking...\nesc to interrupt\n"
        result = classify_pane_signal(pane, harness="claude")
        self.assertEqual(result.signal, "mid-turn")

    def test_modal_confirmation_is_blocked(self) -> None:
        pane = "Do you want to proceed?\n(y/n)\n"
        result = classify_pane_signal(pane, harness="claude")
        self.assertEqual(result.signal, "blocked")

    def test_ordinary_output_is_normal(self) -> None:
        pane = "Here is a summary of the change.\nAll tests passed.\n"
        result = classify_pane_signal(pane, harness="claude")
        self.assertEqual(result.signal, "normal")

    def test_blank_pane_is_normal_not_a_trigger(self) -> None:
        result = classify_pane_signal("", harness="codex")
        self.assertEqual(result.signal, "normal")
        result = classify_pane_signal(None, harness="codex")
        self.assertEqual(result.signal, "normal")

    def test_mid_turn_takes_precedence_over_blocked_markers(self) -> None:
        # A busy marker anywhere in the pane must not be misread as blocked, even if a
        # confirmation-shaped string appears elsewhere in scrollback.
        pane = "Do you want to keep going? already answered.\nesc to interrupt\n"
        result = classify_pane_signal(pane, harness="claude")
        self.assertEqual(result.signal, "mid-turn")


if __name__ == "__main__":
    unittest.main()
