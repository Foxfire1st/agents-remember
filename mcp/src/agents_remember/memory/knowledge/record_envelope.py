"""The typed record envelope's payload seam: one registry, one entry point.

The envelope is a *typed* envelope rather than an unvalidated property bag, and this module is the
only place any write path decides whether a payload is admissible. Two values select the shape:

* ``record_schema`` names the frozen Pydantic shape the revision was written against, and selects
  it;
* ``kind`` constrains which shapes are admissible for that kind.

An unknown ``kind``, an unknown ``record_schema``, or a payload that does not validate against the
resolved model is refused with the shipped code ``invalid_payload``, with no row written and the
before/after digest unchanged.

**One internal conformance kind, plus the eight authored facet kinds, plus the two
mechanical-detection kinds, plus the citation-binding kind.** The concrete knowledge categories that
are *not* facets (``EvidenceClaim``, …) are later leaves. This leaf registers the eight
authored-judgment subtypes beside the internal conformance kind -- one registry entry per subtype,
whose model is the frozen payload model the facet vocabulary declares -- so the typed half of the
envelope carries the real vocabulary rather than only a promise. The internal kind is marked internal
and is not a knowledge category.

The detection leaf registers the two kinds this docstring used to defer: ``detection_signal`` and
``detection_run`` resolve to the frozen payload models
:mod:`agents_remember.models.knowledge.detection` declares, so a detection record's required field
set, its closed vocabularies and its construction refusals are enforced by the same seam every other
typed record passes through rather than by a second one beside it.

The citation-binding leaf registers ``citation_binding`` the same way: the binding's authored facts
resolve to the frozen payload model :mod:`agents_remember.models.knowledge.citation` declares, so
"a binding is authored, not inferred" is enforced by the one payload seam rather than restated at
the write path.

The supporting-records leaf registers the two remaining kinds this docstring deferred:
``evidence_claim`` and ``verification_observation`` resolve to the frozen payload models
:mod:`agents_remember.models.knowledge.evidence` declares. The claim's *subject* and its *claimed
coverage* are deliberately not payload fields -- they are resolved relations, so they live in the
typed join tables the supporting-records leaf's own generation appends, where endpoint-kind
compatibility is a constraint of the schema rather than a value this seam validates.

The registry maps to **frozen** models: a validated payload is a value, and a caller cannot mutate
what it validated into something the registry would not have accepted.

The requirement-revision leaf registers the fourth typed family: ``requirement_revision`` resolves
to the frozen payload model :mod:`agents_remember.models.knowledge.requirement` declares, which
carries the obligation's owning packet identity, the attributed self-contained explanation and the
inherited ``state_at_origin``/``acceptance_ref`` pair. That record group adds no table of its own --
the pair above is the envelope, and a requirement revision is an envelope record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, KnowledgeModel
from agents_remember.models.knowledge.citation import (
    BINDING_RECORD_KIND,
    BINDING_RECORD_SCHEMA,
    CitationBindingPayload,
)
from agents_remember.models.knowledge.detection import (
    DETECTION_RUN_KIND,
    DETECTION_RUN_SCHEMA,
    DETECTION_SIGNAL_KIND,
    DETECTION_SIGNAL_SCHEMA,
    DetectionRunPayload,
    DetectionSignalPayload,
)
from agents_remember.models.knowledge.evidence import (
    EVIDENCE_CLAIM_KIND,
    EVIDENCE_CLAIM_SCHEMA,
    VERIFICATION_OBSERVATION_KIND,
    VERIFICATION_OBSERVATION_SCHEMA,
    EvidenceClaimPayload,
    VerificationObservationPayload,
)
from agents_remember.models.knowledge.facet import (
    FACET_KINDS,
    FACET_RECORD_SCHEMAS,
    facet_payload_models,
)
from agents_remember.models.knowledge.requirement import REQUIREMENT_PAYLOAD_MODELS
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The one kind this leaf registers. It is internal: it carries no knowledge-category meaning and
# exists so the payload seam is constructible and refutable inside this leaf.
INTERNAL_CONFORMANCE_KIND = "internal_conformance"

# The one frozen shape that kind resolves to.
INTERNAL_CONFORMANCE_SCHEMA = "internal-conformance/v1"


class ConformancePayload(KnowledgeModel):
    """The minimal frozen shape the internal conformance kind validates against.

    It is deliberately tiny: the seam's contract is "one registry entry, one resolved model, one
    validation", and a shape with more fields would only make the refusal cases harder to read.
    """

    note: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


# ``(kind, record_schema)`` -> exactly one frozen model. The pair is the key rather than the schema
# alone, because a kind constrains which shapes are admissible for it: a schema that is valid for
# one kind is not automatically valid for another.
#
# The facet entries are generated from the facet vocabulary's own declarations rather than restated,
# so a subtype cannot exist in the vocabulary without a registered shape here, and the two key sets
# cannot drift. A ninth subtype has no entry and therefore no admissible payload.
_FACET_PAYLOAD_MODELS: Mapping[tuple[str, str], type[BaseModel]] = {
    (facet_kind, FACET_RECORD_SCHEMAS[facet_kind]): facet_payload_models()[facet_kind]
    for facet_kind in FACET_KINDS
}

PAYLOAD_MODELS: Mapping[tuple[str, str], type[BaseModel]] = {
    (INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA): ConformancePayload,
    **_FACET_PAYLOAD_MODELS,
    # The mechanical-detection record group. It is the pair of kinds this envelope's own docstring
    # named as "later leaves", and they register here rather than as generation-4 columns because
    # the frozen payload model *is* the shape: a signal's required field set, its closed
    # vocabularies and its construction refusals are declared once, in
    # :mod:`agents_remember.models.knowledge.detection`, and a second declaration as SQL columns
    # would be a second place for the same field set to drift. Generation 4 appends only what the
    # envelope cannot express -- the run's recorded signal order.
    (DETECTION_SIGNAL_KIND, DETECTION_SIGNAL_SCHEMA): DetectionSignalPayload,
    (DETECTION_RUN_KIND, DETECTION_RUN_SCHEMA): DetectionRunPayload,
    # The requirement-revision record group. It is the envelope's fourth typed family, and it
    # registers the same way the two above do: the frozen payload model *is* the shape, declared
    # once in :mod:`agents_remember.models.knowledge.requirement`, so a revision's owner reference,
    # its self-contained explanation and its inherited state/acceptance pair are enforced by the same
    # seam every other typed record passes through rather than by a second one beside it. The mapping
    # is unpacked from that module rather than restated here, so the registry and the vocabulary
    # cannot drift and a requirement kind cannot exist without a registered shape.
    **REQUIREMENT_PAYLOAD_MODELS,
    # The citation-binding record group. Its payload is the three authored facts that belong to the
    # record -- the prose owner revision, the local key as written, and the typed target reference
    # with its locator -- and it registers here for the same reason the detection kinds do: the
    # frozen payload model *is* the shape, so an unregistered kind, a schema inadmissible for its
    # kind and a payload carrying an undeclared field are all the shipped ``invalid_payload``
    # refusal, raised at this one seam before any row exists. The binding's owner-revision/key
    # identity pair and its governing route are columns, because the envelope cannot express them.
    (BINDING_RECORD_KIND, BINDING_RECORD_SCHEMA): CitationBindingPayload,
    # The supporting-record pair. A claim's required authored content -- the explanation, the
    # limitations field that is never re-read as "unknown", the opaque assessment references and the
    # lifecycle -- and an observation's recorded candidate, command identity, artifact reference,
    # closed execution result, run environment and publication reference are each declared once, in
    # :mod:`agents_remember.models.knowledge.evidence`, and reached through this seam like every
    # other typed record rather than through a second decision point beside it.
    (EVIDENCE_CLAIM_KIND, EVIDENCE_CLAIM_SCHEMA): EvidenceClaimPayload,
    (
        VERIFICATION_OBSERVATION_KIND,
        VERIFICATION_OBSERVATION_SCHEMA,
    ): VerificationObservationPayload,
}

# The facet kinds this registry admits, for a caller that needs the closed vocabulary rather than a
# lookup. It is derived from the registry, so it answers "which kinds have a shape" rather than
# "which kinds does the vocabulary name", and the two are equal by construction.
FACET_RECORD_KINDS: frozenset[str] = frozenset(kind for (kind, _schema) in _FACET_PAYLOAD_MODELS)

# The mechanical-detection kinds this registry admits, derived from the same declarations the entries
# above are built from rather than restated. A caller that needs to say what the registry holds names
# all three groups -- the internal conformance kind, the eight facet kinds and these two -- and the
# three sets are disjoint by construction because a kind is one string.
DETECTION_RECORD_KINDS: frozenset[str] = frozenset({DETECTION_SIGNAL_KIND, DETECTION_RUN_KIND})

# The requirement-revision kinds this registry admits, derived from the same declaration the entry
# above is built from rather than restated. A caller that needs to say what the registry holds names
# every group -- the internal conformance kind, the eight facet kinds, the two detection kinds, the
# citation-binding kind and this one -- and the groups are disjoint by construction because a kind is one string.
REQUIREMENT_RECORD_KINDS: frozenset[str] = frozenset(
    kind for (kind, _schema) in REQUIREMENT_PAYLOAD_MODELS
)
# The citation-binding kinds this registry admits, derived from the declarations the entry above is
# built from rather than restated. A caller that needs to say what the registry holds names every
# group -- the internal conformance kind, the eight facet kinds, the two detection kinds, the
# requirement-revision kinds and this one -- and the four sets are disjoint by construction because a kind is one string.
CITATION_BINDING_RECORD_KINDS: frozenset[str] = frozenset({BINDING_RECORD_KIND})

# The supporting-record kinds, derived from the same declarations the entries above are built from.
# A caller that needs to say what the registry holds names every group -- the internal conformance
# kind, the eight facet kinds, the two detection kinds, the requirement-revision kinds, the
# citation-binding kind and these two -- and the sets are disjoint by construction because a kind is
# one string.
EVIDENCE_RECORD_KINDS: frozenset[str] = frozenset(
    {EVIDENCE_CLAIM_KIND, VERIFICATION_OBSERVATION_KIND}
)

# Which shapes each kind admits. Derived from the registry rather than restated, so a kind cannot
# admit a shape the registry does not hold.
KIND_SCHEMAS: Mapping[str, frozenset[str]] = {
    kind: frozenset(
        schema for (registered_kind, schema) in PAYLOAD_MODELS if registered_kind == kind
    )
    for kind, _ in PAYLOAD_MODELS
}


def validate_record_payload(
    kind: str,
    record_schema: str,
    payload: Mapping[str, Any],
    *,
    operation: KnowledgeOperation = "create_invariant_revision",
    record_id: str | None = None,
) -> BaseModel | KnowledgeRefusal:
    """Validate one payload against the frozen shape its ``(kind, record_schema)`` resolves to.

    This is the **only** place a write path decides whether a payload is admissible. It returns the
    validated model rather than a boolean so a caller stores what was validated instead of
    re-deriving it, and it returns a refusal rather than raising so an expected failure is a value
    the caller branches on.
    """

    admitted = KIND_SCHEMAS.get(kind)
    if admitted is None:
        return _invalid_payload_refusal(
            operation,
            f"the record kind {kind!r} is not registered, so no payload shape is admissible for it",
            record_id=record_id,
            observed=kind,
            expected=" | ".join(sorted(KIND_SCHEMAS)),
        )
    if record_schema not in admitted:
        return _invalid_payload_refusal(
            operation,
            f"the record schema {record_schema!r} is not admissible for kind {kind!r}",
            record_id=record_id,
            observed=record_schema,
            expected=" | ".join(sorted(admitted)),
        )
    model = PAYLOAD_MODELS[(kind, record_schema)]
    try:
        return model.model_validate(dict(payload))
    except ValidationError as error:
        return _invalid_payload_refusal(
            operation,
            f"the payload does not validate against {record_schema}: "
            f"{_render_validation_error(error)}",
            record_id=record_id,
            observed=record_schema,
            expected=record_schema,
        )


def validate_facet_payload(
    facet_kind: str,
    payload: Mapping[str, Any],
    *,
    operation: KnowledgeOperation = "add_facet",
    record_id: str | None = None,
) -> BaseModel | KnowledgeRefusal:
    """Validate one authored facet payload against the frozen shape its subtype resolves to.

    The subtype decides the ``record_schema``, so a caller names one value rather than two that
    could disagree, and an unknown or ninth subtype is refused here -- as the shipped
    ``invalid_payload``, listing the eight declared subtypes -- rather than stored as a generic
    facet. Requirement 1.2's "reached through a discriminator" is this resolution: the subtype
    selects exactly one frozen model, and only that model's fields are admissible.
    """

    record_schema = _declared_facet_record_schema(facet_kind)
    if record_schema is None:
        return _invalid_payload_refusal(
            operation,
            f"the facet kind {facet_kind!r} is not one of the declared authored-judgment subtypes",
            record_id=record_id,
            observed=facet_kind,
            expected=" | ".join(FACET_KINDS),
        )
    return validate_record_payload(
        facet_kind, record_schema, payload, operation=operation, record_id=record_id
    )


def _declared_facet_record_schema(facet_kind: str) -> str | None:
    """Return the record schema one declared facet kind stores, or ``None`` for an undeclared kind.

    ``FACET_RECORD_SCHEMAS`` is keyed by the declared ``FacetKind`` rather than by ``str``, so a
    caller's spelling is resolved against the closed tuple first: the registry is never asked a key
    this build does not declare, and an unknown or ninth subtype is answered with ``None`` -- the
    refusal this operation reports -- instead of a lookup that cannot succeed.
    """

    for declared in FACET_KINDS:
        if facet_kind == declared:
            return FACET_RECORD_SCHEMAS[declared]
    return None


def _render_validation_error(error: ValidationError) -> str:
    """Render one pydantic failure as the bounded, position-naming detail a refusal carries."""

    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()[:4]
    )


def _invalid_payload_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    record_id: str | None,
    expected: str | None,
    observed: str | None,
) -> KnowledgeRefusal:
    """Refuse an inadmissible payload with the shipped ``invalid_payload`` code."""

    return refusal(
        "invalid_payload",
        operation,
        f"the record payload is not admissible: {detail}",
        facts=RefusalFacts(
            table="record_revision",
            record_id=record_id,
            expected=expected,
            observed=observed,
        ),
        next_action=(
            "Correct the payload for the kind and schema it declares, or declare the "
            "registered schema this shape belongs to, and submit it again. Nothing was written."
        ),
    )
