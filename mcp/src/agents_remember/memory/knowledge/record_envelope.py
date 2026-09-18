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
mechanical-detection kinds.** The concrete knowledge categories that are *not* facets
(``EvidenceClaim``, …) are later leaves. This leaf registers the eight authored-judgment subtypes
beside the internal conformance kind -- one registry entry per subtype, whose model is the frozen
payload model the facet vocabulary declares -- so the typed half of the envelope carries the real
vocabulary rather than only a promise. The internal kind is marked internal and is not a knowledge
category.

The detection leaf registers the two kinds this docstring used to defer: ``detection_signal`` and
``detection_run`` resolve to the frozen payload models
:mod:`agents_remember.models.knowledge.detection` declares, so a detection record's required field
set, its closed vocabularies and its construction refusals are enforced by the same seam every other
typed record passes through rather than by a second one beside it.

The registry maps to **frozen** models: a validated payload is a value, and a caller cannot mutate
what it validated into something the registry would not have accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, KnowledgeModel
from agents_remember.models.knowledge.detection import (
    DETECTION_RUN_KIND,
    DETECTION_RUN_SCHEMA,
    DETECTION_SIGNAL_KIND,
    DETECTION_SIGNAL_SCHEMA,
    DetectionRunPayload,
    DetectionSignalPayload,
)
from agents_remember.models.knowledge.facet import (
    FACET_KINDS,
    FACET_RECORD_SCHEMAS,
    facet_payload_models,
)
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

    record_schema = FACET_RECORD_SCHEMAS.get(facet_kind)
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
