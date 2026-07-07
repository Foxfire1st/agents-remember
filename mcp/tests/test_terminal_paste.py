"""Tests for the server-side capture-verified paste helper (``serving.terminal_paste``, slice L2).

The paster mirrors the frontend ``pasteAndConfirm`` / ``submitAndConfirm`` over tmux primitives. Every
tmux operation is injectable, so the confirmation loop is driven against an in-memory fake pane -- no
real tmux server and no real sleeping (an injected clock + no-op sleep make the timeouts deterministic).

The ``DeliveryIntegrityTests`` class encodes 260707-HFX-L3: the SF-1 blind seat (codex chip vocabulary
unrecognized -> false verdicts) and the F-V duplicate stack (blind retry re-pasted a landed paste up
to 7 times). Each scenario failed against the pre-fix seam by construction.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal_paste import (
    TerminalPaster,
    _capture_pane_argv,
    count_paste_chips,
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

    def load_buffer(self, name: str, text: str) -> None:
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


class _CodexChipPane(_FakePane):
    """A codex composer: a large paste renders ONLY the ``[Pasted Content N chars]`` chip (SF-1)."""

    def __init__(self) -> None:
        super().__init__(echo=False)
        self.content = "codex> "

    def paste_buffer(self, tmux_name: str, buffer_name: str) -> None:
        self.pasted.append((tmux_name, buffer_name, self.buffers.get(buffer_name)))
        self.content += "\n[Pasted Content 5324 chars]"


class _LaggyChipPane(_FakePane):
    """A codex composer whose chip renders a beat BEHIND the paste (the F-V race).

    The chip appears only after ``lag_captures`` further pane captures -- past the first attempt's
    echo window, so only the retry path's re-capture guard can see it landed.
    """

    def __init__(self, lag_captures: int = 2) -> None:
        super().__init__(echo=False)
        self.content = "codex> "
        self.lag = lag_captures
        self._pending: int | None = None

    def paste_buffer(self, tmux_name: str, buffer_name: str) -> None:
        self.pasted.append((tmux_name, buffer_name, self.buffers.get(buffer_name)))
        self._pending = self.lag

    def capture(self, _tmux_name: str) -> str:
        if self._pending is not None:
            self._pending -= 1
            if self._pending <= 0:
                self.content += "\n[Pasted Content 5324 chars]"
                self._pending = None
        return self.content


class _ScrollingCodexPane(_FakePane):
    """A TRUNCATING codex pane: capture returns only the last ``window`` lines (review N1 honesty).

    Origin shows two OLD chips; every capture appends a boot-log line so those chips scroll out of
    the window before the new chip renders (itself lagging two captures behind the paste). Bare
    chip-count growth is blind here -- the final window still counts two chips -- so only the
    payload-specific probe can prove the landing.
    """

    def __init__(self, *, window: int = 6) -> None:
        super().__init__(echo=False)
        self.window = window
        self.lines: list[str] = [
            "[Pasted Content 111 chars]",
            "[Pasted Content 222 chars]",
            "codex> ",
        ]
        self._log = 0
        self._chip_pending: int | None = None

    def paste_buffer(self, tmux_name: str, buffer_name: str) -> None:
        self.pasted.append((tmux_name, buffer_name, self.buffers.get(buffer_name)))
        self._chip_pending = 2

    def capture(self, _tmux_name: str) -> str:
        self.lines.append(f"boot log {self._log}")
        self._log += 1
        if self._chip_pending is not None:
            self._chip_pending -= 1
            if self._chip_pending <= 0:
                self.lines.append("[Pasted Content 4096 chars]")
                self._chip_pending = None
        return "\n".join(self.lines[-self.window :])


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
        load_buffer=pane.load_buffer,
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


class ChipCountTests(unittest.TestCase):
    def test_counts_both_harness_chip_vocabularies(self) -> None:
        # Claude Code renders [Pasted text #N]; codex renders [Pasted Content N chars]. Both are
        # delivery evidence (260707-HFX-L3: the codex form was unrecognized -> SF-1 false verdicts).
        pane = (
            "claude> [Pasted text #1]\n"
            "[Pasted text]\n"
            "codex> [Pasted Content 5324 chars]\n"
            "[pasted content 12 chars] not-a-chip: [Pasted Content chars]"
        )
        self.assertEqual(count_paste_chips(pane), 3)

    def test_plain_pane_has_no_chips(self) -> None:
        self.assertEqual(count_paste_chips("prompt> loading MCP servers"), 0)


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
        # A verifiably-unlanded paste is retried across the boot window (more than one attempt) --
        # the idempotence guard re-captured first and found no trace each time.
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


class DeliveryIntegrityTests(unittest.TestCase):
    """260707-HFX-L3: capture-verified delivery, idempotent retry, loud failure, no Escape.

    Each test failed against the pre-fix seam: the codex chip was unrecognized (SF-1 -> blind
    retries, F-V -> 7 stacked duplicates) and a failed verification returned a bare boolean with
    no pane evidence.
    """

    def test_codex_pasted_content_chip_confirms_delivery_with_a_single_paste(self) -> None:
        # SF-1: a codex target renders a large paste as [Pasted Content N chars] -- that chip IS
        # the delivery confirmation. Pre-fix the vocabulary missed it, reported unconfirmed, and
        # blind-retried until the boot deadline.
        pane = _CodexChipPane()
        result = _paster(pane).paste("ar-reviewer", "x" * 5324, submit=False, echo_timeout=1)
        self.assertTrue(result.delivered)
        self.assertEqual(len(pane.pasted), 1)

    def test_late_rendering_chip_is_seen_by_recapture_and_never_repasted(self) -> None:
        # F-V: the TUI renders a beat behind keystrokes. The first attempt's echo window closes
        # before the chip appears; the retry path re-captures FIRST, sees the landed chip, and
        # returns delivered WITHOUT re-pasting -- duplicate stacking impossible by construction.
        pane = _LaggyChipPane(lag_captures=2)
        result = _paster(pane).paste(
            "ar-reviewer", "y" * 4096, submit=False, echo_timeout=1, boot_deadline=30
        )
        self.assertTrue(result.delivered)
        self.assertEqual(len(pane.pasted), 1)
        self.assertEqual(count_paste_chips(pane.content), 1)

    def test_unverifiable_delivery_returns_false_with_the_pane_capture_attached(self) -> None:
        # Loud failure: delivered=False must carry the final pane capture as evidence -- the
        # caller diagnoses the blind seat from the result, never from a trusted boolean.
        pane = _FakePane(echo=False)
        result = _paster(pane).paste(
            "ar-worker", "discarded", submit=True, echo_timeout=1, boot_deadline=8
        )
        self.assertFalse(result.delivered)
        self.assertEqual(result.capture, pane.content)
        self.assertIn("prompt>", result.capture)

    def test_successful_delivery_also_attaches_the_confirming_capture(self) -> None:
        pane = _CodexChipPane()
        result = _paster(pane).paste("ar-reviewer", "packet", submit=False, echo_timeout=1)
        self.assertTrue(result.delivered)
        self.assertIn("[Pasted Content 5324 chars]", result.capture)

    def test_escape_is_refused_by_the_delivery_seam(self) -> None:
        # Run discipline (dispatch-pack PASTE DISCIPLINE): Escape interrupts a codex session, so
        # the delivery seam refuses it by construction.
        pane = _FakePane()
        paster = _paster(pane)
        with self.assertRaises(ValueError):
            paster._press("ar-reviewer", "Escape")  # the guard itself is the contract under test
        self.assertEqual(pane.keys, [])

    def test_only_enter_is_ever_sent_across_paste_and_submit(self) -> None:
        pane = _FakePane(echo=True, submit_echo=True)
        _paster(pane).paste("ar-worker", "go", submit=True)
        self.assertTrue(all(key == "Enter" for _tmux, key in pane.keys))

    def test_old_chips_scrolling_out_of_view_do_not_cause_a_duplicate_repaste(self) -> None:
        # Review N1: two old chips visible at origin scroll out of the (truncating) capture window
        # before the new chip renders, so bare count-growth stays blind -- the final window still
        # counts exactly two chips. The payload-SPECIFIC codex chip probe ([Pasted Content <len>
        # chars]) proves the landing anyway: one paste, no duplicate.
        pane = _ScrollingCodexPane(window=6)
        result = _paster(pane).paste(
            "ar-reviewer", "z" * 4096, submit=False, echo_timeout=1, boot_deadline=30
        )
        self.assertTrue(result.delivered)
        self.assertEqual(len(pane.pasted), 1)
        # The blindness being defended against: no NET chip growth in the truncated window.
        self.assertEqual(count_paste_chips(result.capture), 2)
        self.assertIn("[Pasted Content 4096 chars]", result.capture)
        self.assertNotIn("[Pasted Content 111 chars]", result.capture)


class CaptureWindowTests(unittest.TestCase):
    def test_verification_capture_includes_bounded_history(self) -> None:
        # Review N1: viewport-only capture (-p without -S) let landed chips scroll out of the
        # verification universe. Origin and verification captures share this argv, so growth math
        # compares like against like.
        self.assertEqual(
            _capture_pane_argv("ar-worker"),
            ["tmux", "capture-pane", "-p", "-S", "-200", "-t", "ar-worker"],
        )


if __name__ == "__main__":
    unittest.main()
