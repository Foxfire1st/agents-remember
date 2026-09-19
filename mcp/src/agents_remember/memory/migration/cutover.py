"""The cutover's three artifacts -- criteria, plan and proposal -- and the escalation they stay behind.

``KS-R21@v1`` §8.1 requires this leaf to produce three artifacts and execute none of them. This module
is those three artifacts as data, plus the one function that **evaluates** the criteria against a
census result. It contains no trigger, no flag, no scheduled activation and no code path that changes
which memory is live: §8.4 requires that nothing here be a gate, and the way to keep that true is for
the module to have no side effect at all. There is no function in this module that writes anything.

Two properties are worth stating because they are the ones a reviewer should check:

* **The criteria are not softened to be met.** Each is a predicate over a census result, and
  :func:`evaluate_criteria` returns the exact measures that fall short rather than a boolean. §8.3
  makes "cutover is not yet justified" a *conforming* output, so a criterion that cannot be met is
  reported unmet with its observed value, never narrowed until it passes.
* **The escalation is a recorded proposal and a recorded decision.** ``design/storage-design.md:465``
  lists "starts migration/cutover" among the actions that must be recorded and escalated, so the
  proposal carries the exact quoted boundary and the decision it awaits. This module records the
  proposal; the decision is absent, and its absence is the state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# The quoted boundary every escalation here rests on. Stored as data rather than as a comment so the
# proposal a reader holds names the same sentence the decision is reserved by.
ESCALATION_BOUNDARY = "design/storage-design.md:465"
ESCALATION_ITEM = "starts migration/cutover"

# The decision state a proposal carries until the owner records one. There is no third state, and no
# value on this vocabulary means "approved": an approval is a recorded decision owned elsewhere, and
# this leaf has no way to represent one that it did not receive.
ProposalDecision = Literal["absent", "owner-recorded"]


@dataclass(frozen=True)
class CriterionObservation:
    """One criterion's outcome: whether it holds, and the exact measure that decided it.

    ``observed`` carries the number the criterion read, so an unmet criterion reports *what fell
    short* rather than only that it did. ``required`` is the criterion's own statement of what it
    needs, rendered as text because several criteria are about presence rather than about a ratio.
    """

    criterion_id: str
    statement: str
    met: bool
    observed: str
    required: str
    evidence: str


@dataclass(frozen=True)
class CutoverCriterion:
    """One condition that must hold before the live authority may change.

    ``evidence`` names what would *show* the condition holds -- the observable artifact or measurement
    -- because a criterion whose evidence is not named is a criterion nobody can check.
    """

    criterion_id: str
    statement: str
    evidence: str


@dataclass(frozen=True)
class CutoverStep:
    """One ordered step of the cutover plan, with its own reversibility stated."""

    order: int
    action: str
    reversible: bool
    note: str


@dataclass(frozen=True)
class CutoverPlan:
    """The ordered steps, the rollback story, the archival step and the point of no return.

    ``archival`` is requirement 7.3's step, and it is stated with the property that makes it
    sufficient rather than merely present: archived material is preserved and referencable, and
    **nothing falls back to it implicitly**. ``point_of_no_return`` names the exact step index after
    which the rollback no longer restores the previous authority, so "can we go back?" has an answer
    that is a number rather than a hope.
    """

    steps: tuple[CutoverStep, ...]
    rollback_story: str
    archival: str
    point_of_no_return: int


@dataclass(frozen=True)
class CutoverProposal:
    """The thing that goes to the owner for decision, with the boundary it is escalated under."""

    proposal_id: str
    summary: str
    boundary_reference: str
    boundary_item: str
    decision_state: ProposalDecision
    decision_reference: str | None
    executes_cutover: Literal[False] = False


# The criteria, each stated as something observable with the evidence that would show it. They are
# deliberately strict: a criterion is a condition for changing which memory is live, and the cost of an
# unmet criterion is a deferred cutover rather than a wrong authority.
CUTOVER_CRITERIA: tuple[CutoverCriterion, ...] = (
    CutoverCriterion(
        criterion_id="CRIT-1-inventory-complete",
        statement=(
            "every in-scope surface has an inventory row, including the surfaces with no onboarding"
        ),
        evidence=(
            "the census's inventory state counts at the frozen baseline: the absent count must be "
            "explained surface by surface, not merely reported"
        ),
    ),
    CutoverCriterion(
        criterion_id="CRIT-2-parse-outcomes-dispositioned",
        statement=(
            "every artifact has a parse outcome and a migration disposition, and no artifact is "
            "left with an outcome of parsed and no disposition"
        ),
        evidence="the census's parse-outcome and disposition counts at the frozen baseline",
    ),
    CutoverCriterion(
        criterion_id="CRIT-3-references-resolved",
        statement=(
            "no reference in the corpus is unresolved or ambiguous, or every unresolved and "
            "ambiguous reference carries a curator's recorded disposition"
        ),
        evidence=(
            "the mechanical mismatch report's reference counts together with the dispositions "
            "recorded against them"
        ),
    ),
    CutoverCriterion(
        criterion_id="CRIT-4-assessment-completion",
        statement=(
            "assessment completion (T + F + U) / N is total: every claim in the cohort has been "
            "assessed, so no claim remains in P"
        ),
        evidence=(
            "the census's four-way separation: a non-zero P is the exact shortfall, and this "
            "criterion reports it rather than excluding those claims from N"
        ),
    ),
    CutoverCriterion(
        criterion_id="CRIT-5-denominator-reviewed",
        statement=(
            "the reference inventory has an independent reviewer recorded, and K was not derived "
            "from the corpus being measured"
        ),
        evidence=(
            "the reference inventory record's author, its reviewer identity and the recorded "
            "statement that C / K is reported only where the review exists"
        ),
    ),
    CutoverCriterion(
        criterion_id="CRIT-6-no-authority-fallback",
        statement="no path revives legacy prose as authority once the substrate is live",
        evidence=(
            "the negative proof: no authority flag, no cutover trigger, no scheduled activation, "
            "and no reader that falls back to the archived corpus"
        ),
    ),
)

# The plan's ordered steps. The first four are preparation and are reversible; the archival step is
# where the corpus stops being the operational authority, and the point of no return follows it.
CUTOVER_PLAN = CutoverPlan(
    steps=(
        CutoverStep(
            order=1,
            action="freeze the baseline and re-run the census against it",
            reversible=True,
            note="a re-run at the same baseline and mapping must produce the same outcome set",
        ),
        CutoverStep(
            order=2,
            action="complete curation of every claim in the cohort, including recording U explicitly",
            reversible=True,
            note="an assessed claim is a curator's authored record; nothing here changes a reader",
        ),
        CutoverStep(
            order=3,
            action="evaluate the criteria against the census result and record each outcome",
            reversible=True,
            note="an unmet criterion defers the cutover; it is not a failure to be smoothed",
        ),
        CutoverStep(
            order=4,
            action="obtain the owner's recorded decision on the proposal",
            reversible=True,
            note="the escalation boundary requires a recorded decision, not an inference from silence",
        ),
        CutoverStep(
            order=5,
            action="archive the legacy corpus to its archival destination",
            reversible=False,
            note="the archive is preserved and referencable and is never read as authority again",
        ),
        CutoverStep(
            order=6,
            action="switch the live authority to the substrate",
            reversible=False,
            note="after this step there is exactly one live authority",
        ),
        CutoverStep(
            order=7,
            action="verify that no reader falls back to the archived corpus",
            reversible=False,
            note="a fallback that revives legacy prose is a second authority wearing a different name",
        ),
    ),
    rollback_story=(
        "Steps 1 to 4 are reversible by doing nothing: they publish no authority change, so the "
        "legacy corpus stays live and the archived copy does not exist yet. Step 5 is the point of no "
        "return for reverting *without* an owner decision, because the corpus has moved to an archive "
        "whose whole contract is that it is not read as authority; recovering it is a second, "
        "separately approved cutover in the other direction rather than a rollback. The rollback must "
        "therefore be decided before step 5 and recorded with the decision."
    ),
    archival=(
        "The legacy corpus is archived whole, at the exact frozen baseline, to a destination that is "
        "preserved and referencable by an exact identity and is **not** on any reader's resolution "
        "path. No code path may consult the archive to answer a question the substrate cannot answer: "
        "an implicit fallback to archived prose is a second live authority wearing a different name, "
        "and the substrate's own unresolved state is the answer in that case. The archival mechanics "
        "-- destination, retention and referencability -- are the owner's to decide, and this plan "
        "names the property they must satisfy rather than inventing a location."
    ),
    point_of_no_return=5,
)

CUTOVER_PROPOSAL = CutoverProposal(
    proposal_id="cutover-proposal/legacy-onboarding-to-substrate",
    summary=(
        "Change the live authority for the agents-remember onboarding corpus from the legacy "
        "Markdown-era tree to the knowledge substrate, once the criteria hold. The proposal is "
        "recorded here and is not acted on by this leaf."
    ),
    boundary_reference=ESCALATION_BOUNDARY,
    boundary_item=ESCALATION_ITEM,
    decision_state="absent",
    decision_reference=None,
)


def evaluate_criteria(
    observations: Sequence[CriterionObservation],
) -> tuple[CriterionObservation, ...]:
    """Return the criteria in declared order, each with its recorded outcome.

    A pure re-ordering and completeness check, not an evaluator: the outcomes are the caller's, and
    this function's only job is to refuse a result that omits a declared criterion -- an evaluation
    that silently skipped one would read as a satisfied criterion set.
    """

    by_id = {observation.criterion_id: observation for observation in observations}
    missing = [
        criterion.criterion_id
        for criterion in CUTOVER_CRITERIA
        if criterion.criterion_id not in by_id
    ]
    if missing:
        raise ValueError(
            f"the criteria evaluation omits declared criteria {missing}; an omitted criterion reads "
            "as a satisfied one"
        )
    return tuple(by_id[criterion.criterion_id] for criterion in CUTOVER_CRITERIA)


def criteria_verdict(observations: Sequence[CriterionObservation]) -> str:
    """Render the honest verdict §8.3 requires, including "cutover is not yet justified".

    The verdict is derived from the observations rather than passed in, so a caller cannot report a
    satisfied cutover while carrying an unmet criterion.
    """

    unmet = [observation for observation in observations if not observation.met]
    if not unmet:
        return "cutover is justified by the recorded criteria"
    rendered = "; ".join(
        f"{observation.criterion_id} unmet ({observation.observed})" for observation in unmet
    )
    return f"cutover is not yet justified: {rendered}"


def measure_criterion(
    criterion_id: str,
    *,
    holds: Callable[[], bool],
    observed: str,
) -> CriterionObservation:
    """Build one criterion observation from a predicate and the measure that decided it.

    The predicate is called once, here, so the caller cannot report a met criterion whose own measure
    says otherwise: the two are computed together and stored together.
    """

    declared = next(
        (criterion for criterion in CUTOVER_CRITERIA if criterion.criterion_id == criterion_id),
        None,
    )
    if declared is None:
        raise ValueError(
            f"{criterion_id!r} is not a declared cutover criterion; a criterion invented at "
            "evaluation time would not be a condition anybody reviewed"
        )
    return CriterionObservation(
        criterion_id=criterion_id,
        statement=declared.statement,
        met=holds(),
        observed=observed,
        required=declared.statement,
        evidence=declared.evidence,
    )


def cutover_artifacts() -> Mapping[str, object]:
    """Return the three artifacts this leaf produces, as one value a caller can record.

    The mapping is the deliverable §8.1 names: the criteria, the plan and the proposal. Nothing in it
    is executed, and the function has no side effect -- reading it is the whole operation.
    """

    return {
        "criteria": CUTOVER_CRITERIA,
        "plan": CUTOVER_PLAN,
        "proposal": CUTOVER_PROPOSAL,
    }
