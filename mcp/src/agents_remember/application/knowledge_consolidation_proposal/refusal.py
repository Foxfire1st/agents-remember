"""The one refusal a consolidation proposal answers with: a code and its sentence."""

from __future__ import annotations


class ProposalRefusal(Exception):
    """The proposal could not be answered for this read; ``detail`` names the remedy."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
