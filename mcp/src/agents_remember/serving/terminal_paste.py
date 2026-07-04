"""Server-side echo-confirmed stdin paste into a durable tmux session (slice L2 dispatch).

The browser delivers a context packet over the live WebSocket (``data/terminal.ts``
``pasteAndConfirm`` / ``submitAndConfirm``): it wraps the text as ONE sanitized *bracketed paste*,
watches the pane's own echo to confirm the composer accepted it (a booting harness discards stdin
until its composer mounts), then -- only when submitting -- sends ``Enter`` and watches for output that
advances past the post-paste baseline. The agent-facing ``spawn_agent_session`` tool has **no live
WebSocket**: it drives a freshly-spawned, PTY-clientless durable tmux session. This module mirrors the
frontend semantics server-side over tmux primitives:

* **paste** -- ``set-buffer`` + ``paste-buffer -p`` (tmux does the ``ESC[200~ … ESC[201~`` bracketing
  itself), the robust way to inject a multi-line blob into a pane with no escape-byte fiddling.
* **echo confirmation** -- ``capture-pane`` before/after: delivery is confirmed only when the pasted
  draft text or a new ``[Pasted text #N]`` chip appears. A booting harness may repaint the pane while
  still discarding stdin, so a generic capture change is not enough.
* **submit** -- ``send-keys Enter`` then one more ``capture-pane`` advance check (workers auto-start;
  a human draft-only flow leaves ``submit=False`` so the draft stays editable, unsubmitted).

Every tmux operation is an injectable callable (the ``terminal.py`` posture) so tests drive the loop
against fakes -- no real tmux, no real sleeping.
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

# Mirror of the frontend ``sanitizeForInjection`` (data/terminal.ts): strip any embedded
# (ESC-framed) bracketed-paste markers so the injected text can't break out of tmux's own bracketing,
# then scrub the C0 control range except TAB (0x09) and NEWLINE (0x0a) -- dropping CR, ESC, the 0x1a
# suspend byte, and DEL (0x7f). tmux's ``paste-buffer -p`` re-adds the bracketing around the clean text.
_PASTE_MARKER = re.compile(r"\x1b\[20[01]~")
_CONTROL_NOISE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PASTED_TEXT_CHIP = re.compile(r"\[Pasted text(?: #[0-9]+)?\]")

# Delivery/submit confirmation cadence. Modest by default so a deliberate spawn dispatch does not block
# unbounded; overridable per call. A just-spawned harness (Claude Code loading MCP) discards stdin while
# booting, so the paste is retried across the boot window until the composer echoes it.
_ECHO_TIMEOUT = 4.0
"""Seconds to watch for the composer's echo after one paste before re-pasting."""
_BOOT_DEADLINE = 30.0
"""Total seconds to keep re-pasting across a harness boot before reporting unconfirmed delivery."""
_SUBMIT_TIMEOUT = 8.0
"""Seconds to watch for output past the post-paste baseline after ``Enter`` before reporting unsubmitted."""
_POLL_INTERVAL = 0.4
"""Delay between ``capture-pane`` confirmation polls."""
_RETRY_DELAY = 0.5
"""Breather between paste attempts across the boot deadline."""


def sanitize_for_injection(text: str) -> str:
    """Strip control noise + stray paste markers from an injected packet (mirrors the frontend)."""
    return _CONTROL_NOISE.sub("", _PASTE_MARKER.sub("", text))


@dataclass(frozen=True)
class PasteResult:
    """Outcome of an echo-confirmed paste: whether the composer echoed it and whether it submitted."""

    delivered: bool
    submitted: bool


TmuxBufferSetter = Callable[[str, str], None]
"""Load text into a named tmux buffer: ``(buffer_name, text)``."""

TmuxBufferPaster = Callable[[str, str], None]
"""Bracketed-paste + delete a named buffer into a session: ``(tmux_session_name, buffer_name)``."""

TmuxKeySender = Callable[[str, str], None]
"""Send one named key to a session: ``(tmux_session_name, key)`` (e.g. ``"Enter"``)."""

TmuxPaneCapturer = Callable[[str], str]
"""Capture the visible pane text of a session: ``(tmux_session_name) -> text``."""


def _tmux_set_buffer(buffer_name: str, text: str) -> None:
    subprocess.run(
        ["tmux", "set-buffer", "-b", buffer_name, "--", text],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_TMUX_TIMEOUT,
    )


