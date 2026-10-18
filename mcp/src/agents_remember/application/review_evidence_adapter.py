"""The prepared-evidence seam: the landed comparison payload, a packet, and exact addresses.

MIK-R102 rule 2 asks the explanation to start from what the product already knows, and rule 8 asks
every claim to name an address a consumer can open. This module is the seam between them, and it
owns no second comparison: it takes the landed comparison owner's opened :class:`ReviewTrees`
with its measured side/index states, reads the landed schemas at that exact tree, and produces

* an :class:`EvidencePacket` whose collections are filled from those reads, with each unselected
  scope still stated as unselected rather than defaulted into "nothing found"; and
* :class:`PreparedAddress` rows -- one per inspected thing and one per claim basis -- carrying the
  side, path, line range, record id and revision, judgment row and attempt, or proof id that a
  consumer needs to reopen exactly what the explanation cites.

Reading is the bounded read of :mod:`review_evidence_read`, so the adapter inherits its exact-tree
operand, its byte and page bounds and its distinct absent/binary/unavailable states. It starts no
role, launches no page and writes nothing: it prepares evidence for a consumer that already exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.review_evidence_packet import (
    SideState,
    build_evidence_packet,
    comparison_reference,
    same_comparison,
)
from agents_remember.application.review_evidence_read import (
    DEFAULT_READ_LIMITS,
    read_bound_source,
)
from agents_remember.application.review_evidence_relations import read_bound_relationships
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceCollection,
    EvidenceLimits,
    EvidenceMap,
    EvidencePacket,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
)

__all__ = [
    "NAME_FAMILY",
    "NAME_JUDGMENTS",
    "NAME_PROOFS",
    "NAME_SOURCES",
    "PreparedAddress",
    "PreparedEvidence",
    "consume_address",
    "prepare_evidence",
]

NAME_SOURCES = "source-inventory-and-linked-locations"
NAME_FAMILY = "family-guarantees-and-membership"
NAME_JUDGMENTS = "judgments"
NAME_PROOFS = "proofs"

_MAX_HISTORY_FILES = 200


@dataclass(frozen=True)
class PreparedAddress:
    """One exact address the packet's claims can cite, in the landed owners' own identifiers."""

    address_id: str
    kind: str
    reference: str
    side: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    record_id: str | None = None
    record_revision: int | None = None
    row_id: str | None = None
    attempt: int | None = None
    proof_id: str | None = None
    read_state: str = "present"
    claim_ids: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True)
class PreparedEvidence:
    """What preparation produced: the packet, its addresses, and what it could not reach."""

    packet: EvidencePacket
    addresses: tuple[PreparedAddress, ...]
    state: str
    limitation: str | None


@dataclass(frozen=True)
class AddressRead:
    """What one consumer read of a prepared address found."""

    address_id: str
    state: str
    read_state: str | None = None
    path: str | None = None
    object_id: str | None = None
    detail: str = ""


def prepare_evidence(  # noqa: PLR0913 - one preparation states its own scope and payload
    trees: ReviewTrees,
    *,
    scope: str,
    question: str,
    evidence_map: EvidenceMap,
    claims: tuple[EvidenceClaim, ...] = (),
    paths: tuple[str, ...] = (),
    family_path: str | None = None,
    limits: EvidenceLimits = DEFAULT_READ_LIMITS,
) -> PreparedEvidence:
    """Prepare from the comparison owner's opened sides, preserving its measured index state."""

    record = trees.record
    before = _memory_state(trees.before.wire)
    after = _memory_state(trees.after.wire)
    states = {side.side: SideState(side.state, side.detail) for side in trees.code_sides}
    if set(states) != {"base", "candidate"}:
        raise ValueError("preparation requires the comparison owner's measured code sides")
    if claims:
        cited = {
            basis.path for claim in claims for basis in claim.bases if isinstance(basis.path, str)
        }
        if len(cited | set(paths)) > limits.paths_per_packet:
            raise ValueError(
                f"the prepared scope names more than {limits.paths_per_packet} path(s); select a "
                "narrower scope or page the evidence"
            )
    sources = _source_collection(record, paths, limits)
    family = _family_collection(record, family_path, limits)
    judgments, addresses = _judgment_collections(record, limits)
    proofs, proof_addresses = _proof_collection(record, limits)
    collections = (sources, family, judgments, proofs)
    packet = build_evidence_packet(
        record,
        repository_id=record.task_id,
        scope=scope,
        question=question,
        code_base=states["base"],
        code_candidate=states["candidate"],
        memory_before=before,
        memory_after=after,
        evidence_map=evidence_map,
        limits=limits,
        collections={collection.name: collection for collection in collections},
        claims=claims,
        limitations=_limitations(record, collections)
        + tuple(side.detail for side in (before, after) if side.detail),
        recorded_at=record.recorded_at,
    )
    prepared = tuple(
        PreparedAddress(
            address_id=address.address_id,
            kind=address.kind,
            reference=address.reference,
            side=address.side,
            path=address.path,
            start_line=address.start_line,
            end_line=address.end_line,
            record_id=address.record_id,
            record_revision=address.record_revision,
            row_id=address.row_id,
            attempt=address.attempt,
            proof_id=address.proof_id,
            read_state=address.read_state,
            claim_ids=_claim_ids(claims, address),
            detail=address.detail,
        )
        for address in (*addresses, *proof_addresses)
    )
    missing = tuple(collection.name for collection in collections if collection.state != "present")
    incomplete = any(
        side.state != "available" or side.index_state == "partial" for side in (before, after)
    )
    return PreparedEvidence(
        packet=packet,
        addresses=prepared,
        state="partial" if missing or incomplete else "prepared",
        limitation=(
            f"these collections carry no present population: {', '.join(missing)}; each stays "
            "stated as it was read rather than as an empty result"
            if missing
            else "; ".join(side.detail for side in (before, after) if side.detail) or None
        ),
    )


