"""``RequirementRevision``: requirement *meaning* gets a database home, never a second task authority.

This module owns the frozen payload vocabulary of the requirement-revision record group. The record
itself is not declared here: ``KS-R10@v1`` already built the envelope it lives in, and
``knowledge_record`` carries the kind, the authority home, the lifecycle and the governing route
while ``record_revision`` carries the payload and its content digest. One record identity per
requirement obligation, holding immutable revisions -- which is why this module adds **no** table,
**no** second envelope, **no** second revision aggregate and **no** identity mechanism of its own.

Four properties are load-bearing and none is incidental:

* **The owner reference is the task plane's own three components, spelled its way.** ``owner``
  carries ``path`` / ``stableId`` / ``version``: the same three names
  :class:`agents_remember.models.task_intent.ApprovedRequirementPacketRef` uses, so the two sides
  cannot spell one version two ways and drift apart in comparison. The admitted version spelling is
  that reference's own ``^v[1-9][0-9]*$``. A fourth identifier -- a UUID, a content address, a
  database-local surrogate -- is refused, not by a denylist but by ``extra="forbid"`` on a shape
  that declares exactly three fields.
* **This payload does not police the reference.** It bounds the three components; it does **not**
  confine the path to a task root, require a ``.md`` suffix, or check the packet's own metadata
  rows. All three are the owner's refusals
  (:func:`agents_remember.tasks.task_intent._approved_packet_ref`), and a substrate that pre-refused
  them would be substituting its own answer for the owner's -- which requirement 2.3 forbids in
  terms. The owner's answer is carried instead, as data: :class:`RequirementOwnerResolution`.
* **There is no field that is the operative obligation.** ``explanation`` is attributed authored
  material *about* what an obligation means; the text other systems implement against stays in the
  owner's packet. The enforcement is the absence: no ``obligation_text``, no ``requirement_text``,
  no ``title``, no ``display_version`` field exists for a copy to land in, and ``extra="forbid"``
  refuses a payload that arrives carrying one. The same absence is what keeps task status, seat
  ownership and lifecycle gates out of the record at both planes (requirement 3).
* **State is data, and it is sealed.** ``state_at_origin`` and ``acceptance_ref`` are payload
  fields, so the shipped ``proposed | accepted`` vocabulary and the shipped
  :func:`…base.require_consistent_acceptance` rule apply **at construction**, exactly as every
  revision aggregate in this vocabulary already applies them, and the envelope's ``content_digest``
  covers them because the payload is inside the seal. Nothing here promotes: ``accepted`` records an
  acceptance the owner made and never produces one.

**Recorded ruling on the degree of quotation in ``explanation`` (``CR19-5``, gating the freeze).**
``KS-R19@v1``'s Open Truth Gap 3 leaves open how much of an obligation's own text a self-contained
explanation may quote, and its carried finding ``CR19-5`` requires a *recorded ruling before the
payload schema is frozen* rather than a guess embedded in it. No developer or design source rules
the degree, so this is a **worker-recorded ruling taken under the owning seat's brief**, confirmed
by the owning seat on 2026-09-18 and recorded in the master's decision log on the same basis:

  *No quotation-degree policy is encoded in this schema.* The schema holds one attributed, nonempty
  ``explanation`` and refuses an empty or absent one; it holds no field that is the operative
  obligation. The degree is an **authoring discipline** carried by attribution plus that absence,
  **not** a schema rule, because policing the degree would require comparing the field against the
  owner's packet bytes -- and requirement 2.3 forbids the substrate from treating the packet as an
  operand at all. The shape is therefore correct under either possible developer ruling: a
  permissive ruling needs no schema change, and a restrictive one is a rule on authors that the
  substrate could not check without the operand it is forbidden to hold.

The exact sentence an author is bound by: *the explanation is attributed, self-contained, and never
the text another system implements against.*
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROPOSED_STATE,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
    KnowledgeState,
    require_consistent_acceptance,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The one stable value this record group declares in the envelope's typed kind vocabulary, and the
# one frozen shape it resolves to. The pair is the registry's key, so a payload admitted for one
# kind is never automatically admitted for another.
REQUIREMENT_REVISION_KIND = "requirement_revision"
REQUIREMENT_REVISION_SCHEMA = "requirement-revision/v1"

# The admitted version spelling is the task plane's own --
# ``models/task_intent/__init__.py``, ``ApprovedRequirementPacketRef.version``. Declared here as the
# same pattern rather than a second, wider one, because two admitted spellings for one version is
# exactly the drift requirement 2.2 forbids.
REQUIREMENT_PACKET_VERSION_PATTERN = r"^v[1-9][0-9]*$"

# The lifecycle every requirement revision is written under. A requirement revision recorded by this
# record group is a *record of* the owner's obligation, not an approval of it, so the envelope
# declares the proposed lifecycle exactly as an authored facet and a detection record do. The
# owner's own acceptance state travels in the payload's ``state_at_origin``.
REQUIREMENT_RECORD_LIFECYCLE = "proposed"

# The two owner-resolution outcomes, as the closed vocabulary the payload stores.
OwnerResolutionState = Literal["resolved", "unresolved"]

# What a currentness fact reports, as a closed vocabulary. Each member names a *distinct* fact a
# caller consumed from the owner, and each is reported rather than resolved: the substrate chooses
# no winner between its own stored state and the owner's recorded one.
CurrentnessBasis = Literal[
    "not-declared",
    "owner-moved",
    "record-stale",
    "packet-body-changed",
]

# The derived currentness state, computed from the two values the record holds. It is derived rather
# than supplied, so a caller cannot assert agreement the two values do not show.
CurrentnessState = Literal["aligned", "disagreement", "unresolved-owner"]


class RequirementOwnerRef(KnowledgeModel):
    """The canonical owner of one requirement obligation, in the task plane's own three components.

    The field names are :class:`…task_intent.ApprovedRequirementPacketRef`'s, deliberately, so the
    substrate's stored reference and the task plane's typed one compare literally rather than
    through a translation that could disagree. ``extra="forbid"`` is what refuses a fourth
    addressing scheme: there is no field for a UUID or a content address to arrive in.

    Nothing here confines the path or requires a Markdown target. Those are the owner's refusals and
    are carried as the owner's own codes; see :class:`RequirementOwnerResolution`.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    stableId: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    version: str = Field(pattern=REQUIREMENT_PACKET_VERSION_PATTERN)

    @model_validator(mode="after")
    def _require_nonblank_components(self) -> RequirementOwnerRef:
        for name in ("path", "stableId"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"owner reference {name} must not be blank")
        return self


