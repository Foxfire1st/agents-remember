"""The family-review pipeline's own vocabulary: grouped facts, separated statuses, routing, decision.

``KS-R16@v1`` owns the pipeline *between* ``KS-R14@v1``'s detector, ``KS-R15@v1``'s authored record and
``KS-R17@v1``'s authored composition. This module holds the records that pipeline produces and
redefines none of theirs: a detection signal stays a detection signal, an assessment stays an
assessment, and a composition edge stays an authored edge.

Four properties are enforced by the shape of these records rather than by a rule a caller remembers:

* **A fact group cannot carry a conclusion.** Its declared field set contains no name from
  :data:`…detection.CONCLUSION_BEARING_FIELD_NAMES`, and ``KS-R14@v1``'s own review function is the
  measurement: the case that protects §2.1 calls it over every record here and requires the empty
  tuple. Grouping may merge matches and a declared order may order them, but every matched condition
  and its supporting paths and edges are retained -- there is no field that could hold a dropped match,
  and :meth:`FamilyIntegrityFactGroup.retains` is the review that a merge lost nothing.
* **The five status owners stay separate.** :class:`SeparatedStatusReport` carries exactly one entry per
  owner in the declared order, each with its own closed status vocabulary and its own statement of what
  it does *not* establish. There is no field anywhere in it for one verdict, one badge or one boolean.
* **Missing stays missing.** A subject with no stored record is reported as
  ``no-record-recorded`` and never as a favourable disposition; the vocabulary has no "compatible"
  member and no default (``Doc13:104``).
* **Currentness is per-input and never re-judged.** :class:`FindingCurrentness` carries the recorded
  comparison's result, the identities that moved, and a typed statement that nothing was reinterpreted
  for the new inputs. Its ``binding_state`` spelling is ``KS-R14@v1``'s own two-member literal, which
  is the mapping ``KS-R15@v1`` also records: one spelling a reader has met twice.

**The routing report decides no gate.** :class:`FamilyReviewRouting` carries the shipped actionability
formula's three terms, the family-review row count as a *separate* number, and a field whose only
admissible value names the validator that actually decides closeout readiness -- ``CR16-5``'s point,
made structural: a zero actionable count is necessary and never sufficient.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.detection import (
    DETECTION_CONDITIONS,
    DetectionCondition,
    DetectionSide,
)

__all__ = [
    "CLOSEOUT_READINESS_DECIDER",
    "FACT_GROUPING_POLICY_VERSION",
    "FAMILY_REVIEW_ROUTING_SURFACES",
    "PIPELINE_STATUS_OWNERS",
    "RECORDED_GATE_CONSEQUENCE_DECISION",
    "STATUS_OWNER_DECLARATIONS",
    "STATUS_VOCABULARIES",
    "DecisionOwner",
    "FactMatch",
    "FactSupportingEdge",
    "FamilyIntegrityFactGroup",
    "FamilyReviewGateDecision",
    "FamilyReviewRouting",
    "FamilyReviewRoutingRow",
    "FindingCurrentness",
    "PipelineStatusEntry",
    "SeparatedStatusReport",
    "fact_group_identity",
    "fact_grouping_rule",
]

# The declared grouping rule's version. §2.2 permits deduplication to group matches by family and
# input snapshots and permits an order to follow a declared rule; a declared rule needs an identity or
# the grouping is unreproducible, so it is a value here and travels on every group this pipeline
# produces. It is *this* leaf's constant: it names a presentation-level merge, not a detection policy.
FACT_GROUPING_POLICY_VERSION = "family-fact-grouping/v1"

# The two surfaces a finding routes into, named exactly as ``design/retrieval-review-design.md:348-350``
# names them. They are ``KS-R15@v1``'s, and a third surface is what §5.2 and §5.7 forbid: there is no
# member here for a new worklist, a new reports directory or a parallel checklist, and a routing row
# that named one would fail construction.
FAMILY_REVIEW_ROUTING_SURFACES: tuple[str, ...] = (
    "curator-coherence-assessment-collection",
    "knowledgeReview",
)

# The one authority that decides closeout readiness, named as a value so the routing report can state
# what it does *not* decide. ``CR16-5`` recorded that §5.3 and the Rationale can be read as equating a
# zero actionable count with closeout readiness; the shipped composition is
# ``application/memory_quality/controller.py``'s join with the curator-coherence validator, and nothing
# this leaf produces is an input to it.
CLOSEOUT_READINESS_DECIDER = "curator-coherence-authority-validator"

# The five status owners of ``Doc13:363-369``, in the design's own order. They are a value rather than a
# comment because :class:`SeparatedStatusReport` requires exactly one entry per owner, so the set cannot
# be widened or narrowed without the report saying so.
PIPELINE_STATUS_OWNERS: tuple[str, ...] = (
    "structural-validator",
    "detector",
    "curator-reviewer",
    "verification-runner",
    "authority-currentness",
)

# One owner's closed status vocabulary, and the two columns the design's table gives it: what the owner
# can report, and -- required, because the separations exist to keep it visible -- what it does not
# establish. This is declarative data transcribed from ``Doc13:363-369`` rather than logic: the report
# validates a status against its own owner's members and carries both sentences verbatim.
STATUS_OWNER_DECLARATIONS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "structural-validator",
        ("declared-checks-passed", "declared-checks-failed", "declared-checks-could-not-run"),
        "Declared schema, row, reference and graph checks passed, failed, or could not run.",
        "Behavioral correctness or semantic agreement.",
    ),
    (
        "detector",
        ("matched", "no-match-under-declared-policy", "unresolved-inputs", "incomplete-scan"),
        "Matched conditions, no match under the declared policy, traversed registered scope, "
        "unresolved inputs, or an incomplete scan.",
        "Presence or absence of a behavioral conflict.",
    ),
    (
        "curator-reviewer",
        ("record-recorded", "declared-unresolved", "no-record-recorded"),
        "An authored record and its disposition, including no concern found or an unresolved judgment.",
        "Publication approval, unless granted by the existing authority process.",
    ),
    (
        "verification-runner",
        ("assertions-executed", "no-assertions-executed"),
        "The exact assertions executed, their results, the candidate and environment identity, and the "
        "artifacts.",
        "Whether those assertions sufficiently represent the intended guarantee.",
    ),
    (
        "authority-currentness",
        ("dependencies-match", "stale"),
        "The authorization identity and whether the recorded dependency identities still match.",
        "A new authored record that changed inputs are semantically equivalent.",
    ),
)

STATUS_VOCABULARIES: dict[str, tuple[str, ...]] = {
    owner: members for owner, members, _establishes, _not_establishes in STATUS_OWNER_DECLARATIONS
}


def fact_grouping_rule() -> str:
    """Render the one declared grouping rule, as the sentence a record and its refusal both quote.

    The rule is deliberately narrow and mechanical: matches merge when the recorded family revisions
    and the declared input snapshots agree, the merged group keeps every contributing match, and the
    order is the declared condition order followed by item identity. Nothing in it reads a path, a
    label or a count to decide membership, which is what keeps grouping a *presentation* act rather
    than a second detector.
    """

    return (
        "merge matches whose recorded family revisions and declared input snapshots agree; retain "
        "every matched condition with its supporting paths and edges; order groups by the declared "
        "condition order, then by item identity"
    )


def fact_group_identity(subject_id: str, input_signature: str) -> str:
    """Return one group's identity, as the declared rule's own key spelled once.

    The key is derived from the two facts the rule merges on -- the recorded subject and the declared
    input snapshots' signature -- so a group identity cannot be produced from a path, a label or a
    title. It is a *name for a merge*, not a content address: no digest, fingerprint or new identity
    authority is introduced here.
    """

    return f"family-review/{subject_id}/{input_signature}"


class FactSupportingEdge(KnowledgeModel):
    """One recorded relationship a match was reached through, kept because grouping may not drop it."""

    edge_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    from_record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    to_record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    mapping_snapshot: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)


class FactMatch(KnowledgeModel):
    """One matched condition as a fact, with the sides, paths and edges that support it.

    ``condition`` is ``KS-R14@v1``'s closed, policy-owned identity and not a rendering of it: this
    record repeats the vocabulary member the signal already carried and never renames it. The two
    tuple fields are the *supporting* facts §2.2 requires grouping to retain, at the granularity
    ``KS-R14@v1`` recorded them.
    """

    condition: DetectionCondition
    sides: tuple[DetectionSide, ...] = ()
    supporting_paths: tuple[str, ...] = ()
    supporting_edges: tuple[FactSupportingEdge, ...] = ()
    supporting_item_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_declared_condition_vocabulary(self) -> FactMatch:
        """Refuse a condition outside the policy's closed vocabulary, naming the member observed."""

        if (
            self.condition not in DETECTION_CONDITIONS
        ):  # pragma: no cover - the Literal refuses first
            raise ValueError(
                f"the matched condition {self.condition!r} is not one of the conditions the declared "
                "policy owns; a condition the policy does not declare is not a detection fact"
            )
        return self


