"""The closed effect vocabulary: the nine labels an intended change may carry.

:data:`ADMITTED_EFFECT_LABELS` names the nine labels ``Doc13:98`` names, in that order, and
:data:`EffectLabel` is the literal type built from exactly that tuple, so a synonym, a compound
label, a free-text label and a tenth member are all the same refusal: the value does not validate.
Code never derives a label from anything -- a comparison that observes a condition set growing does
not make the effect ``strengthen``. There is no ``preserve`` member: a preservation claim is a
separate record, not an effect.

The effect claim, preservation claim and unresolved-question payload models that once stored this
vocabulary in the canonical database were retired with it (MIK-R26); planned effects are rows of the
history files (:mod:`agents_remember.models.knowledge_files.planned`).
"""

from __future__ import annotations

from typing import Literal

# The nine effect labels ``Doc13:98`` names, in that document's own order. The tuple is the one
# declaration; the literal type below is derived from it and a case asserts the two agree, so a
# tenth member cannot be added to the vocabulary without the schema admitting it.
ADMITTED_EFFECT_LABELS: tuple[str, ...] = (
    "restore",
    "clarify",
    "introduce",
    "strengthen",
    "weaken",
    "replace",
    "split",
    "merge",
    "retire",
)

EffectLabel = Literal[
    "restore",
    "clarify",
    "introduce",
    "strengthen",
    "weaken",
    "replace",
    "split",
    "merge",
    "retire",
]
