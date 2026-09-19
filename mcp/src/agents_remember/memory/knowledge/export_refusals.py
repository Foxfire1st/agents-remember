"""The refusal vocabulary of the portable export and import boundary.

One factory per observable failure point, in one module so the codes, the offending record and the
advertised next action stay together instead of being spelled out at each call site. The shared
``refusal`` factory and the exception types stay in :mod:`refusals`; this module only names the
failures a portable artifact can produce.

The artifact is an **external input**: it arrives as text, it was written by something that may not
be this code, and nothing about it is trusted because it parses. The refusals below separate the
questions that creates -- is this a well-formed artifact of a supported generation, is what it
carries a complete logical dataset, and may it be installed *here* -- so a caller can tell a
malformed document from an artifact that is internally consistent but not this dataset or not this
destination.

Every one of them leaves the destination exactly as it was: staging happens in a private file and
the install is the same atomic replace every other publication uses. An artifact that is refused
publishes nothing, and a partially imported dataset is not an observable state.
"""

from __future__ import annotations

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal


def invalid_export_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    facts: RefusalFacts | None = None,
) -> KnowledgeRefusal:
    """Refuse a document that is not a complete, well-formed export of this format.

    One code covers the malformed document and the incomplete dataset on purpose: the caller's
    decision is the same for both (the artifact cannot be imported), and ``detail`` names which of
    the two it is. Splitting it would invite a caller to treat "parsed" as "trustworthy", which is
    the confusion this boundary exists to prevent.
    """

    return refusal(
        "invalid_export",
        operation,
        detail,
        facts=facts,
        next_action=(
            "Correct or regenerate the artifact and import it again. Nothing was published: the "
            "destination still holds exactly what it held before, and no missing information was "
            "invented to complete the dataset."
        ),
    )


def non_canonical_export_refusal(operation: KnowledgeOperation, detail: str) -> KnowledgeRefusal:
    """Refuse a well-formed artifact whose text is not the canonical rendering of its content.

    The code is ``invalid_export``, and the split is deliberate: for the caller the decision is the
    same as for any other malformed document -- the artifact cannot be imported. It has its own
    factory because the *fact* is different and is the one this format's guarantee rests on: this
    document parses, and the dataset it declares is the dataset inside it, but it is spelled some
    other way than the one form the format accepts. The detail names how the text differs from its
    rendering, and nothing is repaired here: an artifact is refused, never quietly normalised.
    """

    return refusal(
        "invalid_export",
        operation,
        detail,
        facts=RefusalFacts(record_id="<canonical document>"),
        next_action=(
            "Re-encode the dataset with this package's encoder and import that artifact. This "
            "format accepts exactly one spelling, so a second spelling is a different document "
            "rather than a tolerated variant, and the accepted bytes are therefore a function of "
            "the dataset. Nothing was published and the input was not modified."
        ),
    )


def unsupported_schema_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeRefusal:
    """Refuse an artifact written for a schema generation this build does not implement.

    A version string is not evidence that the tables match it, so this refusal names the declared
    generation and the supported one. A future generation needs a reader that implements it rather
    than a tolerant read of this one: this operation never migrates and never drops a collection it
    does not understand.
    """

    return refusal(
        "unsupported_schema",
        operation,
        detail,
        facts=RefusalFacts(expected=expected, observed=observed),
        next_action=(
            "Import the artifact with the build that implements its schema generation. No "
            "migration, downgrade or partial read of an unsupported artifact is performed here."
        ),
    )


def destination_occupied_refusal(
    operation: KnowledgeOperation, *, destination_ref: str, observed: str
) -> KnowledgeRefusal:
    """Refuse an import whose destination holds a dataset the caller never admitted.

    An import never patches a live destination in place. When the request admits no expectation
    there is nothing to compare against, so an occupied destination is refused and the caller has
    to state the identity it believes is there -- which is the same rule every publication in this
    package follows, applied before any staging work happens.
    """

    return refusal(
        "destination_occupied",
        operation,
        "the destination already holds a knowledge dataset and the request admitted no identity "
        "for it",
        facts=RefusalFacts(record_id=destination_ref, expected="<absent>", observed=observed),
        next_action=(
            "Re-issue the import naming the destination's exact logical identity, or import to a "
            "path that is absent. Nothing was replaced and no staged file remains."
        ),
    )


def destination_absent_refusal(
    operation: KnowledgeOperation, *, destination_ref: str, expected: str
) -> KnowledgeRefusal:
    """Refuse an import that admitted an existing destination which is not there.

    The mirror of the case above, and it is a refusal rather than a fresh install on purpose: a
    caller that named the identity it expected to replace is telling us a dataset it believes
    exists, and creating a new one there would answer a different question than the one asked.
    """

    return refusal(
        "destination_stale",
        operation,
        "the destination the import was admitted against does not exist",
        facts=RefusalFacts(record_id=destination_ref, expected=expected, observed="<absent>"),
        next_action=(
            "Re-issue the import with no expected destination if a new dataset should be created "
            "at this path. Nothing was created and no staged file remains."
        ),
    )


def import_validation_failed_refusal(
    operation: KnowledgeOperation, detail: str, *, record_id: str | None = None
) -> KnowledgeRefusal:
    """Refuse a dataset whose rows are complete as a document but not valid as this schema.

    This is the refusal of the *staged* database: the artifact's rows loaded, and the integrity
    checks a normal store open performs -- foreign keys, typed JSON columns, recomputed sealed
    payload digests -- did not hold. It is a different fact from a malformed artifact, and it is
    reported with its own code path so a caller can tell "your document is wrong" from "your
    records are not this schema's knowledge".
    """

    return refusal(
        "relationship_constraint",
        operation,
        f"the staged dataset is not valid as this schema's knowledge: {detail}",
        facts=RefusalFacts(record_id=record_id),
        next_action=(
            "Regenerate the artifact from a dataset this store produced. The staged database was "
            "removed and the destination was never touched."
        ),
    )
