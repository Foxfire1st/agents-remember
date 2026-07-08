"""The per-harness delivery adapter -- ONE interface, per-harness signatures (260707-HFX2-L3, R2).

Developer ruling (2026-07-07T22:45, verbatim): "As injector we will then use the paste into chat
method. It's the one that is harness independent as long as you get its method right." This module
is where "get its method right" lives: it encapsulates the pane-text signatures the injector
(``serving/injector.py``) needs to read per harness --

* **boot-readiness** (the P-5 window: has the composer mounted at all yet?)
* **composer state** (empty / has-content / a stacked paste chip -- the F-V trigger)
* **mid-turn behavior** (a paste while the harness is generating is queued as next-turn input --
  never a delivery failure)
* **modal dialog traps** (a codex quota dialog (#20) or a permission/confirmation prompt -- these
  are ``blocked(reason)``, never silent non-delivery)
* **post-submit confirmation** (did the turn actually start, closing "delivered into a dead turn")

Every signature here is a THIN COMPOSITION over the existing pane classifiers
(``pane_signals.classify_pane_signal`` / ``composer_state``, ``turn_state.classify_turn_state`` /
``boot_ready``) -- this module owns no new regex table. The per-harness differentiation already
lives where those modules put it: a ``dict[harness_id, patterns]`` override next to the shared
patterns. Adding a harness here means the harness id flows through to those tables, nothing more.

NEW-HARNESS CHECKLIST (R4: a future harness is one adapter, never a new delivery path):
    1. Chip vocabulary: extend ``terminal_paste._PASTE_CHIP`` only if the harness renders a large
       paste placeholder differently from both known vocabularies.
    2. Boot / busy / turn-ended markers: add an entry to ``turn_state.py``'s
       ``_HARNESS_WORKING_PATTERNS`` / ``_HARNESS_AWAITING_INPUT_PATTERNS`` /
       ``_HARNESS_TURN_ENDED_PATTERNS``, keyed by the harness id.
    3. Modal/blocked markers (quota, permission prompts): add an entry to ``pane_signals.py``'s
       ``_HARNESS_BLOCKED_PATTERNS``.
    4. Empty-composer marker, only if the harness's idle prompt glyph differs from the shared
       ``> `` family: ``pane_signals.py``'s ``_HARNESS_EMPTY_COMPOSER_PATTERNS``.
    5. Nothing in ``harness_adapters.py`` or ``injector.py`` branches on harness id -- ``get_adapter``
       below already reads whatever tables exist for the new id (empty tables classify off the
       shared markers, same graceful-degradation the existing classifiers already give an
       unconfigured harness).

R4 NON-GOALS (developer-ruled): harness hooks, Agent SDK sessions, and the codex app-server
protocol are NOT delivery channels this adapter (or ``injector.py``) ever reads through. The paste
seam is the one mechanism this project controls end-to-end regardless of harness; a protocol- or
hook-based channel would fork the delivery story per harness, which is exactly what this leaf
retires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.serving.pane_signals import (
    ComposerState,
    blocked_reason_label,
    classify_pane_signal,
)
from agents_remember.serving.pane_signals import (
    composer_state as _composer_state,
)
from agents_remember.serving.turn_state import boot_ready as _boot_ready
from agents_remember.serving.turn_state import classify_turn_state

MidTurnBehavior = Literal["queued-next-turn"]
"""R2: a paste delivered while the harness is generating is never lost -- both known harnesses
queue it as the next turn's input. Documented here (not a branch): there is nothing to react to
differently per harness for THIS signature today, but the classification stays first-class (rather
than folded into ``mid_turn() -> bool``'s caller re-deriving meaning) so a harness whose mid-turn
paste behavior differs (e.g. rejected instead of queued) has a typed slot to declare it in."""


@dataclass(frozen=True)
class HarnessAdapter:
    """The one adapter interface every harness reads through (claude-code, codex, and any future
    harness id) -- differentiated entirely by which per-harness pattern tables exist for
    ``harness_id``, never by a subclass or an if/elif ladder here.
    """

    harness_id: str | None

    def boot_ready(self, pane_text: str | None) -> bool:
        """The P-5 signature: has the composer rendered any recognizable state yet?"""
        return _boot_ready(pane_text, harness=self.harness_id)

    def composer_state(self, pane_text: str | None) -> ComposerState:
        """``empty`` / ``has-content`` / ``chip-stacked`` (the F-V trigger)."""
        return _composer_state(pane_text, harness=self.harness_id)

    def mid_turn(self, pane_text: str | None) -> bool:
        """Is the harness actively generating (so a paste queues rather than lands now)?"""
        return classify_pane_signal(pane_text, harness=self.harness_id).signal == "mid-turn"

    def mid_turn_behavior(self, pane_text: str | None) -> MidTurnBehavior | None:
        """The documented mid-turn contract for this harness, or ``None`` when not mid-turn."""
        return "queued-next-turn" if self.mid_turn(pane_text) else None

    def blocked_reason(self, pane_text: str | None) -> str | None:
        """A modal dialog trap's structured reason (``codex-quota-limit`` / ``permission-prompt``
        / ``modal-dialog``), or ``None`` when the pane shows no such trap. Checked mid-turn-first
        by the underlying classifier (``pane_signals.classify_pane_signal``'s precedence) so an
        actively-generating pane is never misread as blocked.
        """
        classification = classify_pane_signal(pane_text, harness=self.harness_id)
        if classification.signal != "blocked":
            return None
        return blocked_reason_label(classification.evidence)

    def turn_started(self, capture: str | None, *, advanced: bool) -> bool:
        """Post-submit confirmation: did the turn actually start (closes drop point 8, "delivered
        into a dead turn")? ``advanced`` is the generic pane-advance signal the paster already
        computes (``TerminalPaster``'s baseline-diff); this adds the harness-aware corroboration a
        bare byte-diff misses -- a busy/spinner marker proves the turn started even in the single
        poll window before the pane visibly diverges from its pre-submit baseline.
        """
        if advanced:
            return True
        return classify_turn_state(capture, harness=self.harness_id).state == "working"


GENERIC_ADAPTER = HarnessAdapter(harness_id=None)
"""An unconfigured/unknown harness: classifies off the shared markers only (graceful degradation,
matching ``pane_signals``/``turn_state``'s own per-harness-table fallback)."""

CLAUDE_CODE_ADAPTER = HarnessAdapter(harness_id="claude")
"""``Harness.id == "claude"`` (``harnesses.py``) -- the Claude Code TUI."""

CODEX_ADAPTER = HarnessAdapter(harness_id="codex")
"""``Harness.id == "codex"`` -- the codex TUI (quota/rate-limit modal, issue #20)."""

_ADAPTERS: dict[str, HarnessAdapter] = {
    "claude": CLAUDE_CODE_ADAPTER,
    "codex": CODEX_ADAPTER,
}


def get_adapter(harness_id: str | None) -> HarnessAdapter:
    """The adapter for ``harness_id`` -- a known id returns its named instance (documentation +
    identity for tests); an unknown/``None`` id returns the generic adapter, which still classifies
    correctly off the shared markers (never a refusal: a pane-signal read must never block on a
    harness the registry hasn't met yet).
    """
    if harness_id is None:
        return GENERIC_ADAPTER
    return _ADAPTERS.get(harness_id, HarnessAdapter(harness_id=harness_id))
