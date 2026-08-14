"""Diagnostic turn-state classification from pane observation.

Classifies a captured pane's text into one of the catalog's ``SeatTurnState`` values --
``working`` / ``turn-ended`` / ``awaiting-input`` / ``stale`` -- the same "model ended its turn" /
"waiting on you" signal a developer would read off a raw tmux/cmux pane, surfaced onto the catalog
row before the protocol cutover. It is now retained only as ``paneDiagnostic`` detail on the
adapter-owned liveness sweep; it cannot set catalog turn state or authorize any action. It remains
marker-based (regex over captured text), not a terminal-control-sequence parser.

Per-harness marker tables are declared here (not in ``harnesses.py``): they are pane-TEXT patterns,
an orthogonal concern to that registry's launch-argv/knob-mapping tables. A harness id with no
declared markers, or no captured text at all, classifies generically off the shared markers so an
    unknown/uncustomized harness still gets a best-effort diagnostic rather than none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents_remember.models.terminal_catalog import (
    SeatTurnState,
)

# Busy markers: the harness is visibly generating (a spinner, an interrupt hint, an explicit
# "thinking" style word). Checked FIRST -- a transient busy marker inside an otherwise-idle-looking
# pane must not misclassify as turn-ended.
_WORKING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"esc to interrupt", re.IGNORECASE),
    re.compile(r"\besc\s+to\s+cancel\b", re.IGNORECASE),
    re.compile(r"\bthinking\b", re.IGNORECASE),
    re.compile(r"\bgenerating\b", re.IGNORECASE),
    re.compile(r"[⠁-⣿]"),  # braille-pattern spinner glyphs (common TUI spinner block)
)

# Awaiting-input markers: the harness is blocked on a developer decision (a permission/confirmation
# prompt), distinct from "turn ended with nothing further expected".
_AWAITING_INPUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"do you want to", re.IGNORECASE),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\ballow\b.*\?", re.IGNORECASE),
    re.compile(r"\bproceed\?", re.IGNORECASE),
    re.compile(r"press enter to continue", re.IGNORECASE),
)

# Turn-ended markers: an idle input prompt with nothing pending -- the harness is ready for the
# next instruction (the tmux/cmux "model ended its turn" signal).
_TURN_ENDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?m)^\s*>\s*$"),
    re.compile(r"(?m)^\s*│\s*>\s*│?\s*$"),
    re.compile(r"\bready\b", re.IGNORECASE),
)

# Harness-specific marker overrides/additions (260707-HFX-L8 S1 pin): keyed by ``Harness.id``.
# Codex's tail-sensitive empty composer is handled below because a regex over full scrollback would
# confuse submitted U+203A transcript lines with the live editor. Other future static shapes belong
# in these tables.
_HARNESS_WORKING_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}
_HARNESS_AWAITING_INPUT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}
_HARNESS_TURN_ENDED_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}

_CODEX_EMPTY_COMPOSER = re.compile(
    r"^\s*\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
    r"(?:\s+Explain this codebase)?\s*$"
)
_CODEX_FOOTER = re.compile(
    r"(?:\?\s+for shortcuts|\b\d{1,3}%\s+(?:context\s+)?left\b|"
    r"^\s*[A-Za-z0-9._-]+\s+[A-Za-z0-9._-]+(?:\s+[A-Za-z0-9._-]+)?"
    r"\s+·\s+(?:~|/).+$)",
    re.IGNORECASE,
)


def codex_empty_composer_visible(pane_text: str) -> bool:
    """Whether the pane tail is Codex 0.144.1's empty/placeholder composer.

    Submitted user messages also render with a leading U+203A in scrollback. Only the live tail --
    optionally followed by Codex's status/footer line -- is a readiness marker, and arbitrary text
    after U+203A is an existing draft rather than an empty composer.
    """

    lines = [line for line in pane_text.splitlines() if line.strip()]
    if not lines:
        return False
    if _CODEX_EMPTY_COMPOSER.fullmatch(lines[-1]):
        return True
    return bool(
        len(lines) >= 2
        and _CODEX_EMPTY_COMPOSER.fullmatch(lines[-2])
        and codex_footer_line(lines[-1])
    )


def codex_footer_line(line: str) -> bool:
    """Whether one line is the bounded shipped Codex status/footer family."""

    return _CODEX_FOOTER.search(line) is not None


@dataclass(frozen=True)
class TurnStateClassification:
    """One classification result: the state plus which marker family fired (for diagnostics)."""

    state: SeatTurnState
    evidence: str | None


def _first_matching_state(
    ladder: tuple[tuple[SeatTurnState, tuple[re.Pattern[str], ...]], ...], text: str
) -> TurnStateClassification:
    """Walk a precedence-ordered marker ladder; the first pattern that fires names the state."""

    for state, patterns in ladder:
        for pattern in patterns:
            if pattern.search(text):
                return TurnStateClassification(state, evidence=pattern.pattern)
    return TurnStateClassification("stale", evidence=None)


def _classify_codex_pane(pane_text: str) -> TurnStateClassification:
    """Classify Codex off the live pane TAIL only.

    Codex renders submitted user messages into scrollback with the same U+203A the live composer
    uses, so a full-scrollback regex would keep re-reading history. Readiness comes from the empty
    composer; busy/blocked markers are read from the last two non-blank lines. There is deliberately
    no turn-ended marker family here -- an unrecognized tail is ``stale``, not idle.
    """

    if codex_empty_composer_visible(pane_text):
        return TurnStateClassification("turn-ended", evidence="codex-empty-composer-tail")
    non_blank = [line for line in pane_text.splitlines() if line.strip()]
    tail = "\n".join(non_blank[-2:])
    return _first_matching_state(
        (
            ("working", _WORKING_PATTERNS),
            ("awaiting-input", _AWAITING_INPUT_PATTERNS),
        ),
        tail,
    )


def _classify_by_marker_tables(pane_text: str, harness: str | None) -> TurnStateClassification:
    """Classify any harness off the shared marker tables, with per-harness overrides first."""

    key = harness or ""
    return _first_matching_state(
        (
            ("working", (*_HARNESS_WORKING_PATTERNS.get(key, ()), *_WORKING_PATTERNS)),
            (
                "awaiting-input",
                (*_HARNESS_AWAITING_INPUT_PATTERNS.get(key, ()), *_AWAITING_INPUT_PATTERNS),
            ),
            ("turn-ended", (*_HARNESS_TURN_ENDED_PATTERNS.get(key, ()), *_TURN_ENDED_PATTERNS)),
        ),
        pane_text,
    )


def classify_turn_state(
    pane_text: str | None, *, harness: str | None = None
) -> TurnStateClassification:
    """Classify a captured pane's text into a live turn-state.

    ``None`` or blank ``pane_text`` (a vanished/unreadable pane, the same evidence-less case the
    spawn-delivery paster already treats specially) classifies as ``stale`` -- there is nothing to
    read a turn signal off of. Precedence: working (busy) > awaiting-input (blocked-on-you) >
    turn-ended (idle-ready) > stale (no marker matched at all, i.e. an unrecognized pane shape).
    """
    if not pane_text or not pane_text.strip():
        return TurnStateClassification("stale", evidence=None)
    if harness == "codex":
        return _classify_codex_pane(pane_text)
    return _classify_by_marker_tables(pane_text, harness)


def boot_ready(pane_text: str | None, *, harness: str | None = None) -> bool:
    """Whether the harness shows its idle, empty composer and can accept a fresh dispatch input."""

    return classify_turn_state(pane_text, harness=harness).state == "turn-ended"
