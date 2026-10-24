"""The token counter's name, declared where nothing heavy has to load to read it (MIK-R204 rule 1).

`agents_remember.models.tokens` imports `tiktoken`; a page header that only wants the tokenizer's *name*
should not pay for that at startup. This module is the light owner of the string, and
`tests/test_token_names.py` pins it to the counter's own name so the two cannot drift.
"""

from __future__ import annotations

#: The name `DEFAULT_TOKEN_COUNTER` reports. Kept beside the counter in meaning, apart from it in import cost.
TOKENIZER_NAME = "tiktoken:o200k_base"

__all__ = ["TOKENIZER_NAME"]
