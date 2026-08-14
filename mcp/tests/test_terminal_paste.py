from __future__ import annotations

import pytest
from agents_remember.serving.terminal_paste import (
    CODEX_PASTE_ENTER_SUPPRESS_SECONDS,
    DISPATCH_SUBMIT_SETTLE_SECONDS,
    AcceptanceWindow,
    DispatchPastePolicy,
    PasteRecoveryLadder,
    TerminalPaster,
    TerminalPasterSeams,
    sanitize_for_injection,
)


class _Clock:
    def __init__(self, step: float = 0.5) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


class _Tmux:
    """A tmux double that can also refuse: ``paste_results`` and ``failing_keys`` fail commands.

    Every tmux command the paster drives can fail in production (the pane went away mid-ladder,
    the server is wedged), and the paster's contract is different for each failure -- an unwritten
    buffer is not delivered, a refused Enter is delivered but unsubmitted. The double therefore has
    to be able to say no per command, not merely record the calls.
    """

    def __init__(
        self,
        *,
        capture_values: list[str] | None = None,
        paste_results: list[bool] | None = None,
        failing_keys: frozenset[str] = frozenset(),
    ) -> None:
        self.loads: list[str] = []
        self.pastes = 0
        self.keys: list[str] = []
        self.captures = 0
        self.capture_values = list(capture_values or [])
        self.paste_results = list(paste_results or [])
        self.failing_keys = failing_keys
        self.sleeps: list[float] = []

    def load(self, _name: str, text: str) -> bool:
        self.loads.append(text)
        return True

    def paste(self, _tmux: str, _name: str) -> bool:
        self.pastes += 1
        return self.paste_results.pop(0) if self.paste_results else True

    def key(self, _tmux: str, key: str) -> bool:
        self.keys.append(key)
        return key not in self.failing_keys

    def capture(self, _tmux: str) -> str:
        self.captures += 1
        if self.capture_values:
            return self.capture_values.pop(0)
        return "failure pane"

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _paster(tmux: _Tmux) -> TerminalPaster:
    return TerminalPaster(
        TerminalPasterSeams(
            load_buffer=tmux.load,
            paste_buffer=tmux.paste,
            send_key=tmux.key,
            capture_pane=tmux.capture,
            sleep=tmux.sleep,
            monotonic=_Clock(),
        )
    )


def test_sanitize_strips_control_noise_and_nested_paste_markers() -> None:
    clean = sanitize_for_injection("a\x1a\x1b[200~b\x1b[201~\r\n\tc")
    assert clean == "ab\n\tc"


def test_success_uses_log_probe_and_never_captures_pane() -> None:
    tmux = _Tmux()
    result = _paster(tmux).paste("ar-1", "brief", submit=True, accepted=lambda: True)
    assert result.submitted
    assert tmux.loads == []
    assert tmux.keys == []
    assert tmux.captures == 0


def test_acceptance_on_the_first_enter_climbs_no_rung_of_the_ladder() -> None:
    # The pane was not yet accepted when the paster was called, so the whole ladder is armed --
    # and the ordinary case is that the very first Enter is enough. Nothing beyond it may run:
    # one paste, one Enter, and no clear/replace, because every extra rung risks a duplicate.
    tmux = _Tmux()
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: tmux.keys == ["Enter"],
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.submitted
    assert result.delivered
    assert tmux.loads == ["brief"]
    assert tmux.pastes == 1
    assert tmux.keys == ["Enter"]
    # Exactly the one origin capture the ladder takes before pasting; no rung read the pane.
    assert tmux.captures == 1


def test_an_unwritable_buffer_reports_undelivered_and_never_presses_enter() -> None:
    # tmux refused the paste, so nothing reached the composer. Pressing Enter now would submit
    # whatever the composer already held, so the paster must stop with delivered=False instead.
    tmux = _Tmux(paste_results=[False])
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert not result.delivered
    assert not result.submitted
    assert result.capture == "failure pane"
    assert tmux.pastes == 1
    assert tmux.keys == []


def test_a_refused_enter_ends_the_ladder_as_delivered_but_unsubmitted() -> None:
    # The bytes are in the composer (delivered) but tmux would not send the key, so no submission
    # can be claimed and no further rung may run: a rung that re-pastes would duplicate the draft.
    tmux = _Tmux(failing_keys=frozenset({"Enter"}))
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == "failure pane"
    assert tmux.pastes == 1
    assert tmux.keys == ["Enter"]


