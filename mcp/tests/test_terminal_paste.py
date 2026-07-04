"""Tests for the server-side echo-confirmed paste helper (``serving.terminal_paste``, slice L2).

The paster mirrors the frontend ``pasteAndConfirm`` / ``submitAndConfirm`` over tmux primitives. Every
tmux operation is injectable, so the confirmation loop is driven against an in-memory fake pane -- no
real tmux server and no real sleeping (an injected clock + no-op sleep make the timeouts deterministic).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal_paste import (
    TerminalPaster,
    sanitize_for_injection,
)


class _FakePane:
    """An in-memory tmux pane: buffers, paste/key sinks, and a growing visible-content string."""

    def __init__(self, *, echo: bool = True, submit_echo: bool = True) -> None:
        self.content = "prompt> "
        self.buffers: dict[str, str] = {}
        self.pasted: list[tuple[str, str, str | None]] = []
        self.keys: list[tuple[str, str]] = []
        self.echo = echo
        self.submit_echo = submit_echo

    def set_buffer(self, name: str, text: str) -> None:
        self.buffers[name] = text

    def paste_buffer(self, tmux_name: str, buffer_name: str) -> None:
        self.pasted.append((tmux_name, buffer_name, self.buffers.get(buffer_name)))
        if self.echo:
            self.content += "\n[Pasted text #1]"

    def send_key(self, tmux_name: str, key: str) -> None:
        self.keys.append((tmux_name, key))
        if key == "Enter" and self.submit_echo:
            self.content += "\nassistant: working"

    def capture(self, _tmux_name: str) -> str:
        return self.content


class _BootingPane(_FakePane):
    """A pane that repaints boot output while discarding pasted stdin."""

    def paste_buffer(self, tmux_name: str, buffer_name: str) -> None:
        self.pasted.append((tmux_name, buffer_name, self.buffers.get(buffer_name)))
        self.content += "\nloading MCP servers"


class _Clock:
    """A monotonic stand-in that advances a fixed step per call so timeouts are hit deterministically."""

    def __init__(self, step: float = 1.0) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def _paster(pane: _FakePane) -> TerminalPaster:
    return TerminalPaster(
        set_buffer=pane.set_buffer,
        paste_buffer=pane.paste_buffer,
        send_key=pane.send_key,
        capture_pane=pane.capture,
        sleep=lambda _seconds: None,
        monotonic=_Clock(),
    )


class SanitizeTests(unittest.TestCase):
    def test_strips_suspend_byte_and_paste_markers_keeps_newline_and_tab(self) -> None:
        raw = "line1\n\ttab\x1asuspend\x1b[200~marker\x1b[201~\rend"
        cleaned = sanitize_for_injection(raw)
        self.assertNotIn("\x1a", cleaned)
        self.assertNotIn("\x1b[200~", cleaned)
        self.assertNotIn("\x1b[201~", cleaned)
        self.assertNotIn("\r", cleaned)
        self.assertIn("\n", cleaned)
        self.assertIn("\ttab", cleaned)
        self.assertIn("marker", cleaned)


class PasteTests(unittest.TestCase):
    def test_paste_echo_confirmed_without_submit_leaves_draft(self) -> None:
        pane = _FakePane(echo=True)
        result = _paster(pane).paste("ar-worker", "context packet", submit=False)
        self.assertTrue(result.delivered)
        self.assertFalse(result.submitted)
        # One bracketed paste of the sanitized text; no Enter (draft stays a draft).
        self.assertEqual(len(pane.pasted), 1)
        self.assertEqual(pane.pasted[0][2], "context packet")
        self.assertEqual(pane.keys, [])

    def test_paste_and_submit_presses_enter_and_confirms(self) -> None:
        pane = _FakePane(echo=True, submit_echo=True)
        result = _paster(pane).paste("ar-worker", "go", submit=True)
        self.assertTrue(result.delivered)
        self.assertTrue(result.submitted)
        self.assertEqual(pane.keys, [("ar-worker", "Enter")])

    def test_unechoed_paste_reports_unconfirmed_delivery_after_boot_deadline(self) -> None:
        pane = _FakePane(echo=False)
        result = _paster(pane).paste(
            "ar-worker", "discarded", submit=True, echo_timeout=1, boot_deadline=8
        )
        self.assertFalse(result.delivered)
        self.assertFalse(result.submitted)
        # A discarded paste is retried across the boot window (more than one attempt).
        self.assertGreaterEqual(len(pane.pasted), 2)
        # Never submit an unconfirmed paste.
        self.assertEqual(pane.keys, [])

    def test_boot_output_advance_without_paste_echo_does_not_confirm_delivery(self) -> None:
        pane = _BootingPane(echo=False)
        result = _paster(pane).paste(
            "ar-worker", "discarded", submit=True, echo_timeout=1, boot_deadline=8
        )
        self.assertFalse(result.delivered)
        self.assertFalse(result.submitted)
        self.assertGreaterEqual(len(pane.pasted), 2)
        self.assertEqual(pane.keys, [])

    def test_submit_unconfirmed_when_enter_produces_no_output(self) -> None:
        pane = _FakePane(echo=True, submit_echo=False)
        result = _paster(pane).paste("ar-worker", "go", submit=True, submit_timeout=2)
        self.assertTrue(result.delivered)
        self.assertFalse(result.submitted)
        self.assertEqual(pane.keys, [("ar-worker", "Enter")])


if __name__ == "__main__":
    unittest.main()