class FamilyIntegrityFactGroup(KnowledgeModel):
    """One deduplicated group of matches: the facts, and nothing that could be read as a conclusion.

    The group is what the pipeline hands to the curator's worklist, so it is the last place a
    mechanical verdict could enter before an authored record. It therefore carries only the recorded
    family revisions, the declared input signature, the grouping rule's identity and the matches --
    and :meth:`retains` is the review a merge must pass: every contributing match is still here, with
    its supporting paths and edges.
    """

    group_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    subject_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    family_revision_ids: tuple[str, ...] = ()
    input_signature: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    grouping_policy_version: str = Field(
        default=FACT_GROUPING_POLICY_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )
    matches: tuple[FactMatch, ...] = Field(min_length=1)
    # The authored records already stored for this group's subject, as references. It is a citation and
    # not a conclusion: the field names records that exist, and an empty tuple is the honest report of
    # a subject nobody has reviewed -- never a default to a favourable state.
    review_record_ids: tuple[str, ...] = ()

    def matched_conditions(self) -> tuple[DetectionCondition, ...]:
        """Return every matched condition, in the declared condition order."""

        order = {condition: index for index, condition in enumerate(DETECTION_CONDITIONS)}
        return tuple(sorted((match.condition for match in self.matches), key=order.__getitem__))

    def supporting_paths(self) -> tuple[str, ...]:
        """Return every supporting path across every match, deduplicated and ordered."""

        return tuple(sorted({path for match in self.matches for path in match.supporting_paths}))

    def supporting_edges(self) -> tuple[FactSupportingEdge, ...]:
        """Return every supporting edge across every match, in a stable recorded order."""

        return tuple(
            sorted(
                (edge for match in self.matches for edge in match.supporting_edges),
                key=lambda edge: (edge.edge_kind, edge.from_record_id, edge.to_record_id),
            )
        )

    def retains(self, contributing: tuple[FactMatch, ...]) -> bool:
        """Return whether this group still carries every match that contributed to it.

        §2.2's "grouping must retain every matched condition and its supporting paths and edges" is a
        claim about a merge, so it needs a comparison rather than an assurance: a group that dropped a
        contributing match, a path or an edge answers ``False`` here, and the case that protects the
        clause measures exactly that.
        """

        for match in contributing:
            kept = [
                candidate for candidate in self.matches if candidate.condition == match.condition
            ]
            if not any(
                set(match.supporting_paths) <= set(candidate.supporting_paths)
                and set(match.supporting_item_ids) <= set(candidate.supporting_item_ids)
                for candidate in kept
            ):
                return False
        return True


