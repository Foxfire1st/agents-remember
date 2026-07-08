"""Tests for the per-harness delivery adapter (260707-HFX2-L3, R2 + R5).

Fixtures are captured-pane-shaped text for both known harnesses across the pilot run's trigger
states: boot (nothing rendered yet), ready (empty composer), mid-turn (actively generating),
chip-stacked (the F-V trigger), and a modal trap (codex quota (#20) / a permission prompt).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.harness_adapters import (
    CLAUDE_CODE_ADAPTER,
    CODEX_ADAPTER,
    GENERIC_ADAPTER,
    get_adapter,
)

# --- claude-code pane fixtures -------------------------------------------------------------------

CLAUDE_BOOT = ""
CLAUDE_READY = "Welcome to Claude Code\n>\n"
CLAUDE_MID_TURN = "Thinking about the request...\nesc to interrupt\n"
CLAUDE_CHIP_STACKED = "[Pasted text #1]\n[Pasted text #2]\n>\n"
CLAUDE_BLOCKED = "Do you want to proceed?\n(y/n)\n"

# --- codex pane fixtures --------------------------------------------------------------------------

CODEX_BOOT = ""
CODEX_READY = "codex\n>\n"
CODEX_MID_TURN = "Working...\nesc to interrupt\n"
CODEX_CHIP_STACKED = "[Pasted Content 40 chars]\n[Pasted Content 41 chars]\n>\n"
CODEX_QUOTA_MODAL = "Approaching rate limits — switch model?\n"
CODEX_PERMISSION_PROMPT = "Allow command: rm build/?\n"


class ClaudeCodeAdapterTests(unittest.TestCase):
    def test_boot_pane_is_not_ready(self) -> None:
        self.assertFalse(CLAUDE_CODE_ADAPTER.boot_ready(CLAUDE_BOOT))

    def test_ready_pane_with_empty_composer(self) -> None:
        self.assertTrue(CLAUDE_CODE_ADAPTER.boot_ready(CLAUDE_READY))
        self.assertEqual(CLAUDE_CODE_ADAPTER.composer_state(CLAUDE_READY), "empty")

    def test_mid_turn_pane(self) -> None:
        self.assertTrue(CLAUDE_CODE_ADAPTER.mid_turn(CLAUDE_MID_TURN))
        self.assertEqual(CLAUDE_CODE_ADAPTER.mid_turn_behavior(CLAUDE_MID_TURN), "queued-next-turn")
        self.assertIsNone(CLAUDE_CODE_ADAPTER.blocked_reason(CLAUDE_MID_TURN))

    def test_chip_stacked_composer(self) -> None:
        self.assertEqual(CLAUDE_CODE_ADAPTER.composer_state(CLAUDE_CHIP_STACKED), "chip-stacked")

    def test_blocked_confirmation_modal(self) -> None:
        self.assertEqual(CLAUDE_CODE_ADAPTER.blocked_reason(CLAUDE_BLOCKED), "permission-prompt")

    def test_turn_started_by_generic_advance(self) -> None:
        self.assertTrue(CLAUDE_CODE_ADAPTER.turn_started(CLAUDE_MID_TURN, advanced=True))

    def test_turn_started_corroborated_by_spinner_when_not_advanced(self) -> None:
        # The generic byte-diff hasn't fired yet (advanced=False), but the pane already shows the
        # busy/spinner marker -- the harness-aware corroboration this leaf adds.
        self.assertTrue(CLAUDE_CODE_ADAPTER.turn_started(CLAUDE_MID_TURN, advanced=False))

    def test_turn_not_started_on_an_idle_pane(self) -> None:
        self.assertFalse(CLAUDE_CODE_ADAPTER.turn_started(CLAUDE_READY, advanced=False))


class CodexAdapterTests(unittest.TestCase):
    def test_boot_pane_is_not_ready(self) -> None:
        self.assertFalse(CODEX_ADAPTER.boot_ready(CODEX_BOOT))

    def test_ready_pane_with_empty_composer(self) -> None:
        self.assertTrue(CODEX_ADAPTER.boot_ready(CODEX_READY))
        self.assertEqual(CODEX_ADAPTER.composer_state(CODEX_READY), "empty")

    def test_mid_turn_pane(self) -> None:
        self.assertTrue(CODEX_ADAPTER.mid_turn(CODEX_MID_TURN))
        self.assertEqual(CODEX_ADAPTER.mid_turn_behavior(CODEX_MID_TURN), "queued-next-turn")

    def test_chip_stacked_composer_uses_the_codex_chip_vocabulary(self) -> None:
        self.assertEqual(CODEX_ADAPTER.composer_state(CODEX_CHIP_STACKED), "chip-stacked")

    def test_quota_modal_is_blocked_with_a_structured_reason(self) -> None:
        # Issue #20: "Approaching rate limits — switch model?" ends the seat's turn and needs a
        # developer decision -- classified distinctly from an ordinary permission prompt.
        self.assertEqual(CODEX_ADAPTER.blocked_reason(CODEX_QUOTA_MODAL), "codex-quota-limit")

    def test_permission_prompt_is_blocked_but_not_quota(self) -> None:
        self.assertEqual(CODEX_ADAPTER.blocked_reason(CODEX_PERMISSION_PROMPT), "permission-prompt")

    def test_mid_turn_never_misread_as_blocked(self) -> None:
        # A confirmation-shaped string in scrollback must not override an active busy marker.
        pane = "Approaching rate limits — switch model? already dismissed.\nesc to interrupt\n"
        self.assertTrue(CODEX_ADAPTER.mid_turn(pane))
        self.assertIsNone(CODEX_ADAPTER.blocked_reason(pane))


class AdapterRegistryTests(unittest.TestCase):
    def test_known_harness_ids_resolve_to_named_adapters(self) -> None:
        self.assertIs(get_adapter("claude"), CLAUDE_CODE_ADAPTER)
        self.assertIs(get_adapter("codex"), CODEX_ADAPTER)

    def test_unknown_or_missing_harness_falls_back_to_generic(self) -> None:
        self.assertIs(get_adapter(None), GENERIC_ADAPTER)
        # An uncustomized future harness still classifies off the shared markers -- never a refusal.
        adapter = get_adapter("some-future-harness")
        self.assertEqual(adapter.blocked_reason(CLAUDE_BLOCKED), "permission-prompt")


if __name__ == "__main__":
    unittest.main()