def _tmux_paste_buffer(tmux_name: str, buffer_name: str) -> None:
    # -p = bracketed paste (tmux frames it with ESC[200~ / ESC[201~); -d = delete the buffer after.
    subprocess.run(
        ["tmux", "paste-buffer", "-t", tmux_name, "-b", buffer_name, "-p", "-d"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_TMUX_TIMEOUT,
    )


def _tmux_send_key(tmux_name: str, key: str) -> None:
    subprocess.run(
        ["tmux", "send-keys", "-t", tmux_name, key],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_TMUX_TIMEOUT,
    )


def _tmux_capture_pane(tmux_name: str) -> str:
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", tmux_name],
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


class TerminalPaster:
    """Echo-confirmed paste into a durable tmux session, mirroring the frontend paste/submit loop.

    All tmux operations are injectable (the ``terminal.py`` posture) so the confirmation loop is
    deterministically unit-testable against fakes -- no real tmux server, no real sleep.
    """

    def __init__(
        self,
        *,
        set_buffer: TmuxBufferSetter | None = None,
        paste_buffer: TmuxBufferPaster | None = None,
        send_key: TmuxKeySender | None = None,
        capture_pane: TmuxPaneCapturer | None = None,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._set_buffer = set_buffer or _tmux_set_buffer
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
        echo_timeout: float = _ECHO_TIMEOUT,
        boot_deadline: float = _BOOT_DEADLINE,
        submit_timeout: float = _SUBMIT_TIMEOUT,
        poll_interval: float = _POLL_INTERVAL,
    ) -> PasteResult:
        """Paste ``text`` (echo-confirmed) and optionally submit it.

        Re-pastes across the boot window until the composer echoes the draft (``delivered``); only then,
        when ``submit``, sends ``Enter`` and watches for output past the post-paste baseline
        (``submitted``). Never raises on a missing/gone session -- an unchanged pane simply reports the
        unconfirmed outcome, the same "surface a retry, never silently drop" contract as the frontend.
        """
        sanitized = sanitize_for_injection(text)
        buffer_name = f"ar-spawn-{os.getpid()}-{uuid4().hex[:8]}"
        delivered = self._paste_until_echo(
            tmux_name,
            sanitized,
            buffer_name,
            echo_timeout=echo_timeout,
            boot_deadline=boot_deadline,
            poll_interval=poll_interval,
        )
        if not (delivered and submit):
            return PasteResult(delivered=delivered, submitted=False)
        baseline = self._capture_pane(tmux_name)
        self._send_key(tmux_name, "Enter")
        submitted = self._await_advance(
            tmux_name, baseline, timeout=submit_timeout, poll_interval=poll_interval
        )
        return PasteResult(delivered=True, submitted=submitted)

    def _paste_until_echo(
        self,
        tmux_name: str,
        sanitized: str,
        buffer_name: str,
        *,
        echo_timeout: float,
        boot_deadline: float,
        poll_interval: float,
    ) -> bool:
        started = self._monotonic()
        first = True
        while first or self._monotonic() - started < boot_deadline:
            first = False
            baseline = self._capture_pane(tmux_name)
            self._set_buffer(buffer_name, sanitized)
            self._paste_buffer(tmux_name, buffer_name)
            if self._await_echo(
                tmux_name,
                baseline,
                sanitized,
                timeout=echo_timeout,
                poll_interval=poll_interval,
            ):
                return True
            with contextlib.suppress(OSError):
                self._sleep(_RETRY_DELAY)
        return False

    def _await_echo(
        self,
        tmux_name: str,
        baseline: str,
        sanitized: str,
        *,
        timeout: float,
        poll_interval: float,
    ) -> bool:
        """Poll until the pane contains this paste's visible draft or pasted-text chip."""
        started = self._monotonic()
        while True:
            current = self._capture_pane(tmux_name)
            if _echo_observed(baseline, current, sanitized):
                return True
            if self._monotonic() - started >= timeout:
                return False
            with contextlib.suppress(OSError):
                self._sleep(poll_interval)

    def _await_advance(
        self, tmux_name: str, baseline: str, *, timeout: float, poll_interval: float
    ) -> bool:
        """Poll ``capture-pane`` until the visible pane differs from ``baseline`` or ``timeout``."""
        started = self._monotonic()
        while True:
            if self._capture_pane(tmux_name) != baseline:
                return True
            if self._monotonic() - started >= timeout:
                return False
            with contextlib.suppress(OSError):
                self._sleep(poll_interval)


def _echo_observed(baseline: str, current: str, sanitized: str) -> bool:
    """True when ``current`` shows evidence of the paste itself, not just new boot output."""
    if current == baseline:
        return False
    if len(_PASTED_TEXT_CHIP.findall(current)) > len(_PASTED_TEXT_CHIP.findall(baseline)):
        return True
    fragment = _echo_fragment(sanitized)
    return bool(fragment and current.count(fragment) > baseline.count(fragment))


def _echo_fragment(sanitized: str) -> str:
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0][:120]
