"""Assemble an explanation's evidence packet from one existing comparison (MIK-R102 rules 1, 2, 7, 10, 11).

The builder owns exactly the facts a *packet* owns: which comparison it describes, which memory-base
tree was compared, which collections preparation actually built, and which claims the presenter
makes with which bases. It reads nothing itself and starts nothing: the collections come from their
own producers (the review surface, the worklist owner, the judgment owner), and the side states come
from the comparison owner's reopen. That split is what keeps rule 13 true -- preparing an
explanation grants no read of a file that its producer did not admit, and no write at all.

Three refusals are the builder's own:

* a packet whose four sides are not the record's four sides is refused, because a comparison is
  identified by its trees and not by the number printed beside them (rule 1, rule 11);
* a collection that preparation skipped is recorded as ``not-selected`` or ``not-read`` with its
  reason, never as an empty list (rule 2);
* relabelling a packet's binding onto another comparison is impossible: the binding is copied from
  the record the packet was built for, and :func:`require_same_binding` refuses a second record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents_remember.models.knowledge.review_evidence import (
    EVIDENCE_COLLECTIONS,
    INTENDED_CLASSIFICATION,
    ClaimMap,
    EvidenceClaim,
    EvidenceCodeSide,
    EvidenceCollection,
    EvidenceComparisonBinding,
    EvidenceConvertedBase,
    EvidenceLimits,
    EvidenceMap,
    EvidenceMemorySide,
    EvidenceOmission,
    EvidencePacket,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    comparison_reference,
    planned_reference,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "COMPARISON_OWNER",
    "INTENDED_AUTHORITY",
    "INTENDED_REFERENCE_DIRECTORY",
    "INTENDED_SOURCE_ROOTS",
    "INTENDED_TASK_DOCUMENT",
    "PLANNED_OWNER",
    "PacketBindingMismatch",
    "build_evidence_packet",
    "comparison_binding",
    "comparison_reference",
    "intended_receipt_ids",
    "intended_source_root",
    "planned_reference",
    "retained_supplemental",
    "same_comparison",
    "unselected_collection",
]

#: The claim authority for behaviour the task is meant to produce (MIK-R102 v2 item 1; MIK-R189
#: rule 6). A planned page's claim that rests only on the task's own documents wears this one; the
#: model's spelling is the only spelling, so the packet and the claim cannot drift apart.
INTENDED_AUTHORITY: str = INTENDED_CLASSIFICATION
#: The reference folder a planned leaf writes before it starts, under the admitted ``notes/`` root:
#: ``notes/reference/<leaf>/`` (MIK-R189 rule 9).
INTENDED_REFERENCE_DIRECTORY = "notes/reference"
#: The leaf's own task document, admitted by its exact two names at the task root and never by the
#: root itself (MIK-R102 v2 item 2).
INTENDED_TASK_DOCUMENT = "task-document"
INTENDED_TASK_DOCUMENT_IDS: frozenset[str] = frozenset(
    {"task-document", "task-document-json", "task-document-md"}
)
#: The supplemental roots an intended claim's bases may be receipts of: a packet under
#: ``requirements/``, the leaf's task document, and a file under ``notes/reference/<leaf>/``.
#: ``DEVELOPER-DIRECTION.md`` is deliberately outside them: a decision reaches a page through the
#: packet that quotes it, never as a receipt of its own (MIK-R102 v2 item 2).
INTENDED_SOURCE_ROOTS: tuple[str, ...] = ("requirements", INTENDED_REFERENCE_DIRECTORY)


class PacketBindingMismatch(ValueError):
    """A packet was asked to describe a comparison other than the one it was built for."""


@dataclass(frozen=True)
class SideState:
    """One side's own state as the comparison owner measured it, and its reason when not available."""

    state: str
    detail: str | None = None
    index_state: str | None = None


