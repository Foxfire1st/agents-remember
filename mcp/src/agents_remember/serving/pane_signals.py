"""Supervisor pane-state classifier (260707-HFX2-L2, R2a): the P-15 fixture-zoo predicates.

Distinct from ``turn_state.py``'s ``classify_turn_state`` (which classifies the L8 catalog's
``working``/``turn-ended``/``awaiting-input``/``stale`` UI state): this classifier answers the
supervisor's own mechanical question -- which of the pilot run's four intervention triggers does
this pane show, right now -- so the sweep can act without a model ever reading the pane itself.

* ``never-briefed`` -- an empty composer on a freshly-booted harness pane (P-5/P-14): the brief
  never landed, or landed and was never confirmed.
* ``delivery-stalled`` -- two or more stacked, un-consumed paste chips: the F-V duplicate-paste
  class this run forensically diagnosed (terminal_paste.py's ``count_paste_chips``), now read as
  a supervisor trigger rather than a paste-verification signal.
* ``mid-turn`` -- an "esc to interrupt"-style marker: the harness is actively generating, so the
  supervisor must not intervene.
* ``blocked`` -- a modal confirmation/permission dialog (#20): the harness is waiting on a
  developer decision no automatic action can resolve.
* ``normal`` -- none of the above; no supervisor trigger fires on this pane.

Per-harness marker tables are declared here (mirroring ``turn_state.py``'s pattern), empty for now:
a harness id with no declared markers classifies off the shared markers, so an unknown/uncustomized
harness still gets a best-effort signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agents_remember.serving.terminal_paste import count_paste_chips

PaneSignal = Literal["never-briefed", "delivery-stalled", "mid-turn", "blocked", "normal"]

STACKED_CHIP_THRESHOLD = 2
"""Two or more un-consumed paste chips is the F-V delivery-stalled trigger."""

# Checked first: an actively-generating pane must never be misread as blocked or stalled.
_MID_TURN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"esc to interrupt", re.IGNORECASE),
    re.compile(r"\besc\s+to\s+cancel\b", re.IGNORECASE),
)

# A modal confirmation/permission dialog: blocked on a developer decision (#20).
_BLOCKED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"do you want to", re.IGNORECASE),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\ballow\b.*\?", re.IGNORECASE),
    re.compile(r"\bproceed\?", re.IGNORECASE),
    re.compile(r"press enter to continue", re.IGNORECASE),
)

# An idle composer prompt with nothing else rendered: a freshly-booted, never-briefed pane
# (P-5/P-14). Matched last among the marker families -- a blank composer under a mid-turn or
# blocked marker is not "never-briefed", it is whatever fired first.
_EMPTY_COMPOSER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?m)^\s*>\s*$"),
    re.compile(r"(?m)^\s*│\s*>\s*│?\s*$"),
)

# Codex-specific modal traps (260707-HFX2-L3, issue #20): the quota/rate-limit dialog ends the
# seat's turn and needs a developer decision (spend a reset / wait / switch harness) no automatic
# action can make -- classified as ``blocked`` here, never a silent non-delivery.
_HARNESS_MID_TURN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}
_HARNESS_BLOCKED_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "codex": (
        re.compile(r"approaching rate limits", re.IGNORECASE),
        re.compile(r"switch model\?", re.IGNORECASE),
        re.compile(r"hit your usage limit", re.IGNORECASE),
        re.compile(r"\busage limit\b", re.IGNORECASE),
    ),
}
_HARNESS_EMPTY_COMPOSER_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {}

# Blocked-reason label lookup (structured NEEDS-ATTENTION classification, R2): each pattern above
# maps to a short machine-readable reason so a caller never has to re-parse pane text to tell a
# quota modal apart from an ordinary permission prompt.
_QUOTA_REASON_MARKERS: tuple[str, ...] = ("rate limit", "usage limit", "switch model")


def blocked_reason_label(evidence: str | None) -> str:
    """The structured NEEDS-ATTENTION reason for a ``blocked`` classification's ``evidence`` pattern.

    ``evidence`` is the regex source text ``classify_pane_signal`` matched on -- reusing it (rather
    than re-scanning the pane) keeps this a pure lookup: no new pane read, no new pattern family.
    """
    if evidence is None:
        return "modal-dialog"
    lowered = evidence.lower()
    if any(marker in lowered for marker in _QUOTA_REASON_MARKERS):
        return "codex-quota-limit"
    return "permission-prompt"


ComposerState = Literal["empty", "has-content", "chip-stacked"]


def composer_state(pane_text: str | None, *, harness: str | None = None) -> ComposerState:
    """The R2 composer-state signature: ``empty`` / ``has-content`` / ``chip-stacked``.

    Reuses this module's own chip-count threshold and empty-composer patterns -- the single source
    of truth ``harness_adapters.py`` composes into the per-harness adapter, never a duplicated
    pattern table.
    """
    if not pane_text or not pane_text.strip():
        return "empty"
    if count_paste_chips(pane_text) >= STACKED_CHIP_THRESHOLD:
        return "chip-stacked"
    for pattern in (
        *_HARNESS_EMPTY_COMPOSER_PATTERNS.get(harness or "", ()),
        *_EMPTY_COMPOSER_PATTERNS,
    ):
        if pattern.search(pane_text):
            return "empty"
    return "has-content"


@dataclass(frozen=True)
class PaneSignalClassification:
    """One classification result: the signal plus which marker/count fired (for diagnostics)."""

    signal: PaneSignal
    evidence: str | None


def classify_pane_signal(
    pane_text: str | None, *, harness: str | None = None
) -> PaneSignalClassification:
    """Classify a captured pane's text into a supervisor trigger signal.

    ``None``/blank ``pane_text`` (a vanished/unreadable pane) classifies as ``normal`` -- there is
    no evidence of a trigger, and an evidence-less pane is a liveness concern (predicate R2e), not
    a pane-signal one. Precedence: mid-turn (busy) > blocked (needs you) > delivery-stalled (chip
    count, independent of prompt shape) > never-briefed (empty composer, post-boot) > normal.
    """
    if not pane_text or not pane_text.strip():
        return PaneSignalClassification("normal", evidence=None)
    for pattern in (*_HARNESS_MID_TURN_PATTERNS.get(harness or "", ()), *_MID_TURN_PATTERNS):
        if pattern.search(pane_text):
            return PaneSignalClassification("mid-turn", evidence=pattern.pattern)
    for pattern in (*_HARNESS_BLOCKED_PATTERNS.get(harness or "", ()), *_BLOCKED_PATTERNS):
        if pattern.search(pane_text):
            return PaneSignalClassification("blocked", evidence=pattern.pattern)
    chips = count_paste_chips(pane_text)
    if chips >= STACKED_CHIP_THRESHOLD:
        return PaneSignalClassification("delivery-stalled", evidence=f"chips={chips}")
    for pattern in (
        *_HARNESS_EMPTY_COMPOSER_PATTERNS.get(harness or "", ()),
        *_EMPTY_COMPOSER_PATTERNS,
    ):
        if pattern.search(pane_text):
            return PaneSignalClassification("never-briefed", evidence=pattern.pattern)
    return PaneSignalClassification("normal", evidence=None)
