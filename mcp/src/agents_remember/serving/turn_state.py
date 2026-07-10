"""Live turn-state classification from pane observation (260707-HFX-L8, issue #4).

Classifies a captured pane's text into one of the catalog's ``SeatTurnState`` values --
``working`` / ``turn-ended`` / ``awaiting-input`` / ``stale`` -- the same "model ended its turn" /
"waiting on you" signal a developer would read off a raw tmux/cmux pane, surfaced onto the catalog
row instead. Deliberately marker-based (regex over captured text), not a terminal-control-sequence
parser: cheap enough to run on the EXISTING L5 liveness sweep cadence (``terminal_liveness.py``),
never a new hot loop or a new tmux round-trip.

Per-harness marker tables are declared here (not in ``harnesses.py``): they are pane-TEXT patterns,
an orthogonal concern to that registry's launch-argv/knob-mapping tables. A harness id with no
declared markers, or no captured text at all, classifies generically off the shared markers so an
unknown/uncustomized harness still gets a best-effort signal rather than none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents_remember.serving.terminal_catalog import SeatTurnState

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
# Empty for now -- every known harness classifies off the shared markers above; a future harness
# with a distinctive pane shape adds its table here without touching the classifier itself.
_HARNESS_WORKING_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}
_HARNESS_AWAITING_INPUT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}
_HARNESS_TURN_ENDED_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}


@dataclass(frozen=True)
class TurnStateClassification:
    """One classification result: the state plus which marker family fired (for diagnostics)."""

    state: SeatTurnState
    evidence: str | None


def classify_turn_state(pane_text: str | None, *, harness: str | None = None) -> TurnStateClassification:
    """Classify a captured pane's text into a live turn-state.

    ``None`` or blank ``pane_text`` (a vanished/unreadable pane, the same evidence-less case the
    spawn-delivery paster already treats specially) classifies as ``stale`` -- there is nothing to
    read a turn signal off of. Precedence: working (busy) > awaiting-input (blocked-on-you) >
    turn-ended (idle-ready) > stale (no marker matched at all, i.e. an unrecognized pane shape).
    """
    if not pane_text or not pane_text.strip():
        return TurnStateClassification("stale", evidence=None)
    for pattern in (*_HARNESS_WORKING_PATTERNS.get(harness or "", ()), *_WORKING_PATTERNS):
        if pattern.search(pane_text):
            return TurnStateClassification("working", evidence=pattern.pattern)
    for pattern in (
        *_HARNESS_AWAITING_INPUT_PATTERNS.get(harness or "", ()),
        *_AWAITING_INPUT_PATTERNS,
    ):
        if pattern.search(pane_text):
            return TurnStateClassification("awaiting-input", evidence=pattern.pattern)
    for pattern in (*_HARNESS_TURN_ENDED_PATTERNS.get(harness or "", ()), *_TURN_ENDED_PATTERNS):
        if pattern.search(pane_text):
            return TurnStateClassification("turn-ended", evidence=pattern.pattern)
    return TurnStateClassification("stale", evidence=None)


def boot_ready(pane_text: str | None, *, harness: str | None = None) -> bool:
    """The R2 boot-readiness signature (the P-5 window): has the composer rendered ANY recognizable
    state yet (working / awaiting-input / turn-ended), as opposed to a still-booting pane with no
    marker at all (``stale``, this classifier's catch-all for "nothing recognized")?

    Deliberately reuses :func:`classify_turn_state` rather than a second marker table: a harness
    that has rendered any of its known shapes has, by construction, mounted a composer a paste can
    land in.
    """
    return classify_turn_state(pane_text, harness=harness).state != "stale"
