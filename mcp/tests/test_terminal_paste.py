from __future__ import annotations

import pytest
from agents_remember.serving.terminal_paste import (
    CODEX_PASTE_ENTER_SUPPRESS_SECONDS,
    DISPATCH_SUBMIT_SETTLE_SECONDS,
    DispatchPastePolicy,
    TerminalPaster,
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
    def __init__(self, *, capture_values: list[str] | None = None) -> None:
        self.loads: list[str] = []
        self.pastes = 0
        self.keys: list[str] = []
        self.captures = 0
        self.capture_values = list(capture_values or [])
        self.sleeps: list[float] = []

    def load(self, _name: str, text: str) -> bool:
        self.loads.append(text)
        return True

    def paste(self, _tmux: str, _name: str) -> bool:
        self.pastes += 1
        return True

    def key(self, _tmux: str, key: str) -> bool:
        self.keys.append(key)
        return True

    def capture(self, _tmux: str) -> str:
        self.captures += 1
        if self.capture_values:
            return self.capture_values.pop(0)
        return "failure pane"

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _paster(tmux: _Tmux) -> TerminalPaster:
    return TerminalPaster(
        load_buffer=tmux.load,
        paste_buffer=tmux.paste,
        send_key=tmux.key,
        capture_pane=tmux.capture,
        sleep=tmux.sleep,
        monotonic=_Clock(),
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


def test_first_absence_waits_full_window_before_enter_repress() -> None:
    tmux = _Tmux()

    def accepted() -> bool:
        return len(tmux.keys) >= 2

    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=accepted,
        flush_window=1.0,
        poll_interval=0.1,
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
        flush_window=1.0,
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
        flush_window=1.0,
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
        flush_window=1.0,
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
        flush_window=1.0,
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
        flush_window=1.0,
    )
    assert result.delivered
    assert not result.submitted
    assert result.capture == ""
    assert tmux.pastes == 1
    assert tmux.keys == ["Enter", "Enter"]


def test_settle_guard_is_at_least_100ms() -> None:
    tmux = _Tmux()
    _paster(tmux).paste("ar-1", "brief", submit=True, accepted=lambda: True, settle_delay=0)
    # Pre-existing acceptance skips transport. Exercise the transport-only command path instead.
    _paster(tmux).paste("ar-1", "/effort ultracode", submit=True, accepted=None, settle_delay=0)
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
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: tmux.keys == ["Enter"],
        dispatch_policy=DispatchPastePolicy(attempt="initial", visible_marker="entry: E1"),
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
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: tmux.keys == ["Enter"],
        dispatch_policy=DispatchPastePolicy(
            attempt="recovery", visible_marker="entry: E1", harness="codex"
        ),
    )
    assert result.submitted
    assert tmux.loads == []
    assert tmux.pastes == 0
    assert tmux.keys == ["Enter"]


def test_dispatch_retry_pastes_once_only_after_verified_absence() -> None:
    tmux = _Tmux(
        capture_values=["\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"]
    )
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: tmux.keys == ["Enter"],
        dispatch_policy=DispatchPastePolicy(
            attempt="recovery", visible_marker="entry: E1", harness="codex"
        ),
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
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        dispatch_policy=DispatchPastePolicy(
            attempt="recovery", visible_marker="entry: E1", harness="codex"
        ),
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
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        dispatch_policy=DispatchPastePolicy(
            attempt="recovery", visible_marker="entry: E1", harness="codex"
        ),
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
    result = _paster(tmux).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: False,
        dispatch_policy=DispatchPastePolicy(
            attempt="recovery", visible_marker="entry: E1", harness="codex"
        ),
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
        flush_window=0,
        settle_delay=0.1,
        enter_represses=0,
        repastes=0,
    )
    assert not early_result.submitted
    assert early.keys == ["Enter"]

    dispatch = _SuppressionTmux()
    dispatch_result = _paster(dispatch).paste(
        "ar-1",
        "brief",
        submit=True,
        accepted=lambda: dispatch.submitted,
        flush_window=0,
        dispatch_policy=DispatchPastePolicy(attempt="initial", visible_marker="entry: E1"),
    )
    assert dispatch_result.submitted
    assert dispatch.keys == ["Enter"]