def _memory_state(side: ReviewKnowledgeSide) -> SideState:
    detail = side.detail
    if side.index_state == "partial":
        detail = (
            f"the {side.side} memory index at {side.tree} is partial: {side.problems}; "
            "valid records remain usable, but absence of an omitted record is unknown"
        )
    return SideState(state=side.state, index_state=side.index_state, detail=detail)


def consume_address(
    record: ReviewTreeComparisonRecord, prepared: PreparedEvidence, address_id: str
) -> AddressRead:
    """Resolve one prepared address for *this* comparison, through the preparation's own bounds.

    The address belongs to the packet that prepared it. A record for another comparison -- another
    task, leaf, number, repository or conversion -- is refused before any read, and an address whose
    judgment row, attempt or revision the prepared packet does not carry is unresolved, so a foreign
    tree holding an unrelated file at the same path cannot answer for the prepared one.
    """

    if not same_comparison(prepared.packet, record):
        return AddressRead(
            address_id=address_id,
            state="refused",
            detail=(
                f"address {address_id!r} belongs to the prepared packet for comparison "
                f"{prepared.packet.comparison.comparison_reference}; the record offered is "
                f"{comparison_reference(record)}, a different identity, so the address is not "
                "evidence about it"
            ),
        )
    address = next((entry for entry in prepared.addresses if entry.address_id == address_id), None)
    if address is None:
        return AddressRead(
            address_id=address_id,
            state="unresolved",
            detail=f"the prepared evidence holds no address {address_id!r}",
        )
    if address.kind in {"judgment-row", "history-event", "proof-entry"} and not _carried(
        prepared, address
    ):
        return AddressRead(
            address_id=address_id,
            state="unresolved",
            read_state=address.read_state,
            detail=(
                "the prepared packet carries no present item with this address' record, row, "
                "attempt or proof identity; an address the preparation did not inspect is not "
                "resolved against a path that only looks the same"
            ),
        )
    if address.side is None or address.path is None:
        return AddressRead(
            address_id=address_id,
            state="unresolved",
            read_state=address.read_state,
            detail=(
                f"address {address_id!r} names no tree path this read can open; the packet states "
                "the limitation it carries instead of inventing a destination"
            ),
        )
    receipt = read_bound_source(
        record,
        address.side,
        address.path,
        start_line=address.start_line,
        end_line=address.end_line,
        limits=prepared.packet.limits,
    )
    return AddressRead(
        address_id=address_id,
        state="resolved" if receipt.state == "present" else "unresolved",
        read_state=receipt.state,
        path=address.path,
        object_id=receipt.object_id,
        detail=receipt.detail,
    )