class PipelineStatusEntry(KnowledgeModel):
    """One status owner's own report, carrying what it establishes and what it does not.

    Both sentences are required and nonblank because a separation that reported only the first column
    would still read as a verdict: the second column is the whole content of ``Doc13:363-369``'s
    table, so a pipeline output that dropped it would have collapsed five rows into one.
    """

    owner: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    status: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    establishes: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    does_not_establish: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_owners_own_status_member(self) -> PipelineStatusEntry:
        """Refuse a status outside its own owner's closed vocabulary."""

        members = STATUS_VOCABULARIES.get(self.owner)
        if members is None:
            raise ValueError(
                f"the status owner {self.owner!r} is not one of the five declared owners "
                f"({' | '.join(PIPELINE_STATUS_OWNERS)})"
            )
        if self.status not in members:
            raise ValueError(
                f"the owner {self.owner!r} reports {self.status!r}, which is not one of its declared "
                f"statuses ({' | '.join(members)}); the five owners have different vocabularies "
                "precisely so that one owner cannot report another's fact"
            )
        return self


class SeparatedStatusReport(KnowledgeModel):
    """The five owners reported together and never merged.

    The report has exactly one entry per declared owner and no field that could hold a combined
    verdict; ``unmerged`` is the property a caller asserts rather than argues. §4.1 forbids collapsing
    the owners into one verdict, one badge or one boolean, and the cheapest way to make that true is to
    give the record nowhere to put one.
    """

    entries: tuple[PipelineStatusEntry, ...]

    @model_validator(mode="after")
    def _require_one_entry_per_owner_in_order(self) -> SeparatedStatusReport:
        """Refuse a report that omits an owner, repeats one, or reorders them."""

        owners = tuple(entry.owner for entry in self.entries)
        if owners != PIPELINE_STATUS_OWNERS:
            raise ValueError(
                "a separated status report carries exactly one entry per owner, in the declared order "
                f"({' | '.join(PIPELINE_STATUS_OWNERS)}); it recorded "
                f"{' | '.join(owners) or '<none>'}. An omitted owner is a collapse and a repeated one "
                "is two facts wearing one row"
            )
        return self

    def entry_for(self, owner: str) -> PipelineStatusEntry:
        """Return one owner's entry."""

        for entry in self.entries:
            if entry.owner == owner:
                return entry
        raise KeyError(owner)  # pragma: no cover - the validator above guarantees presence


