"""Opening exact evidence from an admitted revision (MIK-R104 rule 7).

The frame may *ask* to open something. The host decides what that is:

* the reference must be one the revision's own admitted R102 packet carries -- a claim's basis, a
  claim identity, a collection item's address field, or a supplemental receipt. A reference the
  packet does not carry is refused, so a page cannot name a task report, a source checkout or an
  arbitrary address and have it opened;
* identity is typed. A ``claimId`` selects only that claim's own bases, so a request cannot borrow
  another claim's basis by sharing its reference; a reference two claims carry without a named claim
  is refused as ambiguous rather than resolved to whichever claim happens to come first; and prose
  in a collection item that merely equals a reference string admits nothing (L104-B09);
* the packet and the revision binding must name the same comparison, including both code and both
  memory sides; a page cannot move itself onto a weaker comparison by changing a message;
* the destination is the *existing* owner's destination, typed: a source blob at its exact tree, a
  knowledge record at its revision, a judgment row at its attempt, a proof entry at its record, a
  history event at its row. Nothing here invents a route;
* a basis whose state is not ``present`` answers ``unavailable`` with the basis' own limitation --
  a historical object that is gone stays gone, and today's record is never opened under yesterday's
  claim;
* a retained receipt answers its own ``read_state``: a present capture opens, and a changed or
  vanished one keeps its limitation and its exact source identity instead of being reported opened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import FrameManifest

__all__ = [
    "EvidenceDestination",
    "admitted_references",
    "resolve_evidence_reference",
]

_KIND_TO_DESTINATION: dict[str, str] = {
    "tree-blob": "source",
    "knowledge-record": "knowledge",
    "judgment-row": "judgment",
    "proof-entry": "proof",
    "history-event": "history",
    "worklist-row": "worklist",
    "comparison-field": "comparison",
    "supplemental-receipt": "supplemental",
}


@dataclass(frozen=True)
class EvidenceDestination:
    """What one evidence request produced: an exact destination, a limitation, or a refusal."""

    state: Literal["opened", "unavailable", "refused"]
    reference: str
    comparison: str
    kind: str | None = None
    target: dict[str, str] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


def _comparison_identity(manifest: FrameManifest) -> tuple[str, str, int]:
    binding = manifest.binding
    return binding.comparison_reference, binding.task_id, binding.comparison_number


def _same_comparison(manifest: FrameManifest) -> bool:
    """The packet states the revision's own comparison, in both of its identities."""

    packet = manifest.evidence.comparison
    reference, task_id, number = _comparison_identity(manifest)
    return (
        packet.comparison_reference == reference
        and packet.task_id == task_id
        and packet.comparison_number == number
        and (packet.code_base.tree, packet.code_candidate.tree)
        == (manifest.binding.code_base_tree, manifest.binding.code_candidate_tree)
        and (packet.memory_base.tree, packet.memory_candidate.tree)
        == (manifest.binding.memory_base_tree, manifest.binding.memory_candidate_tree)
    )


def _target(manifest: FrameManifest) -> dict[str, str]:
    """The review target the destination belongs to: the exact task and leaf of the binding."""

    return {
        "repository": manifest.evidence.comparison.repository_id,
        "master": manifest.binding.task_id,
        "leaf": manifest.binding.leaf_id,
        "comparison": manifest.binding.comparison_reference,
    }


def _location(basis: ClaimBasis) -> dict[str, Any]:
    """The typed identity a destination needs, taken from the basis and never re-derived."""

    location: dict[str, Any] = {}
    for name in (
        "side",
        "tree",
        "path",
        "start_line",
        "end_line",
        "record_id",
        "record_revision",
        "row_id",
        "attempt",
        "proof_id",
        "execution",
        "receipt_id",
        "comparison_number",
    ):
        value = getattr(basis, name)
        if value is not None:
            location[name] = value
    return location


def _find_claim(manifest: FrameManifest, claim_id: str) -> EvidenceClaim | None:
    return next(
        (claim for claim in manifest.evidence.claims.claims if claim.claim_id == claim_id), None
    )


def _find_receipt(manifest: FrameManifest, receipt_id: str) -> SupplementalReceipt | None:
    return next(
        (receipt for receipt in manifest.supplemental if receipt.receipt_id == receipt_id), None
    )


def _matching_bases(
    manifest: FrameManifest, reference: str, claim_id: str | None
) -> tuple[tuple[str, ClaimBasis], ...]:
    """Every basis that carries this reference, inside the named claim when one is named.

    A claim identity is a selector, not a hint: with a ``claim_id`` only that claim's own bases may
    answer, so a basis of another claim that happens to share the reference is never opened under
    the requested claim's name (L104-B09).
    """

    return tuple(
        (claim.claim_id, basis)
        for claim in manifest.evidence.claims.claims
        if claim_id is None or claim.claim_id == claim_id
        for basis in claim.bases
        if basis.reference == reference
    )


#: The item fields that are addresses. Prose in another field that happens to equal the reference is
#: not an admission (L104-B09).
_ADDRESS_FIELDS = ("reference", "path", "record_id", "row_id", "proof_id", "receipt_id")


def _collection_item(manifest: FrameManifest, reference: str) -> tuple[str, dict[str, Any]] | None:
    """The one collection and the address fields that admit this reference, or ``None``."""

    for collection in manifest.evidence.collections:
        for item in collection.items:
            address = {name: item[name] for name in _ADDRESS_FIELDS if item.get(name) == reference}
            if address:
                return collection.name, address
    return None


