"""The closed authored-judgment vocabulary: eight frozen subtypes and their authored commands.

This module owns **one** closed list. ``FACET_KINDS`` is the eight subtypes ``Doc13:94`` names, and
every other declaration here is derived from it or checked against it:

* one frozen payload model per subtype, each under the shipped :class:`KnowledgeModel` base, so an
  undeclared field is refused rather than stored and the payload is a validated shape instead of the
  untyped properties bag ``design/storage-design.md:97`` refuses;
* :data:`FacetPayload`, the discriminated union whose member set is exactly ``FACET_KINDS``. The
  discriminator is what makes "the vocabulary is closed" checkable: a ninth subtype has no member to
  resolve to, so it cannot be stored as a generic facet;
* :data:`FACET_RECORD_SCHEMAS`, the ``record_schema`` each subtype's payload resolves to in the
  record envelope's registry. It is declared here, beside the models, because the pair
  ``(kind, record_schema)`` is the registry's key and a second spelling elsewhere could drift.

The minimum meaning each subtype must carry is requirement 1.3's table; the field spelling is this
leaf's recorded decision. Two properties are load-bearing and neither is incidental:

* **No payload field can be read as, or substituted for, the record's provenance** (requirement
  2.2). A decision's ``decider`` is authored *content about who decided*; the record's authorship is
  the admitted operation that recorded it. There is no payload field for ``actor_ref``,
  ``authorization_ref``, ``operation_id`` or ``recorded_at``, and ``extra="forbid"`` is what refuses
  a payload that arrives carrying one.
* **Diagnostic guidance is guidance, not a verdict** (requirement 1.5). Its payload carries an
  interpretation and the limitation of that interpretation; it carries no field that could hold an
  assessment, a compatibility verdict, a severity or an endorsement, so there is nothing for a
  writer to fill in and nothing for a reader to mistake for one.

The authored commands live here too, next to the vocabulary they address, rather than in the
candidate module: the union in :mod:`agents_remember.models.knowledge.candidate` is the operation's
whole reach and names these members, while the *shapes* belong with the facet kinds they carry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
    KnowledgeState,
    require_consistent_acceptance,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The eight subtypes, in ``Doc13:94``'s order. The tuple is the closed list; the ``Literal`` below
# is the same eight spellings, and a case asserts the two agree so a member cannot be added to one
# without the other.
FacetKind = Literal[
    "decision",
    "assumption",
    "incident",
    "failure_mode",
    "scenario",
    "limitation",
    "diagnostic_guidance",
    "terminology",
]

FACET_KINDS: tuple[FacetKind, ...] = (
    "decision",
    "assumption",
    "incident",
    "failure_mode",
    "scenario",
    "limitation",
    "diagnostic_guidance",
    "terminology",
)

# The one spelling of a facet record's ``record_schema``. It is derived from the kind so the two
# cannot disagree, and ``facet`` leads it so a facet schema is never mistaken for a generation name.
FACET_SCHEMA_PREFIX = "facet-"
FACET_SCHEMA_SUFFIX = "/v1"


def facet_record_schema(facet_kind: FacetKind) -> str:
    """Return the frozen payload schema one facet subtype resolves to."""

    return f"{FACET_SCHEMA_PREFIX}{facet_kind}{FACET_SCHEMA_SUFFIX}"


class DecisionPayload(KnowledgeModel):
    """Requirement 1.3, ``decision``: the outcome, the reason, and the decider.

    The governed artifacts are this record's attachment rows (requirement 5.1), so there is no
    second prose copy of them here, and the supersession edge is a recorded table fact rather than
    a payload field (requirement 5.2). ``decider`` is authored content about who decided: it is not
    the record's provenance and cannot be substituted for it.
    """

    facet_kind: Literal["decision"] = "decision"
    outcome: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    decider: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)


class AssumptionPayload(KnowledgeModel):
    """Requirement 1.3, ``assumption``: the assumed proposition and the basis it is assumed under."""

    facet_kind: Literal["assumption"] = "assumption"
    proposition: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    basis: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class IncidentPayload(KnowledgeModel):
    """Requirement 1.3, ``incident``: what happened, where or when it was observed, and its effect."""

    facet_kind: Literal["incident"] = "incident"
    occurrence: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    observed_at: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    observed_effect: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class FailureModePayload(KnowledgeModel):
    """Requirement 1.3, ``failure_mode``: the failure, its condition, its observable effect."""

    facet_kind: Literal["failure_mode"] = "failure_mode"
    failure: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    condition: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    observable_effect: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ScenarioPayload(KnowledgeModel):
    """Requirement 1.3, ``scenario``: the situation, its preconditions, and the described outcome.

    ``preconditions`` is a nonempty tuple: the meaning requirement 1.3 fixes is only carried when
    at least one precondition is named, and an empty tuple would satisfy the field while omitting
    the meaning.
    """

    facet_kind: Literal["scenario"] = "scenario"
    situation: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    preconditions: tuple[str, ...] = Field(min_length=1)
    outcome: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class LimitationPayload(KnowledgeModel):
    """Requirement 1.3, ``limitation``: what is limited, the boundary, what remains unsupported."""

    facet_kind: Literal["limitation"] = "limitation"
    limited: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    boundary: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    unsupported: tuple[str, ...] = Field(min_length=1)


class DiagnosticGuidancePayload(KnowledgeModel):
    """Requirement 1.3, ``diagnostic_guidance``: condition, signal, interpretation, its limitation.

    Requirement 1.5 is why the interpretation and the limitation of that interpretation are two
    required fields and why there is no third one: the payload may state a reading of an observed
    signal, and it must state how far that reading reaches. It carries no assessment, no
    compatibility verdict and no endorsement.
    """

    facet_kind: Literal["diagnostic_guidance"] = "diagnostic_guidance"
    condition: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    signal: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    interpretation: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    interpretation_limit: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class TerminologyPayload(KnowledgeModel):
    """Requirement 1.3, ``terminology``: the term, its definition as used here, and its scope."""

    facet_kind: Literal["terminology"] = "terminology"
    term: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    definition: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    scope: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


# The discriminator's member set. It is exactly the eight subtypes: a ninth payload model cannot be
# reached without being added here, and adding it here is the vocabulary change requirement 1.1
# requires its own packet for.
FacetPayload = Annotated[
    DecisionPayload
    | AssumptionPayload
    | IncidentPayload
    | FailureModePayload
    | ScenarioPayload
    | LimitationPayload
    | DiagnosticGuidancePayload
    | TerminologyPayload,
    Field(discriminator="facet_kind"),
]

# ``kind`` -> the one frozen model that carries it, and ``kind`` -> its record schema. Both are
# derived from the same declaration list so a kind cannot be in one and missing from the other, and
# a case asserts the two key sets are equal to ``FACET_KINDS``.
_FACET_MODELS: Mapping[FacetKind, type[BaseModel]] = {
    "decision": DecisionPayload,
    "assumption": AssumptionPayload,
    "incident": IncidentPayload,
    "failure_mode": FailureModePayload,
    "scenario": ScenarioPayload,
    "limitation": LimitationPayload,
    "diagnostic_guidance": DiagnosticGuidancePayload,
    "terminology": TerminologyPayload,
}

FACET_RECORD_SCHEMAS: Mapping[FacetKind, str] = {
    facet_kind: facet_record_schema(facet_kind) for facet_kind in FACET_KINDS
}


def facet_payload_model(facet_kind: str) -> type[BaseModel] | None:
    """Return the frozen payload model one facet kind resolves to, or ``None``.

    ``None`` for a kind this build does not declare is the point: an unknown or ninth subtype has no
    shape to validate against, so it is refused rather than stored as a generic facet.
    """

    for declared, model in _FACET_MODELS.items():
        if facet_kind == declared:
            return model
    return None


def facet_payload_models() -> Mapping[str, type[BaseModel]]:
    """Return every ``(facet kind, model)`` pair, for the envelope registry to register."""

    return {facet_kind: _FACET_MODELS[facet_kind] for facet_kind in FACET_KINDS}


# ---------------------------------------------------------------------------
# Attachment endpoints (requirement 4). The route is deliberately absent: it is the envelope's own
# association (requirement 4.2), written as ``governing_route_id`` on the facet's record row, and a
# second route mechanism here would be the competing one that clause forbids.

AttachmentEndpointKind = Literal[
    "invariant_revision",
    "family_revision",
    "source_anchor",
    "realization_claim",
]

# One name per kind, so the DDL's checked group, the endpoint model's discriminator and the
# existence check that applies all read the same spelling from one place.
INVARIANT_REVISION_ENDPOINT_KIND: AttachmentEndpointKind = "invariant_revision"
FAMILY_REVISION_ENDPOINT_KIND: AttachmentEndpointKind = "family_revision"
SOURCE_ANCHOR_ENDPOINT_KIND: AttachmentEndpointKind = "source_anchor"
REALIZATION_CLAIM_ENDPOINT_KIND: AttachmentEndpointKind = "realization_claim"

ATTACHMENT_ENDPOINT_KINDS: tuple[AttachmentEndpointKind, ...] = (
    INVARIANT_REVISION_ENDPOINT_KIND,
    FAMILY_REVISION_ENDPOINT_KIND,
    SOURCE_ANCHOR_ENDPOINT_KIND,
    REALIZATION_CLAIM_ENDPOINT_KIND,
)


class InvariantRevisionEndpoint(KnowledgeModel):
    """An attachment to one exact invariant revision (requirement 4.3)."""

    kind: Literal["invariant_revision"] = INVARIANT_REVISION_ENDPOINT_KIND
    revision_id: str = Field(pattern=UUID_PATTERN)


class FamilyRevisionEndpoint(KnowledgeModel):
    """An attachment to one exact family revision (requirement 4.3)."""

    kind: Literal["family_revision"] = FAMILY_REVISION_ENDPOINT_KIND
    revision_id: str = Field(pattern=UUID_PATTERN)


class SourceAnchorEndpoint(KnowledgeModel):
    """An attachment to one stored source anchor."""

    kind: Literal["source_anchor"] = SOURCE_ANCHOR_ENDPOINT_KIND
    anchor_id: str = Field(pattern=UUID_PATTERN)


class RealizationClaimEndpoint(KnowledgeModel):
    """An attachment to one stored realization claim."""

    kind: Literal["realization_claim"] = REALIZATION_CLAIM_ENDPOINT_KIND
    claim_id: str = Field(pattern=UUID_PATTERN)


AttachmentEndpoint = Annotated[
    InvariantRevisionEndpoint
    | FamilyRevisionEndpoint
    | SourceAnchorEndpoint
    | RealizationClaimEndpoint,
    Field(discriminator="kind"),
]

# The identity column each endpoint kind populates in ``facet_attachment``. The stored row carries a
# checked foreign-key group per kind and a constraint that exactly one group is populated and
# matches the stored kind, so which column an endpoint lands in is a fact of the table rather than a
# convention the write path remembers.
ENDPOINT_COLUMNS: Mapping[AttachmentEndpointKind, str] = {
    "invariant_revision": "invariant_revision_id",
    "family_revision": "family_revision_id",
    "source_anchor": "anchor_id",
    "realization_claim": "claim_id",
}


def endpoint_identity(endpoint: AttachmentEndpoint) -> str:
    """Return the exact identity one typed endpoint names.

    The two revision kinds spell their identity ``revision_id`` and the two stored-row kinds spell
    theirs by their own key, so the narrowing is written out rather than reached through ``getattr``
    -- a spelling a caller is shown has to be a field the endpoint actually carries.
    """

    if isinstance(endpoint, InvariantRevisionEndpoint | FamilyRevisionEndpoint):
        return endpoint.revision_id
    if isinstance(endpoint, SourceAnchorEndpoint):
        return endpoint.anchor_id
    return endpoint.claim_id


# ---------------------------------------------------------------------------
# Explanation subjects (requirement 6.6). The set is CLOSED at the two statement revisions the
# design names -- the invariant statement and the family joint guarantee -- and it is recorded as
# this leaf's intake decision rather than left open. Each subject kind is its own typed model, so a
# caller cannot attach an anchor identity where a statement revision belongs.

ExplanationSubjectKind = Literal["invariant_revision", "family_revision"]

# One name per subject kind. The spellings coincide with the endpoint kind names because both name
# the same canonical table, but they are separate constants: an explanation's subject set and an
# attachment's endpoint set are two closed sets that could diverge, and sharing a literal would hide
# that.
INVARIANT_STATEMENT_SUBJECT_KIND: ExplanationSubjectKind = "invariant_revision"
FAMILY_GUARANTEE_SUBJECT_KIND: ExplanationSubjectKind = "family_revision"

EXPLANATION_SUBJECT_KINDS: tuple[ExplanationSubjectKind, ...] = (
    INVARIANT_STATEMENT_SUBJECT_KIND,
    FAMILY_GUARANTEE_SUBJECT_KIND,
)

SUBJECT_COLUMNS: Mapping[ExplanationSubjectKind, str] = {
    INVARIANT_STATEMENT_SUBJECT_KIND: "subject_invariant_revision_id",
    FAMILY_GUARANTEE_SUBJECT_KIND: "subject_family_revision_id",
}


class InvariantStatementSubject(KnowledgeModel):
    """The exact invariant revision whose statement an explanation explains."""

    kind: Literal["invariant_revision"] = INVARIANT_STATEMENT_SUBJECT_KIND
    invariant_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)


class FamilyJointGuaranteeSubject(KnowledgeModel):
    """The exact family revision whose joint guarantee an explanation explains."""

    kind: Literal["family_revision"] = FAMILY_GUARANTEE_SUBJECT_KIND
    family_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)


ExplanationSubject = Annotated[
    InvariantStatementSubject | FamilyJointGuaranteeSubject,
    Field(discriminator="kind"),
]


def subject_identity(subject: ExplanationSubject) -> tuple[str, str]:
    """Return the ``(identity, revision)`` pair one subject names."""

    if isinstance(subject, InvariantStatementSubject):
        return (subject.invariant_id, subject.revision_id)
    return (subject.family_id, subject.revision_id)


def subject_revision_id(subject: ExplanationSubject) -> str:
    """Return the exact statement revision one subject names."""

    return subject_identity(subject)[1]


# ---------------------------------------------------------------------------
# The authored facet commands. Six members, each naming one distinct authored act: record a facet,
# attach it to an exact endpoint, remove one attachment, author an explanation, edit an explanation
# by authoring a successor, and record which explanation revision is designated.


class AddFacet(KnowledgeModel):
    """Record one authored facet as one record envelope plus its first sealed revision.

    The payload arrives as the authored mapping and is validated **at the envelope seam**, which is
    the one place any write path decides whether a payload is admissible. It is deliberately not
    pre-validated here: a second validation in the command model would be a second decision point,
    and it would turn an unknown or ninth subtype into a parse error instead of the typed
    ``invalid_payload`` refusal requirement 7.4 requires a caller to branch on. What the command
    fixes is the pair the seam resolves with -- the declared subtype and the revision the payload is
    written under -- so a refusal can name both.

    ``state_at_origin`` and ``acceptance_ref`` exist so that "this operation stores proposed origin
    data only" is checkable in both directions: the accepted-origin consistency rule is inherited
    from the shipped vocabulary base, and a command that would store accepted origin data is refused
    by the batch with the shipped ``promotion_not_supported`` (requirement 3.2).
    """

    kind: Literal["add_facet"] = "add_facet"
    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    facet_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    payload: Mapping[str, Any]
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    supersedes_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    state_at_origin: KnowledgeState = "proposed"
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_supersession_of_a_decision(self) -> AddFacet:
        """Only a decision carries a supersession edge (requirement 5.2 is that record kind's)."""

        if self.supersedes_revision_id is not None and self.facet_kind != "decision":
            raise ValueError(
                "a supersession edge is authored by a superseding decision; facet kind "
                f"{self.facet_kind!r} supersedes nothing"
            )
        return self

    @model_validator(mode="after")
    def _require_consistent_origin(self) -> AddFacet:
        require_consistent_acceptance(self.state_at_origin, self.acceptance_ref)
        return self


class AttachFacet(KnowledgeModel):
    """Attach one exact facet revision to one exact, typed endpoint (requirement 4)."""

    kind: Literal["attach_facet"] = "attach_facet"
    attachment_id: str = Field(pattern=UUID_PATTERN)
    facet_revision_id: str = Field(pattern=UUID_PATTERN)
    endpoint: AttachmentEndpoint


class RemoveFacetAttachment(KnowledgeModel):
    """Remove one attachment by identity and expected row digest (requirement 4.6)."""

    kind: Literal["remove_facet_attachment"] = "remove_facet_attachment"
    attachment_id: str = Field(pattern=UUID_PATTERN)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class AuthorExplanation(KnowledgeModel):
    """Author the first revision of one separable explanation record (requirement 6.1).

    The subject is an exact statement revision of one of the two declared subject kinds. Nothing
    here writes through the explained statement: the subject is a reference this record carries.
    """

    kind: Literal["author_explanation"] = "author_explanation"
    explanation_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    subject: ExplanationSubject
    body: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class AddExplanationRevision(KnowledgeModel):
    """Edit an explanation by authoring a successor naming its exact predecessor (requirement 6.2)."""

    kind: Literal["add_explanation_revision"] = "add_explanation_revision"
    explanation_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    predecessor_revision_id: str = Field(pattern=UUID_PATTERN)
    body: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class DesignateExplanation(KnowledgeModel):
    """Record which explanation revision is designated for one explanation identity (requirement 6.3).

    The designation is the one mutable field of an explanation identity row, so it names the exact
    row it expects, exactly as a label edit does.
    """

    kind: Literal["designate_explanation"] = "designate_explanation"
    explanation_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


# The facet commands, as the union the candidate module adds to its own. Declared here so the
# facet vocabulary and the acts that author it stay in one file; the closed union itself stays in
# ``models.knowledge.candidate``, which is where the operation's whole reach is declared.
FacetCommand = Annotated[
    AddFacet
    | AttachFacet
    | RemoveFacetAttachment
    | AuthorExplanation
    | AddExplanationRevision
    | DesignateExplanation,
    Field(discriminator="kind"),
]

FACET_COMMAND_KINDS: tuple[str, ...] = (
    "add_facet",
    "attach_facet",
    "remove_facet_attachment",
    "author_explanation",
    "add_explanation_revision",
    "designate_explanation",
)


class FacetWriteRequest(KnowledgeModel):
    """One standalone authored facet write, addressed at exactly one namespace.

    The shipped operations each have two entry points: the operation, which owns the candidate lock
    and one ``BEGIN IMMEDIATE`` transaction, and the in-transaction step, which raises a typed
    refusal for the batch path to roll back. This is the standalone operation's input -- one
    namespace and one command -- so six writes do not need six differently shaped requests.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    command: FacetCommand
    # The admitted provenance envelope, carried on the request exactly as the realization-claim
    # request carries it: the application builds this request from the destination it resolved, so
    # the envelope comes from the admission rather than from any authored payload.
    provenance: Authorship


# The canonical tables a facet command writes. Declared here, beside the commands that address
# them, and folded into the candidate module's ``MutableRecordTable`` so an expectation may name one
# of them; a case asserts the two declarations agree rather than assuming they do.
FACET_WRITABLE_TABLES: tuple[str, ...] = (
    "knowledge_record",
    "record_revision",
    "facet_attachment",
    "facet_decision_supersession",
    "explanation",
    "explanation_revision",
)

FacetRecordTable = Literal[
    "knowledge_record",
    "record_revision",
    "facet_attachment",
    "facet_decision_supersession",
    "explanation",
    "explanation_revision",
]


class FacetWriteIdentity(KnowledgeModel):
    """One row a facet write touched, carrying the digest the store computed for it."""

    state: Literal["written", "removed"] = "written"
    table: FacetRecordTable
    record_id: str = Field(pattern=UUID_PATTERN)
    digest: str = Field(pattern=SHA256_PATTERN)


class FacetWriteResult(KnowledgeModel):
    """The factual receipt of one standalone facet write.

    It reports the rows the write touched, each carrying the digest the store computed for it, or
    the typed refusal that replaced the whole write. It has no field that could carry an approval,
    an endorsement or a semantic judgement (requirement 2.3).
    """

    state: Literal["applied", "no_change", "refused"]
    repository_id: str = Field(pattern=UUID_PATTERN)
    written: tuple[FacetWriteIdentity, ...] = ()
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_receipt(self) -> FacetWriteResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused facet write must carry its refusal")
            if self.written:
                raise ValueError("a refused facet write changed nothing and reports no row")
            return self
        if self.refusal is not None:
            raise ValueError("a facet write that was not refused cannot also carry a refusal")
        if self.state == "no_change":
            # The third state the store's other receipts already carry: the command was admissible
            # and touched nothing, which is a fact rather than a failure. It reports no row and no
            # refusal, so it is not ``applied`` and must not be read as one.
            if self.written:
                raise ValueError("a no-change facet write reports no touched row")
            return self
        if not self.written:
            raise ValueError("an applied facet write reports at least one touched row")
        return self


__all__ = [
    "ATTACHMENT_ENDPOINT_KINDS",
    "ENDPOINT_COLUMNS",
    "EXPLANATION_SUBJECT_KINDS",
    "FACET_COMMAND_KINDS",
    "FACET_KINDS",
    "FACET_RECORD_SCHEMAS",
    "FACET_WRITABLE_TABLES",
    "FAMILY_REVISION_ENDPOINT_KIND",
    "INVARIANT_REVISION_ENDPOINT_KIND",
    "REALIZATION_CLAIM_ENDPOINT_KIND",
    "SOURCE_ANCHOR_ENDPOINT_KIND",
    "SUBJECT_COLUMNS",
    "AddExplanationRevision",
    "AddFacet",
    "AssumptionPayload",
    "AttachFacet",
    "AttachmentEndpoint",
    "AttachmentEndpointKind",
    "AuthorExplanation",
    "DecisionPayload",
    "DesignateExplanation",
    "DiagnosticGuidancePayload",
    "ExplanationSubject",
    "ExplanationSubjectKind",
    "FacetCommand",
    "FacetKind",
    "FacetPayload",
    "FacetRecordTable",
    "FacetWriteIdentity",
    "FacetWriteRequest",
    "FacetWriteResult",
    "FailureModePayload",
    "FamilyJointGuaranteeSubject",
    "FamilyRevisionEndpoint",
    "IncidentPayload",
    "InvariantRevisionEndpoint",
    "InvariantStatementSubject",
    "LimitationPayload",
    "RealizationClaimEndpoint",
    "RemoveFacetAttachment",
    "ScenarioPayload",
    "SourceAnchorEndpoint",
    "TerminologyPayload",
    "endpoint_identity",
    "facet_payload_model",
    "facet_payload_models",
    "facet_record_schema",
    "subject_identity",
    "subject_revision_id",
]
