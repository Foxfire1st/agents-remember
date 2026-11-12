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

Reading is the read of :mod:`review_evidence_read`, so the adapter inherits its exact-tree operand,
its full answers and its distinct absent/binary/unavailable states; every collection carries every
record it counts (MIK-R211 v4 rules 1-2). It starts no
role, launches no page and writes nothing: it prepares evidence for a consumer that already exists.

One refusal belongs to the *binding* rather than to the claim model: on a planned comparison, a
claim resting only on receipts of the leaf's packets, task document or reference files wears the
``intended`` authority, and :func:`require_intended_authority` refuses ``recorded-fact`` or
``mechanical-signal`` for it (MIK-R102 v2 item 1; MIK-R189 rule 6). Another binding is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.application import review_evidence_map as evidence_mapping
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    SideState,
    comparison_reference,
    intended_receipt_ids,
    same_comparison,
)
from agents_remember.application.review_evidence_read import (
    read_bound_source,
    read_whole_source,
)
from agents_remember.application.review_evidence_relations import read_bound_relationships
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceCollection,
    EvidenceMap,
    EvidencePacket,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
)

__all__ = [
    "IntendedAuthorityRefusal",
    "PreparedAddress",
    "PreparedEvidence",
    "consume_address",
    "prepare_evidence",
    "require_intended_authority",
    "require_intended_packet",
]


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


class EvidencePrecondition(ValueError):
    """A preparation that cannot be made from what the comparison owner measured.

    It carries the reason a build or an Update shows -- ``evidence-source-unavailable`` -- so an
    absent input is a named refusal at the step that needed it, never a server error (MIK-R185 v2
    rule 3).
    """

    reason = "evidence-source-unavailable"


class IntendedAuthorityRefusal(EvidencePrecondition):
    """A planned binding's claim was dressed in an authority its bases cannot earn (rule 7).

    On a page bound to the planned comparison, a claim resting *only* on receipts of the leaf's own
    packets, task document or reference files says what the task is meant to produce. That is the
    ``intended`` authority; ``recorded-fact`` and ``mechanical-signal`` would read as facts about
    code that does not exist yet (MIK-R102 v2 item 1; MIK-R189 rule 6). The refusal is named, so a
    caller answers with it rather than with a server error.
    """

    reason = "intended-claim-required"


def require_intended_authority(
    claims: tuple[EvidenceClaim, ...],
    *,
    supplemental: tuple[SupplementalReceipt, ...],
    comparison_owner: str,
    leaf_id: str,
    leaf_document: str | None = None,
) -> None:
    """Refuse a planned binding's claim that rests only on the task's own receipts (rule 7).

    The rule is about the *binding*, so it runs here, where a revision's binding and its claim map
    meet: the claim model cannot see the owner, and another binding's page must keep the kinds it
    has always had. A claim whose present bases are all ``supplemental-receipt`` bases of an
    admitted intended root must be classified ``intended``; a claim that also cites a tree blob or a
    knowledge record at the master tip keeps today's kinds, because that citation is a fact about
    code or memory that exists. An unread basis establishes nothing and is not a citation, so it is
    not counted here either.
    """

    if comparison_owner != PLANNED_OWNER:
        return
    admitted = intended_receipt_ids(supplemental, leaf_id=leaf_id, leaf_document=leaf_document)
    if not admitted:
        return
    for claim in claims:
        if claim.classification not in {"recorded-fact", "mechanical-signal"}:
            continue
        present = [basis for basis in claim.bases if basis.state == "present"]
        if not present:
            continue
        if all(
            basis.kind == "supplemental-receipt" and basis.reference in admitted
            for basis in present
        ):
            raise IntendedAuthorityRefusal(
                f"on the planned comparison a claim resting only on packet, task-document or "
                f"reference receipts is intended, not {claim.classification}: claim "
                f"{claim.claim_id!r} cites "
                f"{', '.join(sorted({str(basis.reference) for basis in present}))} and nothing at "
                "the master tip, so it states behaviour the task is meant to produce rather than a "
                "fact about the code"
            )


def require_intended_packet(
    packet: EvidencePacket,
    *,
    comparison_owner: str,
    leaf_id: str,
    leaf_document: str | None = None,
) -> None:
    """The same refusal for an already-built packet, at the publication seam (rule 7)."""

    require_intended_authority(
        packet.claims.claims,
        supplemental=packet.supplemental,
        comparison_owner=comparison_owner,
        leaf_id=leaf_id,
        leaf_document=leaf_document,
    )


