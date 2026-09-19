"""The truth-coverage census's frozen record vocabulary: the inventory row, the claim, the disposition.

This module owns the three census record kinds' payload shapes and nothing else. It is a
:mod:`agents_remember.models` module because these shapes cross the wire: a census row is read back by
the census's own reporting surface and by the reviewer surface that presents it.

Three properties are load-bearing and each is a property of the declared fields:

* **There is no field that could hold an inference.** No classification the parser could have
  computed, no verdict, no mismatch class, no derived status. ``claim_kind`` admits ``unclassified``
  and the four kinds ``Doc12:65-70`` closes the taxonomy at; ``disposition`` admits ``imported``,
  ``unmapped``, ``unsupported``, ``unreadable``, ``retired``, ``historical`` and ``non_claim``. None
  of those is a verdict about whether a claim is true, and ``extra="forbid"`` on the shared base is
  what refuses a payload arriving with one. A semantic status such as *supported*, *contradicted* or
  *unresolved* is a curator's authored :class:`…ReviewAssessment`, read by the census and never
  written by it.
* **Provenance is a required stored value, not a report note.** ``CensusProvenance`` carries the
  artifact, the location within it and the frozen baseline the artifact was read at, and every one of
  the three records requires it. A record that could omit its provenance is a record whose claim is
  no longer addressable to its original text.
* **No record carries an identity of its own beyond its key.** There is no content address, no
  logical digest and no fingerprint field here, and the schema declares no such column: the census
  mints no second identity authority, and the content digest stays on ``record_revision`` where the
  record envelope puts it.

``CensusProvenance.baseline`` is a :class:`…SnapshotIdentity` rather than two loose strings because
the frozen baseline *is* one: the packet requires "an exact code revision and an exact memory
revision, recorded by identity", and ``SnapshotIdentity`` is the shipped shape that records exactly
that pair. Two artifacts examined at two baselines are two observations, which is why the baseline is
part of the record rather than a parameter of the run that produced it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity

# The three stable values this record group declares in the envelope's typed kind vocabulary, and the
# one frozen shape each resolves to. Each pair is the registry's key, like every other record
# group's. The names are this implementation's reading of ``Doc12:49-55`` rather than quotations of
# doc-defined type names, which the packet records as a fact about itself.
CENSUS_INVENTORY_ROW_KIND = "census_inventory_row"
CENSUS_INVENTORY_ROW_SCHEMA = "census-inventory-row/v1"
CENSUS_CLAIM_KIND = "census_claim"
CENSUS_CLAIM_SCHEMA = "census-claim/v1"
CENSUS_DISPOSITION_KIND = "census_disposition"
CENSUS_DISPOSITION_SCHEMA = "census-disposition/v1"

# The closed vocabularies, imported from the generation module that renders the same values into the
# DDL's CHECK constraints. One declaration, two consumers: a value added here is a value the schema
# already admits, and a value the schema refuses is a value this model refuses.
CensusArtifactKind = Literal["file_level_onboarding", "route_local_overview", "other"]
CensusParseOutcome = Literal["parsed", "unparsed", "unsupported", "unreadable"]
CensusInventoryState = Literal["present", "absent", "unreadable"]
CensusClaimKind = Literal[
    "unclassified",
    "current_behavior",
    "accepted_invariant",
    "historical_rationale",
    "realization_attribution",
]
CensusApplicability = Literal["assessable", "non_claim", "historical_non_applicable"]
CensusDispositionKind = Literal[
    "imported",
    "unmapped",
    "unsupported",
    "unreadable",
    "retired",
    "historical",
    "non_claim",
]
CensusDispositionState = Literal["recorded", "applied"]
CensusEvidenceState = Literal["unassessed", "assessed", "unresolved"]
CensusAssessmentDisposition = Literal["no_concern_found", "concern_found", "unresolved"]
CensusRealizationState = Literal["unattributed", "attributed", "missing_realization"]
CensusLinkKind = Literal["split_into", "combined_with", "retired", "corrected_by"]
CensusTargetState = Literal["resolved", "unresolved", "ambiguous"]

# ``Doc12:65-70``'s taxonomy as the four kinds the taxonomy actually names, in the doc's own order.
# ``unclassified`` is deliberately not a member: it is the *absence* of a kind, and a caller that
# wanted to hand back "one of the four" would otherwise be handed the state that means none of them.
CLASSIFIED_CLAIM_KINDS: tuple[str, ...] = (
    "current_behavior",
    "accepted_invariant",
    "historical_rationale",
    "realization_attribution",
)

# The one applicability value that enters the claim cohort ``N``. Every other value is a piece the
# census records and accounts for elsewhere, which is the eligibility rule the packet requires be
# stated so that ``Doc12``'s Example 3 and requirement 5.2 read alike.
COHORT_APPLICABILITY: CensusApplicability = "assessable"


class CensusProvenance(KnowledgeModel):
    """One census record's stored provenance: the artifact, the location in it, and the baseline.

    ``artifact_path`` is repository-relative and is stored verbatim: it is the observed path, so
    normalising it would be the census editing its own evidence. ``location`` is the address within
    the artifact -- a line for a claim, a section heading for a piece of a route overview -- and it is
    what makes a claim addressable to its original text.

    ``baseline`` is the frozen baseline this observation belongs to. It is required rather than
    optional because an observation with no baseline is an observation against a moving target.
    """

    artifact_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    location: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    baseline: SnapshotIdentity
    observed_content_digest: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    author: Authorship | None = None

    @model_validator(mode="after")
    def _require_relative_artifact_path(self) -> CensusProvenance:
        """Refuse an artifact path that is not the repository-relative spelling the corpus uses.

        The corpus addresses artifacts repository-relative, so an absolute path or a traversal form
        is not a second spelling of one artifact -- it is a path this census never observed, and
        admitting it would put a machine location in a durable record.
        """

        path = self.artifact_path
        if path.startswith("/") or "\\" in path:
            raise ValueError(
                "an artifact path is repository-relative; an absolute or backslash spelling is not "
                "an artifact this census observed"
            )
        if any(segment in ("", ".", "..") for segment in path.split("/")):
            raise ValueError(
                "an artifact path must not carry an empty, dot or traversal segment; a path that "
                "does is not a confined reading of the corpus"
            )
        return self


class CensusInventoryRowPayload(KnowledgeModel):
    """One in-scope artifact's inventory row, with the outcome of examining it.

    ``declared_source_path`` is what the artifact *claims* to describe and ``inventory_state`` says
    whether that source is present at the baseline. The pair is the reason a corpus-derived
    inventory cannot see the files with no card: this row exists for an artifact whether or not the
    artifact names a source, and a route with **no onboarding** is an ``inventory_state="absent"``
    row rather than an omission.

    ``unparsed_content`` is the exact observed content the parser could not interpret. It is stored
    on the row rather than reported in a side channel because a silent skip is indistinguishable
    from coverage: a reader must be able to see *what* was not read, not only that something was not.
    """

    inventory_row_kind: Literal["census_inventory_row"] = "census_inventory_row"
    artifact_path: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    artifact_kind: CensusArtifactKind
    declared_source_path: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed_doc_type: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    observed_route_path: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    outcome: CensusParseOutcome
    unparsed_content: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    source_route_path: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    inventory_state: CensusInventoryState
    provenance: CensusProvenance

    @model_validator(mode="after")
    def _require_unparsed_content_with_a_failed_parse(self) -> CensusInventoryRowPayload:
        """An artifact that did not parse reports what was not parsed; one that did reports nothing.

        This is the guard that keeps requirement 1.4's "rows with an outcome, never skipped files"
        from degrading into an outcome with no evidence: a row saying ``unreadable`` and carrying
        nothing is a row indistinguishable from a row that was never examined.
        """

        if self.outcome == "parsed" and self.unparsed_content is not None:
            raise ValueError(
                "an artifact that parsed carries no unparsed content; a stored unparsed remainder "
                "on a parsed artifact would report a section as unread that was read"
            )
        if self.outcome != "parsed" and not (self.unparsed_content or "").strip():
            raise ValueError(
                "an artifact whose outcome is not 'parsed' must report the observed content that "
                "was not parsed; an outcome with no evidence is indistinguishable from no row"
            )
        return self


class CensusClaimPayload(KnowledgeModel):
    """One distinct assessable claim, with its original text and its disposition state.

    ``claim_text`` is the claim's **original** text, stored verbatim. The census does not paraphrase
    it: a paraphrase would be the census interpreting its own subject matter, which is the act
    ``Doc13:458`` reserves to a curator.

    ``claim_kind`` starts at ``unclassified`` for every extracted claim. Nothing in the extraction
    path may set one of the four classified kinds -- that assignment is requirement 4.1 work, with an
    author and an instant -- and ``unclassified`` is a reportable state the census counts, not a
    placeholder to be filled by a default.

    ``applicability`` carries the eligibility rule's input. ``claim_kind="accepted_invariant"`` with
    ``applicability="historical_non_applicable"`` is a representable and expected combination: a
    claim can be perfectly well extracted, perfectly well classified, and outside the cohort whose
    truth share is being measured.
    """

    record_kind: Literal["census_claim"] = "census_claim"
    claim_text: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    claim_location: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    claim_kind: CensusClaimKind = "unclassified"
    applicability: CensusApplicability = COHORT_APPLICABILITY
    disposition: CensusDispositionKind
    provenance: CensusProvenance


class CensusDispositionPayload(KnowledgeModel):
    """One migration disposition: what became of one artifact or one claim.

    ``DispositionState`` separates a disposition that has only been **recorded** from one that has
    been **applied**. The distinction exists because this leaf prepares a migration and does not
    execute a cutover: an ``unmapped`` disposition is recorded and stays recorded, while an
    ``imported`` disposition is applied once the record it describes is written. Nothing here flips
    a live authority, and there is no value on this vocabulary that could mean "legacy is no longer
    authoritative".
    """

    disposition_kind: CensusDispositionKind
    disposition_state: CensusDispositionState = "recorded"
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    provenance: CensusProvenance


class CensusClaimEvidence(KnowledgeModel):
    """One stored reference from a census claim to the evidence a curator assessed it with.

    ``evidence_state`` is the **absence-of-assessment** state requirement 2.6 permits: ``unassessed``
    is derivable because no assessment row exists. The other two values are not derived by anything
    in the pipeline -- they are set only where a curator's authored assessment resolves the reference
    -- and this module deliberately offers no function that computes one from an import outcome.

    ``assessment_disposition`` is that curator's authored verdict, **stored verbatim from the
    curator's own record** and never computed here. It is optional, and its absence is the
    unassessed state rather than a fourth disposition: a claim with no assessment is ``P``, and never
    a claim whose verdict defaulted to something. The pipeline has no code path that writes a
    non-``None`` value -- only a curator's authored assessment supplies one.
    """

    claim_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    evidence_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    evidence_state: CensusEvidenceState = "unassessed"
    assessment_disposition: CensusAssessmentDisposition | None = None


class CensusClaimRealization(KnowledgeModel):
    """One stored reference from a census claim to a realization it attributes.

    This relation is what the realization-coverage measure counts: known, correctly attributed
    realizations over the reference inventory's realizations. ``missing_realization`` is the state
    that measure exists to make visible, and it is recorded rather than computed so that "the
    realization is absent" stays distinguishable from "nobody looked".
    """

    claim_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    realization_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    attribution_state: CensusRealizationState = "unattributed"


class CensusDispositionLink(KnowledgeModel):
    """One edge from a migration disposition to a record the disposition produced.

    ``Doc12:55`` makes "links to the resulting new records, including claims that are split,
    combined, retired, or corrected" part of the record, so each of those four is a link kind rather
    than a prose note. ``target_state`` is the reference-resolution state of the target and is
    carried verbatim: a link to a record that does not exist is reported as ``unresolved`` and is
    never repaired by resemblance or satisfied by creating a record to receive it.
    """

    disposition_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    link_kind: CensusLinkKind
    target_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    target_state: CensusTargetState = "resolved"


class CensusInventoryRowCommand(KnowledgeModel):
    """Record one inventory row as one envelope record and its sealed revision.

    ``governing_route_id`` is the **envelope's** explicit association, and it is optional on purpose:
    an inventory row exists for a surface whether or not the substrate has a ``Route`` record for it,
    and an absent route is the recorded ``absent`` state rather than a reason the row cannot exist.
    That asymmetry is what lets the census see a route with no onboarding -- the row is the finding.
    """

    kind: Literal["add_census_inventory_row"] = "add_census_inventory_row"
    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revision_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    payload: CensusInventoryRowPayload
    governing_route_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)


class CensusClaimCommand(KnowledgeModel):
    """Record one assessable claim as one envelope record and its sealed revision.

    The command carries no author and no instant: provenance comes from the admission, so no part of
    a submitted payload can become the claim's author. It also carries no verdict field, because a
    verdict is a curator's authored assessment in another record group, read by the census.
    """

    kind: Literal["add_census_claim"] = "add_census_claim"
    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revision_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    payload: CensusClaimPayload
    governing_route_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    evidence: tuple[CensusClaimEvidence, ...] = ()
    realizations: tuple[CensusClaimRealization, ...] = ()


class CensusDispositionCommand(KnowledgeModel):
    """Record one migration disposition as one envelope record, its revision and its links.

    The links travel inside the creation batch rather than through a second operation, so a
    disposition and the records it produced are written whole or not at all -- the same reason the
    authored-effect succession edge is inserted inside its successor's own batch.
    """

    kind: Literal["add_census_disposition"] = "add_census_disposition"
    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revision_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    payload: CensusDispositionPayload
    governing_route_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    links: tuple[CensusDispositionLink, ...] = ()


# The three commands this record group adds to the closed union, as one alias so the batch's
# dispatch, its preconditions and this record group's own write path name one type rather than three,
# and so a fourth command added to the union without a handler is a type error at the dispatch table.
# The registry entries this record group contributes, unpacked into the envelope's own
# ``PAYLOAD_MODELS``. The mapping lives here rather than beside the write path because a payload
# shape is vocabulary: the registry that decides admissibility reads it, and the record group that
# stores it reads the same declaration rather than a second one that could drift.
CENSUS_PAYLOAD_MODELS: Mapping[tuple[str, str], type[BaseModel]] = {
    (CENSUS_INVENTORY_ROW_KIND, CENSUS_INVENTORY_ROW_SCHEMA): CensusInventoryRowPayload,
    (CENSUS_CLAIM_KIND, CENSUS_CLAIM_SCHEMA): CensusClaimPayload,
    (CENSUS_DISPOSITION_KIND, CENSUS_DISPOSITION_SCHEMA): CensusDispositionPayload,
}

CENSUS_COMMAND_KINDS: frozenset[str] = frozenset(
    {"add_census_inventory_row", "add_census_claim", "add_census_disposition"}
)

# The three record kinds this group contributes to the envelope's registry, as one derived set so a
# registry case can name the group rather than restate its three literals.
CENSUS_RECORD_KINDS: frozenset[str] = frozenset(
    {CENSUS_INVENTORY_ROW_KIND, CENSUS_CLAIM_KIND, CENSUS_DISPOSITION_KIND}
)

# The tables a census command writes. Every one is declared by the generation that registers the
# census's record kinds, and a dataset older than that generation is neither migrated nor widened.
CENSUS_WRITABLE_TABLES: tuple[str, ...] = (
    "knowledge_record",
    "record_revision",
    "census_inventory_row",
    "census_claim",
    "census_disposition",
    "census_claim_evidence",
    "census_claim_realization",
    "census_disposition_link",
)


class CensusInventoryRow(KnowledgeModel):
    """One inventory row as the census read it back: its payload plus the envelope's own facts."""

    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: Literal["proposed", "accepted"]
    governing_route_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    payload: CensusInventoryRowPayload


class CensusClaim(KnowledgeModel):
    """One claim as the census read it back: its payload, the envelope's facts and its relations."""

    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: Literal["proposed", "accepted"]
    governing_route_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    payload: CensusClaimPayload
    evidence: tuple[CensusClaimEvidence, ...] = ()
    realizations: tuple[CensusClaimRealization, ...] = ()


class CensusDisposition(KnowledgeModel):
    """One disposition as the census read it back: its payload, the envelope's facts and its links."""

    record_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: Literal["proposed", "accepted"]
    governing_route_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    payload: CensusDispositionPayload
    links: tuple[CensusDispositionLink, ...] = ()
