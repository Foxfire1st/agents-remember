"""The document-level check of a leaf's declared knowledge effects (``expectedKnowledgeEffects``).

Each declaration's own shape is checked by its model; this check needs the whole document: the
declarations belong to a leaf, a declared list is never empty (``null`` clears it), every
``requirementRef`` names a packet of the document's ``requirements`` when it lists packets, and no
subject and effect is declared twice.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Protocol


class _Declared(Protocol):
    @property
    def subject(self) -> str: ...

    @property
    def effect(self) -> str: ...

    @property
    def requirementRef(self) -> str: ...


class _Packet(Protocol):
    @property
    def stableId(self) -> str: ...

    @property
    def version(self) -> str: ...


def check_expected_knowledge_effects(
    kind: str,
    effects: Sequence[_Declared] | None,
    packets: Sequence[_Packet],
) -> None:
    """Refuse a declaration list that the document around it contradicts."""

    if effects is None:
        return
    if kind == "master":
        raise ValueError("expectedKnowledgeEffects belongs to a leaf document, not a master")
    if not effects:
        raise ValueError(
            "expectedKnowledgeEffects declares at least one effect; clear it with null instead"
        )
    named = {f"{packet.stableId}@{packet.version}" for packet in packets}
    unknown = sorted({effect.requirementRef for effect in effects} - named)
    if named and unknown:
        raise ValueError(
            "expectedKnowledgeEffects requirementRef names no packet in requirements: "
            + ", ".join(unknown)
        )
    counts = Counter(f"{effect.subject}#{effect.effect}" for effect in effects)
    repeated = sorted(key for key, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(
            "expectedKnowledgeEffects repeats a declaration (the same subject and effect): "
            + ", ".join(repeated)
        )
