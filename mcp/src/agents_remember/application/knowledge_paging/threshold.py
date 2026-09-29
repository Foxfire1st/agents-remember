"""The one token threshold every bounded knowledge read of a memory tree is cut to (MIK-R02 rule 1).

The bound is counted in ``tiktoken:o200k_base`` tokens over the canonical compact JSON a response
is measured in everywhere else (:func:`agents_remember.models.tokens.count_response_tokens`), so the
number a page is cut to and the ``tokens`` figure the response choke point stamps are the same
measurement. It is declared once, here; every surface that pages a memory tree reads this constant,
every page states it, and a continuation binds it, so a resumed read uses the same bound on
whichever surface it resumes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.models.tokens import DEFAULT_TOKEN_COUNTER, count_response_tokens

__all__ = [
    "ENVELOPE_RESERVE_TOKENS",
    "KNOWLEDGE_PAGE_THRESHOLD_TOKENS",
    "KNOWLEDGE_PAGE_TOKENIZER",
    "response_tokens",
    "threshold_block",
]

# The declared threshold, in tokens of the tokenizer below.
KNOWLEDGE_PAGE_THRESHOLD_TOKENS = 8000
KNOWLEDGE_PAGE_TOKENIZER = DEFAULT_TOKEN_COUNTER.name

# What the mounted response choke point adds after a page is cut: ``ok``, ``operation`` and the
# three token-accounting fields. A page is cut to the threshold less this reserve, so the response
# a caller receives -- not only the body this module measured -- stays within the threshold.
ENVELOPE_RESERVE_TOKENS = 64


def response_tokens(value: Mapping[str, Any]) -> int:
    """The token count of one response body, in the measurement the threshold is declared in."""

    return count_response_tokens(value)


def threshold_block() -> dict[str, Any]:
    """The threshold as every page states it."""

    return {"tokens": KNOWLEDGE_PAGE_THRESHOLD_TOKENS, "tokenizer": KNOWLEDGE_PAGE_TOKENIZER}