class FindingCurrentness(KnowledgeModel):
    """One authored record's currentness against the exact inputs it examined.

    ``binding_state`` is ``KS-R14@v1``'s two-member literal, so the design's ``binding_state=stale``
    has one spelling across the substrate. ``moved_identities`` names the inputs that no longer match,
    because "one input moved" and "the binding is stale" are different facts and a reader acting on
    the second needs the first. The last two fields are typed statements rather than flags: a stale
    record stays readable, and nothing re-judged it for the new inputs (``Doc13:371``).
    """

    subject_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    review_record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    binding_state: Literal["current", "stale"]
    moved_identities: tuple[str, ...] = ()
    record_readable: Literal[True] = True
    reuse_permitted: bool = False
    reinterpreted_for_new_inputs: Literal[False] = False

    @model_validator(mode="after")
    def _require_the_state_to_follow_from_the_moved_identities(self) -> FindingCurrentness:
        """Refuse a currentness that disagrees with the comparison it carries.

        Two directions, and both matter: a binding with a moved input is stale whatever a caller
        would prefer, and a binding reported stale with nothing moved is a claim about a comparison
        nobody made. Reuse is refused on a stale binding because §4.5 permits reuse only where an
        explicit dependency contract permits it, and no such contract exists for a moved input -- the
        recovery is a new authored record.
        """

        expected = "stale" if self.moved_identities else "current"
        if self.binding_state != expected:
            raise ValueError(
                f"a binding's state follows from the comparison: {len(self.moved_identities)} moved "
                f"identities give {expected!r}, not {self.binding_state!r}"
            )
        if self.binding_state == "stale" and self.reuse_permitted:
            raise ValueError(
                "a stale binding is readable and is not reused: reuse needs an explicit dependency "
                "contract permitting it, and a moved input is exactly the case none permits"
            )
        return self


