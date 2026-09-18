"""Mechanical detection: the facts a signal carries, and the closed vocabularies they come from.

This module is the whole vocabulary of one detection run, and it holds no SQL. It exists because a
*detection signal* is a different claim from a comparison, and the differences are the ones that
have to be unrepresentable rather than merely discouraged:

* **No semantic conclusion, by construction.** A signal and a run have no field that could hold a
  severity, an assessed priority, a conflict or compatibility verdict, a causal explanation, a
  harmlessness label or an authored finding -- and the declared field set is *reviewable* against
  one closed list (:data:`CONCLUSION_BEARING_FIELD_NAMES`, with
  :func:`conclusion_bearing_fields` as the review). The shipped comparison states the same property
  at ``models/knowledge/diff.py:12-13``; this module states it as a rule rather than as an appeal to
  that precedent, and construction refuses a payload that supplies one, whether as an extra field
  (``extra="forbid"`` on the shipped base) or written into a prose field.
* **The declared input set is required, explicit and closed.** A signal states which of the three
  readings it performed, and the declaration is checked against the recorded discriminator of the
  member it named -- never inferred from the condition, from the number of sides a request happened
  to carry, or from the running build. A signal whose declaration and recorded inputs disagree fails
  construction rather than being reported under the broader member.
* **Observed changes are typed at their recorded granularity.** The granularity is part of the fact:
  a whole-file observation recorded as a body change is refused, because a file changing is not
  evidence that an attributed span changed.
* **Versions are published twice.** The extractor and policy versions are values on the run *and* on
  every signal it produced, so a signal lifted out of its run still carries what it ran under and a
  run does not have to be reconstructed from its signals.

The prose a signal may carry is deliberately not free: :func:`observed_basis_detail` renders the one
string a signal or a run is allowed to hold, and construction requires the recorded ``detail`` to
*equal* that rendering. A rendered judgment written into the string is therefore refused exactly as a
verdict field is, and the refusal names the field and the record it would have been carried on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.diff import DiffCoverage
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import KnowledgeReadContext
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "CONCLUSION_BEARING_FIELD_NAMES",
    "CONDITION_VOCABULARY_VERSION",
    "DECLARED_INPUT_SETS",
    "DETECTION_CONDITIONS",
    "DETECTION_EXTRACTOR_VERSION",
    "DETECTION_LIMITATIONS",
    "DETECTION_POLICY_VERSION",
    "DETECTION_RUN_KIND",
    "DETECTION_RUN_SCHEMA",
    "DETECTION_SIGNAL_KIND",
    "DETECTION_SIGNAL_SCHEMA",
    "MANIFEST_DESTINATION_KINDS",
    "NO_SEMANTIC_ASSESSMENT_LIMITATION",
    "DeclaredInputSet",
    "DetectionChangeGranularity",
    "DetectionCondition",
    "DetectionCounterpartProbe",
    "DetectionInputSide",
    "DetectionLimitation",
    "DetectionManifestResolution",
    "DetectionObservedChange",
    "DetectionRecordedInputSet",
    "DetectionRelationshipPath",
    "DetectionRunCurrentness",
    "DetectionRunDifference",
    "DetectionRunInputDifference",
    "DetectionRunPayload",
    "DetectionRunReproduction",
    "DetectionRunRequest",
    "DetectionRunResult",
    "DetectionScopeManifest",
    "DetectionScopeStatus",
    "DetectionSignalPayload",
    "DetectionSignalSet",
    "ManifestDestinationObservation",
    "conclusion_bearing_fields",
    "declared_input_set_discriminators",
    "observed_basis_detail",
]

# The policy this detection contract is produced by, in the shipped constant idiom
# (``DIFF_POLICY_VERSION`` at ``models/knowledge/diff.py:98``;
# ``KNOWLEDGE_READ_POLICY_VERSION`` at ``models/knowledge/read.py:87``). It names the detection
# contract, not a second selection rule: which records are selected is R07's policy and which union
# is compared is R08's, and neither is restated here.
DETECTION_POLICY_VERSION = "family-detection/v1"

# The extractor version this leaf authors. Measured at intake on the merged branch: the shipped
# anchor resolver has no symbol extractor at all -- ``memory/knowledge/read_anchors.py:132-139``
# returns ``resolution="unsupported_locator"`` with the reason that none supports a symbol locator
# in this increment -- so there is no existing constant to cite and the version is authored here
# rather than invented as a plausible import. It names what this build actually extracts:
# whole-path and recorded-range observations against an exact tree object, and nothing else.
DETECTION_EXTRACTOR_VERSION = "recorded-anchor-locator/v1"

# The matched-condition vocabulary's own version. Requirement 1.2 makes the vocabulary closed and
# *versioned with the policy*: a condition the policy does not declare is refused with the observed
# identity and this version, and is never recorded as free prose. The members are ``Doc13``
# §8's own candidate list, and nothing beyond it: a condition no walk can emit would be dead
# vocabulary, and one this leaf invented would be a policy decision it does not own.
DetectionCondition = Literal[
    "source_changed_on_both_sides_joined_to_same_family",
    "one_sided_source_change_with_recorded_siblings",
    "edited_invariant_family_or_evidence_record",
    "absent_anchor",
    "removed_or_reparented_attribution",
]

# The declared membership order. It is the order the detection walk emits in and the order the
# deterministic total order is declared over, so it is a value rather than a comment.
DETECTION_CONDITIONS: tuple[DetectionCondition, ...] = (
    "source_changed_on_both_sides_joined_to_same_family",
    "one_sided_source_change_with_recorded_siblings",
    "edited_invariant_family_or_evidence_record",
    "absent_anchor",
    "removed_or_reparented_attribution",
)

CONDITION_VOCABULARY_VERSION = "detection-conditions/v1"

# The three declared input sets. Requirement 2.1 makes the declaration a single, required, explicit
# value from this closed set; there is no wider default to fall back to and no inference from the
# condition or from the shape of a request.
DeclaredInputSet = Literal[
    "both_sides_declared",
    "union_of_both_sides",
    "trigger_side_only",
]

DECLARED_INPUT_SETS: tuple[DeclaredInputSet, ...] = (
    "both_sides_declared",
    "union_of_both_sides",
    "trigger_side_only",
)

# The side a signal read. ``before``/``after`` are R08's own two sides; ``trigger`` names the one
# side a trigger-side-only read consulted, which is a different fact from "the after side" because
# nothing about the read says which half of a comparison it was.
DetectionSide = Literal["before", "after", "trigger"]

# Requirement 1.3's distinct facts, at their recorded granularity. A whole-file observation and an
# attributed span observation are different facts, and recording the second when only the first was
# observed is the non-conforming signal this vocabulary exists to make unrepresentable.
DetectionChangeGranularity = Literal[
    "source_file_changed",
    "attributed_span_changed",
    "anchor_resolved_elsewhere",
    "realization_claim_record_changed",
    "invariant_statement_changed",
    "family_statement_changed",
]

# The locator kinds whose observation is a whole path rather than a span inside it. A signal
# recording ``attributed_span_changed`` against one of these claims a span observation nothing made.
_WHOLE_PATH_LOCATOR_KINDS: tuple[str, ...] = ("file", "directory")

# Requirement 6.1's registered scope status, plus the one outcome that says the scan did not finish.
# ``complete_for_declared_policy`` is a bounded scan result and is not proof that attribution is
# accurate or that every behavioral dependency is known; nothing in this model upgrades it to one.
DetectionScopeStatus = Literal[
    "complete_for_declared_policy",
    "incomplete_scan",
]

# Every declared limit a signal or a run can carry. Each is a statement about what the record cannot
# claim, and each is checked against the recorded data by this module's own validators so a gap
# cannot be omitted from the declaration that is supposed to advertise it.
DetectionLimitation = Literal[
    "unread_declared_input",
    "unmapped_changed_paths",
    "unsupported_locator",
    "truncated_scan",
    "ambiguous_common_base",
    "missing_attribution",
    "no_counterpart_read",
    "records_present_outside_the_declared_selection",
    "no_semantic_assessment_performed",
]

# The same members as the ``Literal`` above, as a tuple, so a caller can iterate the closed set and
# a case can assert the two agree. Keeping both is the shipped idiom (``ANCHOR_RESOLUTIONS`` beside
# ``AnchorResolutionState`` at ``models/knowledge/read.py:112-130``): the ``Literal`` is the type a
# field is validated against and the tuple is the value a reader enumerates.
DETECTION_LIMITATIONS: tuple[DetectionLimitation, ...] = (
    "unread_declared_input",
    "unmapped_changed_paths",
    "unsupported_locator",
    "truncated_scan",
    "ambiguous_common_base",
    "missing_attribution",
    "no_counterpart_read",
    "records_present_outside_the_declared_selection",
    "no_semantic_assessment_performed",
)

# The one limitation every signal and every run states unconditionally, for the shipped comparison's
# reason (``models/knowledge/diff.py:501-510``; the validator at ``:564-569``): no field of either
# record could carry an assessment, so the absence is declared rather than left to be inferred from
# a missing field.
NO_SEMANTIC_ASSESSMENT_LIMITATION: DetectionLimitation = "no_semantic_assessment_performed"

# The conclusion-bearing names a declared field set is reviewed against. Requirement 5.2 makes
# "a conclusion must not be representable" checkable by a review of the declared field set, and this
# is the list that review is performed against: a field whose name names one of these concepts is a
# conclusion field whatever its type, and :func:`conclusion_bearing_fields` reports it.
CONCLUSION_BEARING_FIELD_NAMES: tuple[str, ...] = (
    "severity",
    "assessed_priority",
    "priority",
    "verdict",
    "conflict",
    "semantic_conflict",
    "compatibility",
    "compatible",
    "harmless",
    "harmlessness",
    "neutral",
    "neutrality",
    "causal_explanation",
    "explanation",
    "assessment",
    "finding",
    "judgment",
    "judgement",
    "dangerous",
    "impact",
    "risk",
    "breaks",
    "strengthens",
    "weakens",
    "recommendation",
    "disposition",
)

# Where a referenced scope manifest may live. Only ``durable_publication`` survives enclosure
# cleanup, so only it can back a ``retained`` report; the other three are the destinations
# ``design/retrieval-review-design.md:370`` and ``Doc13:114`` name as not sufficient.
MANIFEST_DESTINATION_KINDS: tuple[str, ...] = (
    "durable_publication",
    "enclosure_local",
    "worktree_local",
    "regenerable_worklist",
)

# The record envelope's registry keys for this record group. The pair is the key, exactly as the
# facet vocabulary declares its own: a second spelling elsewhere could drift from the registry.
DETECTION_SIGNAL_KIND = "detection_signal"
DETECTION_SIGNAL_SCHEMA = "detection-signal/v1"
DETECTION_RUN_KIND = "detection_run"
DETECTION_RUN_SCHEMA = "detection-run/v1"


def conclusion_bearing_fields(model: type[KnowledgeModel]) -> tuple[str, ...]:
    """Return the declared field names of one model that could carry a conclusion.

    Requirement 5.2's first half is a *review of the declared field set*, so it needs a total,
    mechanical answer rather than an assurance: this returns every declared field whose name names
    a conclusion concept, and the shipped comparison's own result and this leaf's signal and run
    must all return the empty tuple. It reads the declared field set rather than an instance, so a
    field is reported whether or not any payload happens to populate it.
    """

    reported: list[str] = []
    for name in model.model_fields:
        lowered = name.lower()
        if any(concept in lowered for concept in CONCLUSION_BEARING_FIELD_NAMES):
            reported.append(name)
    return tuple(reported)


def declared_input_set_discriminators() -> Mapping[str, str]:
    """Return each declared input set's own recorded discriminator, as prose the refusal quotes.

    Requirement 2.3 makes the three members record *different* facts rather than three names for one
    shape. The mapping is derived from the closed tuple so it cannot name a member the vocabulary
    does not declare, and the validators below enforce exactly what each entry says.
    """

    return {
        "both_sides_declared": (
            "both declared sides' snapshot identities, each under its own selector and context, and "
            "no counterpart-probe outcome"
        ),
        "union_of_both_sides": (
            "the union worked over, and the counterpart probe's recorded outcome per reported item"
        ),
        "trigger_side_only": "exactly one side's snapshot identity and no probe outcome at all",
    }


class DetectionInputSide(KnowledgeModel):
    """One side a run or a signal read: the exact context, and the selector that read it.

    ``context`` is the whole admission this side has, exactly as R08 admits one, so the recorded
    input is an identity rather than a path: a side names the exact logical snapshot it read, the
    optional exact code tree it resolved against and the repository binding, and nothing here
    substitutes a newer commit or a working tree for the named one.
    """

    side: DetectionSide
    context: KnowledgeReadContext
    selector_digest: str = Field(pattern=SHA256_PATTERN)
    selector_policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_namespace(self) -> DetectionInputSide:
        if self.context.knowledge.repository_id != self.context.repository_id:
            raise ValueError(
                "a detection input side names a namespace its selected knowledge snapshot does "
                "not belong to"
            )
        return self


class DetectionCounterpartProbe(KnowledgeModel):
    """One union item's recorded counterpart-probe outcome, in the shipped coverage vocabulary.

    This is R08's own :data:`~agents_remember.models.knowledge.diff.DiffCoverage` and not a second
    one: the probe distinguishes absence from presence-outside-selection by one exact existence
    question asked of the other snapshot, and the five members are that answer's whole range.
    """

    item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    item_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    coverage: DiffCoverage


class DetectionObservedChange(KnowledgeModel):
    """One observed change, at its recorded granularity.

    The granularity is part of the fact rather than a rendering of it (requirement 1.3). A claim
    observed to resolve elsewhere records ``anchor_resolved_elsewhere`` and not
    ``attributed_span_changed``, and the two are not interchangeable descriptions of one event.
    """

    item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    item_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    granularity: DetectionChangeGranularity
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    recorded_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    observed_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    locator_kind: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_recorded_granularity(self) -> DetectionObservedChange:
        """Refuse a span observation that no whole-path locator could have made.

        A whole-file observation recorded as a body change is the packet's non-conforming signal:
        a file changing is not proof that the span the author attributed the obligation to changed.
        The refusal names the locator that was read and the granularity that was claimed, so the
        defect is diagnosable rather than merely rejected.
        """

        if (
            self.granularity == "attributed_span_changed"
            and self.locator_kind in _WHOLE_PATH_LOCATOR_KINDS
        ):
            raise ValueError(
                "an observed change records the granularity actually observed: the "
                f"{self.locator_kind!r} locator on {self.path or self.item_id!r} names a whole path, "
                "so its change is 'source_file_changed' and a whole-file change is not proof that "
                "an attributed span changed"
            )
        return self


class DetectionRelationshipPath(KnowledgeModel):
    """One recorded path a signal followed, retaining every edge and the snapshot it came from.

    ``edges`` are the recorded relationship identities the walk traversed, in order, and
    ``snapshot_side`` names which side's selection reached the last of them. A path is *recorded*
    rather than derived: a link the candidate removed is still reported with the side that still
    holds it, because the union retains both sides -- the same retention R08 implements by carrying
    both sides on every item (``models/knowledge/diff.py:288-300``, ``:318-324``). A cross-snapshot
    union is a historical lookup for candidate discovery and is never a claim that mutually
    exclusive relationships hold at once.
    """

    path_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    snapshot_side: DetectionSide
    edges: tuple[str, ...] = Field(min_length=1)
    reached_item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    realization_role: RealizationRole | None = None


class ManifestDestinationObservation(KnowledgeModel):
    """What one destination check observed, recorded rather than assumed.

    ``design/retrieval-review-design.md:370`` requires implementation to *verify* the actual durable
    archive destination rather than assume retention, and this is the verified answer as a value.
    Nothing in this leaf publishes, archives or deletes anything: it records the reference, its
    retention state and the destination's identity, and reports retention it cannot show as not
    retained.
    """

    destination_kind: str
    destination_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    resolved: bool
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class DetectionScopeManifest(KnowledgeModel):
    """The retained, addressable object one signal references -- or its unresolved reference.

    Requirement 4.1 makes the manifest a retained object rather than a recomputable worklist: it
    holds the exact revisions, observed source identities, followed edges, extractor versions,
    registered sibling and evidence references and any unresolved or truncated inputs. Requirement
    4.2 makes retention conditional on need and *stated as such*, and 4.5 refuses a retention report
    whose destination cannot be resolved. :meth:`resolve` is that refusal as a value.
    """

    manifest_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    retention_required: bool
    destination_kind: str
    destination_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    retention_basis: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    recorded_revision_ids: tuple[str, ...] = ()
    recorded_source_identities: tuple[str, ...] = ()
    followed_edges: tuple[str, ...] = ()
    extractor_versions: tuple[str, ...] = ()
    sibling_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    unresolved_inputs: tuple[str, ...] = ()
    truncated_inputs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_a_declared_destination(self) -> DetectionScopeManifest:
        if self.destination_kind not in MANIFEST_DESTINATION_KINDS:
            raise ValueError(
                f"the manifest destination kind {self.destination_kind!r} is not one of "
                f"{' | '.join(MANIFEST_DESTINATION_KINDS)}"
            )
        if (
            self.retention_required
            and self.destination_kind == "durable_publication"
            and not (self.destination_ref or "").strip()
        ):
            raise ValueError(
                "a manifest whose retention is required and whose destination is the durable "
                "publication route must record that destination's identity; retention it "
                "cannot show is not retention"
            )
        return self

    def resolve(self, observation: ManifestDestinationObservation) -> DetectionManifestResolution:
        """Report this reference as retained, or as unresolved with what would resolve it.

        The unresolved outcome names the exact reference and what would resolve it, and it is never
        an empty manifest: a signal whose manifest was discarded reports the reference, not a
        manifest with no contents.
        """

        destination_ok = (
            observation.destination_kind == "durable_publication"
            and observation.resolved
            and bool((observation.destination_ref or "").strip())
        )
        if self.retention_required and destination_ok:
            return DetectionManifestResolution(
                manifest_ref=self.manifest_ref,
                retention_state="retained",
                destination_kind=observation.destination_kind,
                destination_ref=observation.destination_ref,
                detail=(
                    f"manifest {self.manifest_ref} is retained at "
                    f"{observation.destination_ref} through the durable publication route that "
                    "KS-R12@v1 owns; this leaf records the reference and its destination and "
                    "publishes nothing itself"
                ),
            )
        return DetectionManifestResolution(
            manifest_ref=self.manifest_ref,
            retention_state="unresolved",
            destination_kind=observation.destination_kind,
            destination_ref=observation.destination_ref,
            what_would_resolve=self._what_would_resolve(observation),
            detail=(
                f"manifest {self.manifest_ref} is recorded as an unresolved reference: "
                f"{observation.detail}. It is not an empty manifest, and no content is invented "
                "for it"
            ),
        )

    def _what_would_resolve(self, observation: ManifestDestinationObservation) -> str:
        if not self.retention_required:
            return (
                "a durable assessment that needs this manifest, which is what makes retention "
                "required in the first place"
            )
        if observation.destination_kind != "durable_publication":
            return (
                "publication of this manifest through the durable publication route KS-R12@v1 "
                f"owns; the checked destination was {observation.destination_kind!r}"
            )
        return (
            "a resolvable destination on the durable publication route KS-R12@v1 owns at "
            f"{self.destination_ref or '<no destination recorded>'}"
        )


class DetectionManifestResolution(KnowledgeModel):
    """One manifest reference's answer: retained with its destination, or unresolved.

    There is no third state and no field that could hold a manifest's contents: the record reports
    the reference and the destination that was checked, which is what requirement 4.5 permits this
    leaf to do.
    """

    manifest_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    retention_state: Literal["retained", "unresolved"]
    destination_kind: str
    destination_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    what_would_resolve: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_unresolved_half(self) -> DetectionManifestResolution:
        if self.retention_state == "unresolved" and not (self.what_would_resolve or "").strip():
            raise ValueError(
                "an unresolved manifest reference must name what would resolve it; reporting the "
                "reference without that is how a discarded manifest becomes a silent gap"
            )
        if self.retention_state == "retained" and not (self.destination_ref or "").strip():
            raise ValueError(
                "a retained manifest reports the destination that was checked; retention without a "
                "destination is an assumption, not a fact"
            )
        return self


class DetectionRecordedInputSet(KnowledgeModel):
    """One signal's declared input set and the recorded discriminator that must agree with it.

    Requirement 2.3 decides agreement by the declared member's *own recorded discriminator*, not by
    prose and not by counting sides, and this model is where that is enforced. Each member records
    different facts and a signal must record its declared member's discriminator and no other's:

    * ``both_sides_declared`` records both declared sides' snapshot identities, each under its own
      selector and context, and records no counterpart-probe outcome;
    * ``union_of_both_sides`` records the union it worked over *and* the counterpart probe's
      recorded outcome in the shipped coverage vocabulary;
    * ``trigger_side_only`` records exactly one side's snapshot identity and no probe outcome at all.
    """

    declared: DeclaredInputSet
    sides: tuple[DetectionInputSide, ...] = Field(min_length=1)
    counterpart_probe: tuple[DetectionCounterpartProbe, ...] = ()
    probe_omission_declared: bool = False

    @model_validator(mode="after")
    def _require_the_members_own_discriminator(self) -> DetectionRecordedInputSet:
        recorded_sides = tuple(side.side for side in self.sides)
        if len(set(recorded_sides)) != len(recorded_sides):
            raise ValueError("a declared input set records each side it read exactly once")
        if self.declared == "both_sides_declared":
            return self._require_two_sides_without_probe(recorded_sides)
        if self.declared == "union_of_both_sides":
            return self._require_the_probe(recorded_sides)
        return self._require_one_side_without_probe(recorded_sides)

    def _require_two_sides_without_probe(
        self, recorded_sides: tuple[str, ...]
    ) -> DetectionRecordedInputSet:
        if set(recorded_sides) != {"before", "after"}:
            raise ValueError(
                "the declared input set 'both_sides_declared' records both declared sides' snapshot "
                "identities, each under its own selector and context, and records no "
                "counterpart-probe outcome; the recorded inputs name "
                f"{' | '.join(sorted(recorded_sides))} instead. A two-sided condition reported from "
                "a one-sided read is a claim about correspondence nothing compared"
            )
        if self.counterpart_probe:
            raise ValueError(
                "the declared input set 'both_sides_declared' records no counterpart-probe "
                f"outcome, and {len(self.counterpart_probe)} were recorded; a probe outcome is the "
                "'union_of_both_sides' discriminator and recording it here reports a union read "
                "that was not declared"
            )
        return self

    def _require_the_probe(self, recorded_sides: tuple[str, ...]) -> DetectionRecordedInputSet:
        if set(recorded_sides) != {"before", "after"}:
            raise ValueError(
                "the declared input set 'union_of_both_sides' records the union it worked over, "
                "which needs both sides' identities; the recorded inputs name "
                f"{' | '.join(sorted(recorded_sides))} only"
            )
        if not self.counterpart_probe:
            raise ValueError(
                "the declared input set 'union_of_both_sides' claims union semantics the signal "
                "cannot show it applied: its recorded discriminator is the union worked over *and* "
                "the counterpart probe's recorded outcome per reported item, and no probe outcome "
                "was recorded. A union declaration with no probe is the silent-widening shape this "
                "requirement exists to prevent"
            )
        if self.probe_omission_declared and not self.counterpart_probe:
            raise ValueError(  # pragma: no cover - unreachable behind the guard above
                "an omission was declared for present_outside_the_declared_selection with no probe"
            )
        return self

    def _require_one_side_without_probe(
        self, recorded_sides: tuple[str, ...]
    ) -> DetectionRecordedInputSet:
        if len(recorded_sides) != 1:
            raise ValueError(
                "the declared input set 'trigger_side_only' records exactly one side's snapshot "
                f"identity; {len(recorded_sides)} sides were recorded. The member names what the "
                "detector read, not what the policy is allowed to conclude"
            )
        if self.counterpart_probe:
            raise ValueError(
                "the declared input set 'trigger_side_only' records no counterpart-probe outcome at "
                "all, and one was recorded; the detector read one side and compared it against "
                "nothing, so it has no counterpart answer to report"
            )
        return self


class DetectionSignalPayload(KnowledgeModel):
    """One mechanically matched condition, as facts, with every required field present.

    Requirement 1.1's field set is all-required: the stable signal id, the repository, the governing
    route, the matched condition, the declared input set, the exact input snapshots, the observed
    changes, the relationship paths, the extractor and policy versions, the referenced scope
    manifest, the registered scope status and the scope limitations. A signal missing any of them
    fails construction -- following the shipped models, a field that cannot be supplied is a
    refusal and not an absent value.

    Two further properties are enforced here rather than documented:

    * the condition is an identity from the closed, policy-owned vocabulary, and a condition the
      policy does not declare is refused with the observed identity and the vocabulary's version;
    * ``detail`` is not free prose: it must equal :func:`observed_basis_detail`, which renders
      exactly the recorded basis, so a rendered judgment written into the string is refused as the
      same defect a verdict field would be.
    """

    signal_id: str = Field(pattern=UUID_PATTERN)
    repository_id: str = Field(pattern=UUID_PATTERN)
    governing_route_id: str = Field(pattern=UUID_PATTERN)
    condition: DetectionCondition
    condition_vocabulary_version: str = Field(
        default=CONDITION_VOCABULARY_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )
    input_set: DetectionRecordedInputSet
    # All four are required even though three of them are frequently empty: requirement 1.1 lists
    # each as a field a signal must carry, and a signal that observed nothing *states* that it
    # observed nothing rather than leaving the field to a default that reads the same as a
    # construction that forgot it.
    observed_changes: tuple[DetectionObservedChange, ...]
    relationship_paths: tuple[DetectionRelationshipPath, ...]
    extractor_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    scope_manifest: DetectionScopeManifest
    registered_scope_status: DetectionScopeStatus
    unmapped_changed_paths: tuple[str, ...]
    limitations: tuple[DetectionLimitation, ...]
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_closed_vocabularies(self) -> DetectionSignalPayload:
        if NO_SEMANTIC_ASSESSMENT_LIMITATION not in self.limitations:
            raise ValueError(
                "every detection signal states that it performs no semantic assessment; a signal "
                "that omitted the statement would read as one that performed none because there "
                "was nothing to assess"
            )
        if self.unmapped_changed_paths and "unmapped_changed_paths" not in self.limitations:
            raise ValueError(
                "a signal that recorded unmapped changed paths must declare the "
                "'unmapped_changed_paths' limitation: an empty list means only that the declared "
                "lookup found recorded links for those paths, and a non-empty one is a gap that "
                "cannot be dropped from the declaration that advertises it"
            )
        if self.registered_scope_status == "incomplete_scan" and "truncated_scan" not in (
            self.limitations
        ):
            raise ValueError(
                "a signal whose registered scope status is 'incomplete_scan' must declare the "
                "'truncated_scan' limitation; a bounded scan is not a complete one"
            )
        probe_omission = self.input_set.probe_omission_declared
        hidden = "records_present_outside_the_declared_selection" in self.limitations
        if probe_omission != hidden:
            raise ValueError(
                "a signal that omitted an item for reason "
                "'present_outside_the_declared_selection' must declare the "
                "'records_present_outside_the_declared_selection' limitation, and one that "
                "declares it must have omitted something for that reason"
            )
        if self.input_set.declared == "trigger_side_only" and (
            not any(side.side == "trigger" for side in self.input_set.sides)
        ):
            raise ValueError(
                "a 'trigger_side_only' signal records the trigger side it read; no side recorded "
                "under the trigger member names one"
            )
        return self

    @model_validator(mode="after")
    def _require_the_observed_basis_as_detail(self) -> DetectionSignalPayload:
        """Refuse a detail string that is not the rendering of this signal's recorded basis.

        Requirement 1.6 bounds a signal's own prose to observed basis -- the recorded condition
        identity, the recorded paths and the recorded limitations -- and requirement 5.2 refuses a
        verdict written into a prose field as it refuses a verdict field. Both are enforced by
        making the string a *rendering* rather than authored text, and the refusal names the field
        and the record it would have been carried on.
        """

        expected = observed_basis_detail(
            condition=self.condition,
            relationship_paths=tuple(path.path_id for path in self.relationship_paths),
            limitations=self.limitations,
        )
        if self.detail.strip() == expected:
            return self
        if self.input_set.declared == "trigger_side_only":
            raise ValueError(
                "the detail of a 'trigger_side_only' signal asserts something this signal did not "
                "read: the detector read exactly one side and compared it against nothing, so it "
                "may not claim a two-sided join, may not claim the other side was unchanged, and "
                "may not claim absence of a counterpart. The absence of a counterpart is a scope "
                "limitation, not a finding of equality, and 'detail' must record the observed "
                f"basis ({expected!r})"
            )
        raise ValueError(
            "the 'detail' field of a detection signal is bounded to observed basis -- the recorded "
            "condition identity, the recorded paths and the recorded limitations -- and a rendered "
            "judgment written into it is refused exactly as a verdict field is. The field must "
            f"equal {expected!r}"
        )

    def ordered_changes(self) -> tuple[DetectionObservedChange, ...]:
        """Return the observed changes in the declared deterministic order over item identity."""

        return tuple(sorted(self.observed_changes, key=lambda change: change.item_id))


class DetectionRunPayload(KnowledgeModel):
    """One execution of one detection policy, reproducible from its recorded inputs.

    A run records the policy identity it executed under and its exact inputs as identities -- the
    snapshots it read, the code trees it resolved against, the selectors it applied and the
    repository binding -- and it carries the declared deterministic total order over signal identity,
    so reproducibility is a comparison of two ordered sequences and not of two sets.

    ``declared_input_sets`` is the set of members the run's own signals declared. Requirement 2.4
    keeps the declaration per signal: a run that executed one policy over two signals with different
    declared input sets records both, and no field here collapses them into a run-level default.
    """

    run_id: str = Field(pattern=UUID_PATTERN)
    repository_id: str = Field(pattern=UUID_PATTERN)
    assessed_repository_id: str = Field(pattern=UUID_PATTERN)
    governing_route_id: str = Field(pattern=UUID_PATTERN)
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    extractor_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    condition_vocabulary_version: str = Field(
        default=CONDITION_VOCABULARY_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )
    input_sides: tuple[DetectionInputSide, ...] = ()
    declared_input_sets: tuple[DeclaredInputSet, ...] = ()
    recorded_conditions: tuple[DetectionCondition, ...] = ()
    signal_order: tuple[str, ...] = ()
    limitations: tuple[DetectionLimitation, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_reproducible_run(self) -> DetectionRunPayload:
        if NO_SEMANTIC_ASSESSMENT_LIMITATION not in self.limitations:
            raise ValueError(
                "every detection run states that it performs no semantic assessment; no field of "
                "this record could carry one, and the statement is not left to be inferred"
            )
        if len(set(self.signal_order)) != len(self.signal_order):
            raise ValueError(
                "the declared deterministic total order over signal identity names each signal "
                "once: a repeated identity is not an order over a set of signals"
            )
        for member in self.declared_input_sets:
            if member not in DECLARED_INPUT_SETS:  # pragma: no cover - the Literal refuses first
                raise ValueError(
                    f"the declared input set {member!r} is not one this contract declares"
                )
        expected = _run_basis_detail(self)
        if self.detail.strip() != expected:
            raise ValueError(
                "the 'detail' field of a detection run is bounded to observed basis: the recorded "
                "policy identity, the declared input sets and the recorded limitations. A rendered "
                f"judgment written into it is refused exactly as a verdict field is; the field must "
                f"equal {expected!r}"
            )
        return self

    @model_validator(mode="after")
    def _require_an_order_for_every_condition(self) -> DetectionRunPayload:
        """Refuse a run whose declared order and recorded conditions describe different sets.

        The order is declared over signal identity and is compared element-wise, so a run whose
        order names a different number of signals than it recorded conditions is a record whose
        reproducibility cannot be checked at all.
        """

        if len(self.signal_order) != len(self.recorded_conditions):
            raise ValueError(
                "a detection run's declared order names exactly the signals it recorded: "
                f"{len(self.signal_order)} ordered identities against "
                f"{len(self.recorded_conditions)} recorded conditions"
            )
        return self


def _run_basis_detail(run: DetectionRunPayload) -> str:
    members = " | ".join(run.declared_input_sets) or "<none>"
    limitations = " | ".join(run.limitations) or "<none>"
    return (
        f"policy={run.policy_version}; extractor={run.extractor_version}; "
        f"conditions={run.condition_vocabulary_version}; declared_input_sets={members}; "
        f"ordered_signals={len(run.signal_order)}; limitations={limitations}"
    )


class DetectionSignalSet(KnowledgeModel):
    """The signals one run produced, in the run's declared deterministic order.

    The order is the run's own recorded ``signal_order`` rather than whatever order rows happened to
    come back in, which is what makes "reproducibility compares two ordered sequences" a property of
    the record instead of a property of a query.
    """

    run_id: str = Field(pattern=UUID_PATTERN)
    signals: tuple[DetectionSignalPayload, ...] = ()

    def ordered_ids(self) -> tuple[str, ...]:
        """Return the signal identities in the recorded order."""

        return tuple(signal.signal_id for signal in self.signals)


class DetectionRunRequest(KnowledgeModel):
    """One request to record a detection run and the signals it produced."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    provenance: Authorship
    run: DetectionRunPayload
    signals: tuple[DetectionSignalPayload, ...] = ()
    # The datasets this run measured, as the paths their stores were opened from. They are the
    # boundary requirement 7.1 refuses to cross: a detection write may not enter the assessed
    # dataset's own measurement transaction, and this is what makes that checkable rather than
    # asserted.
    assessed_database_paths: tuple[str, ...] = ()


