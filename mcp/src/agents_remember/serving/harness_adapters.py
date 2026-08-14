"""Per-harness pane diagnostics retained outside the protocol authority path.

No pane text participates in readiness, delivery acceptance, liveness, or approvals. These labels
exist only so migration diagnostics can distinguish a known modal from an empty/dead pane.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.serving.pane_signals import blocked_reason_label, classify_pane_signal


@dataclass(frozen=True)
class HarnessAdapter:
    """Diagnostic classifier for one harness id."""

    harness_id: str | None

    def blocked_reason(self, pane_text: str | None) -> str | None:
        """Label a modal in a failure-only capture; never decide whether input was accepted."""
        classification = classify_pane_signal(pane_text, harness=self.harness_id)
        if classification.signal != "blocked":
            return None
        return blocked_reason_label(classification.evidence)


GENERIC_ADAPTER = HarnessAdapter(harness_id=None)
CLAUDE_CODE_ADAPTER = HarnessAdapter(harness_id="claude")
CODEX_ADAPTER = HarnessAdapter(harness_id="codex")

_ADAPTERS = {
    "claude": CLAUDE_CODE_ADAPTER,
    "codex": CODEX_ADAPTER,
}


def get_adapter(harness_id: str | None) -> HarnessAdapter:
    if harness_id is None:
        return GENERIC_ADAPTER
    return _ADAPTERS.get(harness_id, HarnessAdapter(harness_id=harness_id))