class RequirementOwnerResolution(KnowledgeModel):
    """The owner's resolution *result*, consumed rather than re-derived.

    ``resolved`` means the owner's own resolver accepted this exact reference. ``unresolved``
    carries the refusal the owner returned, verbatim: ``refusal_code`` is the owner's own status
    string (for example ``task-intent-requirement-packet-missing``) and ``refusal_detail`` is its
    own message. Nothing in this record group constructs either value, so a store can never report
    an owner's refusal the owner did not give.

    The resolution carries no components of its own. The reference it is about is the record's one
    ``owner`` field, which is never rewritten by a resolution -- so a resolution cannot disagree
    with the reference it resolves, and a failed resolution leaves the reference intact.
    """

    state: OwnerResolutionState
    refusal_code: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    refusal_detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_outcome(self) -> RequirementOwnerResolution:
        carried = (self.refusal_code, self.refusal_detail)
        if self.state == "resolved":
            if any(value is not None for value in carried):
                raise ValueError("a resolved owner carries no refusal")
            return self
        if any(value is None or not str(value).strip() for value in carried):
            raise ValueError(
                "an unresolved owner carries the refusal the owner returned: code and detail"
            )
        return self


class RequirementRevisionPayload(KnowledgeModel):
    """One immutable revision of one requirement obligation, as the envelope stores it.

    ``explanation`` is the self-contained explanation ``Doc13:96`` requires and ``Doc13:55`` bounds:
    attributed authored material, sufficient to interpret the obligation without opening the packet,
    and never the operative text. It refuses an empty or absent value, because a record that stores
    only a pointer adds no meaning the owner did not already have.

    There is no field for the revision's authorship. The explanation's authorship **is** the
    revision's ``provenance`` on ``record_revision``, so a payload field naming an actor,
    an authorization, an operation or a recorded time would be a second, competing statement of one
    fact -- and ``extra="forbid"`` is what refuses one that arrives.
    """

    requirement_kind: Literal["requirement_revision"] = "requirement_revision"
    owner: RequirementOwnerRef
    owner_resolution: RequirementOwnerResolution
    explanation: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    state_at_origin: KnowledgeState = PROPOSED_STATE
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_consistent_origin(self) -> RequirementRevisionPayload:
        require_consistent_acceptance(self.state_at_origin, self.acceptance_ref)
        return self

    @model_validator(mode="after")
    def _require_an_explanation_that_says_something(self) -> RequirementRevisionPayload:
        """Refuse an explanation that is empty after trimming, not only one of length zero.

        A whitespace-only body is the same fact as an empty one -- a pointer without meaning -- and
        ``min_length=1`` alone would admit it. The refusal is by value, so it happens at construction
        exactly where the consistency rule above does.
        """

        if not self.explanation.strip():
            raise ValueError(
                "an explanation must not be blank: a pointer without meaning is not this record"
            )
        return self


