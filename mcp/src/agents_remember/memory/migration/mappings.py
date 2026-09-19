"""The explicit mapping registry: data a reviewer can read, not an inference at parse time.

``KS-R21@v1`` §2.1 requires every imported artifact to be imported under an **explicit mapping**
naming its source artifact form, the target record kind and the fields the mapping supplies, and it
requires that mapping to be "data that can be inspected and reviewed; it is not an inference performed
at parse time and not a table of heuristics inside the parser".

So the mapping is a value here, selected by an equality test on a **declared** field of the artifact
(its ``doc_type`` and its format name) and never by a property of the artifact's prose. Where no entry
matches, the outcome is :data:`UNMAPPED`, a named state, and §2.2 forbids the three things an
importer does when it only pretends to be mechanical:

* it does not construct a best-fit mapping;
* it does not choose a target kind by name similarity;
* it does not create a record so that the row looks complete.

:func:`select_mapping` therefore returns ``None`` rather than a candidate, and there is deliberately
no scoring function anywhere in this module: a "closest" mapping would be an inference with a
distance metric on it.

Every entry declares the fields it supplies, and the declaration is checked against the payload model
the target kind actually requires. That check is what keeps the registry honest: a mapping cannot
claim to supply a field the record kind does not have, and a target kind whose required field no
mapping supplies is a mapping that would refuse at write time -- discovered here rather than after a
partial import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.knowledge.census import (
    CENSUS_CLAIM_KIND,
    CENSUS_DISPOSITION_KIND,
    CENSUS_INVENTORY_ROW_KIND,
)

# The registry's own version, recorded beside every outcome so a re-run compares like with like. A
# changed registry is a changed mapping, and ``KS-R21@v1`` §2.5 makes a re-run at a different mapping
# a *difference* to report rather than a silent second import.
MAPPING_REGISTRY_VERSION = "explicit-mapping-registry/v1"

# The named state an artifact with no matching entry carries. It is a value of the closed disposition
# vocabulary the census payload already declares, so an unmapped artifact appears in the census's
# dispositions rather than in a log line.
UNMAPPED: Literal["unmapped"] = "unmapped"

# The mapping identifier an unmapped artifact carries, so a reader can tell "no mapping existed" from
# "a mapping existed and was named": an empty identifier is the absence, never a blank mapping.
NO_MAPPING_ID = ""


@dataclass(frozen=True)
class ExplicitMapping:
    """One recorded mapping from an artifact form to a target census record kind.

    ``supplied_fields`` is the declaration §2.1 requires: the exact census-payload fields the mapping
    fills from the artifact. It is checked against the target payload model's own required field set,
    so the registry cannot claim a field the record kind does not declare.

    ``provenance_fields`` names the artifact's declared front-matter keys this mapping carries into the
    record's stored provenance -- the artifact, the location within it and the baseline are supplied
    by the importer itself, and these are the extra declarations a reader needs to trace a claim back
    to the exact line that asserted it.
    """

    mapping_id: str
    artifact_format: str
    artifact_doc_type: str
    record_kind: str
    supplied_fields: tuple[str, ...]
    provenance_fields: tuple[str, ...]
    rationale: str


# The registry. One entry per artifact form this leaf supports, and every entry names its target kind
# and the fields it supplies. The three entries below are the whole registry: a fourth record kind
# would need a fourth entry rather than a widened first one.
MAPPINGS: tuple[ExplicitMapping, ...] = (
    ExplicitMapping(
        mapping_id="file-card-to-inventory-row",
        artifact_format="markdown-metadata-table/v1",
        artifact_doc_type="file-level-onboarding",
        record_kind=CENSUS_INVENTORY_ROW_KIND,
        supplied_fields=(
            "artifact_path",
            "declared_source_path",
            "observed_doc_type",
            "outcome",
            "inventory_state",
        ),
        provenance_fields=("lastVerifiedCommitHash", "governingOverview"),
        rationale=(
            "A file-level card declares the source it documents and the revision it was verified "
            "against, so the fields that become an inventory row are read from declared metadata "
            "rather than from the card's prose. The card's body is not read by this mapping at all."
        ),
    ),
    ExplicitMapping(
        mapping_id="route-overview-to-inventory-row",
        artifact_format="markdown-metadata-table/v1",
        artifact_doc_type="route-local-overview",
        record_kind=CENSUS_INVENTORY_ROW_KIND,
        supplied_fields=(
            "artifact_path",
            "observed_doc_type",
            "observed_route_path",
            "outcome",
            "inventory_state",
        ),
        provenance_fields=("lastVerifiedCommitHash", "governingOverview"),
        rationale=(
            "A route overview declares its own scope through sourceRoute rather than through a path, "
            "so it becomes an inventory row for the surface it governs and supplies no declared "
            "source path. Its declared sourceRoute is recorded as the observed route path, which is "
            "what lets a route with no cards be reported."
        ),
    ),
    ExplicitMapping(
        mapping_id="file-card-to-claim",
        artifact_format="markdown-metadata-table/v1",
        artifact_doc_type="file-level-onboarding",
        record_kind=CENSUS_CLAIM_KIND,
        supplied_fields=("claim_text", "claim_location"),
        provenance_fields=("path", "governingOverview"),
        rationale=(
            "The one mapping that produces a claim, and it supplies only the claim's original text "
            "and its location. It deliberately supplies no claim kind, no applicability and no "
            "assessment: those are a curator's authored work, and a mapping that filled them would "
            "be the importer interpreting prose, which this record group has no vocabulary for."
        ),
    ),
    ExplicitMapping(
        mapping_id="artifact-to-disposition",
        artifact_format="markdown-metadata-table/v1",
        artifact_doc_type="*",
        record_kind=CENSUS_DISPOSITION_KIND,
        supplied_fields=("disposition_kind",),
        provenance_fields=("repository",),
        rationale=(
            "Every artifact gets a migration disposition, including the ones deliberately not "
            "imported, because non-claim content and historical material must carry an explicit "
            "disposition rather than silently vanishing from the inventory. The wildcard doc_type is "
            "the one entry that matches any declared form, and it supplies only the disposition's "
            "kind -- never a rationale, which is authored."
        ),
    ),
)


def mappings_for_kind(record_kind: str) -> tuple[ExplicitMapping, ...]:
    """Return every mapping whose target is one record kind, in registry order."""

    return tuple(entry for entry in MAPPINGS if entry.record_kind == record_kind)


DECLARED_FORMATS: tuple[str, ...] = tuple(sorted({entry.artifact_format for entry in MAPPINGS}))


def select_mapping(artifact_format: str, artifact_doc_type: str | None) -> ExplicitMapping | None:
    """Return the one mapping a declared artifact form selects, or ``None`` for unmapped.

    Selection is an equality test on two **declared** values, with two stated limits:

    * a format the registry does not declare selects nothing, so a format the parser met but the
      registry never admitted is **unmapped** rather than matched by the nearest entry. That is the
      §1.1 rule that an undeclared format is a finding rather than a silent extension, kept here so
      the state stays reachable.
    * ``"*"`` is the one wildcard, and it matches any **declared** doc type -- including an artifact
      that declared none, which is the honest attribution for a piece whose form is unknown.
    """

    if artifact_format not in DECLARED_FORMATS:
        return None
    declared = "" if artifact_doc_type is None else artifact_doc_type
    for entry in MAPPINGS:
        if entry.artifact_format != artifact_format:
            continue
        if entry.artifact_doc_type in ("*", declared):
            return entry
    return None


def mapping_identity(mapping: ExplicitMapping | None) -> str:
    """Return the identifier one mapping outcome carries, or the empty absence for unmapped."""

    return NO_MAPPING_ID if mapping is None else mapping.mapping_id


def unmapped_disposition() -> Literal["unmapped"]:
    """Return the disposition an artifact with no mapping carries.

    A function rather than a constant read at the call site, so "no mapping exists" is produced in one
    place and a later reader can see that the state is *chosen* here rather than defaulted there.
    """

    return UNMAPPED
