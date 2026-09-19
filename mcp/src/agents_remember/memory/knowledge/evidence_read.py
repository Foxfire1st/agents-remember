"""The evidence-specific selection: one seed, one complete aggregate, or one typed refusal.

``KS-R07@v1``'s recorded-scope selection and ``KS-R11@v1``'s facet selection are unchanged by this
leaf, and this module is why: the evidence selection is a **third** selection with its own declared
policy name, its own seeds and its own item stream, reachable from neither of the others. Nothing
here calls into a shipped selection, and no shipped selection calls into anything here, so a shipped
seed's serialized page stays byte-identical to what it was before this leaf because no code path is
shared.

The contract this selection declares:

* **One seed selects one bounded aggregate.** A claim seed selects that claim's envelope, every
  retained revision of it, its subject edge and every claimed-coverage edge -- so a caller reading a
  claim sees what it asserts and what it says it covers, together. A candidate seed selects every
  observation whose recorded tested candidate is the exact candidate named, each with its own
  retained revisions.
* **Complete or refused.** The whole aggregate is selected and the whole aggregate is served. A
  selection that reaches ``EVIDENCE_SELECTION_ITEM_LIMIT`` raises rather than emitting a partial page
  with a total that was never computed, and the caller converts that into the shipped
  ``selection_incomplete`` refusal. There is no cursor, so there is no continuation contract to bind
  and no position that could be read as a different selection.
* **Nothing is derived and nothing is judged.** An item's order is fixed by its kind and by stable
  identifiers, never by an authored label, an insertion order or a timestamp. Every retained revision
  is served as its own item. The execution result is served as the member that was recorded, and no
  field on a page is a verdict, a grade, a score or an aggregate status: the counts are the selected
  set's own arithmetic and nothing more.

The two absences a caller can tell apart are the shipped ones: a seed naming nothing recorded is
``selector_absent``, while a recorded claim with an empty claimed-coverage list is impossible by
construction and a claim with an empty assessment-reference list is a real page reporting the
unassessed state it really has.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge import evidence_records
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.evidence_read import (
    EVIDENCE_SELECTION_ITEM_LIMIT,
    EVIDENCE_SELECTION_POLICY_VERSION,
    ArtifactResolution,
    AssessmentReferenceState,
    EvidenceClaimItem,
    EvidenceClaimRevisionItem,
    EvidenceClaimSeed,
    EvidenceReadCounts,
    EvidenceReadItem,
    EvidenceReadSeed,
    ObservationCandidateSeed,
    VerificationObservationItem,
    VerificationObservationRevisionItem,
    evidence_item_id,
    evidence_item_sort_key,
)

# The selection's declared execution bound, named beside the reason so a refusal can say which bound
# it reached without restating the number.
ITEM_LIMIT = EVIDENCE_SELECTION_ITEM_LIMIT


class EvidenceSelectionIncomplete(ValueError):
    """The selected aggregate reached its declared execution bound before it was enumerated.

    It is an exception rather than a returned refusal because the caller is the read operation,
    which owns the operation name the refusal is reported under. The detail names both the bound and
    the count, so a caller is never told "too large" without being told what was too large.
    """

    def __init__(self, item_count: int, bound: int) -> None:
        super().__init__(
            f"the evidence selection reached its declared execution bound of {bound} items "
            f"({item_count} counted)"
        )
        self.item_count = item_count
        self.bound = bound


@dataclass(frozen=True)
class EvidenceSelectionQuery:
    """One evidence selection: the namespace it is read in, the seed it selects and its root."""

    repository_id: str
    seed: EvidenceReadSeed
    artifact_root: str | None = None


@dataclass(frozen=True)
class EvidenceSelection:
    """One selected evidence aggregate, as the shared tuple every count and the manifest derive from."""

    items: tuple[EvidenceReadItem, ...]
    counts: EvidenceReadCounts
    manifest_digest: str
    seed_recorded: bool

    @property
    def empty(self) -> bool:
        """Whether the selection holds no item at all."""

        return not self.items


def select_evidence_scope(
    connection: apsw.Connection, query: EvidenceSelectionQuery
) -> EvidenceSelection:
    """Select one evidence aggregate at one snapshot, completely or not at all.

    The connection is the caller's read-only handle, so this function issues ``SELECT`` statements
    and nothing else: a refusal leaves the file byte-identical because there is no statement that
    could change it.
    """

    if isinstance(query.seed, EvidenceClaimSeed):
        recorded, items = _claim_items(connection, query)
    else:
        recorded, items = _observation_items(connection, query)
    if len(items) > ITEM_LIMIT:
        raise EvidenceSelectionIncomplete(len(items), ITEM_LIMIT)
    ordered = tuple(sorted(items, key=evidence_item_sort_key))
    return EvidenceSelection(
        items=ordered,
        counts=_counts(ordered),
        manifest_digest=_manifest_digest(query.seed, ordered),
        seed_recorded=recorded,
    )


def _claim_items(
    connection: apsw.Connection, query: EvidenceSelectionQuery
) -> tuple[bool, tuple[EvidenceReadItem, ...]]:
    """Return one claim's whole aggregate: its envelope, its revisions, its subject and coverage.

    The three rows that make one claim readable are the envelope (its kind, lifecycle and author),
    the revision that carries its frozen payload, and the edge that names its subject. A claim whose
    envelope or subject edge is missing is *not recorded*, which is the shipped ``selector_absent``
    state rather than a partial page: serving a claim whose subject cannot be named would be a page a
    reader could mistake for a claim about nothing.
    """

    seed = query.seed
    if not isinstance(seed, EvidenceClaimSeed):  # pragma: no cover - the caller dispatches on it
        raise KnowledgeStorageError("a claim selection needs a claim seed")
    repository_id = query.repository_id
    claim = evidence_records.claim_record_at(connection, repository_id, seed.claim_id)
    ledger_row = _one(connection, evidence_records.CLAIM_BY_ID, (repository_id, seed.claim_id))
    subject = evidence_records.subject_of_claim_at(connection, repository_id, seed.claim_id)
    if claim is None or ledger_row is None or subject is None:
        return (False, ())
    coverage = tuple(
        evidence_records.decode_coverage_row(row)
        for row in _many(
            connection, evidence_records.COVERAGE_BY_CLAIM, (repository_id, seed.claim_id)
        )
    )
    items: list[EvidenceReadItem] = [
        EvidenceClaimItem(
            claim=claim,
            subject=subject,
            evidence_anchor_id=str(ledger_row[2]),
            coverage=coverage,
            assessments=_assessment_state(claim),
        )
    ]
    items.extend(
        EvidenceClaimRevisionItem(revision=revision)
        for revision in evidence_records.claim_revisions_at(
            connection, repository_id, seed.claim_id
        )
    )
    return (True, tuple(items))


def _assessment_state(claim: Any) -> AssessmentReferenceState:
    """Return the claim's recorded assessment-reference state, as a state and nothing more.

    This leaf does not implement an assessment and does not pre-shape one. What it can report is the
    *absence* of one: a claim that named no assessment reference is served as the explicit
    unassessed state, never defaulted to compatible, complete or satisfied. ``resolved`` is ``False``
    for every reference because the referent is owned by ``KS-R15@v1`` and is not checked here; when
    that leaf lands, this field's behaviour changes and no stored claim's meaning does.
    """

    references = tuple(claim.payload.assessment_refs)
    return AssessmentReferenceState(
        assessed=bool(references), references=references, resolved=False
    )


def _observation_items(
    connection: apsw.Connection, query: EvidenceSelectionQuery
) -> tuple[bool, tuple[EvidenceReadItem, ...]]:
    """Return every observation one candidate seed selected, with its artifact resolution fact."""

    seed = query.seed
    if not isinstance(seed, ObservationCandidateSeed):  # pragma: no cover - dispatch is on the seed
        raise KnowledgeStorageError("an observation selection needs a candidate seed")
    ids = _observation_ids(connection, query.repository_id, seed)
    if not ids:
        return (False, ())
    items: list[EvidenceReadItem] = []
    for observation_id in ids:
        row = _one(
            connection, evidence_records.OBSERVATION_BY_ID, (query.repository_id, observation_id)
        )
        if row is None:  # pragma: no cover - the identity came from that same table
            raise KnowledgeStorageError(f"observation {observation_id} is not stored")
        record = evidence_records.decode_observation_row(row)
        items.append(
            VerificationObservationItem(
                observation=record,
                artifact_resolution=_artifact_resolution(record, query.artifact_root),
            )
        )
        items.extend(
            VerificationObservationRevisionItem(revision=revision)
            for revision in evidence_records.observation_revisions_at(
                connection, query.repository_id, observation_id
            )
        )
    return (True, tuple(items))


def _observation_ids(
    connection: apsw.Connection, repository_id: str, seed: ObservationCandidateSeed
) -> tuple[str, ...]:
    """Return the observation identities one candidate seed selects, deduplicated and ordered."""

    selected: set[str] = set()
    if seed.knowledge_logical_digest is not None:
        for row in _many(
            connection,
            evidence_records.OBSERVATIONS_OF_SNAPSHOT,
            (repository_id, seed.knowledge_logical_digest),
        ):
            selected.add(str(row[0]))
    if seed.code_candidate_tree_id is not None:
        for row in _many(
            connection,
            evidence_records.OBSERVATIONS_OF_CODE_TREE,
            (repository_id, seed.code_candidate_tree_id),
        ):
            selected.add(str(row[0]))
    return tuple(sorted(selected))


# ---------------------------------------------------------------------------
# The artifact resolution fact. Four states, and the reader never invents one.


def _artifact_resolution(record: Any, artifact_root: str | None) -> ArtifactResolution | None:
    """Return what this read observed about one record's artifact reference, or ``None``.

    ``None`` means the record carries no artifact reference at all, which is a different fact from
    "the reference did not resolve". A reference is resolved only against a root the caller declared;
    without one the honest state is ``not_attempted``, and the record's own content is served exactly
    as it was written either way.
    """

    artifact = record.payload.result_artifact
    if artifact is None:
        return None
    if artifact_root is None:
        return _resolution(
            "not_attempted",
            artifact,
            detail=(
                "no artifact root was declared for this read, so the recorded path was not resolved "
                "against any checkout; the recorded digest and size are served as recorded"
            ),
        )
    root = Path(artifact_root)
    resolved = (root / artifact.path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return _resolution(
            "not_attempted",
            artifact,
            detail=(
                "the recorded path resolves outside the declared artifact root, so this read did not "
                "read bytes it cannot attribute to the repository the record describes"
            ),
        )
    if not resolved.is_file():
        return _resolution(
            "absent",
            artifact,
            detail="the recorded artifact path is not a file under the declared artifact root",
        )
    observed = resolved.read_bytes()
    digest = hashlib.sha256(observed).hexdigest()
    if digest == artifact.sha256 and len(observed) == artifact.size_bytes:
        return _resolution(
            "equal",
            artifact,
            detail="the bytes at the recorded path hash to the recorded digest and size",
            observed_sha256=digest,
            observed_size_bytes=len(observed),
        )
    return _resolution(
        "digest_mismatch",
        artifact,
        detail=(
            "the bytes at the recorded path do not hash to the recorded digest, or their size "
            "differs; the stored reference is reported as written and is never re-pinned"
        ),
        observed_sha256=digest,
        observed_size_bytes=len(observed),
    )


def _resolution(
    state: str,
    artifact: Any,
    *,
    detail: str,
    observed_sha256: str | None = None,
    observed_size_bytes: int | None = None,
) -> ArtifactResolution:
    return ArtifactResolution(
        state=state,  # type: ignore[arg-type]
        path=artifact.path,
        recorded_sha256=artifact.sha256,
        recorded_size_bytes=artifact.size_bytes,
        digest_checked_at_write=artifact.digest_checked_against_bytes,
        observed_sha256=observed_sha256,
        observed_size_bytes=observed_size_bytes,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# The derived half: counts and the manifest, both from the same item tuple the page is built from.


def _counts(items: Sequence[EvidenceReadItem]) -> EvidenceReadCounts:
    """Return the counted shape of one selected aggregate, derived from its own item tuple."""

    return EvidenceReadCounts(
        evidence_claims=sum(1 for item in items if isinstance(item, EvidenceClaimItem)),
        evidence_claim_revisions=sum(
            1 for item in items if isinstance(item, EvidenceClaimRevisionItem)
        ),
        verification_observations=sum(
            1 for item in items if isinstance(item, VerificationObservationItem)
        ),
        verification_observation_revisions=sum(
            1 for item in items if isinstance(item, VerificationObservationRevisionItem)
        ),
    )


def _manifest_digest(seed: EvidenceReadSeed, items: Sequence[EvidenceReadItem]) -> str:
    """Return the digest of one selected set: its policy, its seed and its exact item identities."""

    return sha256_digest(
        {
            "policy_version": EVIDENCE_SELECTION_POLICY_VERSION,
            "seed": seed.model_dump(mode="json"),
            "items": [{"kind": item.kind, "item_id": evidence_item_id(item)} for item in items],
        }
    )


def _one(
    connection: apsw.Connection, statement: str, parameters: Sequence[Any]
) -> tuple[Any, ...] | None:
    return next(iter(connection.execute(statement, tuple(parameters))), None)


def _many(
    connection: apsw.Connection, statement: str, parameters: Sequence[Any]
) -> tuple[tuple[Any, ...], ...]:
    return tuple(connection.execute(statement, tuple(parameters)))


# Every statement below is a ``SELECT`` on a read-only connection. Each is ordered by the item's own
# stable identifier rather than left to the storage engine, because the page's declared order is a
# property of this selection and not of how SQLite happened to answer.
_REVISIONS_OF_RECORD = (
    "SELECT * FROM record_revision WHERE repository_id = ? AND record_id = ? ORDER BY revision_id"
)

__all__ = [
    "ITEM_LIMIT",
    "EvidenceSelection",
    "EvidenceSelectionIncomplete",
    "EvidenceSelectionQuery",
    "select_evidence_scope",
]