class RequirementRecordedState(KnowledgeModel):
    """One side's recorded state: the value, and the provenance that records it.

    Two of these are what a currentness fact compares -- this record's stored state and the owner's
    recorded state -- and both are carried with their provenance rather than merged into one value,
    because merging them is the substrate choosing a winner.
    """

    state_at_origin: KnowledgeState
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: Authorship

    @model_validator(mode="after")
    def _require_consistent_origin(self) -> RequirementRecordedState:
        require_consistent_acceptance(self.state_at_origin, self.acceptance_ref)
        return self


class RequirementCurrentness(KnowledgeModel):
    """The currentness fact for one head revision: both recorded states, and no winner.

    ``state`` is derived from the two values, so agreement cannot be asserted against them.
    ``basis`` is the caller's consumed statement of *which* fact produced a disagreement -- the owner
    moved, the record was imported stale, or the packet body changed without a version move -- and is
    ``not-declared`` when a caller reports a disagreement without naming one. An unresolved owner has
    nothing to compare, and says so rather than reporting agreement.
    """

    revision_id: str = Field(pattern=UUID_PATTERN)
    state: CurrentnessState
    stored: RequirementRecordedState
    owner: RequirementRecordedState | None = None
    basis: CurrentnessBasis = "not-declared"
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_currentness_outcome(self) -> RequirementCurrentness:
        if self.state == "unresolved-owner":
            if self.owner is not None:
                raise ValueError("an unresolved owner carries no owner-recorded state to compare")
            return self
        if self.owner is None:
            raise ValueError("a compared currentness fact carries the owner's recorded state")
        expected = (
            "aligned"
            if (self.stored.state_at_origin, self.stored.acceptance_ref)
            == (self.owner.state_at_origin, self.owner.acceptance_ref)
            else "disagreement"
        )
        if self.state != expected:
            raise ValueError(
                f"the two recorded states show {expected!r}, not {self.state!r}: currentness is "
                "derived from the values it reports"
            )
        return self


class RequirementRevisionView(KnowledgeModel):
    """One stored revision, as a derived view reads it back."""

    revision_id: str = Field(pattern=UUID_PATTERN)
    owner: RequirementOwnerRef
    owner_resolution: RequirementOwnerResolution
    explanation: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    state_at_origin: KnowledgeState
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    content_digest: str = Field(pattern=SHA256_PATTERN)
    provenance: Authorship


class RequirementPredecessorChainView(KnowledgeModel):
    """The predecessor chain of one head revision, nearest predecessor first.

    The chain is walked from stored ``predecessor_revision_id`` values only. No operation of this
    record group can build a cycle -- every write refuses one in-transaction -- but the walk is
    bounded anyway, so a store damaged outside the operation is reported as a truncated chain rather
    than as a hang.
    """

    head_revision_id: str = Field(pattern=UUID_PATTERN)
    chain: tuple[str, ...]
    truncated: bool = False
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class RequirementGoverningRouteView(KnowledgeModel):
    """The recorded scope axis of one requirement record -- authored, or explicitly ungoverned.

    ``state`` is ``ungoverned`` exactly when the envelope's ``governing_route_id`` is ``NULL``. That
    is a recorded fact and never a default: the packet is a task-relative document, so its own
    directory is not a code path and cannot supply a route.
    """

    record_id: str = Field(pattern=UUID_PATTERN)
    state: Literal["governed", "ungoverned"]
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)

    @model_validator(mode="after")
    def _require_one_route_outcome(self) -> RequirementGoverningRouteView:
        expected = "governed" if self.governing_route_id is not None else "ungoverned"
        if self.state != expected:
            raise ValueError(
                f"the recorded route shows {expected!r}, not {self.state!r}: the ungoverned state "
                "is the absence of a route, never a route derived from the packet's location"
            )
        return self


