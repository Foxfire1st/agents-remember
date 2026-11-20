"""The sealed revision payloads and their digests.

One function owns what a revision's identity seals. The payload is the complete authored
semantic content -- statement, essential applicability, conditions, exclusions, the provenance
envelope and the *sorted* predecessor set -- and the digest excludes itself and the revision's
derived ``state``: the state is no authored content but what the history files of the tree being
read say of the revision (proposed, reviewed or consolidated), so one revision keeps one seal while
a review fact is added beside it. Because the predecessor set is inside the digest, an edge cannot
be edited behind an existing sealed revision: adding or removing one changes the aggregate
the revision identity stands for.

A family revision is sealed by the same rule over its own payload: the joint guarantee,
provenance and sorted predecessor set. The two payloads carry
different payload versions because they seal different field sets, so a digest can never be
mistaken for the other object's identity.
"""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.family import FamilyRevision
from agents_remember.models.knowledge.invariant import InvariantRevision

# Bumped only when the sealed field set changes. A different payload version is a different
# identity computation, so it is recorded rather than inferred from the presence of fields.
# v2: the authored origin state and its acceptance reference left the payload; the state is derived.
REVISION_PAYLOAD_VERSION = "invariant-revision-payload/v2"
FAMILY_REVISION_PAYLOAD_VERSION = "family-revision-payload/v2"


def canonical_revision_payload(revision: InvariantRevision) -> dict[str, Any]:
    """Return the exact mapping the revision digest seals.

    The digest field is deliberately absent: a digest cannot cover itself. Everything else a
    reviewer would need to reproduce the statement is present, so two revisions with the same
    digest are the same authored aggregate.
    """

    return {
        "payload_version": REVISION_PAYLOAD_VERSION,
        "repository_id": revision.repository_id,
        "invariant_id": revision.invariant_id,
        "revision_id": revision.revision_id,
        "display_version": revision.display_version,
        "statement": revision.statement,
        "applicability": revision.applicability,
        "conditions": list(revision.conditions),
        "exclusions": list(revision.exclusions),
        "provenance": revision.provenance.model_dump(mode="json"),
        "predecessors": sorted(revision.predecessors),
    }


def revision_payload_digest(revision: InvariantRevision) -> str:
    """Return the SHA-256 hex digest sealing ``revision``'s authored payload."""

    return sha256_digest(canonical_revision_payload(revision))


def canonical_family_revision_payload(revision: FamilyRevision) -> dict[str, Any]:
    """Return the exact mapping a family revision's digest seals.

    The joint guarantee is sealed here rather than anywhere a membership could reach it: a
    membership cites the family *revision*, so the text it resolves is covered by that
    revision's identity and cannot be changed behind it.
    """

    return {
        "payload_version": FAMILY_REVISION_PAYLOAD_VERSION,
        "repository_id": revision.repository_id,
        "family_id": revision.family_id,
        "revision_id": revision.revision_id,
        "display_version": revision.display_version,
        "joint_guarantee": revision.joint_guarantee,
        "provenance": revision.provenance.model_dump(mode="json"),
        "predecessors": sorted(revision.predecessors),
    }


def family_revision_payload_digest(revision: FamilyRevision) -> str:
    """Return the SHA-256 hex digest sealing ``revision``'s authored payload."""

    return sha256_digest(canonical_family_revision_payload(revision))