def _carried(prepared: PreparedEvidence, address: PreparedAddress) -> bool:
    """Whether the prepared packet holds the item this address names, by its own identity."""

    names = {
        "judgment-row": (NAME_JUDGMENTS,),
        "history-event": (NAME_JUDGMENTS,),
        "proof-entry": (NAME_PROOFS,),
    }.get(address.kind, ())
    for name in names:
        collection = prepared.packet.collection(name)
        if collection is None:
            continue
        for item in collection.items:
            if str(item.get("state") or "") != "present":
                continue
            identity = {
                "path": address.path,
                "side": address.side,
                "record_revision": address.record_revision,
                "row_id": address.row_id,
                "attempt": address.attempt,
                "proof_id": address.proof_id,
                "record_id": address.record_id,
            }
            if all(item.get(key) == value for key, value in identity.items()):
                return True
    return False


# --- collections from the landed payload --------------------------------------------------------


def _source_collection(
    record: ReviewTreeComparisonRecord, paths: tuple[str, ...], limits: EvidenceLimits
) -> EvidenceCollection:
    """The supplied paths, each read at the memory candidate, with its own state kept."""

    if not paths:
        return EvidenceCollection(
            name=NAME_SOURCES,
            producer=comparison_reference(record),
            population="the scope named no source path",
            state="not-selected",
            reason="this preparation was asked to explain the comparison without naming source paths",
        )
    items: list[dict[str, object]] = []
    states: list[str] = []
    for path in paths[: limits.paths_per_packet]:
        receipt = read_bound_source(record, "memory-candidate", path, limits=limits)
        states.append(receipt.state)
        items.append(
            {
                "path": path,
                "side": "memory-candidate",
                "tree": record.memory_candidate.tree,
                "object_id": receipt.object_id,
                "returned_range": receipt.returned_range,
                "state": receipt.state,
                "detail": receipt.detail,
            }
        )
    present = sum(1 for state in states if state == "present")
    if present:
        return EvidenceCollection(
            name=NAME_SOURCES,
            producer=comparison_reference(record),
            population=f"the {len(paths)} source path(s) named for this comparison",
            state="present",
            count=len(items),
            items=tuple(items),
        )
    return EvidenceCollection(
        name=NAME_SOURCES,
        producer=comparison_reference(record),
        population=f"the {len(paths)} source path(s) named for this comparison",
        state="measured-empty" if all(state == "absent" for state in states) else "unavailable",
        count=0,
        reason=(
            "none of the named paths holds a text file at this comparison's memory candidate: "
            + ", ".join(f"{item['path']} is {item['state']}" for item in items)
        ),
    )


