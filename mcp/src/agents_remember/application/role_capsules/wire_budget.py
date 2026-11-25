"""First-message wire-token ceilings and offline tiktoken measurement helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from types import MappingProxyType
from typing import Final

import tiktoken

from agents_remember.models.tokens import VENDORED_ENCODING_NAME, vendored_vocabulary_cache

PHASE_1_WIRE_TOKEN_CEILINGS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "investigator/orientation": 6_600,
        "bootstrap/bootstrap": 7_300,
        "worker/implementation": 10_200,
        "orchestrator/coordination": 10_400,
        "architect/planning": 11_100,
        "reviewer/review": 11_200,
        "curator/curation": 11_600,
        "manager/coordination": 11_800,
    }
)
# Explicit non-ratcheting Phase 1 appendix allowances added to PHASE_1_WIRE_TOKEN_CEILINGS[key]
# for ordinary-Latin Turn-1 appendices at the default 1,000-character message limit.
PHASE_1_HELD_APPENDIX_ALLOWANCE: Final[int] = 600
PHASE_1_LAUNCH_APPENDIX_ALLOWANCE: Final[int] = 900


@lru_cache(maxsize=1)
def _wire_encoding() -> tiktoken.Encoding:
    with vendored_vocabulary_cache(VENDORED_ENCODING_NAME):
        return tiktoken.get_encoding(VENDORED_ENCODING_NAME)


def measure_wire_tokens(
    message: str,
    *,
    replacements: Sequence[tuple[str, str]] = (),
) -> int:
    """Count plain-text wire tokens after applying ordered ``(old, new)`` normalizations."""

    normalized = message
    for old, new in replacements:
        if old:
            normalized = normalized.replace(old, new)
    return len(_wire_encoding().encode(normalized, disallowed_special=()))


def evaluate_wire_token_ceilings(
    measurements: Mapping[str, int],
    ceilings: Mapping[str, int] = PHASE_1_WIRE_TOKEN_CEILINGS,
) -> list[str]:
    """Return failure descriptions for any missing ceiling or over-budget measurement."""

    failures: list[str] = []
    for key, tokens in measurements.items():
        ceiling = ceilings.get(key)
        if ceiling is None:
            failures.append(f"{key}: no wire token ceiling configured (measured {tokens} tokens)")
        elif tokens > ceiling:
            failures.append(f"{key}: measured {tokens} tokens exceeds ceiling {ceiling}")
    return failures


__all__ = [
    "PHASE_1_HELD_APPENDIX_ALLOWANCE",
    "PHASE_1_LAUNCH_APPENDIX_ALLOWANCE",
    "PHASE_1_WIRE_TOKEN_CEILINGS",
    "evaluate_wire_token_ceilings",
    "measure_wire_tokens",
]