def comparison_binding(  # noqa: PLR0913 -- one record's four sides, each with its own state
    record: ReviewTreeComparisonRecord,
    *,
    repository_id: str,
    code_base: SideState,
    code_candidate: SideState,
    memory_before: SideState,
    memory_after: SideState,
    recorded_at: str,
    owner: str = COMPARISON_OWNER,
    reference: str | None = None,
) -> EvidenceComparisonBinding:
    """Copy one record's four sides and conversion inputs into a packet binding (rule 1).

    The states are the ones the reopen owner measured; this function invents none of them, and a
    side it is not given a state for is a programming error rather than a silently available tree.
    """

    converted = (
        None
        if record.converted_base is None
        else EvidenceConvertedBase(
            commit=record.converted_base.commit,
            version=record.converted_base.version,
            code_commit=record.converted_base.code_commit,
            tree=record.converted_base.tree,
        )
    )
    return EvidenceComparisonBinding(
        comparison_owner=owner,
        repository_id=repository_id,
        task_id=record.task_id,
        leaf_id=record.leaf_id,
        comparison_number=record.number,
        comparison_reference=reference or comparison_reference(record),
        code_base=EvidenceCodeSide(
            side="base",
            repository=record.code_base.repository,
            tree=record.code_base.tree,
            commit=record.code_base.commit,
            ref=record.code_base.ref,
            state=code_base.state,  # type: ignore[arg-type]
            detail=code_base.detail,
        ),
        code_candidate=EvidenceCodeSide(
            side="candidate",
            repository=record.code_candidate.repository,
            tree=record.code_candidate.tree,
            commit=record.code_candidate.commit,
            ref=record.code_candidate.ref,
            state=code_candidate.state,  # type: ignore[arg-type]
            detail=code_candidate.detail,
        ),
        memory_base=EvidenceMemorySide(
            side="before",
            repository=record.memory_base.repository,
            tree=record.memory_base.tree,
            commit=record.memory_base.commit,
            ref=record.memory_base.ref,
            state=memory_before.state,  # type: ignore[arg-type]
            index_state=memory_before.index_state,  # type: ignore[arg-type]
            detail=memory_before.detail,
        ),
        memory_candidate=EvidenceMemorySide(
            side="after",
            repository=record.memory_candidate.repository,
            tree=record.memory_candidate.tree,
            commit=record.memory_candidate.commit,
            ref=record.memory_candidate.ref,
            state=memory_after.state,  # type: ignore[arg-type]
            index_state=memory_after.index_state,  # type: ignore[arg-type]
            detail=memory_after.detail,
        ),
        converted_base=converted,
        stored_memory_base_tree=record.memory_base.tree,
        compared_memory_base_tree=(
            record.converted_base.tree
            if record.converted_base is not None
            else record.memory_base.tree
        ),
        recorded_at=recorded_at,
    )


def unselected_collection(
    name: str,
    *,
    producer: str,
    population: str,
    reason: str,
    state: str = "not-selected",
) -> EvidenceCollection:
    """A named collection preparation did not build, with the reason it did not (rule 2).

    ``not-selected`` is a decision not to build it; ``not-read`` is a population that exists and was
    not read. Both are statements, and neither is an empty population.
    """

    return EvidenceCollection(
        name=name,
        producer=producer,
        population=population,
        state=state,  # type: ignore[arg-type]
        reason=reason,
    )


def _default_collections(
    supplied: Mapping[str, EvidenceCollection], reason: str
) -> tuple[EvidenceCollection, ...]:
    """Every named collection, with the ones preparation skipped recorded as not selected."""

    unknown = [name for name in supplied if name not in EVIDENCE_COLLECTIONS]
    if unknown:
        raise ValueError(
            "the packet names only the collections rule 2 defines; unknown: " + ", ".join(unknown)
        )
    return tuple(
        supplied.get(name)
        or unselected_collection(
            name,
            producer="agents_remember.application.review_evidence_packet",
            population="the collection rule 2 names",
            reason=reason,
        )
        for name in EVIDENCE_COLLECTIONS
    )


def _admit_populations(
    collections: Sequence[EvidenceCollection],
    claims: Sequence[EvidenceClaim],
    limits: EvidenceLimits,
) -> None:
    """Refuse a packet whose supplied populations exceed its own admitted bounds (rule 4).

    The model checks the same numbers; this check happens before the packet exists, so a caller
    learns which collection or path set broke the bound rather than only that the packet is invalid.
    """

    paths = {
        str(item["path"])
        for collection in collections
        for item in collection.items
        if isinstance(item.get("path"), str)
    } | {basis.path for claim in claims for basis in claim.bases if basis.path}
    if len(paths) > limits.paths_per_packet:
        raise ValueError(
            f"the packet would name {len(paths)} distinct path(s), beyond the admitted "
            f"{limits.paths_per_packet} per packet; select a narrower scope or page the evidence"
        )
    for collection in collections:
        if collection.count is not None and collection.count > limits.records_per_collection:
            raise ValueError(
                f"collection {collection.name!r} holds {collection.count} record(s), beyond the "
                f"admitted {limits.records_per_collection} per collection"
            )
    paths = {
        str(item["path"])
        for collection in collections
        for item in collection.items
        if isinstance(item.get("path"), str)
    } | {basis.path for claim in claims for basis in claim.bases if basis.path}
    if len(paths) > limits.paths_per_packet:
        raise ValueError(
            f"the packet would name {len(paths)} distinct path(s), beyond the admitted "
            f"{limits.paths_per_packet} per packet; select a narrower scope or page the evidence"
        )