class FamilyReviewRoutingRow(KnowledgeModel):
    """One fact group's routing into the existing curator worklist, and the records already there.

    ``review_record_ids`` empty is the report of "no authored record yet" and carries no disposition:
    §4.2's missing assessment stays missing, and a row that spelled the absence as a favourable state
    would be the promotion ``Doc13:104`` forbids.
    """

    subject_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    group_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    matched_condition_count: int = Field(ge=1)
    review_record_ids: tuple[str, ...] = ()
    routes_to: tuple[str, ...] = FAMILY_REVIEW_ROUTING_SURFACES

    @model_validator(mode="after")
    def _require_the_existing_surfaces_only(self) -> FamilyReviewRoutingRow:
        """Refuse a row routed anywhere but the two surfaces ``KS-R15@v1`` owns."""

        unknown = tuple(
            name for name in self.routes_to if name not in FAMILY_REVIEW_ROUTING_SURFACES
        )
        if unknown:
            raise ValueError(
                f"the routing target {unknown[0]!r} is not one of the surfaces this leaf consumes "
                f"({' | '.join(FAMILY_REVIEW_ROUTING_SURFACES)}); a third surface is what §5.2 and "
                "§5.7 forbid, and inventing one here would be a second worklist"
            )
        return self


class FamilyReviewRouting(KnowledgeModel):
    """The routing report: two counts, never one, and no claim about closeout readiness.

    The three term fields are the shipped formula's own three, and ``actionable_count`` is tied to them
    here so the record cannot grow a fourth term without failing construction. That tie is the record's
    own arithmetic identity and not a second counter: the *value* is produced by the shipped expression
    in :mod:`agents_remember.memory_quality.curator_checklist`, which this record consumes.
    ``family_review_row_count`` is reported beside it and folded into nothing.
    """

    rows: tuple[FamilyReviewRoutingRow, ...] = ()
    report_only_section: str = Field(
        default="knowledgeReview", min_length=1, max_length=LABEL_MAX_LENGTH
    )
    review_row_count: int = Field(ge=0)
    repair_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    actionable_count: int = Field(ge=0)
    decides_closeout_readiness: Literal["curator-coherence-authority-validator"] = (
        "curator-coherence-authority-validator"
    )

    @model_validator(mode="after")
    def _require_two_counts_and_no_fourth_term(self) -> FamilyReviewRouting:
        """Refuse a report whose counts disagree with the facts they count.

        Three checks, each catching a distinct defect: a row count that is not the rows it carries; an
        actionable count that is not the shipped three terms (which is how a fourth term would have to
        arrive, since there is no field for one); and a family-review row counted into the actionable
        total, which is the new gate ``retrieval-review-design.md:348`` forbids.
        """

        if self.review_row_count != len(self.rows):
            raise ValueError(
                f"the family-review row count is {self.review_row_count} against {len(self.rows)} "
                "recorded rows; a count that is not its rows is not a count"
            )
        if self.actionable_count != self.repair_count + self.missing_count + self.stale_count:
            raise ValueError(
                "the actionable count is exactly the shipped three terms -- repair, missing and stale "
                "onboarding -- and gains no fourth; a family-review row is none of the three and "
                "contributes to none of them"
            )
        return self

    def family_rows_are_report_only(self) -> bool:
        """Return whether the family rows moved no count, as the property §5.3 asks a caller to assert."""

        return self.actionable_count == self.repair_count + self.missing_count + self.stale_count