def _family_collection(
    record: ReviewTreeComparisonRecord, family_path: str | None, limits: EvidenceLimits
) -> EvidenceCollection:
    """One family's recorded membership and links, followed at its exact tree (rule 5)."""

    if family_path is None:
        return EvidenceCollection(
            name=NAME_FAMILY,
            producer=comparison_reference(record),
            population="no family was named",
            state="not-selected",
            reason="the explanation's scope names no family to read",
        )
    relations = read_bound_relationships(record, "memory-candidate", family_path, limits=limits)
    if relations.state != "records":
        return EvidenceCollection(
            name=NAME_FAMILY,
            producer=comparison_reference(record),
            population=f"the family at {family_path}",
            state="unavailable",
            reason=relations.detail or "the family record could not be read",
        )
    items = tuple(
        {
            "record_id": edge.record_id,
            "relation": edge.relation,
            "direction": edge.direction,
            "path": edge.path,
            "record_revision": edge.record_revision,
            "origin_leaf": edge.origin_leaf,
            "state": edge.state,
        }
        for edge in relations.edges
    )
    return EvidenceCollection(
        name=NAME_FAMILY,
        producer=comparison_reference(record),
        population=f"the family at {family_path} and the records it names",
        state="present" if items else "measured-empty",
        count=len(items) if items else 0,
        reason=None if items else "the family records no member, link or owner row",
        items=items,
    )


def _judgment_collections(
    record: ReviewTreeComparisonRecord, limits: EvidenceLimits
) -> tuple[EvidenceCollection, tuple[PreparedAddress, ...]]:
    """The historical judgment rows of the memory candidate, with their row and attempt identity."""

    names = _history_paths(record, limits)
    if names is None:
        return (
            EvidenceCollection(
                name=NAME_JUDGMENTS,
                producer=comparison_reference(record),
                population="the memory candidate's judgment history",
                state="unavailable",
                reason="the memory candidate's history listing did not answer at this tree",
            ),
            (),
        )
    items: list[dict[str, object]] = []
    addresses: list[PreparedAddress] = []
    for path in names[:_MAX_HISTORY_FILES]:
        receipt = read_bound_source(record, "memory-candidate", path, limits=limits)
        if receipt.state != "present" or not receipt.text:
            continue
        document = _json(receipt.text)
        rows = document.get("rows") if isinstance(document, dict) else None
        attempt = document.get("attempt") if isinstance(document, dict) else None
        attempt = attempt if isinstance(attempt, int) else 1
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            subject = str(row.get("subject") or "")
            items.append(
                {
                    "record_id": subject or None,
                    "row_id": str(row["id"]),
                    "attempt": attempt,
                    "path": path,
                    "side": "memory-candidate",
                    "tree": record.memory_candidate.tree,
                    "record_revision": row.get("revision"),
                    "state": "present",
                }
            )
            addresses.append(
                PreparedAddress(
                    address_id=f"judgment-row:{path}#{row['id']}@{attempt}",
                    kind="judgment-row",
                    reference=f"memory-candidate:{path}",
                    side="memory-candidate",
                    path=path,
                    record_id=subject or None,
                    record_revision=(
                        row.get("revision") if isinstance(row.get("revision"), int) else None
                    ),
                    row_id=str(row["id"]),
                    attempt=attempt,
                )
            )
    collection = EvidenceCollection(
        name=NAME_JUDGMENTS,
        producer=comparison_reference(record),
        population=f"the {len(names)} history file(s) at the memory candidate",
        state="present" if items else "measured-empty",
        count=len(items) if items else 0,
        reason=None if items else "the history files hold no judgment row",
        items=tuple(items),
    )
    return collection, tuple(addresses)