def test_a_refused_clear_key_blocks_the_repaste_rather_than_appending() -> None:
    # Rung 3 found the prior payload still visible and could not clear it. Re-pasting on top of a
    # payload that is still there is exactly the duplicate submission the ladder exists to avoid,
    # so the attempt stops on the pane it could not clear.
    tmux = _Tmux(
        capture_values=["codex >", "[Pasted Content 5 chars]\ncodex >"],
        failing_keys=frozenset({"C-u"}),
    )
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == "failure pane"
    assert tmux.keys == ["Enter", "Enter", "C-u"]
    assert tmux.loads == ["brief"]
    assert tmux.pastes == 1


def test_dispatch_recovery_reports_failure_when_the_verified_repaste_cannot_be_written() -> None:
    # The retry pane proved the prior draft absent, so re-pasting the durable brief is the correct
    # move -- but tmux refused it. Nothing reached the composer, so this is an undelivered failure
    # and no Enter follows it.
    tmux = _Tmux(
        capture_values=["\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"],
        paste_results=[False],
    )
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: False,
        policy=DispatchPastePolicy(attempt="recovery", visible_marker="entry: E1", harness="codex"),
    )
    assert not result.delivered
    assert not result.submitted
    assert tmux.loads == ["brief"]
    assert tmux.pastes == 1
    assert tmux.keys == []


def test_first_absence_waits_full_window_before_enter_repress() -> None:
    tmux = _Tmux()

    def accepted() -> bool:
        return len(tmux.keys) >= 2

    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=accepted,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0, poll_interval=0.1)),
    )
    assert result.submitted
    assert tmux.keys == ["Enter", "Enter"]
    assert tmux.pastes == 1
    assert tmux.captures == 1


def test_repaste_happens_only_after_enter_repress_window() -> None:
    tmux = _Tmux()

    def accepted() -> bool:
        return tmux.pastes >= 2

    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=accepted,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.submitted
    assert tmux.pastes == 2
    assert tmux.keys == ["Enter", "Enter", "Enter"]
    assert tmux.captures == 2


def test_exhausted_ladder_returns_the_final_failure_capture() -> None:
    tmux = _Tmux()
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == "failure pane"
    assert tmux.captures == 3
    assert tmux.pastes == 2


def test_duplicate_chip_blocks_repaste_when_clear_does_not_remove_it() -> None:
    tmux = _Tmux(
        capture_values=[
            "codex >",
            "[Pasted Content 5 chars]\ncodex >",
            "[Pasted Content 5 chars]\ncodex >",
        ]
    )
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == "[Pasted Content 5 chars]\ncodex >"
    assert tmux.pastes == 1
    assert tmux.loads == ["brief"]
    assert tmux.keys == ["Enter", "Enter", "C-u"]
    assert "Escape" not in tmux.keys


def test_visible_composer_chip_is_cleared_before_replacement() -> None:
    tmux = _Tmux(
        capture_values=[
            "codex >",
            "[Pasted Content 5 chars]\ncodex >",
            "codex >",
        ]
    )

    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: tmux.pastes >= 2,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.submitted
    assert tmux.pastes == 2
    assert tmux.loads == ["brief", "brief"]
    assert tmux.keys == ["Enter", "Enter", "C-u", "Enter"]
    assert "Escape" not in tmux.keys


def test_unobservable_pane_blocks_repaste() -> None:
    tmux = _Tmux(capture_values=["codex >", ""])
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        ladder=PasteRecoveryLadder(window=AcceptanceWindow(flush_window=1.0)),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == ""
    assert tmux.pastes == 1
    assert tmux.keys == ["Enter", "Enter"]


def test_settle_guard_is_at_least_100ms() -> None:
    tmux = _Tmux()
    _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: True,
        ladder=PasteRecoveryLadder(settle_delay=0),
    )
    # Pre-existing acceptance skips transport. Exercise the transport-only command path instead.
    _paster(tmux).paste(
        "ar-1",
        "/effort ultracode",
        submit=True,
        accepted=None,
        ladder=PasteRecoveryLadder(settle_delay=0),
    )
    assert 0.1 in tmux.sleeps


def test_unbound_command_never_claims_log_acceptance() -> None:
    tmux = _Tmux()
    result = _paster(tmux).paste("ar-1", "/effort ultracode", submit=True, accepted=None)
    assert result.delivered
    assert not result.submitted
    assert tmux.keys == ["Enter"]
    assert tmux.captures == 0