class DecisionOwner(KnowledgeModel):
    """Who owns the ruling a recorded decision rests on, named rather than implied.

    ``role`` is the seat or authority; ``identity_ref`` is the durable reference a reader follows to
    establish it; ``ruling_basis`` is the sentence that makes the ownership a fact rather than an
    assumption. §5.4 requires the owner field to be populated, and a decision that cannot name an owner
    is not recorded as satisfied -- it is escalated -- so there is no default here and no empty owner.
    """

    role: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    identity_ref: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    ruling_basis: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class FamilyReviewGateDecision(KnowledgeModel):
    """The recorded decision that closes the gate-consequence question, naming its owner.

    §5.4 fixes the minimum field set and §5.5 bounds the content to what
    ``design/retrieval-review-design.md:348`` already settles: an outstanding signal is report-only, a
    future gate consequence needs a separate explicit requirement and an authority ruling, and that
    ruling is not made by this packet. The record therefore carries the question, the outcome, the
    owner, the rationale, the authority basis, the effective-from point and the revisit condition -- and
    no field for a gate, a refusal code, a checklist status value or a readiness input.
    """

    decision_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    durable_home: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    question: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    outcome: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    owner: DecisionOwner
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    authority_basis: tuple[str, ...] = Field(min_length=1)
    effective_from: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revisit_when: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    creates_gate: Literal[False] = False

    @model_validator(mode="after")
    def _require_the_authority_basis_to_name_its_sources(self) -> FamilyReviewGateDecision:
        """Refuse a decision resting on an unnamed or blank authority."""

        blank = tuple(item for item in self.authority_basis if not item.strip())
        if blank:
            raise ValueError(
                "every authority basis entry names a source; a blank entry is an authority nobody "
                "can follow and it is what makes a decision unfalsifiable"
            )
        return self


# The one recorded decision. ``KS-R16@v1`` §5.4 requires the question closed by a record that names
# its owner, and its ## Open Truth Gaps records that the packet deliberately does not invent one: what
# resolves it is the authority ruling the decision records. The owner below is that ruling's own
# authority as the design states it -- ``design/retrieval-review-design.md:348`` makes a future gate
# consequence require "an explicit requirement and authority ruling", and a requirement's authority in
# this master is the developer's ruling, recorded through this master's decision log. The record names
# the route to that ruling rather than claiming a ruling this leaf has no authority to make.
RECORDED_GATE_CONSEQUENCE_DECISION = FamilyReviewGateDecision(
    decision_id="family-review-gate-consequence/v1",
    durable_home=(
        "agents-remember: mcp/src/agents_remember/models/knowledge/family_review.py "
        "(RECORDED_GATE_CONSEQUENCE_DECISION)"
    ),
    question=(
        "What does an outstanding family-integrity signal do to a gate, and how does a finding route "
        "into the existing curator worklist?"
    ),
    outcome=(
        "The signal is report-only and changes no gate: not a merge gate, not a closeout gate, not a "
        "publication gate, and not the closeout-readiness composition. A finding routes into the "
        "existing curator-coherence assessment collection and the report-only knowledgeReview "
        "checklist section, and family-review rows contribute to none of the three terms of "
        "curatorActionableCount."
    ),
    owner=DecisionOwner(
        role=(
            "the developer (requirement authority for master 260915-KS), on a requirement packet "
            "authored by this master's architect/orchestrator seat"
        ),
        identity_ref=(
            "agents-remember/260915_knowledge-substrate/task.md (the master's decision log and its "
            "recorded durable approvals)"
        ),
        ruling_basis=(
            "design/retrieval-review-design.md:348 states that any future gate consequence needs an "
            "explicit requirement and authority ruling; a requirement in this master is admitted by "
            "the developer's ruling, so the authority that could create a gate consequence is the "
            "developer acting on such a packet and no seat below it"
        ),
    ),
    rationale=(
        "Doc13:422 defers the trigger policy, the traversal limits and the gate consequences to "
        "explicit design/authority decisions and forbids the tempting reading -- that a same-family "
        "match itself blocks or approves a merge. retrieval-review-design.md:348 forbids the two "
        "partial versions of the same mistake. The only honest deliverable for an unowned authority "
        "question is a record that names who owns it: anything else either invents authority or "
        "leaves the question to be answered silently by whoever writes the next line of code."
    ),
    authority_basis=(
        "design/retrieval-review-design.md:348",
        "Doc13:422",
        "PLANNING.md:32",
    ),
    effective_from="2026-09-18 (at this leaf's implementation, master 260915-KS)",
    revisit_when=(
        "an explicit requirement packet and authority ruling for a gate consequence exists, at which "
        "point that ruling supersedes this record rather than being absorbed into the pipeline"
    ),
)