def _proof_collection(
    record: ReviewTreeComparisonRecord, limits: EvidenceLimits
) -> tuple[EvidenceCollection, tuple[PreparedAddress, ...]]:
    """The proof entries the memory candidate records, with execution left as it was recorded."""

    names = _sidecar_paths(record, limits)
    if names is None:
        return (
            EvidenceCollection(
                name=NAME_PROOFS,
                producer=comparison_reference(record),
                population="the memory candidate's proof sidecars",
                state="unavailable",
                reason="the memory candidate's onboarding listing did not answer at this tree",
            ),
            (),
        )
    items: list[dict[str, object]] = []
    addresses: list[PreparedAddress] = []
    for path in names[: limits.paths_per_packet]:
        receipt = read_bound_source(record, "memory-candidate", path, limits=limits)
        if receipt.state != "present" or not receipt.text:
            continue
        document = _json(receipt.text)
        proofs = document.get("proves") if isinstance(document, dict) else None
        for entry in proofs if isinstance(proofs, list) else ():
            if not isinstance(entry, dict) or not entry.get("invariant"):
                continue
            proof_id = str(entry.get("id") or f"{path}#{entry['invariant']}")
            items.append(
                {
                    "proof_id": proof_id,
                    "record_id": str(entry["invariant"]),
                    "path": path,
                    "side": "memory-candidate",
                    "tree": record.memory_candidate.tree,
                    "execution": str(entry.get("execution") or "unestablished"),
                    "state": "present",
                }
            )
            addresses.append(
                PreparedAddress(
                    address_id=f"proof-entry:{proof_id}",
                    kind="proof-entry",
                    reference=f"memory-candidate:{path}",
                    side="memory-candidate",
                    path=path,
                    record_id=str(entry["invariant"]),
                    proof_id=proof_id,
                    detail=f"the entry is recorded in {path}; its execution result is as recorded",
                )
            )
    collection = EvidenceCollection(
        name=NAME_PROOFS,
        producer=comparison_reference(record),
        population=f"the {len(names)} sidecar file(s) at the memory candidate",
        state="present" if items else "measured-empty",
        count=len(items) if items else 0,
        reason=None if items else "the sidecars record no proof entry",
        items=tuple(items),
    )
    return collection, tuple(addresses)


def _history_paths(record: ReviewTreeComparisonRecord, limits: EvidenceLimits) -> list[str] | None:
    return _listed(record, "knowledge/history", limits)


def _sidecar_paths(record: ReviewTreeComparisonRecord, limits: EvidenceLimits) -> list[str] | None:
    return _listed(record, "onboarding", limits)


def _listed(
    record: ReviewTreeComparisonRecord, prefix: str, limits: EvidenceLimits
) -> list[str] | None:
    """The memory candidate's file names under one prefix, or ``None`` when listing failed."""

    try:
        result = run_git(
            Path(record.memory_candidate.repository),
            ["ls-tree", "-r", "--name-only", "-z", record.memory_candidate.tree, "--", prefix],
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    names = [name for name in result.stdout.split("\0") if name.endswith(".json")]
    return names[: limits.paths_per_packet * 4]


def _json(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _claim_ids(claims: tuple[EvidenceClaim, ...], address: PreparedAddress) -> tuple[str, ...]:
    """Which claims of this preparation cite the address, by the identity it actually carries."""

    cited: list[str] = []
    for claim in claims:
        for basis in claim.bases:
            if _basis_names(basis, address):
                cited.append(claim.claim_id)
                break
    return tuple(cited)


def _basis_names(basis: ClaimBasis, address: PreparedAddress) -> bool:
    if basis.kind != address.kind:
        return False
    if address.row_id is not None and basis.row_id != address.row_id:
        return False
    if address.proof_id is not None and basis.proof_id != address.proof_id:
        return False
    if address.record_id is not None and basis.record_id not in {None, address.record_id}:
        return False
    return not (address.path is not None and basis.path not in {None, address.path})


def _limitations(
    record: ReviewTreeComparisonRecord, collections: tuple[EvidenceCollection, ...]
) -> tuple[str, ...]:
    """What this preparation could not establish, stated beside the packet (rule 10)."""

    found: list[str] = [
        "the packet describes this comparison only: an answer about another comparison, "
        "repository or task is not evidence about this one"
    ]
    for collection in collections:
        if collection.state == "not-selected":
            found.append(f"{collection.name}: not selected for this scope")
        elif collection.state != "present":
            found.append(
                f"{collection.name}: {collection.state}"
                + (f" ({collection.reason})" if collection.reason else "")
            )
    if record.converted_base is not None:
        found.append(
            "the memory base is read as the comparison's converted tree "
            f"{record.converted_base.tree}; the stored tree {record.memory_base.tree} is named "
            "beside it and is not what these reads examined"
        )
    return tuple(found)