def test_escape_is_refused() -> None:
    with pytest.raises(ValueError, match="never sends Escape"):
        _paster(_Tmux())._press("ar-1", "Escape")


def test_dispatch_settle_is_strictly_beyond_codex_suppression_window() -> None:
    assert DISPATCH_SUBMIT_SETTLE_SECONDS > CODEX_PASTE_ENTER_SUPPRESS_SECONDS


def test_initial_dispatch_uses_one_paste_and_one_enter() -> None:
    tmux = _Tmux()
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: tmux.keys == ["Enter"],
        policy=DispatchPastePolicy(attempt="initial", visible_marker="entry: E1"),
    )
    assert result.submitted
    assert tmux.loads == ["brief"]
    assert tmux.pastes == 1
    assert tmux.keys == ["Enter"]
    assert tmux.sleeps == [DISPATCH_SUBMIT_SETTLE_SECONDS]


def test_dispatch_retry_submits_visible_same_draft_without_repaste() -> None:
    tmux = _Tmux(
        capture_values=[
            "[Pasted Content 5 chars]\n"
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"
        ]
    )
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: tmux.keys == ["Enter"],
        policy=DispatchPastePolicy(attempt="recovery", visible_marker="entry: E1", harness="codex"),
    )
    assert result.submitted
    assert tmux.loads == []
    assert tmux.pastes == 0
    assert tmux.keys == ["Enter"]


def test_dispatch_retry_pastes_once_only_after_verified_absence() -> None:
    tmux = _Tmux(
        capture_values=["\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"]
    )
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: tmux.keys == ["Enter"],
        policy=DispatchPastePolicy(attempt="recovery", visible_marker="entry: E1", harness="codex"),
    )
    assert result.submitted
    assert tmux.loads == ["brief"]
    assert tmux.pastes == 1
    assert tmux.keys == ["Enter"]


def test_dispatch_retry_leaves_ambiguous_duplicate_chips_pending() -> None:
    capture = (
        "[Pasted Content 5 chars]\n[Pasted Content 5 chars]\n"
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
    )
    tmux = _Tmux(capture_values=[capture])
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: False,
        policy=DispatchPastePolicy(attempt="recovery", visible_marker="entry: E1", harness="codex"),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == capture
    assert tmux.loads == []
    assert tmux.pastes == 0
    assert tmux.keys == []


def test_dispatch_retry_leaves_unrelated_codex_draft_pending() -> None:
    capture = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} do not overwrite me"
    tmux = _Tmux(capture_values=[capture])
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: False,
        policy=DispatchPastePolicy(attempt="recovery", visible_marker="entry: E1", harness="codex"),
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == capture
    assert tmux.loads == []
    assert tmux.pastes == 0
    assert tmux.keys == []


def test_dispatch_retry_does_not_submit_historical_matching_marker() -> None:
    capture = (
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} entry: E1\n■ You've hit your usage limit."
    )
    tmux = _Tmux(capture_values=[capture])
    result = _paster(tmux).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: False,
        policy=DispatchPastePolicy(attempt="recovery", visible_marker="entry: E1", harness="codex"),
    )
    assert result.delivered
    assert not result.submitted
    assert tmux.loads == []
    assert tmux.pastes == 0
    assert tmux.keys == []


def test_early_enter_control_is_suppressed_but_dispatch_enter_submits() -> None:
    class _SuppressionTmux(_Tmux):
        def __init__(self) -> None:
            super().__init__()
            self.elapsed = 0.0
            self.submitted = False

        def key(self, _tmux: str, key: str) -> bool:
            self.keys.append(key)
            self.submitted = self.elapsed > CODEX_PASTE_ENTER_SUPPRESS_SECONDS
            return True

        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            self.elapsed += seconds

    early = _SuppressionTmux()
    early_result = _paster(early).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: early.submitted,
        ladder=PasteRecoveryLadder(
            window=AcceptanceWindow(flush_window=0), settle_delay=0.1, enter_represses=0, repastes=0
        ),
    )
    assert not early_result.submitted
    assert early.keys == ["Enter"]

    dispatch = _SuppressionTmux()
    dispatch_result = _paster(dispatch).paste_dispatch(
        "ar-1",
        "brief",
        accepted=lambda: dispatch.submitted,
        window=AcceptanceWindow(flush_window=0),
        policy=DispatchPastePolicy(attempt="initial", visible_marker="entry: E1"),
    )
    assert dispatch_result.submitted
    assert dispatch.keys == ["Enter"]