class DetectionRunInputDifference(KnowledgeModel):
    """One input or version that differs between a recorded run and its re-execution.

    A changed snapshot identity, policy version, extractor version or declared input set each make
    the two runs distinct facts, exactly as the shipped ``KnowledgeDiffBinding`` makes a moved
    candidate unable to continue an older comparison (``models/knowledge/diff.py:256-286``). The
    recorded and re-executed values are both carried, so the difference is inspectable rather than
    merely reported.
    """

    kind: Literal[
        "snapshot_identity",
        "code_tree",
        "selector",
        "policy_version",
        "extractor_version",
        "declared_input_set",
        "condition",
    ]
    side: DetectionSide | None = None
    recorded: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    reexecuted: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class DetectionRunReproduction(KnowledgeModel):
    """The answer to "was this run reproduced": two ordered sequences, and what differs.

    There is no field that could report the re-execution as "the same run": the type carries the
    recorded run's identity, the re-executed run's identity and the differences, so a caller reads
    which two runs are being compared. Requirement 3.4 refuses a re-execution that differs from the
    recorded run being reported as the same run and refuses it overwriting the recorded one; nothing
    in this model writes anything.
    """

    recorded_run_id: str = Field(pattern=UUID_PATTERN)
    reexecuted_run_id: str = Field(pattern=UUID_PATTERN)
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    reproduced: bool
    ordered_signals_equal: bool
    recorded_signal_order: tuple[str, ...] = ()
    reexecuted_signal_order: tuple[str, ...] = ()
    differences: tuple[DetectionRunInputDifference, ...] = ()

    @model_validator(mode="after")
    def _require_one_verdict_from_the_facts(self) -> DetectionRunReproduction:
        """Refuse a reproduction whose verdict disagrees with the facts it carries."""

        if self.reproduced and (self.differences or not self.ordered_signals_equal):
            raise ValueError(
                "a run reported as reproduced carries no differing input or version and two equal "
                "ordered sequences; a difference is what makes a re-execution a distinct fact"
            )
        if self.ordered_signals_equal != (
            self.recorded_signal_order == self.reexecuted_signal_order
        ):
            raise ValueError(
                "the ordered-signals flag must state whether the two recorded orders are equal; a "
                "reproduction that reports an order it does not carry is not a comparison"
            )
        return self


