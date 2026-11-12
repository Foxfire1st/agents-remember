"""Row encoders for fixtures that fill index-schema datasets directly.

Moved text-identical from ``memory/knowledge/records.py``: production decodes rows
(``decode_*`` stay there) and never encodes one, since the canonical writers are retired (MIK-R26).
"""

from __future__ import annotations

from typing import Any, get_args

from agents_remember.kernel.canonical_json import canonical_json_bytes, sha256_digest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.graph import RealizationRole
from anchor_fixture_models import RealizationClaimDraft


def encode_typed_column(value: Any) -> str:
    """Encode one typed-column value as canonical JSON text for storage."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return canonical_json_bytes(value).decode("utf-8")


def encode_authorship(authorship: Authorship) -> str:
    return encode_typed_column(authorship)


def claim_row_digest(
    repository_id: str,
    claim: RealizationClaimDraft,
    anchor_id: str,
    provenance: Authorship,
) -> str:
    """Digest one realization claim row so an explicit removal can name the row it expects."""

    return sha256_digest(
        {
            "table": "realization_claim",
            "repository_id": repository_id,
            "claim_id": claim.claim_id,
            "invariant_revision_id": claim.invariant_revision_id,
            "anchor_id": anchor_id,
            "role": claim.role,
            "rationale": claim.rationale,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def stored_realization_role(text: str) -> RealizationRole:
    """Narrow one stored role to the authored vocabulary, refusing an unknown one.

    Every role this store writes is already in the vocabulary, so an unknown value means the row
    was edited outside the operation. Serving it as if it were authored vocabulary would let a
    reader render a role nobody chose.
    """

    for role in get_args(RealizationRole):
        if text == role:
            return role
    raise KnowledgeStorageError(
        f"stored realization role {text!r} is not one of the authored role vocabulary "
        f"{get_args(RealizationRole)}; the row was altered outside the operation"
    )
