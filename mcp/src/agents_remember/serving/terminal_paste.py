"""Bracketed tmux paste with harness-log acceptance.

The server controls the input bytes and Enter key; the harness owns the durable acceptance record.
Submitted inputs are therefore accepted only when a caller-supplied harness-log probe finds them.
Pane text never grants acceptance; immediately before a re-paste it is used only to prove the prior
payload absent, or to clear and replace visible composer content instead of appending a duplicate.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from agents_remember.serving.turn_state import (
    boot_ready,
    codex_empty_composer_visible,
    codex_footer_line,
)

_TMUX_TIMEOUT = 5.0
"""Per-tmux-subprocess timeout (seconds)."""

_POLL_INTERVAL = 0.1
"""Real-seat calibration floor: the fastest Codex id record was visible after 86 ms."""

_PASTE_MARKER = re.compile(r"\x1b\[20[01]~")
_CONTROL_NOISE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PASTE_CHIP = re.compile(
    r"\[Pasted text(?: #[0-9]+)?(?: \+[0-9]+ lines)?\]|"
    r"\[Pasted [Cc]ontent [0-9]+ chars\]"
)

_CAPTURE_HISTORY_LINES = 200
"""Bounded pane tail for failure evidence and pre-re-paste absence checks."""

CODEX_PASTE_ENTER_SUPPRESS_SECONDS = 0.120
DISPATCH_SUBMIT_SETTLE_SECONDS = 0.150
DispatchPasteAttempt = Literal["initial", "recovery"]


def sanitize_for_injection(text: str) -> str:
    """Strip control noise and embedded bracketed-paste markers before tmux frames the input."""
    return _CONTROL_NOISE.sub("", _PASTE_MARKER.sub("", text))


@dataclass(frozen=True)
class PasteResult:
    """Transport plus harness-log acceptance for one input."""

    delivered: bool
    submitted: bool
    capture: str = ""


@dataclass(frozen=True)
class DispatchPastePolicy:
    """Exact-once input policy for one durable dispatch-brief row."""

    attempt: DispatchPasteAttempt
    visible_marker: str
    harness: str | None = None
    settle_delay: float = DISPATCH_SUBMIT_SETTLE_SECONDS


@dataclass(frozen=True)
class AcceptanceWindow:
    """How long one Enter is given to show up in the harness log, and how often that is checked.

    The two are one calibration: the poll interval is meaningless without the window it divides,
    and shortening the window without tightening the interval changes how many probes a submission
    actually gets. Every rung of the recovery ladder waits exactly one of these.
    """

    flush_window: float = 30.0
    poll_interval: float = _POLL_INTERVAL


@dataclass(frozen=True)
class PasteRecoveryLadder:
    """The fixed, bounded recovery contract for one verified paste.

    Three rungs in order, each ending the moment the harness log confirms acceptance: the initial
    paste+Enter, then :attr:`enter_represses` bare Enter re-presses for a composer that held the
    text unsubmitted, then :attr:`repastes` clear/replace re-pastes driven by :attr:`clear_key`.
    ``settle_delay`` is the pause between putting bytes in the composer and pressing Enter.

    The bounds ARE the contract -- "fixed and bounded recovery" is a single safety property, and
    raising one bound without the others silently changes how many duplicate submissions the ladder
    can produce. They travel as one value so the whole contract is chosen at one call site.
    """

    window: AcceptanceWindow = AcceptanceWindow()
    settle_delay: float = 0.1
    enter_represses: int = 1
    repastes: int = 1
    clear_key: str = "C-u"


DEFAULT_ACCEPTANCE_WINDOW = AcceptanceWindow()
DEFAULT_PASTE_LADDER = PasteRecoveryLadder()


TmuxBufferLoader = Callable[[str, str], bool | None]
TmuxBufferPaster = Callable[[str, str], bool | None]
TmuxKeySender = Callable[[str, str], bool | None]
TmuxPaneCapturer = Callable[[str], str]
AcceptanceProbe = Callable[[], bool]


@dataclass(frozen=True)
class TerminalPasterSeams:
    """The impure surface a :class:`TerminalPaster` drives: the tmux commands and the clock.

    One object for the same reason :class:`~agents_remember.serving.terminal.TerminalHostSeams` is:
    a test that fakes tmux replaces the whole boundary at once, and time is part of that boundary
    because the recovery ladder is defined in seconds. ``None`` keeps the real implementation.
    """

    load_buffer: TmuxBufferLoader | None = None
    paste_buffer: TmuxBufferPaster | None = None
    send_key: TmuxKeySender | None = None
    capture_pane: TmuxPaneCapturer | None = None
    sleep: Callable[[float], None] | None = None
    monotonic: Callable[[], float] | None = None


def _tmux_load_buffer(buffer_name: str, text: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "load-buffer", "-b", buffer_name, "-"],
            check=False,
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TMUX_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        # A missing/timed-out tmux is a bounded transport failure. The caller attaches the one
        # failure-only pane capture instead of leaking an exception from the delivery seam.
        return False
    return result.returncode == 0


def _tmux_paste_buffer(tmux_name: str, buffer_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "paste-buffer", "-t", tmux_name, "-b", buffer_name, "-p", "-d"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TMUX_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _tmux_send_key(tmux_name: str, key: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, key],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TMUX_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _capture_pane_argv(tmux_name: str) -> list[str]:
    return ["tmux", "capture-pane", "-p", "-S", f"-{_CAPTURE_HISTORY_LINES}", "-t", tmux_name]


def _tmux_capture_pane(tmux_name: str) -> str:
    try:
        result = subprocess.run(
            _capture_pane_argv(tmux_name),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_TMUX_TIMEOUT,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


def capture_pane(tmux_name: str) -> str:
    """Public pane capture used by liveness and bounded dispatch retry/failure evidence."""
    return _tmux_capture_pane(tmux_name)


class TerminalPaster:
    """Paste one input, then accept it only through a harness-log probe.

    Recovery is fixed and bounded: initial paste+Enter, one Enter re-press, then at most one
    clear/replace + Enter. Each log absence must persist for the calibrated window before recovery
    advances, and a re-paste additionally requires pane-verified absence. Escape is refused.
    """

    def __init__(self, seams: TerminalPasterSeams | None = None) -> None:
        resolved = seams or TerminalPasterSeams()
        self._load_buffer = resolved.load_buffer or _tmux_load_buffer
        self._paste_buffer = resolved.paste_buffer or _tmux_paste_buffer
        self._send_key = resolved.send_key or _tmux_send_key
        self._capture_pane = resolved.capture_pane or _tmux_capture_pane
        self._sleep = resolved.sleep or time.sleep
        self._monotonic = resolved.monotonic or time.monotonic

    def paste(
        self,
        tmux_name: str,
        text: str,
        *,
        submit: bool = False,
        accepted: AcceptanceProbe | None = None,
        ladder: PasteRecoveryLadder = DEFAULT_PASTE_LADDER,
    ) -> PasteResult:
        """Paste ``text`` and require ``accepted`` after Enter, climbing ``ladder`` if it is absent.

        ``accepted=None`` is allowed only for draft transport or a spawn command whose evidence is
        intentionally checked retroactively after the id-bearing brief binds the session log. It
        never produces ``submitted=True``. A durable dispatch brief uses :meth:`paste_dispatch`
        instead: it is exact-once and must never climb a recovery ladder.
        """
        sanitized = sanitize_for_injection(text)
        if not submit or accepted is None:
            return self._paste_unverified(
                tmux_name, sanitized, submit=submit, settle_delay=ladder.settle_delay
            )
        return self._paste_verified(tmux_name, sanitized, accepted=accepted, ladder=ladder)

    def paste_dispatch(
        self,
        tmux_name: str,
        text: str,
        *,
        accepted: AcceptanceProbe,
        policy: DispatchPastePolicy,
        window: AcceptanceWindow = DEFAULT_ACCEPTANCE_WINDOW,
    ) -> PasteResult:
        """Submit one durable brief exact-once: no Enter re-presses, no duplicate re-pastes.

        The harness-log probe is mandatory here -- a durable brief that cannot be proven accepted
        must fail rather than be retried into a duplicate.
        """
        return self._paste_dispatch(
            tmux_name, text, accepted=accepted, window=window, policy=policy
        )

    def _paste_unverified(
        self,
        tmux_name: str,
        sanitized: str,
        *,
        submit: bool,
        settle_delay: float,
    ) -> PasteResult:
        """Move bytes only: paste, optionally press Enter, and never claim acceptance.

        This is the draft-transport path and the spawn command whose evidence is checked
        retroactively. With no harness-log probe there is nothing that could grant
        ``submitted=True``, so the recovery ladder has nothing to recover towards and does not run.
        """

        if not self._paste_text(tmux_name, sanitized):
            return self._failure(tmux_name)
        if not submit:
            return PasteResult(delivered=True, submitted=False)
        self._settle(settle_delay)
        if not self._press(tmux_name, "Enter"):
            return self._failure(tmux_name, delivered=True)
        return PasteResult(delivered=True, submitted=False)

    def _paste_verified(
        self,
        tmux_name: str,
        sanitized: str,
        *,
        accepted: AcceptanceProbe,
        ladder: PasteRecoveryLadder,
    ) -> PasteResult:
        """Submit and accept only what the harness log confirms, climbing the recovery ladder.

        Three fixed rungs, each ending the moment the probe confirms acceptance: the initial
        paste+Enter, then bounded Enter re-presses for a composer that held the text unsubmitted,
        then bounded clear/replace re-pastes. Running out of rungs is a failure, not a submission.
        """

        if accepted():
            return PasteResult(delivered=True, submitted=True)
        origin = self._capture_pane(tmux_name)
        if not self._paste_text(tmux_name, sanitized):
            return self._failure(tmux_name)
        self._settle(ladder.settle_delay)
        settled = self._press_enter_and_await(tmux_name, accepted=accepted, window=ladder.window)
        if settled is None:
            settled = self._retry_enter_represses(tmux_name, accepted=accepted, ladder=ladder)
        if settled is None:
            settled = self._retry_repastes(
                tmux_name, sanitized, origin=origin, accepted=accepted, ladder=ladder
            )
        return settled if settled is not None else self._failure(tmux_name, delivered=True)

    def _retry_enter_represses(
        self,
        tmux_name: str,
        *,
        accepted: AcceptanceProbe,
        ladder: PasteRecoveryLadder,
    ) -> PasteResult | None:
        """Ladder rung 2: re-press Enter without re-pasting. ``None`` means still unaccepted."""

        for _attempt in range(max(0, ladder.enter_represses)):
            settled = self._press_enter_and_await(
                tmux_name, accepted=accepted, window=ladder.window
            )
            if settled is not None:
                return settled
        return None

    def _retry_repastes(
        self,
        tmux_name: str,
        sanitized: str,
        *,
        origin: str,
        accepted: AcceptanceProbe,
        ladder: PasteRecoveryLadder,
    ) -> PasteResult | None:
        """Ladder rung 3: clear any visible prior payload, re-paste, submit. ``None`` if unaccepted.

        The pane is read here only to prove the prior payload absent or to clear it; that evidence
        never grants acceptance.
        """

        for _attempt in range(max(0, ladder.repastes)):
            blocked = self._clear_prior_payload(
                tmux_name,
                sanitized,
                origin=origin,
                clear_key=ladder.clear_key,
                settle_delay=ladder.settle_delay,
            )
            if blocked is not None:
                return blocked
            if not self._paste_text(tmux_name, sanitized):
                return self._failure(tmux_name, delivered=True)
            self._settle(ladder.settle_delay)
            settled = self._press_enter_and_await(
                tmux_name, accepted=accepted, window=ladder.window
            )
            if settled is not None:
                return settled
        return None

    def _clear_prior_payload(
        self,
        tmux_name: str,
        sanitized: str,
        *,
        origin: str,
        clear_key: str,
        settle_delay: float,
    ) -> PasteResult | None:
        """Leave the composer verifiably free of the prior payload, or return the failure that stops.

        ``None`` means a re-paste may proceed: either nothing of the payload was visible, or the
        clear key removed it and a second capture confirmed the removal.
        """

        current = self._capture_pane(tmux_name)
        presence = _payload_presence(origin, current, sanitized)
        if presence is None:
            return self._failure(tmux_name, delivered=True, capture=current)
        if not presence:
            return None
        if not self._press(tmux_name, clear_key):
            return self._failure(tmux_name, delivered=True)
        self._settle(settle_delay)
        cleared = self._capture_pane(tmux_name)
        if _payload_presence(origin, cleared, sanitized) is not False:
            # A transcript chip or an uncleared composer is still evidence that the prior
            # payload may exist. Failing here is safer than appending a duplicate.
            return self._failure(tmux_name, delivered=True, capture=cleared)
        return None

    def _press_enter_and_await(
        self,
        tmux_name: str,
        *,
        accepted: AcceptanceProbe,
        window: AcceptanceWindow,
    ) -> PasteResult | None:
        """Press Enter and wait out one acceptance window.

        Returns a finished result when the log accepted the input or the key could not be sent,
        and ``None`` when the window closed unaccepted -- the caller's cue to advance a rung.
        """

        if not self._press(tmux_name, "Enter"):
            return self._failure(tmux_name, delivered=True)
        if self._await_acceptance(accepted, window):
            return PasteResult(delivered=True, submitted=True)
        return None

    def _paste_dispatch(
        self,
        tmux_name: str,
        text: str,
        *,
        accepted: AcceptanceProbe,
        window: AcceptanceWindow,
        policy: DispatchPastePolicy,
    ) -> PasteResult:
        """Submit one durable brief without Enter re-presses or duplicate re-pastes."""

        if accepted():
            return PasteResult(delivered=True, submitted=True)
        sanitized = sanitize_for_injection(text)
        blocked = self._compose_dispatch(tmux_name, sanitized, policy=policy)
        if blocked is not None:
            return blocked
        self._settle(policy.settle_delay)
        settled = self._press_enter_and_await(tmux_name, accepted=accepted, window=window)
        return settled if settled is not None else self._failure(tmux_name, delivered=True)

    def _compose_dispatch(
        self,
        tmux_name: str,
        sanitized: str,
        *,
        policy: DispatchPastePolicy,
    ) -> PasteResult | None:
        """Put the brief in the composer for this attempt, or return the failure that stops it.

        A recovery attempt must not append a second copy of a durable brief, so it re-pastes only
        when the pane proves the prior draft absent; an unreadable or ambiguous pane fails instead.
        """

        if policy.attempt != "recovery":
            return None if self._paste_text(tmux_name, sanitized) else self._failure(tmux_name)
        capture = self._capture_pane(tmux_name)
        presence = _dispatch_payload_presence(
            capture,
            sanitized,
            visible_marker=policy.visible_marker,
            harness=policy.harness,
        )
        if presence is None:
            return self._failure(tmux_name, delivered=True, capture=capture)
        if not presence and not self._paste_text(tmux_name, sanitized):
            return self._failure(tmux_name)
        return None

    def _paste_text(self, tmux_name: str, text: str) -> bool:
        buffer_name = f"ar-spawn-{os.getpid()}-{uuid4().hex[:8]}"
        return (
            self._load_buffer(buffer_name, text) is not False
            and self._paste_buffer(tmux_name, buffer_name) is not False
        )

    def _press(self, tmux_name: str, key: str) -> bool:
        if key == "Escape":
            raise ValueError(
                "terminal delivery never sends Escape; it interrupts the target session"
            )
        return self._send_key(tmux_name, key) is not False

    def _await_acceptance(self, accepted: AcceptanceProbe, window: AcceptanceWindow) -> bool:
        started = self._monotonic()
        while True:
            if accepted():
                return True
            remaining = max(0.0, window.flush_window) - (self._monotonic() - started)
            if remaining <= 0.0:
                return False
            with contextlib.suppress(OSError):
                # Never oversleep the calibrated window merely because the final poll interval is
                # longer than the remaining evidence window.
                self._sleep(min(max(0.01, window.poll_interval), remaining))

    def _settle(self, delay: float) -> None:
        with contextlib.suppress(OSError):
            self._sleep(max(0.1, delay))

    def _failure(
        self,
        tmux_name: str,
        *,
        delivered: bool = False,
        capture: str | None = None,
    ) -> PasteResult:
        return PasteResult(
            delivered=delivered,
            submitted=False,
            capture=self._capture_pane(tmux_name) if capture is None else capture,
        )


def _payload_presence(origin: str, current: str, sanitized: str) -> bool | None:
    """Whether the prior payload is visibly present before a re-paste.

    ``True`` blocks appending and triggers one clear/replace attempt. ``False`` is verified absence.
    ``None`` means the pane could not be observed, which also blocks re-paste. This evidence never
    grants delivery acceptance; only the harness-log callback can do that.
    """
    if not current.strip():
        return None
    expected_codex_chip = f"[Pasted Content {len(sanitized)} chars]"
    if current.count(expected_codex_chip) > origin.count(expected_codex_chip):
        return True
    if len(_PASTE_CHIP.findall(current)) > len(_PASTE_CHIP.findall(origin)):
        return True
    head = _payload_head(sanitized)
    return bool(head and current.count(head) > origin.count(head))


def _dispatch_payload_presence(
    current: str,
    sanitized: str,
    *,
    visible_marker: str,
    harness: str | None,
) -> bool | None:
    """Classify a retry pane as same draft, verified absence, or ambiguous."""

    if not current.strip():
        return None
    marker_count = current.count(visible_marker) if visible_marker else 0
    marker = _unique_draft_evidence(
        marker_count,
        _marker_is_live_draft(current, visible_marker, harness=harness),
    )
    if marker is not False:
        return marker
    expected_codex_chip = f"[Pasted Content {len(sanitized)} chars]"
    chip_count = current.count(expected_codex_chip)
    chip = _unique_draft_evidence(
        chip_count,
        _chip_is_live_draft(current, expected_codex_chip, harness=harness),
    )
    if chip is not False:
        return chip
    if _PASTE_CHIP.search(current):
        return None
    return False if boot_ready(current, harness=harness) else None


def _unique_draft_evidence(count: int, is_live_draft: bool) -> bool | None:
    """True for one live draft, False for no candidate, None for ambiguous evidence."""

    if count == 0:
        return False
    if count == 1 and is_live_draft:
        return True
    return None


def _marker_is_live_draft(current: str, marker: str, *, harness: str | None) -> bool:
    lines = [line for line in current.splitlines() if line.strip()]
    if not lines:
        return False
    if marker in lines[-1]:
        return True
    return bool(
        harness == "codex"
        and len(lines) >= 2
        and marker in lines[-2]
        and codex_footer_line(lines[-1])
    )


def _chip_is_live_draft(current: str, chip: str, *, harness: str | None) -> bool:
    lines = [line for line in current.splitlines() if line.strip()]
    if not lines:
        return False
    if chip in lines[-1]:
        return True
    return bool(
        harness == "codex"
        and len(lines) >= 2
        and chip in lines[-2]
        and codex_empty_composer_visible("\n".join(lines[-2:]))
    )


def _payload_head(sanitized: str) -> str:
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    return lines[0][:120] if lines else ""