class DetectionRunDifference(KnowledgeModel):
    """One recorded run and one re-execution, reported as two distinct facts when they differ."""

    recorded_run_id: str = Field(pattern=UUID_PATTERN)
    reexecuted_run_id: str = Field(pattern=UUID_PATTERN)
    same_identity: bool = False
    differences: tuple[DetectionRunInputDifference, ...] = ()


class DetectionRunCurrentness(KnowledgeModel):
    """Whether a recorded run is current under the versions now in force.

    Requirement 3.6 permits code to *mark* a run stale on this comparison and forbids it to
    reinterpret the run's signals for the new versions, re-label them current, or silently re-run and
    present the new result as the old one. The model therefore carries the recorded versions as
    facts beside the current ones, and it carries no field holding a re-interpretation or a
    re-labelled signal.
    """

    run_id: str = Field(pattern=UUID_PATTERN)
    recorded_policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    current_policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    recorded_extractor_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    current_extractor_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    binding_state: Literal["current", "stale"]
    differing_versions: tuple[str, ...] = ()
    signals_unchanged: Literal[True] = True

    @model_validator(mode="after")
    def _require_the_state_to_follow_from_the_versions(self) -> DetectionRunCurrentness:
        differing = tuple(
            name
            for name, recorded, current in (
                ("policy_version", self.recorded_policy_version, self.current_policy_version),
                (
                    "extractor_version",
                    self.recorded_extractor_version,
                    self.current_extractor_version,
                ),
            )
            if recorded != current
        )
        if differing != self.differing_versions:
            raise ValueError(
                "a run's currentness names exactly the version axes that moved: expected "
                f"{' | '.join(differing) or '<none>'}, recorded "
                f"{' | '.join(self.differing_versions) or '<none>'}"
            )
        expected_state = "stale" if differing else "current"
        if self.binding_state != expected_state:
            raise ValueError(
                f"the binding state follows from the version comparison: {expected_state!r} is what "
                f"the recorded and current versions give, not {self.binding_state!r}"
            )
        return self


