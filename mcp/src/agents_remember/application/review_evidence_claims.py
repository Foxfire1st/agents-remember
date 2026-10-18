"""Evidence addresses and claim resolution (MIK-R102 rules 7, 8, 9).

A claim's reference is an address, and an address is only evidence when the packet that cites it
actually holds the object. This module reads a ``side:path#L1-L2`` reference through the same bounded
read the investigation uses, and resolves each typed basis against the packet's own comparison, its
collections and its receipts. A judgment row whose owner the packet does not carry stays unresolved
even when the text says ``present``; a proof entry with no established execution result cannot
support a recorded claim. None of this judges whether a claim's prose is true -- only whether the
authority it wears is one the packet can show.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents_remember.application.review_evidence_read import (
    DEFAULT_READ_LIMITS,
    read_bound_source,
)
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceLimits,
    EvidencePacket,
)
from agents_remember.models.knowledge.review_evidence_read import (
    ClaimResolution,
    EvidenceReadReceipt,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "EvidenceAddress",
    "evidence_address",
    "read_claim_basis",
    "resolve_claim",
]

_PATH_ADDRESS = re.compile(
    r"^(?P<side>code-base|code-candidate|memory-base|memory-candidate):"
    r"(?P<path>[^#]+?)(?:#L(?P<start>[1-9][0-9]*)(?:-L?(?P<end>[1-9][0-9]*))?)?$"
)


@dataclass(frozen=True)
class EvidenceAddress:
    """One claim reference resolved to the operands the same read takes (rule 8)."""

    side: str
    path: str
    start_line: int | None = None
    end_line: int | None = None


def evidence_address(reference: str) -> EvidenceAddress | None:
    """The side, path and range one claim reference spells, or ``None`` for another kind of basis."""

    matched = _PATH_ADDRESS.match(reference)
    if matched is None:
        return None
    return EvidenceAddress(
        side=matched.group("side"),
        path=matched.group("path"),
        start_line=int(matched.group("start")) if matched.group("start") else None,
        end_line=int(matched.group("end")) if matched.group("end") else None,
    )


def read_claim_basis(
    record: ReviewTreeComparisonRecord,
    basis: EvidenceAddress | str,
    *,
    limits: EvidenceLimits = DEFAULT_READ_LIMITS,
) -> EvidenceReadReceipt | None:
    """Read exactly what one claim cites, through the same bounded read (rule 8)."""

    address = basis if isinstance(basis, EvidenceAddress) else evidence_address(basis)
    if address is None:
        return None
    return read_bound_source(
        record,
        address.side,
        address.path,
        start_line=address.start_line,
        end_line=address.end_line,
        limits=limits,
    )


def resolve_claim(packet: EvidencePacket, claim_id: str) -> ClaimResolution:
    """Check every basis of one claim against the packet that cites it (rules 7, 8, 9).

    The check is mechanical and local: a basis resolves when the object it names is one the packet
    actually holds -- the packet's own comparison for a tree read, an item of the collection that
    claims to carry it, or a receipt the packet keeps. A judgment row whose owner is not in the
    packet is unresolved even when the text says ``present``; that is what stops a caller-written
    label from standing in for an inspected authority.
    """

    claim = next((entry for entry in packet.claims.claims if entry.claim_id == claim_id), None)
    if claim is None:
        return ClaimResolution(
            claim_id=claim_id,
            resolvable=False,
            checked=0,
            unresolved=(claim_id,),
            detail=f"the packet holds no claim {claim_id!r}",
        )
    unresolved: list[str] = []
    for basis in claim.bases:
        problem = _basis_problem(packet, basis)
        if problem is not None:
            unresolved.append(f"{basis.kind}:{basis.reference}: {problem}")
    return ClaimResolution(
        claim_id=claim_id,
        resolvable=not unresolved,
        checked=len(claim.bases),
        unresolved=tuple(unresolved),
        detail=(
            f"all {len(claim.bases)} basis/bases resolve against the packet's own comparison and "
            "collections"
            if not unresolved
            else f"{len(unresolved)} of {len(claim.bases)} basis/bases do not resolve"
        ),
    )


def _basis_problem(  # noqa: PLR0911 - one dispatch per basis kind, each its own answer
    packet: EvidencePacket, basis: object
) -> str | None:
    assert isinstance(basis, ClaimBasis)
    comparison = packet.comparison
    if (
        basis.comparison_number is not None
        and basis.comparison_number != comparison.comparison_number
    ):
        return (
            f"the basis names comparison {basis.comparison_number}, and this packet describes "
            f"{comparison.comparison_number}"
        )
    if basis.state != "present":
        return None  # a basis that says it was not read is honestly unresolved, not a lie
    if basis.kind in {
        "tree-blob",
        "knowledge-record",
        "judgment-row",
        "proof-entry",
        "history-event",
    }:
        return _item_problem(packet, basis)
    if basis.kind == "supplemental-receipt":
        receipts = {receipt.receipt_id: receipt for receipt in packet.supplemental}
        held = receipts.get(basis.receipt_id or "")
        if held is None:
            return f"the packet keeps no receipt {basis.receipt_id!r}"
        if held.read_state != "present":
            return f"receipt {basis.receipt_id!r} is {held.read_state}, not present bytes"
        return None
    if basis.kind == "worklist-row":
        worklist = packet.collection("worklist")
        if worklist is None or worklist.state != "present":
            return "the packet carries no readable worklist collection"
        return None
    return None


def _item_problem(packet: EvidencePacket, basis: object) -> str | None:
    assert isinstance(basis, ClaimBasis)
    names = {
        "tree-blob": ("source-inventory-and-linked-locations", "recorded-effects"),
        "knowledge-record": ("judgments", "recorded-effects", "family-guarantees-and-membership"),
        "judgment-row": ("judgments",),
        "proof-entry": ("proofs",),
        "history-event": ("judgments",),
    }[basis.kind]
    for name in names:
        collection = packet.collection(name)
        if collection is None or collection.state not in {"present", "truncated"}:
            continue
        for item in collection.items:
            if _item_matches(basis, item):
                if basis.kind == "proof-entry" and not _execution_established(item):
                    return (
                        "the proof entry in the packet carries no established execution result, so "
                        "it cannot support a recorded claim"
                    )
                return None
    return (
        f"the packet's {', '.join(names)} collection(s) hold no item this {basis.kind} names; a "
        "present label on a basis the packet does not carry is not an inspected authority"
    )


def _item_matches(basis: object, item: dict[str, object]) -> bool:
    """Whether this item is a present record of exactly the thing the basis names (rules 7, 8).

    A matched entry must itself have been read: an item the packet records as absent carries no
    inspected fact, and a source item without an object id is a path the read did not find. The
    comparison's own side and tree must agree too, so a basis cannot ride on another operand.
    """

    assert isinstance(basis, ClaimBasis)
    if str(item.get("state") or "") != "present":
        return False
    if basis.kind == "tree-blob" and not item.get("object_id"):
        return False
    if (
        basis.kind == "tree-blob"
        and (basis.start_line is not None or basis.end_line is not None)
        and not _range_matches(basis, item.get("returned_range"))
    ):
        return False
    if basis.kind == "proof-entry" and str(item.get("execution") or "") != "established":
        return False
    checks = {
        "side": basis.side,
        "tree": basis.tree,
        "path": basis.path,
        "record_id": basis.record_id,
        "record_revision": basis.record_revision,
        "row_id": basis.row_id,
        "attempt": basis.attempt,
        "proof_id": basis.proof_id,
    }
    return all(expected is None or item.get(name) == expected for name, expected in checks.items())


def _range_matches(basis: ClaimBasis, inspected: object) -> bool:
    if not isinstance(inspected, (tuple, list)) or len(inspected) != 2:
        return False
    first, last = inspected
    return (
        isinstance(first, int)
        and isinstance(last, int)
        and first <= (basis.start_line or 1) <= (basis.end_line or basis.start_line or 1) <= last
    )


def _execution_established(item: dict[str, object]) -> bool:
    return str(item.get("execution") or "") == "established"
