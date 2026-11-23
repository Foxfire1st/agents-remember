"""Retain only byte-bounded, isolated copies of candidate memory parses."""

from __future__ import annotations

from copy import deepcopy
from typing import Final

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo

CANDIDATE_PARSE_BYTES: Final = 256 * 1024 * 1024
CandidateKey = tuple[str, str]

_CANDIDATE_PARSES: BoundedMemo[CandidateKey, KnowledgeSide] = BoundedMemo.by_retained_bytes(
    CANDIDATE_PARSE_BYTES, max_entries=8
)


def remember_candidate_parse(key: CandidateKey, side: KnowledgeSide) -> None:
    """Leave an oversized parse to its caller and copy only a retention candidate."""

    if _CANDIDATE_PARSES.weight_of(key, side) <= _CANDIDATE_PARSES.capacity:
        _CANDIDATE_PARSES.put(key, deepcopy(side))