def _refused(reference: str, comparison: str, detail: str) -> EvidenceDestination:
    return EvidenceDestination(
        state="refused", reference=reference, comparison=comparison, detail=detail
    )


def admitted_references(manifest: FrameManifest, *, limit: int = 200) -> tuple[str, ...]:
    """The references this revision admits, in packet order and bounded.

    It exists for diagnosis and for a panel that wants to show what a saved page may open; it is not
    an admission mechanism. A request is still checked against the packet itself.
    """

    found: list[str] = []
    for claim in manifest.evidence.claims.claims:
        for basis in claim.bases:
            if len(found) < limit:
                found.append(basis.reference)
    for receipt in manifest.supplemental:
        if len(found) < limit:
            found.append(receipt.receipt_id)
    return tuple(dict.fromkeys(found))


def _basis_destination(
    manifest: FrameManifest, reference: str, basis: ClaimBasis
) -> EvidenceDestination:
    """The exact destination one admitted basis names, or its own limitation."""

    kind = _KIND_TO_DESTINATION[basis.kind]
    if basis.state != "present":
        return EvidenceDestination(
            state="unavailable",
            reference=reference,
            comparison=manifest.binding.comparison_reference,
            kind=kind,
            target=_target(manifest),
            location=_location(basis),
            detail=(
                basis.limitation
                or f"the {basis.kind} this claim rests on was {basis.state} when it was admitted"
            ),
        )
    return EvidenceDestination(
        state="opened",
        reference=reference,
        comparison=manifest.binding.comparison_reference,
        kind=kind,
        target=_target(manifest),
        location=_location(basis),
        detail=f"the exact {basis.kind} this revision's claim rests on",
    )


def _receipt_destination(
    manifest: FrameManifest, reference: str, receipt: SupplementalReceipt
) -> EvidenceDestination:
    """A retained receipt answers with its own read state: an unread capture never opens."""

    location: dict[str, Any] = {
        "receipt_id": receipt.receipt_id,
        "canonical_source": receipt.canonical_source,
        "capture_identity": receipt.capture_identity,
    }
    if receipt.read_state != "present":
        return EvidenceDestination(
            state="unavailable",
            reference=reference,
            comparison=manifest.binding.comparison_reference,
            kind="supplemental",
            target=_target(manifest),
            location={**location, "read_state": receipt.read_state},
            detail=(
                receipt.limitation
                or f"the receipt was {receipt.read_state} when this comparison was recorded"
            ),
        )
    if receipt.digest is not None:
        location["digest"] = receipt.digest
    return EvidenceDestination(
        state="opened",
        reference=reference,
        comparison=manifest.binding.comparison_reference,
        kind="supplemental",
        target=_target(manifest),
        location=location,
        detail="the supplemental receipt this revision retained with its examined bytes",
    )


def _collection_destination(
    manifest: FrameManifest, reference: str, item: tuple[str, dict[str, Any]]
) -> EvidenceDestination:
    name, address = item
    return EvidenceDestination(
        state="opened",
        reference=reference,
        comparison=manifest.binding.comparison_reference,
        kind="collection-item",
        target=_target(manifest),
        location={**address, "collection": name},
        detail="an item of one of the packet's own collections, by its address field",
    )


def _unclaimed_destination(
    manifest: FrameManifest, reference: str, claim_id: str | None
) -> EvidenceDestination:
    """What answers when no basis carries the reference: a receipt, an item, or a refusal."""

    comparison = manifest.binding.comparison_reference
    if claim_id is not None:
        return _refused(
            reference,
            comparison,
            f"claim {claim_id!r} carries no basis {reference!r}; a claim identity selects its own "
            "bases and never borrows another claim's",
        )
    receipt = _find_receipt(manifest, reference)
    if receipt is not None:
        return _receipt_destination(manifest, reference, receipt)
    item = _collection_item(manifest, reference)
    if item is not None:
        return _collection_destination(manifest, reference, item)
    return _refused(
        reference,
        comparison,
        "the revision's admitted evidence does not carry this reference; a page cannot open a "
        "path, record or address its own packet did not admit",
    )


def _ambiguous(
    reference: str, comparison: str, matches: tuple[tuple[str, ClaimBasis], ...]
) -> EvidenceDestination:
    """Two claims may not answer one unnamed reference: the host would substitute a basis."""

    owners = ", ".join(sorted({claim_id for claim_id, _basis in matches}))
    return _refused(
        reference,
        comparison,
        f"{len(matches)} bases carried by claims {owners} share the reference {reference!r}; the "
        "request names no claim identity, so the host cannot choose one typed basis without "
        "substituting another claim's",
    )


def resolve_evidence_reference(
    manifest: FrameManifest, reference: str, *, claim_id: str | None = None
) -> EvidenceDestination:
    """Resolve one requested reference against one admitted revision, or refuse it by name."""

    comparison = manifest.binding.comparison_reference
    if not _same_comparison(manifest):
        return _refused(
            reference,
            comparison,
            "the revision's evidence packet and its binding name different comparisons; no "
            "destination is opened from a packet that does not belong to these bytes",
        )
    if claim_id is not None and _find_claim(manifest, claim_id) is None:
        return _refused(
            reference,
            comparison,
            f"the packet carries no claim {claim_id!r}; a claim identity is not a hint",
        )
    matches = _matching_bases(manifest, reference, claim_id)
    if len(matches) > 1:
        return _ambiguous(reference, comparison, matches)
    if matches:
        return _basis_destination(manifest, reference, matches[0][1])
    return _unclaimed_destination(manifest, reference, claim_id)
