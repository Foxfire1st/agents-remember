"""Server-side capture-verified stdin paste into a durable tmux session (slice L2 dispatch).

The browser delivers a context packet over the live WebSocket (``data/terminal.ts``
``pasteAndConfirm`` / ``submitAndConfirm``): it wraps the text as ONE sanitized *bracketed paste*,
watches the pane's own echo to confirm the composer accepted it (a booting harness discards stdin
until its composer mounts), then -- only when submitting -- sends ``Enter`` and watches for output that
advances past the post-paste baseline. The agent-facing ``spawn_agent_session`` tool has **no live
WebSocket**: it drives a freshly-spawned, PTY-clientless durable tmux session. This module mirrors the
frontend semantics server-side over tmux primitives, hardened by 260707-HFX-L3 (the SF-1 blind seat +
the F-V 7x duplicate-paste stack, both forensically recorded in the 260707 sprint):

* **paste** -- ``load-buffer`` (payload rides stdin, never argv, so size is unbounded by ARG_MAX) +
  ``paste-buffer -p`` (tmux does the ``ESC[200~ … ESC[201~`` bracketing itself) -- the run's proven
  large-payload mechanic; raw ``send-keys`` never carries a payload.
* **capture verification** -- ``capture-pane -p -S -<history>`` (the viewport PLUS a bounded
  scrollback window; review N1: chips visible at origin that scroll out of the viewport before the
  new chip renders would make growth undetectable, and the truncated view would re-paste a landed
  paste) against the PRE-DELIVERY origin snapshot: delivery is confirmed only when THIS payload's
  expected codex chip (``[Pasted Content <len> chars]`` -- the chip embeds the payload size, so the
  probe is payload-specific), a NEW paste chip of either vocabulary (Claude Code
  ``[Pasted text #N]``, codex ``[Pasted Content N chars]``), or the payload's own head appears. A
  booting harness may repaint the pane while still discarding stdin, so a generic capture change is
  not enough.
* **idempotent retry** -- before ANY re-paste the pane is re-captured and checked against the same
  origin snapshot: a paste that landed after its echo window is reported delivered, never re-sent.
  Duplicate stacking is impossible by construction (F-V happened because the codex chip vocabulary
  was unknown here AND every attempt re-baselined, blinding the seam to its own landed chips).
* **loud failure** -- an unverifiable delivery reports ``delivered=False`` WITH the final pane
  capture attached (``PasteResult.capture``), so a blind seat (SF-1: ``contextDelivered:true`` over
  a clean-booted codex pane) is diagnosable from the result itself, never trusted from a boolean.
* **submit** -- ``send-keys Enter`` then one more ``capture-pane`` advance check (workers auto-start;
  a human draft-only flow leaves ``submit=False`` so the draft stays editable, unsubmitted). Enter is
  the ONLY key this seam ever sends: Escape is refused by construction -- it interrupts a codex
  session (run discipline, dispatch-pack PASTE DISCIPLINE).

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

# The paste-chip vocabulary, one alternative per harness TUI: Claude Code renders a large paste as
# ``[Pasted text #N]``; codex renders ``[Pasted Content N chars]``. 260707-HFX-L3: the codex form was
# unrecognized here, so the seam misread landed pastes as unconfirmed and blind-retried -- the F-V
# forensic run stacked 7 duplicate chips in one composer.
_PASTE_CHIP = re.compile(r"\[Pasted text(?: #[0-9]+)?\]|\[Pasted [Cc]ontent [0-9]+ chars\]")

# Delivery/submit confirmation cadence. Modest by default so a deliberate spawn dispatch does not block
# unbounded; overridable per call. A just-spawned harness (Claude Code loading MCP) discards stdin while
# booting, so the paste is retried across the boot window -- but only after a re-capture proves the
# previous attempt did NOT land (the idempotence guard above).
_ECHO_TIMEOUT = 4.0
"""Seconds of settle re-captures after one paste before the retry path may consider re-pasting."""
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


def count_paste_chips(capture: str) -> int:
    """Count rendered paste chips in a pane capture, across both harness chip vocabularies.

    The delivery-evidence probe: a chip count that grew past the pre-delivery origin proves the
    composer accepted a paste (260707-HFX-L3 -- the codex ``[Pasted Content N chars]`` form counts).
    """
    return len(_PASTE_CHIP.findall(capture))


@dataclass(frozen=True)
class PasteResult:
    """Outcome of a capture-verified paste: delivery, submission, and the final pane capture.

    ``capture`` is the LAST pane snapshot taken. On ``delivered=False`` it is the loud-failure
    evidence the caller must surface (260707-HFX-L3: never a bare false-success boolean again).
    """

    delivered: bool
    submitted: bool
    capture: str = ""


TmuxBufferLoader = Callable[[str, str], None]
"""Load text into a named tmux buffer: ``(buffer_name, text)``."""

TmuxBufferPaster = Callable[[str, str], None]
"""Bracketed-paste + delete a named buffer into a session: ``(tmux_session_name, buffer_name)``."""

TmuxKeySender = Callable[[str, str], None]
"""Send one named key to a session: ``(tmux_session_name, key)`` (e.g. ``"Enter"``)."""

TmuxPaneCapturer = Callable[[str], str]
"""Capture the visible pane text of a session: ``(tmux_session_name) -> text``."""


def _tmux_load_buffer(buffer_name: str, text: str) -> None:
    # ``load-buffer -`` reads the payload from stdin: nothing rides argv, so a large context packet
    # can never hit ARG_MAX or shell-quoting seams (the 260707 run's manual mechanic, server-encoded).
    subprocess.run(
        ["tmux", "load-buffer", "-b", buffer_name, "-"],
        check=False,
        input=text.encode("utf-8"),
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


_CAPTURE_HISTORY_LINES = 200
"""Scrollback lines every verification capture includes (``-S -200``). Review N1 (260707-HFX-L3):
viewport-only capture let chips visible at origin scroll out before the new chip rendered, making
count growth undetectable -- and the idempotence guard shared the same truncated view, so a LANDED
paste could be re-sent (bounded, but the exact F-V class). Origin and verification captures both
flow through this window: growth math only holds when both sides see the same universe."""


def _capture_pane_argv(tmux_name: str) -> list[str]:
    """The history-inclusive verification capture command (pinned by test, not just by prose)."""
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
    """Public raw pane-capture entry point (260707-HFX-L8 turn-state classification).

    Shares the same history-inclusive ``tmux capture-pane`` this module already uses to verify
    pastes, so turn-state classification reads the identical view a paste-verification capture
    would -- no second capture command, no new tmux round-trip shape.
    """
    return _tmux_capture_pane(tmux_name)


class TerminalPaster:
    """Capture-verified paste into a durable tmux session, mirroring the frontend paste/submit loop.

    All tmux operations are injectable (the ``terminal.py`` posture) so the confirmation loop is
    deterministically unit-testable against fakes -- no real tmux server, no real sleep.
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
        echo_timeout: float = _ECHO_TIMEOUT,
        boot_deadline: float = _BOOT_DEADLINE,
        submit_timeout: float = _SUBMIT_TIMEOUT,
        poll_interval: float = _POLL_INTERVAL,
    ) -> PasteResult:
        """Paste ``text`` (capture-verified against the pre-delivery origin) and optionally submit it.

        Delivery is reported ONLY after a pane capture shows this paste landed (new chip or payload
        head); the retry path re-captures FIRST and never re-sends a landed paste (260707-HFX-L3).
        Only then, when ``submit``, sends ``Enter`` and watches for output past the post-paste
        baseline (``submitted``). Never raises on a missing/gone session -- an unverifiable delivery
        reports ``delivered=False`` with the final pane capture attached, the same "surface loudly,
        never silently drop" contract as the frontend.
        """
        sanitized = sanitize_for_injection(text)
        buffer_name = f"ar-spawn-{os.getpid()}-{uuid4().hex[:8]}"
        delivered, snap = self._paste_until_verified(
            tmux_name,
            sanitized,
            buffer_name,
            echo_timeout=echo_timeout,
            boot_deadline=boot_deadline,
            poll_interval=poll_interval,
        )
        if not (delivered and submit):
            return PasteResult(delivered=delivered, submitted=False, capture=snap)
        baseline = self._capture_pane(tmux_name)
        self._press(tmux_name, "Enter")
        submitted, snap = self._await_advance(
            tmux_name, baseline, timeout=submit_timeout, poll_interval=poll_interval
        )
        return PasteResult(delivered=True, submitted=submitted, capture=snap)

    def _press(self, tmux_name: str, key: str) -> None:
        """Send one key through the delivery seam -- ``Escape`` is refused by construction.

        260707-HFX-L3 run discipline (dispatch-pack PASTE DISCIPLINE): Escape INTERRUPTS a codex
        session, so no delivery/cleanup path may ever emit it through this seam.
        """
        if key == "Escape":
            raise ValueError(
                "terminal delivery never sends Escape -- it interrupts the target session "
                "(260707-HFX-L3 run discipline)"
            )
        self._send_key(tmux_name, key)

    def _paste_until_verified(
        self,
        tmux_name: str,
        sanitized: str,
        buffer_name: str,
        *,
        echo_timeout: float,
        boot_deadline: float,
        poll_interval: float,
    ) -> tuple[bool, str]:
        """Deliver ONE paste, capture-verified; re-send only after proof the last one did not land.

        The pre-delivery ``origin`` snapshot is the single baseline for every check. Re-baselining
        per attempt was the F-V defect: a chip that rendered between attempts landed inside the
        fresh baseline, so the seam never saw it and re-pasted -- stacking up to 7 duplicates.
        Returns ``(delivered, last_capture)``; the capture rides back as loud-failure evidence.
        """
        origin = self._capture_pane(tmux_name)
        started = self._monotonic()
        pasted_once = False
        while True:
            if pasted_once:
                # Idempotence guard (260707-HFX-L3): the TUI renders a beat behind keystrokes, so
                # re-capture FIRST -- the previous paste may have landed after its echo window
                # closed. A landed paste is never re-sent; the retry below is reached only with
                # fresh proof that the pane still shows no trace of it.
                snap = self._capture_pane(tmux_name)
                if _paste_landed(origin, snap, sanitized):
                    return True, snap
                if self._monotonic() - started >= boot_deadline:
                    return False, snap
            self._load_buffer(buffer_name, sanitized)
            self._paste_buffer(tmux_name, buffer_name)
            pasted_once = True
            landed, snap = self._await_echo(
                tmux_name,
                origin,
                sanitized,
                timeout=echo_timeout,
                poll_interval=poll_interval,
            )
            if landed:
                return True, snap
            with contextlib.suppress(OSError):
                self._sleep(_RETRY_DELAY)

    def _await_echo(
        self,
        tmux_name: str,
        origin: str,
        sanitized: str,
        *,
        timeout: float,
        poll_interval: float,
    ) -> tuple[bool, str]:
        """Bounded settle re-captures until the pane shows this paste's chip or visible draft.

        Returns ``(landed, last_capture)`` -- the final capture rides back so a verification
        failure is concluded WITH evidence, never from a stale snapshot.
        """
        started = self._monotonic()
        while True:
            current = self._capture_pane(tmux_name)
            if _paste_landed(origin, current, sanitized):
                return True, current
            if self._monotonic() - started >= timeout:
                return False, current
            with contextlib.suppress(OSError):
                self._sleep(poll_interval)

    def _await_advance(
        self, tmux_name: str, baseline: str, *, timeout: float, poll_interval: float
    ) -> tuple[bool, str]:
        """Poll ``capture-pane`` until the visible pane differs from ``baseline`` or ``timeout``."""
        started = self._monotonic()
        while True:
            current = self._capture_pane(tmux_name)
            if current != baseline:
                return True, current
            if self._monotonic() - started >= timeout:
                return False, current
            with contextlib.suppress(OSError):
                self._sleep(poll_interval)


