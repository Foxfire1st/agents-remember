"""The explainer's instructions: MIK-R105 v2 rule 12's capability text, read where a seat runs.

The seat is created with this text as its instructions and with no first prompt (MIK-R186 rule 5), so
the text is read here once, through the loader that enforces the finite word budget and the clauses
the capability may not lose, and from the generated copy an installed build ships rather than from a
checkout-only source file (rule 12 of MIK-R105's contract, L104-B19). A loader refusal becomes the
explainer's own typed refusal, so a build names why no seat could be instructed instead of starting
one with no instructions.
"""

from __future__ import annotations

from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.cli.role_instructions import CapabilityRefused, load_capability_resource

__all__ = ["CAPABILITY", "capability_text"]

#: The settings role and the registered capability both call this seat's text ``review-presenter``.
CAPABILITY = "review-presenter"


def capability_text() -> str:
    """The explainer's instructions, as the loader admits them."""

    try:
        return load_capability_resource(CAPABILITY).text
    except CapabilityRefused as error:
        raise ExplainerRefused("capsule-unavailable", str(error)) from error
