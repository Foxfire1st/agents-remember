"""The one continuation every bounded read of a memory tree mints, and ``knowledge_read`` accepts.

A page of a converted memory tree's knowledge carries this token wherever it was minted -- the
published-intent block of ``read_ar_files`` or a ``knowledge_read`` view -- and the mounted
``knowledge_read`` tool resumes it (MIK-R02 rule 3). The token is self-describing so that a surface
which did not mint it can still resume it: it names

* the **memory tree** it pages (the tree key the derived index was built for), so a tree that
  changed between pages is refused instead of served a page from another state;
* the **response** it pages (``scope`` for the selective scope read behind the published-intent
  block, ``view`` for a named view) and the ``knowledge_read`` view that resumes it;
* the **seed**, spelled as the minting surface spelled it, so the resuming call need not repeat it;
* the **selection policy and its version**, and the **selection-manifest digest** of the whole
  ordered selection, so a position is only ever a position in the selection it was cut from;
* the **position** of the next row, and the **threshold** the pages were cut with, so a resumed
  read pages with the same bound whichever surface it resumes on;
* the **code tree** page 1 resolved its anchors at, so every later page resolves at the same one
  (the tree only: where its objects are read comes from the resuming request, never a stored
  path);
* the **seeds queued** behind this one when a full ``read_ar_files`` block collapsed its tail.
  A view walk's seed also carries its effective ordering, so a resume orders the rows the same way.

Nothing about the walk is kept by the server: the token is the whole state (MIK-R02, Exclusions).
It is an opaque, URL-safe string (compressed canonical JSON, so the queued seeds of a collapsed block
share their common path prefixes) with a fixed prefix, so it cannot be mistaken for the view token
(``<view>:<digest>:<position>``) or the scope cursor a database read mints; both of those keep their
own meaning on an unconverted dataset.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)

__all__ = [
    "CONTINUATION_FORMAT",
    "CONTINUATION_PREFIX",
    "KnowledgeContinuation",
    "PagedResponse",
    "decode_continuation",
    "encode_continuation",
    "is_knowledge_continuation",
]

CONTINUATION_FORMAT: Literal["knowledge-continuation/v2"] = "knowledge-continuation/v2"
# The token's fixed spelling prefix. It contains no ``:``, so the view-token codec reads a token of
# this format as "not a view token" rather than as a malformed one of its own.
CONTINUATION_PREFIX = "kc2."
# A memory tree is keyed by its Git tree id (SHA-1 or SHA-256 object format).
_TREE_ID_PATTERN = r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"
# Bounds what a caller can hand the decoder before anything is parsed: a real token is well under a
# kilobyte, and the seed it carries is bounded by the path and identity fields it holds.
_MAX_TOKEN_LENGTH = 16384
# The most seeds one collapsed block tail can queue; a block never holds more seeds than this.
_MAX_QUEUED_SEEDS = 64

PagedResponse = Literal["scope", "view"]


class KnowledgeContinuation(KnowledgeModel):
    """Where one bounded walk of a memory tree's selection resumes, and everything it is bound to.

    The wire spelling uses short keys (the aliases): a token is carried in every page, and in a
    ``read_ar_files`` block once per seed, so its size is part of every bounded response.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    continuation_format: Literal["knowledge-continuation/v2"] = Field(
        default=CONTINUATION_FORMAT, serialization_alias="f", validation_alias="f"
    )
    memory_tree_id: str = Field(
        pattern=_TREE_ID_PATTERN, serialization_alias="t", validation_alias="t"
    )
    response: PagedResponse = Field(serialization_alias="r", validation_alias="r")
    view: str = Field(
        min_length=1, max_length=LABEL_MAX_LENGTH, serialization_alias="v", validation_alias="v"
    )
    seed: dict[str, str] = Field(serialization_alias="s", validation_alias="s")
    selection_policy: str = Field(
        min_length=1, max_length=LABEL_MAX_LENGTH, serialization_alias="sp", validation_alias="sp"
    )
    policy_version: str = Field(
        min_length=1, max_length=LABEL_MAX_LENGTH, serialization_alias="pv", validation_alias="pv"
    )
    manifest_digest: str = Field(
        pattern=SHA256_PATTERN, serialization_alias="m", validation_alias="m"
    )
    position: int = Field(ge=0, serialization_alias="p", validation_alias="p")
    threshold_tokens: int = Field(ge=1, serialization_alias="th", validation_alias="th")
    # The code tree the walk's anchors were resolved at (``None`` when page 1 resolved none). Only
    # the tree is bound: where its objects are read is resolved from the resuming request, so the
    # token carries no local path.
    code_tree_id: str | None = Field(
        default=None, pattern=_TREE_ID_PATTERN, serialization_alias="c", validation_alias="c"
    )
    # Seeds queued after this one, when a full ``read_ar_files`` block collapsed its tail into one
    # continuation: the walk moves on to each in turn, from its first row.
    rest: tuple[dict[str, str], ...] = Field(
        default=(), max_length=_MAX_QUEUED_SEEDS, serialization_alias="q", validation_alias="q"
    )


def encode_continuation(continuation: KnowledgeContinuation) -> str:
    """Spell one continuation as the opaque token a page carries."""

    body = continuation.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    packed = zlib.compress(text.encode("utf-8"), level=9)
    encoded = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")
    return f"{CONTINUATION_PREFIX}{encoded}"


def is_knowledge_continuation(token: str) -> bool:
    """Whether a token claims this format -- decoding may still refuse it."""

    return token.startswith(CONTINUATION_PREFIX)


def decode_continuation(token: str) -> KnowledgeContinuation | None:
    """Read one token back, or ``None`` when it is not a well-formed token of this format."""

    if not is_knowledge_continuation(token) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    body = token[len(CONTINUATION_PREFIX) :]
    try:
        packed = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        raw = zlib.decompressobj().decompress(packed, _MAX_TOKEN_LENGTH * 4)
        continuation = KnowledgeContinuation.model_validate_json(raw)
    except (binascii.Error, zlib.error, ValueError, ValidationError):
        return None
    seeds = (continuation.seed, *continuation.rest)
    if any(len(value) > PATH_MAX_LENGTH for seed in seeds for value in seed.values()):
        return None
    return continuation