def _paste_landed(origin: str, current: str, sanitized: str) -> bool:
    """True when ``current`` shows evidence of the paste itself (vs ``origin``), not just boot noise.

    Probes, strongest first (review N1): the payload-SPECIFIC codex chip (its size text is derived
    from this payload, so it stays detectable even when unrelated origin chips scroll out of the
    capture window and bare count-growth goes blind); generic chip-count growth (either vocabulary);
    the payload-head probe for composers that echo the draft text verbatim (claude prompt-echo).
    """
    if current == origin:
        return False
    expected_chip = _expected_codex_chip(sanitized)
    if current.count(expected_chip) > origin.count(expected_chip):
        return True
    if count_paste_chips(current) > count_paste_chips(origin):
        return True
    fragment = _echo_fragment(sanitized)
    return bool(fragment and current.count(fragment) > origin.count(fragment))


def _expected_codex_chip(sanitized: str) -> str:
    """The exact chip codex renders for THIS payload -- it embeds the pasted character count.

    A payload-specific probe: growth of this literal proves OUR paste landed. If codex ever counts
    differently, the literal simply never matches and the generic probes still apply (adjunct only,
    never load-bearing alone).
    """
    return f"[Pasted Content {len(sanitized)} chars]"


def _echo_fragment(sanitized: str) -> str:
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0][:120]