def _omissions_for(
    collections: Sequence[EvidenceCollection],
    supplemental: Sequence[SupplementalReceipt] = (),
) -> tuple[EvidenceOmission, ...]:
    """The evidence map's own entry for every skipped collection or truncated receipt read."""

    collection_omissions = tuple(
        EvidenceOmission(
            item=collection.name,
            kind="not-selected-collection",
            detail=f"{collection.state}: {collection.reason}",
            witness=collection.producer,
        )
        for collection in collections
        if collection.state in {"not-selected", "not-read"}
    )
    receipt_omissions = tuple(
        EvidenceOmission(
            item=receipt.canonical_source,
            kind="truncated-read",
            detail=receipt.limitation or "supplemental source omitted at read bound",
            witness=receipt.receipt_id,
        )
        for receipt in supplemental
        if receipt.read_state == "unavailable" and "truncated-read" in (receipt.limitation or "")
    )
    return collection_omissions + receipt_omissions


def build_evidence_packet(  # noqa: PLR0913 -- one call states one packet's own inputs
    record: ReviewTreeComparisonRecord,
    *,
    repository_id: str,
    scope: str,
    question: str,
    code_base: SideState,
    code_candidate: SideState,
    memory_before: SideState,
    memory_after: SideState,
    evidence_map: EvidenceMap,
    limits: EvidenceLimits,
    collections: Mapping[str, EvidenceCollection] | None = None,
    supplemental: Sequence[SupplementalReceipt] = (),
    claims: Sequence[EvidenceClaim] = (),
    limitations: Sequence[str] = (),
    recorded_at: str,
    skipped_reason: str = "preparation selected no producer for this collection",
    comparison_owner: str = COMPARISON_OWNER,
    comparison_reference_value: str | None = None,
) -> EvidencePacket:
    """One comparison's packet, with every collection and claim accounted for.

    The evidence map is the caller's, because scope and omissions are judged facts. The omissions
    this builder adds are the mechanical ones: each collection preparation skipped. Everything else
    -- an unexamined sibling, a truncated read, a contradictory judgment -- is the caller's to name,
    and a caller that names none of them is saying it found none.
    """

    built = _default_collections(collections or {}, skipped_reason)
    _admit_populations(built, claims, limits)
    named = evidence_map.omissions + _omissions_for(built, supplemental)
    return EvidencePacket(
        comparison=comparison_binding(
            record,
            repository_id=repository_id,
            code_base=code_base,
            code_candidate=code_candidate,
            memory_before=memory_before,
            memory_after=memory_after,
            recorded_at=recorded_at,
            owner=comparison_owner,
            reference=comparison_reference_value,
        ),
        scope=scope,
        question=question,
        collections=built,
        supplemental=tuple(supplemental),
        claims=ClaimMap(claims=tuple(claims)),
        evidence_map=EvidenceMap(
            included=evidence_map.included,
            omissions=named,
            counts=evidence_map.counts,
            edge_meanings=evidence_map.edge_meanings,
            coverage=evidence_map.coverage,
        ),
        limits=limits,
        limitations=tuple(limitations),
    )


def same_comparison(packet: EvidencePacket, record: ReviewTreeComparisonRecord) -> bool:
    """Whether the packet describes exactly this comparison's full identity (rules 1, 11).

    The identity is the owner's own: task, leaf and number, each side's repository, tree, commit and
    ref, and the complete conversion record. Two rows with equal tree ids in different repositories,
    or with a different converted base, are different comparisons; the number alone is not an
    identity, and equal content hashes do not make one.
    """

    comparison = packet.comparison
    if (comparison.task_id, comparison.leaf_id, comparison.comparison_number) != (
        record.task_id,
        record.leaf_id,
        record.number,
    ):
        return False
    sides = (
        (comparison.code_base, record.code_base),
        (comparison.code_candidate, record.code_candidate),
        (comparison.memory_base, record.memory_base),
        (comparison.memory_candidate, record.memory_candidate),
    )
    for held, owned in sides:
        if (held.repository, held.tree, held.commit, held.ref) != (
            owned.repository,
            owned.tree,
            owned.commit,
            owned.ref,
        ):
            return False
    converted = comparison.converted_base
    if (converted is None) != (record.converted_base is None):
        return False
    if (
        converted is not None
        and record.converted_base is not None
        and (
            converted.commit,
            converted.version,
            converted.code_commit,
            converted.tree,
        )
        != (
            record.converted_base.commit,
            record.converted_base.version,
            record.converted_base.code_commit,
            record.converted_base.tree,
        )
    ):
        return False
    return (
        comparison.stored_memory_base_tree == record.memory_base.tree
        and comparison.compared_memory_base_tree
        == (record.converted_base.tree if record.converted_base else record.memory_base.tree)
    )