class RequirementCurrentStateView(KnowledgeModel):
    """The current-state view of one record: its head revisions and their currentness facts.

    ``head_revision_ids`` are the revisions no stored revision names as its predecessor. A store
    with more than one head has *forked*, and the fork is reported as more than one head rather than
    resolved by the substrate picking one. ``owner`` is ``None`` only for a record that holds no
    stored revision at all, which no write path of this record group produces and which is reported
    as an absence rather than filled with a placeholder reference.
    """

    record_id: str = Field(pattern=UUID_PATTERN)
    head_revision_ids: tuple[str, ...]
    owner: RequirementOwnerRef | None = None
    currentness: tuple[RequirementCurrentness, ...]
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class RequirementRevisionScope(KnowledgeModel):
    """Everything this record group makes readable about one requirement obligation.

    The whole scope is a *derived* value: it is computed on read from the record row and its
    revision rows, and nothing in it is stored. Deleting it changes no obligation, no state and no
    acceptance, and a rebuild from the same rows reproduces it byte for byte, because there is no
    second place for a value to live. ``predecessor_chain`` is ``None`` only for a record holding no
    stored revision, which is reported as an absence rather than given a placeholder head.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    record_id: str = Field(pattern=UUID_PATTERN)
    kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    record_schema: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    record_provenance: Authorship
    revisions: tuple[RequirementRevisionView, ...]
    current_state: RequirementCurrentStateView
    predecessor_chain: RequirementPredecessorChainView | None = None
    governing_route: RequirementGoverningRouteView


class RequirementRevisionRequest(KnowledgeModel):
    """One request to record one immutable revision of one requirement obligation.

    The request is deliberately *not* the operation's whole reach. It carries no field for the
    owner's obligation text, no field for a title, and no field that renumbers a stored version --
    so "change the operative obligation", "retitle an obligation" and "renumber a version" are not
    acts this record group can be asked to perform. ``state_at_origin`` is present so that the
    opposite property is checkable: the request *can* declare accepted origin data, and the write
    path refuses it, rather than there being no way to ask.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    provenance: Authorship
    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    payload: RequirementRevisionPayload
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)


# The two operations this record group performs, as the narrow vocabulary its receipts carry. A
# receipt that named any other operation would report an act this record group did not perform.
RequirementRevisionOperation = Literal[
    "record_requirement_revision",
    "read_requirement_revisions",
]


class RequirementRevisionResult(KnowledgeModel):
    """The typed outcome of one requirement-revision operation: a write, a read, or one refusal."""

    state: Literal["recorded", "read", "refused"]
    operation: RequirementRevisionOperation
    repository_id: str = Field(pattern=UUID_PATTERN)
    scope: RequirementRevisionScope | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> RequirementRevisionResult:
        if self.state == "refused":
            if self.refusal is None or self.scope is not None:
                raise ValueError("a refused requirement operation carries its refusal and no scope")
            return self
        if self.scope is None or self.refusal is not None:
            raise ValueError("a served requirement operation carries its scope and no refusal")
        return self


class RequirementReferenceResolution(KnowledgeModel):
    """Where another record group's reference to a requirement obligation resolves, or does not.

    Requirement 2.6's direction, as a value: a reference resolves to a ``RequirementRevision`` of
    this record group, or it stays unresolved, or more than one record carries it and it is reported
    as ambiguous rather than resolved to a chosen one. Neither unresolved nor ambiguous is a refusal
    -- this record group invents no record to satisfy a reference, and picks no winner between two.
    """

    reference: RequirementOwnerRef | None = None
    state: Literal["resolved", "unresolved", "ambiguous"]
    record_ids: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_resolution_outcome(self) -> RequirementReferenceResolution:
        if self.state == "resolved" and len(self.record_ids) != 1:
            raise ValueError("a resolved reference names exactly one holding record")
        if self.state == "unresolved" and self.record_ids:
            raise ValueError("an unresolved reference names no holding record")
        if self.state == "ambiguous" and len(self.record_ids) < 2:
            raise ValueError("an ambiguous reference names more than one holding record")
        return self


def requirement_owner_reference(payload: Mapping[str, object]) -> RequirementOwnerRef | None:
    """Return the owner reference one authored payload mapping declares, or ``None``.

    This exists for the *structural* gate a reference-holding record group needs (requirement 2.6):
    a caller holding a reference to a requirement obligation asks whether a stored payload is a
    requirement revision of this record group, and this reads exactly the three components out of
    the payload that is already there. It resolves nothing and rewrites nothing -- it is a read of
    what was stored, and a mapping that does not carry a well-formed reference answers ``None``
    rather than an invented one.
    """

    reference = payload.get("owner")
    if not isinstance(reference, Mapping):
        return None
    try:
        return RequirementOwnerRef.model_validate(dict(reference))
    except ValueError:
        return None


# The authored payload shapes this record group declares, keyed by the pair the envelope registry
# uses. Declared here, beside the models, so the record envelope's registry and this vocabulary
# cannot drift: the registry imports this mapping rather than restating the pair.
REQUIREMENT_PAYLOAD_MODELS: Mapping[tuple[str, str], type[KnowledgeModel]] = {
    (REQUIREMENT_REVISION_KIND, REQUIREMENT_REVISION_SCHEMA): RequirementRevisionPayload,
}
