"""The family coverage one ingest report carries: what was authored, examined, and left unresolved.

The report is the product of a curator run, so this module assembles the family plane's half of it
from what the run measured. It decides nothing about storage and writes nothing: it reads the plans
:mod:`…curator_family_planning` resolved and the post-batch facts the run read back, and produces the
typed coverage :class:`…knowledge_curator_ingest.IngestReport` carries.

Three states are kept apart and never merge: ``recorded`` means the batch committed and every count
here was read from the candidate; ``projected`` means a planning run wrote nothing and the lists are
what it would write; ``not-recorded`` means no family row was written, with the sentence naming why.
A guarantee is ``authored`` when this run sealed its revision and ``examined`` when the candidate
already held it -- the reuse case, where the recorded text is read back and reported rather than
restated. ``unexamined`` and ``unresolved`` are different facts on purpose: the first names committed
entries the curator authored no family decision for, and the second names entries whose outcome this
run could not establish at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents_remember.application.curator_family_planning import (
    CuratorFamilyAuthoring,
    DeclarationPlan,
    MembershipPlan,
    RetirementPlan,
    StoredFamilyFacts,
)

__all__ = [
    "CoverageScope",
    "FamilyCoverage",
    "GuaranteeOutcome",
    "MembershipOutcome",
    "NoFamilyOutcome",
    "family_coverage",
]


@dataclass(frozen=True)
class GuaranteeOutcome:
    """One family guarantee this run authored or examined, with what the dataset then recorded."""

    family_key: str
    family_id: str
    family_revision_id: str
    state: str
    display_version: str
    joint_guarantee: str
    members_recorded: int | None
    unchanged_sibling_members: int | None
    predecessors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MembershipOutcome:
    """One membership this run added, found already recorded, or retired."""

    entry_id: str
    family_key: str
    family_revision_id: str
    invariant_revision_id: str
    member_id: str
    state: str
    basis: str
    retained_from_member_id: str | None = None


@dataclass(frozen=True)
class NoFamilyOutcome:
    """One deliberate no-family outcome: the exact revision it was authored for, and its basis."""

    entry_id: str
    invariant_revision_id: str
    basis: str


@dataclass(frozen=True)
class FamilyCoverage:
    """The family plane's whole result: what was authored, examined, and left unresolved.

    ``state`` is one of three and they never merge: ``recorded`` means the batch committed and every
    count here was read from the candidate; ``projected`` means this was a planning run and the lists
    are what it would write; ``not-recorded`` means no family row was written by this run, with
    ``detail`` naming why. ``unexamined`` and ``unresolved`` are different facts on purpose: the first
    names committed entries the curator authored no family decision for, and the second names entries
    whose family outcome this run could not establish at all.
    """

    state: str
    detail: str
    guarantees: tuple[GuaranteeOutcome, ...] = ()
    memberships: tuple[MembershipOutcome, ...] = ()
    no_family: tuple[NoFamilyOutcome, ...] = ()
    unexamined: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageScope:
    """One run's own outcome, as the family coverage needs it: what it established, and over what.

    Grouped rather than passed as five trailing arguments because they are one fact about one run:
    ``state`` and ``detail`` say what this run established about the family plane, ``placed`` and
    ``unresolved`` are the two entry sets that outcome divides, and ``after`` is the post-batch read
    of the candidate that exists only when the batch committed.
    """

    state: str
    detail: str
    placed: tuple[str, ...]
    unresolved: tuple[str, ...] = ()
    after: StoredFamilyFacts | None = None


def family_coverage(
    declarations: DeclarationPlan,
    authoring: Mapping[str, CuratorFamilyAuthoring],
    scope: CoverageScope,
) -> FamilyCoverage:
    """Assemble the family coverage one report carries, from what the run measured.

    A guarantee's ``state`` is ``authored`` when this run sealed its revision and ``examined`` when the
    candidate already held it -- the reuse case, where the recorded text is read back and reported
    rather than restated. The two member counts are ``None`` whenever the run did not commit: an
    uncommitted run has no dataset to measure them against, and a null says so instead of a zero that
    would read as a measured empty family.
    """

    placed = scope.placed
    memberships = tuple(
        _membership_outcome(one) for entry in placed for one in authoring[entry].memberships
    ) + tuple(_retired_outcome(one) for entry in placed for one in authoring[entry].retirements)
    return FamilyCoverage(
        state=scope.state,
        detail=scope.detail,
        guarantees=_guarantee_outcomes(
            declarations,
            placed,
            authoring,
            recorded=scope.state == "recorded",
            after=scope.after,
        ),
        memberships=memberships,
        no_family=tuple(
            NoFamilyOutcome(
                entry_id=entry,
                invariant_revision_id=authoring[entry].invariant_revision_id,
                basis=authoring[entry].no_family_basis or "",
            )
            for entry in placed
            if authoring[entry].no_family_basis is not None
        ),
        unexamined=tuple(entry for entry in placed if not authoring[entry].examined),
        unresolved=tuple(scope.unresolved),
    )


def _guarantee_outcomes(
    declarations: DeclarationPlan,
    placed: Sequence[str],
    authoring: Mapping[str, CuratorFamilyAuthoring],
    *,
    recorded: bool,
    after: StoredFamilyFacts | None,
) -> tuple[GuaranteeOutcome, ...]:
    """One outcome per guarantee the committed entries cite, with its measured member count.

    A guarantee enters here when a committed entry cites it -- by the membership it places, or by the
    membership it retires, because a retirement is a membership change and the packet's own rule is
    that such a change prompts examination of the affected recorded guarantee. A guarantee this run
    only examined (a membership that named a stored revision) has no declaration of its own in this
    list, so its outcome is built from the dataset's recorded row: the text and version reported are
    the ones the candidate holds, not a restatement of what the run expected.
    """

    cited: dict[str, str] = {}
    for entry in placed:
        for one in authoring[entry].memberships:
            cited.setdefault(one.family_revision_id, one.key)
        for one in authoring[entry].retirements:
            cited.setdefault(one.family_revision_id, "")
    planned = {one.family_revision_id: one for one in declarations.declarations}
    outcomes: list[GuaranteeOutcome] = []
    for family_revision_id, key in cited.items():
        added = sum(
            1
            for entry in placed
            for one in authoring[entry].memberships
            if one.family_revision_id == family_revision_id
            and not one.stored
            and one.retained_from_member_id is None
        )
        members_now = None if after is None else after.members_of(family_revision_id)
        plan = planned.get(family_revision_id)
        if plan is None:
            outcomes.append(
                _examined_outcome(
                    declarations, key, _CitedGuarantee(family_revision_id, members_now, added)
                )
            )
            continue
        stored_text = (
            None
            if declarations.stored is None
            else declarations.stored.recorded_guarantee(family_revision_id)
        )
        # An examined plan's revision is stored by construction -- that is what ``examined`` means --
        # so its recorded text is the one reported. The declared text is the fallback rather than a
        # second claim: it is what the same revision was authored with, and it is unreachable while
        # the store holds the row.
        reported_text = plan.joint_guarantee if stored_text is None else stored_text
        outcomes.append(
            GuaranteeOutcome(
                family_key=plan.key,
                family_id=plan.family_id,
                family_revision_id=plan.family_revision_id,
                state=("examined" if plan.examined else ("authored" if recorded else "projected")),
                display_version=plan.display_version,
                joint_guarantee=reported_text if plan.examined else plan.joint_guarantee,
                members_recorded=members_now,
                unchanged_sibling_members=(
                    None if members_now is None else max(members_now - added, 0)
                ),
                predecessors=plan.predecessor_revision_ids,
            )
        )
    return tuple(outcomes)


@dataclass(frozen=True)
class _CitedGuarantee:
    """One guarantee a committed entry cited that this list did not declare: its row and its counts."""

    family_revision_id: str
    members_recorded: int | None
    added: int


def _examined_outcome(
    declarations: DeclarationPlan, key: str, cited: _CitedGuarantee
) -> GuaranteeOutcome:
    """One guarantee this list did not declare, reported from the dataset's own recorded row.

    The row was read while this run planned, so the text, the version and the family identity below
    are what the candidate holds. A guarantee the candidate does not hold at all is a state this
    cannot reach: a membership citing it is refused in planning, so nothing reaches a report from it.
    """

    stored = declarations.stored
    family_revision_id = cited.family_revision_id
    return GuaranteeOutcome(
        family_key=key,
        family_id="" if stored is None else stored.family_of.get(family_revision_id, ""),
        family_revision_id=family_revision_id,
        state="examined",
        display_version="" if stored is None else stored.recorded_version(family_revision_id),
        joint_guarantee=(
            "" if stored is None else (stored.recorded_guarantee(family_revision_id) or "")
        ),
        members_recorded=cited.members_recorded,
        unchanged_sibling_members=(
            None if cited.members_recorded is None else max(cited.members_recorded - cited.added, 0)
        ),
    )


def _membership_outcome(plan: MembershipPlan) -> MembershipOutcome:
    return MembershipOutcome(
        entry_id=plan.entry_id,
        family_key=plan.key,
        family_revision_id=plan.family_revision_id,
        invariant_revision_id=plan.invariant_revision_id,
        member_id=plan.member_id,
        state="reused" if plan.stored else "added",
        basis=plan.basis,
        retained_from_member_id=plan.retained_from_member_id,
    )


def _retired_outcome(plan: RetirementPlan) -> MembershipOutcome:
    return MembershipOutcome(
        entry_id=plan.entry_id,
        family_key="",
        family_revision_id=plan.family_revision_id,
        invariant_revision_id=plan.invariant_revision_id,
        member_id=plan.member_id,
        state="retired",
        basis="",
    )
