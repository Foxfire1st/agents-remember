"""The supporting records' own refusals: three failures no earlier record group can reach.

Refusals live beside the record group that owns them rather than in one growing module, for the same
reason the row codecs and the schemas do: a refusal is a statement about *this* contract, and the
shared module is where the code, the facts and the next action are *spelled*, not where every
contract's failures accumulate. ``refusals.py`` was at 1196 of the repository's 1200-line hard limit
before this leaf added a line to it, so extending it in place was not available -- and splitting it by
protected property is the remedy the repository's own file-size rail asks for.

Three failures, and each is a different fact:

* **A digest that does not describe the bytes.** This is the one place a *recorded* value is compared
  against something outside the database, and ``invalid_reference`` is the honest code: the remedy is
  to correct the digest or the artifact, not to reread a row, and nothing stored was read to produce
  the refusal.
* **A resolution that leaves the declared root.** The recorded path is confined by construction (the
  vocabulary refuses an absolute, drive, UNC, backslash, NUL, ``..`` or pathspec spelling), so a
  resolution that escapes means the *root* is not the checkout the record describes.
* **Accepted origin data in a supporting record.** Persisting a claim or an observation endorses
  nothing, so this record group authors proposals and exposes no promotion. The code is the shipped
  ``promotion_not_supported``; the factory is separate because the offending record is a supporting
  record and its own table is what a caller has to look at.
"""

from __future__ import annotations

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.result import KnowledgeRefusal


def artifact_digest_mismatch_refusal(
    *, path: str, recorded: str, observed: str
) -> KnowledgeRefusal:
    """Refuse an observation whose recorded digest does not describe the artifact's bytes.

    Requirement 3.4: the digest is of the artifact's bytes and of nothing else. A record written with
    a digest the writer observed to be false would be an immutable row carrying a claim the substrate
    knows to be wrong, and downgrading it to the asserted-digest flag would launder it rather than
    report it.
    """

    return refusal(
        "invalid_reference",
        "add_verification_observation",
        "the recorded result-artifact digest does not describe the bytes at the recorded path",
        facts=RefusalFacts(
            table="verification_observation", record_id=path, expected=recorded, observed=observed
        ),
        next_action=(
            "Record the digest of the artifact's bytes as they are, or point the record at the "
            "artifact the digest was taken from. A stored digest is never re-pinned to the bytes "
            "that are present now, and re-observation is a new record."
        ),
    )


def artifact_root_escape_refusal(*, path: str, recorded: str, observed: str) -> KnowledgeRefusal:
    """Refuse an artifact resolution that leaves the root the caller declared."""

    return refusal(
        "invalid_reference",
        "add_verification_observation",
        "the recorded artifact path resolves outside the artifact root the caller declared",
        facts=RefusalFacts(
            table="verification_observation", record_id=path, expected=recorded, observed=observed
        ),
        next_action=(
            "Declare the root the record's repository-relative path belongs to. The path itself is "
            "confined vocabulary; a resolution that leaves the root describes another checkout."
        ),
    )


def evidence_promotion_not_supported_refusal(record_id: str) -> KnowledgeRefusal:
    """Refuse an evidence command that would store accepted origin data.

    §6.2 and §1.6: persisting a claim or an observation endorses nothing, so this record group
    authors proposed origin data only and exposes no promotion operation. The code is the shipped
    ``promotion_not_supported``; the factory is separate because the offending record is a supporting
    record and its own table is what a caller has to look at.
    """

    return refusal(
        "promotion_not_supported",
        "change_candidate",
        "the batch carries a supporting record authored as accepted origin data, which this "
        "candidate-only operation never stores",
        facts=RefusalFacts(table="knowledge_record", record_id=record_id),
        next_action=(
            "Author the record as proposed. Acceptance is decided by the owner of that process, not "
            "by a candidate change batch, and no supporting-record command promotes a proposal."
        ),
    )


__all__ = [
    "artifact_digest_mismatch_refusal",
    "artifact_root_escape_refusal",
    "evidence_promotion_not_supported_refusal",
]
