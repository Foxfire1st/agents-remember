"""The tokenizer's name is declared twice -- lightly here, authoritatively in the counter -- and pinned."""

from __future__ import annotations

from agents_remember.models.token_names import TOKENIZER_NAME
from agents_remember.models.tokens import DEFAULT_TOKEN_COUNTER


def test_the_light_name_is_the_counters_own_name() -> None:
    """MIK-R204 rule 1 lets the name live apart from the counter; this is what keeps them one fact."""

    assert DEFAULT_TOKEN_COUNTER.name == TOKENIZER_NAME
