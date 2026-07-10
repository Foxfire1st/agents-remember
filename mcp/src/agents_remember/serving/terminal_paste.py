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
from uuid import uuid4

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


def sanitize_for_injection(text: str) -> str:
    """Strip control noise and embedded bracketed-paste markers before tmux frames the input."""
    return _CONTROL_NOISE.sub("", _PASTE_MARKER.sub("", text))


@dataclass(frozen=True)
class PasteResult:
    """Transport plus harness-log acceptance for one input."""

    delivered: bool
    submitted: bool
    capture: str = ""


TmuxBufferLoader = Callable[[str, str], bool | None]
TmuxBufferPaster = Callable[[str, str], bool | None]
TmuxKeySender = Callable[[str, str], bool | None]
TmuxPaneCapturer = Callable[[str], str]
AcceptanceProbe = Callable[[], bool]


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

    def __init__(
        self,
        *,
        load_buffer: TmuxBufferLoader | None = None,
        paste_buffer: TmuxBufferPaster | None = None,
        send_key: TmuxKeySender | None = None,
        capture_pane: TmuxPaneCapturer | None = None,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._load_buffer = load_buffer or _tmux_load_buffer
        self._paste_buffer = paste_buffer or _tmux_paste_buffer
        self._send_key = send_key or _tmux_send_key
        self._capture_pane = capture_pane or _tmux_capture_pane
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def paste(
        self,
        tmux_name: str,
        text: str,
        *,
        submit: bool = False,
        accepted: AcceptanceProbe | None = None,
        flush_window: float = 30.0,
        poll_interval: float = _POLL_INTERVAL,
        settle_delay: float = 0.1,
        enter_represses: int = 1,
        repastes: int = 1,
        clear_key: str = "C-u",
    ) -> PasteResult:
        """Paste ``text`` and require ``accepted`` after Enter.

        ``accepted=None`` is allowed only for draft transport or a spawn command whose evidence is
        intentionally checked retroactively after the id-bearing brief binds the session log. It
        never produces ``submitted=True``.
        """
        sanitized = sanitize_for_injection(text)
        if submit and accepted is not None and accepted():
            return PasteResult(delivered=True, submitted=True)
        origin = self._capture_pane(tmux_name) if submit and accepted is not None else ""
        if not self._paste_text(tmux_name, sanitized):
            return self._failure(tmux_name)
        if not submit:
            return PasteResult(delivered=True, submitted=False)
        self._settle(settle_delay)
        if not self._press(tmux_name, "Enter"):
            return self._failure(tmux_name, delivered=True)
        if accepted is None:
            return PasteResult(delivered=True, submitted=False)
        if self._await_acceptance(accepted, flush_window, poll_interval):
            return PasteResult(delivered=True, submitted=True)

        for _attempt in range(max(0, enter_represses)):
            if not self._press(tmux_name, "Enter"):
                return self._failure(tmux_name, delivered=True)
            if self._await_acceptance(accepted, flush_window, poll_interval):
                return PasteResult(delivered=True, submitted=True)

        for _attempt in range(max(0, repastes)):
            current = self._capture_pane(tmux_name)
            presence = _payload_presence(origin, current, sanitized)
            if presence is None:
                return self._failure(tmux_name, delivered=True, capture=current)
            if presence:
                if not self._press(tmux_name, clear_key):
                    return self._failure(tmux_name, delivered=True)
                self._settle(settle_delay)
                cleared = self._capture_pane(tmux_name)
                if _payload_presence(origin, cleared, sanitized) is not False:
                    # A transcript chip or an uncleared composer is still evidence that the prior
                    # payload may exist. Failing here is safer than appending a duplicate.
                    return self._failure(tmux_name, delivered=True, capture=cleared)
            if not self._paste_text(tmux_name, sanitized):
                return self._failure(tmux_name, delivered=True)
            self._settle(settle_delay)
            if not self._press(tmux_name, "Enter"):
                return self._failure(tmux_name, delivered=True)
            if self._await_acceptance(accepted, flush_window, poll_interval):
                return PasteResult(delivered=True, submitted=True)
        return self._failure(tmux_name, delivered=True)

    def _paste_text(self, tmux_name: str, text: str) -> bool:
        buffer_name = f"ar-spawn-{os.getpid()}-{uuid4().hex[:8]}"
        return self._load_buffer(buffer_name, text) is not False and self._paste_buffer(
            tmux_name, buffer_name
        ) is not False

    def _press(self, tmux_name: str, key: str) -> bool:
        if key == "Escape":
            raise ValueError("terminal delivery never sends Escape; it interrupts the target session")
        return self._send_key(tmux_name, key) is not False

    def _await_acceptance(
        self,
        accepted: AcceptanceProbe,
        flush_window: float,
        poll_interval: float,
    ) -> bool:
        started = self._monotonic()
        while True:
            if accepted():
                return True
            remaining = max(0.0, flush_window) - (self._monotonic() - started)
            if remaining <= 0.0:
                return False
            with contextlib.suppress(OSError):
                # Never oversleep the calibrated window merely because the final poll interval is
                # longer than the remaining evidence window.
                self._sleep(min(max(0.01, poll_interval), remaining))

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


def _payload_head(sanitized: str) -> str:
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    return lines[0][:120] if lines else ""