def require_same_binding(packet: EvidencePacket, record: ReviewTreeComparisonRecord) -> None:
    """Refuse to read a packet as evidence of another comparison (rule 11).

    Movement -- code-only or memory-only -- never relabels old inputs as the new comparison's
    evidence. The comparison number alone is not the identity; the four sides' repositories, trees,
    commits and refs, and the conversion inputs, are.
    """

    if not same_comparison(packet, record):
        raise PacketBindingMismatch(
            "the packet describes comparison "
            f"{packet.comparison.comparison_reference} with its own four sides and conversion; the "
            f"record asked about is {comparison_reference(record)} with a different identity, and a "
            "moved comparison is never relabelled onto the older packet's evidence"
        )


def retained_supplemental(packet: EvidencePacket) -> tuple[SupplementalReceipt, ...]:
    """The receipts a packet keeps, exactly as captured: never refreshed in place (rule 6)."""

    return packet.supplemental


def intended_source_root(
    canonical_source: str, *, leaf_id: str, leaf_document: str | None = None
) -> str | None:
    """Which admitted root an intended claim's receipt may come from, or ``None`` (item 1).

    Exactly three kinds of source establish an intention (MIK-R189 rule 6):

    * a packet file under ``requirements/``;
    * the leaf's own task document -- ``<leaf_document>.json`` and ``.md`` at the task root, or the
      names built from ``leaf_id`` when the caller does not name the document's stem;
    * a file under ``notes/reference/<leaf_id>/``.

    Everything else answers ``None``, and ``DEVELOPER-DIRECTION.md`` answers ``None`` explicitly: it
    sits at the task root beside the document and is not admitted, because a decision reaches a page
    through the packet that quotes it (MIK-R102 v2 item 2). A path that is absolute, carries a
    ``..`` or ``.`` segment, or a backslash is refused here too, so the predicate and the receipt
    admission agree about what a task-relative path is.
    """

    source = canonical_source.strip()
    if not source or source.startswith("/") or "\\" in source:
        return None
    segments = source.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    if segments[0] == "requirements" and len(segments) > 1:
        return "requirements"
    if (
        len(segments) > 2
        and segments[0] == "notes"
        and segments[1] == "reference"
        and segments[2] == leaf_id
    ):
        return f"{INTENDED_REFERENCE_DIRECTORY}/{leaf_id}"
    if len(segments) == 1 and source.endswith((".json", ".md")):
        stem = source.rsplit(".", 1)[0]
        if stem == leaf_id or (leaf_document is not None and stem == leaf_document):
            return INTENDED_TASK_DOCUMENT
    return None


def intended_receipt_ids(
    receipts: Sequence[SupplementalReceipt],
    *,
    leaf_id: str,
    leaf_document: str | None = None,
) -> frozenset[str]:
    """The receipts whose source is one of the three intended roots, by receipt id (item 1).

    Only a ``present`` receipt names bytes that were examined, so only it can establish anything.
    The leaf's own document receipt is also recognised by its id, ``task-document``: the receipt
    composer names the leaf's own document exactly that, and admission has already refused another
    leaf's document under the leaf's own name.
    """

    admitted: set[str] = set()
    for receipt in receipts:
        if receipt.read_state != "present":
            continue
        root = intended_source_root(
            receipt.canonical_source, leaf_id=leaf_id, leaf_document=leaf_document
        )
        named_document = (
            receipt.receipt_id in INTENDED_TASK_DOCUMENT_IDS
            and "/" not in receipt.canonical_source
            and receipt.canonical_source.endswith((".json", ".md"))
        )
        if root is not None or named_document:
            admitted.add(receipt.receipt_id)
    return frozenset(admitted)
