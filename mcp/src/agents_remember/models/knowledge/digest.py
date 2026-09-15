"""The sealed revision payload and its digest.

One function owns what a revision's identity seals. The payload is the complete authored
semantic content -- statement, essential applicability, conditions, exclusions, origin state,
acceptance reference, the provenance envelope and the *sorted* predecessor set -- and the
digest excludes only itself. Because the predecessor set is inside the digest, an edge cannot
be edited behind an existing sealed revision: adding or removing one changes the aggregate
the revision identity stands for.
"""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.invariant import InvariantRevision

# Bumped only when the sealed field set changes. A different payload version is a different
# identity computation, so it is recorded rather than inferred from the presence of fields.
REVISION_PAYLOAD_VERSION = "invariant-revision-payload/v1"


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
        "state_at_origin": revision.state_at_origin,
        "acceptance_ref": revision.acceptance_ref,
        "provenance": revision.provenance.model_dump(mode="json"),
        "predecessors": sorted(revision.predecessors),
    }


def revision_payload_digest(revision: InvariantRevision) -> str:
    """Return the SHA-256 hex digest sealing ``revision``'s authored payload."""

    return sha256_digest(canonical_revision_payload(revision))


def sealed_revision(revision: InvariantRevision) -> InvariantRevision:
    """Return the revision carrying its recomputed digest.

    The store recomputes rather than trusting a supplied digest, so this is the single place
    that turns an authored aggregate into a stored one.
    """

    return revision.model_copy(update={"payload_digest": revision_payload_digest(revision)})