class DetectionRunResult(KnowledgeModel):
    """The typed outcome of one detection operation: a recorded run, a read run, or one refusal."""

    state: Literal["created", "read", "refused"]
    operation: Literal["record_detection_run", "read_detection_run"] = "record_detection_run"
    repository_id: str = Field(pattern=UUID_PATTERN)
    run: DetectionRunPayload | None = None
    signals: tuple[DetectionSignalPayload, ...] = ()
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> DetectionRunResult:
        if self.state == "refused" and (self.refusal is None or self.run is not None):
            raise ValueError("a refused detection operation carries its refusal and no run")
        if self.state != "refused" and (self.run is None or self.refusal is not None):
            raise ValueError("a served detection operation carries its run and no refusal")
        return self

    def ordered_signal_ids(self) -> tuple[str, ...]:
        """Return the served signals in the run's recorded order."""

        order = {signal.signal_id: signal for signal in self.signals}
        if self.run is None:
            return tuple(order)
        return tuple(name for name in self.run.signal_order if name in order)


def observed_basis_detail(
    *,
    condition: str,
    relationship_paths: Sequence[str],
    limitations: Sequence[str],
) -> str:
    """Render the one detail string a signal is allowed to hold.

    Requirement 1.6 bounds a signal's prose to observed basis: the recorded condition identity, the
    recorded paths and the recorded limitations. This is that sentence as a function, so the record
    holds a *rendering* of its own facts rather than authored text, and a judgment written into the
    string cannot survive construction.
    """

    paths = " | ".join(relationship_paths) or "<none>"
    limitations = " | ".join(limitations) or "<none>"
    return f"condition={condition}; followed_paths={paths}; limitations={limitations}"
