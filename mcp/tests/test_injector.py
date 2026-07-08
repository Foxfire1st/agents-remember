"""Tests for the ONE delivery path (260707-HFX2-L3, R1 + R3 + R5).

``DeliveryOutcomeMappingTests`` drives :func:`deliver` against a stub paster returning canned
``PasteResult``s -- fast, exact control over each branch of the four-way ``DeliveryOutcome``
contract. ``ScriptedTmuxE2ETests`` drives it against a real ``TerminalPaster`` wired to an
in-memory scripted pane (no real tmux, no real sleeping, mirroring ``test_terminal_paste.py``'s
fakes) -- the end-to-end injection test R5 asks for.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.injector import DeliveryRow, deliver, envelope_text
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster


class _StubPaster:
    """Returns a fixed :class:`PasteResult` regardless of what is pasted -- pure outcome mapping."""

    def __init__(self, result: PasteResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, bool]] = []

    def paste(self, tmux_name: str, text: str, *, submit: bool = False) -> PasteResult:
        self.calls.append((tmux_name, text, submit))
        return self.result


class EnvelopeTests(unittest.TestCase):
    def test_envelope_renders_kind_entry_id_and_ack_instruction(self) -> None:
        row = DeliveryRow(kind="nudge", entry_id="E1", text="please continue", ack_instruction="reply ok")
        text = envelope_text(row)
        self.assertIn("[Agents Remember delivery:nudge id=E1]", text)
        self.assertIn("ack: reply ok", text)
        self.assertIn("please continue", text)

    def test_envelope_false_ships_the_body_verbatim(self) -> None:
        row = DeliveryRow(kind="brief", entry_id="S1", text="you are the worker", envelope=False)
        self.assertEqual(envelope_text(row), "you are the worker")

    def test_envelope_without_ack_instruction_omits_the_ack_line(self) -> None:
        row = DeliveryRow(kind="brief", entry_id="S1", text="hello")
        text = envelope_text(row)
        self.assertNotIn("ack:", text)


class DeliveryOutcomeMappingTests(unittest.TestCase):
    def _row(self, **overrides: object) -> DeliveryRow:
        defaults: dict[str, object] = {"kind": "message", "entry_id": "E1", "text": "hello", "envelope": False}
        defaults.update(overrides)
        return DeliveryRow(**defaults)  # type: ignore[arg-type]

    def test_delivered_and_submitted_is_acked(self) -> None:
        paster = _StubPaster(PasteResult(delivered=True, submitted=True, capture="worker> ok"))
        result = deliver(self._row(), tmux_name="ar-1", paster=paster, harness="claude")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "acked")
        self.assertTrue(result.submitted)

    def test_draft_only_delivery_is_landed_unacked(self) -> None:
        paster = _StubPaster(PasteResult(delivered=True, submitted=False, capture="worker> draft"))
        result = deliver(self._row(submit=False), tmux_name="ar-1", paster=paster, harness="claude")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "landed-unacked")

    def test_submitted_but_turn_never_started_is_landed_unacked(self) -> None:
        # The generic pane-advance never fired AND the pane shows no busy marker -- the paste sat
        # there unsubmitted-in-effect (drop point 8: "delivered into a dead turn").
        paster = _StubPaster(PasteResult(delivered=True, submitted=False, capture="claude> idle draft"))
        result = deliver(self._row(submit=True), tmux_name="ar-1", paster=paster, harness="claude")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "landed-unacked")
        self.assertFalse(result.submitted)

    def test_submitted_and_spinner_corroborates_the_turn_started(self) -> None:
        # Generic advance flag is False (the diff hasn't fired this poll) but the pane already
        # shows the busy marker -- the harness-aware corroboration promotes this to acked.
        paster = _StubPaster(PasteResult(delivered=True, submitted=False, capture="esc to interrupt"))
        result = deliver(self._row(submit=True), tmux_name="ar-1", paster=paster, harness="claude")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "acked")

    def test_never_capture_verified_is_failed(self) -> None:
        paster = _StubPaster(PasteResult(delivered=False, submitted=False, capture="codex> (clean boot)"))
        result = deliver(self._row(), tmux_name="ar-1", paster=paster, harness="codex")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "failed")

    def test_codex_quota_modal_is_blocked_even_though_paste_landed(self) -> None:
        # A modal trap in the FINAL capture overrides an otherwise "delivered" reading -- a paste
        # that nominally landed into a quota dialog is not something a caller should treat as acked.
        capture = "Approaching rate limits — switch model?"
        paster = _StubPaster(PasteResult(delivered=True, submitted=True, capture=capture))
        result = deliver(self._row(), tmux_name="ar-1", paster=paster, harness="codex")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.reason, "codex-quota-limit")

    def test_permission_prompt_is_blocked_with_a_distinct_reason(self) -> None:
        paster = _StubPaster(PasteResult(delivered=False, submitted=False, capture="Do you want to proceed?\n(y/n)"))
        result = deliver(self._row(), tmux_name="ar-1", paster=paster, harness="claude")  # type: ignore[arg-type]
        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.reason, "permission-prompt")

    def test_envelope_text_is_what_gets_pasted(self) -> None:
        paster = _StubPaster(PasteResult(delivered=True, submitted=True, capture=""))
        row = self._row(kind="nudge", ack_instruction="reply ok", envelope=True)
        deliver(row, tmux_name="ar-1", paster=paster, harness="claude")  # type: ignore[arg-type]
        self.assertIn("[Agents Remember delivery:nudge id=E1]", paster.calls[0][1])
        self.assertIn("ack: reply ok", paster.calls[0][1])


class _ScriptedPane:
    """A minimal in-memory codex-shaped pane driving a real ``TerminalPaster`` end-to-end."""

    def __init__(self, initial: str = "codex> ") -> None:
        self.content = initial
        self.buffers: dict[str, str] = {}

    def load_buffer(self, name: str, text: str) -> None:
        self.buffers[name] = text

    def paste_buffer(self, _tmux_name: str, buffer_name: str) -> None:
        text = self.buffers.get(buffer_name, "")
        self.content += f"\n[Pasted Content {len(text)} chars]"

    def send_key(self, _tmux_name: str, key: str) -> None:
        if key == "Enter":
            self.content += "\nesc to interrupt"

    def capture(self, _tmux_name: str) -> str:
        return self.content


class _Clock:
    def __init__(self, step: float = 1.0) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def _scripted_paster(pane: _ScriptedPane) -> TerminalPaster:
    return TerminalPaster(
        load_buffer=pane.load_buffer,
        paste_buffer=pane.paste_buffer,
        send_key=pane.send_key,
        capture_pane=pane.capture,
        sleep=lambda _seconds: None,
        monotonic=_Clock(),
    )


class ScriptedTmuxE2ETests(unittest.TestCase):
    """R5: an end-to-end injection test against a scripted tmux pane."""

    def test_brief_lands_and_the_turn_starts(self) -> None:
        pane = _ScriptedPane()
        paster = _scripted_paster(pane)
        row = DeliveryRow(kind="brief", entry_id="S1", text="you are the worker", envelope=False)
        result = deliver(row, tmux_name="ar-worker-1", paster=paster, harness="codex")
        self.assertEqual(result.outcome, "acked")
        self.assertIn("[Pasted Content", pane.content)
        self.assertIn("esc to interrupt", pane.content)

    def test_quota_modal_already_on_the_pane_blocks_delivery(self) -> None:
        # The pane never lands the paste (boot deadline expires against a modal that never clears)
        # -- but the final capture shows the quota dialog, so this is blocked, not a bare failure.
        pane = _ScriptedPane(initial="codex> Approaching rate limits — switch model?")

        class _FrozenPane(_ScriptedPane):
            def paste_buffer(self, _tmux_name: str, _buffer_name: str) -> None:
                return None  # the modal swallows the paste -- nothing ever renders a chip

        frozen = _FrozenPane(initial=pane.content)
        paster = TerminalPaster(
            load_buffer=frozen.load_buffer,
            paste_buffer=frozen.paste_buffer,
            send_key=frozen.send_key,
            capture_pane=frozen.capture,
            sleep=lambda _seconds: None,
            monotonic=_Clock(step=5.0),
        )
        row = DeliveryRow(kind="brief", entry_id="S1", text="you are the worker", envelope=False)
        result = deliver(row, tmux_name="ar-worker-1", paster=paster, harness="codex")
        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.reason, "codex-quota-limit")


if __name__ == "__main__":
    unittest.main()
