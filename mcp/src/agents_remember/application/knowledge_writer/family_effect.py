"""Whether a family history row may carry the curator's ``effect``: only a ``changed`` row does.

A changed family row records the curator's judgment of the meaning change, so it needs an effect;
any other disposition changes no meaning and records none.
"""

from __future__ import annotations

from typing import get_args

from agents_remember.application.knowledge_writer.handoff import RowRequest
from agents_remember.models.knowledge.effect import EffectLabel


def family_effect_refusal(request: RowRequest, subject: str) -> str | None:
    """Why a family row's effect input is refused, or ``None``: only a changed row carries one."""

    if request.disposition == "changed" and request.effect is None:
        return (
            f"family {subject}: a changed row records the curator's judgment of the meaning "
            "change, so it needs an 'effect', one of "
            f"{', '.join(get_args(EffectLabel))}; add it to this 'history' entry"
        )
    if request.disposition != "changed" and request.effect_supplied:
        return (
            f"family {subject}: a {request.disposition} row records no effect (only a changed row "
            "does); remove 'effect' from this 'history' entry, or make it a changed row"
        )
    return None
