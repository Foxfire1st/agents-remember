"""The typed record envelope's payload seam: one registry, one entry point.

The envelope is a *typed* envelope rather than an unvalidated property bag, and this module is the
only place any write path decides whether a payload is admissible. Two values select the shape:

* ``record_schema`` names the frozen Pydantic shape the revision was written against, and selects
  it;
* ``kind`` constrains which shapes are admissible for that kind.

An unknown ``kind``, an unknown ``record_schema``, or a payload that does not validate against the
resolved model is refused with the shipped code ``invalid_payload``, with no row written and the
before/after digest unchanged.

**One internal conformance kind, and no product kinds.** The concrete knowledge categories
(``EvidenceClaim``, ``DetectionSignal``, …) are later leaves. This leaf registers exactly one kind --
:data:`INTERNAL_CONFORMANCE_KIND` -- with a minimal frozen shape, used only to exercise the seam, so
the typed half of the envelope has a mechanism rather than a promise. It is marked internal, it is
not a knowledge category, and the later leaves add the real kinds *beside* it rather than replacing
it.

The registry maps to **frozen** models: a validated payload is a value, and a caller cannot mutate
what it validated into something the registry would not have accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, KnowledgeModel
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
PAYLOAD_MODELS: Mapping[tuple[str, str], type[BaseModel]] = {
    (INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA): ConformancePayload,
}

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