def prepare_evidence(  # noqa: PLR0913 - one preparation states its own scope and payload
    trees: ReviewTrees,
    *,
    scope: str,
    question: str,
    evidence_map: EvidenceMap,
    claims: tuple[EvidenceClaim, ...] = (),
    paths: tuple[str, ...] = (),
    family_path: str | None = None,
    supplemental: tuple[SupplementalReceipt, ...] = (),
    comparison_owner: str = COMPARISON_OWNER,
    comparison_reference: str | None = None,
    leaf_document: str | None = None,
) -> PreparedEvidence:
    """Prepare from the comparison owner's opened sides, preserving its measured index state.

    ``leaf_document`` is the leaf's own task-document stem, when the caller knows it: a planned
    binding's intended claims may rest on that document's own bytes, and this is how their receipt
    is recognised (MIK-R102 v2 item 1).
    """

    record = trees.record
    before = _memory_state(trees.before.wire)
    after = _memory_state(trees.after.wire)
    states = {side.side: SideState(side.state, side.detail) for side in trees.code_sides}
    require_intended_authority(
        claims,
        supplemental=supplemental,
        comparison_owner=comparison_owner,
        leaf_id=record.leaf_id,
        leaf_document=leaf_document,
    )
    if set(states) != {"base", "candidate"}:
        raise EvidencePrecondition(
            "preparation requires the comparison owner's measured code sides; a comparison with "
            "no measured side cannot be explained, and nothing is substituted for it"
        )
    # Ruling 6085456d, addendum (c): a scope is never refused for naming many paths. The count is
    # measured by the collections that carry it; it does not decide what preparation admits.
    sources = _source_collection(record, paths)
    family = _family_collection(record, family_path)
    # Every collection reads what its own files hold and carries all of it (ruling 6085456d,
    # addendum c; MIK-R211 v4 rules 1-2).
    judgments, addresses = _judgment_collections(record, owner=comparison_owner)
    proofs, proof_addresses = _proof_collection(record, owner=comparison_owner)
    # The map is built from the rows and the index this preparation already read, so it adds no read
    # of its own; a comparison whose memory index cannot be opened has no map, and says so through
    # the sides it states rather than through a map of nothing (MIK-R211 v2 rule 1).
    groups = evidence_mapping._group_map(
        trees,
        rows=judgments.items,
        supplemental=supplemental,
        owner=comparison_owner,
    )
    collections = (sources, family, judgments, proofs)
    packet = evidence_mapping._fit_packet(
        record,
        collections=collections,
        groups=groups,
        sides={
            "base": states["base"],
            "candidate": states["candidate"],
            "before": before,
            "after": after,
        },
        scope=scope,
        question=question,
        evidence_map=evidence_map,
        claims=claims,
        supplemental=supplemental,
        comparison_owner=comparison_owner,
        comparison_reference=comparison_reference,
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
    """One side's measured state, with a partial index stated by count and every path it failed.

    A real memory repository can leave thousands of files unreadable to the index. The detail names
    each of them (MIK-R211 v4 rule 1): the count is the measurement and the paths are the evidence,
    and no prose bound stands between the reader and either.
    """

    detail = side.detail
    if side.index_state == "partial":
        problems = side.problems or ()
        named = ", ".join(path for path, _ in problems)
        detail = (
            f"the {side.side} memory index at {side.tree} is partial: {len(problems)} file(s) "
            f"failed ({named}); valid records remain usable, but absence of an omitted record is "
            "unknown"
        )
    return SideState(state=side.state, index_state=side.index_state, detail=detail)


def consume_address(
    record: ReviewTreeComparisonRecord, prepared: PreparedEvidence, address_id: str
) -> AddressRead:
    """Resolve one prepared address for *this* comparison, through the same read the packet used.

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
        "judgment-row": (evidence_mapping.NAME_JUDGMENTS,),
        "history-event": (evidence_mapping.NAME_JUDGMENTS,),
        "proof-entry": (evidence_mapping.NAME_PROOFS,),
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
    record: ReviewTreeComparisonRecord, paths: tuple[str, ...]
) -> EvidenceCollection:
    """The supplied paths, each read at the memory candidate, with its own state kept."""

    if not paths:
        return EvidenceCollection(
            name=evidence_mapping.NAME_SOURCES,
            producer=comparison_reference(record),
            population="the scope named no source path",
            state="not-selected",
            reason="this preparation was asked to explain the comparison without naming source paths",
        )
    items: list[dict[str, object]] = []
    states: list[str] = []
    for path in paths:
        receipt = read_bound_source(record, "memory-candidate", path)
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
            name=evidence_mapping.NAME_SOURCES,
            producer=comparison_reference(record),
            population=f"the {len(paths)} source path(s) named for this comparison",
            state="present",
            count=len(items),
            items=tuple(items),
        )
    return EvidenceCollection(
        name=evidence_mapping.NAME_SOURCES,
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
    record: ReviewTreeComparisonRecord, family_path: str | None
) -> EvidenceCollection:
    """One family's recorded membership and links, followed at its exact tree (rule 5)."""

    if family_path is None:
        return EvidenceCollection(
            name=evidence_mapping.NAME_FAMILY,
            producer=comparison_reference(record),
            population="no family was named",
            state="not-selected",
            reason="the explanation's scope names no family to read",
        )
    relations = read_bound_relationships(record, "memory-candidate", family_path)
    if relations.state != "records":
        return EvidenceCollection(
            name=evidence_mapping.NAME_FAMILY,
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
        name=evidence_mapping.NAME_FAMILY,
        producer=comparison_reference(record),
        population=f"the family at {family_path} and the records it names",
        state="present" if items else "measured-empty",
        count=len(items) if items else 0,
        reason=None if items else "the family records no member, link or owner row",
        items=items,
    )


def _judgment_collections(
    record: ReviewTreeComparisonRecord, *, owner: str
) -> tuple[EvidenceCollection, tuple[PreparedAddress, ...]]:
    """The judgment rows of the history files the comparison changed (rule 1, rule 2).

    A planned binding has no changed memory side to read, so its rows are the leaf's own history
    file's -- the leaf's recorded judgments about its own change -- and a leaf without one does not
    get another task's file. A recorded binding reads the history files the comparison *changed*,
    each to its end so the rows it holds are counted rather than sampled, the leaf's own file first
    and the changed invariants' rows before the rest. The collection carries every row in that one
    order (MIK-R211 v4 rules 1-2).
    """

    planned = owner == PLANNED_OWNER
    leaf_path = f"knowledge/history/{record.leaf_id}.json"
    if planned:
        names: list[str] | None = [leaf_path]
        scope = f"the leaf's own history file {leaf_path}"
    else:
        names = evidence_mapping._changed_memory_paths(record, "knowledge/history")
        scope = (
            "the history files the comparison changed at the memory candidate"
            if names is not None
            else "the comparison's changed history files"
        )
        if names is not None:
            names = [leaf_path] + [name for name in names if name != leaf_path]
    if names is None:
        return (
            EvidenceCollection(
                name=evidence_mapping.NAME_JUDGMENTS,
                producer=comparison_reference(record),
                population=scope,
                state="unavailable",
                reason="the comparison's changed history files could not be listed at this tree",
            ),
            (),
        )
    rows, found = _read_history_rows(record, names, planned=planned)
    if not planned and not names:
        return (
            EvidenceCollection(
                name=evidence_mapping.NAME_JUDGMENTS,
                producer=comparison_reference(record),
                population=scope,
                state="measured-empty",
                count=0,
                reason="the comparison changed no history file, so it records no judgment row",
            ),
            (),
        )
    if planned and not found:
        return (
            EvidenceCollection(
                name=evidence_mapping.NAME_JUDGMENTS,
                producer=comparison_reference(record),
                population=scope,
                state="not-selected",
                reason=(
                    f"the leaf records no knowledge/history/{record.leaf_id}.json at this tree, so "
                    "a planned binding has no judgment file of its own to read and another task's "
                    "history is not read in its place"
                ),
            ),
            (),
        )
    ordered = _admitted_rows(record, rows)
    items = tuple(item for item, _address in ordered)
    collection = EvidenceCollection(
        name=evidence_mapping.NAME_JUDGMENTS,
        producer=comparison_reference(record),
        population=(
            f"{scope}: {len(rows)} judgment row(s) in {len({row.path for row in rows})} file(s)"
            if rows
            else scope
        ),
        state="present" if items else "measured-empty",
        count=len(rows) if rows else 0,
        reason=None if items else "the history files hold no judgment row",
        items=items,
    )
    return collection, tuple(address for _item, address in ordered)


@dataclass(frozen=True)
class _HistoryRow:
    """One recorded judgment row, with the file and attempt that give it its identity."""

    path: str
    attempt: int
    row: dict[str, object]


def _read_history_rows(
    record: ReviewTreeComparisonRecord, names: list[str], *, planned: bool
) -> tuple[list[_HistoryRow], bool]:
    """Every row of every named history file, each file read whole, in the one stated order.

    ``found`` says whether any named file was readable at all, which is what separates a planned
    leaf with no history file of its own from a planned leaf whose file is merely empty.
    """

    rows: list[_HistoryRow] = []
    for path in names:
        receipt = read_whole_source(record, "memory-candidate", path)
        if receipt.state != "present" or not receipt.text:
            if receipt.state == "absent" and planned:
                return [], False
            continue
        document = evidence_mapping._json(receipt.text)
        recorded = document.get("rows") if isinstance(document, dict) else None
        attempt = document.get("attempt") if isinstance(document, dict) else None
        attempt = attempt if isinstance(attempt, int) else 1
        if not isinstance(recorded, list):
            continue
        rows.extend(
            _HistoryRow(path=path, attempt=attempt, row=row)
            for row in recorded
            if isinstance(row, dict) and row.get("id")
        )
    return rows, bool(names)


def _admitted_rows(
    record: ReviewTreeComparisonRecord, rows: list[_HistoryRow]
) -> list[tuple[dict[str, object], PreparedAddress]]:
    """Every row this packet carries, in one stated order (MIK-R211 v4 rules 1-2).

    The order is stated in the requirement: the leaf's own history file first, then the rows of the
    invariants the comparison changed, then every other row in the order its file records. Each
    carried row keeps its row id, attempt, path and revision, which is the address a consumer opens.
    """

    changed = evidence_mapping._changed_invariant_ids(record)
    ordered = sorted(
        rows,
        key=lambda row: (
            0 if row.path == f"knowledge/history/{record.leaf_id}.json" else 1,
            0 if evidence_mapping._names_changed_invariant(row.row, changed) else 1,
        ),
    )
    admitted: list[tuple[dict[str, object], PreparedAddress]] = []
    for row in ordered:
        subject = str(row.row.get("subject") or "") or None
        revision = row.row.get("revision")
        row_id = str(row.row["id"])
        admitted.append(
            (
                {
                    "record_id": subject,
                    "row_id": row_id,
                    "attempt": row.attempt,
                    "path": row.path,
                    "side": "memory-candidate",
                    "tree": record.memory_candidate.tree,
                    "record_revision": revision,
                    "state": "present",
                },
                PreparedAddress(
                    address_id=f"judgment-row:{row.path}#{row_id}@{row.attempt}",
                    kind="judgment-row",
                    reference=f"memory-candidate:{row.path}",
                    side="memory-candidate",
                    path=row.path,
                    record_id=subject,
                    record_revision=revision if isinstance(revision, int) else None,
                    row_id=row_id,
                    attempt=row.attempt,
                ),
            )
        )
    return admitted


def _proof_collection(
    record: ReviewTreeComparisonRecord, *, owner: str
) -> tuple[EvidenceCollection, tuple[PreparedAddress, ...]]:
    """The proof entries of the sidecars the comparison changed (rule 1, rule 2).

    A planned binding changed no memory side, so it has no changed sidecar to read and says so
    rather than reading another task's proofs. A recorded binding reads every changed sidecar whole
    and in path order, with no count stopping it (ruling 6085456d, addendum c).
    """

    planned = owner == PLANNED_OWNER
    if planned:
        return (
            EvidenceCollection(
                name=evidence_mapping.NAME_PROOFS,
                producer=comparison_reference(record),
                population="the sidecars the comparison changed",
                state="measured-empty",
                count=0,
                reason="a planned binding compares the leaf against itself, so it changed no sidecar",
            ),
            (),
        )
    names = evidence_mapping._changed_memory_paths(record, "onboarding")
    if names is None:
        return (
            EvidenceCollection(
                name=evidence_mapping.NAME_PROOFS,
                producer=comparison_reference(record),
                population="the sidecars the comparison changed",
                state="unavailable",
                reason="the comparison's changed sidecars could not be listed at this tree",
            ),
            (),
        )
    if not names:
        return (
            EvidenceCollection(
                name=evidence_mapping.NAME_PROOFS,
                producer=comparison_reference(record),
                population="the sidecars the comparison changed",
                state="measured-empty",
                count=0,
                reason="the comparison changed no onboarding sidecar",
            ),
            (),
        )
    items: list[dict[str, object]] = []
    addresses: list[PreparedAddress] = []
    for path in names:
        receipt = read_whole_source(record, "memory-candidate", path)
        if receipt.state != "present" or not receipt.text:
            continue
        document = evidence_mapping._json(receipt.text)
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
        name=evidence_mapping.NAME_PROOFS,
        producer=comparison_reference(record),
        population=(
            f"the {len(names)} sidecar file(s) the comparison changed, each read whole: "
            f"{len(items)} proof entr(ies)"
        ),
        state="present" if items else "measured-empty",
        count=len(items),
        reason=None if items else "the changed sidecars record no proof entry",
        items=tuple(items),
    )
    return collection, tuple(addresses)


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
